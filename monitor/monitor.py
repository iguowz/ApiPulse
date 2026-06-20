"""
巡检监控服务 v4
修复项：
- 连续失败计数/前次状态持久化到 Redis（重启不丢失）
- 差异检测增加哈希快速预判（大响应体跳过全量 diff）
- _fire_alert 告警记录写入后再推渠道，不因渠道失败丢记录
- v4: 告警去重（30min 窗口内相同指纹只发一次，恢复通知不受限制）
- v4: 告警静默期（silence_until 内跳过巡检，支持计划维护窗口）
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from deepdiff import DeepDiff
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase
from redis.asyncio import Redis

from dag_engine.engine import DagExecutionEngine, _eval_single_assert, extract_jsonpath
from models.dsl import AlertRecord, ApiDSL, Environment, ExecutionRecord, MonitorDSL, RiskLevel, StepResult
from services.ai_job_service import AiJobService
from services.sql_runtime_service import SqlRuntimeService, summarize_sql_result

_RISK_EMOJI = {
    RiskLevel.LOW:      "🟡",
    RiskLevel.MEDIUM:   "🟠",
    RiskLevel.HIGH:     "🔴",
    RiskLevel.CRITICAL: "🚨",
}

_FAIL_COUNT_PREFIX   = "monitor:fail_count:"
_PREV_STATUS_PREFIX  = "monitor:prev_status:"
_ALERT_DEDUP_PREFIX  = "monitor:last_alert_fp:"  # 告警指纹去重 key 前缀

# 告警去重时间窗口（秒）：同一指纹在此窗口内只发一次
_ALERT_DEDUP_WINDOW_S = 30 * 60  # 30 分钟

# 响应体超过此大小（字节）时跳过 DeepDiff，只比较哈希
_DIFF_SIZE_LIMIT = 64 * 1024  # 64 KB


def _assert_to_dict(rule: Any) -> dict[str, Any]:
    if isinstance(rule, dict):
        return rule
    if hasattr(rule, "model_dump"):
        return rule.model_dump()
    return {}


def _is_sql_assert(rule: Any) -> bool:
    data = _assert_to_dict(rule)
    return data.get("source") == "sql" or bool(data.get("sql_ref") or data.get("sql_text") or data.get("sql_query"))


class MonitorService:
    def __init__(self, db: AsyncIOMotorDatabase, redis: Redis | None = None, ws_manager: Any = None):
        self._db          = db
        self._redis       = redis
        self._engine      = DagExecutionEngine(db, redis)
        self._monitor_col = db["monitors"]
        self._exec_col    = db["executions"]
        self._alert_col   = db["alert_records"]
        self._scheduler   = AsyncIOScheduler(timezone="Asia/Shanghai")
        self._ws_manager  = ws_manager  # WebSocket 推送管理器（可选）

    async def start(self):
        monitors = await self._monitor_col.find({"enabled": True}).to_list(length=500)
        # 逐个注册已启用的监控任务，单个失败不影响其他任务启动
        for m in monitors:
            try:
                self._add_job(MonitorDSL(**m))
            except Exception as e:
                # 模型反序列化失败时不阻塞调度器启动，记录日志后继续处理下一个
                logger.error("Failed to load monitor {}: {}", m.get("id", "?"), e)
        self._scheduler.start()
        logger.info("Monitor service started: {} jobs", len(monitors))

    def stop(self):
        self._scheduler.shutdown(wait=False)

    def _add_job(self, monitor: MonitorDSL):
        """注册监控定时任务：cron 非空时优先使用 CronTrigger，否则使用 IntervalTrigger"""
        job_id = f"monitor_{monitor.id}"
        # cron 字段有值时使用 CronTrigger（支持精确到分钟调度），否则回退到简单间隔
        if monitor.cron and monitor.cron.strip():
            # 使用 cron 表达式调度（如 "0 9 * * *" 每天9点）
            try:
                cron_parts = monitor.cron.strip().split()
                if len(cron_parts) != 5:
                    raise ValueError(f"cron 表达式需要 5 个字段，实际 {len(cron_parts)}")
                trigger = CronTrigger(
                    minute=cron_parts[0],
                    hour=cron_parts[1],
                    day=cron_parts[2],
                    month=cron_parts[3],
                    day_of_week=cron_parts[4],
                    timezone="Asia/Shanghai",
                )
                self._scheduler.add_job(
                    self._run_monitor,
                    trigger=trigger,
                    args=[monitor.id],
                    id=job_id,
                    replace_existing=True,
                    max_instances=1,
                )
                logger.info("Monitor job: '{}' cron='{}'", monitor.name, monitor.cron)
            except Exception as e:
                # cron 解析失败（格式非法等），回退到间隔调度确保监控不中断运行
                logger.error("Invalid cron '{}' for monitor '{}': {}, fallback to interval", monitor.cron, monitor.name, e)
                self._add_interval_job(monitor, job_id)
        else:
            # cron 为空 → 直接用简单间隔模式调度
            self._add_interval_job(monitor, job_id)

    def _add_interval_job(self, monitor: MonitorDSL, job_id: str):
        """使用简单间隔注册定时任务（回退逻辑）"""
        interval_s = _parse_interval(monitor.interval)
        self._scheduler.add_job(
            self._run_monitor,
            trigger=IntervalTrigger(seconds=interval_s),
            args=[monitor.id],
            id=job_id,
            replace_existing=True,
            max_instances=1,
        )
        logger.info("Monitor job: '{}' every {}s", monitor.name, interval_s)

    async def register_monitor(self, monitor: MonitorDSL) -> str:
        await self._monitor_col.insert_one(monitor.model_dump())
        # 仅对启用状态的监控注册定时任务，禁用的只入库不调度
        if monitor.enabled:
            self._add_job(monitor)
        return monitor.id

    # ── Redis 持久化计数 ───────────────────────────────────

    async def _get_fail_count(self, monitor_id: str) -> int:
        # 无 Redis 实例时（开发/单机模式）返回 0，不阻塞巡检流程
        if not self._redis:
            return 0
        try:
            val = await self._redis.get(f"{_FAIL_COUNT_PREFIX}{monitor_id}")
            # key 存在时返回整数值，不存在时默认 0（首次巡检）
            return int(val) if val else 0
        except Exception:
            # Redis 不可用时降级为 0，保证巡检主流程不中断
            return 0

    async def _set_fail_count(self, monitor_id: str, count: int) -> None:
        # 无 Redis 实例时跳过持久化（计数仅在内存中有效，重启后丢失）
        if not self._redis:
            return
        try:
            # TTL = 7天，避免过期数据堆积
            await self._redis.setex(f"{_FAIL_COUNT_PREFIX}{monitor_id}", 604800, count)
        except Exception as e:
            # 写入失败不抛异常，防止 Redis 故障阻塞主线巡检
            logger.warning("Redis fail_count set error: {}", e)

    async def _get_prev_passed(self, monitor_id: str) -> bool:
        # 无 Redis 时默认上次通过，避免误告警
        if not self._redis:
            return True
        try:
            val = await self._redis.get(f"{_PREV_STATUS_PREFIX}{monitor_id}")
            # val 为 None（key不存在）→ 默认 True；val 为 "0" → 上次失败；其他 → True
            return val != "0" if val is not None else True
        except Exception:
            # Redis 异常时降级为 True，不阻塞巡检
            return True

    async def _set_prev_passed(self, monitor_id: str, passed: bool) -> None:
        # 无 Redis 时跳过持久化，内存中的计数在新一轮巡检中仍然可用（同进程内）
        if not self._redis:
            return
        try:
            await self._redis.setex(
                f"{_PREV_STATUS_PREFIX}{monitor_id}",
                604800,
                "1" if passed else "0",
            )
        except Exception as e:
            # 写入失败不阻塞主流程
            logger.warning("Redis prev_status set error: {}", e)

    # ── 核心巡检逻辑 ──────────────────────────────────────

    async def _run_monitor(self, monitor_id: str):
        doc = await self._monitor_col.find_one({"id": monitor_id})
        # 监控配置已被删除（并发竞争窗口），直接退出不报错
        if not doc:
            return
        try:
            monitor = MonitorDSL(**doc)
        except Exception as e:
            # 模型反序列化失败（字段变更/数据损坏），记录后退出
            logger.error("Monitor {}: deserialize failed: {}", monitor_id, e)
            return

        # 告警静默期检查：计划维护窗口内跳过巡检，避免误报告警
        if monitor.silence_until is not None:
            now = datetime.now(timezone(timedelta(hours=8)))
            # silence_until 可能不带时区，统一转为无时区比较
            silence_deadline = monitor.silence_until
            if silence_deadline.tzinfo is not None:
                silence_deadline = silence_deadline.replace(tzinfo=None)
            # 当前时间未过静默截止 → 跳过本次巡检
            if now.replace(tzinfo=None) < silence_deadline:
                logger.info(
                    "Monitor '{}' silenced until {}, skipping check",
                    monitor.name, monitor.silence_until.isoformat(),
                )
                return

        # 新模型以 target_id 为主，api_id 仅作为 API 监控的冗余展示字段。
        effective_api_id = monitor.target_id or monitor.api_id
        target_type = monitor.target_type or "api"

        # 根据监控目标类型分发：API 模式调用 run_monitor_api，场景模式调用 run_monitor_scenario
        if target_type == "api":
            api_doc = await self._db["api_dsls"].find_one({"id": effective_api_id, "project_id": monitor.project_id})
            # API 文档不存在 → 可能已删除，记录错误并跳过
            if not api_doc:
                logger.error("Monitor {}: API {} not found", monitor_id, effective_api_id)
                return
            try:
                api = ApiDSL(**api_doc)
            except Exception as e:
                # ApiDSL 反序列化失败，数据可能已损坏
                logger.error("Monitor {}: ApiDSL deserialize failed: {}", monitor_id, e)
                return
            try:
                await self._run_monitor_api(monitor, api, monitor_id)
            except Exception as e:
                # API 巡检执行异常，catch 住避免影响调度器其他任务
                logger.error("Monitor '{}' crashed: {}", monitor.name, e)
        elif target_type == "scenario":
            try:
                await self._run_monitor_scenario(monitor, monitor_id)
            except Exception as e:
                # 场景巡检执行异常，不阻塞调度器
                logger.error("Monitor '{}' scenario run crashed: {}", monitor.name, e)
        elif target_type == "data_factory":
            # P0-3: 补齐 data_factory 巡检分支，此前走 else 只记 error log。
            # 数据工厂巡检：生成数据 → 校验字段规则（null/empty/invalid 注入率是否生效）→ 可选 diff 基线。
            try:
                await self._run_monitor_data_factory(monitor, monitor_id)
            except Exception as e:
                logger.error("Monitor '{}' data_factory run crashed: {}", monitor.name, e)
        else:
            # 不支持的 target_type，记录错误（正常情况下不应到达）
            logger.error("Monitor {}: unsupported target_type '{}'", monitor_id, target_type)

    async def _run_monitor_api(self, monitor: MonitorDSL, api: ApiDSL, monitor_id: str):
        """现有的 API 单接口监控逻辑"""
        # SQL 断言需在 HTTP 执行后读取响应/环境上下文，不能直接塞入 ApiDSL.asserts。
        monitor_asserts = list(monitor.asserts or [])
        sql_asserts = [_assert_to_dict(a) for a in monitor_asserts if _is_sql_assert(a)]
        http_asserts = [a for a in monitor_asserts if not _is_sql_assert(a)]
        if http_asserts:
            api = api.model_copy(update={"asserts": http_asserts})

        # 加载执行环境配置：若绑定了 environment_id，读取 base_url/headers/variables
        env_headers: dict[str, str] = {}
        env_variables: dict[str, str] = {}
        if monitor.environment_id:
            env_doc = await self._db["environments"].find_one({"id": monitor.environment_id}, {"_id": 0})
            if env_doc:
                env = Environment(**env_doc)
                # 环境有 base_url 且 API 未显式覆盖时才设置，API 自身 base_url_override 优先级最高
                if env.base_url and not api.base_url_override:
                    api.base_url_override = env.base_url
                env_headers = env.headers
                env_variables = env.variables

        record = await self._engine.run_single(
            api, trigger="monitor", owner=monitor.owner or "",
            env_headers=env_headers, env_variables=env_variables,
        )
        if sql_asserts:
            record = await self._apply_monitor_sql_asserts(monitor, record, sql_asserts, env_variables)
        await self._evaluate_result(monitor, record, monitor_id, api_id=monitor.target_id or monitor.api_id)

    async def _run_monitor_scenario(self, monitor: MonitorDSL, monitor_id: str):
        """场景监控：执行场景并汇总结果"""
        scenario_doc = await self._db["scenarios"].find_one({"id": monitor.target_id})
        # 场景不存在 → 可能已被删除，记录错误并跳过
        if not scenario_doc:
            logger.error("Monitor {}: scenario {} not found", monitor_id, monitor.target_id)
            return
        from models.dsl import ScenarioDSL
        try:
            scenario = ScenarioDSL(**scenario_doc)
        except Exception as e:
            # ScenarioDSL 反序列化失败（字段不兼容或数据损坏）
            logger.error("Monitor {}: ScenarioDSL deserialize failed: {}", monitor_id, e)
            return

        # 加载执行环境配置：若绑定了 environment_id，读取 base_url/headers/variables
        env_base_url = ""
        env_headers: dict[str, str] = {}
        env_variables: dict[str, str] = {}
        if monitor.environment_id:
            env_doc = await self._db["environments"].find_one({"id": monitor.environment_id}, {"_id": 0})
            if env_doc:
                env = Environment(**env_doc)
                env_base_url = env.base_url
                env_headers = env.headers
                env_variables = env.variables

        record = await self._engine.run_scenario(
            scenario, trigger="monitor", owner=monitor.owner or "",
            env_base_url=env_base_url, env_headers=env_headers, env_variables=env_variables,
        )
        sql_asserts = [_assert_to_dict(a) for a in list(monitor.asserts or []) if _is_sql_assert(a)]
        if sql_asserts:
            record = await self._apply_monitor_sql_asserts(monitor, record, sql_asserts, env_variables)
        await self._evaluate_result(monitor, record, monitor_id, api_id=monitor.target_id or monitor.api_id,
                                     scenario_id=monitor.target_id)

    async def _apply_monitor_sql_asserts(
        self,
        monitor: MonitorDSL,
        record: ExecutionRecord,
        sql_asserts: list[dict[str, Any]],
        env_variables: dict[str, str] | None = None,
    ) -> ExecutionRecord:
        """巡检 SQL 断言：执行后追加到首个步骤，失败会回写执行记录并触发后续告警逻辑。"""
        first_step = record.steps[0] if record.steps else None
        response = first_step.response_received if first_step else {}
        context = {
            "monitor": monitor.model_dump(),
            "execution": record.model_dump(),
            "response": response.get("body") if isinstance(response, dict) else response,
            "env": env_variables or {},
        }
        results: list[dict[str, Any]] = []
        passed = True
        for idx, rule in enumerate(sql_asserts):
            query = rule.get("sql_query") or rule
            name = query.get("target_var") or query.get("name") or f"monitor_sql_{idx + 1}"
            sql_result = await SqlRuntimeService(self._db).run_ref(monitor.project_id, query, context)
            field_path = rule.get("path") or rule.get("field") or f"$.{name}.scalar"
            actual = extract_jsonpath({name: sql_result}, field_path) if str(field_path).startswith("$.") else sql_result.get(str(field_path), None)
            if actual is None and str(field_path).startswith("sql."):
                actual = extract_jsonpath({"sql": {name: sql_result}}, "$." + str(field_path))
            op = rule.get("operator") or "eq"
            expected = rule.get("expected")
            ok = _eval_single_assert(op, actual, expected)
            results.append({
                "field": field_path,
                "operator": op,
                "expected": expected,
                "actual": actual,
                "passed": ok,
                "source": "sql",
                "sql_name": name,
                "sql_summary": summarize_sql_result(sql_result),
            })
            if not ok or sql_result.get("error"):
                passed = False
        if first_step:
            first_step.assert_results.extend(results)
            first_step.passed = first_step.passed and passed
            if not passed:
                first_step.error = (first_step.error + "; " if first_step.error else "") + "monitor sql assertion failed"
        record.passed = record.passed and passed
        if not record.passed and not record.failure_reason:
            record.failure_reason = "monitor sql assertion failed"
        await self._exec_col.update_one(
            {"id": record.id},
            {"$set": {
                "steps": [step.model_dump() for step in record.steps],
                "passed": record.passed,
                "failure_reason": record.failure_reason,
            }},
        )
        return record

    async def _run_monitor_data_factory(self, monitor: MonitorDSL, monitor_id: str):
        """
        P0-3: 数据工厂巡检——验证数据模板的生成能力是否正常。
        执行逻辑：
        1. 加载 target_id 指向的 DataTemplate
        2. 调用 DataFactory.generate 生成一批数据
        3. 校验生成结果：字段完整性（模板定义的字段是否都生成了）、
           异常值注入是否生效（若配置了 null_rate/invalid_rate，验证确实产生了异常值）
        4. 构造 ExecutionRecord 复用 _evaluate_result 的告警/差异检测逻辑
        目的：监控数据工厂本身的健康度，避免造数能力静默失效导致下游测试用例拿到错误数据。
        """
        from data_factory.factory import DataFactory, DataTemplate
        import uuid as _uuid

        # 加载数据模板
        tmpl_doc = await self._db["data_templates"].find_one({"id": monitor.target_id})
        if not tmpl_doc:
            # 模板已删除 → 记录错误跳过，不持续告警（避免误报）
            logger.error("Monitor {}: data template {} not found", monitor_id, monitor.target_id)
            return
        try:
            template = DataTemplate(**tmpl_doc)
        except Exception as e:
            logger.error("Monitor {}: DataTemplate deserialize failed: {}", monitor_id, e)
            return

        started = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        factory = DataFactory(self._redis) if self._redis else DataFactory.__new__(DataFactory)
        # 在线程池执行造数（CPU 密集型），count=10 足够验证注入率
        data_list = await asyncio.to_thread(factory.generate, template, {}, 10)
        finished = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        latency_ms = int((finished - started).total_seconds() * 1000)

        # ── 校验生成结果 ──
        failures: list[str] = []
        field_names = {f.name.split(".")[0] for f in template.fields}  # 取顶层字段名集合

        if not data_list:
            # 完全无产出 → 严重故障，直接判失败
            failures.append("data factory generated 0 records (template may have invalid config)")
        else:
            for record_data in data_list:
                # 校验顶层字段完整性：模板定义的字段都应出现在生成结果中
                for fname in field_names:
                    if fname not in record_data:
                        failures.append(f"missing field '{fname}' in generated record")

        # ── 校验异常值注入是否生效（仅当配置了注入率时） ──
        # 意义：若用户配置了 invalid_rate=0.5 但 10 条数据全是正常值，
        # 说明数据工厂的注入逻辑失效，需告警。
        for ft in template.fields:
            top_name = ft.name.split(".")[0]
            if ft.null_rate > 0 or ft.invalid_rate > 0 or ft.empty_rate > 0:
                # 统计生成的 10 条数据中该字段为异常（None/""/在 invalid_values 内）的比例
                anomalous = 0
                for record_data in data_list:
                    val = record_data.get(top_name)
                    if val is None or val == "" or (ft.invalid_values and val in ft.invalid_values):
                        anomalous += 1
                # 配置了注入率但 10 条全正常 → 注入逻辑疑似失效（允许偶发，全 0 才告警）
                if len(data_list) >= 10 and anomalous == 0:
                    failures.append(
                        f"field '{ft.name}' configured anomaly rate but 0/10 anomalies generated "
                        f"(null_rate={ft.null_rate}, invalid_rate={ft.invalid_rate})"
                    )

        passed = len(failures) == 0
        # 构造 ExecutionRecord，type=data_factory，复用 _evaluate_result 告警链路
        step = StepResult(
            step_id="factory_check", api_id=monitor.target_id, name=template.name,
            passed=passed,
            response_received={"generated_count": len(data_list), "sample": data_list[:3]},
            latency_ms=latency_ms,
            error="; ".join(failures) if failures else "",
        )
        record = ExecutionRecord(
            id=str(_uuid.uuid4()),
            api_id=monitor.target_id,
            type="data_factory",
            steps=[step],
            passed=passed,
            started_at=started,
            finished_at=finished,
            duration_ms=latency_ms,
            trigger="monitor",
            executor=monitor.owner or "",
            project_id=monitor.project_id,
            failure_reason="; ".join(failures) if failures else "",
        )
        await self._exec_col.insert_one(record.model_dump())
        await self._evaluate_result(monitor, record, monitor_id, api_id=monitor.target_id)

    async def _evaluate_result(self, monitor: MonitorDSL, record, monitor_id: str,
                                api_id: str = "", scenario_id: str = ""):
        """统一的结果评估和告警逻辑，支持 API 和场景两种类型"""
        target_type = monitor.target_type or "api"
        diff_info = await self._detect_diff(monitor, record, target_type, api_id, scenario_id)
        prev_passed = await self._get_prev_passed(monitor_id)
        fail_count = await self._get_fail_count(monitor_id)

        # 通过且无差异变化 → 重置连续失败计数；否则累加
        if record.passed and not diff_info:
            fail_count = 0
        else:
            fail_count += 1

        await self._set_fail_count(monitor_id, fail_count)
        await self._set_prev_passed(monitor_id, record.passed)

        # 告警条件：当前失败（或检测到差异）且连续失败次数达到阈值
        should_alert = (
            (not record.passed or bool(diff_info))
            and fail_count >= monitor.max_consecutive_failures
        )
        # 恢复条件：当前通过 + 上次失败 + 无差异（排除反复切换）
        is_recovery = record.passed and not prev_passed and not diff_info

        if should_alert:
            # 满足告警条件 → 触发告警通知
            await self._fire_alert(monitor, record, diff_info, is_recovery=False, scenario_id=scenario_id)
        elif is_recovery and monitor.alert_on_recovery:
            # 已恢复且配置了恢复通知 → 发送恢复消息
            await self._fire_alert(monitor, record, None, is_recovery=True, scenario_id=scenario_id)
        else:
            # 正常状态：通过但未达到告警条件，或告警中但未达阈值
            logger.info(
                "Monitor '{}' OK: passed={} fail_streak={} diff_changes={}",
                monitor.name, record.passed, fail_count,
                diff_info["change_count"] if diff_info else 0,
            )

    # ── 差异检测（哈希快速预判 + DeepDiff 深度分析）───────

    @staticmethod
    def _body_hash(body: Any) -> str:
        raw = json.dumps(body, sort_keys=True, default=str).encode()
        return hashlib.md5(raw).hexdigest()

    async def _detect_diff(
        self,
        monitor: MonitorDSL,
        current: ExecutionRecord,
        target_type: str = "api",
        api_id: str = "",
        scenario_id: str = "",
    ) -> dict[str, Any] | None:
        # 兼容直接调用 _detect_diff(monitor, record) 的链路：从 monitor/current 自动推导目标。
        # 这样巡检 diff 不会因为调用方未显式传 api_id/scenario_id 而静默跳过。
        target_type = target_type or monitor.target_type or "api"
        if target_type == "api" and not api_id:
            api_id = monitor.target_id or monitor.api_id or current.api_id
        if target_type == "scenario" and not scenario_id:
            scenario_id = monitor.target_id or current.scenario_id
        # 根据目标类型构建不同的查询条件，查找上一次成功的巡检记录
        if target_type == "scenario" and scenario_id:
            # 场景模式：按 scenario_id + type=scenario 查找上次记录
            query: dict[str, Any] = {
                "type": "scenario",
                "scenario_id": scenario_id,
                "passed": True,
                "trigger": "monitor",
                "id": {"$ne": current.id},
            }
        elif target_type == "api" and api_id:
            # API 模式：按 api_id + type=single 查找上次记录
            query = {
                "api_id": api_id,
                "type": "single",
                "passed": True,
                "trigger": "monitor",
                "id": {"$ne": current.id},
            }
        else:
            # 无法确定查询条件时跳过差异检测（首次运行或参数缺失）
            return None
        last = await self._exec_col.find_one(query, sort=[("started_at", -1)])
        # 没有历史记录或历史记录无步骤数据 → 无基线，跳过 diff
        if not last or not last.get("steps"):
            return None

        try:
            prev_resp = last["steps"][0].get("response_received") or {}
            prev_body = prev_resp.get("body")
            # 安全提取当前响应的 body，防御 steps 为空的情况
            curr_step = current.steps[0] if current.steps else None
            curr_resp = curr_step.response_received if curr_step else None
            curr_body = curr_resp.get("body") if curr_resp else None
        except (IndexError, KeyError, AttributeError, TypeError):
            # 响应体结构异常（字段缺失/类型不匹配），放弃本次 diff
            return None

        # 哈希快速判断：相同直接跳过 DeepDiff（节省 CPU）
        if self._body_hash(prev_body) == self._body_hash(curr_body):
            return None

        # 大响应体只做哈希对比，不进行 DeepDiff（避免 CPU 爆炸）
        body_size = len(json.dumps(curr_body, default=str).encode())
        if body_size > _DIFF_SIZE_LIMIT:
            return {
                "change_count": 1,
                "diff_summary": json.dumps({"note": "large_body_hash_changed"}),
                "prev_exec_id": str(last.get("id", "")),
            }

        exclude_paths = {f"root{p}" for p in monitor.diff_ignore_paths}
        diff = DeepDiff(
            prev_body, curr_body,
            ignore_order=True,
            exclude_paths=exclude_paths or None,
        )
        change_count = sum(len(v) for v in diff.values()) if diff else 0

        # 变更数未达阈值 → 忽略，不触发差异告警
        if change_count < monitor.diff_threshold:
            return None

        return {
            "change_count": change_count,
            "diff_summary": diff.to_json() if diff else "{}",
            "prev_exec_id": str(last.get("id", "")),
        }

    # ── 告警去重（指纹 = md5(monitor_id + error_prefix)）──────────

    def _build_alert_fingerprint(self, monitor_id: str, error_msg: str) -> str:
        """基于 monitor_id + 错误信息前200字符构建告警指纹，用于去重"""
        raw = f"{monitor_id}|{error_msg[:200]}"
        return hashlib.md5(raw.encode()).hexdigest()

    async def _is_alert_duplicate(self, monitor_id: str, error_msg: str) -> bool:
        """检查相同指纹在去重窗口内是否已发送过告警；无 Redis 时默认不重复"""
        # 无 Redis 实例 → 无法去重，默认允许发送
        if not self._redis:
            return False
        try:
            fp = self._build_alert_fingerprint(monitor_id, error_msg)
            exists = await self._redis.get(f"{_ALERT_DEDUP_PREFIX}{fp}")
            # key 存在 → 窗口内已发过，返回 True（重复）
            return exists is not None
        except Exception:
            # Redis 查询异常 → 保守策略，允许发送（不阻塞告警）
            return False

    async def _mark_alert_sent(self, monitor_id: str, error_msg: str) -> None:
        """记录告警指纹及时间，TTL=30min 即去重窗口"""
        # 无 Redis 实例 → 无法标记，跳过
        if not self._redis:
            return
        try:
            fp = self._build_alert_fingerprint(monitor_id, error_msg)
            await self._redis.setex(
                f"{_ALERT_DEDUP_PREFIX}{fp}",
                _ALERT_DEDUP_WINDOW_S,
                datetime.now(timezone(timedelta(hours=8))).isoformat(),
            )
        except Exception as e:
            # 标记失败不阻塞告警流程，仅记录日志
            logger.warning("Redis alert dedup mark error: {}", e)

    # ── 告警派发 ──────────────────────────────────────────

    async def _fire_alert(
        self,
        monitor: MonitorDSL,
        record: ExecutionRecord,
        diff_info: dict[str, Any] | None,
        is_recovery: bool,
        scenario_id: str = "",
    ):
        # 安全提取首个 step 的响应信息，防御 records.steps 为空的情况
        step = record.steps[0] if record.steps else None
        # step.response_received 可能为 None，防御性取值避免 AttributeError
        resp = step.response_received if step and step.response_received else {}
        status_code = resp.get("status_code", "?")
        latency     = step.latency_ms if step else 0
        # 告警错误信息：优先取 step 级别 error，无 step 时回退到 record 级别 failure_reason
        error       = step.error if step else record.failure_reason

        emoji = "✅" if is_recovery else _RISK_EMOJI.get(monitor.risk_level, "⚠️")
        target_type = monitor.target_type or "api"
        # 恢复通知 vs 告警通知，标题前缀不同
        title = (
            f"{emoji} [恢复] {monitor.name}"
            if is_recovery
            else f"{emoji} [告警] {monitor.name}"
        )
        # 根据目标类型显示不同的 ID 信息
        if target_type == "scenario":
            target_label = f"场景 ID: {monitor.target_id}"
        elif target_type == "data_factory":
            target_label = f"数据模板 ID: {monitor.target_id}"
        else:
            target_label = f"接口 ID: {monitor.target_id or monitor.api_id}"
        lines = [
            target_label,
            f"结果: {'✓ 通过' if record.passed else '✗ 失败'}",
            f"HTTP: {status_code}  延迟: {latency}ms",
        ]
        # 仅告警通知展示错误详情和 diff 信息，恢复通知省略
        if error and not is_recovery:
            lines.append(f"错误: {error}")
        if diff_info:
            lines.append(f"响应变更字段数: {diff_info['change_count']}")
        if not is_recovery:
            lines.append(f"执行ID: {record.id}")

        message = "\n".join(lines)
        logger.warning("ALERT {} | {}", title, message)

        # 先持久化告警记录（即使渠道推送失败也不丢失）
        # project_id 从 monitor 继承，实现按项目隔离告警数据
        alert = AlertRecord(
            id=str(uuid.uuid4()),
            monitor_id=monitor.id,
            api_id=monitor.target_id or monitor.api_id,
            execution_id=record.id,
            project_id=monitor.project_id,
            risk_level=monitor.risk_level,
            title=title,
            message=message,
            channels=monitor.alert_channels,
            is_recovery=is_recovery,
        )
        try:
            await self._alert_col.insert_one(alert.model_dump())
        except Exception as e:
            # 入库失败记录日志，不阻塞后续推送（告警记录丢失优于告警通知丢失）
            logger.error("Alert record save failed: {}", e)

        # 告警去重：非恢复通知在30min窗口内相同指纹只发一次
        # 恢复通知不受去重限制，确保用户及时收到恢复消息
        is_dup = False
        if not is_recovery:
            is_dup = await self._is_alert_duplicate(monitor.id, error or "")

        if is_dup:
            # 指纹在窗口内已存在 → 跳过推送，避免频繁重复通知
            logger.info(
                "Alert dedup: skip push for monitor '{}' (fingerprint sent within {}min)",
                monitor.name, _ALERT_DEDUP_WINDOW_S // 60,
            )
            return

        # WebSocket 实时推送告警到已连接的前端客户端
        if self._ws_manager:
            await self._ws_manager.broadcast(f"monitor:events:{monitor.project_id}", alert.model_dump())

        # 并发推送所有渠道（互不阻塞），单个渠道失败不影响其他渠道
        if monitor.alert_channels:
            await asyncio.gather(
                *[_push_channel(url, title, message, monitor.risk_level, owner=monitor.owner)
                  for url in monitor.alert_channels],
                return_exceptions=True,
            )

        # 记录告警指纹，用于后续去重（恢复通知也标记，避免恢复后立即再次告警）
        await self._mark_alert_sent(monitor.id, error or "")

        # P1-2: 告警记录保存后，异步触发 AI 评估（不阻塞渠道推送）。
        # AI 评估完成后回写 AlertRecord.ai_severity/ai_root_cause 字段，前端展示降噪标签。
        # 恢复通知不需要 AI 评估（无异常可分析）。
        if not is_recovery and self._redis is not None and alert.id:
            try:
                import json as _json
                job_id = f"alert:{alert.id}"
                payload = {
                    "alert_id": alert.id,
                    "project_id": monitor.project_id,
                    "job_id": job_id,
                    "status": "queued",
                    "fail_count": 0,
                }
                await self._redis.rpush(
                    "queue:alert_analyze",
                    _json.dumps(payload, ensure_ascii=False),
                )
                await AiJobService(self._db).mark_queued(
                    job_id=job_id,
                    type="alert",
                    project_id=monitor.project_id,
                    source="monitor_alert",
                    target_ids=[alert.id],
                    queue_key="queue:alert_analyze",
                    payload=payload,
                )
            except Exception as e:
                # AI 评估入队失败不影响告警本身，仅记录日志
                logger.warning("Alert AI assess enqueue failed for {}: {}", alert.id, e)

    # ── 统计 ──────────────────────────────────────────────

    async def get_monitor_stats(self, monitor_id: str) -> dict[str, Any]:
        fail_streak  = await self._get_fail_count(monitor_id)
        prev_passed  = await self._get_prev_passed(monitor_id)
        # 根据监控目标类型构建正确的执行记录过滤器
        doc = await self._monitor_col.find_one(
            {"id": monitor_id},
            {"api_id": 1, "target_type": 1, "target_id": 1, "project_id": 1},
        )
        # 文档不存在（已删除）时默认 api 类型，防止 None access
        target_type = (doc.get("target_type") or "api") if doc else "api"
        exec_filter: dict[str, Any] = {"trigger": "monitor"}
        if target_type == "scenario":
            # 场景监控的执行记录通过 scenario_id + type=scenario 标识
            scenario_id = doc.get("target_id", "") if doc else ""
            if scenario_id:
                exec_filter["scenario_id"] = scenario_id
                exec_filter["type"] = "scenario"
        elif target_type == "data_factory":
            template_id = doc.get("target_id") if doc else None
            if template_id:
                exec_filter["api_id"] = template_id
                exec_filter["type"] = "data_factory"
        else:
            # API 监控：通过 target_id 过滤，api_id 仅为冗余展示字段。
            api_id = (doc.get("target_id") or doc.get("api_id")) if doc else None
            if api_id:
                exec_filter["api_id"] = api_id
                exec_filter["type"] = "single"
        if doc and doc.get("project_id"):
            exec_filter["project_id"] = doc.get("project_id")
        total_execs  = await self._exec_col.count_documents(exec_filter)
        total_alerts = await self._alert_col.count_documents({"monitor_id": monitor_id})
        return {
            "monitor_id":        monitor_id,
            "fail_streak":       fail_streak,
            "last_passed":       prev_passed,
            "total_executions":  total_execs,
            "total_alerts":      total_alerts,
        }


# ── 渠道推送（模块级函数，便于测试 mock）─────────────────

async def _push_channel(url: str, title: str, content: str, risk: RiskLevel, owner: str = ""):
    # 拼接负责人 @mention：不同渠道语法不同
    # - 钉钉 markdown: @手机号 才生效，这里仅展示负责人信息
    # - 企微 markdown: <@userid> 可触发 @mention
    # - Slack mrkdwn: <@userid> 可触发 @mention
    owner_line = f"> 👤 负责人: @{owner}\n\n" if owner else ""

    url_lower = url.lower()
    # 根据 webhook URL 特征匹配渠道，构造对应格式的 payload
    if "oapi.dingtalk" in url_lower:
        # 钉钉 markdown 中 @mobile 才生效，owner 字符串直接展示
        md_text = f"## {title}\n\n{owner_line}{content}"
        payload: dict[str, Any] = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": md_text},
        }
    elif "hooks.slack" in url_lower:
        # Slack 使用 <@userid> 语法，若 owner 是 Slack 用户 ID 则生效
        slack_content = f"{owner_line}{content}"
        payload = {
            "text": title,
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": title}},
                {"type": "section", "text": {"type": "mrkdwn", "text": slack_content}},
            ],
        }
    elif "qyapi.weixin" in url_lower:
        # 企微 markdown 支持 <@userid> 语法，若 owner 是企微 userid 则生效
        wx_content = f"{owner_line}**{title}**\n{content}"
        payload = {
            "msgtype": "markdown",
            "markdown": {"content": wx_content},
        }
    else:
        # 未知渠道 → 发送通用 JSON 格式，由接收方自行解析
        payload = {
            "title": title, "content": content,
            "risk_level": risk,
            "owner": owner or None,
            "timestamp": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None).isoformat(),
        }
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(url, json=payload)
        logger.info("Alert sent → {} [{}]", url[:50], resp.status_code)
    except Exception as e:
        # 单渠道推送失败不抛出异常（外层 gather 已设 return_exceptions=True）
        logger.error("Alert push failed → {}: {}", url[:50], e)


def _parse_interval(s: str) -> int:
    """解析间隔字符串，如 5m / 30s / 1h / 1d；非法输入返回默认 300s"""
    # 空值或非字符串类型 → 默认 300s
    if not s or not isinstance(s, str):
        return 300
    s = s.strip()
    # 长度不足（如 "5" 缺单位）→ 默认
    if len(s) < 2:
        return 300
    unit = s[-1].lower()
    try:
        val = int(s[:-1])
    except (ValueError, TypeError):
        # 数字部分解析失败（如 "abc"）→ 默认
        return 300
    # 数值无效（0 或负数）→ 默认
    if val <= 0:
        return 300
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    # 单位不在支持列表中 → 默认
    if unit not in multipliers:
        return 300
    return multipliers[unit] * val
