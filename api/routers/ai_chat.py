"""
AI 对话路由 —— P1-3 AI 助手对话面板

提供 Cmd+K 唤起的全局 AI 助手，支持多轮对话 + 上下文感知 + 基础工具调用。
区别于 analyzer 的"批处理任务"，这是"交互式对话"形态。

能力：
1. SSE 流式响应（逐 token 返回，前端打字机效果）
2. 对话上下文管理（Redis 存近 20 轮，按 user_id + session_id 隔离）
3. 上下文感知：前端注入当前页面上下文（如 api_id/scenario_id），AI 回答更精准
4. 工具调用：AI 可调用 search_api/get_execution 等只读工具增强回答
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase
from openai import AsyncOpenAI, APIError, RateLimitError

from config.database import get_db, get_redis
from config.settings import get_settings
from api.deps import _get_user_from_request, ensure_project_access, user_has_permission, visible_project_id
from api.state import _memory_service
from services.llm_config_service import resolve_llm_config
from models.generation_version import (
    GenerationSource,
    GenerationStatus,
    GenerationType,
    GenerationVersion,
)

router = APIRouter(tags=["AIChat"])

# 对话上下文 Redis 配置
CHAT_CTX_PREFIX = "chat_ctx"
CHAT_CTX_MAX_ROUNDS = 20  # 保留最近 20 轮
CHAT_CTX_TTL = 86400 * 7  # 7 天过期

_CHAT_SYSTEM = """\
你是 ApiPulse 平台的 AI 助手，帮助用户管理 API、测试场景、巡检监控、数据工厂等任务。

能力：
- 解答 API 测试相关问题（如何写断言、如何编排场景、如何造数据）
- 分析执行失败原因（结合诊断结果和 import-diffs）
- 提供接口质量改进建议
- 查询平台数据（接口列表、执行记录、巡检状态）

回答风格：
- 简洁直接，先给结论再补充细节
- 涉及代码/JSON 时用代码块
- 不确定时明确说明，不要编造
- 中文回答（技术术语可英文）"""

# 可用工具定义（只读查询，AI 可主动调用）。必须与 _TOOL_DEFINITIONS 和 /ai/chat/tools 保持一致。
_AVAILABLE_TOOLS = [
    "search_apis",
    "get_api",
    "get_execution",
    "list_scenarios",
    "get_monitor_stats",
    "get_pending_generations",
]


@router.post("/ai/chat")
async def chat(
    body: dict[str, Any] = Body(...),
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    AI 对话主端点 —— SSE 流式响应。
    请求体：
    - message: 用户消息
    - session_id: 会话 ID（首次为空，服务端生成返回）
    - context: 页面上下文（{type: "api"|"scenario"|"monitor", id: "..."}）
    响应：text/event-stream，每行 data: {json}
    """
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "message is required")

    session_id = body.get("session_id") or str(uuid.uuid4())
    context = body.get("context") or {}
    user = _get_user_from_request(request) or {}
    user_id = user.get("username", "anonymous")

    s = get_settings()
    if not s.openai_api_key:
        raise HTTPException(503, "AI service not configured (no API key)")

    redis = await get_redis()

    async def event_stream():
        """SSE 流式生成器。"""
        memory = _get_memory()
        try:
            # 1. 加载对话上下文（优先 MemoryService L4，降级到原始 Redis）
            ctx_key = f"{CHAT_CTX_PREFIX}:{user_id}:{session_id}"
            history = await memory.get_l4(user_id, session_id) if memory else []
            if not history:
                ctx_raw = await redis.get(ctx_key)
                history = json.loads(ctx_raw) if ctx_raw else []

            # 2. 构建消息列表（system + history + 页面上下文 + 当前消息）
            system_prompt = _build_system_with_context(_CHAT_SYSTEM, context, db)
            system_prompt += (
                "\n\n## 工具使用规则\n"
                "当用户询问接口、执行记录、场景或巡检数据时，优先调用可用只读工具查询真实平台数据；"
                "回答必须基于工具结果，数据不足时说明缺口，不要编造。"
            )
            messages = [{"role": "system", "content": system_prompt}]
            # 历史对话（仅 role/content，截断避免 token 爆炸；pre_reasoning 会进一步智能压缩）
            for h in history[-CHAT_CTX_MAX_ROUNDS*2:]:
                messages.append({"role": h["role"], "content": h["content"][:2000]})
            messages.append({"role": "user", "content": message})

            # 3. 发送 session_id（首个事件，前端据此建立会话）
            yield _sse({"type": "session", "session_id": session_id})

            # 4. 先让模型决定是否调用工具；该分支解决纯聊天无法读取项目数据的问题，
            #    只执行后端白名单只读工具，随后再进入原有 SSE token 流。
            db_config = await db["settings"].find_one({"key": "llm_config"}) or {}
            runtime = resolve_llm_config(db_config, "chat")
            client = runtime.build_client()
            messages, prefetch_events = await _apply_context_prefetch(
                db=db,
                user=user,
                context=context,
                messages=messages,
            )
            for event in prefetch_events:
                yield _sse(event)

            messages, tool_events = await _apply_tool_calls(
                client=client,
                db=db,
                user=user,
                context=context,
                messages=messages,
                model=runtime.model,
                timeout=s.openai_timeout,
            )
            for event in tool_events:
                yield _sse(event)

            # 4b. 工具结果智能压缩（替代原有 content[:12000] 硬截断，
            #     近期结果保留完整 ~100KB，旧结果自动截断至 ~3KB 摘要）
            if memory and memory._available:
                messages = await memory.compact_tool_results(messages)

            # 4c. ReMe pre-reasoning：压缩早期上下文 + 注入跨会话语义记忆
            #     解决原有 history[-N*2:] 粗暴丢弃早期上下文的问题
            if memory and memory._available:
                messages, _ = await memory.pre_reasoning(
                    messages,
                    system_prompt=system_prompt,
                )

            # 5. 流式调用 LLM，保持前端现有 ReadableStream + SSE 解析方式不变。
            full_response_parts = []
            stream = await client.chat.completions.create(
                model=runtime.model, messages=messages,
                temperature=runtime.temperature, max_tokens=runtime.max_tokens,
                timeout=s.openai_timeout, stream=True,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    full_response_parts.append(delta.content)
                    yield _sse({"type": "delta", "content": delta.content})

            # 6. 保存对话到上下文（优先 MemoryService L4）
            full_response = "".join(full_response_parts)
            generation_event = await _create_chat_generation_if_needed(
                db=db,
                user=user,
                context=context,
                message=message,
                assistant_response=full_response,
                model=runtime.model,
                project_id=visible_project_id(user, context.get("project_id")),
                session_id=session_id,
            )
            if generation_event:
                yield _sse(generation_event)
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": full_response})
            history = history[-CHAT_CTX_MAX_ROUNDS*2:]
            await _save_history(memory, redis, ctx_key, user_id, session_id, history)

            # 7. 快照会话到 L3（含回退摘要），使记忆模块有内容可查
            await _save_l3_snapshot(memory, user_id, session_id, visible_project_id(user, context.get("project_id")), history)

            # 8. 发送完成事件
            yield _sse({"type": "done", "session_id": session_id})

        except (RateLimitError, APIError) as e:
            logger.error("AI chat API error: {}", e)
            yield _sse({"type": "error", "message": f"AI 服务暂时不可用: {str(e)[:100]}"})
        except Exception as e:
            logger.error("AI chat error: {}", e)
            yield _sse({"type": "error", "message": f"对话出错: {str(e)[:100]}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Nginx 禁用缓冲，确保流式实时
        },
    )


@router.get("/ai/chat/history/{session_id}")
async def get_chat_history(
    session_id: str,
    request: Request = None,
):
    """获取指定会话的对话历史（优先 L4，降级 Redis）。"""
    user = _get_user_from_request(request) or {}
    user_id = user.get("username", "anonymous")
    memory = _get_memory()
    history = await memory.get_l4(user_id, session_id) if memory else []
    if not history:
        redis = await get_redis()
        ctx_key = f"{CHAT_CTX_PREFIX}:{user_id}:{session_id}"
        ctx_raw = await redis.get(ctx_key)
        history = json.loads(ctx_raw) if ctx_raw else []
    return {"history": history, "session_id": session_id}


@router.delete("/ai/chat/history/{session_id}")
async def clear_chat_history(
    session_id: str,
    request: Request = None,
):
    """清除指定会话的对话历史（先归档 L3 → 再清 L4 + Redis）。"""
    user = _get_user_from_request(request) or {}
    user_id = user.get("username", "anonymous")
    memory = _get_memory()
    if memory:
        # 先将会话归档到 L3，避免丢失对话记录
        await memory.end_session(user_id, session_id, "default")
        # 清 L4
        await memory.delete_l4(user_id, session_id)
    redis = await get_redis()
    ctx_key = f"{CHAT_CTX_PREFIX}:{user_id}:{session_id}"
    await redis.delete(ctx_key)
    return {"cleared": True, "session_id": session_id}


@router.get("/ai/chat/tools")
async def list_chat_tools():
    """返回可用工具列表（前端展示能力说明）。"""
    return {"tools": [{"name": t["function"]["name"], "description": t["function"]["description"]} for t in _TOOL_DEFINITIONS]}


_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_apis",
            "description": "按关键字、HTTP 方法或当前项目搜索 API 接口定义，只返回只读摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "接口名称、路径或摘要关键字"},
                    "method": {"type": "string", "description": "可选 HTTP 方法，如 GET/POST"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "返回数量，默认 5"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_api",
            "description": "按 api_id 查询接口详情、文档、断言数量和质量状态；当前 API 详情页可省略 api_id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "api_id": {"type": "string", "description": "API ID；当前 API 详情页可省略"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_execution",
            "description": "按 execution_id 查询单条执行记录详情，适合分析失败原因。",
            "parameters": {
                "type": "object",
                "properties": {
                    "execution_id": {"type": "string", "description": "执行记录 ID；当前执行详情页可省略"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_scenarios",
            "description": "列出当前项目下测试场景，可按 API、关键字、状态筛选。",
            "parameters": {
                "type": "object",
                "properties": {
                    "api_id": {"type": "string", "description": "可选，筛选包含该 API 的场景"},
                    "search": {"type": "string", "description": "可选，场景名称或描述关键字"},
                    "status": {"type": "string", "description": "可选，场景状态"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "返回数量，默认 5"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_monitor_stats",
            "description": "查询当前项目巡检监控概览、告警数量和最近巡检执行摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "monitor_id": {"type": "string", "description": "可选，指定监控 ID"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "最近巡检执行数量，默认 5"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pending_generations",
            "description": "查询当前项目待审核的 AI 生成内容，可按 API 或类型过滤。",
            "parameters": {
                "type": "object",
                "properties": {
                    "api_id": {"type": "string", "description": "可选，按 API ID 过滤；当前 API 详情页可省略"},
                    "type": {"type": "string", "description": "可选，doc/asserts/scenario/data_template"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "返回数量，默认 5"},
                },
                "additionalProperties": False,
            },
        },
    },
]


async def _apply_tool_calls(
    *,
    client: AsyncOpenAI,
    db: AsyncIOMotorDatabase,
    user: dict[str, Any],
    context: dict[str, Any],
    messages: list[dict[str, Any]],
    model: str,
    timeout: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """执行一轮 OpenAI tools 决策，并把工具结果回填到 messages。

    只做单轮工具调用是最小可用链路：足够让助手查询项目数据，同时避免递归调用导致延迟和成本不可控。
    """
    if not user_has_permission(user, "ai_chat:use"):
        return messages, []

    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        tools=_TOOL_DEFINITIONS,
        tool_choice="auto",
        temperature=0,
        max_tokens=256,
        timeout=timeout,
        stream=False,
    )
    if not resp.choices:
        return messages, []

    assistant_message = resp.choices[0].message
    tool_calls = assistant_message.tool_calls or []
    if not tool_calls:
        return messages, []

    assistant_payload = {
        "role": "assistant",
        "content": assistant_message.content or "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ],
    }
    messages.append(assistant_payload)

    events: list[dict[str, Any]] = []
    for tool_call in tool_calls[:4]:
        name = tool_call.function.name
        args = _parse_tool_arguments(tool_call.function.arguments)
        events.append({"type": "tool_call", "tool": name, "params": args})

        # 白名单分支：只允许当前声明的只读工具，避免模型构造未知函数造成越权行为。
        if name not in _AVAILABLE_TOOLS:
            result = {"error": f"unsupported tool: {name}"}
        else:
            result = await _execute_readonly_tool(db, user, context, name, args)

        safe_result = _sanitize_tool_result(result)
        events.append({"type": "tool_result", "tool": name, "result": safe_result})
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "name": name,
            "content": json.dumps(safe_result, ensure_ascii=False, default=str)[:12000],
        })

    return messages, events


async def _apply_context_prefetch(
    *,
    db: AsyncIOMotorDatabase,
    user: dict[str, Any],
    context: dict[str, Any],
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按页面上下文预取关键对象，解决模型未主动调用工具时回答失真的问题。"""
    ctx_type = context.get("type")
    if ctx_type not in {"api", "execution"}:
        return messages, []

    tool_name = "get_api" if ctx_type == "api" else "get_execution"
    args = {"api_id": context.get("id")} if ctx_type == "api" else {"execution_id": context.get("id")}
    result = await _execute_readonly_tool(db, user, context, tool_name, args)
    safe_result = _sanitize_tool_result(result)
    messages.append({
        "role": "system",
        "content": "当前页面真实数据："
                   + json.dumps({"tool": tool_name, "result": safe_result}, ensure_ascii=False, default=str)[:12000],
    })
    return messages, [
        {"type": "tool_call", "tool": tool_name, "params": args, "source": "context"},
        {"type": "tool_result", "tool": tool_name, "result": safe_result, "source": "context"},
    ]


async def _execute_readonly_tool(
    db: AsyncIOMotorDatabase,
    user: dict[str, Any],
    context: dict[str, Any],
    name: str,
    args: dict[str, Any],
) -> Any:
    """执行模型请求的只读工具；每个分支都限定 project_id，确保多租户隔离。"""
    project_id = visible_project_id(user, context.get("project_id"))
    ctx_type = context.get("type")
    ctx_id = context.get("id", "")

    if name == "search_apis":
        limit = _coerce_limit(args.get("limit"), default=5, maximum=10)
        query = str(args.get("query") or "").strip()[:120]
        method = str(args.get("method") or "").strip().upper()
        filt: dict[str, Any] = {"project_id": project_id}
        if method:
            filt["request.method"] = method
        if query:
            # 搜索分支：限定在名称/路径/摘要，避免把整个文档暴露给模型做模糊匹配。
            regex = {"$regex": query, "$options": "i"}
            filt["$or"] = [{"name": regex}, {"request.path": regex}, {"doc.summary": regex}]
        docs = await db["api_dsls"].find(filt, {"_id": 0}).sort("updated_at", -1).limit(limit).to_list(limit)
        items = [_compact_api(d) for d in docs]
        return {"total": len(docs), "items": items, "references": [_ref_api(i) for i in items]}

    if name == "get_api":
        api_id = str(args.get("api_id") or "").strip()
        if not api_id and ctx_type == "api":
            api_id = ctx_id
        if not api_id:
            return {"error": "api_id is required", "references": []}
        doc = await db["api_dsls"].find_one({"id": api_id}, {"_id": 0})
        if not doc:
            return {"error": "API not found", "api_id": api_id, "references": []}
        ensure_project_access(user, doc.get("project_id", project_id))
        item = _compact_api_detail(doc)
        return {"item": item, "references": [_ref_api(item)]}

    if name == "get_execution":
        execution_id = str(args.get("execution_id") or "").strip()
        if not execution_id and ctx_type == "execution":
            execution_id = ctx_id
        if not execution_id:
            return {"error": "execution_id is required", "references": []}
        doc = await db["executions"].find_one({"id": execution_id}, {"_id": 0})
        if not doc:
            return {"error": "Execution not found", "execution_id": execution_id, "references": []}
        ensure_project_access(user, doc.get("project_id", project_id))
        item = _compact_execution(doc)
        return {"item": item, "references": [_ref_execution(item)]}

    if name == "list_scenarios":
        limit = _coerce_limit(args.get("limit"), default=5, maximum=10)
        filt: dict[str, Any] = {"project_id": project_id}
        api_id = str(args.get("api_id") or "").strip()
        if not api_id and ctx_type == "api":
            api_id = ctx_id
        if api_id:
            filt["steps.api_id"] = api_id
        search = str(args.get("search") or "").strip()[:120]
        if search:
            regex = {"$regex": search, "$options": "i"}
            filt["$or"] = [{"name": regex}, {"description": regex}]
        status = str(args.get("status") or "").strip()
        if status:
            filt["status"] = status
        docs = await db["scenarios"].find(filt, {"_id": 0}).sort("updated_at", -1).limit(limit).to_list(limit)
        items = [_compact_scenario(d) for d in docs]
        return {"total": len(docs), "items": items, "references": [_ref_scenario(i) for i in items]}

    if name == "get_monitor_stats":
        limit = _coerce_limit(args.get("limit"), default=5, maximum=10)
        monitor_id = str(args.get("monitor_id") or "").strip()
        monitor_filter: dict[str, Any] = {"project_id": project_id}
        if monitor_id:
            monitor_filter["id"] = monitor_id
        monitors = await db["monitors"].find(monitor_filter, {"_id": 0}).sort("updated_at", -1).limit(20).to_list(20)
        alerts = await db["alert_records"].count_documents({"project_id": project_id})
        enabled_count = sum(1 for m in monitors if m.get("enabled"))
        exec_filter: dict[str, Any] = {"project_id": project_id, "trigger": "monitor"}
        if monitor_id:
            exec_filter["monitor_id"] = monitor_id
        recent_execs = await db["executions"].find(exec_filter, {"_id": 0}).sort("started_at", -1).limit(limit).to_list(limit)
        monitors_compact = [_compact_monitor(m) for m in monitors]
        execs_compact = [_compact_execution_summary(e) for e in recent_execs]
        return {
            "monitor_count": len(monitors),
            "enabled_count": enabled_count,
            "alert_count": alerts,
            "monitors": monitors_compact,
            "recent_executions": execs_compact,
            "references": [_ref_monitor(m) for m in monitors_compact] + [_ref_execution(e) for e in execs_compact],
        }

    if name == "get_pending_generations":
        limit = _coerce_limit(args.get("limit"), default=5, maximum=10)
        filt: dict[str, Any] = {"project_id": project_id, "status": GenerationStatus.PENDING_REVIEW.value}
        api_id = str(args.get("api_id") or "").strip()
        if not api_id and ctx_type == "api":
            api_id = ctx_id
        if api_id:
            filt["api_id"] = api_id
        gen_type = str(args.get("type") or "").strip()
        if gen_type:
            filt["type"] = gen_type
        docs = await db["generation_versions"].find(filt, {"content": 0, "prompt": 0}).sort("created_at", -1).limit(limit).to_list(limit)
        items = [_compact_generation(d) for d in docs]
        return {"total": len(items), "items": items, "references": [_ref_generation(i) for i in items]}

    return {"error": f"unsupported tool: {name}"}


def _parse_tool_arguments(raw: str | None) -> dict[str, Any]:
    """解析模型生成的工具参数；非 JSON 时降级为空对象，避免中断整条 SSE。"""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _coerce_limit(value: Any, default: int, maximum: int) -> int:
    """限制工具返回数量，控制 token 与响应延迟。"""
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(limit, maximum))


def _compact_api(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": doc.get("id"),
        "project_id": doc.get("project_id"),
        "name": doc.get("name"),
        "method": (doc.get("request") or {}).get("method"),
        "path": (doc.get("request") or {}).get("path"),
        "summary": (doc.get("doc") or {}).get("summary"),
        "analysis_status": doc.get("analysis_status"),
        "assert_count": len(doc.get("asserts") or []),
        "quality": doc.get("quality"),
    }


def _compact_api_detail(doc: dict[str, Any]) -> dict[str, Any]:
    item = _compact_api(doc)
    item.update({
        "description": (doc.get("doc") or {}).get("description"),
        "params_count": len((doc.get("doc") or {}).get("params") or []),
        "response_fields_count": len((doc.get("doc") or {}).get("response_fields") or []),
        "tags": (doc.get("doc") or {}).get("tags") or doc.get("tags") or [],
        "updated_at": doc.get("updated_at"),
    })
    return item


def _compact_execution(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": doc.get("id"),
        "project_id": doc.get("project_id"),
        "type": doc.get("type"),
        "passed": doc.get("passed"),
        "failure_reason": doc.get("failure_reason"),
        "api_id": doc.get("api_id"),
        "scenario_id": doc.get("scenario_id"),
        "monitor_id": doc.get("monitor_id"),
        "diagnosis": doc.get("diagnosis"),
        "steps": [
            {
                "step_id": s.get("step_id"),
                "name": s.get("name"),
                "passed": s.get("passed"),
                "error": s.get("error"),
                "status_code": (s.get("response_received") or {}).get("status_code"),
                "assert_results": [a for a in (s.get("assert_results") or []) if not a.get("passed")][:5],
            }
            for s in (doc.get("steps") or [])[:10]
        ],
    }


def _compact_execution_summary(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": doc.get("id"),
        "type": doc.get("type"),
        "api_id": doc.get("api_id"),
        "scenario_id": doc.get("scenario_id"),
        "monitor_id": doc.get("monitor_id"),
        "passed": doc.get("passed"),
        "duration_ms": doc.get("duration_ms"),
        "failure_reason": doc.get("failure_reason"),
        "started_at": doc.get("started_at"),
    }


def _compact_scenario(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": doc.get("id"),
        "project_id": doc.get("project_id"),
        "name": doc.get("name"),
        "description": doc.get("description"),
        "status": doc.get("status"),
        "steps_count": len(doc.get("steps") or []),
        "steps": [
            {"step_id": s.get("step_id"), "api_id": s.get("api_id"), "name": s.get("name")}
            for s in (doc.get("steps") or [])[:10]
        ],
    }


def _compact_monitor(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": doc.get("id"),
        "project_id": doc.get("project_id"),
        "name": doc.get("name"),
        "enabled": doc.get("enabled"),
        "target_type": doc.get("target_type"),
        "target_id": doc.get("target_id") or doc.get("api_id"),
        "interval": doc.get("interval"),
        "cron": doc.get("cron"),
        "updated_at": doc.get("updated_at"),
    }


def _compact_generation(doc: dict[str, Any]) -> dict[str, Any]:
    generation_id = str(doc.get("_id") or doc.get("id") or "")
    return {
        "id": generation_id,
        "api_id": doc.get("api_id"),
        "type": doc.get("type"),
        "status": doc.get("status"),
        "summary": doc.get("summary"),
        "source": doc.get("source"),
        "job_id": doc.get("job_id"),
        "created_at": doc.get("created_at"),
    }


def _ref_api(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "api",
        "id": item.get("id"),
        "title": item.get("name") or item.get("path") or item.get("id"),
        "path": item.get("path"),
        "route": f"/apis/{item.get('id')}",
    }


def _ref_execution(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "execution",
        "id": item.get("id"),
        "title": item.get("failure_reason") or item.get("id"),
        "path": item.get("api_id") or item.get("scenario_id"),
        "route": f"/executions/{item.get('id')}",
    }


def _ref_scenario(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "scenario",
        "id": item.get("id"),
        "title": item.get("name") or item.get("id"),
        "path": "",
        "route": f"/scenarios/{item.get('id')}",
    }


def _ref_monitor(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "monitor",
        "id": item.get("id"),
        "title": item.get("name") or item.get("id"),
        "path": item.get("target_id"),
        "route": "/monitor",
    }


def _ref_generation(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "generation",
        "id": item.get("id"),
        "title": item.get("summary") or item.get("type") or item.get("id"),
        "path": item.get("api_id"),
        "route": f"/generations?status=pending_review",
    }


def _sanitize_tool_result(obj: Any) -> Any:
    """工具结果脱敏，避免 token/password/header 泄露给模型或前端事件。"""
    sensitive = {"password", "passwd", "secret", "token", "authorization", "api_key", "apikey", "cookie"}
    if isinstance(obj, dict):
        return {
            k: "***" if k.lower() in sensitive else _sanitize_tool_result(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_sanitize_tool_result(i) for i in obj[:20]]
    return obj


async def _create_chat_generation_if_needed(
    *,
    db: AsyncIOMotorDatabase,
    user: dict[str, Any],
    context: dict[str, Any],
    message: str,
    assistant_response: str,
    model: str,
    project_id: str,
    session_id: str,
) -> dict[str, Any] | None:
    """聊天中的可写建议统一进入 GenerationVersion，禁止直接写 DSL。"""
    if context.get("type") != "api" or not context.get("id"):
        return None
    lower = f"{message}\n{assistant_response}".lower()
    intent_keywords = ("修改", "改写", "更新", "生成", "补充", "建议", "断言", "文档", "assert", "doc", "scenario", "模板")
    if not any(k in lower for k in intent_keywords):
        return None

    gen_type = _infer_generation_type(lower)
    api_id = str(context.get("id"))
    api_doc = await db["api_dsls"].find_one({"id": api_id}, {"_id": 0, "project_id": 1})
    if not api_doc:
        return None
    ensure_project_access(user, api_doc.get("project_id", project_id))
    content = _structured_chat_generation_content(gen_type, assistant_response, message)
    # 降级路径：无法解析为标准DSL时，内容仅含 chat_suggestion 纯文本，类型改为 CHAT_SUGGESTION
    if "chat_suggestion" in content:
        gen_type = GenerationType.CHAT_SUGGESTION
    gv = GenerationVersion(
        api_id=api_id,
        type=gen_type,
        status=GenerationStatus.PENDING_REVIEW,
        content=content,
        summary=f"AI 助手建议：{message[:80]}",
        model=model,
        prompt=message,
        api_ids=[api_id],
        project_id=api_doc.get("project_id", project_id),
        source=GenerationSource.AI_CHAT,
        job_id=f"chat:{session_id}",
    )
    doc = gv.model_dump()
    doc.pop("id", None)
    result = await db["generation_versions"].insert_one(doc)
    generation_id = str(result.inserted_id)
    return {
        "type": "generation_created",
        "generation_id": generation_id,
        "gen_type": gen_type.value,
        "api_id": api_id,
        "project_id": api_doc.get("project_id", project_id),
        "summary": gv.summary,
        "references": [_ref_generation({"id": generation_id, "summary": gv.summary, "api_id": api_id})],
    }


def _infer_generation_type(text: str) -> GenerationType:
    if "场景" in text or "scenario" in text:
        return GenerationType.SCENARIO
    if "模板" in text or "template" in text:
        return GenerationType.DATA_TEMPLATE
    if "断言" in text or "assert" in text:
        return GenerationType.ASSERTS
    if "文档" in text or "doc" in text:
        return GenerationType.DOC
    return GenerationType.CHAT_SUGGESTION  # 通用聊天建议，无法匹配为已知DSL类型


def _structured_chat_generation_content(gen_type: GenerationType, assistant_response: str, user_message: str) -> dict[str, Any]:
    """将聊天建议尽量转换为可直接审核应用的标准 content。

    该分支解决旧版 AI Chat 只写入 chat_suggestion，审核通过后无法按类型自动应用的问题。
    若回复中没有可解析 JSON，保留原建议内容作为降级路径，避免丢失用户可读建议。
    """
    parsed = _extract_first_json(assistant_response)
    if gen_type == GenerationType.DOC:
        if isinstance(parsed, dict):
            if any(k in parsed for k in ("summary", "description", "params", "response_fields", "tags")):
                return parsed
            if isinstance(parsed.get("doc"), dict):
                return parsed["doc"]
    if gen_type == GenerationType.ASSERTS:
        if isinstance(parsed, list):
            return {"asserts": [item for item in parsed if isinstance(item, dict)]}
        if isinstance(parsed, dict):
            if isinstance(parsed.get("asserts"), list):
                return {"asserts": [item for item in parsed["asserts"] if isinstance(item, dict)]}
    if gen_type == GenerationType.SCENARIO:
        if isinstance(parsed, list):
            return {"scenarios": [item for item in parsed if isinstance(item, dict)]}
        if isinstance(parsed, dict):
            if isinstance(parsed.get("scenarios"), list):
                return {"scenarios": [item for item in parsed["scenarios"] if isinstance(item, dict)]}
            if parsed.get("steps"):
                return parsed
    if gen_type == GenerationType.DATA_TEMPLATE:
        if isinstance(parsed, dict) and isinstance(parsed.get("fields"), list):
            return parsed
        if isinstance(parsed, dict) and isinstance(parsed.get("data_template"), dict):
            return parsed["data_template"]
    return {
        "chat_suggestion": assistant_response,
        "user_message": user_message,
        "instructions": "该内容由 AI Chat 生成，未能解析为标准 DSL；请在审核中心编辑后再接受。",
    }


def _extract_first_json(text: str) -> Any:
    """从聊天回复中提取首个 JSON 对象/数组，支持 markdown 代码块。"""
    if not text:
        return None
    import re
    candidates: list[str] = []
    for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE):
        candidates.append(match.group(1).strip())
    stripped = text.strip()
    candidates.append(stripped)
    starts = [idx for idx in (stripped.find("{"), stripped.find("[")) if idx >= 0]
    if starts:
        candidates.append(stripped[min(starts):])
    for candidate in candidates:
        for end in range(len(candidate), 0, -1):
            fragment = candidate[:end].strip()
            if not fragment or fragment[-1] not in "}]":
                continue
            try:
                return json.loads(fragment)
            except Exception:
                continue
    return None


def _build_system_with_context(base_system: str, context: dict, db: AsyncIOMotorDatabase) -> str:
    """将页面上下文注入 system prompt，使 AI 回答更精准。
    上下文示例：{type: "api", id: "api-123"} → AI 知道用户在看哪个接口。"""
    if not context or not context.get("type"):
        return base_system
    ctx_type = context.get("type", "")
    ctx_id = context.get("id", "")
    ctx_hint = f"\n\n## 当前页面上下文\n用户正在查看：{ctx_type}（ID: {ctx_id}）\n回答时优先关联此上下文。"
    return base_system + ctx_hint


def _sse(data: dict) -> str:
    """格式化为 SSE 事件行。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _get_memory():
    """获取 MemoryService 实例（可能为 None）。"""
    try:
        return _memory_service
    except Exception:
        return None


async def _save_history(memory, redis, ctx_key: str, user_id: str, session_id: str,
                        history: list[dict[str, Any]]) -> None:
    """保存对话历史：优先 MemoryService L4，同时写 Redis 作为降级备份。"""
    if memory:
        await memory.set_l4(user_id, session_id, history, ttl=CHAT_CTX_TTL)
    # 双写 Redis 作为降级路径（MemoryService 不可用时仍可读取历史）
    await redis.setex(ctx_key, CHAT_CTX_TTL, json.dumps(history, ensure_ascii=False))


async def _save_l3_snapshot(memory, user_id: str, session_id: str,
                            project_id: str, history: list[dict[str, Any]]) -> None:
    """将对话快照写入 L3 会话记忆，使记忆模块可检索本次对话记录。

    MemoryService 不可用或摘要生成失败时静默降级，不影响主流程。
    """
    if not memory or not history:
        return
    try:
        await memory.save_session_to_l3(user_id, session_id, project_id, history)
    except Exception as e:
        logger.warning("Failed to save L3 snapshot for session {}: {}", session_id, e)
