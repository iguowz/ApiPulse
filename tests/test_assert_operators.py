"""
断言操作符前后端对齐测试（P0-2）

核心保证：
1. ASSERT_OPERATORS 元数据中声明的每个 operator 都被 run_asserts 真正实现
   （防止「元数据声明了但引擎没实现」导致断言静默失败）
2. run_asserts 实现的 operator 都在 ASSERT_OPERATORS 中声明
   （防止「引擎实现了但前端下拉没有」导致用户无法配置）
3. get_assert_operators 返回的数据结构完整（op/group/label_key/expected_type/help_zh 齐全）
4. expected_type 取值在合法枚举范围内（前端按此渲染控件）
5. ASSERT_TYPE_CANDIDATES 与 run_asserts 的 type_map 一致

这是 P0-2 单一来源对齐方案的回归基线，后续新增 operator 必须同步更新两端，
否则本测试会失败，强制开发者保持一致。
"""
from __future__ import annotations

import inspect

import pytest

from api.routers.apis import dry_run_assertions
from models.dsl import (
    ASSERT_OPERATORS, ASSERT_TYPE_CANDIDATES, get_assert_operators,
    ApiDSL, AssertRule, HttpMethod, RequestDSL, ResponseDSL,
)
from dag_engine.engine import run_asserts


# ── 从 run_asserts 源码中静态提取所有已实现的 operator ──────────────

def _extract_implemented_operators() -> set[str]:
    """
    解析 run_asserts 函数源码，提取所有 `op == "xxx"` 分支的 operator。
    这样无需逐个构造断言规则触发，即可静态核对元数据与实现的一致性。
    """
    src = inspect.getsource(run_asserts)
    import re
    # 匹配 op == "operator_name" 形式（单/双引号均兼容）
    matches = re.findall(r'op\s*==\s*["\']([a-z_]+)["\']', src)
    return set(matches)


IMPLEMENTED_OPS = _extract_implemented_operators()
DECLARED_OPS = {item["op"] for item in ASSERT_OPERATORS}


# ── 一致性校验 ────────────────────────────────────────────

def test_all_declared_operators_are_implemented():
    """元数据声明的每个 operator 都必须被 run_asserts 实现，否则断言静默失败。"""
    not_implemented = DECLARED_OPS - IMPLEMENTED_OPS
    assert not not_implemented, (
        f"ASSERT_OPERATORS 声明了但 run_asserts 未实现的 operator: {not_implemented}。"
        f"请补全 engine.py 的 run_asserts 分支，否则这些断言会被标记为 unknown operator。"
    )


def test_all_implemented_operators_are_declared():
    """run_asserts 实现的每个 operator 都必须在元数据中声明，否则前端无法配置。"""
    not_declared = IMPLEMENTED_OPS - DECLARED_OPS
    assert not not_declared, (
        f"run_asserts 实现了但 ASSERT_OPERATORS 未声明的 operator: {not_declared}。"
        f"请补全 models/dsl.py 的 ASSERT_OPERATORS，否则前端下拉缺失这些选项。"
    )


def test_no_duplicate_operators():
    """ASSERT_OPERATORS 不得有重复 operator（前端 v-for key 会冲突）。"""
    ops = [item["op"] for item in ASSERT_OPERATORS]
    assert len(ops) == len(set(ops)), f"存在重复 operator: {set([o for o in ops if ops.count(o) > 1])}"


# ── 元数据结构完整性 ────────────────────────────────────────

REQUIRED_FIELDS = {"op", "group", "label_key", "expected_type", "help_zh"}
VALID_EXPECTED_TYPES = {"none", "text", "number", "select_type", "json", "multi", "header_name"}


def test_operator_metadata_fields_complete():
    """每个 operator 元数据必须包含前端渲染所需的全部字段。"""
    for item in ASSERT_OPERATORS:
        missing = REQUIRED_FIELDS - set(item.keys())
        assert not missing, f"operator {item.get('op')} 缺少字段: {missing}"


def test_expected_type_values_valid():
    """expected_type 必须在合法枚举内，前端按此选择渲染控件。"""
    for item in ASSERT_OPERATORS:
        assert item["expected_type"] in VALID_EXPECTED_TYPES, (
            f"operator {item['op']} 的 expected_type='{item['expected_type']}' 非法，"
            f"合法值: {VALID_EXPECTED_TYPES}"
        )


# ── type_match 候选一致性 ───────────────────────────────────

def test_type_candidates_match_type_map():
    """ASSERT_TYPE_CANDIDATES 必须与 run_asserts 内 type_map 支持的类型一致。"""
    import re
    src = inspect.getsource(run_asserts)
    # 提取 type_map dict 中的 key（字符串字面量）
    # 形如 "int": int, "float": float ...
    type_map_section = src[src.find("type_map"):src.find("expected_type")]
    map_keys = set(re.findall(r'"([a-z]+)":\s', type_map_section))
    # ASSERT_TYPE_CANDIDATES 是前端展示的规范候选（type_map 含 number/array 等别名）
    # 规范候选必须全部被 type_map 支持
    for tc in ASSERT_TYPE_CANDIDATES:
        assert tc in map_keys, (
            f"ASSERT_TYPE_CANDIDATES 中的 '{tc}' 不在 run_asserts type_map 支持范围内，"
            f"type_map 支持: {map_keys}"
        )


# ── 接口下发函数 ────────────────────────────────────────────

def test_get_assert_operators_returns_copy():
    """get_assert_operators 返回深拷贝，避免外部修改污染常量。"""
    result1 = get_assert_operators()
    result2 = get_assert_operators()
    assert result1 == result2, "两次调用结果应一致"
    # 修改返回值不应影响再次调用的结果
    result1[0]["op"] = "tampered"
    assert get_assert_operators()[0]["op"] != "tampered", "返回值应是拷贝，不应被外部修改污染"


# ── 关键 operator 端到端抽检（确保声明+实现都真的能跑） ──────────

def _make_api(rule: AssertRule) -> ApiDSL:
    """构造带单条断言的 ApiDSL，用于端到端抽检。"""
    return ApiDSL(
        id="t1", name="t",
        request=RequestDSL(method=HttpMethod.GET, url="http://x"),
        response=ResponseDSL(status_code=200),
        asserts=[rule],
    )


def test_none_expected_type_operators_need_no_expected():
    """expected_type=none 的 operator（exists/empty 等）不应需要 expected 值即可判定。"""
    for item in ASSERT_OPERATORS:
        if item["expected_type"] != "none":
            continue
        # 构造 expected=None 的断言规则，验证 run_asserts 能正常求值不报错
        rule = AssertRule(field="$.code", operator=item["op"], expected=None)
        results, passed, fail_summary = run_asserts(
            _make_api(rule), status_code=200,
            body={"code": 0}, latency_ms=50, response_headers={"content-type": "application/json"},
        )
        # 关键：不抛异常、返回单个结果即视为实现完整（passed 与否取决于语义）
        assert len(results) == 1, f"operator {item['op']} 应返回 1 条结果"
        # 不应有 unknown operator 的失败摘要
        assert "unknown operator" not in fail_summary, f"operator {item['op']} 未被正确实现"


def test_response_time_lt_operator_runs():
    """response_time_lt 是性能类关键 operator，抽检其能正确判定。"""
    rule = AssertRule(field="$response_time_ms", operator="response_time_lt", expected=100)
    _, passed, _ = run_asserts(_make_api(rule), status_code=200, body={}, latency_ms=50)
    assert passed is True, "latency 50ms < 100ms 应通过"
    _, passed, _ = run_asserts(_make_api(rule), status_code=200, body={}, latency_ms=150)
    assert passed is False, "latency 150ms >= 100ms 应失败"


def test_header_operators_use_response_headers():
    """header_eq/header_contains 从 response_headers 取值，抽检一致性。"""
    rule = AssertRule(field="content-type", operator="header_eq", expected="application/json")
    _, passed, _ = run_asserts(
        _make_api(rule), status_code=200, body={},
        response_headers={"Content-Type": "application/json"},
    )
    assert passed is True, "header_eq 应不区分 header 名大小写匹配"


@pytest.mark.asyncio
async def test_dry_run_assertions_support_common_sources():
    """断言试算覆盖响应体、状态码、Header、性能四类常用来源。"""
    result = await dry_run_assertions(
        body={
            "sample": {
                "status_code": 200,
                "headers": {"Content-Type": "application/json"},
                "body": {"code": 0, "data": {"id": 42}},
                "latency_ms": 80,
            },
            "assertions": [
                {"source": "response", "path": "$.data.id", "operator": "eq", "expected": 42},
                {"source": "status", "path": "status_code", "operator": "eq", "expected": 200},
                {"source": "header", "path": "content-type", "operator": "header_contains", "expected": "json"},
                {"source": "performance", "path": "$response_time_ms", "operator": "response_time_lt", "expected": 100},
            ],
        },
        current_user={"username": "tester", "role": "admin"},
    )

    assert result["ok"] is True
    assert result["total"] == 4
    assert result["passed"] == 4
    assert result["failed"] == 0
    assert result["results"][0]["actual"] == 42


@pytest.mark.asyncio
async def test_dry_run_assertions_reports_failures_and_schema_errors():
    """断言试算失败时返回 actual/expected/error，便于前端直接定位。"""
    result = await dry_run_assertions(
        body={
            "sample": {
                "status_code": 201,
                "headers": {},
                "body": {"data": {"id": "42"}},
                "latency_ms": 250,
            },
            "assertions": [
                {"source": "response", "path": "$.data.id", "operator": "type_match", "expected": "int"},
                {
                    "source": "response",
                    "path": "$",
                    "operator": "json_schema",
                    "expected": {
                        "type": "object",
                        "required": ["code"],
                    },
                },
            ],
        },
        current_user={"username": "tester", "role": "admin"},
    )

    assert result["ok"] is False
    assert result["total"] == 2
    assert result["passed"] == 0
    assert result["failed"] == 2
    assert result["results"][0]["actual"] == "42"
    assert result["results"][1]["error"]
