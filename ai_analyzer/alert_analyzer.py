"""
AI 告警分析服务 —— P1-2 巡检 AI 降噪与根因分析

解决问题：此前 monitor 告警全是阈值触发，无 AI 降噪。
- 偶发抖动（网络/限流）触发告警 → 噪声
- 告警无根因关联，用户需自行排查
- 无误报识别，长期告警疲劳

AI 评估维度：
1. 误报识别：对比历史通过样本，判断是否偶发抖动 → severity=noise，标记不急迫
2. 根因关联：结合 ReMe 记忆 + 最近 import-diffs，给出 probable_root_cause
3. 严重度分级：critical/high/medium/low，影响告警优先级

流程：_fire_alert 触发后异步入队 queue:alert_analyze → worker 调 LLM → 回写 AlertRecord.ai_* 字段。
不阻塞渠道推送（先推后评估，评估完成后前端刷新展示）。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase
from redis.asyncio import Redis
from openai import AsyncOpenAI, APIError, RateLimitError

from ai_analyzer.utils import safe_fire_and_forget

from config.settings import get_settings
from services.ai_job_service import AiJobService
from services.llm_config_service import resolve_llm_config
from services.structured_output_service import StructuredOutputError, parse_structured_output


ALERT_ANALYZE_QUEUE = "queue:alert_analyze"
ALERT_ANALYZE_DLQ = "queue:alert_analyze:dlq"
MAX_RETRY = 3


_ALERT_SYSTEM = """\
你是 SRE 告警分析专家。你的任务是评估监控告警的严重程度、识别误报、并给出可能的根因。

规则：
1. severity 取值（按优先级）：
   - critical: 核心服务不可用（5xx 错误率激增、超时持续、依赖宕机）
   - high: 重要功能异常（业务码错误、关键字段缺失），但服务可用
   - medium: 次要异常（响应变慢、非关键字段变更），需关注但不紧急
   - low: 轻微波动（单次失败、快速恢复），信息性告警
   - noise: 误报（偶发抖动、网络瞬时问题、已知维护窗口），不应触发响应

2. 误报识别依据：
   - 告警前有连续通过记录 → 偶发，倾向 noise
   - 错误信息含 timeout/connection/EOF → 网络瞬时，倾向 noise/low
   - 错误信息含 assertion/business code → 真实异常，不低于 medium
   - 响应变更字段数少（<3）且非关键字段 → low

3. root_cause 给出一句话根因推断，结合错误信息和响应变更。

4. 仅输出纯 JSON：{"severity":"...","root_cause":"...","confidence":0.0~1.0}，不要 markdown 围栏。"""


class AlertAnalyzerService:
    """AI 告警分析服务 —— 异步消费 queue:alert_analyze。"""

    def __init__(self, db: AsyncIOMotorDatabase, redis: Redis, ws_manager=None,
                 memory=None):  # P5: MemoryService | None，4-tier 记忆服务
        self._db = db
        self._redis = redis
        self._ws = ws_manager
        self._memory = memory  # P5: L2 项目记忆记录，None 时静默跳过
        self._client: AsyncOpenAI | None = None
        self._model = ""
        self._temperature = 0.1
        self._max_tokens = 1024  # 告警评估输出短，节省成本
        self._refresh_client()

    def _refresh_client(self) -> None:
        """从 settings 重新加载 LLM 客户端配置（支持运行时热刷新）。"""
        db_config = getattr(self, "_runtime_llm_config", None) or {}
        runtime = resolve_llm_config(db_config if isinstance(db_config, dict) else {}, "alert")
        local_providers = {"ollama", "lmstudio", "llamacpp"}
        if not runtime.api_key and runtime.provider not in local_providers:
            self._client = None
            logger.warning("AlertAnalyzer: no API key configured, AI assessment disabled")
            return
        try:
            self._client = runtime.build_client()
            self._model = runtime.model
            self._temperature = runtime.temperature
            self._max_tokens = runtime.max_tokens
        except Exception as e:
            logger.error("AlertAnalyzer client init failed: {}", e)
            self._client = None

    async def refresh_client(self) -> None:
        """运行时刷新 LLM 配置，供 /settings/llm 保存后调用。"""
        try:
            doc = await self._db["settings"].find_one({"key": "llm_config"})
            self._runtime_llm_config = doc or {}
        except Exception as e:
            logger.warning("AlertAnalyzer config reload failed: {}", e)
            self._runtime_llm_config = {}
        self._refresh_client()

    async def _get_prompt(self, task_type: str, default: str) -> str:
        """读取激活 Prompt 版本，失败时回退代码默认值。"""
        try:
            doc = await self._db["prompt_templates"].find_one({"task_type": task_type, "active": True})
            if isinstance(doc, dict) and doc.get("content"):
                return doc["content"]
        except Exception as e:
            logger.debug("Prompt DB lookup failed for {}: {}", task_type, e)
        return default

    async def run_worker(self, concurrency: int = 2):
        """queue:alert_analyze 消费者。"""
        if self._client is None:
            logger.warning("AlertAnalyzer worker not started (no LLM client)")
            return
        logger.info("Alert analyzer worker started (concurrency={})", concurrency)
        sem = asyncio.Semaphore(concurrency)

        async def process_one(task_raw: bytes | str):
            async with sem:
                try:
                    task = json.loads(task_raw)
                    alert_id = task["alert_id"]
                    job_id = task.get("job_id") or f"alert:{uuid.uuid4().hex[:8]}"
                    fail_count = task.get("fail_count", 0)
                    project_id = task.get("project_id", "default")
                except Exception as e:
                    logger.error("Bad alert_analyze payload: {}", e)
                    return
                try:
                    await AiJobService(self._db).mark_running(
                        job_id=job_id,
                        type="alert",
                        project_id=project_id,
                        source="alert_analyzer",
                        target_ids=[alert_id],
                        queue_key=ALERT_ANALYZE_QUEUE,
                        retry_count=fail_count,
                        payload=task,
                    )
                    ok = await self.assess_alert(alert_id, job_id=job_id)
                    if not ok:
                        await self._requeue(alert_id, fail_count + 1, job_id=job_id, error="assessment failed")
                except (RateLimitError, APIError) as e:
                    logger.warning("Alert analyze rate limit for {}: {}", alert_id, e)
                    await asyncio.sleep(30)
                    await self._requeue(alert_id, fail_count, job_id=job_id, error=str(e))
                except Exception as e:
                    logger.error("Alert analyze error for {}: {}", alert_id, e)
                    await self._requeue(alert_id, fail_count + 1, job_id=job_id, error=str(e))

        while True:
            try:
                result = await self._redis.blpop(ALERT_ANALYZE_QUEUE, timeout=10)
                if result:
                    _, task_raw = result
                    asyncio.create_task(process_one(task_raw))
            except asyncio.CancelledError:
                logger.info("Alert analyzer worker cancelled")
                break
            except Exception as e:
                logger.error("Alert analyzer loop error: {}", e)
                await asyncio.sleep(2)

    async def assess_alert(self, alert_id: str, job_id: str = "") -> bool:
        """
        对指定告警进行 AI 评估。
        返回 True 表示评估成功，False 表示需重试。
        """
        # 1. 加载告警记录
        alert_doc = await self._db["alert_records"].find_one({"id": alert_id})
        if not alert_doc:
            logger.warning("Alert {} not found for assessment", alert_id)
            return False
        project_id = alert_doc.get("project_id", "default")
        await AiJobService(self._db).mark_running(
            job_id=job_id,
            type="alert",
            project_id=project_id,
            source="alert_analyzer",
            target_ids=[alert_id],
            queue_key=ALERT_ANALYZE_QUEUE,
        )
        if self._ws:
            try:
                await self._ws.broadcast(f"monitor:events:{project_id}", {
                    "type": "alert_assessment",
                    "project_id": project_id,
                    "alert_id": alert_id,
                    "job_id": job_id,
                    "status": "running",
                })
            except Exception as e:
                logger.warning("Alert assess running broadcast failed: {}", e)

        # 2. 加载该 monitor 最近 10 条执行记录（判断是否偶发）
        monitor_id = alert_doc.get("monitor_id", "")
        recent_execs = []
        if monitor_id:
            cursor = self._db["executions"].find(
                {"trigger": "monitor", "project_id": project_id}
            ).sort("started_at", -1).limit(10)
            async for doc in cursor:
                recent_execs.append({
                    "passed": doc.get("passed"),
                    "latency_ms": doc.get("duration_ms", 0),
                    "started_at": str(doc.get("started_at", "")),
                })

        # 3. 构建 prompt
        user_prompt = self._build_user_prompt(alert_doc, recent_execs)
        try:
            raw = await self._call_llm(user_prompt, await self._get_prompt("alert", _ALERT_SYSTEM))
        except Exception as e:
            logger.error("Alert LLM call failed for {}: {}", alert_id, e)
            return False

        if not raw or not raw.strip():
            logger.warning("Alert {} empty LLM response", alert_id)
            return False

        # 4. 解析评估结果
        assessment = self._parse_assessment(raw)
        if assessment is None:
            logger.warning("Alert {} failed to parse assessment", alert_id)
            return False

        # 5. 回写告警记录
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        await self._db["alert_records"].update_one(
            {"id": alert_id},
            {"$set": {
                "ai_severity": assessment["severity"],
                "ai_root_cause": assessment["root_cause"],
                "ai_confidence": assessment["confidence"],
                "ai_assessed_at": now,
                "ai_job_id": job_id,
            }},
        )
        logger.info("Alert {} assessed: severity={} confidence={:.0%}",
                     alert_id, assessment["severity"], assessment["confidence"])

        # 6. 广播评估完成事件（前端刷新展示 AI 标签）
        if self._ws:
            try:
                await self._ws.broadcast(f"monitor:events:{project_id}", {
                    "type": "alert_assessed",
                    "alert_id": alert_id,
                    "job_id": job_id,
                    "status": "done",
                    "severity": assessment["severity"],
                    "root_cause": assessment["root_cause"],
                    "project_id": project_id,
                })
            except Exception as e:
                logger.warning("Alert assess broadcast failed: {}", e)

        await AiJobService(self._db).mark_done(
            job_id=job_id,
            type="alert",
            project_id=project_id,
            source="alert_analyzer",
            target_ids=[alert_id],
        )
        # P5: 记录 L2 项目记忆（fire-and-forget，告警分析结论持久化）
        memory = getattr(self, "_memory", None)
        if memory is not None:
            alert_title = alert_doc.get("title", alert_id)
            safe_fire_and_forget(
                memory.record_l2(
                    project_id, "alert_assessment",
                    f"告警评估: {alert_title[:80]}",
                    f"严重度={assessment['severity']}，置信度={assessment['confidence']:.0%}，根因={assessment['root_cause']}",
                    tags=["alert", assessment["severity"]],
                    source="alert_analyzer",
                ),
                name="memory.record_l2:alert_assessment",
            )
        return True

    def _build_user_prompt(self, alert_doc: dict, recent_execs: list) -> str:
        """构建告警评估 user prompt。"""
        return json.dumps({
            "alert_title": alert_doc.get("title", ""),
            "message": alert_doc.get("message", "")[:800],
            "risk_level": alert_doc.get("risk_level", ""),
            "is_recovery": alert_doc.get("is_recovery", False),
            "recent_executions": recent_execs,
        }, ensure_ascii=False, default=str)

    async def _call_llm(self, user_prompt: str, system_prompt: str, retries: int = 3) -> str:
        """调用 LLM，带指数退避重试。"""
        s = get_settings()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        runtime = resolve_llm_config(getattr(self, "_runtime_llm_config", {}), "alert")
        client = runtime.build_client() if self._client is not None else self._client
        last_error = None
        for attempt in range(retries):
            try:
                resp = await client.chat.completions.create(
                    model=runtime.model, messages=messages,
                    temperature=runtime.temperature,
                    max_tokens=runtime.max_tokens,
                    timeout=s.openai_timeout,
                )
                if not resp.choices:
                    continue
                return resp.choices[0].message.content or ""
            except (RateLimitError, APIError) as e:
                last_error = e
                if attempt < retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise
        if last_error:
            raise last_error
        return ""

    @staticmethod
    def _parse_assessment(raw: str) -> dict | None:
        """解析 LLM 评估输出，校验字段完整性。"""
        try:
            return parse_structured_output("alert", raw)
        except StructuredOutputError:
            return None

    async def _requeue(self, alert_id: str, fail_count: int, job_id: str = "", error: str = "") -> None:
        """重试/DLQ。"""
        alert_doc = await self._db["alert_records"].find_one({"id": alert_id}, {"project_id": 1})
        project_id = (alert_doc or {}).get("project_id", "default")
        if fail_count < MAX_RETRY:
            payload_obj = {"alert_id": alert_id, "project_id": project_id, "job_id": job_id, "fail_count": fail_count, "status": "retry"}
            payload = json.dumps(payload_obj, ensure_ascii=False)
            await self._redis.rpush(ALERT_ANALYZE_QUEUE, payload)
            await AiJobService(self._db).mark_retry(
                job_id=job_id,
                type="alert",
                project_id=project_id,
                source="alert_analyzer",
                target_ids=[alert_id],
                queue_key=ALERT_ANALYZE_QUEUE,
                retry_count=fail_count,
                error=error,
                payload=payload_obj,
            )
            if self._ws:
                await self._ws.broadcast(f"monitor:events:{project_id}", {
                    "type": "alert_assessment",
                    "project_id": project_id,
                    "alert_id": alert_id,
                    "job_id": job_id,
                    "status": "retry",
                    "error": error,
                })
        else:
            payload_obj = {"alert_id": alert_id, "project_id": project_id, "job_id": job_id, "fail_count": fail_count, "status": "dlq", "error": error or "max retry"}
            payload = json.dumps(payload_obj, ensure_ascii=False)
            await self._redis.rpush(ALERT_ANALYZE_DLQ, payload)
            await AiJobService(self._db).mark_dlq(
                job_id=job_id,
                type="alert",
                project_id=project_id,
                source="alert_analyzer",
                target_ids=[alert_id],
                queue_key=ALERT_ANALYZE_QUEUE,
                retry_count=fail_count,
                error=error or "max retry",
                payload=payload_obj,
            )
            if self._ws:
                await self._ws.broadcast(f"monitor:events:{project_id}", {
                    "type": "alert_assessment",
                    "project_id": project_id,
                    "alert_id": alert_id,
                    "job_id": job_id,
                    "status": "dlq",
                    "error": error or "max retry",
                })
            logger.error("Alert {} moved to DLQ after {} retries", alert_id, fail_count)
