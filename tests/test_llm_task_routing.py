"""
LLM task_type 模型路由测试。

覆盖：配置解析、保存清洗、任务级覆盖优先于全局配置。
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.llm_config_service import (
    TASK_ROUTE_TYPES,
    resolve_llm_config,
    sanitize_task_routes,
    serialize_llm_config,
)


def test_resolve_task_route_overrides_global_config():
    cfg = {
        "provider": "openai",
        "api_key": "global-key",
        "base_url": "https://global.example/v1",
        "model": "global-model",
        "temperature": 0.1,
        "max_tokens": 4096,
        "stream": True,
        "task_routes": {
            "doc": {
                "enabled": True,
                "model": "doc-model",
                "temperature": 0.2,
                "max_tokens": 1234,
                "stream": False,
            }
        },
    }

    doc = resolve_llm_config(cfg, "doc")
    asserts = resolve_llm_config(cfg, "asserts")

    assert doc.model == "doc-model"
    assert doc.temperature == 0.2
    assert doc.max_tokens == 1234
    assert doc.stream is False
    assert doc.base_url == "https://global.example/v1"
    assert asserts.model == "global-model"
    assert asserts.stream is True


def test_sanitize_task_routes_drops_unknown_and_disabled_routes():
    cleaned = sanitize_task_routes({
        "doc": {"enabled": True, "model": "m1", "temperature": "0.4", "max_tokens": "2048", "stream": True},
        "unknown": {"enabled": True, "model": "bad"},
        "alert": {"enabled": False, "model": "ignored"},
    })

    assert set(cleaned) == {"doc"}
    assert cleaned["doc"]["temperature"] == 0.4
    assert cleaned["doc"]["max_tokens"] == 2048
    assert cleaned["doc"]["stream"] is True


def test_serialize_llm_config_returns_all_task_types_with_effective_values():
    data = serialize_llm_config({"base_url": "http://local/v1", "model": "global", "task_routes": {"alert": {"enabled": True, "model": "alert-model"}}})

    assert set(data["task_routes"].keys()) == set(TASK_ROUTE_TYPES)
    assert data["task_routes"]["alert"]["enabled"] is True
    assert data["task_routes"]["alert"]["effective"]["model"] == "alert-model"
    assert data["task_routes"]["doc"]["effective"]["model"] == "global"


@pytest.mark.asyncio
async def test_analyzer_call_llm_uses_task_route_runtime_config():
    from ai_analyzer.analyzer import AiAnalyzerService

    captured = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return MagicMock(
                choices=[MagicMock(message=MagicMock(content="{}"))],
                usage=None,
            )

    class FakeClient:
        chat = MagicMock(completions=FakeCompletions())

    db = MagicMock()
    redis = AsyncMock()
    svc = AiAnalyzerService.__new__(AiAnalyzerService)
    svc._db = db
    svc._redis = redis
    svc._client = FakeClient()
    svc._model = "global"
    svc._temperature = 0.1
    svc._max_tokens = 4096
    svc._runtime_llm_config = {
        "base_url": "http://local/v1",
        "model": "global",
        "task_routes": {"doc": {"enabled": True, "model": "doc-model", "temperature": 0.5, "max_tokens": 777}},
    }

    with patch("services.llm_config_service.LlmRuntimeConfig.build_client", return_value=FakeClient()):
        result = await svc._call_llm("hello", "system", max_tokens=3000, task_type="doc")

    assert result == "{}"
    assert captured["model"] == "doc-model"
    assert captured["temperature"] == 0.5
    assert captured["max_tokens"] == 777
