"""
HAR 解析器测试 v2
- _should_skip: 静态资源过滤（扩展名 / content-type）
- _parse_body: json / form / multipart / xml / base64
- _parse_response_body: base64 解码 / json / text
- _make_hash: 确定性 / URL query 剥离
- _entry_to_dsl: 字段映射 / 伪头部过滤 / project_id 注入
- HarParserService.parse_and_save: 完整流程（成功/失败/去重/全静态/部分失败）
- _push_ai_queue: Redis pipeline 批量推送
"""
from __future__ import annotations

import base64
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, call

from har_parser.parser import (
    HarParserService,
    _entry_to_dsl,
    _make_hash,
    _parse_body,
    _parse_response_body,
    _should_skip,
)
from models.dsl import BodyType


# ─────────────────────────────────────────────────────────
# _should_skip
# ─────────────────────────────────────────────────────────

def _entry_with_url(url: str, content_type: str = "application/json") -> dict:
    return {
        "request": {"url": url, "method": "GET", "headers": [], "queryString": []},
        "response": {
            "status": 200,
            "headers": [{"name": "Content-Type", "value": content_type}],
            "content": {"mimeType": content_type, "text": "{}"},
        },
        "time": 10,
    }


@pytest.mark.parametrize("url,ct,expected_skip", [
    ("https://cdn.example.com/app.js", "application/javascript", True),
    ("https://cdn.example.com/style.css", "text/css", True),
    ("https://cdn.example.com/logo.png", "image/png", True),
    ("https://cdn.example.com/font.woff2", "font/woff2", True),
    ("https://api.example.com/v1/users", "application/json", False),
    ("https://api.example.com/v1/login", "application/json", False),
    ("https://api.example.com/data.json", "application/json", False),
    ("https://api.example.com/", "text/html", True),
    ("https://cdn.example.com/bundle.map", "application/json", True),  # .map 扩展名过滤
])
def test_should_skip(url, ct, expected_skip):
    entry = _entry_with_url(url, ct)
    assert _should_skip(entry) == expected_skip


# ─────────────────────────────────────────────────────────
# _parse_body
# ─────────────────────────────────────────────────────────

def test_parse_body_none():
    body, bt = _parse_body(None)
    assert body is None
    assert bt == BodyType.NONE


def test_parse_body_json():
    post = {"mimeType": "application/json", "text": '{"a": 1}'}
    body, bt = _parse_body(post)
    assert body == {"a": 1}
    assert bt == BodyType.JSON


def test_parse_body_json_invalid_text():
    post = {"mimeType": "application/json", "text": "not json"}
    body, bt = _parse_body(post)
    assert body == "not json"
    assert bt == BodyType.TEXT


def test_parse_body_form_params():
    post = {
        "mimeType": "application/x-www-form-urlencoded",
        "params": [{"name": "user", "value": "alice"}, {"name": "pw", "value": "123"}],
        "text": "",
    }
    body, bt = _parse_body(post)
    assert body == {"user": "alice", "pw": "123"}
    assert bt == BodyType.FORM


def test_parse_body_form_text_fallback():
    post = {
        "mimeType": "application/x-www-form-urlencoded",
        "params": [],
        "text": "user=alice&pw=123",
    }
    body, bt = _parse_body(post)
    assert body == {"user": "alice", "pw": "123"}
    assert bt == BodyType.FORM


def test_parse_body_multipart():
    post = {
        "mimeType": "multipart/form-data",
        "params": [{"name": "file", "value": "content"}],
    }
    body, bt = _parse_body(post)
    assert bt == BodyType.MULTIPART
    assert body == {"file": "content"}


def test_parse_body_xml():
    post = {"mimeType": "application/xml", "text": "<root><a>1</a></root>"}
    body, bt = _parse_body(post)
    assert bt == BodyType.XML
    assert "<root>" in body


# ─────────────────────────────────────────────────────────
# _parse_response_body
# ─────────────────────────────────────────────────────────

def test_parse_response_body_json():
    content = {"mimeType": "application/json", "text": '{"code":0}'}
    result = _parse_response_body(content)
    assert result == {"code": 0}


def test_parse_response_body_text():
    content = {"mimeType": "text/plain", "text": "hello world"}
    result = _parse_response_body(content)
    assert result == "hello world"


def test_parse_response_body_empty():
    result = _parse_response_body({"mimeType": "application/json", "text": ""})
    assert result is None


def test_parse_response_body_base64():
    raw = json.dumps({"key": "value"})
    encoded = base64.b64encode(raw.encode()).decode()
    content = {"mimeType": "application/json", "text": encoded, "encoding": "base64"}
    result = _parse_response_body(content)
    assert result == {"key": "value"}


def test_parse_response_body_base64_binary():
    content = {"mimeType": "image/png", "text": "iVBORw0KGgo=", "encoding": "base64"}
    result = _parse_response_body(content)
    assert isinstance(result, str)  # 解码后文本（含乱码）或 [binary response]


# ─────────────────────────────────────────────────────────
# _make_hash
# ─────────────────────────────────────────────────────────

def test_make_hash_deterministic():
    h1 = _make_hash("POST", "https://api.example.com/login?t=1", {"u": "a"})
    h2 = _make_hash("POST", "https://api.example.com/login?t=2", {"u": "a"})
    # query string 被剥离，两者相同
    assert h1 == h2


def test_make_hash_different_method():
    h1 = _make_hash("GET", "https://api.example.com/users", None)
    h2 = _make_hash("POST", "https://api.example.com/users", None)
    assert h1 != h2


def test_make_hash_different_body():
    h1 = _make_hash("POST", "https://api.example.com/login", {"a": 1})
    h2 = _make_hash("POST", "https://api.example.com/login", {"a": 2})
    assert h1 != h2


def test_make_hash_length():
    h = _make_hash("GET", "https://example.com/api", None)
    assert len(h) == 16


# ─────────────────────────────────────────────────────────
# _entry_to_dsl
# ─────────────────────────────────────────────────────────

SAMPLE_POST_ENTRY = {
    "request": {
        "method": "POST",
        "url": "https://api.example.com/v1/users/login?source=web",
        "headers": [
            {"name": "Content-Type", "value": "application/json"},
            {"name": ":method", "value": "POST"},       # 伪头部
            {"name": ":authority", "value": "example.com"},  # 伪头部
        ],
        "postData": {
            "mimeType": "application/json",
            "text": '{"username": "alice", "password": "secret"}',
        },
        "queryString": [],
    },
    "response": {
        "status": 200,
        "headers": [{"name": "Content-Type", "value": "application/json"}],
        "content": {
            "mimeType": "application/json",
            "text": '{"code": 0, "data": {"token": "jwt_abc"}}',
        },
    },
    "time": 145,
}

SAMPLE_GET_ENTRY = {
    "request": {
        "method": "GET",
        "url": "https://api.example.com/v1/products?page=1&size=20",
        "headers": [],
        "queryString": [
            {"name": "page", "value": "1"},
            {"name": "size", "value": "20"},
        ],
    },
    "response": {
        "status": 200,
        "headers": [],
        "content": {"mimeType": "application/json", "text": '{"items":[],"total":0}'},
    },
    "time": 60,
}


def test_entry_to_dsl_post_method_and_path():
    dsl = _entry_to_dsl(SAMPLE_POST_ENTRY, "test.har", "proj1")
    assert dsl.request.method.value == "POST"
    assert dsl.request.path == "/v1/users/login"


def test_entry_to_dsl_pseudo_headers_filtered():
    dsl = _entry_to_dsl(SAMPLE_POST_ENTRY, "test.har", "proj1")
    assert ":method" not in dsl.request.headers
    assert ":authority" not in dsl.request.headers
    assert "Content-Type" in dsl.request.headers


def test_entry_to_dsl_body_parsed():
    dsl = _entry_to_dsl(SAMPLE_POST_ENTRY, "test.har", "proj1")
    assert dsl.request.body == {"username": "alice", "password": "secret"}
    assert dsl.request.body_type == BodyType.JSON


def test_entry_to_dsl_response():
    dsl = _entry_to_dsl(SAMPLE_POST_ENTRY, "test.har", "proj1")
    assert dsl.response.status_code == 200
    assert dsl.response.body["data"]["token"] == "jwt_abc"
    assert dsl.response.latency_ms == 145


def test_entry_to_dsl_source_fields():
    dsl = _entry_to_dsl(SAMPLE_POST_ENTRY, "archive.har", "my_project")
    assert dsl.source_har == "archive.har"
    assert dsl.project_id == "my_project"
    assert len(dsl.source_hash) == 16
    assert dsl.id  # UUID 非空


def test_entry_to_dsl_get_query_params():
    dsl = _entry_to_dsl(SAMPLE_GET_ENTRY, "test.har", "proj1")
    assert dsl.request.query_params.get("page") == "1"
    assert dsl.request.query_params.get("size") == "20"


def test_entry_to_dsl_name_fallback():
    entry = {
        "request": {
            "method": "GET", "url": "https://api.example.com/", "headers": [],
            "queryString": [],
        },
        "response": {"status": 200, "headers": [], "content": {"mimeType": "application/json", "text": "{}"}},
        "time": 10,
    }
    dsl = _entry_to_dsl(entry, "t.har", "p1")
    assert dsl.name  # 非空


# ─────────────────────────────────────────────────────────
# HarParserService.parse_and_save 集成测试
# ─────────────────────────────────────────────────────────

SAMPLE_HAR = {
    "log": {
        "entries": [
            SAMPLE_POST_ENTRY,
            SAMPLE_GET_ENTRY,
        ]
    }
}

STATIC_ONLY_HAR = {
    "log": {
        "entries": [
            _entry_with_url("https://cdn.example.com/app.js", "application/javascript"),
            _entry_with_url("https://cdn.example.com/logo.png", "image/png"),
        ]
    }
}

MIXED_HAR = {
    "log": {
        "entries": [
            SAMPLE_POST_ENTRY,
            _entry_with_url("https://cdn.example.com/style.css", "text/css"),  # static → skip
        ]
    }
}


def _make_service(existing_hashes: list[str] | None = None):
    """构造 HarParserService，mock MongoDB + Redis + QuarantineStore"""
    db = MagicMock()
    col = AsyncMock()
    col.distinct = AsyncMock(return_value=existing_hashes or [])
    col.insert_many = AsyncMock(
        return_value=MagicMock(inserted_ids=["id_placeholder"] * 99)
    )
    db.__getitem__ = MagicMock(return_value=col)

    # pipeline 支持 async with 语法
    pipeline = AsyncMock()
    pipeline.rpush = AsyncMock()
    pipeline.execute = AsyncMock(return_value=[1])
    pipeline.__aenter__ = AsyncMock(return_value=pipeline)
    pipeline.__aexit__ = AsyncMock(return_value=False)

    redis = AsyncMock()
    redis.pipeline = MagicMock(return_value=pipeline)

    quarantine = AsyncMock()
    quarantine.save = AsyncMock(return_value="minio://har-quarantine/file.har")

    svc = HarParserService(db, redis, quarantine, project_id="test_proj")
    return svc, col, redis, quarantine, pipeline


@pytest.mark.asyncio
async def test_parse_success_two_entries():
    svc, col, redis, quarantine, pipe = _make_service()
    result = await svc.parse_and_save("test.har", json.dumps(SAMPLE_HAR).encode())

    assert result["status"] == "success"
    assert result["total_entries"] == 2
    assert result["skipped_static"] == 0
    assert result["new_apis"] == 2
    assert result["skipped_duplicates"] == 0
    assert result["failed_entries"] == 0
    assert len(result["api_ids"]) == 2

    col.insert_many.assert_awaited_once()
    pipe.rpush.assert_awaited()


@pytest.mark.asyncio
async def test_parse_filters_static_resources():
    svc, col, redis, quarantine, pipe = _make_service()
    result = await svc.parse_and_save("mixed.har", json.dumps(MIXED_HAR).encode())

    assert result["status"] == "success"
    assert result["skipped_static"] == 1
    assert result["new_apis"] == 1


@pytest.mark.asyncio
async def test_parse_all_static_fails():
    svc, col, redis, quarantine, pipe = _make_service()
    result = await svc.parse_and_save("static.har", json.dumps(STATIC_ONLY_HAR).encode())

    assert result["status"] == "failed"
    assert "no valid api entries" in result["reason"]
    quarantine.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_parse_invalid_json():
    svc, col, _, quarantine, _ = _make_service()
    result = await svc.parse_and_save("bad.har", b"not json at all {{{")
    assert result["status"] == "failed"
    quarantine.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_parse_empty_entries():
    svc, col, _, quarantine, _ = _make_service()
    har = {"log": {"entries": []}}
    result = await svc.parse_and_save("empty.har", json.dumps(har).encode())
    assert result["status"] == "failed"
    assert result["reason"] == "no entries"


@pytest.mark.asyncio
async def test_parse_dedup_skips_existing():
    # 假设 POST /v1/users/login 的 hash 已存在
    svc, col, _, _, _ = _make_service()

    # 先解析一次获取真实 hash
    from har_parser.parser import _entry_to_dsl
    real_dsl = _entry_to_dsl(SAMPLE_POST_ENTRY, "t.har", "p")
    existing_hash = real_dsl.source_hash

    col.distinct = AsyncMock(return_value=[existing_hash])
    col.insert_many = AsyncMock(return_value=MagicMock(inserted_ids=["id2"]))

    result = await svc.parse_and_save("test.har", json.dumps(SAMPLE_HAR).encode())
    assert result["status"] == "success"
    assert result["skipped_duplicates"] == 1
    assert result["new_apis"] == 1


@pytest.mark.asyncio
async def test_parse_all_duplicate_no_insert():
    svc, col, redis, _, _ = _make_service()

    from har_parser.parser import _entry_to_dsl
    h1 = _entry_to_dsl(SAMPLE_POST_ENTRY, "t.har", "p").source_hash
    h2 = _entry_to_dsl(SAMPLE_GET_ENTRY, "t.har", "p").source_hash
    col.distinct = AsyncMock(return_value=[h1, h2])

    result = await svc.parse_and_save("test.har", json.dumps(SAMPLE_HAR).encode())
    assert result["status"] == "success"
    assert result["new_apis"] == 0
    assert result["skipped_duplicates"] == 2
    col.insert_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_parse_project_id_injected():
    svc, col, _, _, _ = _make_service()
    await svc.parse_and_save("test.har", json.dumps(SAMPLE_HAR).encode())

    insert_call = col.insert_many.call_args[0][0]
    for doc in insert_call:
        assert doc["project_id"] == "test_proj"


@pytest.mark.asyncio
async def test_pipeline_used_for_queue_push():
    """AI 队列推送使用 Redis pipeline，不是逐条 rpush"""
    svc, col, redis, _, pipe = _make_service()
    await svc.parse_and_save("test.har", json.dumps(SAMPLE_HAR).encode())
    # pipeline.execute 应被调用
    pipe.execute.assert_awaited()
