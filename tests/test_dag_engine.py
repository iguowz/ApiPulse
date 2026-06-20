"""
DAG 执行引擎测试 v4
新增：
- extract_jsonpath [*] 返回完整列表（修复：之前只返回首个）
- _sanitize 日志脱敏
- run_scenario 并发取消逻辑（通过 mock _dispatch_step）
- 具体化异常类型（httpx.TimeoutException / RequestError）
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from models.dsl import (
    ApiDSL, AssertRule, HttpMethod, RequestDSL,
    ResponseDSL, RiskLevel, ScenarioStep, StepCondition, StepResult,
)
from dag_engine.engine import (
    DagExecutionEngine, extract_jsonpath, run_asserts,
    _eval_condition, _sanitize,
)


# ── extract_jsonpath ──────────────────────────────────────

def test_jsonpath_simple():
    data = {"code": 0, "data": {"token": "abc"}}
    assert extract_jsonpath(data, "$.code") == 0
    assert extract_jsonpath(data, "$.data.token") == "abc"


def test_jsonpath_array_index():
    data = {"items": [{"id": 1}, {"id": 2}]}
    assert extract_jsonpath(data, "$.items[0].id") == 1
    assert extract_jsonpath(data, "$.items[1].id") == 2


def test_jsonpath_wildcard_returns_full_list():
    """[*] 修复：应返回完整列表，而非仅首个元素"""
    data = {"items": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}
    result = extract_jsonpath(data, "$.items[*].name")
    assert result == ["a", "b", "c"], f"期望完整列表，得到: {result}"


def test_jsonpath_wildcard_single_item():
    data = {"items": [{"id": 99}]}
    result = extract_jsonpath(data, "$.items[*].id")
    assert result == [99]


def test_jsonpath_wildcard_empty_list():
    data = {"items": []}
    result = extract_jsonpath(data, "$.items[*].id")
    assert result is None


def test_jsonpath_missing_key():
    assert extract_jsonpath({"a": 1}, "$.b.c") is None


def test_jsonpath_not_dollar():
    assert extract_jsonpath({}, "code") is None


def test_jsonpath_nested_array():
    data = {"results": [{"tags": ["x", "y"]}]}
    assert extract_jsonpath(data, "$.results[0].tags[1]") == "y"


def test_jsonpath_wildcard_nested():
    data = {"users": [{"addr": {"city": "BJ"}}, {"addr": {"city": "SH"}}]}
    result = extract_jsonpath(data, "$.users[*].addr.city")
    assert result == ["BJ", "SH"]


# ── _sanitize ─────────────────────────────────────────────

def test_sanitize_password():
    body = {"username": "alice", "password": "secret123"}
    result = _sanitize(body)
    assert result["password"] == "***"
    assert result["username"] == "alice"


def test_sanitize_token():
    body = {"token": "eyJhb...", "data": {"access_token": "xyz"}}
    result = _sanitize(body)
    assert result["token"] == "***"
    assert result["data"]["access_token"] == "***"


def test_sanitize_case_insensitive():
    body = {"PASSWORD": "secret", "Authorization": "Bearer x"}
    result = _sanitize(body)
    assert result["PASSWORD"] == "***"
    assert result["Authorization"] == "***"


def test_sanitize_preserves_non_sensitive():
    body = {"name": "alice", "age": 30, "email": "a@b.com"}
    result = _sanitize(body)
    assert result == {"name": "alice", "age": 30, "email": "a@b.com"}


def test_sanitize_list_truncation():
    big_list = list(range(20))
    result = _sanitize(big_list)
    assert len(result) == 10


def test_sanitize_depth_limit():
    deep = {"a": {"b": {"c": {"d": {"e": {"f": "deep"}}}}}}
    result = _sanitize(deep)
    # 超过4层应被截断为 "..."
    assert result["a"]["b"]["c"]["d"] == "..."


# ── run_asserts ───────────────────────────────────────────

def _make_api(asserts):
    return ApiDSL(
        id="t",
        request=RequestDSL(method=HttpMethod.GET, url="http://x.com"),
        response=ResponseDSL(status_code=200),
        asserts=asserts,
    )


@pytest.mark.parametrize("op,expected,actual,should_pass", [
    ("eq",           200,      200,         True),
    ("eq",           200,      404,         False),
    ("ne",           200,      404,         True),
    ("gt",           0,        1,           True),
    ("gte",          5,        5,           True),
    ("lt",           10,       5,           True),
    ("lte",          5,        5,           True),
    ("contains",     "ok",     "all ok",    True),
    ("not_contains", "err",    "success",   True),
    ("starts_with",  "hel",    "hello",     True),
    ("ends_with",    "ld",     "world",     True),
    ("exists",       None,     "val",       True),
    ("not_exists",   None,     None,        True),
    ("in",           [1,2,3],  2,           True),
    ("not_in",       [1,2,3],  5,           True),
    ("regex",        r"\d{3}", "abc123",    True),
    ("regex",        r"^\d+$", "abc",       False),
])
def test_assert_operator(op, expected, actual, should_pass):
    api = _make_api([AssertRule(field="status_code", operator=op, expected=expected)])
    _, passed, _ = run_asserts(api, actual, {})
    assert passed == should_pass


def test_no_asserts_2xx():
    _, passed, _ = run_asserts(_make_api([]), 200, {})
    assert passed is True


def test_no_asserts_5xx():
    _, passed, summary = run_asserts(_make_api([]), 500, {})
    assert passed is False
    assert "500" in summary


def test_jsonpath_assert():
    api = _make_api([AssertRule(field="$.data.code", operator="eq", expected=0)])
    _, passed, _ = run_asserts(api, 200, {"data": {"code": 0}})
    assert passed is True


def test_failure_summary_contains_field():
    api = _make_api([AssertRule(field="$.code", operator="eq", expected=0)])
    _, passed, summary = run_asserts(api, 200, {"code": 1})
    assert passed is False
    assert "$.code" in summary


# ── 拓扑排序 ─────────────────────────────────────────────

def ms(sid, deps=None):
    return ScenarioStep(step_id=sid, api_id=f"api_{sid}", depends_on=deps or [])


def test_topo_linear():
    groups = DagExecutionEngine._topo_sort([ms("A"), ms("B", ["A"]), ms("C", ["B"])])
    assert [g[0].step_id for g in groups] == ["A", "B", "C"]


def test_topo_parallel():
    groups = DagExecutionEngine._topo_sort([ms("A"), ms("B", ["A"]), ms("C", ["A"])])
    assert groups[0][0].step_id == "A"
    assert {s.step_id for s in groups[1]} == {"B", "C"}


def test_topo_diamond():
    steps = [ms("A"), ms("B", ["A"]), ms("C", ["A"]), ms("D", ["B", "C"])]
    groups = DagExecutionEngine._topo_sort(steps)
    assert groups[0][0].step_id == "A"
    assert {s.step_id for s in groups[1]} == {"B", "C"}
    assert groups[2][0].step_id == "D"


def test_topo_all_independent():
    groups = DagExecutionEngine._topo_sort([ms("X"), ms("Y"), ms("Z")])
    assert len(groups) == 1 and len(groups[0]) == 3


def test_topo_circular_raises():
    with pytest.raises(ValueError, match="Circular"):
        DagExecutionEngine._topo_sort([ms("A", ["B"]), ms("B", ["A"])])


def test_topo_unknown_dep_raises():
    with pytest.raises(ValueError, match="unknown step"):
        DagExecutionEngine._topo_sort([ms("A", ["GHOST"])])


# ── 条件分支 ─────────────────────────────────────────────

def test_condition_eq_true():
    c = StepCondition(variable="s", operator="eq", value="ok")
    assert _eval_condition(c, {"s": "ok"}) is True


def test_condition_eq_false():
    c = StepCondition(variable="s", operator="eq", value="ok")
    assert _eval_condition(c, {"s": "no"}) is False


def test_condition_exists():
    c = StepCondition(variable="token", operator="exists")
    assert _eval_condition(c, {"token": "abc"}) is True
    assert _eval_condition(c, {}) is False


def test_condition_contains():
    c = StepCondition(variable="msg", operator="contains", value="ok")
    assert _eval_condition(c, {"msg": "all ok"}) is True


def test_condition_bad_type_no_crash():
    c = StepCondition(variable="x", operator="gt", value=5)
    result = _eval_condition(c, {"x": "not_a_number"})
    assert isinstance(result, bool)


# ── run_scenario 并发取消（单元 mock）────────────────────

@pytest.mark.asyncio
async def test_run_scenario_cancels_on_failure():
    """
    wave 中有两个并发步骤 A 和 B。
    A 成功，B 失败。
    验证场景最终 passed=False 且 failure_reason 非空。
    """
    db    = MagicMock()
    col   = AsyncMock()
    col.find_one     = AsyncMock(return_value=None)
    col.insert_one   = AsyncMock()
    col.count_documents = AsyncMock(return_value=0)
    db.__getitem__   = MagicMock(return_value=col)

    from models.dsl import ScenarioDSL
    scenario = ScenarioDSL(
        id="s1", name="test",
        steps=[ms("A"), ms("B")],   # A、B 无依赖 → 同一波次并发
    )

    engine = DagExecutionEngine(db)

    pass_result = [StepResult(step_id="A", api_id="api_A", passed=True)]
    fail_result = [StepResult(step_id="B", api_id="api_B", passed=False, error="404 not found")]

    call_count = {"n": 0}
    async def mock_dispatch(step, ctx, env, api=None):
        call_count["n"] += 1
        if step.step_id == "A":
            return pass_result
        return fail_result

    engine._dispatch_step = mock_dispatch

    record = await engine.run_scenario(scenario)
    assert record.passed is False
    assert record.failure_reason


@pytest.mark.asyncio
async def test_run_scenario_all_pass():
    db  = MagicMock()
    col = AsyncMock()
    col.insert_one = AsyncMock()
    db.__getitem__ = MagicMock(return_value=col)

    from models.dsl import ScenarioDSL
    scenario = ScenarioDSL(id="s2", name="ok", steps=[ms("A"), ms("B", ["A"])])

    engine = DagExecutionEngine(db)

    async def mock_dispatch(step, ctx, env, api=None):
        return [StepResult(step_id=step.step_id, api_id=step.api_id, passed=True)]

    engine._dispatch_step = mock_dispatch
    record = await engine.run_scenario(scenario)
    assert record.passed is True
