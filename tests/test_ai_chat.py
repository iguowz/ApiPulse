"""
P1-3: AI 对话面板测试

验证：
1. /ai/chat 端点存在且参数校验（空 message → 400）
2. /ai/chat/tools 返回工具列表
3. _build_system_with_context 正确注入页面上下文
4. /ai/chat/history/{id} GET/DELETE 端点存在
5. SSE 格式（_sse 函数）正确
"""
from __future__ import annotations

import json
from fastapi.testclient import TestClient
from fastapi import FastAPI

from api.routers.ai_chat import (
    router, _build_system_with_context, _sse, _CHAT_SYSTEM, _AVAILABLE_TOOLS,
    _TOOL_DEFINITIONS, _coerce_limit, _parse_tool_arguments, _sanitize_tool_result,
    _extract_first_json, _structured_chat_generation_content,
    CHAT_CTX_MAX_ROUNDS, CHAT_CTX_TTL,
)
from models.generation_version import GenerationType


def _make_app():
    app = FastAPI()
    app.include_router(router)
    return app


def test_chat_endpoint_validates_empty_message():
    """空 message → 400。"""
    client = TestClient(_make_app())
    r = client.post("/ai/chat", json={"message": ""})
    assert r.status_code == 400


def test_chat_endpoint_validates_missing_message():
    """缺少 message 字段 → 422（pydantic 校验）或 400。"""
    client = TestClient(_make_app())
    r = client.post("/ai/chat", json={})
    assert r.status_code in (400, 422)


def test_chat_tools_returns_tool_list():
    """/ai/chat/tools 应返回工具列表。"""
    client = TestClient(_make_app())
    r = client.get("/ai/chat/tools")
    assert r.status_code == 200
    data = r.json()
    assert "tools" in data
    assert isinstance(data["tools"], list)
    assert len(data["tools"]) > 0
    # 每个工具应有 name 和 description
    for tool in data["tools"]:
        assert "name" in tool
        assert "description" in tool


def test_tool_definitions_match_public_tool_list():
    """OpenAI tools 协议定义应与公开工具列表保持一致。"""
    names = [t["function"]["name"] for t in _TOOL_DEFINITIONS]
    assert names == _AVAILABLE_TOOLS
    for tool in _TOOL_DEFINITIONS:
        assert tool["type"] == "function"
        assert tool["function"]["parameters"]["type"] == "object"
        assert tool["function"]["parameters"]["additionalProperties"] is False


def test_tool_argument_helpers_are_defensive():
    """工具参数解析/数量限制应容错，避免坏参数中断 SSE。"""
    assert _parse_tool_arguments('{"limit": 3}') == {"limit": 3}
    assert _parse_tool_arguments("{bad json") == {}
    assert _parse_tool_arguments("[1,2]") == {}
    assert _coerce_limit("99", default=5, maximum=10) == 10
    assert _coerce_limit("bad", default=5, maximum=10) == 5


def test_tool_result_sanitizes_sensitive_fields():
    """工具结果进入模型上下文前应脱敏。"""
    result = _sanitize_tool_result({
        "Authorization": "Bearer token",
        "nested": {"api_key": "secret", "name": "ok"},
    })
    assert result["Authorization"] == "***"
    assert result["nested"]["api_key"] == "***"
    assert result["nested"]["name"] == "ok"


def test_build_system_with_context_injects_page_info():
    """上下文应注入 system prompt。"""
    ctx = {"type": "api", "id": "api-123"}
    result = _build_system_with_context(_CHAT_SYSTEM, ctx, None)
    assert "api-123" in result, "应包含上下文 ID"
    assert "api" in result.lower(), "应包含上下文类型"
    assert "当前页面上下文" in result


def test_build_system_with_context_empty_returns_base():
    """空上下文 → 返回原始 system prompt。"""
    result = _build_system_with_context(_CHAT_SYSTEM, {}, None)
    assert result == _CHAT_SYSTEM
    result2 = _build_system_with_context(_CHAT_SYSTEM, None, None)
    assert result2 == _CHAT_SYSTEM


def test_sse_format_correct():
    """_sse 应返回标准 SSE 格式（data: json\n\n）。"""
    data = {"type": "delta", "content": "hello"}
    result = _sse(data)
    assert result.startswith("data: ")
    assert result.endswith("\n\n")
    # 解析回 JSON 应一致
    parsed = json.loads(result[6:-2])
    assert parsed == data


def test_sse_handles_unicode():
    """_sse 应正确处理中文（ensure_ascii=False）。"""
    data = {"type": "delta", "content": "你好世界"}
    result = _sse(data)
    assert "你好世界" in result, "中文不应被转义为 \\uXXXX"


def test_chat_history_endpoints_exist():
    """history GET/DELETE 端点应注册存在（不实际调用，避免 TestClient + redis 跨 loop 问题）。"""
    paths_methods = [(r.path, list(getattr(r, "methods", set()))) for r in router.routes]
    all_endpoints = {(p, m) for p, methods in paths_methods for m in methods}
    # GET history
    assert any("/ai/chat/history/" in p and "GET" in ms for p, ms in paths_methods), "应有 GET history 端点"
    # DELETE history
    assert any("/ai/chat/history/" in p and "DELETE" in ms for p, ms in paths_methods), "应有 DELETE history 端点"


def test_chat_system_prompt_has_role_definition():
    """_CHAT_SYSTEM 应定义 AI 角色和能力。"""
    assert "ApiPulse" in _CHAT_SYSTEM, "应提及平台名"
    assert "助手" in _CHAT_SYSTEM or "Assistant" in _CHAT_SYSTEM, "应定义助手角色"
    # 应有回答风格指导
    assert "简洁" in _CHAT_SYSTEM or "中文" in _CHAT_SYSTEM


def test_chat_context_config_reasonable():
    """对话上下文配置应合理（轮数和 TTL）。"""
    assert CHAT_CTX_MAX_ROUNDS == 20, "应保留 20 轮历史"
    assert CHAT_CTX_TTL == 86400 * 7, "TTL 应为 7 天"


def test_chat_routes_registered():
    """4 个端点应全部注册。"""
    paths = [r.path for r in router.routes]
    assert "/ai/chat" in paths
    assert any("/ai/chat/history/" in p for p in paths)
    assert "/ai/chat/tools" in paths


def test_extract_first_json_from_markdown_fence():
    """聊天回复中的 fenced JSON 应能被提取为结构化内容。"""
    text = '建议如下：\n```json\n{"summary":"登录接口","params":[]}\n```'
    parsed = _extract_first_json(text)
    assert parsed == {"summary": "登录接口", "params": []}


def test_structured_chat_generation_content_doc():
    """doc 意图应优先保存标准文档 content，而不是纯 chat_suggestion。"""
    response = '{"summary":"登录","description":"用户登录","params":[],"response_fields":[],"tags":["auth"]}'
    content = _structured_chat_generation_content(GenerationType.DOC, response, "生成文档")
    assert content["summary"] == "登录"
    assert content["params"] == []
    assert "chat_suggestion" not in content


def test_structured_chat_generation_content_asserts_from_list():
    """asserts 意图支持 LLM 直接返回数组，审核后可自动应用。"""
    response = '[{"field":"$.code","operator":"equals","expected":0}]'
    content = _structured_chat_generation_content(GenerationType.ASSERTS, response, "生成断言")
    assert content == {"asserts": [{"field": "$.code", "operator": "equals", "expected": 0}]}


def test_structured_chat_generation_content_data_template():
    """data_template 意图应保留 fields 结构，供审核中心部分接受。"""
    response = '{"fields":[{"name":"email","faker_method":"email"}],"summary":"增强邮箱"}'
    content = _structured_chat_generation_content(GenerationType.DATA_TEMPLATE, response, "增强模板")
    assert content["fields"][0]["name"] == "email"


def test_structured_chat_generation_content_fallback_to_suggestion():
    """无法结构化时仍回退 chat_suggestion，避免丢失聊天建议。"""
    content = _structured_chat_generation_content(GenerationType.DOC, "这是一段自然语言建议", "帮我看看")
    assert content["chat_suggestion"] == "这是一段自然语言建议"
    assert content["user_message"] == "帮我看看"
