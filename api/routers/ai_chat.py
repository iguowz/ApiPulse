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
import time
import asyncio
import uuid
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase
from openai import AsyncOpenAI, APIError, RateLimitError

from config.database import get_db, get_redis
from config.settings import get_settings
from api.deps import _get_user_from_request, ensure_project_access, is_admin, require_auth, user_has_permission, visible_project_id
# 使用模块导入动态访问 _memory_service，避免 from import 在模块加载时绑定 None
# （_memory_service 在 startup() 中注入，from import 无法感知后续赋值）
import api.state as _state
from services.llm_config_service import resolve_llm_config
from models.generation_version import (
    GenerationSource,
    GenerationStatus,
    GenerationType,
    GenerationVersion,
)
from models.audit import AuditAction, AuditResource
from models.dsl import DataTemplate
from api.page_knowledge import PAGE_KNOWLEDGE
from services.stats_service import StatsService
from services.audit_service import AuditService
from services.api_service import ApiService
from services.scenario_service import ScenarioService
from services.mock_service import MockServiceManager
from services.diff_service import DiffService

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
- 执行写操作：创建/更新/删除场景、巡检任务、数据模板、Mock 服务、知识条目；更新 API 断言/文档；处理差异告警

写操作确认流程：
- 当你调用写工具时，系统会生成一个确认请求，用户需要在界面上点击确认后才会真正执行
- 调用写工具后，告知用户"已生成操作提案，请在弹窗中确认"
- 不要替用户做决定，始终等待用户确认

回答风格：
- 简洁直接，先给结论再补充细节
- 涉及代码/JSON 时用代码块
- 不确定时明确说明，不要编造
- 中文回答（技术术语可英文）"""

# 只读工具白名单，AI 可主动调用查询。
_READONLY_TOOLS = [
    "search_apis",
    "get_api",
    "get_execution",
    "list_scenarios",
    "get_monitor_stats",
    "get_pending_generations",
    "search_knowledge",
    "list_data_templates",
    "list_diff_alerts",
    "list_mock_services",
    "get_coverage",
    # Phase 1: 面板统计查询
    "get_overview_stats",
    "get_top_failing_apis",
    "get_api_health",
    "get_execution_trend",
]

# 写工具白名单，调用时走"提案→确认→执行"流程，不直接落库。
_WRITE_TOOL_NAMES = [
    # API 域
    "update_api_asserts",
    "update_api_doc",
    "delete_api",
    # 场景域
    "create_scenario",
    "update_scenario",
    "delete_scenario",
    # 巡检域
    "create_monitor",
    "update_monitor",
    "delete_monitor",
    # 数据工厂域
    "create_data_template",
    "update_data_template",
    "delete_data_template",
    # Mock 服务域
    "create_mock_service",
    "update_mock_service",
    "delete_mock_service",
    # 知识库域
    "upsert_knowledge",
    # 差异域
    "resolve_diff",
    # 批量域
    "batch_generate",
    # Phase 2: 执行操作（触发实际运行，不直接落库）
    "run_api",
    "run_scenario",
    "run_monitor",
    "generate_data",
    "test_mock",
    "toggle_monitor",
]

# 可用工具白名单（只读 + 写），用于 _apply_tool_calls 校验。
_AVAILABLE_TOOLS = _READONLY_TOOLS + _WRITE_TOOL_NAMES

# 待确认写操作暂存（内存），5 分钟 TTL 自动过期。
_pending_actions: dict[str, dict[str, Any]] = {}
_PENDING_TTL = 300


# ── AI 对话核心逻辑（供 SSE 端点和 Bot 共用）─────────────────


async def process_chat(
    db: AsyncIOMotorDatabase,
    user: dict[str, Any],
    user_id: str,
    message: str,
    session_id: str,
    context: dict[str, Any],
    history: list[dict[str, Any]],
) -> AsyncGenerator[dict[str, Any], None]:
    """AI 对话核心逻辑（不含 SSE 格式化和 Web 端特定逻辑）。

    被 SSE 端点（event_stream）和 Bot（BotMessageService）共用。
    Yields: session, tool_result, confirm_required, assistant_token, done (含 full_response)
    """
    s = get_settings()
    memory = _get_memory()

    # 2. 构建消息列表（system + history + 页面上下文 + 当前消息）
    system_prompt = await _build_enhanced_system_prompt(
        _CHAT_SYSTEM, context, db,
        memory=memory,
        project_id=visible_project_id(user, context.get("project_id")),
    )
    system_prompt += (
        "\n\n## 工具使用规则\n"
        "当用户询问接口、执行记录、场景或巡检数据时，优先调用可用只读工具查询真实平台数据；"
        "回答必须基于工具结果，数据不足时说明缺口，不要编造。"
    )
    messages = [{"role": "system", "content": system_prompt}]
    for h in history[-CHAT_CTX_MAX_ROUNDS*2:]:
        messages.append({"role": h["role"], "content": h["content"][:2000]})
    messages.append({"role": "user", "content": message})

    # 3. 发送 session_id（首个事件，前端据此建立会话）
    yield {"type": "session", "session_id": session_id}

    # 4. 先让模型决定是否调用工具；该分支解决纯聊天无法读取项目数据的问题，
    #    只执行后端白名单只读工具，随后再进入原有 token 流。
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
        yield event

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
        yield event

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
    full_response_parts: list[str] = []
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
            # Bot 消费 assistant_token 事件，SSE 端点映射为 delta
            yield {"type": "assistant_token", "content": delta.content}

    # 8. 发送完成事件（含 full_response，供 SSE 端点保存历史）
    yield {"type": "done", "session_id": session_id, "full_response": "".join(full_response_parts)}


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
        """SSE 流式生成器 —— 基于 process_chat() 核心逻辑。"""
        memory = _get_memory()
        full_response = ""
        try:
            # 1. 加载对话上下文（优先 MemoryService L4，降级到原始 Redis）
            ctx_key = f"{CHAT_CTX_PREFIX}:{user_id}:{session_id}"
            history = await memory.get_l4(user_id, session_id) if memory else []
            if not history:
                ctx_raw = await redis.get(ctx_key)
                history = json.loads(ctx_raw) if ctx_raw else []

            # 2-5. 调用 AI 对话核心逻辑（process_chat），映射事件到 SSE 格式
            async for event in process_chat(
                db=db, user=user, user_id=user_id, message=message,
                session_id=session_id, context=context, history=history,
            ):
                etype = event.get("type", "")
                if etype == "assistant_token":
                    # 映射 assistant_token → delta，保持 SSE 端点向后兼容
                    yield _sse({"type": "delta", "content": event["content"]})
                elif etype == "done":
                    full_response = event.get("full_response", "")
                    yield _sse({"type": "done", "session_id": session_id})
                else:
                    # session, tool_result, confirm_required 等事件原样包裹
                    yield _sse(event)

            # 6. 保存对话到上下文（优先 MemoryService L4）
            # AI 助手对话不进入审核中心，跳过 generation 创建
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": full_response})
            history = history[-CHAT_CTX_MAX_ROUNDS*2:]
            await _save_history(memory, redis, ctx_key, user_id, session_id, history)

            # 7. 快照会话到 L3（含回退摘要），使记忆模块有内容可查
            # 附加 tags：会话场景、用户信息，便于记忆列表筛选展示
            l3_tags = [f"user:{user_id}", f"page:{context.get('type', 'unknown')}"]
            await _save_l3_snapshot(memory, user_id, session_id, visible_project_id(user, context.get("project_id")), history, tags=l3_tags)

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
    user: dict = Depends(require_auth),
):
    """获取指定会话的对话历史（优先 L4，降级 Redis）。"""
    user_id = user["username"]
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
    user: dict = Depends(require_auth),
):
    """清除指定会话的对话历史（先归档 L3 → 再清 L4 + Redis）。"""
    user_id = user["username"]
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
    return {"tools": [{"name": t["function"]["name"], "description": t["function"]["description"]} for t in _ALL_TOOL_DEFINITIONS]}


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
    # Part B: 新增 5 个只读工具，覆盖 knowledge/factory/import_diffs/mock_services/coverage 页面
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "搜索知识库条目，可按类型、标签、关键字筛选，用于查找字段模式、断言模式、文档模式等约定知识。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键字，匹配标题和内容"},
                    "type": {"type": "string", "description": "可选，知识类型：field_pattern/assertion_pattern/doc_pattern/scenario_pattern"},
                    "tag": {"type": "string", "description": "可选，按标签筛选"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "返回数量，默认 5"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_data_templates",
            "description": "列出当前项目的数据工厂模板，可按名称搜索、按关联 API 筛选。",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "可选，模板名称关键字"},
                    "api_id": {"type": "string", "description": "可选，按关联 API ID 筛选"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "返回数量，默认 5"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_diff_alerts",
            "description": "列出当前项目的导入差异告警，按状态、严重程度筛选，用于发现接口变更。",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "可选，差异状态：pending/confirmed/auto_fixed/dismissed"},
                    "severity": {"type": "string", "description": "可选，严重程度：low/medium/high/critical"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "返回数量，默认 5"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_mock_services",
            "description": "列出当前项目的 Mock 服务，包含路由数量、启用状态、最近调用时间。",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "可选，服务名称或 slug 关键字"},
                    "enabled": {"type": "boolean", "description": "可选，仅返回启用/禁用的服务"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "返回数量，默认 5"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_coverage",
            "description": "查询当前项目的测试覆盖率矩阵，按模块×维度展示覆盖情况，发现覆盖盲区。",
            "parameters": {
                "type": "object",
                "properties": {
                    "dimension": {"type": "string", "description": "可选，按维度筛选：doc/asserts/scenario/monitor/execute"},
                },
                "additionalProperties": False,
            },
        },
    },
    # Phase 1: 面板统计查询（4 个只读工具，复用 StatsService）
    {
        "type": "function",
        "function": {
            "name": "get_overview_stats",
            "description": "查询当前项目平台概览：API 总数/场景数/覆盖率/告警数/质量分布/LLM配置状态。",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_failing_apis",
            "description": "查询指定时间窗口内失败次数最多的 API 排行，用于定位问题接口。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "返回数量，默认 10"},
                    "hours": {"type": "integer", "minimum": 1, "maximum": 720, "description": "统计时间窗口（小时），默认 24"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_api_health",
            "description": "查询 API 健康评分列表（0-100），综合成功率+响应时间+活跃度，返回等级（excellent/good/fair/poor）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "返回数量，默认 20"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_execution_trend",
            "description": "查询执行趋势：按时/天粒度统计通过率、失败数变化，用于发现波动。",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": ["24h", "7d", "30d"], "description": "统计周期，默认 24h"},
                    "granularity": {"type": "string", "enum": ["hour", "day"], "description": "时间粒度，hour 仅对 24h 有效，默认 hour"},
                },
                "additionalProperties": False,
            },
        },
    },
]

# ── 写工具定义（提案→确认→执行，不直接落库）───────────────
_WRITE_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    # ── API 域（3 个）──────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "update_api_asserts",
            "description": "替换指定 API 的断言列表。注意：这会完全覆盖已有断言，请传入完整的断言数组。",
            "parameters": {
                "type": "object",
                "properties": {
                    "api_id": {"type": "string", "description": "目标 API ID"},
                    "asserts": {
                        "type": "array",
                        "description": "完整的断言数组，每项含 type(enum: status_code/response_time/json_body/header/body_contains/schema)、path(JSONPath，status_code/response_time 可省略)、expected(期望值)、operator(eq/gt/lt/contains)、severity(high/medium/low)",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": ["status_code", "response_time", "json_body", "header", "body_contains", "schema"]},
                                "path": {"type": "string", "description": "JSONPath 路径，status_code/response_time 可省略"},
                                "expected": {"type": "string", "description": "期望值"},
                                "operator": {"type": "string", "enum": ["eq", "gt", "lt", "contains"], "description": "比较运算符"},
                                "severity": {"type": "string", "enum": ["high", "medium", "low"], "description": "严重级别"},
                            },
                            "required": ["type", "expected"],
                        },
                    },
                },
                "required": ["api_id", "asserts"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_api_doc",
            "description": "更新指定 API 的文档信息：摘要、描述、参数、响应字段等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "api_id": {"type": "string", "description": "目标 API ID"},
                    "doc": {
                        "type": "object",
                        "description": "文档内容，可包含 summary/description/params/response_fields/tags",
                        "properties": {
                            "summary": {"type": "string", "description": "接口摘要"},
                            "description": {"type": "string", "description": "接口详细描述"},
                            "params": {"type": "array", "description": "请求参数列表"},
                            "response_fields": {"type": "array", "description": "响应字段列表"},
                            "tags": {"type": "array", "items": {"type": "string"}, "description": "标签"},
                        },
                    },
                },
                "required": ["api_id", "doc"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_api",
            "description": "删除指定 API 及其关联数据。此操作不可逆，请谨慎使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "api_id": {"type": "string", "description": "要删除的 API ID"},
                },
                "required": ["api_id"],
                "additionalProperties": False,
            },
        },
    },
    # ── 场景域（3 个）─────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "create_scenario",
            "description": "创建新的测试场景，包含步骤编排。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "场景名称"},
                    "description": {"type": "string", "description": "场景描述"},
                    "steps": {
                        "type": "array",
                        "description": "场景步骤列表，每项含 type(api/delay/condition)、api_id、name、config(请求覆盖配置)",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": ["api", "delay", "condition"], "description": "步骤类型"},
                                "api_id": {"type": "string", "description": "关联 API ID（type=api 时必填）"},
                                "name": {"type": "string", "description": "步骤名称"},
                                "config": {"type": "object", "description": "请求覆盖配置（method/path/headers/body/params）"},
                            },
                            "required": ["type", "name"],
                        },
                    },
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "标签列表"},
                },
                "required": ["name", "steps"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_scenario",
            "description": "更新指定场景的名称、描述、步骤或标签。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scenario_id": {"type": "string", "description": "场景 ID"},
                    "updates": {
                        "type": "object",
                        "description": "要更新的字段：name/description/steps/tags/status",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "steps": {"type": "array"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                            "status": {"type": "string", "enum": ["active", "draft", "archived"]},
                        },
                    },
                },
                "required": ["scenario_id", "updates"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_scenario",
            "description": "删除指定测试场景。此操作不可逆。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scenario_id": {"type": "string", "description": "要删除的场景 ID"},
                },
                "required": ["scenario_id"],
                "additionalProperties": False,
            },
        },
    },
    # ── 巡检域（3 个）─────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "create_monitor",
            "description": "创建 API 巡检监控任务，按 cron 表达式定时执行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "巡检任务名称"},
                    "target_type": {"type": "string", "enum": ["api", "scenario"], "description": "巡检目标类型"},
                    "target_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "目标 API ID 或场景 ID 列表",
                    },
                    "cron": {"type": "string", "description": "Cron 表达式，如 */5 * * * * 表示每5分钟"},
                    "retry": {"type": "integer", "minimum": 0, "maximum": 3, "description": "失败重试次数，默认 0"},
                    "enabled": {"type": "boolean", "description": "是否启用，默认 true"},
                },
                "required": ["name", "target_type", "target_ids", "cron"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_monitor",
            "description": "更新指定巡检任务的名称、cron、启用状态等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "monitor_id": {"type": "string", "description": "巡检任务 ID"},
                    "updates": {
                        "type": "object",
                        "description": "要更新的字段：name/cron/enabled/retry/target_ids",
                        "properties": {
                            "name": {"type": "string"},
                            "cron": {"type": "string"},
                            "enabled": {"type": "boolean"},
                            "retry": {"type": "integer", "minimum": 0, "maximum": 3},
                            "target_ids": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "required": ["monitor_id", "updates"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_monitor",
            "description": "删除指定巡检任务，同时移除调度器中的定时任务。",
            "parameters": {
                "type": "object",
                "properties": {
                    "monitor_id": {"type": "string", "description": "要删除的巡检任务 ID"},
                },
                "required": ["monitor_id"],
                "additionalProperties": False,
            },
        },
    },
    # ── 数据工厂域（3 个）─────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "create_data_template",
            "description": "创建数据工厂模板，用于自动生成测试数据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "模板名称"},
                    "fields": {
                        "type": "array",
                        "description": "字段定义列表，每项含 name/type(faker方法名)/generator/params",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "字段名"},
                                "type": {"type": "string", "description": "Faker 方法名，如 name/email/random_int"},
                                "generator": {"type": "string", "description": "生成器类型"},
                                "params": {"type": "object", "description": "Faker 方法参数"},
                            },
                            "required": ["name", "type"],
                        },
                    },
                    "api_id": {"type": "string", "description": "关联 API ID（可选）"},
                },
                "required": ["name", "fields"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_data_template",
            "description": "更新数据工厂模板的名称或字段定义。",
            "parameters": {
                "type": "object",
                "properties": {
                    "template_id": {"type": "string", "description": "模板 ID"},
                    "updates": {
                        "type": "object",
                        "description": "要更新的字段：name/fields/api_id",
                        "properties": {
                            "name": {"type": "string"},
                            "fields": {"type": "array"},
                            "api_id": {"type": "string"},
                        },
                    },
                },
                "required": ["template_id", "updates"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_data_template",
            "description": "删除指定数据工厂模板。",
            "parameters": {
                "type": "object",
                "properties": {
                    "template_id": {"type": "string", "description": "要删除的模板 ID"},
                },
                "required": ["template_id"],
                "additionalProperties": False,
            },
        },
    },
    # ── Mock 服务域（3 个）────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "create_mock_service",
            "description": "创建 Mock 服务，定义路由和响应规则。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Mock 服务名称"},
                    "slug": {"type": "string", "description": "URL 路径前缀（字母数字+短横线），如 user-mock"},
                    "routes": {
                        "type": "array",
                        "description": "路由规则列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "路由路径，如 /api/users"},
                                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"], "description": "HTTP 方法"},
                                "response": {"type": "object", "description": "Mock 响应体"},
                                "status_code": {"type": "integer", "description": "响应状态码，默认 200"},
                            },
                            "required": ["path", "method", "response"],
                        },
                    },
                },
                "required": ["name", "slug", "routes"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_mock_service",
            "description": "更新 Mock 服务的名称、路由或启用状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_id": {"type": "string", "description": "Mock 服务 ID"},
                    "updates": {
                        "type": "object",
                        "description": "要更新的字段：name/routes/enabled/slug",
                        "properties": {
                            "name": {"type": "string"},
                            "slug": {"type": "string"},
                            "routes": {"type": "array"},
                            "enabled": {"type": "boolean"},
                        },
                    },
                },
                "required": ["service_id", "updates"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_mock_service",
            "description": "删除指定 Mock 服务。",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_id": {"type": "string", "description": "要删除的 Mock 服务 ID"},
                },
                "required": ["service_id"],
                "additionalProperties": False,
            },
        },
    },
    # ── 知识库域（1 个）────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "upsert_knowledge",
            "description": "创建或更新知识库条目。存在同类型同标题的条目时自动合并（提升置信度、合并标签）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["field_pattern", "assertion_pattern", "doc_pattern", "scenario_pattern"], "description": "知识类型"},
                    "key": {"type": "string", "description": "条目标题/唯一标识"},
                    "content": {"type": "string", "description": "知识内容（Markdown 或纯文本）"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "标签列表"},
                    "api_id": {"type": "string", "description": "关联 API ID（可选）"},
                },
                "required": ["type", "key", "content"],
                "additionalProperties": False,
            },
        },
    },
    # ── 差异域（1 个）─────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "resolve_diff",
            "description": "处理导入差异告警：确认（接受变更）或 dismiss（忽略差异）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "diff_id": {"type": "string", "description": "差异记录 ID"},
                    "action": {"type": "string", "enum": ["confirm", "dismiss"], "description": "confirm=确认变更，dismiss=忽略差异"},
                },
                "required": ["diff_id", "action"],
                "additionalProperties": False,
            },
        },
    },
    # ── 批量域（1 个）─────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "batch_generate",
            "description": "对一批 API 批量触发 AI 分析/生成（文档、断言、场景等）。实际生成结果会进入审核中心。",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["doc", "asserts", "scenario", "data_template"], "description": "生成类型"},
                    "api_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 20,
                        "description": "目标 API ID 列表（1~20 个）",
                    },
                    "options": {"type": "object", "description": "额外选项（取决于生成类型）"},
                },
                "required": ["type", "api_ids"],
                "additionalProperties": False,
            },
        },
    },
    # Phase 2: 执行操作（6 个写工具，复用 Domain Service）
    {
        "type": "function",
        "function": {
            "name": "run_api",
            "description": "执行指定 API 接口测试，返回执行结果摘要和 execution_id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "api_id": {"type": "string", "description": "要执行的 API ID"},
                    "env_id": {"type": "string", "description": "可选，指定环境 ID"},
                },
                "required": ["api_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_scenario",
            "description": "执行指定测试场景（包含多个步骤），返回执行结果摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scenario_id": {"type": "string", "description": "要执行的场景 ID"},
                    "env_id": {"type": "string", "description": "可选，指定环境 ID"},
                },
                "required": ["scenario_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_monitor",
            "description": "手动触发指定巡检任务立即执行一次（不影响定时调度）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "monitor_id": {"type": "string", "description": "巡检任务 ID"},
                },
                "required": ["monitor_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_data",
            "description": "根据数据工厂模板生成测试数据并缓存，返回生成的数据或缓存 key。",
            "parameters": {
                "type": "object",
                "properties": {
                    "template_id": {"type": "string", "description": "数据模板 ID（或关联的 api_id）"},
                    "count": {"type": "integer", "minimum": 1, "maximum": 100, "description": "生成数量，默认 1"},
                    "context": {"type": "object", "description": "可选的上下文变量（如 user_id、timestamp）"},
                },
                "required": ["template_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "test_mock",
            "description": "测试 Mock 服务的路由匹配和响应，使用模拟请求调用 evaluate，返回匹配结果和响应摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_id": {"type": "string", "description": "Mock 服务 ID"},
                    "method": {"type": "string", "description": "请求方法，默认 GET"},
                    "path": {"type": "string", "description": "请求路径，默认 /"},
                },
                "required": ["service_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_monitor",
            "description": "启用或禁用指定巡检任务，同步更新 scheduler 定时调度。",
            "parameters": {
                "type": "object",
                "properties": {
                    "monitor_id": {"type": "string", "description": "巡检任务 ID"},
                    "enabled": {"type": "boolean", "description": "true=启用，false=禁用"},
                },
                "required": ["monitor_id", "enabled"],
                "additionalProperties": False,
            },
        },
    },
]

# 合并全部工具定义（只读 + 写），传给 LLM
_ALL_TOOL_DEFINITIONS = _TOOL_DEFINITIONS + _WRITE_TOOL_DEFINITIONS


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

    只读工具直接执行；写工具走"提案→确认→执行"流程，发出 confirm_required 事件。
    只做单轮工具调用是最小可用链路：足够让助手查询/提案操作，同时避免递归调用导致延迟和成本不可控。
    """
    if not user_has_permission(user, "ai_chat:use"):
        return messages, []

    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        tools=_ALL_TOOL_DEFINITIONS,
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

        # 白名单分支：只允许已声明的工具，避免模型构造未知函数造成越权行为。
        if name not in _AVAILABLE_TOOLS:
            result = {"error": f"unsupported tool: {name}"}
        elif name in _WRITE_TOOL_NAMES:
            # 写工具：不立即执行，生成待确认提案 → 前端弹窗 → 用户确认后 POST /ai/chat/confirm
            result = _stage_pending_action(db=db, user=user, context=context, name=name, args=args, events=events)
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


def _stage_pending_action(
    *,
    db: AsyncIOMotorDatabase,
    user: dict[str, Any],
    context: dict[str, Any],
    name: str,
    args: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """将写操作暂存到待确认队列，发出 confirm_required SSE 事件。

    返回 pending 状态给 LLM，使其告知用户等待确认。
    """
    # 检查写权限
    if not user_has_permission(user, "ai_chat:write"):
        return {"error": "无写操作权限，需要 ai_chat:write 权限"}

    project_id = visible_project_id(user, context.get("project_id"))
    action_id = uuid.uuid4().hex
    _pending_actions[action_id] = {
        "tool": name,
        "args": args,
        "user": user,
        "context": context,
        "project_id": project_id,
        "created_at": time.time(),
        "db": db,
    }

    # 构建用户友好的摘要
    summary_bundle = _build_write_summary(db, user, context, name, args)

    events.append({
        "type": "confirm_required",
        "action_id": action_id,
        "tool": name,
        "summary": summary_bundle.get("summary", f"{name}: {json.dumps(args, ensure_ascii=False, default=str)[:200]}"),
        "target_name": summary_bundle.get("target_name", ""),
        "target_type": summary_bundle.get("target_type", ""),
        "params_preview": summary_bundle.get("params_preview", args),
    })

    # 返回给 LLM 的结果：明确告知等待确认
    return {
        "action_id": action_id,
        "status": "pending_confirmation",
        "message": f"操作提案已生成，等待用户确认后执行。action_id={action_id}",
    }


def _build_write_summary(
    db: AsyncIOMotorDatabase,
    user: dict[str, Any],
    context: dict[str, Any],
    name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """为确认弹窗构建用户友好的操作摘要。

    返回：
    - summary: 一句话操作描述
    - target_name: 被操作对象名称
    - target_type: 被操作对象类型
    - params_preview: 关键参数预览
    """
    project_id = visible_project_id(user, context.get("project_id"))
    action_verbs = {
        "create": "创建", "update": "更新", "delete": "删除",
        "upsert": "创建/更新", "resolve": "处理", "batch": "批量",
    }

    if name == "update_api_asserts":
        api_id = str(args.get("api_id", ""))
        asserts = args.get("asserts") or []
        return {
            "summary": f"更新 API 断言：{api_id}（{len(asserts)} 条断言）",
            "target_name": api_id,
            "target_type": "api",
            "params_preview": {"api_id": api_id, "assert_count": len(asserts)},
        }

    if name == "update_api_doc":
        api_id = str(args.get("api_id", ""))
        doc = args.get("doc") or {}
        return {
            "summary": f"更新 API 文档：{api_id}",
            "target_name": api_id,
            "target_type": "api",
            "params_preview": {"api_id": api_id, "doc_keys": list(doc.keys()) if isinstance(doc, dict) else []},
        }

    if name == "delete_api":
        api_id = str(args.get("api_id", ""))
        return {
            "summary": f"删除 API：{api_id}（不可逆操作）",
            "target_name": api_id,
            "target_type": "api",
            "params_preview": {"api_id": api_id},
        }

    if name == "create_scenario":
        scenario_name = str(args.get("name", ""))
        steps = args.get("steps") or []
        return {
            "summary": f"创建测试场景：{scenario_name}（{len(steps)} 个步骤）",
            "target_name": scenario_name,
            "target_type": "scenario",
            "params_preview": {"name": scenario_name, "steps_count": len(steps)},
        }

    if name == "update_scenario":
        scenario_id = str(args.get("scenario_id", ""))
        updates = args.get("updates") or {}
        return {
            "summary": f"更新测试场景：{scenario_id}",
            "target_name": scenario_id,
            "target_type": "scenario",
            "params_preview": {"scenario_id": scenario_id, "update_keys": list(updates.keys()) if isinstance(updates, dict) else []},
        }

    if name == "delete_scenario":
        scenario_id = str(args.get("scenario_id", ""))
        return {
            "summary": f"删除测试场景：{scenario_id}（不可逆操作）",
            "target_name": scenario_id,
            "target_type": "scenario",
            "params_preview": {"scenario_id": scenario_id},
        }

    if name == "create_monitor":
        monitor_name = str(args.get("name", ""))
        target_ids = args.get("target_ids") or []
        cron = str(args.get("cron", ""))
        return {
            "summary": f"创建巡检任务：{monitor_name}（{len(target_ids)} 个目标，cron: {cron}）",
            "target_name": monitor_name,
            "target_type": "monitor",
            "params_preview": {"name": monitor_name, "target_count": len(target_ids), "cron": cron},
        }

    if name == "update_monitor":
        monitor_id = str(args.get("monitor_id", ""))
        updates = args.get("updates") or {}
        return {
            "summary": f"更新巡检任务：{monitor_id}",
            "target_name": monitor_id,
            "target_type": "monitor",
            "params_preview": {"monitor_id": monitor_id, "update_keys": list(updates.keys()) if isinstance(updates, dict) else []},
        }

    if name == "delete_monitor":
        monitor_id = str(args.get("monitor_id", ""))
        return {
            "summary": f"删除巡检任务：{monitor_id}（不可逆操作）",
            "target_name": monitor_id,
            "target_type": "monitor",
            "params_preview": {"monitor_id": monitor_id},
        }

    if name == "create_data_template":
        tmpl_name = str(args.get("name", ""))
        fields = args.get("fields") or []
        return {
            "summary": f"创建数据模板：{tmpl_name}（{len(fields)} 个字段）",
            "target_name": tmpl_name,
            "target_type": "data_template",
            "params_preview": {"name": tmpl_name, "field_count": len(fields)},
        }

    if name == "update_data_template":
        template_id = str(args.get("template_id", ""))
        updates = args.get("updates") or {}
        return {
            "summary": f"更新数据模板：{template_id}",
            "target_name": template_id,
            "target_type": "data_template",
            "params_preview": {"template_id": template_id, "update_keys": list(updates.keys()) if isinstance(updates, dict) else []},
        }

    if name == "delete_data_template":
        template_id = str(args.get("template_id", ""))
        return {
            "summary": f"删除数据模板：{template_id}（不可逆操作）",
            "target_name": template_id,
            "target_type": "data_template",
            "params_preview": {"template_id": template_id},
        }

    if name == "create_mock_service":
        svc_name = str(args.get("name", ""))
        routes = args.get("routes") or []
        return {
            "summary": f"创建 Mock 服务：{svc_name}（{len(routes)} 条路由）",
            "target_name": svc_name,
            "target_type": "mock_service",
            "params_preview": {"name": svc_name, "route_count": len(routes)},
        }

    if name == "update_mock_service":
        service_id = str(args.get("service_id", ""))
        updates = args.get("updates") or {}
        return {
            "summary": f"更新 Mock 服务：{service_id}",
            "target_name": service_id,
            "target_type": "mock_service",
            "params_preview": {"service_id": service_id, "update_keys": list(updates.keys()) if isinstance(updates, dict) else []},
        }

    if name == "delete_mock_service":
        service_id = str(args.get("service_id", ""))
        return {
            "summary": f"删除 Mock 服务：{service_id}（不可逆操作）",
            "target_name": service_id,
            "target_type": "mock_service",
            "params_preview": {"service_id": service_id},
        }

    if name == "upsert_knowledge":
        ktype = str(args.get("type", ""))
        key = str(args.get("key", ""))
        return {
            "summary": f"创建/更新知识条目：[{ktype}] {key}",
            "target_name": key,
            "target_type": "knowledge",
            "params_preview": {"type": ktype, "key": key},
        }

    if name == "resolve_diff":
        diff_id = str(args.get("diff_id", ""))
        action = str(args.get("action", ""))
        return {
            "summary": f"处理差异告警：{diff_id}（{action}）",
            "target_name": diff_id,
            "target_type": "diff",
            "params_preview": {"diff_id": diff_id, "action": action},
        }

    if name == "batch_generate":
        gen_type = str(args.get("type", ""))
        api_ids = args.get("api_ids") or []
        return {
            "summary": f"批量 AI 生成：{gen_type}（{len(api_ids)} 个 API）",
            "target_name": f"batch_{gen_type}",
            "target_type": "batch",
            "params_preview": {"type": gen_type, "api_count": len(api_ids)},
        }

    # Phase 2: 执行操作摘要
    if name == "run_api":
        api_id = str(args.get("api_id", ""))
        env_id = str(args.get("env_id") or "")
        summary = f"执行接口测试：{api_id}"
        if env_id:
            summary += f"（环境：{env_id}）"
        return {"summary": summary, "target_name": api_id, "target_type": "api", "params_preview": {"api_id": api_id, "env_id": env_id}}

    if name == "run_scenario":
        scenario_id = str(args.get("scenario_id", ""))
        env_id = str(args.get("env_id") or "")
        summary = f"执行测试场景：{scenario_id}"
        if env_id:
            summary += f"（环境：{env_id}）"
        return {"summary": summary, "target_name": scenario_id, "target_type": "scenario", "params_preview": {"scenario_id": scenario_id, "env_id": env_id}}

    if name == "run_monitor":
        monitor_id = str(args.get("monitor_id", ""))
        return {"summary": f"手动触发巡检：{monitor_id}", "target_name": monitor_id, "target_type": "monitor", "params_preview": {"monitor_id": monitor_id}}

    if name == "generate_data":
        template_id = str(args.get("template_id", ""))
        count = int(args.get("count") or 1)
        return {"summary": f"生成测试数据：模板 {template_id}（{count} 条）", "target_name": template_id, "target_type": "data_template", "params_preview": {"template_id": template_id, "count": count}}

    if name == "test_mock":
        service_id = str(args.get("service_id", ""))
        method = str(args.get("method") or "GET").upper()
        path = str(args.get("path") or "/")
        return {"summary": f"测试 Mock 服务：{service_id}（{method} {path}）", "target_name": service_id, "target_type": "mock_service", "params_preview": {"service_id": service_id, "method": method, "path": path}}

    if name == "toggle_monitor":
        monitor_id = str(args.get("monitor_id", ""))
        enabled = bool(args.get("enabled", True))
        action = "启用" if enabled else "禁用"
        return {"summary": f"{action}巡检任务：{monitor_id}", "target_name": monitor_id, "target_type": "monitor", "params_preview": {"monitor_id": monitor_id, "enabled": enabled}}

    return {
        "summary": f"执行操作：{name}",
        "target_name": "",
        "target_type": "unknown",
        "params_preview": args,
    }


async def _execute_write_tool(
    db: AsyncIOMotorDatabase,
    user: dict[str, Any],
    context: dict[str, Any],
    name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """执行写工具操作。仅在用户确认后由 /ai/chat/confirm 端点调用。

    每个分支复用对应 Domain Service 或直接 MongoDB 操作，与已有 router 保持一致。
    """
    project_id = visible_project_id(user, context.get("project_id"))
    audit_svc = AuditService(db)

    # ── API 域 ──────────────────────────────────────────
    if name == "update_api_asserts":
        api_id = str(args.get("api_id", ""))
        asserts = args.get("asserts") or []
        api_svc = ApiService(db)
        ok = await api_svc.replace_asserts(api_id, asserts)
        if not ok:
            return {"error": "API not found or update failed", "api_id": api_id}
        await audit_svc.log_action(
            user=user, action=AuditAction.UPDATE, resource=AuditResource.API,
            resource_id=api_id, resource_name=api_id, detail=f"AI 助手更新 {len(asserts)} 条断言",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已更新 {api_id} 的 {len(asserts)} 条断言"}

    if name == "update_api_doc":
        api_id = str(args.get("api_id", ""))
        doc_data = args.get("doc") or {}
        api_svc = ApiService(db)
        ok = await api_svc.update_api(api_id, doc_data)
        if not ok:
            return {"error": "API not found or update failed", "api_id": api_id}
        await audit_svc.log_action(
            user=user, action=AuditAction.UPDATE, resource=AuditResource.API,
            resource_id=api_id, resource_name=api_id, detail="AI 助手更新 API 文档",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已更新 {api_id} 的文档"}

    if name == "delete_api":
        api_id = str(args.get("api_id", ""))
        api_svc = ApiService(db)
        ok = await api_svc.delete_api(api_id)
        if not ok:
            return {"error": "API not found or delete failed", "api_id": api_id}
        await audit_svc.log_action(
            user=user, action=AuditAction.DELETE, resource=AuditResource.API,
            resource_id=api_id, resource_name=api_id, detail="AI 助手删除 API",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已删除 API：{api_id}"}

    # ── 场景域 ──────────────────────────────────────────
    if name == "create_scenario":
        svc = ScenarioService(db)
        from models.scenario_dsl import ScenarioDSL
        scenario = ScenarioDSL(
            id=str(uuid.uuid4()),
            project_id=project_id,
            name=str(args.get("name", "")),
            description=str(args.get("description", "")),
            steps=args.get("steps") or [],
            tags=args.get("tags") or [],
        )
        result = await svc.create_scenario(scenario, project_id)
        await audit_svc.log_action(
            user=user, action=AuditAction.CREATE, resource=AuditResource.SCENARIO,
            resource_id=result.id, resource_name=result.name, detail="AI 助手创建场景",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已创建场景：{result.name}", "scenario_id": result.id}

    if name == "update_scenario":
        scenario_id = str(args.get("scenario_id", ""))
        updates = args.get("updates") or {}
        svc = ScenarioService(db)
        ok = await svc.update_scenario(scenario_id, updates, project_id)
        if not ok:
            return {"error": "Scenario not found or update failed", "scenario_id": scenario_id}
        await audit_svc.log_action(
            user=user, action=AuditAction.UPDATE, resource=AuditResource.SCENARIO,
            resource_id=scenario_id, resource_name=scenario_id, detail="AI 助手更新场景",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已更新场景：{scenario_id}"}

    if name == "delete_scenario":
        scenario_id = str(args.get("scenario_id", ""))
        svc = ScenarioService(db)
        ok = await svc.delete_scenario(scenario_id, project_id)
        if not ok:
            return {"error": "Scenario not found or delete failed", "scenario_id": scenario_id}
        await audit_svc.log_action(
            user=user, action=AuditAction.DELETE, resource=AuditResource.SCENARIO,
            resource_id=scenario_id, resource_name=scenario_id, detail="AI 助手删除场景",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已删除场景：{scenario_id}"}

    # ── 巡检域 ──────────────────────────────────────────
    if name == "create_monitor":
        from models.monitor_dsl import MonitorDSL
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        monitor = MonitorDSL(
            id=str(uuid.uuid4()),
            project_id=project_id,
            name=str(args.get("name", "")),
            target_type=str(args.get("target_type", "api")),
            target_ids=args.get("target_ids") or [],
            cron=str(args.get("cron", "*/5 * * * *")),
            retry=min(max(int(args.get("retry", 0)), 0), 3),
            enabled=args.get("enabled", True),
            created_at=now,
            updated_at=now,
        )
        # 使用 _state._monitor_service 注册（复用 scheduler）
        if _state._monitor_service:
            monitor_id = await _state._monitor_service.register_monitor(monitor)
        else:
            await db["monitors"].insert_one(monitor.model_dump())
            monitor_id = monitor.id
        await audit_svc.log_action(
            user=user, action=AuditAction.CREATE, resource=AuditResource.MONITOR,
            resource_id=monitor_id, resource_name=monitor.name, detail="AI 助手创建巡检任务",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已创建巡检任务：{monitor.name}", "monitor_id": monitor_id}

    if name == "update_monitor":
        monitor_id = str(args.get("monitor_id", ""))
        updates = args.get("updates") or {}
        old = await db["monitors"].find_one({"id": monitor_id, "project_id": project_id})
        if not old:
            return {"error": "Monitor not found", "monitor_id": monitor_id}
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        merged = {**old, **updates, "updated_at": now}
        merged.pop("_id", None)
        r = await db["monitors"].update_one({"id": monitor_id, "project_id": project_id}, {"$set": merged})
        if not r.matched_count:
            return {"error": "Monitor update failed", "monitor_id": monitor_id}
        # 同步调度器
        if _state._monitor_service:
            from models.monitor_dsl import MonitorDSL
            try:
                _state._monitor_service._scheduler.remove_job(f"monitor_{monitor_id}")
            except Exception:
                pass
            if merged.get("enabled", False):
                _state._monitor_service._add_job(MonitorDSL(**merged))
        await audit_svc.log_action(
            user=user, action=AuditAction.UPDATE, resource=AuditResource.MONITOR,
            resource_id=monitor_id, resource_name=monitor_id, detail="AI 助手更新巡检任务",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已更新巡检任务：{monitor_id}"}

    if name == "delete_monitor":
        monitor_id = str(args.get("monitor_id", ""))
        doc = await db["monitors"].find_one({"id": monitor_id, "project_id": project_id})
        if not doc:
            return {"error": "Monitor not found", "monitor_id": monitor_id}
        if _state._monitor_service:
            try:
                _state._monitor_service._scheduler.remove_job(f"monitor_{monitor_id}")
            except Exception:
                pass
        r = await db["monitors"].delete_one({"id": monitor_id, "project_id": project_id})
        if not r.deleted_count:
            return {"error": "Monitor delete failed", "monitor_id": monitor_id}
        await audit_svc.log_action(
            user=user, action=AuditAction.DELETE, resource=AuditResource.MONITOR,
            resource_id=monitor_id, resource_name=doc.get("name", monitor_id), detail="AI 助手删除巡检任务",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已删除巡检任务：{monitor_id}"}

    # ── 数据工厂域 ──────────────────────────────────────
    if name == "create_data_template":
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        tmpl = DataTemplate(
            id=str(uuid.uuid4()),
            project_id=project_id,
            name=str(args.get("name", "")),
            fields=args.get("fields") or [],
            api_id=str(args.get("api_id", "")),
            source="ai_chat",
            created_at=now,
            updated_at=now,
            updated_by=user.get("username", ""),
        )
        await db["data_templates"].insert_one(tmpl.model_dump())
        await audit_svc.log_action(
            user=user, action=AuditAction.CREATE, resource=AuditResource.TEMPLATE,
            resource_id=tmpl.id, resource_name=tmpl.name, detail="AI 助手创建数据模板",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已创建数据模板：{tmpl.name}", "template_id": tmpl.id}

    if name == "update_data_template":
        template_id = str(args.get("template_id", ""))
        updates = args.get("updates") or {}
        old = await db["data_templates"].find_one({"id": template_id, "project_id": project_id})
        if not old:
            return {"error": "Template not found", "template_id": template_id}
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        updates["updated_at"] = now
        updates["updated_by"] = user.get("username", "")
        r = await db["data_templates"].update_one({"id": template_id, "project_id": project_id}, {"$set": updates})
        if not r.matched_count:
            return {"error": "Template update failed", "template_id": template_id}
        await audit_svc.log_action(
            user=user, action=AuditAction.UPDATE, resource=AuditResource.TEMPLATE,
            resource_id=template_id, resource_name=template_id, detail="AI 助手更新数据模板",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已更新数据模板：{template_id}"}

    if name == "delete_data_template":
        template_id = str(args.get("template_id", ""))
        r = await db["data_templates"].delete_one({"id": template_id, "project_id": project_id})
        if not r.deleted_count:
            return {"error": "Template not found", "template_id": template_id}
        await audit_svc.log_action(
            user=user, action=AuditAction.DELETE, resource=AuditResource.TEMPLATE,
            resource_id=template_id, resource_name=template_id, detail="AI 助手删除数据模板",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已删除数据模板：{template_id}"}

    # ── Mock 服务域 ─────────────────────────────────────
    if name == "create_mock_service":
        mgr = MockServiceManager(db)
        data = {
            "name": str(args.get("name", "")),
            "slug": str(args.get("slug", "")),
            "routes": args.get("routes") or [],
        }
        result = await mgr.create_service(data, project_id, user.get("username", ""))
        sid = result.get("id", "")
        await audit_svc.log_action(
            user=user, action=AuditAction.CREATE,
            resource=AuditResource("mock_service") if hasattr(AuditResource, "MOCK_SERVICE") else AuditResource.API,
            resource_id=sid, resource_name=data.get("name", sid), detail="AI 助手创建 Mock 服务",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已创建 Mock 服务：{data.get('name')}", "service_id": sid}

    if name == "update_mock_service":
        service_id = str(args.get("service_id", ""))
        updates = args.get("updates") or {}
        mgr = MockServiceManager(db)
        result = await mgr.update_service(service_id, updates, user.get("username", ""))
        if result is None:
            return {"error": "Mock service not found", "service_id": service_id}
        return {"ok": True, "message": f"已更新 Mock 服务：{service_id}"}

    if name == "delete_mock_service":
        service_id = str(args.get("service_id", ""))
        mgr = MockServiceManager(db)
        ok = await mgr.delete_service(service_id)
        if not ok:
            return {"error": "Mock service not found", "service_id": service_id}
        await audit_svc.log_action(
            user=user, action=AuditAction.DELETE,
            resource=AuditResource("mock_service") if hasattr(AuditResource, "MOCK_SERVICE") else AuditResource.API,
            resource_id=service_id, resource_name=service_id, detail="AI 助手删除 Mock 服务",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已删除 Mock 服务：{service_id}"}

    # ── 知识库域 ────────────────────────────────────────
    if name == "upsert_knowledge":
        knowledge_svc = _state._knowledge_service
        if not knowledge_svc:
            return {"error": "KnowledgeService not available"}
        data = {
            "type": str(args.get("type", "")),
            "title": str(args.get("key", "")),
            "content": str(args.get("content", "")),
            "tags": args.get("tags") or [],
            "project_id": project_id,
            "source_api_ids": [str(args.get("api_id"))] if args.get("api_id") else [],
        }
        entry_id = await knowledge_svc.upsert_entry(data)
        await audit_svc.log_action(
            user=user, action=AuditAction.CREATE, resource=AuditResource.KNOWLEDGE,
            resource_id=entry_id, resource_name=data.get("title", entry_id), detail="AI 助手创建/更新知识条目",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已创建/更新知识条目：{data.get('title')}", "entry_id": entry_id}

    # ── 差异域 ──────────────────────────────────────────
    if name == "resolve_diff":
        diff_id = str(args.get("diff_id", ""))
        action = str(args.get("action", ""))
        if action not in ("confirm", "dismiss"):
            return {"error": "action must be 'confirm' or 'dismiss'"}
        diff_svc = DiffService(db)
        ok = await diff_svc.resolve_diff(diff_id, action)
        if not ok:
            return {"error": "Diff not found or resolve failed", "diff_id": diff_id}
        await audit_svc.log_action(
            user=user, action=AuditAction.UPDATE, resource=AuditResource.DIFF_ALERT,
            resource_id=diff_id, resource_name=diff_id, detail=f"AI 助手处理差异告警：{action}",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已处理差异告警：{diff_id}（{action}）"}

    # ── 批量域 ──────────────────────────────────────────
    if name == "batch_generate":
        gen_type = str(args.get("type", ""))
        api_ids = args.get("api_ids") or []
        if gen_type not in ("doc", "asserts", "scenario", "data_template"):
            return {"error": f"unsupported generation type: {gen_type}"}
        # 批量生成：为每个 API 创建一个 GenerationVersion pending_review 条目
        from models.generation_version import GenerationVersion, GenerationSource, GenerationStatus, GenerationType
        created = []
        now_dt = _now() if "_now" in dir() else __import__("datetime").datetime.now(__import__("datetime").timezone(__import__("datetime").timedelta(hours=8))).replace(tzinfo=None)
        for api_id in api_ids:
            gen_type_enum = {"doc": GenerationType.DOC, "asserts": GenerationType.ASSERTS,
                             "scenario": GenerationType.SCENARIO, "data_template": GenerationType.DATA_TEMPLATE}.get(gen_type)
            if not gen_type_enum:
                continue
            gv = GenerationVersion(
                api_id=api_id,
                type=gen_type_enum,
                status=GenerationStatus.PENDING_REVIEW,
                content={"chat_suggestion": f"AI 助手批量生成 {gen_type}", "user_message": f"batch_generate {gen_type}"},
                summary=f"批量 AI 生成：{gen_type}",
                source=GenerationSource.AI_CHAT,
                project_id=project_id,
                api_ids=[api_id],
                job_id=f"batch:{uuid.uuid4().hex[:8]}",
            )
            doc = gv.model_dump()
            doc.pop("id", None)
            await db["generation_versions"].insert_one(doc)
            created.append(api_id)
        await audit_svc.log_action(
            user=user, action=AuditAction.GENERATE, resource=AuditResource.API,
            resource_id="batch", resource_name=f"batch_{gen_type}", detail=f"AI 助手批量生成 {gen_type}：{len(created)} 个 API",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已批量创建 {len(created)} 个 {gen_type} 生成任务，请在审核中心查看", "api_ids": created}

    # Phase 2: 执行操作（触发实际运行，异步执行不阻塞确认响应）
    if name == "run_api":
        # 调用 ApiService.run_single_api 执行接口测试，异步执行返回 execution_id
        api_id = str(args.get("api_id", ""))
        env_id = str(args.get("env_id") or "")
        redis = await get_redis()
        api_svc = ApiService(db)
        result = await api_svc.run_single_api(api_id, redis, environment_id=env_id, owner=user.get("username", ""))
        if result is None:
            return {"error": "API not found or execution failed", "api_id": api_id}
        record, execution_id = result
        await audit_svc.log_action(
            user=user, action=AuditAction.EXECUTE, resource=AuditResource.API,
            resource_id=api_id, resource_name=api_id, detail=f"AI 助手执行接口测试 → {execution_id}",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已执行接口 {api_id}，execution_id: {execution_id}", "execution_id": execution_id, "passed": record.get("passed")}

    if name == "run_scenario":
        # 调用 ScenarioService.run_scenario 执行场景测试，异步执行返回 record
        scenario_id = str(args.get("scenario_id", ""))
        env_id = str(args.get("env_id") or "")
        redis = await get_redis()
        scenario_svc = ScenarioService(db)
        record = await scenario_svc.run_scenario(
            scenario_id, redis,
            environment_id=env_id,
            owner=user.get("username", ""),
            project_id=project_id,
        )
        if record is None:
            return {"error": "Scenario not found or execution failed", "scenario_id": scenario_id}
        await audit_svc.log_action(
            user=user, action=AuditAction.EXECUTE, resource=AuditResource.SCENARIO,
            resource_id=scenario_id, resource_name=scenario_id, detail=f"AI 助手执行场景测试 → {record.id}",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已执行场景 {scenario_id}，execution_id: {record.id}", "execution_id": record.id, "passed": record.passed}

    if name == "run_monitor":
        # 通过 _state._monitor_service._run_monitor 手动触发巡检（异步执行，避免阻塞）
        monitor_id = str(args.get("monitor_id", ""))
        monitor_doc = await db["monitors"].find_one({"id": monitor_id, "project_id": project_id})
        if not monitor_doc:
            return {"error": "Monitor not found", "monitor_id": monitor_id}
        if not _state._monitor_service:
            return {"error": "MonitorService not available"}
        # 异步触发巡检，不等待完成（长时间运行）
        asyncio.create_task(_state._monitor_service._run_monitor(monitor_id))
        await audit_svc.log_action(
            user=user, action=AuditAction.EXECUTE, resource=AuditResource.MONITOR,
            resource_id=monitor_id, resource_name=monitor_doc.get("name", monitor_id), detail="AI 助手手动触发巡检",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已触发巡检 {monitor_doc.get('name', monitor_id)}，请稍后查看执行结果"}

    if name == "generate_data":
        # 根据数据模板 ID 查找模板 → 调用 DataFactory.generate_and_cache 生成并缓存数据
        from data_factory.factory import DataFactory, DataTemplate
        template_id = str(args.get("template_id", ""))
        count = int(args.get("count") or 1)
        count = max(1, min(count, 100))
        context = args.get("context") or {}
        # 查找模板：优先按 id 查找，其次按 api_id 查找
        template_doc = await db["data_templates"].find_one({"id": template_id, "project_id": project_id})
        if not template_doc:
            template_doc = await db["data_templates"].find_one({"api_id": template_id, "project_id": project_id})
        if not template_doc:
            return {"error": "Data template not found", "template_id": template_id}
        template = DataTemplate(**{k: v for k, v in template_doc.items() if k != "_id"})
        redis = await get_redis()
        factory = DataFactory(redis)
        cache_key, data = await factory.generate_and_cache(template, context, count)
        await audit_svc.log_action(
            user=user, action=AuditAction.EXECUTE, resource=AuditResource("data_template"),
            resource_id=template_id, resource_name=template.name, detail=f"AI 助手生成 {count} 条测试数据",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已生成 {count} 条测试数据", "cache_key": cache_key, "count": len(data), "preview": data[:3]}

    if name == "test_mock":
        # 调用 MockServiceManager.evaluate() 测试 Mock 服务路由匹配和响应
        service_id = str(args.get("service_id", ""))
        method = str(args.get("method") or "GET").upper()
        path = str(args.get("path") or "/")
        mgr = MockServiceManager(db)
        service = await mgr.get_service(service_id)
        if not service:
            return {"error": "Mock service not found", "service_id": service_id}
        # 构建模拟请求，调用 evaluate 测试匹配
        test_req = {"method": method, "path": path, "headers": {}, "query": {}, "body": None}
        eval_result = await mgr.evaluate(service, test_req, write_log=False)
        return {
            "ok": True,
            "message": f"Mock 服务 {service.get('name', service_id)} 测试完成",
            "service_id": service_id,
            "matched": eval_result.get("matched"),
            "status_code": eval_result.get("status_code"),
            "body_type": eval_result.get("body_type"),
            "body_preview": str(eval_result.get("body"))[:500] if eval_result.get("body") else None,
            "duration_ms": eval_result.get("duration_ms"),
        }

    if name == "toggle_monitor":
        # 启停巡检：更新 enabled 字段 + 同步 scheduler（复用 update_monitor 的调度器同步模式）
        monitor_id = str(args.get("monitor_id", ""))
        enabled = bool(args.get("enabled", True))
        old = await db["monitors"].find_one({"id": monitor_id, "project_id": project_id})
        if not old:
            return {"error": "Monitor not found", "monitor_id": monitor_id}
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        await db["monitors"].update_one({"id": monitor_id, "project_id": project_id}, {"$set": {"enabled": enabled, "updated_at": now}})
        # 同步调度器：禁用则移除 job，启用则重新注册
        if _state._monitor_service:
            from models.monitor_dsl import MonitorDSL
            try:
                _state._monitor_service._scheduler.remove_job(f"monitor_{monitor_id}")
            except Exception:
                pass
            if enabled:
                updated_doc = await db["monitors"].find_one({"id": monitor_id, "project_id": project_id})
                if updated_doc:
                    updated_doc.pop("_id", None)
                    _state._monitor_service._add_job(MonitorDSL(**updated_doc))
        action = "启用" if enabled else "禁用"
        await audit_svc.log_action(
            user=user, action=AuditAction.UPDATE, resource=AuditResource.MONITOR,
            resource_id=monitor_id, resource_name=old.get("name", monitor_id), detail=f"AI 助手{action}巡检任务",
            project_id=project_id,
        )
        return {"ok": True, "message": f"已{action}巡检任务：{old.get('name', monitor_id)}"}

    return {"error": f"unsupported write tool: {name}"}


# ── 确认端点 ────────────────────────────────────────────

@router.post("/ai/chat/confirm")
async def confirm_action(
    action_id: str = Body(...),
    approved: bool = Body(...),
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """用户确认/拒绝写操作提案。

    请求体：
    - action_id: 待确认操作 ID（来自 confirm_required SSE 事件）
    - approved: true=确认执行，false=拒绝

    返回 JSON（非 SSE），前端直接处理结果更新聊天 UI。
    """
    user = _get_user_from_request(request) or {}
    pending = _pending_actions.get(action_id)
    if not pending:
        raise HTTPException(404, "待确认操作不存在或已过期（5分钟有效期）")

    if not approved:
        # 用户拒绝：清理 pending，不执行
        _pending_actions.pop(action_id, None)
        return {
            "action_id": action_id,
            "approved": False,
            "message": "操作已取消",
        }

    # 用户确认：清理过期 pending，执行写操作
    _clean_expired_pending()

    tool_name = pending["tool"]
    args = pending["args"]
    pending_user = pending["user"]
    pending_context = pending["context"]

    try:
        result = await _execute_write_tool(db, pending_user, pending_context, tool_name, args)
        _pending_actions.pop(action_id, None)

        if "error" in result:
            return {
                "action_id": action_id,
                "approved": True,
                "status": "failed",
                "message": result.get("error", "执行失败"),
            }

        return {
            "action_id": action_id,
            "approved": True,
            "status": "executed",
            "message": result.get("message", "操作完成"),
            "result": result,
        }
    except Exception as e:
        logger.error("Write tool execution failed: tool={}, error={}", tool_name, e)
        _pending_actions.pop(action_id, None)
        return {
            "action_id": action_id,
            "approved": True,
            "status": "failed",
            "message": f"操作执行失败: {str(e)[:200]}",
        }


def _clean_expired_pending():
    """清理过期的 pending actions（超过 _PENDING_TTL 秒）。"""
    now = time.time()
    expired = [aid for aid, pa in _pending_actions.items() if now - pa.get("created_at", 0) > _PENDING_TTL]
    for aid in expired:
        _pending_actions.pop(aid, None)
        logger.info("Pending action {} expired and removed", aid)


async def _apply_context_prefetch(
    *,
    db: AsyncIOMotorDatabase,
    user: dict[str, Any],
    context: dict[str, Any],
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按页面上下文预取关键对象，解决模型未主动调用工具时回答失真的问题。

    详情页可预取场景/API/执行数据，使 AI 无需工具调用即可知道当前对象信息。
    """
    ctx_type = context.get("type")
    ctx_id = context.get("id", "")

    tool_map = {
        "api": "get_api",
        "execution": "get_execution",
        "scenario": "list_scenarios",
        # Part B: 扩展 context pre-fetch，让知识库/工厂/差异/Mock/覆盖度页面也能自动预取关联数据
        "knowledge": "search_knowledge",
        "factory": "list_data_templates",
        "import_diffs": "list_diff_alerts",
        "mock_services": "list_mock_services",
        "coverage": "get_coverage",
    }
    if ctx_type not in tool_map or not ctx_id:
        return messages, []

    tool_name = tool_map[ctx_type]
    args: dict[str, Any] = {}
    if ctx_type == "api":
        args["api_id"] = ctx_id
    elif ctx_type == "execution":
        args["execution_id"] = ctx_id
    elif ctx_type == "scenario":
        # Bug 5 修复：search 参数仅匹配 name/description，无法按 ID 匹配；改用 scenario_id 精确查找
        args["scenario_id"] = ctx_id
        args["limit"] = 5
    elif ctx_type in ("knowledge", "factory", "import_diffs", "mock_services"):
        args["search"] = ctx_id
        args["limit"] = 5
    elif ctx_type == "coverage":
        args["dimension"] = ""  # 预取全部维度

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
        # Bug 2 修复：SSE 生成器内不能抛 HTTPException，手动检查权限，不符合返回 error dict
        if doc.get("project_id") != project_id and not is_admin(user):
            return {"error": "无权访问该项目数据", "references": []}
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
        # Bug 2 修复：SSE 生成器内不能抛 HTTPException，手动检查权限
        if doc.get("project_id") != project_id and not is_admin(user):
            return {"error": "无权访问该项目数据", "references": []}
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
        # Bug 5 修复：支持 scenario_id 精确查找，用于上下文预取
        scenario_id = str(args.get("scenario_id") or "").strip()
        if scenario_id:
            filt["id"] = scenario_id
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

    # Part B: 新增 5 个只读工具分支
    if name == "search_knowledge":
        limit = _coerce_limit(args.get("limit"), default=5, maximum=10)
        query = str(args.get("query") or "").strip()[:120]
        ktype = str(args.get("type") or "").strip()
        tag = str(args.get("tag") or "").strip()
        # 构建筛选条件，仅查询当前项目下的 knowledge 条目
        filt: dict[str, Any] = {"project_id": project_id}
        if ktype:
            filt["type"] = ktype
        if tag:
            filt["tags"] = tag
        if query:
            regex = {"$regex": query, "$options": "i"}
            filt["$or"] = [{"title": regex}, {"content": regex}]
        docs = await db["knowledge"].find(filt, {"_id": 0}).sort("usage_count", -1).limit(limit).to_list(limit)
        items = [_compact_knowledge(d) for d in docs]
        return {"total": len(items), "items": items, "references": [_ref_knowledge(i) for i in items]}

    if name == "list_data_templates":
        limit = _coerce_limit(args.get("limit"), default=5, maximum=10)
        search = str(args.get("search") or "").strip()[:120]
        api_id = str(args.get("api_id") or "").strip()
        # 构建筛选条件
        filt: dict[str, Any] = {"project_id": project_id}
        if api_id:
            filt["api_id"] = api_id
        if search:
            filt["name"] = {"$regex": search, "$options": "i"}
        docs = await db["data_templates"].find(filt, {"_id": 0}).sort("updated_at", -1).limit(limit).to_list(limit)
        items = [_compact_template(d) for d in docs]
        return {"total": len(items), "items": items, "references": [_ref_template(i) for i in items]}

    if name == "list_diff_alerts":
        limit = _coerce_limit(args.get("limit"), default=5, maximum=10)
        status = str(args.get("status") or "").strip()
        severity = str(args.get("severity") or "").strip()
        # 构建筛选条件
        filt: dict[str, Any] = {"project_id": project_id}
        if status:
            filt["status"] = status
        if severity:
            filt["severity"] = severity
        docs = await db["import_diffs"].find(filt, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
        items = [_compact_diff(d) for d in docs]
        return {"total": len(items), "items": items, "references": [_ref_diff(i) for i in items]}

    if name == "list_mock_services":
        limit = _coerce_limit(args.get("limit"), default=5, maximum=10)
        search = str(args.get("search") or "").strip()[:120]
        # 构建筛选条件
        filt: dict[str, Any] = {"project_id": project_id}
        if search:
            regex = {"$regex": search, "$options": "i"}
            filt["$or"] = [{"name": regex}, {"slug": regex}]
        enabled = args.get("enabled")
        if enabled is not None:
            filt["enabled"] = bool(enabled)
        docs = await db["mock_services"].find(filt, {"_id": 0}).sort("last_called_at", -1).limit(limit).to_list(limit)
        items = [_compact_mock_service(d) for d in docs]
        return {"total": len(items), "items": items, "references": [_ref_mock_service(i) for i in items]}

    if name == "get_coverage":
        # 复用 StatsService.get_coverage() 获取运行时聚合的覆盖率矩阵
        dimension = str(args.get("dimension") or "").strip()
        stats_service = StatsService(db)
        coverage = await stats_service.get_coverage(project_id)
        # 按维度筛选（如需）
        if dimension:
            valid_dims = {"doc", "asserts", "scenario", "monitor", "execute"}
            if dimension in valid_dims:
                # 仅保留指定维度的覆盖数据
                coverage["dimensions"] = [dimension]
                filtered_matrix = []
                for row in coverage.get("matrix", []):
                    filtered_row = {"module": row.get("module"), "total": row.get("total")}
                    filtered_row[dimension] = row.get(dimension)
                    filtered_matrix.append(filtered_row)
                coverage["matrix"] = filtered_matrix
        # 提取未覆盖 API 列表，便于 AI 指出覆盖盲区
        uncovered: list[dict[str, Any]] = []
        for row in coverage.get("matrix", []):
            module_uncovered_apis = row.get("uncovered_apis", [])
            for api_info in module_uncovered_apis[:3]:
                uncovered.append({"module": row.get("module"), "api_id": api_info.get("api_id"), "path": api_info.get("path")})
        return {
            "modules_count": len(coverage.get("modules", [])),
            "dimensions": coverage.get("dimensions", []),
            "matrix": coverage.get("matrix", [])[:10],
            "uncovered_apis_preview": uncovered[:10],
            "references": [{"type": "coverage", "id": project_id, "title": f"覆盖率矩阵 ({project_id})", "route": "/coverage"}],
        }

    # Phase 1: 面板统计查询（4 个只读工具，复用 StatsService 已有方法）
    if name == "get_overview_stats":
        # 调用 StatsService.get_overview() 获取项目全局概览（API/场景/执行/监控/告警/LLM）
        stats_service = StatsService(db)
        overview = await stats_service.get_overview(project_id)
        return {
            "data": overview,
            "references": [{"type": "overview", "id": project_id, "title": f"平台概览 ({project_id})", "route": "/dashboard"}],
        }

    if name == "get_top_failing_apis":
        # 调用 StatsService.get_top_failing() 获取时间窗口内失败次数最多的 API 排行
        limit = _coerce_limit(args.get("limit"), default=10, maximum=20)
        hours = int(args.get("hours") or 24)
        hours = max(1, min(hours, 720))
        stats_service = StatsService(db)
        result = await stats_service.get_top_failing(project_id, limit=limit, hours=hours)
        refs = [{"type": "api", "id": item["api_id"], "title": f"{item.get('method', '')} {item.get('path', '')}"} for item in result.get("items", [])[:5]]
        return {"data": result, "references": refs}

    if name == "get_api_health":
        # 调用 StatsService.get_health_scores() 获取 API 健康评分列表（综合成功率+响应时间+活跃度）
        limit = _coerce_limit(args.get("limit"), default=20, maximum=50)
        stats_service = StatsService(db)
        result = await stats_service.get_health_scores(project_id, limit=limit)
        refs = [{"type": "api", "id": item["api_id"], "title": f"{item.get('method', '')} {item.get('path', '')}"} for item in result.get("items", [])[:10]]
        return {"data": result, "references": refs}

    if name == "get_execution_trend":
        # 调用 StatsService.get_trends() 获取按时/天粒度的执行通过率/失败数趋势
        period = str(args.get("period") or "24h").strip()
        if period not in ("24h", "7d", "30d"):
            period = "24h"
        granularity = str(args.get("granularity") or "hour").strip()
        if granularity not in ("hour", "day"):
            granularity = "hour"
        stats_service = StatsService(db)
        result = await stats_service.get_trends(project_id, period=period, granularity=granularity)
        return {
            "data": result,
            "references": [{"type": "trend", "id": project_id, "title": f"执行趋势 ({period}/{granularity})", "route": "/dashboard"}],
        }

    return {"error": f"unsupported tool: {name}"}


def _parse_tool_arguments(raw: str | None) -> dict[str, Any]:
    """解析模型生成的工具参数；非 JSON 时降级为空对象，避免中断整条 SSE。"""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        logger.warning("_parse_tool_arguments: invalid JSON, raw={}", str(raw)[:200])
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


# Part B: 新增 5 个工具的 compact / ref 辅助函数

def _compact_knowledge(doc: dict[str, Any]) -> dict[str, Any]:
    content = str(doc.get("content") or "")
    return {
        "id": doc.get("id") or str(doc.get("_id")),
        "type": doc.get("type"),
        "title": doc.get("title"),
        "content": content[:300] + ("..." if len(content) > 300 else ""),
        "tags": doc.get("tags") or [],
        "confidence": doc.get("confidence"),
        "usage_count": doc.get("usage_count"),
    }


def _compact_template(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": doc.get("id") or str(doc.get("_id")),
        "name": doc.get("name"),
        "field_count": len(doc.get("fields") or []),
        "source": doc.get("source"),
        "api_id": doc.get("api_id"),
        "updated_at": doc.get("updated_at"),
    }


def _compact_diff(doc: dict[str, Any]) -> dict[str, Any]:
    fields_diff = doc.get("fields_diff") or {}
    # 仅保留差异类型摘要（新增/删除/修改的字段名列表），避免泄露完整字段内容
    diff_summary = {
        "added_fields": list(fields_diff.get("added", {}).keys())[:10] if isinstance(fields_diff, dict) else [],
        "removed_fields": list(fields_diff.get("removed", {}).keys())[:10] if isinstance(fields_diff, dict) else [],
        "modified_fields": list(fields_diff.get("modified", {}).keys())[:10] if isinstance(fields_diff, dict) else [],
    }
    return {
        "id": doc.get("id") or str(doc.get("_id")),
        "api_path": doc.get("api_path"),
        "method": doc.get("method"),
        "status": doc.get("status"),
        "severity": doc.get("severity"),
        "fields_diff_summary": diff_summary,
        "created_at": doc.get("created_at"),
    }


def _compact_mock_service(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": doc.get("id") or str(doc.get("_id")),
        "name": doc.get("name"),
        "slug": doc.get("slug"),
        "enabled": doc.get("enabled"),
        "base_path": doc.get("base_path"),
        "route_count": len(doc.get("routes") or []),
        "last_called_at": doc.get("last_called_at"),
    }


def _ref_knowledge(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "knowledge",
        "id": item.get("id"),
        "title": item.get("title") or item.get("id"),
        "path": item.get("type"),
        "route": "/knowledge",
    }


def _ref_template(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "data_template",
        "id": item.get("id"),
        "title": item.get("name") or item.get("id"),
        "path": item.get("api_id"),
        "route": "/factory",
    }


def _ref_diff(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "import_diff",
        "id": item.get("id"),
        "title": f"{item.get('method') or '?'} {item.get('api_path') or item.get('id')}",
        "path": item.get("api_path"),
        "route": "/import-diffs",
    }


def _ref_mock_service(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "mock_service",
        "id": item.get("id"),
        "title": item.get("name") or item.get("slug") or item.get("id"),
        "path": item.get("base_path"),
        "route": "/mock-services",
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


# Part A2: ReMe 检索关键词映射，按 context type 定位最相关的记忆
_RETRIEVAL_KEYWORDS: dict[str, str] = {
    "api": "API 接口测试 断言 文档",
    "api_list": "API 接口测试 断言 文档",
    "scenario": "测试场景 DAG编排 步骤参数传递",
    "scenario_list": "测试场景 DAG编排 步骤参数传递",
    "execution": "执行结果 失败诊断 断言",
    "execution_list": "执行结果 失败诊断 断言",
    "dashboard": "质量概览 趋势 覆盖率",
    "monitor": "巡检 cron 告警 健康检查",
    "factory": "数据工厂 Faker 测试数据",
    "generations": "AI生成 审核 文档 断言",
    "coverage": "测试覆盖率 覆盖盲区",
    "knowledge": "知识库 字段模式 断言模式",
    "settings": "LLM配置 环境 项目 权限",
    "import_diffs": "差异对比 HAR导入 接口变更",
    "mock_services": "Mock服务 路由规则 响应模板",
}


async def _build_enhanced_system_prompt(
    base_system: str,
    context: dict,
    db: AsyncIOMotorDatabase,
    *,
    memory=None,
    project_id: str = "",
) -> str:
    """构建增强版 system prompt，依次注入页面知识（始终）和 ReMe 记忆（可用时）。

    页面知识来自 PAGE_KNOWLEDGE dict，为 AI 提供当前页面的功能、流程、
    交互方式和变量语法等结构化信息。
    ReMe 记忆通过 MemoryService.retrieve() 跨 L1+L2+L3+语义检索相关历史记忆，
    最多取 3 条高分结果。

    注入顺序：base_system → 页面知识 → ReMe 记忆 → 工具使用规则（在调用方追加）
    """
    ctx_type = context.get("type", "") if context else ""
    ctx_id = context.get("id", "") if context else ""

    # 1. 注入页面知识（始终执行，从 PAGE_KNOWLEDGE 查找）
    page_knowledge = PAGE_KNOWLEDGE.get(ctx_type)
    knowledge_block = ""
    if page_knowledge:
        parts: list[str] = []
        parts.append("## 当前页面知识")
        # 用途
        if page_knowledge.get("purpose"):
            parts.append(f"### 页面用途\n{page_knowledge['purpose']}")
        # 核心功能
        features = page_knowledge.get("features") or []
        if features:
            parts.append("### 核心功能\n" + "\n".join(f"- {f}" for f in features))
        # 常用操作流程
        workflows = page_knowledge.get("workflows") or []
        if workflows:
            parts.append("### 常用操作流程\n" + "\n".join(f"{i+1}. {w}" for i, w in enumerate(workflows)))
        # 变量引用语法（如有）
        if page_knowledge.get("variables"):
            parts.append(f"### 变量引用语法\n{page_knowledge['variables']}")
        # 重要提示（如有）
        tips = page_knowledge.get("tips") or []
        if tips:
            parts.append("### 重要提示\n" + "\n".join(f"- {t}" for t in tips))
        knowledge_block = "\n\n" + "\n\n".join(parts)
    else:
        # 兜底：旧版简单上下文提示
        ctx_hint = ""
        if ctx_type:
            ctx_hint = f"用户正在查看：{ctx_type}"
            if ctx_id:
                ctx_hint += f"（ID: {ctx_id}）"
            ctx_hint += "\n回答时优先关联此上下文。"
        if ctx_hint:
            knowledge_block = f"\n\n## 当前页面上下文\n{ctx_hint}"

    # 2. 注入 ReMe 记忆（MemoryService 可用时执行）
    memory_block = ""
    if memory and ctx_type and project_id:
        try:
            query = _RETRIEVAL_KEYWORDS.get(ctx_type, ctx_type)
            results = await memory.retrieve(project_id, query, limit=3)
            # 聚合 L1/L2/L3/semantic 各层结果，按相关性取最多 3 条
            all_hits: list[dict[str, Any]] = []
            for layer in ("l1", "l2", "l3", "semantic"):
                for item in results.get(layer, []) or []:
                    content = item.get("content") or item.get("summary") or item.get("key", "")
                    if content and isinstance(content, str) and len(content) > 10:
                        all_hits.append({
                            "layer": layer,
                            "content": content[:300] + ("..." if len(content) > 300 else ""),
                        })
                        if len(all_hits) >= 3:
                            break
                if len(all_hits) >= 3:
                    break
            if all_hits:
                lines = [f"- [{h['layer'].upper()}] {h['content']}" for h in all_hits]
                memory_block = "\n\n## 相关记忆（来自知识库）\n" + "\n".join(lines)
        except Exception:
            # MemoryService.retrieve() 失败时静默降级，不影响主流程
            pass

    return base_system + knowledge_block + memory_block


def _sse(data: dict) -> str:
    """格式化为 SSE 事件行。"""
    return f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _get_memory():
    """获取 MemoryService 实例（可能为 None）。

    使用 _state._memory_service 动态访问，而非 from import 绑定的初始值 None。"""
    try:
        return _state._memory_service
    except Exception:
        return None


async def _save_history(memory, redis, ctx_key: str, user_id: str, session_id: str,
                        history: list[dict[str, Any]]) -> None:
    """保存对话历史：优先 MemoryService L4，同时写 Redis 作为降级备份。"""
    if memory:
        await memory.set_l4(user_id, session_id, history, ttl=CHAT_CTX_TTL)
    # 双写 Redis 作为降级路径（MemoryService 不可用时仍可读取历史）
    await redis.setex(ctx_key, CHAT_CTX_TTL, json.dumps(history, ensure_ascii=False, default=str))


async def _save_l3_snapshot(memory, user_id: str, session_id: str,
                            project_id: str, history: list[dict[str, Any]],
                            tags: list[str] | None = None) -> None:
    """将对话快照写入 L3 会话记忆，使记忆模块可检索本次对话记录。

    MemoryService 不可用或摘要生成失败时静默降级，不影响主流程。
    """
    if not memory:
        logger.info("L3 snapshot skipped: MemoryService not available (user={}, session={})", user_id, session_id)
        return
    if not history:
        logger.info("L3 snapshot skipped: empty history (user={}, session={})", user_id, session_id)
        return
    try:
        logger.info("Saving L3 snapshot: user={}, session={}, project={}, history_len={}", user_id, session_id, project_id, len(history))
        await memory.save_session_to_l3(user_id, session_id, project_id, history, tags=tags)
        logger.info("L3 snapshot saved successfully: user={}, session={}", user_id, session_id)
    except Exception as e:
        logger.warning("Failed to save L3 snapshot for session {}: {}", session_id, e)
