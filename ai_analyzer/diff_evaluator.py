"""
AI 差异评估服务 —— 异步消费 queue:diff_evaluate，调用 LLM 评估字段差异的根因和严重程度，
根据评估结果自动修复（ai_doc_error）或确认（api_evolution/breaking_change/new_field）。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase
from openai import AsyncOpenAI, RateLimitError, APIError
from redis.asyncio import Redis

from config.settings import get_settings
from models.dsl import ImportDiffStatus
from models.generation_version import (
    GenerationSource,
    GenerationStatus,
    GenerationType,
    GenerationVersion,
)
from services.ai_job_service import AiJobService
from services.llm_config_service import resolve_llm_config
from services.structured_output_service import StructuredOutputError, parse_structured_output

# Redis 队列名（与 diff_service.py 保持一致）
DIFF_EVALUATE_QUEUE = "queue:diff_evaluate"
DIFF_EVALUATE_DLQ = "queue:diff_evaluate:dlq"
MAX_RETRY = 3

# ── AI 提示词 ─────────────────────────────────────────────

_DIFF_EVAL_SYSTEM = """\
你是 API 变更分析专家。你的任务是分析新旧 API 之间的字段差异，判断差异的根因和严重程度。

## 评估维度
1. **is_valid** (bool)：差异是否真实有效（排除随机值波动、时间戳等无关差异）
2. **root_cause** (string)：
   - "ai_doc_error"：旧 API 的 AI 文档遗漏或描述错误（实际接口未变，文档问题）
   - "api_evolution"：API 正常演进（新增可选字段、新增可选参数等，向后兼容）
   - "api_breaking_change"：破坏性变更（字段类型变化、必填变更、字段移除等，不向后兼容）
   - "api_new_field"：纯粹的新增字段（请求或响应新增字段，旧 API 无此字段）
3. **severity** (string)："low" / "medium" / "high" / "critical"
4. **reasoning** (string)：简短分析过程（1-3句话）
5. **fix_suggestion** (object|null)：仅当 root_cause="ai_doc_error" 时提供修复建议，格式：
   {"doc.params": [...], "doc.response_fields": [...]}
   包含需要更新到旧 API 文档的完整字段列表（合并现有字段+修正/新增字段）

## 严重程度判定
- low：可选字段新增/移除、描述性差异
- medium：字段类型变更（兼容）、必填→可选
- high：必填字段新增/移除、字段类型变更（不兼容）
- critical：核心鉴权字段变更、数据结构完全重构

## 规则
1. 忽略仅值不同的情况（如 example 值不同但类型一致），关注结构和类型层面的差异
2. 类型从 string→number 且值语义不变（如 "100"→100）判定为 medium
3. 仅输出纯 JSON 对象，不要加 markdown 代码围栏（```json），不要加任何解释文字
"""

_DIFF_EVAL_USER = """\
## 已分析 API 文档（旧）
- ID：{existing_api_id}
- 路径：{api_path} {method}
- 请求参数：{existing_params}
- 响应字段：{existing_response_fields}

## 新导入 API 文档
- ID：{new_api_id}
- 请求参数：{new_params}
- 响应字段：{new_response_fields}

## 检测到的字段差异
{diffs_json}

请评估以上差异，输出 JSON 对象。"""

_AUTO_FIX_SYSTEM = """\
你是 API 文档修正专家。根据差异分析和原始文档，生成修正后的完整文档字段列表。

规则：
1. 你收到的 fix_suggestion 是建议的修正操作，你需要基于旧文档字段列表和差异，输出修正后的**完整** params 和 response_fields
2. 每个字段必须包含 name, location, type, required, description, example
3. 对不确定的字段保持原样，仅修正明确有问题的字段
4. 仅输出纯 JSON 对象格式：{"doc.params": [...], "doc.response_fields": [...]}
   不要加 markdown 代码围栏，不要加任何解释文字
"""


class DiffEvaluatorService:
    """差异评估 worker：BLPOP 消费 + LLM 评估 + 自动修复"""

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
        """运行时刷新 LLM 客户端配置，保证设置页保存后差异评估 worker 同步生效。"""
        self._runtime_llm_config = await self._db["settings"].find_one({"key": "llm_config"}) or {}
        runtime = resolve_llm_config(self._runtime_llm_config, "diff")
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
        queue:diff_evaluate 消费者 —— AI 差异评估。
        评估结果通过 WebSocket 广播到 ai_analysis channel（type=diff_eval）。
        """
        logger.info("Diff evaluator worker started (concurrency={})", concurrency)
        sem = asyncio.Semaphore(concurrency)

        async def process_one(task_raw: bytes | str):
            async with sem:
                fail_count = 0
                try:
                    task = json.loads(task_raw)
                    diff_id = task["diff_id"]
                    fail_count = task.get("fail_count", 0)
                    job_id = task.get("job_id") or f"diff:{diff_id}"
                    project_id = task.get("project_id", "default")
                except Exception as e:
                    logger.error("Bad diff_eval task payload: {}", e)
                    return

                try:
                    await AiJobService(self._db).mark_running(
                        job_id=job_id,
                        type="diff",
                        project_id=project_id,
                        source="diff_evaluator",
                        target_ids=[diff_id],
                        queue_key=DIFF_EVALUATE_QUEUE,
                        retry_count=fail_count,
                        payload=task,
                    )
                    ok = await self.evaluate_diff(diff_id)
                    if not ok:
                        await self._requeue_or_dlq(diff_id, fail_count + 1, project_id=project_id)
                except RateLimitError:
                    logger.warning("Diff eval rate limit hit, requeue diff_id={}", diff_id)
                    await asyncio.sleep(60)
                    await self._requeue_or_dlq(diff_id, fail_count, project_id=project_id)
                except APIError as e:
                    logger.error("OpenAI API error for diff {}: {}", diff_id, e)
                    await self._requeue_or_dlq(diff_id, fail_count + 1, project_id=project_id, error=str(e))
                except Exception as e:
                    logger.error("Diff eval worker error for {}: {}", diff_id, e)
                    await self._requeue_or_dlq(diff_id, fail_count + 1, project_id=project_id, error=str(e))

        while True:
            try:
                result = await self._redis.blpop(DIFF_EVALUATE_QUEUE, timeout=5)
                if result:
                    _, task_raw = result
                    asyncio.create_task(process_one(task_raw))
            except asyncio.CancelledError:
                logger.info("Diff evaluator worker cancelled")
                break
            except Exception as e:
                logger.error("Diff evaluator worker loop error: {}", e)
                await asyncio.sleep(2)

    # ── 核心评估逻辑 ──────────────────────────────────────

    async def evaluate_diff(self, diff_id: str) -> bool:
        """
        加载差异记录 + 新旧 API 文档 → 构建 prompt → LLM 评估 → 更新记录。
        返回 True 表示处理成功，False 表示需要重试。
        """
        # 1. 加载差异记录
        diff_doc = await self._db["import_diffs"].find_one({"id": diff_id})
        if not diff_doc:
            logger.warning("Diff record not found: {}", diff_id)
            return True  # 记录已不存在，视为成功（无需重试）

        # 2. 加载新旧 API 文档
        existing_api = await self._db["api_dsls"].find_one({"id": diff_doc["existing_api_id"]})
        new_api = await self._db["api_dsls"].find_one({"id": diff_doc["new_api_id"]})
        if not existing_api or not new_api:
            logger.warning("API docs missing for diff {}", diff_id)
            # 标记为 dismissed（关联 API 已删除）
            await self._db["import_diffs"].update_one(
                {"id": diff_id},
                {"$set": {"status": ImportDiffStatus.DISMISSED.value, "updated_at": _now()}},
            )
            return True

        # 3. 构建评估 prompt
        existing_doc = existing_api.get("doc") or {}
        new_doc = new_api.get("doc") or {}
        user_prompt = _DIFF_EVAL_USER.format(
            existing_api_id=diff_doc["existing_api_id"],
            new_api_id=diff_doc["new_api_id"],
            api_path=diff_doc.get("api_path", ""),
            method=diff_doc.get("method", ""),
            existing_params=json.dumps(existing_doc.get("params", []), ensure_ascii=False, indent=2),
            existing_response_fields=json.dumps(existing_doc.get("response_fields", []), ensure_ascii=False, indent=2),
            new_params=json.dumps(new_doc.get("params", []), ensure_ascii=False, indent=2),
            new_response_fields=json.dumps(new_doc.get("response_fields", []), ensure_ascii=False, indent=2),
            diffs_json=json.dumps(diff_doc.get("fields_diff", []), ensure_ascii=False, indent=2),
        )

        s = get_settings()
        # 4. 调用 LLM 评估（差异评估输出 JSON 较短，max_tokens 从 settings 读取）
        # RateLimitError/APIError 向上传播到 run_worker 做专门处理（速率限制需等60s、不增fail_count）
        # 其他异常（网络错误等）捕获后返回 False 触发重试
        try:
            raw = await self._call_llm(
                user_prompt,
                await self._get_prompt("diff_eval", _DIFF_EVAL_SYSTEM),
                max_tokens=s.openai_max_tokens_diff_eval,
            )
        except (RateLimitError, APIError):
            raise  # 传播到 run_worker 的专门 except 分支
        except Exception as e:
            logger.error("LLM call failed for diff {}: {}", diff_id, e)
            return False

        if not raw:
            return False

        try:
            evaluation = parse_structured_output("diff", raw)
        except StructuredOutputError as e:
            logger.warning("Diff eval structured output invalid for {}: {}", diff_id, self._structured_error_detail(e))
            return False

        # 5. 根据评估结果更新差异记录
        is_valid = evaluation.get("is_valid", True)
        root_cause = evaluation.get("root_cause", "")
        severity = evaluation.get("severity", "low")
        reasoning = evaluation.get("reasoning", "")

        if not is_valid:
            # 无效差异（随机波动等）→ 自动 dismiss
            await self._db["import_diffs"].update_one(
                {"id": diff_id},
                {"$set": {
                    "status": ImportDiffStatus.DISMISSED.value,
                    "severity": severity,
                    "root_cause": "noise",
                    "ai_evaluation": evaluation,
                    "updated_at": _now(),
                }},
            )
            await self._broadcast("ai_analysis", {
                "type": "diff_eval",
                "diff_id": diff_id,
                "job_id": f"diff:{diff_id}",
                "status": "dismissed",
                "reason": "noise",
                "project_id": diff_doc.get("project_id", "default"),
            })
            await AiJobService(self._db).mark_done(
                job_id=f"diff:{diff_id}",
                type="diff",
                project_id=diff_doc.get("project_id", "default"),
                source="diff_evaluator",
                target_ids=[diff_id],
            )
            logger.info("Diff {} dismissed as noise", diff_id)
            return True

        if root_cause == "ai_doc_error":
            # AI 文档问题 → 生成待审核修复版本，禁止绕过 GenerationVersion 直接写 DSL。
            fix_suggestion = evaluation.get("fix_suggestion")
            if fix_suggestion and isinstance(fix_suggestion, dict):
                generation_id = await self._create_doc_fix_generation(diff_id, diff_doc["existing_api_id"], fix_suggestion, evaluation)
                if generation_id:
                    await self._db["import_diffs"].update_one(
                        {"id": diff_id},
                        {"$set": {
                            "status": ImportDiffStatus.CONFIRMED.value,
                            "severity": severity,
                            "root_cause": root_cause,
                            "ai_evaluation": {**evaluation, "generation_id": generation_id},
                            "updated_at": _now(),
                        }},
                    )
                    await self._broadcast("ai_analysis", {
                        "type": "diff_eval",
                        "diff_id": diff_id,
                        "job_id": f"diff:{diff_id}",
                        "status": "pending_review",
                        "root_cause": root_cause,
                        "severity": severity,
                        "generation_id": generation_id,
                        "project_id": diff_doc.get("project_id", "default"),
                    })
                    await AiJobService(self._db).mark_pending_review(
                        job_id=f"diff:{diff_id}",
                        type="diff",
                        project_id=diff_doc.get("project_id", "default"),
                        source="diff_evaluator",
                        target_ids=[diff_id],
                        generation_ids=[generation_id],
                    )
                    logger.info("Diff {} doc fix saved as generation {}", diff_id, generation_id)
                    return True
            # 修复版本创建失败 → 标记为 confirmed 等待用户手动处理
            await self._db["import_diffs"].update_one(
                {"id": diff_id},
                {"$set": {
                    "status": ImportDiffStatus.CONFIRMED.value,
                    "severity": severity,
                    "root_cause": root_cause,
                    "ai_evaluation": evaluation,
                    "updated_at": _now(),
                }},
            )
        else:
            # api_evolution / api_breaking_change / api_new_field → 确认差异
            await self._db["import_diffs"].update_one(
                {"id": diff_id},
                {"$set": {
                    "status": ImportDiffStatus.CONFIRMED.value,
                    "severity": severity,
                    "root_cause": root_cause,
                    "ai_evaluation": evaluation,
                    "updated_at": _now(),
                }},
            )

        await self._broadcast("ai_analysis", {
            "type": "diff_eval",
            "diff_id": diff_id,
            "job_id": f"diff:{diff_id}",
            "status": "confirmed",
            "root_cause": root_cause,
            "severity": severity,
            "project_id": diff_doc.get("project_id", "default"),
        })
        await AiJobService(self._db).mark_done(
            job_id=f"diff:{diff_id}",
            type="diff",
            project_id=diff_doc.get("project_id", "default"),
            source="diff_evaluator",
            target_ids=[diff_id],
        )
        logger.info("Diff {} evaluated: root_cause={} severity={}", diff_id, root_cause, severity)
        return True

    # ── 审核式修复 ────────────────────────────────────────

    async def _create_doc_fix_generation(
        self, diff_id: str, api_id: str, fix_suggestion: dict, evaluation: dict,
    ) -> str | None:
        """
        当根因为 ai_doc_error 时，调用 LLM 生成修正后的完整文档，并保存为待审核 GenerationVersion。
        不直接更新 API DSL，避免 AI 自动修复绕过人工审核。
        """
        # 加载旧 API 当前文档
        api_doc = await self._db["api_dsls"].find_one({"id": api_id})
        if not api_doc:
            return None

        existing_doc = api_doc.get("doc") or {}
        existing_params = json.dumps(existing_doc.get("params", []), ensure_ascii=False, indent=2)
        existing_resp = json.dumps(existing_doc.get("response_fields", []), ensure_ascii=False, indent=2)

        auto_fix_prompt = f"""\
## 当前 API 文档（需要修正）
- 请求参数：{existing_params}
- 响应字段：{existing_resp}

## 检测到的差异
{json.dumps(evaluation.get("fix_suggestion", {}), ensure_ascii=False, indent=2)}

## 差异分析
{json.dumps(evaluation, ensure_ascii=False, indent=2)}

请输出修正后的完整字段列表。"""

        try:
            s = get_settings()
            raw = await self._call_llm(auto_fix_prompt, _AUTO_FIX_SYSTEM, max_tokens=s.openai_max_tokens_doc)
        except Exception as e:
            logger.error("Auto-fix LLM call failed for diff {}: {}", diff_id, e)
            return None

        if not raw:
            return None

        try:
            fix_data = parse_structured_output("doc_fix", raw)
        except StructuredOutputError as e:
            logger.warning("Auto-fix structured output invalid for {}: {}", diff_id, self._structured_error_detail(e))
            return None

        fixed_doc = dict(existing_doc)
        if "doc.params" in fix_data or "params" in fix_data:
            fixed_doc["params"] = fix_data.get("doc.params") or fix_data.get("params", [])
        if "doc.response_fields" in fix_data or "response_fields" in fix_data:
            fixed_doc["response_fields"] = fix_data.get("doc.response_fields") or fix_data.get("response_fields", [])

        if fixed_doc == existing_doc:
            return None

        gv = GenerationVersion(
            api_id=api_id,
            type=GenerationType.DOC,
            status=GenerationStatus.PENDING_REVIEW,
            content=fixed_doc,
            summary=f"Diff 修复建议：{api_doc.get('name') or api_doc.get('request', {}).get('path') or api_id}",
            model=self._model,
            prompt=auto_fix_prompt,
            api_ids=[api_id],
            project_id=api_doc.get("project_id", "default"),
            source=GenerationSource.DIFF_EVALUATOR,
            job_id=f"diff:{diff_id}",
        )
        doc = gv.model_dump()
        doc.pop("id", None)
        result = await self._db["generation_versions"].insert_one(doc)
        gv_id = str(result.inserted_id)
        await AiJobService(self._db).mark_pending_review(
            job_id=f"diff:{diff_id}",
            type="diff",
            project_id=api_doc.get("project_id", "default"),
            source="diff_evaluator",
            target_ids=[diff_id],
            generation_ids=[gv_id],
        )
        return gv_id

    # ── 辅助方法 ──────────────────────────────────────────

    async def _call_llm(
        self, user_prompt: str, system_prompt: str | None = None,
        retries: int = 3,
        max_tokens: int | None = None,
    ) -> str:
        """
        调用 LLM，支持 system role + 指数退避重试。
        指数退避: 1s → 2s → 4s，最多 retries 次。
        max_tokens: 覆盖默认值，用于不同任务设置不同输出长度。
        """
        from config.settings import get_settings
        s = get_settings()
        timeout = s.openai_timeout

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        runtime = resolve_llm_config(getattr(self, "_runtime_llm_config", {}), "diff")
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
                if not resp.choices:
                    logger.warning("LLM returned empty choices (attempt {}/{})", attempt + 1, retries)
                    continue
                content = resp.choices[0].message.content or ""
                if resp.usage:
                    logger.debug("LLM usage: prompt={} completion={} total={}",
                                 resp.usage.prompt_tokens, resp.usage.completion_tokens, resp.usage.total_tokens)
                return content
            except (RateLimitError, APIError) as e:
                # OpenAI 可重试错误：指数退避 1s→2s→4s
                last_error = e
                if attempt < retries - 1:
                    wait = 2 ** attempt
                    logger.warning("Diff eval LLM call failed (attempt {}/{}), retrying in {}s: {}", attempt + 1, retries, wait, e)
                    await asyncio.sleep(wait)
                else:
                    logger.error("Diff eval LLM call exhausted {} retries: {}", retries, e)
                    raise
            except Exception:
                # 非 OpenAI 错误不重试，直接抛出
                raise
        if last_error:
            raise last_error
        return ""

    async def _requeue_or_dlq(self, diff_id: str, fail_count: int, project_id: str = "default", error: str = "") -> None:
        """重试或移入死信队列"""
        if fail_count < MAX_RETRY:
            payload = json.dumps({
                "diff_id": diff_id,
                "project_id": project_id,
                "fail_count": fail_count,
                "job_id": f"diff:{diff_id}",
                "status": "retry",
                "ts": _now().isoformat(),
            })
            await self._redis.rpush(DIFF_EVALUATE_QUEUE, payload)
            await AiJobService(self._db).mark_retry(
                job_id=f"diff:{diff_id}",
                type="diff",
                project_id=project_id,
                source="diff_evaluator",
                target_ids=[diff_id],
                queue_key=DIFF_EVALUATE_QUEUE,
                retry_count=fail_count,
                error=error,
                payload=json.loads(payload),
            )
            logger.warning("Requeued diff_id={} (fail_count={})", diff_id, fail_count)
        else:
            payload = json.dumps({
                "diff_id": diff_id,
                "project_id": project_id,
                "fail_count": fail_count,
                "job_id": f"diff:{diff_id}",
                "status": "dlq",
                "error": error or f"Exceeded {MAX_RETRY} retries",
                "ts": _now().isoformat(),
            })
            await self._redis.rpush(DIFF_EVALUATE_DLQ, payload)
            await AiJobService(self._db).mark_dlq(
                job_id=f"diff:{diff_id}",
                type="diff",
                project_id=project_id,
                source="diff_evaluator",
                target_ids=[diff_id],
                queue_key=DIFF_EVALUATE_QUEUE,
                retry_count=fail_count,
                error=error or f"Exceeded {MAX_RETRY} retries",
                payload=json.loads(payload),
            )
            # 标记差异记录为评估失败（保持 pending 状态供人工处理）
            await self._db["import_diffs"].update_one(
                {"id": diff_id},
                {"$set": {"ai_evaluation": {"error": f"Exceeded {MAX_RETRY} retries"}, "updated_at": _now()}},
            )
            logger.error("diff_id={} moved to DLQ after {} failures", diff_id, fail_count)

    async def _broadcast(self, key: str, data: dict) -> None:
        """安全广播，无 ws_manager 时静默跳过"""
        if key == "ai_analysis" or key.startswith("ai_analysis:"):
            data.setdefault("project_id", "default")
            data.setdefault("job_id", data.get("diff_id") or "")
            data.setdefault("type", "diff_eval")
            data.setdefault("status", "running")
            if key == "ai_analysis" and data.get("project_id"):
                key = f"ai_analysis:{data['project_id']}"
        if self._ws is not None:
            try:
                await self._ws.broadcast(key, data)
            except Exception as e:
                logger.warning("WS broadcast failed: {}", e)

    @staticmethod
    def _safe_parse_json(text: str) -> Any:
        """安全解析 JSON，处理 LLM 返回的非标准格式"""
        from ai_analyzer.utils import safe_parse_json
        return safe_parse_json(text)

    @staticmethod
    def _structured_error_detail(exc: Exception) -> str:
        """结构化输出失败时带上 raw preview，便于 AI Job/DLQ 排查。"""
        if isinstance(exc, StructuredOutputError):
            detail = str(exc)
            if exc.raw_output_preview:
                detail = f"{detail}; raw_output_preview={exc.raw_output_preview[:300]}"
            return detail[:800]
        return str(exc)[:800]


def _now() -> datetime:
    """返回东八区当前时间（不带时区信息）"""
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
