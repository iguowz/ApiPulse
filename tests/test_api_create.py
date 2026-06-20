"""
P1-5b: API 手动新建 + import_curl 集合名修复测试

验证：
1. ApiService.create_api 正确写入 api_dsls 集合，生成 id/时间戳/source_har=manual
2. create_api 支持精简输入（method/url/path）和完整 ApiDSL
3. POST /apis 路由参数校验（无 url → 400，非法 method → 400）
4. import_curl 集合名修复：写入 api_dsls（此前错误写入 apis 集合导致查不到）
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

from services.api_service import ApiService
from models.dsl import ApiDSL, HttpMethod, RequestDSL, ResponseDSL


class _FakeCursor:
    """测试用 Mongo cursor：只实现本文件需要的 sort/to_list 链式调用。"""

    def __init__(self, docs):
        self.docs = docs

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, length=None):
        return self.docs[:length] if length else self.docs


def _make_db_service():
    """构造 ApiService + mock db，api_dsls 集合用 AsyncMock 捕获 insert_one。"""
    db = MagicMock()
    api_col = AsyncMock()
    api_col.insert_one = AsyncMock()
    api_col.find_one = AsyncMock(return_value=None)
    db.__getitem__ = MagicMock(side_effect=lambda k: {"api_dsls": api_col}[k])
    svc = ApiService(db)
    return svc, api_col


def _sample_openapi_spec():
    """构造最小 OpenAPI 3 spec，覆盖 query/header/body/response 导入。"""
    return {
        "openapi": "3.0.3",
        "info": {"title": "Test API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/users": {
                "get": {
                    "summary": "List users",
                    "tags": ["user"],
                    "parameters": [
                        {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}},
                        {"name": "X-Trace", "in": "header", "schema": {"type": "string"}, "example": "trace-1"},
                    ],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"items": {"type": "array", "items": {"type": "object"}}},
                                    },
                                    "example": {"items": []},
                                },
                            },
                        },
                    },
                },
                "post": {
                    "summary": "Create user",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string", "example": "Ada"}},
                                },
                            },
                        },
                    },
                    "responses": {"201": {"description": "Created"}},
                },
            },
        },
    }


@pytest.mark.asyncio
async def test_create_api_generates_id_and_writes_to_api_dsls():
    """create_api 应生成 uuid id，写入 api_dsls 集合（非 apis）。"""
    svc, api_col = _make_db_service()
    api = ApiDSL(
        name="测试接口",
        request=RequestDSL(method=HttpMethod.GET, url="http://x.test/a", path="/a"),
        response=ResponseDSL(status_code=200),
        project_id="proj-1",
    )
    result = await svc.create_api(api)
    # 应写入 api_dsls 集合
    api_col.insert_one.assert_awaited_once()
    # 应生成 uuid 格式的 id
    assert result["id"], "应自动生成 id"
    assert len(result["id"]) == 36, "id 应是 uuid 格式"
    # source_har 应标记为 manual
    assert result["source_har"] == "manual"
    assert result["project_id"] == "proj-1"
    assert result["name"] == "测试接口"


@pytest.mark.asyncio
async def test_create_api_preserves_existing_id():
    """若传入 id，create_api 应保留而非覆盖。"""
    svc, _ = _make_db_service()
    api = ApiDSL(
        id="custom-id-123",
        name="t",
        request=RequestDSL(method=HttpMethod.POST, url="http://x/b"),
        response=ResponseDSL(status_code=0),
    )
    result = await svc.create_api(api)
    assert result["id"] == "custom-id-123"


@pytest.mark.asyncio
async def test_create_api_sets_timestamps():
    """create_api 应设置 created_at 和 updated_at。"""
    svc, _ = _make_db_service()
    api = ApiDSL(
        name="t",
        request=RequestDSL(method=HttpMethod.GET, url="http://x/c"),
        response=ResponseDSL(status_code=0),
    )
    before = datetime.now()
    result = await svc.create_api(api)
    after = datetime.now()
    # 时间戳应在调用前后区间内
    assert result["created_at"] >= before.replace(microsecond=0)
    assert result["updated_at"] <= after


@pytest.mark.asyncio
async def test_import_curl_writes_to_api_dsls_not_apis():
    """P1-5b 修复验证：import_curl 应写入 api_dsls 集合（此前错误写入 apis）。
    此前 bug：apis.py:359 写 db["apis"]，但 list_apis 查 api_dsls，导致 cURL 导入的接口查不到。
    修复后应写入 api_dsls。"""
    # 直接验证路由源码不再使用 db["apis"]
    import inspect
    from api.routers import apis as apis_module
    src = inspect.getsource(apis_module.import_curl)
    # 修复后应包含 db["api_dsls"]，不应有 db["apis"].insert_one
    assert 'db["api_dsls"]' in src, "import_curl 应写入 api_dsls 集合"
    assert 'db["apis"].insert_one' not in src, "import_curl 不应再写入 apis 集合（已修复）"


def test_post_apis_route_registered_before_dynamic():
    """POST /apis 静态路由应注册在 /apis/{api_id} 动态路由之前，避免被捕获。"""
    from api.routers.apis import router
    post_paths = [r.path for r in router.routes
                  if "POST" in getattr(r, "methods", set())]
    # /apis 应在 /apis/{api_id}/run 等动态路由之前出现
    idx_apis = post_paths.index("/apis")
    idx_dynamic = next((i for i, p in enumerate(post_paths) if "{api_id}" in p), len(post_paths))
    assert idx_apis < idx_dynamic, "静态 POST /apis 应在动态路由前注册"
    assert "/apis" in post_paths, "POST /apis 端点应存在"


def test_openapi_routes_registered_before_dynamic():
    """OpenAPI 导入/导出静态路由应注册在 /apis/{api_id} 动态路由之前。"""
    from api.routers.apis import router
    post_paths = [r.path for r in router.routes if "POST" in getattr(r, "methods", set())]
    idx_dynamic = next((i for i, p in enumerate(post_paths) if "{api_id}" in p), len(post_paths))
    assert post_paths.index("/apis/import-openapi") < idx_dynamic
    assert post_paths.index("/apis/export-openapi") < idx_dynamic


def test_generation_field_diffs_use_partial_accept_selectors():
    """审核 diff 应返回可直接用于 accept-partial 的字段选择器。"""
    from api.routers.generations import _build_generation_field_diffs

    doc_diffs = _build_generation_field_diffs(
        {
            "summary": "old",
            "params": [{"name": "page", "description": "old"}],
            "response_fields": [{"name": "data.id", "type": "string"}],
        },
        {
            "summary": "new",
            "params": [{"name": "page", "description": "new"}, {"name": "size", "type": "integer"}],
            "response_fields": [{"name": "data.id", "type": "string"}],
        },
        "doc",
    )
    by_key = {item["key"]: item for item in doc_diffs}
    assert by_key["summary"]["status"] == "modified"
    assert by_key["params:page"]["status"] == "modified"
    assert by_key["params:size"]["status"] == "added"
    assert by_key["response_fields:data.id"]["status"] == "unchanged"
    assert by_key["response_fields:data.id"]["selectable"] is False

    assert_diffs = _build_generation_field_diffs(
        {"asserts": [{"field": "status_code", "expected": 200}]},
        {"asserts": [{"field": "status_code", "expected": 201}, {"field": "$.code", "expected": 0}]},
        "asserts",
    )
    assert {item["key"] for item in assert_diffs} == {"status_code", "$.code"}

    tmpl_diffs = _build_generation_field_diffs(
        {"fields": [{"name": "email", "faker_method": "word"}]},
        {"fields": [{"name": "email", "faker_method": "email"}]},
        "data_template",
    )
    assert tmpl_diffs[0]["key"] == "email"
    assert tmpl_diffs[0]["status"] == "modified"

    monitor_diffs = _build_generation_field_diffs(
        {"monitors": [{"target_type": "api", "target_id": "api1", "interval": "5m"}]},
        {"monitors": [{"target_type": "api", "target_id": "api1", "interval": "1m"}]},
        "monitor",
    )
    assert monitor_diffs[0]["key"] == "api1"
    assert monitor_diffs[0]["status"] == "modified"


def test_post_apis_validates_required_fields():
    """POST /apis 应校验 url 必填（无 url → 400）。"""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from api.routers.apis import get_current_user, router
    app = FastAPI()
    app.include_router(router)
    # 路由单测关注字段校验，认证依赖固定为 admin，避免未登录 401 抢先返回。
    app.dependency_overrides[get_current_user] = lambda: {"role": "admin", "project_id": "default", "username": "tester"}
    client = TestClient(app)
    # 无 url → 400
    r = client.post("/apis", json={"name": "test", "method": "GET"})
    assert r.status_code == 400, f"无 url 应返回 400，实际 {r.status_code}"
    # 非法 method → 400
    r2 = client.post("/apis", json={"name": "test", "method": "INVALID", "url": "http://x"})
    assert r2.status_code == 400, f"非法 method 应返回 400，实际 {r2.status_code}"


@pytest.mark.asyncio
async def test_import_openapi_converts_operations_and_deduplicates():
    """OpenAPI 导入应转换 operation 为 ApiDSL，并按 source_hash 去重。"""
    db = MagicMock()
    api_col = MagicMock()
    api_col.distinct = AsyncMock(return_value=[])
    api_col.insert_many = AsyncMock(return_value=MagicMock(inserted_ids=["m1", "m2"]))
    db.__getitem__ = MagicMock(side_effect=lambda k: {"api_dsls": api_col}[k])
    svc = ApiService(db)

    result = await svc.import_openapi(_sample_openapi_spec(), "proj-1", "openapi.json")

    assert result["new_apis"] == 2
    assert result["skipped_duplicates"] == 0
    api_col.insert_many.assert_awaited_once()
    inserted_docs = api_col.insert_many.await_args.args[0]
    assert {d["request"]["method"] for d in inserted_docs} == {"GET", "POST"}
    get_doc = next(d for d in inserted_docs if d["request"]["method"] == "GET")
    assert get_doc["request"]["url"] == "https://api.example.com/users"
    assert get_doc["request"]["query_params"]["page"] == 1
    assert get_doc["request"]["headers"]["X-Trace"] == "trace-1"
    assert get_doc["response"]["body"] == {"items": []}
    post_doc = next(d for d in inserted_docs if d["request"]["method"] == "POST")
    assert post_doc["request"]["body"] == {"name": "Ada"}
    assert post_doc["request"]["body_type"] == "json"


@pytest.mark.asyncio
async def test_import_openapi_rejects_invalid_spec():
    """缺少 paths 的 JSON 不能作为 OpenAPI 导入。"""
    svc, _ = _make_db_service()
    with pytest.raises(ValueError, match="paths"):
        await svc.import_openapi({"openapi": "3.0.3"}, "proj-1")


@pytest.mark.asyncio
async def test_export_openapi_filters_by_project_and_builds_paths():
    """OpenAPI 导出应只导出当前项目匹配的 API，并生成 paths/operation。"""
    api = ApiDSL(
        id="api-1",
        name="List users",
        request=RequestDSL(method=HttpMethod.GET, url="https://api.example.com/users", path="/users", query_params={"page": 1}),
        response=ResponseDSL(status_code=200, body={"items": []}),
        project_id="proj-1",
        tags=["user"],
    )
    db = MagicMock()
    api_col = MagicMock()
    api_col.find = MagicMock(return_value=_FakeCursor([api.model_dump()]))
    db.__getitem__ = MagicMock(side_effect=lambda k: {"api_dsls": api_col}[k])
    svc = ApiService(db)

    result = await svc.export_openapi(["api-1", "api-other"], "proj-1")

    api_col.find.assert_called_once()
    query = api_col.find.call_args.args[0]
    assert query == {"id": {"$in": ["api-1", "api-other"]}, "project_id": "proj-1"}
    spec = result["openapi"]
    assert spec["openapi"] == "3.0.3"
    assert "/users" in spec["paths"]
    operation = spec["paths"]["/users"]["get"]
    assert operation["summary"] == "List users"
    assert operation["parameters"][0]["name"] == "page"
    assert operation["responses"]["200"]["content"]["application/json"]["example"] == {"items": []}
