"""
AI 分析器测试
- _safe_parse_json: markdown 围栏去除 / 纯 JSON
- analyze_api: 已分析跳过 / LLM 解析文档 / 断言规则写入
- generate_scenarios: LLM 输出解析 / 场景持久化
- Worker: 失败重试 / DLQ 入队
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ai_analyzer.analyzer import AiAnalyzerService, AI_DLQ, MAX_RETRY
from models.dsl import ApiDSL, HttpMethod, RequestDSL, ResponseDSL
from services.structured_output_service import StructuredOutputError
from models.generation_version import GenerationStatus, GenerationType


# ── _safe_parse_json ──────────────────────────────────────

def test_safe_parse_json_plain():
    svc = _make_svc()
    result = svc._safe_parse_json('{"key": "value"}')
    assert result == {"key": "value"}


def test_safe_parse_json_with_markdown_fence():
    svc = _make_svc()
    text = '```json\n{"a": 1}\n```'
    assert svc._safe_parse_json(text) == {"a": 1}


def test_safe_parse_json_fence_no_lang():
    svc = _make_svc()
    text = '```\n[1,2,3]\n```'
    assert svc._safe_parse_json(text) == [1, 2, 3]


def test_safe_parse_json_invalid_raises():
    svc = _make_svc()
    with pytest.raises(ValueError):
        svc._safe_parse_json("not json at all")


def test_safe_parse_json_array():
    svc = _make_svc()
    text = '[{"a":1},{"b":2}]'
    result = svc._safe_parse_json(text)
    assert isinstance(result, list) and len(result) == 2


# ── analyze_api ───────────────────────────────────────────

def _make_api_doc(api_id="api1") -> dict:
    return {
        "id": api_id,
        "name": "POST /login",
        "source_har": "test.har",
        "source_hash": "abc123",
        "request": {
            "method": "POST",
            "url": "https://api.example.com/login",
            "path": "/login",
            "query_params": {},
            "headers": {},
            "body": {"username": "test", "password": "123"},
            "body_type": "json",
        },
        "response": {
            "status_code": 200,
            "headers": {},
            "body": {"code": 0, "data": {"token": "abc"}},
            "latency_ms": 120,
        },
        "asserts": [],
        "doc": {},
        "parse_status": "success",
        "analysis_status": "idle",
        "analysis_error": "",
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00",
        "project_id": "default",
        "env": "test",
        "tags": [],
        "base_url_override": "",
    }


def _make_svc(api_doc=None, already_analyzed=False):
    db = MagicMock()
    api_col = AsyncMock()
    scenario_col = AsyncMock()
    gen_col = AsyncMock()
    ai_jobs_col = AsyncMock()

    doc = api_doc or _make_api_doc()
    if already_analyzed:
        doc["analysis_status"] = "done"

    api_col.find_one = AsyncMock(return_value=doc)
    api_col.find = MagicMock(return_value=AsyncMock(
        to_list=AsyncMock(return_value=[doc])
    ))
    api_col.update_one = AsyncMock()
    scenario_col.insert_one = AsyncMock()
    gen_col.insert_one = AsyncMock(return_value=MagicMock(inserted_id="gv-1"))
    ai_jobs_col.update_one = AsyncMock()

    db.__getitem__ = MagicMock(side_effect=lambda k: {
        "api_dsls": api_col,
        "scenarios": scenario_col,
        "generation_versions": gen_col,
        "ai_jobs": ai_jobs_col,
    }[k])

    redis = AsyncMock()
    redis.blpop = AsyncMock(return_value=None)
    redis.rpush = AsyncMock()

    svc = AiAnalyzerService.__new__(AiAnalyzerService)
    svc._db = db
    svc._redis = redis
    svc._model = "gpt-4o-mini"
    svc._api_col = api_col
    svc._scenario_col = scenario_col
    svc._generation_col = gen_col
    return svc


@pytest.mark.asyncio
async def test_analyze_api_already_done():
    svc = _make_svc(already_analyzed=True)
    result = await svc.analyze_api("api1", force=False)
    assert result is True
    svc._api_col.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_analyze_api_not_found():
    svc = _make_svc()
    svc._api_col.find_one = AsyncMock(return_value=None)
    result = await svc.analyze_api("nonexistent")
    assert result is False


_MOCK_DOC_JSON = json.dumps({
    "summary": "用户登录接口",
    "description": "提供用户名密码换取 Token",
    "params": [
        {"name": "username", "location": "body", "type": "string",
         "required": True, "description": "用户名", "example": "alice"},
        {"name": "password", "location": "body", "type": "string",
         "required": True, "description": "密码", "example": "***"},
    ],
    "response_fields": [
        {"name": "code", "location": "body", "type": "int",
         "required": True, "description": "业务码", "example": 0},
        {"name": "data.token", "location": "body", "type": "string",
         "required": True, "description": "JWT Token", "example": "eyJ..."},
    ],
    "tags": ["auth", "user"],
})

_MOCK_ASSERT_JSON = json.dumps([
    {"field": "status_code", "operator": "eq", "expected": 200,
     "description": "HTTP 200", "risk_level": "critical"},
    {"field": "$.code", "operator": "eq", "expected": 0,
     "description": "业务码 0", "risk_level": "high"},
    {"field": "$.data.token", "operator": "exists", "expected": None,
     "description": "Token 存在", "risk_level": "high"},
])


@pytest.mark.asyncio
async def test_analyze_api_writes_doc_and_asserts():
    svc = _make_svc()

    async def mock_call_llm(user_prompt: str, system_prompt: str | None = None, **kwargs) -> str:
        if "文档工程师" in (system_prompt or user_prompt):
            return _MOCK_DOC_JSON
        return _MOCK_ASSERT_JSON

    svc._call_llm = mock_call_llm

    result = await svc.analyze_api("api1")
    assert result is True
    assert svc._generation_col.insert_one.await_count == 2
    saved_docs = [c.args[0] for c in svc._generation_col.insert_one.call_args_list]
    assert saved_docs[0]["type"] == "doc"
    assert saved_docs[0]["status"] == "pending_review"
    assert saved_docs[0]["content"]["summary"] == "用户登录接口"
    assert saved_docs[1]["type"] == "asserts"
    assert len(saved_docs[1]["content"]["asserts"]) == 3
    assert saved_docs[1]["content"]["asserts"][0]["field"] == "status_code"


@pytest.mark.asyncio
async def test_analyze_api_force_reanalyze():
    """force=True 应重新分析即使 analysis_status=done"""
    svc = _make_svc(already_analyzed=True)

    async def mock_call_llm(user_prompt: str, system_prompt: str | None = None, **kwargs) -> str:
        return _MOCK_DOC_JSON if "文档工程师" in (system_prompt or user_prompt) else _MOCK_ASSERT_JSON

    svc._call_llm = mock_call_llm
    result = await svc.analyze_api("api1", force=True)
    assert result is True
    assert svc._generation_col.insert_one.await_count == 2


@pytest.mark.asyncio
async def test_analyze_api_llm_failure():
    svc = _make_svc()

    async def bad_llm(user_prompt: str, system_prompt: str | None = None, **kwargs) -> str:
        raise ValueError("LLM timeout")

    svc._call_llm = bad_llm
    result = await svc.analyze_api("api1")
    assert result is False


@pytest.mark.asyncio
async def test_apply_version_doc_partial_merges_named_fields():
    """doc 部分采纳应支持 params:name / response_fields:name 粒度并保留未选字段。"""
    from bson import ObjectId
    oid = ObjectId()
    gv_doc = {
        "_id": oid,
        "id": str(oid),
        "api_id": "api1",
        "project_id": "default",
        "type": GenerationType.DOC.value,
        "status": GenerationStatus.PENDING_REVIEW.value,
        "content": {
            "summary": "新摘要",
            "description": "新描述",
            "params": [
                {"name": "page", "location": "query", "type": "integer", "description": "页码"},
                {"name": "size", "location": "query", "type": "integer", "description": "页大小"},
            ],
            "response_fields": [
                {"name": "data.id", "location": "body", "type": "string", "description": "ID"},
                {"name": "data.name", "location": "body", "type": "string", "description": "名称"},
            ],
            "tags": ["new"],
        },
    }
    api_col = AsyncMock()
    api_col.find_one = AsyncMock(return_value={
        "id": "api1",
        "doc": {
            "summary": "旧摘要",
            "description": "旧描述",
            "params": [{"name": "page", "location": "query", "type": "string", "description": "旧页码"}],
            "response_fields": [{"name": "data.name", "location": "body", "type": "string", "description": "旧名称"}],
            "tags": ["old"],
        },
    })
    api_col.update_one = AsyncMock()
    gen_col = AsyncMock()
    gen_col.find_one = AsyncMock(return_value=gv_doc)
    gen_col.update_one = AsyncMock()

    svc = _make_svc()
    svc._api_col = api_col
    svc._generation_col = gen_col
    svc._broadcast = AsyncMock()

    ok = await svc.apply_version(
        str(oid),
        reviewer_id="reviewer",
        partial_fields=["summary", "params:page", "response_fields:data.id"],
    )

    assert ok is True
    update_doc = api_col.update_one.call_args_list[0].args[1]["$set"]["doc"]
    assert update_doc["summary"] == "新摘要"
    assert update_doc["description"] == "旧描述"
    assert update_doc["tags"] == ["old"]
    assert update_doc["params"] == [{"name": "page", "location": "query", "type": "integer", "description": "页码"}]
    assert {f["name"] for f in update_doc["response_fields"]} == {"data.id", "data.name"}
    assert next(f for f in update_doc["response_fields"] if f["name"] == "data.name")["description"] == "旧名称"
    gen_col.update_one.assert_awaited_once()
    assert gen_col.update_one.call_args.args[1]["$set"]["status"] == GenerationStatus.PARTIALLY_ACCEPTED.value


# ── generate_scenarios ────────────────────────────────────

_MOCK_SCENARIO_JSON = json.dumps([
    {
        "name": "正常登录流程",
        "description": "登录后获取 token",
        "steps": [
            {
                "step_id": "step_1",
                "api_id": "api1",
                "name": "用户登录",
                "depends_on": [],
                "extract": {"token": "$.data.token"},
                "override_params": {},
            }
        ],
    }
])


@pytest.mark.asyncio
async def test_generate_scenarios_success():
    svc = _make_svc()

    async def mock_llm(user_prompt: str, system_prompt: str | None = None, **kwargs) -> str:
        return _MOCK_SCENARIO_JSON

    svc._call_llm = mock_llm
    ids = await svc.generate_scenarios(["api1"])
    assert len(ids) == 1
    svc._generation_col.insert_one.assert_awaited_once()
    saved = svc._generation_col.insert_one.call_args[0][0]
    assert saved["type"] == "scenario"
    assert saved["status"] == "pending_review"
    assert saved["content"]["name"] == "正常登录流程"


@pytest.mark.asyncio
async def test_generate_scenarios_no_apis():
    svc = _make_svc()
    svc._api_col.find = MagicMock(return_value=AsyncMock(
        to_list=AsyncMock(return_value=[])
    ))
    ids = await svc.generate_scenarios(["nonexistent"])
    assert ids == []


@pytest.mark.asyncio
async def test_generate_scenarios_llm_error():
    svc = _make_svc()

    async def bad_llm(user_prompt: str, system_prompt: str | None = None, **kwargs) -> str:
        raise RuntimeError("network error")

    svc._call_llm = bad_llm
    with pytest.raises(RuntimeError):
        await svc.generate_scenarios(["api1"])


# ── Worker: DLQ ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_requeue_below_max_retry():
    svc = _make_svc()
    await svc._requeue_or_dlq("api1", fail_count=1)
    svc._redis.rpush.assert_awaited_once()
    call_args = svc._redis.rpush.call_args[0]
    assert call_args[0] == "queue:ai_analyze"
    payload = json.loads(call_args[1])
    assert payload["fail_count"] == 1


@pytest.mark.asyncio
async def test_requeue_at_max_retry_goes_dlq():
    svc = _make_svc()
    await svc._requeue_or_dlq("api1", fail_count=MAX_RETRY)
    svc._redis.rpush.assert_awaited_once()
    call_args = svc._redis.rpush.call_args[0]
    assert call_args[0] == AI_DLQ


@pytest.mark.asyncio
async def test_requeue_dlq_keeps_structured_raw_preview():
    svc = _make_svc()
    err = StructuredOutputError("doc", "bad schema", raw_output='{"unexpected":"shape"}')
    await svc._requeue_or_dlq(
        "api1",
        fail_count=MAX_RETRY,
        error=svc._structured_error_detail(err),
    )
    payload = json.loads(svc._redis.rpush.call_args[0][1])
    assert "raw_output_preview" in payload["error"]
    assert "unexpected" in payload["error"]


@pytest.mark.asyncio
async def test_requeue_just_below_max_retry():
    svc = _make_svc()
    await svc._requeue_or_dlq("api1", fail_count=MAX_RETRY - 1)
    call_args = svc._redis.rpush.call_args[0]
    assert call_args[0] == "queue:ai_analyze"   # 还没到 DLQ
