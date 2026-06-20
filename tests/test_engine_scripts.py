"""
P0-1: 场景引擎脚本/auth/assertions 生效测试

验证此前「装饰性字段」pre_script/post_script/auth/assertions 现在被引擎真正消费：
1. _build_auth_headers / _build_auth_query: bearer/basic/apikey 三类认证正确生成
2. _eval_safe_expr: AST 白名单沙箱阻断危险调用（import/open/dunder）
3. _run_step_script: 声明式 JSON 脚本正确提取变量；纯表达式兼容
4. _eval_single_assert: 步骤断言单条求值与 run_asserts 语义一致
5. _http_call 集成: auth 头注入 + step.assertions 求值（mock httpx）
"""
from __future__ import annotations

import base64
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from models.dsl import (
    ApiDSL, AssertRule, HttpMethod, RequestDSL, ResponseDSL,
    ScenarioStep,
)
from dag_engine.engine import (
    _build_auth_headers, _build_auth_query,
    _eval_safe_expr, _run_step_script, _eval_single_assert,
    DagExecutionEngine, _ExecEnv,
)


# ── auth headers 生成 ──────────────────────────────────────

def test_auth_bearer_generates_authorization_header():
    """bearer 类型应生成 Authorization: Bearer {token}。"""
    headers = _build_auth_headers({"type": "bearer", "token": "abc123"})
    assert headers == {"Authorization": "Bearer abc123"}


def test_auth_bearer_empty_token_returns_empty():
    """空 token 不应生成头，避免发送 "Bearer " 空值被服务端拒绝。"""
    assert _build_auth_headers({"type": "bearer", "token": ""}) == {}


def test_auth_basic_generates_base64_credentials():
    """basic 类型应生成 Base64 编码的 user:pass。"""
    headers = _build_auth_headers({"type": "basic", "username": "admin", "password": "secret"})
    expected_creds = base64.b64encode(b"admin:secret").decode()
    assert headers == {"Authorization": f"Basic {expected_creds}"}


def test_auth_apikey_header_form():
    """apikey header 形态应在 headers 中注入 {key: value}。"""
    headers = _build_auth_headers({
        "type": "apikey", "key": "X-API-Key", "value": "k123", "in": "header"
    })
    assert headers == {"X-API-Key": "k123"}


def test_auth_apikey_query_form():
    """apikey query 形态应通过 _build_auth_query 注入到 params，而非 headers。"""
    headers = _build_auth_headers({
        "type": "apikey", "key": "token", "value": "v1", "in": "query"
    })
    query = _build_auth_query({
        "type": "apikey", "key": "token", "value": "v1", "in": "query"
    })
    assert headers == {}, "query 形态不应生成 header"
    assert query == {"token": "v1"}, "query 形态应生成 params"


def test_auth_empty_or_unknown_type_returns_empty():
    """空 auth 或未知类型不应生成任何 header。"""
    assert _build_auth_headers({}) == {}
    assert _build_auth_headers({"type": ""}) == {}
    assert _build_auth_headers({"type": "oauth"}) == {}


# ── safe expr 沙箱安全性 ────────────────────────────────────

def test_safe_expr_allows_arithmetic_and_builtins():
    """允许的算术与白名单内置函数应正常求值。"""
    assert _eval_safe_expr("1 + 2", {}) == 3
    assert _eval_safe_expr("len([1,2,3])", {}) == 3
    assert _eval_safe_expr("max(1, 2, 3)", {}) == 3
    assert _eval_safe_expr("str(123)", {}) == "123"


def test_safe_expr_allows_context_variable_access():
    """允许访问 context 中的变量。"""
    ctx = {"x": 10, "name": "test"}
    assert _eval_safe_expr("x * 2", ctx) == 20
    assert _eval_safe_expr("name + '!'", ctx) == "test!"


def test_safe_expr_blocks_import():
    """__import__ 调用必须被阻断（防任意模块加载）。
    安全语义：_eval_safe_expr 内部捕获 ValueError 转为返回 None（脚本错误不崩溃主流程），
    故此处验证返回 None 而非 raises——返回 None 即代表表达式被沙箱拒绝执行。"""
    # __import__ 不在白名单 → ValueError → 返回 None
    result = _eval_safe_expr("__import__('os')", {})
    assert result is None, "__import__ 应被沙箱拒绝，返回 None"
    # 带 .system 属性访问的组合 → Attribute 拦截 → 返回 None
    result = _eval_safe_expr("__import__('os').system('id')", {})
    assert result is None, "属性访问应被沙箱拒绝"


def test_safe_expr_blocks_dunder_attribute_access():
    """__dunder__ 属性访问必须被阻断（防 __builtins__ 逃逸）。
    沙箱拒绝时 _eval_safe_expr 返回 None（非崩溃），验证此安全语义。"""
    # 通过属性访问 __class__ 等逃逸路径 → Attribute 拦截 → 返回 None
    result = _eval_safe_expr("().__class__", {})
    assert result is None, "属性访问应被沙箱拒绝，返回 None"


def test_safe_expr_blocks_open_function():
    """open 等危险函数不在白名单 → NameError → 返回 None 不崩溃。"""
    result = _eval_safe_expr("open('/etc/passwd')", {})
    assert result is None, "非白名单函数应求值失败返回 None"


# ── 声明式脚本执行 ──────────────────────────────────────────

def test_step_script_declarative_set_from_response():
    """声明式 JSON: from=response 按 jsonpath 提取响应字段。"""
    script = [{"op": "set", "var": "token", "from": "response", "path": "$.data.token"}]
    result = _run_step_script(script, context={}, response={"data": {"token": "abc"}})
    assert result == {"token": "abc"}


def test_step_script_templates_timestamp_random_and_hash():
    """声明式脚本模板动作应能生成 timestamp/nonce/signature。"""
    script = [
        {"op": "timestamp", "var": "timestamp", "unit": "ms"},
        {"op": "random", "var": "nonce", "kind": "string", "length": 8},
        {"op": "hash", "var": "signature", "value": "{{timestamp}}:{{nonce}}", "algorithm": "sha256", "secret": "{{secret}}"},
    ]
    result = _run_step_script(script, context={"secret": "s1"})
    assert isinstance(result["timestamp"], int)
    assert len(result["nonce"]) == 8
    assert len(result["signature"]) == 64


def test_step_script_declarative_set_literal():
    """声明式 JSON: from=literal 直接取 value。"""
    script = [{"op": "set", "var": "count", "from": "literal", "value": 5}]
    result = _run_step_script(script, context={})
    assert result == {"count": 5}


def test_step_script_declarative_set_from_context():
    """声明式 JSON: from=context 提取已有上下文变量。"""
    script = [{"op": "set", "var": "copied", "from": "context", "path": "$.source"}]
    result = _run_step_script(script, context={"source": "hello"})
    assert result == {"copied": "hello"}


def test_step_script_pure_expr_string():
    """纯表达式字符串兼容形态：结果存入 __expr__。"""
    result = _run_step_script("1 + 2 + 3", context={})
    assert result == {"__expr__": 6}


def test_step_script_empty_returns_empty():
    """空脚本应返回空 dict，不报错。"""
    assert _run_step_script("", {}) == {}
    assert _run_step_script([], {}) == {}
    assert _run_step_script(None, {}) == {}


def test_step_script_invalid_item_does_not_crash():
    """非法脚本项应被跳过，不阻断主流程。"""
    result = _run_step_script([{"op": "set"}], context={})  # 缺 var
    assert result == {}
    result = _run_step_script("import os", context={})  # 危险表达式
    assert result == {"__expr__": None}


def test_step_script_pre_script_no_response_access():
    """pre_script 阶段 response=None，from=response 的项提取结果应为 None。"""
    script = [{"op": "set", "var": "x", "from": "response", "path": "$.a"}]
    result = _run_step_script(script, context={}, response=None)
    assert result == {"x": None}


# ── 步骤断言单条求值 ────────────────────────────────────────

def test_eval_single_assert_eq():
    assert _eval_single_assert("eq", 200, 200) is True
    assert _eval_single_assert("eq", 200, 404) is False


def test_eval_single_assert_string_ops():
    assert _eval_single_assert("contains", "hello world", "world") is True
    assert _eval_single_assert("starts_with", "hello", "he") is True
    assert _eval_single_assert("regex", "test@example.com", r"^\w+@") is True


def test_eval_single_assert_existence():
    assert _eval_single_assert("exists", "x", None) is True
    assert _eval_single_assert("not_exists", None, None) is True
    assert _eval_single_assert("empty", "", None) is True
    assert _eval_single_assert("not_empty", [1], None) is True


def test_eval_single_assert_type_match():
    assert _eval_single_assert("type_match", 42, "int") is True
    assert _eval_single_assert("type_match", "s", "str") is True
    assert _eval_single_assert("type_match", [1], "list") is True


def test_eval_single_assert_unknown_op_returns_false():
    """未知 operator 应保守判 False（避免漏检）。"""
    assert _eval_single_assert("unknown_op", 1, 1) is False


def test_eval_single_assert_type_mismatch_no_crash():
    """类型不兼容（str vs int 比较）应静默判 False，不抛异常。"""
    assert _eval_single_assert("gt", "abc", 123) is False


# ── _http_call 集成：auth 注入 + step.assertions ────────────

def _make_engine() -> DagExecutionEngine:
    """构造一个不依赖 DB 的引擎实例用于 _http_call 测试。"""
    return DagExecutionEngine.__new__(DagExecutionEngine)


def _make_api() -> ApiDSL:
    return ApiDSL(
        id="a1", name="test",
        request=RequestDSL(method=HttpMethod.GET, url="http://x.test/endpoint"),
        response=ResponseDSL(status_code=200),
        asserts=[],
    )


def _make_step(auth=None, assertions=None, pre_script="", post_script="") -> ScenarioStep:
    return ScenarioStep(
        step_id="s1", api_id="a1", name="s1",
        auth=auth or {}, assertions=assertions or [],
        pre_script=pre_script, post_script=post_script,
    )


def _mock_response(status=200, body=None, headers=None):
    """构造 httpx.Response mock。"""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = body if body is not None else {"code": 0, "data": {"id": 42}}
    resp.headers = headers or {"content-type": "application/json"}
    return resp


@pytest.mark.asyncio
async def test_http_call_injects_bearer_auth_header():
    """_http_call 应把 step.auth bearer 注入到请求 headers（且不覆盖 env.headers）。"""
    engine = _make_engine()
    api = _make_api()
    step = _make_step(auth={"type": "bearer", "token": "tok-xyz"})
    env = _ExecEnv()

    captured = {}

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, **kw):
            captured["headers"] = kw.get("headers", {})
            return _mock_response()

    with patch("dag_engine.engine.httpx.AsyncClient", FakeClient):
        result = await engine._http_call(step, api, {}, env)

    # auth header 已注入
    assert captured["headers"].get("Authorization") == "Bearer tok-xyz"
    assert result.passed is True


@pytest.mark.asyncio
async def test_http_call_env_headers_override_auth():
    """env.headers 中的 Authorization 应优先于 step.auth（环境凭据优先）。"""
    engine = _make_engine()
    api = _make_api()
    step = _make_step(auth={"type": "bearer", "token": "step-token"})
    env = _ExecEnv(headers={"Authorization": "Bearer env-token"})

    captured = {}

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, **kw):
            captured["headers"] = kw.get("headers", {})
            return _mock_response()

    with patch("dag_engine.engine.httpx.AsyncClient", FakeClient):
        await engine._http_call(step, api, {}, env)

    # env 凭据应保留，不被 step.auth 覆盖
    assert captured["headers"].get("Authorization") == "Bearer env-token"


@pytest.mark.asyncio
async def test_http_call_apikey_query_injected_to_params():
    """apikey query 形态应注入到 params，而非 headers。"""
    engine = _make_engine()
    api = _make_api()
    step = _make_step(auth={"type": "apikey", "key": "ak", "value": "av", "in": "query"})
    env = _ExecEnv()

    captured = {}

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, **kw):
            captured["params"] = kw.get("params") or {}
            return _mock_response()

    with patch("dag_engine.engine.httpx.AsyncClient", FakeClient):
        await engine._http_call(step, api, {}, env)

    assert captured["params"].get("ak") == "av"


@pytest.mark.asyncio
async def test_http_call_step_assertions_evaluated():
    """step.assertions 应被求值，失败时标记 step 失败。"""
    engine = _make_engine()
    api = _make_api()
    # 步骤断言：期望 data.id == 99，但响应是 42 → 应失败
    step = _make_step(assertions=[
        {"source": "response", "path": "$.data.id", "operator": "eq", "expected": 99}
    ])
    env = _ExecEnv()

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, **kw):
            return _mock_response(body={"code": 0, "data": {"id": 42}})

    with patch("dag_engine.engine.httpx.AsyncClient", FakeClient):
        result = await engine._http_call(step, api, {}, env)

    # 步骤断言失败 → step 整体失败
    assert result.passed is False
    # 步骤断言结果应出现在 assert_results 中，带 source=step 标记
    step_asserts = [a for a in result.assert_results if a.get("source") == "step"]
    assert len(step_asserts) == 1
    assert step_asserts[0]["passed"] is False
    # 错误信息应包含步骤断言失败提示
    assert "step assert" in result.error


@pytest.mark.asyncio
async def test_http_call_step_response_time_assertion_uses_latency():
    """步骤断言的 response_time_lt 应使用本次请求 latency_ms，而不是从响应体取字段。"""
    engine = _make_engine()
    api = _make_api()
    step = _make_step(assertions=[
        {"source": "performance", "path": "$response_time_ms", "operator": "response_time_lt", "expected": 1000}
    ])

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, **kw):
            return _mock_response(body={"code": 0})

    with patch("dag_engine.engine.httpx.AsyncClient", FakeClient):
        result = await engine._http_call(step, api, {}, _ExecEnv())

    step_asserts = [a for a in result.assert_results if a.get("field") == "$response_time_ms"]
    assert step_asserts
    assert step_asserts[0]["passed"] is True
    assert isinstance(step_asserts[0]["actual"], int)


@pytest.mark.asyncio
async def test_http_call_post_script_extracts_response_var():
    """post_script 声明式脚本应从响应提取变量合并到 extracted_vars。
    注意：ScenarioStep.post_script 字段是 str 类型（见 models/dsl.py:301），
    声明式脚本以 JSON 字符串存储，_run_step_script 内部解析。"""
    import json
    engine = _make_engine()
    api = _make_api()
    step = _make_step(post_script=json.dumps([
        {"op": "set", "var": "user_id", "from": "response", "path": "$.data.id"}
    ]))
    env = _ExecEnv()

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, **kw):
            return _mock_response(body={"code": 0, "data": {"id": 42}})

    with patch("dag_engine.engine.httpx.AsyncClient", FakeClient):
        result = await engine._http_call(step, api, {}, env)

    # post_script 提取的变量应出现在 extracted_vars
    assert result.extracted_vars.get("user_id") == 42
    assert any(item.get("phase") == "post" and item.get("var") == "user_id" for item in result.script_results)


@pytest.mark.asyncio
async def test_http_call_pre_script_vars_render_current_request():
    """pre_script 产出的变量必须参与本次请求渲染，而不是只能给后续步骤使用。"""
    import json
    engine = _make_engine()
    api = ApiDSL(
        id="a1", name="test",
        request=RequestDSL(
            method=HttpMethod.POST,
            url="http://x.test/{{route}}",
            query_params={"token": "{{token}}"},
            headers={"Authorization": "Bearer {{token}}"},
            body={"signature": "{{signature}}"},
            body_type="json",
        ),
        response=ResponseDSL(status_code=200),
        asserts=[],
    )
    step = _make_step(pre_script=json.dumps([
        {"op": "set", "var": "token", "from": "literal", "value": "tok-1"},
        {"op": "set", "var": "signature", "from": "literal", "value": "sig-1"},
        {"op": "set", "var": "route", "from": "literal", "value": "secure"},
    ]))
    captured = {}

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, **kw):
            captured.update({"url": url, "params": kw.get("params"), "headers": kw.get("headers"), "json": kw.get("json")})
            return _mock_response()

    with patch("dag_engine.engine.httpx.AsyncClient", FakeClient):
        result = await engine._http_call(step, api, {}, _ExecEnv())

    assert captured["url"] == "http://x.test/secure"
    assert captured["params"] == {"token": "tok-1"}
    assert captured["headers"]["Authorization"] == "Bearer tok-1"
    assert captured["json"] == {"signature": "sig-1"}
    assert result.request_sent["unresolved_refs"] == []
    assert {item["var"] for item in result.script_results if item.get("phase") == "pre"} >= {"token", "signature", "route"}


def test_preview_step_request_reports_unresolved_refs():
    """请求预览应复用真实渲染逻辑，并返回未解析变量列表。"""
    engine = _make_engine()
    api = ApiDSL(
        id="a1", name="test",
        request=RequestDSL(
            method=HttpMethod.GET,
            url="http://x.test/{{known}}/{{missing}}",
            query_params={"env": "{{env.BASE_URL}}"},
            headers={"X-Trace": "{{trace_id}}"},
        ),
        response=ResponseDSL(status_code=200),
        asserts=[],
    )
    step = _make_step()

    preview = engine.preview_step_request(
        step,
        api,
        context={"known": "ok"},
        env=_ExecEnv(variables={"BASE_URL": "dev"}),
    )

    assert preview["request"]["url"] == "http://x.test/ok/{{missing}}"
    assert preview["request"]["params"] == {"env": "dev"}
    assert preview["request"]["headers"]["X-Trace"] == "{{trace_id}}"
    assert set(preview["unresolved_refs"]) == {"missing", "trace_id"}
    assert preview["ok"] is False


def test_preview_step_request_renders_structured_params_and_path():
    """结构化 _query/_path/_body_params/_vars 应参与同一次请求预览和执行组装。"""
    engine = _make_engine()
    api = ApiDSL(
        id="a1", name="test",
        request=RequestDSL(
            method=HttpMethod.POST,
            url="http://x.test/users/{user_id}/orders/:order_id",
            query_params={"page": 1},
            body={"amount": 0, "currency": "USD"},
            body_type="json",
        ),
        response=ResponseDSL(status_code=200),
        asserts=[],
    )
    step = _make_step()
    step.override_params = {
        "_query": {"page": "{{page_no}}", "keyword": "shoe"},
        "_path": {"user_id": "{{uid}}", "order_id": "A 100"},
        "_body_params": {"amount": "{{total}}"},
        "_vars": {"page_no": 2, "uid": "u-1", "total": 99},
    }

    preview = engine.preview_step_request(step, api)

    assert preview["request"]["url"] == "http://x.test/users/u-1/orders/A%20100"
    assert preview["request"]["params"] == {"page": 2, "keyword": "shoe"}
    assert preview["request"]["body"] == {"amount": 99, "currency": "USD"}
    assert preview["unresolved_refs"] == []
    assert preview["ok"] is True


def test_preview_step_request_reports_missing_structured_path_param():
    """缺失 Path 参数时保持 URL 占位符，并把 path.<name> 放入未解析列表。"""
    engine = _make_engine()
    api = ApiDSL(
        id="a1", name="test",
        request=RequestDSL(method=HttpMethod.GET, url="http://x.test/users/{user_id}"),
        response=ResponseDSL(status_code=200),
        asserts=[],
    )
    step = _make_step()

    preview = engine.preview_step_request(step, api)

    assert preview["request"]["url"] == "http://x.test/users/{user_id}"
    assert "path.user_id" in preview["unresolved_refs"]
    assert preview["ok"] is False


def test_preview_step_request_renders_auth_templates():
    """auth token/value 支持引用 env 和上下文变量，预览与真实执行共用同一渲染路径。"""
    engine = _make_engine()
    api = ApiDSL(
        id="a1", name="test",
        request=RequestDSL(method=HttpMethod.GET, url="http://x.test/secure"),
        response=ResponseDSL(status_code=200),
        asserts=[],
    )
    step = _make_step(auth={"type": "bearer", "token": "{{env.API_TOKEN}}"})

    preview = engine.preview_step_request(step, api, env=_ExecEnv(variables={"API_TOKEN": "tok-env"}))

    assert preview["request"]["headers"]["Authorization"] == "Bearer tok-env"
    assert preview["unresolved_refs"] == []


def test_preview_step_request_explains_auth_and_header_sources():
    """请求预览应展示 header/auth 来源，明确环境鉴权优先于步骤鉴权。"""
    engine = _make_engine()
    api = ApiDSL(
        id="a1", name="test",
        request=RequestDSL(
            method=HttpMethod.GET,
            url="http://x.test/secure",
            headers={"X-Trace": "api-trace", "X-Manual": "api-value"},
        ),
        response=ResponseDSL(status_code=200),
        asserts=[],
    )
    step = _make_step(auth={"type": "bearer", "token": "step-token"})
    step.override_headers = {"X-Manual": "manual-value"}

    preview = engine.preview_step_request(
        step,
        api,
        env=_ExecEnv(headers={"Authorization": "Bearer env-token"}),
    )

    headers = preview["request"]["headers"]
    assert headers["Authorization"] == "Bearer env-token"
    assert headers["X-Manual"] == "manual-value"
    assert preview["header_sources"]["authorization"] == "environment"
    assert preview["header_sources"]["x-trace"] == "api"
    assert preview["header_sources"]["x-manual"] == "manual"
    assert preview["auth_sources"]["skipped"][0]["reason"] == "environment_header_priority"


@pytest.mark.asyncio
async def test_dry_run_step_script_route_returns_context_after():
    """脚本 dry-run 路由复用同一执行器并返回输出变量与合并后的上下文。"""
    from api.routers.scenarios import dry_run_step_script
    result = await dry_run_step_script(
        body={
            "phase": "post",
            "context": {"user_id": 1},
            "response": {"data": {"token": "abc"}},
            "script": [{"op": "set", "var": "token", "from": "response", "path": "$.data.token"}],
        },
        current_user={"username": "u1", "role": "admin"},
    )
    assert result["ok"] is True
    assert result["output"] == {"token": "abc"}
    assert result["context_after"]["user_id"] == 1
    assert result["context_after"]["token"] == "abc"
