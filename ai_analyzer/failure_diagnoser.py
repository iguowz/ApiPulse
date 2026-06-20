"""
Phase 4: AI 执行失败诊断服务
- 异步消费 queue:diagnose_failure
- 调用 LLM 分析失败根因：env_mismatch / timeout / assertion_error / api_change / data_issue / unknown
- 诊断结果写入 execution.diagnosis，通过 WebSocket 广播
- 若 root_cause="api_change" 则自动触发 DocDiff 检测（待实现）
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta

import httpx
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase
from openai import AsyncOpenAI, RateLimitError, APIError
from redis.asyncio import Redis

from config.settings import get_settings
from services.ai_job_service import AiJobService
from services.llm_config_service import resolve_llm_config
from services.structured_output_service import StructuredOutputError, parse_structured_output

# Redis 队列名
DIAGNOSE_QUEUE = "queue:diagnose_failure"
DIAGNOSE_DLQ = "queue:diagnose_failure:dlq"
MAX_RETRY = 3


def _structured_error_detail(exc: Exception) -> str:
    """结构化输出失败时带上 raw preview，便于 AI Job/DLQ 排查。"""
    if isinstance(exc, StructuredOutputError):
        detail = str(exc)
        if exc.raw_output_preview:
            detail = f"{detail}; raw_output_preview={exc.raw_output_preview[:300]}"
        return detail[:800]
    return str(exc)[:800]


# ── AI 提示词 ─────────────────────────────────────────────

_DIAGNOSE_SYSTEM = """\
你是 API 测试诊断专家。你的任务是分析失败的测试执行步骤，判断根本原因并提供修复建议。

## 根因分类 (root_cause)
- "env_mismatch"：环境配置问题（域名/端口错误、环境变量未设置、鉴权信息过期等）
- "timeout"：响应超时（网络延迟、服务端处理慢、超时阈值设置过低等）
- "assertion_error"：断言失败（响应数据与期望值不匹配，可能是数据变化或断言规则不合理）
- "api_change"：接口变更（请求/响应结构变化、字段类型变更、新增必填参数等）
- "data_issue"：测试数据问题（参数值无效、依赖数据不存在、数据状态不一致等）
- "unknown"：其他/无法判定

## 输出格式
仅输出纯 JSON 对象，不要加 markdown 代码围栏（```json），不要加任何解释文字：

{
  "root_cause": "env_mismatch|timeout|assertion_error|api_change|data_issue|unknown",
  "explanation": "具体原因说明（1-3句话，中文）",
  "suggested_fix": "建议修复方案（1-3句话，中文，给出可操作的具体步骤）",
  "confidence": 0.0-1.0
}

## 判定规则
1. 若错误信息包含 "Connection refused" / "Name or service not known" / "No route to host" → env_mismatch
2. 若错误信息包含 "timeout" / "Timeout" / "timed out" → timeout
3. 若断言结果中有字段不匹配，且错误不是网络/连接类 → assertion_error
4. 若状态码为 4xx 且非鉴权相关 → api_change（接口可能变更了输入要求）
5. 若状态码为 401/403 → env_mismatch（鉴权信息失效或缺失）
6. 若请求参数中包含测试数据且响应提示"不存在"/"无效" → data_issue
7. 其他情况判断为 unknown
"""

_DIAGNOSE_USER = """\
请分析以下失败测试执行的根因：

## 执行信息
- 执行ID：{execution_id}
- API ID：{api_id}
- 执行类型：{exec_type}
- 开始时间：{started_at}

## 失败步骤
- 步骤ID：{step_id}
- 步骤名称：{step_name}
- 请求方法：{method}
- 请求路径：{path}

### 请求信息
- 请求头：{request_headers}
- 请求参数：{request_params}

### 响应信息
- 状态码：{status_code}
- 响应体：{response_body}
- 响应时间：{latency_ms}ms

### 错误信息
{error_message}

### 断言结果
{assert_results}

请判定根因并输出 JSON 对象。"""


class FailureDiagnoserService:
    """失败诊断 worker：BLPOP 消费 queue:diagnose_failure → LLM 分析 → 回写 execution"""

    def __init__(self, db: AsyncIOMotorDatabase, redis: Redis, ws_manager=None):
        s = get_settings()
        self._db = db
        self._redis = redis
        self._ws = ws_manager
        self._client = AsyncOpenAI(
            api_key=s.openai_api_key,
            base_url=s.openai_base_url,
            # 超时从 settings 读取（默认 120s），本地模型可能较慢
            timeout=httpx.Timeout(s.openai_timeout, connect=10.0),
        )
        self._model = s.openai_model
        self._temperature = s.openai_temperature
        self._max_tokens = s.openai_max_tokens
        self._runtime_llm_config: dict[str, Any] = {}

    async def refresh_client(self) -> None:
        """运行时刷新 LLM 客户端配置，保证失败诊断 worker 与设置页保持一致。"""
        self._runtime_llm_config = await self._db["settings"].find_one({"key": "llm_config"}) or {}
        runtime = resolve_llm_config(self._runtime_llm_config, "diagnose")
        self._client = runtime.build_client()
        self._model = runtime.model
        self._temperature = runtime.temperature
        self._max_tokens = runtime.max_tokens

    async def _get_prompt(self, task_type: str, default: str) -> str:
        """读取激活 Prompt 版本，失败时回退代码默认值。"""
        try:
            doc = await self._db["prompt_templates"].find_one({"task_type": task_type, "active": True})
            if isinstance(doc, dict) and doc.get("content"):
                return doc["content"]
        except Exception as e:
            logger.debug("Prompt DB lookup failed for {}: {}", task_type, e)
        return default

    # ── Worker 主循环 ─────────────────────────────────────

    async def run_worker(self, concurrency: int = 2):
        """
        queue:diagnose_failure 消费者 —— AI 失败诊断。
        诊断结果写入 execution.diagnosis 字段，通过 WebSocket 广播。
        """
        logger.info("Failure diagnoser worker started (concurrency={})", concurrency)
        sem = asyncio.Semaphore(concurrency)

        async def process_one(task_raw: bytes | str):
            async with sem:
                fail_count = 0
                try:
                    task = json.loads(task_raw)
                    execution_id = task["execution_id"]
                    fail_count = task.get("fail_count", 0)
                    job_id = task.get("job_id") or f"diagnose:{execution_id}"
                    project_id = task.get("project_id", "default")
                except Exception as e:
                    logger.error("Bad diagnose task payload: {}", e)
                    return

                try:
                    await AiJobService(self._db).mark_running(
                        job_id=job_id,
                        type="diagnose",
                        project_id=project_id,
                        source="failure_diagnoser",
                        target_ids=[execution_id],
                        queue_key=DIAGNOSE_QUEUE,
                        retry_count=fail_count,
                        payload=task,
                    )
                    ok = await self.diagnose(execution_id)
                    if not ok:
                        # 诊断失败（非异常）→ 增加 fail_count 重试
                        await self._requeue_or_dlq(execution_id, fail_count + 1, project_id=project_id)
                except RateLimitError:
                    # 限流错误：暂停60s后重入队，不增加 fail_count（限制侧问题，非任务本身失败）
                    logger.warning("Diagnose rate limit hit, requeue execution_id={}", execution_id)
                    await asyncio.sleep(60)
                    await self._requeue_or_dlq(execution_id, fail_count, project_id=project_id)
                except APIError as e:
                    # API 错误（如 500）→ 增加 fail_count 重试
                    logger.error("OpenAI API error for diagnose {}: {}", execution_id, e)
                    await self._requeue_or_dlq(execution_id, fail_count + 1, project_id=project_id, error=str(e))
                except Exception as e:
                    # 未知错误 → 保守策略：增加 fail_count 重试
                    logger.error("Diagnose worker task error for {}: {}", execution_id, e)
                    await self._requeue_or_dlq(execution_id, fail_count + 1, project_id=project_id, error=str(e))

        while True:
            try:
                result = await self._redis.blpop(DIAGNOSE_QUEUE, timeout=5)
                if result:
                    # 队列中有消息 → 创建新协程处理（不阻塞主循环继续取消息）
                    _, task_raw = result
                    asyncio.create_task(process_one(task_raw))
                # result 为 None（超时）→ 继续循环等待
            except asyncio.CancelledError:
                # 收到取消信号 → 优雅退出循环
                logger.info("Failure diagnoser worker cancelled")
                break
            except Exception as e:
                # blpop 异常 → 短暂休眠后重试，避免快速失败循环
                logger.error("Diagnose worker loop error: {}", e)
                await asyncio.sleep(2)

    # ── 核心诊断逻辑 ─────────────────────────────────────

    async def diagnose(self, execution_id: str) -> bool:
        """
        对指定执行记录进行 AI 失败诊断。
        返回 True 表示诊断成功，False 表示需要重试。
        """
        # 1. 加载执行记录
        exec_doc = await self._db["executions"].find_one({"id": execution_id})
        if not exec_doc:
            logger.warning("Execution {} not found for diagnosis", execution_id)
            return False  # 执行记录不存在，不重试（返回 False 会进入 DLQ）

        # 2. 只诊断失败的执行
        if exec_doc.get("passed"):
            logger.debug("Execution {} passed, skip diagnosis", execution_id)
            return True  # 通过的执行无需诊断，视为成功

        # 3. 提取失败步骤的信息（找第一个非跳过且未通过的步骤）
        steps = exec_doc.get("steps") or []
        failed_step = None
        for s in steps:
            if not s.get("skipped") and not s.get("passed"):
                failed_step = s
                break

        if not failed_step:
            # 没有找到失败步骤 → 可能全部跳过但仍标记失败，使用第一条步骤
            if steps:
                failed_step = steps[0]
            else:
                logger.warning("Execution {} has no steps, skip diagnosis", execution_id)
                return True  # 无步骤无法诊断，视为完成

        # 4. 更新诊断状态为 running
        await self._db["executions"].update_one(
            {"id": execution_id},
            {"$set": {"diagnosis_status": "running"}},
        )

        # 5. 构建诊断 prompt 并调用 LLM
        try:
            step_result = failed_step
            # 安全截断响应体（超出 2000 字符可能超出 token 限制且无诊断价值）
            response_body_raw = json.dumps(
                step_result.get("response_received") or {},
                ensure_ascii=False,
                default=str,
            )
            response_body = response_body_raw[:2000]

            # 提取失败断言（仅未通过的）
            assert_results_raw = step_result.get("assert_results") or []
            failed_asserts = [a for a in assert_results_raw if not a.get("passed")]
            assert_str = json.dumps(failed_asserts, ensure_ascii=False, default=str)[:1500]

            user_prompt = _DIAGNOSE_USER.format(
                execution_id=execution_id,
                api_id=exec_doc.get("api_id", ""),
                exec_type=exec_doc.get("type", ""),
                started_at=str(exec_doc.get("started_at", "")),
                step_id=step_result.get("step_id", ""),
                step_name=step_result.get("name", step_result.get("step_id", "")),
                method=step_result.get("request_sent", {}).get("method", ""),
                path=step_result.get("request_sent", {}).get("path", ""),
                request_headers=json.dumps(
                    step_result.get("request_sent", {}).get("headers", {}),
                    ensure_ascii=False,
                    default=str,
                )[:800],
                request_params=json.dumps(
                    step_result.get("request_sent", {}).get("params", {}),
                    ensure_ascii=False,
                    default=str,
                )[:1000],
                status_code=step_result.get("response_received", {}).get("status_code", ""),
                response_body=response_body,
                latency_ms=step_result.get("latency_ms", 0),
                error_message=step_result.get("error", exec_doc.get("failure_reason", ""))[:500],
                assert_results=assert_str,
            )

            llm_response = await self._call_llm(
                user_prompt,
                await self._get_prompt("diagnose", _DIAGNOSE_SYSTEM),
                max_tokens=2048,
            )

            # 6. 解析 LLM 响应
            diagnosis = self._parse_diagnosis(llm_response, execution_id)
            if diagnosis is None:
                # 响应解析失败 → 重试
                logger.warning("Failed to parse diagnosis response for {}", execution_id)
                return False

        except Exception as e:
            logger.error("LLM call failed for diagnosis {}: {}", execution_id, e)
            # 更新诊断状态为 failed
            await self._db["executions"].update_one(
                {"id": execution_id},
                {"$set": {
                    "diagnosis_status": "failed",
                    "diagnosis": {"error": str(e), "root_cause": "unknown", "explanation": "AI 诊断服务调用失败", "suggested_fix": "请人工排查日志", "confidence": 0},
                }},
            )
            raise  # 让 worker 层处理重试/DLQ

        # 7. 回写诊断结果到执行记录
        diagnose_doc = {
            "diagnosis": {
                **diagnosis,
                "diagnosed_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                "diagnosed_step": failed_step.get("step_id", ""),
            },
            "diagnosis_status": "done",
        }
        await self._db["executions"].update_one(
            {"id": execution_id},
            {"$set": diagnose_doc},
        )

        # 8. 广播诊断完成事件（通过 WebSocket 通知前端刷新）
        await self._broadcast("ai_analysis", {
            "type": "diagnosis_done",
            "execution_id": execution_id,
            "job_id": f"diagnose:{execution_id}",
            "status": "done",
            "root_cause": diagnosis.get("root_cause"),
            "summary": diagnosis.get("explanation", ""),
            "project_id": exec_doc.get("project_id", "default"),
        })
        await AiJobService(self._db).mark_done(
            job_id=f"diagnose:{execution_id}",
            type="diagnose",
            project_id=exec_doc.get("project_id", "default"),
            source="failure_diagnoser",
            target_ids=[execution_id],
        )

        logger.info(
            "Diagnosis done for {}: root_cause={} confidence={:.0%}",
            execution_id, diagnosis.get("root_cause"), diagnosis.get("confidence", 0),
        )

        # 9. 若根因为 api_change，记录诊断→差异/重分析联动，并自动触发该 API 重新分析。
        # 此前是 TODO，诊断出"接口变更"后无后续动作，用户仍需手动重新分析。
        # 现在自动入队 analyze，让 AI 重新生成 doc/asserts，对比诊断结论是否一致。
        # 选择 enqueue_analyze 而非 DocDiff：DocDiff 依赖 import_diffs 记录（需新导入触发），
        # 诊断场景下接口已被分析过，直接重新分析更直接有效；force=True 确保覆盖现有文档。
        if diagnosis.get("root_cause") == "api_change":
            api_id = exec_doc.get("api_id", "")
            link_doc = {
                "execution_id": execution_id,
                "api_id": api_id,
                "project_id": exec_doc.get("project_id", "default"),
                "root_cause": "api_change",
                "status": "reanalyze_queued",
                "created_at": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None),
            }
            if api_id and self._redis is not None:
                try:
                    await self._db["diagnosis_diff_links"].insert_one(link_doc)
                    from services.api_service import ApiService
                    api_service = ApiService(self._db)
                    ok = await api_service.enqueue_analyze(api_id, self._redis, force=True)
                    if ok:
                        logger.info(
                            "Diagnosis triggered re-analyze for api {} (root_cause=api_change)",
                            api_id,
                        )
                        # 广播事件通知前端：已自动触发重新分析
                        await self._broadcast("ai_analysis", {
                            "type": "diagnosis_triggered_reanalyze",
                            "execution_id": execution_id,
                            "job_id": f"diagnose:{execution_id}",
                            "status": "reanalyze_queued",
                            "api_id": api_id,
                            "root_cause": "api_change",
                            "project_id": exec_doc.get("project_id", "default"),
                        })
                    else:
                        logger.warning(
                            "Diagnosis failed to enqueue re-analyze for api {} (queue full?)",
                            api_id,
                        )
                except Exception as e:
                    # 重新分析入队失败不影响诊断结果本身（诊断已完成并回写）
                    logger.warning("Diagnosis re-analyze trigger failed for api {}: {}", api_id, e)

        return True

    async def _broadcast(self, key: str, data: dict) -> None:
        """安全广播并补齐 AI 事件追踪字段。"""
        if key == "ai_analysis" or key.startswith("ai_analysis:"):
            data.setdefault("project_id", "default")
            data.setdefault("job_id", data.get("execution_id") or "")
            data.setdefault("type", "diagnosis")
            data.setdefault("status", "running")
            if key == "ai_analysis" and data.get("project_id"):
                key = f"ai_analysis:{data['project_id']}"
        if self._ws:
            try:
                await self._ws.broadcast(key, data)
            except Exception as e:
                logger.warning("Failed to broadcast diagnosis event: {}", e)

    # ── LLM 调用 ──────────────────────────────────────────

    async def _call_llm(
        self, user_prompt: str, system_prompt: str | None = None,
        max_tokens: int | None = None,
        retries: int = 3,
    ) -> str:
        """
        调用 LLM，支持 system role 消息、可配置 max_tokens、指数退避重试。
        与 AiAnalyzerService._call_llm 保持一致的模式。
        """
        s = get_settings()
        timeout = s.openai_timeout

        messages = []
        if system_prompt:
            # 有 system_prompt 时作为首条消息注入，指导 LLM 行为模式
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        runtime = resolve_llm_config(getattr(self, "_runtime_llm_config", {}), "diagnose")
        client = runtime.build_client()
        effective_max_tokens = runtime.max_tokens
        last_error = None

        for attempt in range(retries):
            try:
                resp = await client.chat.completions.create(
                    model=runtime.model,
                    messages=messages,
                    temperature=runtime.temperature,
                    max_tokens=effective_max_tokens,
                    timeout=timeout,
                )
                # 防御：LLM 可能返回空 choices 数组，避免 IndexError
                if not resp.choices:
                    logger.warning("LLM returned empty choices (attempt {}/{})", attempt + 1, retries)
                    continue  # 视为可重试的临时问题，进入下一次循环
                return resp.choices[0].message.content or ""
            except (RateLimitError, APIError) as e:
                # 可重试错误（限流/服务端错误）→ 指数退避 1s→2s→4s
                last_error = e
                if attempt < retries - 1:
                    wait = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning("LLM call failed (attempt {}/{}), retrying in {}s: {}", attempt + 1, retries, wait, e)
                    await asyncio.sleep(wait)
                else:
                    # 最后一次重试也失败 → 抛出异常，由上层 worker 处理
                    logger.error("LLM call exhausted {} retries: {}", retries, e)
                    raise
            except Exception:
                # 非 OpenAI 错误（如网络超时、连接拒绝）→ 不重试，直接抛出
                raise
        # 所有重试均返回空 choices（未抛出异常但无有效内容）→ 抛出最后一个错误或直接失败
        if last_error:
            raise last_error
        return ""

    # ── 解析 LLM 响应 ────────────────────────────────────

    @staticmethod
    def _parse_diagnosis(raw: str, execution_id: str) -> dict | None:
        """
        解析 LLM 返回的诊断 JSON。
        返回标准化的 diagnosis dict，解析失败返回 None。
        """
        try:
            return parse_structured_output("diagnose", raw)
        except StructuredOutputError as e:
            logger.warning("Failed to parse diagnosis JSON for {}: {}", execution_id, _structured_error_detail(e))
            return None

    # ── 重试/DLQ 管理 ────────────────────────────────────

    async def _requeue_or_dlq(self, execution_id: str, fail_count: int, project_id: str = "default", error: str = "") -> None:
        """
        失败重试/DLQ 管理：
        - fail_count < MAX_RETRY：重新 rpush 到原队列
        - fail_count >= MAX_RETRY：rpush 到 DLQ，设置 diagnosis_status=failed
        """
        if fail_count < MAX_RETRY:
            # 重试：指数退避 rpush 到原队列
            delay = 2 ** (fail_count - 1)  # 1s, 2s, 4s
            logger.info("Requeueing diagnosis {} (attempt {}/{}) after {}s", execution_id, fail_count + 1, MAX_RETRY, delay)
            await asyncio.sleep(delay)
            await self._redis.rpush(DIAGNOSE_QUEUE, json.dumps({
                "execution_id": execution_id,
                "project_id": project_id,
                "fail_count": fail_count,
                "job_id": f"diagnose:{execution_id}",
                "status": "retry",
            }, ensure_ascii=False))
            await AiJobService(self._db).mark_retry(
                job_id=f"diagnose:{execution_id}",
                type="diagnose",
                project_id=project_id,
                source="failure_diagnoser",
                target_ids=[execution_id],
                queue_key=DIAGNOSE_QUEUE,
                retry_count=fail_count,
                error=error,
                payload={"execution_id": execution_id, "project_id": project_id, "fail_count": fail_count},
            )
        else:
            # 超过重试次数 → 移入 DLQ，标记诊断失败
            logger.error("Diagnosis {} failed after {} retries, moving to DLQ", execution_id, MAX_RETRY)
            await self._redis.rpush(DIAGNOSE_DLQ, json.dumps({
                "execution_id": execution_id,
                "project_id": project_id,
                "fail_count": fail_count,
                "job_id": f"diagnose:{execution_id}",
                "status": "dlq",
                "error": error or "诊断服务重试耗尽",
            }, ensure_ascii=False))
            await AiJobService(self._db).mark_dlq(
                job_id=f"diagnose:{execution_id}",
                type="diagnose",
                project_id=project_id,
                source="failure_diagnoser",
                target_ids=[execution_id],
                queue_key=DIAGNOSE_QUEUE,
                retry_count=fail_count,
                error=error or "诊断服务重试耗尽",
                payload={"execution_id": execution_id, "project_id": project_id, "fail_count": fail_count, "error": error or "诊断服务重试耗尽"},
            )
            await self._db["executions"].update_one(
                {"id": execution_id},
                {"$set": {
                    "diagnosis_status": "failed",
                    "diagnosis": {"error": "诊断服务重试耗尽", "root_cause": "unknown", "explanation": "AI 诊断服务多次重试后仍未成功", "suggested_fix": "请人工排查日志", "confidence": 0},
                }},
            )
