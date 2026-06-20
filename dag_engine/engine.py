"""
DAG 执行引擎 v4
修复项：
- extract_jsonpath [*] 返回完整列表，而非仅首个元素
- run_scenario 并发步骤中任一失败时，用 asyncio.wait(FIRST_EXCEPTION) 取消其他任务
- _http_call 请求日志脱敏（过滤 password/token/secret/key 字段）
- 指数退避重试、条件分支、循环执行保持不变
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import random
import re
import secrets
import string
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase
from redis.asyncio import Redis

from models.dsl import (
    ApiDSL, DataTemplate, ExecutionRecord, ScenarioDSL,
    ScenarioStep, ScenarioStepType, StepCondition, StepResult,
)
from services.ai_job_service import AiJobService
from services.sql_runtime_service import SqlRuntimeService, summarize_sql_result

# 敏感字段名（不区分大小写），日志中替换为 ***
_SENSITIVE_KEYS = frozenset({
    "password", "passwd", "secret", "token", "access_token", "refresh_token",
    "authorization", "api_key", "apikey", "private_key", "credential",
    "auth", "x-api-key", "x-auth-token",
})


# 每次执行的环境配置（线程安全，避免并发执行时相互覆盖实例状态）
@dataclass
class _ExecEnv:
    base_url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    variables: dict[str, str] = field(default_factory=dict)


@dataclass
class _RenderResult:
    value: Any
    unresolved: list[str] = field(default_factory=list)


@dataclass
class _RenderedRequest:
    request_sent: dict[str, Any]
    context: dict[str, Any]
    script_results: list[dict[str, Any]]
    unresolved_refs: list[str]
    auth_sources: dict[str, Any] = field(default_factory=dict)
    header_sources: dict[str, str] = field(default_factory=dict)

_JSONPATH_ARRAY = re.compile(r"\[(\d+|\*)\]")
_TEMPLATE_REF_RE = re.compile(r"\{\{\s*([^}]+)\s*\}\}")
_PATH_PARAM_RE = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})|:([A-Za-z_][A-Za-z0-9_]*)")
_OVERRIDE_META_KEYS = frozenset({"_query", "_path", "_body_params", "_vars", "_body", "_body_type"})


def _dict_or_empty(value: Any) -> dict[str, Any]:
    """结构化参数来自前端 JSON；非对象时降级为空，避免错误配置污染请求。"""
    return value if isinstance(value, dict) else {}


def _split_override_params(override_params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """拆分 StepEditor 写入的结构化参数，同时保留旧式扁平键作为兜底输入。"""
    override_params = override_params or {}
    query_params = _dict_or_empty(override_params.get("_query"))
    path_params = _dict_or_empty(override_params.get("_path"))
    body_params = _dict_or_empty(override_params.get("_body_params"))
    variables = _dict_or_empty(override_params.get("_vars"))
    legacy_params = {k: v for k, v in override_params.items() if k not in _OVERRIDE_META_KEYS}
    return query_params, path_params, body_params, variables, legacy_params


def _path_placeholder_names(url: str) -> set[str]:
    """识别 /users/{id} 与 /users/:id，供路径参数替换和未解析提示使用。"""
    names: set[str] = set()
    for match in _PATH_PARAM_RE.finditer(url or ""):
        names.add(match.group(1) or match.group(2))
    return names


# ── jsonpath 提取 ──────────────────────────────────────────

def extract_jsonpath(data: Any, path: str) -> Any:
    """
    支持格式：$.a.b[0].c  $.items[*].id
    [*] 返回完整列表（非仅首个元素）
    """
    # 仅支持 $.xxx 格式的 jsonpath，其他格式不支持
    if not path.startswith("$."):
        return None
    path = path[2:]
    # 展开 [N] 为 .__N__，[*] 为 .__star__（便于后续 split(".") 统一处理）
    path = _JSONPATH_ARRAY.sub(
        lambda m: ".__star__" if m.group(1) == "*" else f".__{m.group(1)}__", path
    )
    keys = [k for k in path.split(".") if k]
    return _traverse(data, keys)


def _get_path_value(data: Any, path: str) -> Any:
    """按点路径从 dict/list 中取值，服务于模板引用和脚本 context 读取。"""
    if not path:
        return None
    cur = data
    for part in path.replace("$.", "").split("."):
        if not part:
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if 0 <= idx < len(cur) else None
        else:
            return None
    return cur


def _traverse(data: Any, keys: list[str]) -> Any:
    if not keys:
        # 已遍历完所有 key → 返回当前节点
        return data
    k = keys[0]
    rest = keys[1:]

    if k == "__star__":
        # [*] 通配：对数组每个元素递归遍历，合并非 None 结果
        if not isinstance(data, list):
            return None
        results = [_traverse(item, rest) for item in data]
        # 过滤掉 None；若全为 None 则返回 None
        filtered = [r for r in results if r is not None]
        return filtered if filtered else None

    m = re.fullmatch(r"__(\d+)__", k)
    if m:
        # [N] 数字索引：从数组中取指定下标元素
        idx = int(m.group(1))
        if isinstance(data, list):
            try:
                return _traverse(data[idx], rest)
            except IndexError:
                # 下标越界 → None
                return None
        # data 不是数组 → 无法取索引
        return None

    # 普通 key：从 dict 中取值
    if isinstance(data, dict):
        return _traverse(data.get(k), rest)
    # 非 dict 且非数组 → 无法遍历
    return None


# ── 日志脱敏 ──────────────────────────────────────────────

def _sanitize(obj: Any, depth: int = 0) -> Any:
    """递归脱敏，depth >= 4 时截断。"""
    if depth >= 4:
        # 嵌套过深 → 截断避免无限递归
        return "..."
    if isinstance(obj, dict):
        # dict：替换敏感字段值为 ***，其余递归脱敏
        return {
            k: "***" if k.lower() in _SENSITIVE_KEYS else _sanitize(v, depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        # 列表：最多保留前 10 个元素，防止日志过大
        return [_sanitize(i, depth + 1) for i in obj[:10]]
    # 标量值（str/int/float/bool/None）→ 原样返回
    return obj


# ── 条件判断 ──────────────────────────────────────────────

def _eval_condition(cond: StepCondition, context: dict[str, Any]) -> bool:
    val = context.get(cond.variable)
    exp = cond.value
    op  = cond.operator
    try:
        # 数值/等值比较
        if op == "eq":       return val == exp
        if op == "ne":       return val != exp
        if op == "gt":       return val > exp
        if op == "lt":       return val < exp
        if op == "gte":      return val >= exp
        if op == "lte":      return val <= exp
        # 存在性/包含性检查
        if op == "exists":   return val is not None
        if op == "contains": return exp in str(val)
        # 正则匹配
        if op == "regex":    return bool(re.search(str(exp), str(val)))
    except (TypeError, ValueError):
        # 类型不兼容（如 str vs int 比较）时静默返回 False，不中断流程
        pass
    # 未知操作符或异常 → 默认不满足条件
    return False


# ── P0-1: 步骤级 auth / pre_script / post_script 支持 ──────
# 解决问题：StepEditor UI 配置的 auth/pre_script/post_script/assertions 此前是「装饰性字段」，
# 引擎从未消费，导致用户配置的认证、前置/后置逻辑、步骤断言全部不生效。
# 安全策略：pre_script/post_script 优先支持声明式 JSON（无代码注入风险），
# 兼容纯表达式形态时用 AST 白名单沙箱（禁 import/open/exec/eval/dunder 等）。

# 受限表达式 AST 允许的节点类型白名单（仅允许字面量、变量、运算、索引、属性）
import ast as _ast

_SAFE_EXPR_NODES = (
    _ast.Expression, _ast.BoolOp, _ast.BinOp, _ast.UnaryOp, _ast.Compare,
    _ast.Constant, _ast.Name, _ast.Load, _ast.List, _ast.Tuple, _ast.Dict,
    _ast.Set, _ast.Subscript, _ast.Slice, _ast.IfExp,
    # 允许部分内置函数调用（仅白名单函数），降低表达力限制
    _ast.Call,
)
# 表达式中允许调用的内置函数白名单
_SAFE_BUILTINS = {
    "len": len, "str": str, "int": int, "float": float, "bool": bool,
    "abs": abs, "min": min, "max": max, "sum": sum, "round": round,
    "any": any, "all": all, "list": list, "dict": dict, "set": set,
    "sorted": sorted, "enumerate": enumerate, "zip": zip, "range": range,
    "upper": str.upper, "lower": str.lower,
}


def _validate_safe_expr(node: _ast.AST) -> None:
    """
    递归校验 AST 节点是否全在白名单内，违规则抛 ValueError 阻断执行。
    安全策略：
    1. 禁止 Attribute 节点（防 x.__class__.__bases__ 等 dunder 逃逸链）
    2. Call 节点的 func 必须是 Name 且函数名在白名单（防 __import__、eval 等）
    3. 允许表达式节点 + 运算符节点（Add/Eq 等是 BinOp.op 字段，属合法 AST 元素）
    eval(mode="eval") 本身只接受表达式，import/def/class 语句会在 parse 阶段 SyntaxError，
    但表达式内仍可调用 __import__，故在 Call 处额外校验函数名。
    """
    # Attribute 全禁：任何 .attr 访问都阻断（包括方法调用 obj.method()）
    if isinstance(node, _ast.Attribute):
        raise ValueError("属性访问被禁止（防 __dunder__ 注入）")
    # Call 节点：func 必须是 Name 且函数名在白名单 builtins 内
    if isinstance(node, _ast.Call):
        func = node.func
        if not isinstance(func, _ast.Name):
            raise ValueError("仅允许直接调用具名函数")
        if func.id not in _SAFE_BUILTINS:
            raise ValueError(f"函数 '{func.id}' 不在白名单")
    # 允许的节点类型（含运算符 op 节点：Add/Sub/Eq/Gt 等）
    if not isinstance(node, _SAFE_EXPR_NODES) and not isinstance(node, _ast.operator) \
            and not isinstance(node, _ast.cmpop) and not isinstance(node, _ast.boolop) \
            and not isinstance(node, _ast.unaryop):
        raise ValueError(f"不允许的表达式节点: {type(node).__name__}")
    for child in _ast.iter_child_nodes(node):
        _validate_safe_expr(child)


def _eval_safe_expr(expr: str, context: dict[str, Any]) -> Any:
    """
    安全表达式求值：AST 白名单 + 受限 builtins。
    用于 pre_script/post_script 的纯表达式形态，禁止任意代码执行。
    返回表达式求值结果。
    """
    try:
        tree = _ast.parse(expr.strip(), mode="eval")
        _validate_safe_expr(tree)
        # 以 context 为命名空间 + 白名单 builtins
        return eval(  # noqa: S307 - 已通过 AST 白名单校验，仅白名单节点可执行
            compile(tree, "<step_script>", "eval"),
            {"__builtins__": _SAFE_BUILTINS},
            context,
        )
    except Exception as e:
        logger.warning("Safe expr eval failed: {} → expr={!r}", e, expr[:120])
        return None


def _build_auth_headers(auth: dict[str, Any]) -> dict[str, str]:
    """
    根据 step.auth 配置生成认证 headers。
    支持 bearer / basic / apikey 三种类型，与 StepEditor.vue 前端编辑结构对齐：
      - bearer: {type:'bearer', token:'xxx'}
      - basic: {type:'basic', username:'u', password:'p'}
      - apikey: {type:'apikey', key:'X-API-Key', value:'xxx', in:'header'|'query'}
    返回的 headers 由调用方合并，优先级低于 env.headers（环境认证优先）。
    """
    import base64
    if not auth or not auth.get("type"):
        return {}
    atype = auth.get("type")
    if atype == "bearer":
        token = auth.get("token", "")
        # token 为空 → 不生成头，避免发送 "Bearer " 空值被服务端拒绝
        return {"Authorization": f"Bearer {token}"} if token else {}
    if atype == "basic":
        user = auth.get("username", "")
        pwd = auth.get("password", "")
        # 用户名密码拼接后 Base64 编码，符合 HTTP Basic Auth 规范
        raw = f"{user}:{pwd}".encode()
        return {"Authorization": f"Basic {base64.b64encode(raw).decode()}"}
    if atype == "apikey":
        key = auth.get("key", "")
        value = auth.get("value", "")
        # apikey 仅处理 header 形态；query 形式由调用方注入 params（见 _http_call）
        if key and value and auth.get("in", "header") == "header":
            return {key: value}
    return {}


def _build_auth_query(auth: dict[str, Any]) -> dict[str, str]:
    """apikey 的 query 形态：注入到 URL query params。"""
    if not auth or auth.get("type") != "apikey":
        return {}
    if auth.get("in") != "query":
        return {}
    key = auth.get("key", "")
    value = auth.get("value", "")
    return {key: value} if key and value else {}


def _render_step_auth(
    engine: "DagExecutionEngine",
    auth: dict[str, Any],
    context: dict[str, Any],
    env_vars: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    """渲染 auth 中的 token/key/value，解决鉴权字段无法引用脚本和环境变量的问题。"""
    if not isinstance(auth, dict) or not auth.get("type"):
        return {}, []
    rendered = engine._render_templates_detailed(auth, context, env_vars)
    auth_value = rendered.value if isinstance(rendered.value, dict) else {}
    return auth_value, rendered.unresolved


def _replace_path_params(url: str, path_params: dict[str, Any]) -> tuple[str, list[str]]:
    """把结构化 Path 参数写回 URL，并返回仍未补齐的占位符名。"""
    unresolved: list[str] = []

    def repl(match: re.Match) -> str:
        name = match.group(1) or match.group(2)
        value = path_params.get(name)
        if name not in path_params or value in (None, ""):
            unresolved.append(f"path.{name}")
            return match.group(0)
        if isinstance(value, str) and _TEMPLATE_REF_RE.search(value):
            unresolved.append(f"path.{name}")
            return match.group(0)
        return quote(str(value), safe="")

    return _PATH_PARAM_RE.sub(repl, url or ""), unresolved


def _run_step_script_detailed(
    script: str | list, context: dict[str, Any], response: Any = None, phase: str = "script",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    执行 step 的 pre_script / post_script，返回要合并进 context 的变量字典。
    支持两种形态（优先声明式，兼容纯表达式）：
      1. 声明式 JSON（推荐，无注入风险）：
         [{"op":"set", "var":"token", "from":"response", "path":"$.data.token"},
          {"op":"set", "var":"n", "from":"literal", "value":5}]
         存储形态：ScenarioStep.pre_script/post_script 是 str 字段，声明式脚本以 JSON 字符串存储。
      2. 纯表达式字符串（受限 eval，用于简单场景）：
         "1 if context.get('x') else 2"  → 结果存入 __expr__ 变量
    response 参数仅在 post_script 时传入（HTTP 响应体），pre_script 时为 None。
    失败不抛异常，返回空 dict（脚本错误不应阻断主流程）。
    """
    result: dict[str, Any] = {}
    details: list[dict[str, Any]] = []
    if not script:
        return result, details

    def _ctx_path(path: str) -> Any:
        return _get_path_value(context, path.replace("$.", "")) if path else None

    def _hash_value(value: Any, algorithm: str = "sha256", secret: str = "") -> str:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        algo = algorithm if algorithm in {"md5", "sha1", "sha256", "sha512"} else "sha256"
        if secret:
            return hmac.new(str(secret).encode(), text.encode(), getattr(hashlib, algo)).hexdigest()
        return getattr(hashlib, algo)(text.encode()).hexdigest()

    def _random_value(kind: str = "uuid", length: int = 16) -> Any:
        if kind == "uuid":
            return str(uuid.uuid4())
        if kind == "int":
            return random.randint(0, 999999)
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(max(1, min(int(length or 16), 128))))

    def _render_script_text(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        merged = {**context, **result}

        def repl(match: re.Match) -> str:
            key = match.group(1).strip()
            found = _get_path_value(merged, key)
            return str(found) if found is not None else match.group(0)

        return _TEMPLATE_REF_RE.sub(repl, value)
    # 字符串形态：尝试 JSON 解析为声明式列表；解析失败则当作纯表达式处理
    if isinstance(script, str):
        stripped = script.strip()
        if stripped.startswith("["):
            try:
                import json as _json
                script = _json.loads(stripped)
            except Exception as e:
                logger.warning("step script JSON parse failed: {} → treat as expr", e)
                # JSON 解析失败 → 回退为纯表达式字符串
                value = _eval_safe_expr(stripped, context)
                return {"__expr__": value}, [{
                    "phase": phase, "op": "expr", "var": "__expr__", "value": value,
                    "ok": value is not None, "error": "" if value is not None else "expression returned None",
                }]
        else:
            # 非 JSON 数组 → 纯表达式字符串
            value = _eval_safe_expr(stripped, context)
            return {"__expr__": value}, [{
                "phase": phase, "op": "expr", "var": "__expr__", "value": value,
                "ok": value is not None, "error": "" if value is not None else "expression returned None",
            }]
    # 统一为列表处理：单表达式字符串包成单项列表
    items = script if isinstance(script, list) else [script]
    for item in items:
        try:
            if isinstance(item, dict):
                op = item.get("op", "set")
                var = item.get("var", "")
                if not var:
                    details.append({"phase": phase, "op": op, "var": "", "ok": False, "error": "missing var"})
                    continue
                src = item.get("from", "literal")
                if op in {"timestamp", "time"}:
                    val = int(time.time() * 1000) if item.get("unit") == "ms" else int(time.time())
                    src = "generated"
                elif op == "random":
                    val = _random_value(item.get("kind", "uuid"), int(item.get("length", 16) or 16))
                    src = "generated"
                elif op == "hash":
                    raw_value = item.get("value")
                    if item.get("path"):
                        raw_value = _ctx_path(str(item.get("path")))
                    elif item.get("from") == "response" and response is not None:
                        raw_value = extract_jsonpath(response, item.get("response_path", item.get("path", "")))
                    val = _hash_value(
                        _render_script_text(raw_value),
                        item.get("algorithm", "sha256"),
                        _render_script_text(item.get("secret", "")),
                    )
                    src = "hash"
                elif op in {"compute", "expr"}:
                    val = _eval_safe_expr(item.get("value", ""), context)
                    src = "expr"
                elif op in {"copy", "extract"} and src == "context":
                    val = _ctx_path(item.get("path", ""))
                elif src == "response" and response is not None:
                    # 从 HTTP 响应体按 jsonpath 提取
                    val = extract_jsonpath(response, item.get("path", ""))
                elif src == "context":
                    # 从已有上下文按 jsonpath 提取
                    val = extract_jsonpath(context, item.get("path", ""))
                elif src == "expr":
                    # 声明式里嵌入受限表达式
                    val = _eval_safe_expr(item.get("value", ""), context)
                else:
                    # literal：直接取 value
                    val = item.get("value")
                result[var] = val
                details.append({"phase": phase, "op": op, "var": var, "from": src, "value": val, "ok": True, "error": ""})
            elif isinstance(item, str):
                # 纯表达式字符串 → 受限 eval，结果存入 __expr__
                val = _eval_safe_expr(item, context)
                result["__expr__"] = val
                details.append({
                    "phase": phase, "op": "expr", "var": "__expr__", "value": val,
                    "ok": val is not None, "error": "" if val is not None else "expression returned None",
                })
        except Exception as e:
            logger.warning("step script item failed: {} → item={!r}", e, str(item)[:120])
            details.append({"phase": phase, "op": "unknown", "var": "", "ok": False, "error": str(e)[:300]})
    return result, details


def _run_step_script(
    script: str | list, context: dict[str, Any], response: Any = None,
) -> dict[str, Any]:
    result, _details = _run_step_script_detailed(script, context, response=response)
    return result


async def _run_sql_queries(
    db: AsyncIOMotorDatabase,
    project_id: str,
    queries: list[dict[str, Any]],
    context: dict[str, Any],
    prefix: str = "sql",
) -> dict[str, Any]:
    """运行步骤 SQL 查询并写入上下文，失败结果也保留在上下文中供断言定位。"""
    if not queries:
        return {}
    service = SqlRuntimeService(db)
    results: dict[str, Any] = {}
    failures: list[str] = []
    for idx, query in enumerate(queries):
        if not isinstance(query, dict):
            continue
        name = query.get("target_var") or query.get("name") or f"q{idx + 1}"
        try:
            result = await service.run_ref(project_id, query, context)
        except Exception as e:
            result = _sql_exception_result(e, query)
        result["name"] = name
        result["phase"] = query.get("phase") or prefix
        results[name] = result
        # SQL 默认阻断步骤，显式 fail_on_error=false 时仅记录错误，便于非关键观察类查询继续执行。
        if result.get("error") and query.get("fail_on_error", True):
            failures.append(f"{name}: {result.get('message') or result.get('error')}")
    payload: dict[str, Any] = {prefix: results}
    if failures:
        payload["_sql_failures"] = failures
    return payload


def _sql_actual(sql_context: dict[str, Any], field_path: str) -> Any:
    """从 SQL 上下文提取断言/提取值，支持 sql.user.scalar / $.sql.user.first.id。"""
    if not field_path:
        return sql_context
    path = field_path
    if path.startswith("sql."):
        path = "$." + path
    return extract_jsonpath(sql_context, path) if path.startswith("$.") else sql_context.get(path)


def _sql_exception_result(e: Exception, query: dict[str, Any]) -> dict[str, Any]:
    """把 SQL 配置异常转换为统一结果结构，保证执行记录可解释而不是只显示 Unexpected。"""
    detail = getattr(e, "detail", None) or str(e)
    return {
        "ok": False,
        "error_code": type(e).__name__,
        "error": str(detail),
        "message": str(detail),
        "columns": [],
        "rows": [],
        "first": None,
        "scalar": None,
        "row_count": 0,
        "duration_ms": 0,
        "truncated": False,
        "rendered_sql_preview": query.get("sql_text") or query.get("sql_ref") or "",
        "rendered_params": query.get("params") or {},
    }


# ── 断言引擎 ──────────────────────────────────────────────

def _eval_single_assert(op: str, actual: Any, expected: Any) -> bool:
    """
    P0-1: 单条断言求值（步骤断言专用，与 run_asserts 内部语义保持一致）。
    从 run_asserts 抽取核心判定逻辑，使 step.assertions 复用同一套 operator 语义，
    避免「API 断言用一套、步骤断言用另一套」导致行为不一致。
    复用 ASSERT_OPERATORS 的 operator 集合（已通过 test_assert_operators 校验对齐）。
    未知 operator 一律返回 False（保守判定，避免漏检）。
    """
    # 工具函数：尝试数值类型强制转换（如 int 200 vs str "200"），解决步骤断言中
    # expected 来自前端表单为字符串而 actual 为数值类型时的跨类型比较失败问题
    def _coerce_numeric(a, e):
        if type(a) == type(e):
            return a, e
        if isinstance(a, (int, float)) and isinstance(e, str):
            try:
                return a, type(a)(e)
            except (ValueError, TypeError):
                pass
        elif isinstance(e, (int, float)) and isinstance(a, str):
            try:
                return type(e)(a), e
            except (ValueError, TypeError):
                pass
        return a, e

    try:
        # ── 比较类（含数值类型强制转换） ──
        if op == "eq":
            if actual == expected:
                return True
            a2, e2 = _coerce_numeric(actual, expected)
            return a2 == e2
        if op == "ne":
            if actual != expected:
                return True
            a2, e2 = _coerce_numeric(actual, expected)
            return a2 != e2
        if op == "gt":
            a2, e2 = _coerce_numeric(actual, expected)
            return a2 > e2
        if op == "gte":
            a2, e2 = _coerce_numeric(actual, expected)
            return a2 >= e2
        if op == "lt":
            a2, e2 = _coerce_numeric(actual, expected)
            return a2 < e2
        if op == "lte":
            a2, e2 = _coerce_numeric(actual, expected)
            return a2 <= e2
        # ── 字符串类 ──
        if op == "contains":     return expected in str(actual)
        if op == "not_contains": return expected not in str(actual)
        if op == "starts_with":  return str(actual).startswith(str(expected))
        if op == "ends_with":    return str(actual).endswith(str(expected))
        if op == "regex":        return bool(re.search(str(expected), str(actual)))
        # ── 存在性类 ──
        if op == "exists":       return actual is not None
        if op == "not_exists":   return actual is None
        if op == "empty":
            return actual is None or actual == "" or actual == [] or actual == {} or actual == 0
        if op == "not_empty":
            return actual is not None and actual != "" and actual != [] and actual != {} and actual != 0
        # ── 集合类 ──
        if op == "in":           return actual in expected
        if op == "not_in":       return actual not in expected
        # ── 类型/结构类（步骤断言较少用，但仍支持） ──
        if op == "length":
            return isinstance(actual, (list, str, dict)) and len(actual) == expected
        if op == "type_match":
            type_map = {"int": int, "float": float, "str": str, "bool": bool,
                        "list": list, "dict": dict, "null": type(None),
                        "array": list, "object": dict, "string": str,
                        "number": (int, float), "integer": int, "boolean": bool}
            return isinstance(actual, type_map.get(str(expected).lower(), type(None)))
        if op == "response_time_lt":
            return actual < expected
    except (TypeError, ValueError):
        # 类型不兼容（如 str vs int 比较）→ 静默判 False
        pass
    # 未知 operator → 保守判 False
    return False


def run_asserts(
    api: ApiDSL,
    status_code: int,
    body: Any,
    latency_ms: int = 0,
    response_headers: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], bool, str]:
    """
    返回 (assert_results, all_passed, failure_summary)

    支持的操作符（v4 扩展）：
    - 比较: eq / ne / gt / gte / lt / lte
    - 字符串: contains / not_contains / starts_with / ends_with / regex
    - 存在性: exists / not_exists / empty / not_empty
    - 集合: in / not_in
    - 类型: type_match (int/float/str/bool/list/dict/null)
    - 结构: length (数组长度或字符串长度), json_schema (JSON Schema 校验)
    - 响应头: header_eq / header_contains
    - 性能: response_time_lt (响应时间小于 N ms)

    特殊字段名:
    - $response_time_ms: 自动匹配 latency_ms，无需从 body 提取
    """
    # 无断言规则时的默认行为：仅检查 HTTP 状态码 2xx
    if not api.asserts:
        passed = 200 <= status_code < 300
        return [], passed, "" if passed else f"status {status_code} not 2xx"

    results: list[dict[str, Any]] = []
    failures: list[str] = []

    for rule in api.asserts:
        # ── 根据字段类型获取实际值 ──
        if rule.field == "$response_time_ms":
            # 特殊字段: 响应时间 → 直接使用 latency_ms
            actual: Any = latency_ms
            source = "performance"
        elif rule.operator in ("header_eq", "header_contains"):
            # 响应头断言: field 为 header 名（如 "content-type"），从 response_headers 按小写匹配
            actual = None
            source = "header"
            if response_headers:
                field_lower = rule.field.lower()
                for hk, hv in response_headers.items():
                    if hk.lower() == field_lower:
                        actual = hv
                        break
            # response_headers 为空或未找到 → actual 保持 None
        elif rule.field == "status_code":
            # 状态码断言 → 直接使用 HTTP 状态码
            actual = status_code
            source = "status"
        else:
            # 普通字段 → 从响应体通过 jsonpath 提取
            actual = extract_jsonpath(body, rule.field)
            source = "response"

        op, exp = rule.operator, rule.expected
        passed = False
        error = ""
        try:
            # 委托给 _eval_single_assert 处理共享运算符，确保 API 断言与步骤断言使用同一套比较逻辑
            # （包含数值类型强制转换 int/str，避免 expected 来自前端表单字符串时比较失败）
            if op in ("eq", "ne", "gt", "gte", "lt", "lte",
                       "contains", "not_contains", "starts_with", "ends_with",
                       "regex", "exists", "not_exists", "empty", "not_empty",
                       "in", "not_in", "length", "type_match", "response_time_lt"):
                passed = _eval_single_assert(op, actual, exp)
                # _eval_single_assert 静默返回 False 而非报错，此处保持与旧逻辑一致通过 error 字段区分原因
                if not passed and op == "length" and not isinstance(actual, (list, str, dict)):
                    error = f"length operator requires list/str/dict, got {type(actual).__name__}"
                    failures.append(f"{rule.field}: {error}")
                elif not passed and op == "type_match" and str(exp).lower() not in {
                    "int", "float", "str", "bool", "list", "dict", "null",
                    "array", "object", "string", "number", "integer", "boolean",
                }:
                    error = f"unknown type '{exp}'"
                    failures.append(f"{rule.field}: {error}")
            elif op == "json_schema":
                # JSON Schema 校验：exp 为 JSON Schema dict，对响应体（body）做校验
                # 使用 jsonschema 库进行校验，若未安装则跳过并标记失败
                try:
                    import jsonschema
                    target = body if rule.field in ("$", "$.body", "body") else actual
                    jsonschema.validate(target, exp)
                    passed = True
                except ImportError:
                    logger.warning("jsonschema library not installed, json_schema assertion skipped")
                    passed = False
                    error = "jsonschema library not available"
                    failures.append(f"{rule.field}: {error}")
                except Exception as schema_err:
                    passed = False
                    error = f"schema validation failed: {schema_err}"
                    failures.append(f"{rule.field}: {error}")
            elif op == "header_eq":
                # 响应头等值校验：已在上面提取 actual，此处做等值比较
                passed = str(actual).lower() == str(exp).lower() if actual is not None else False
            elif op == "header_contains":
                # 响应头包含校验：actual 中包含 exp 子串（不区分大小写）
                passed = str(exp).lower() in str(actual).lower() if actual is not None else False
            # 未知断言操作符：标记失败并记录警告，避免静默通过导致漏检
            else:
                logger.warning("Unknown assertion operator '{}' for field '{}', marking as failed", op, rule.field)
                passed = False
                error = f"unknown operator '{op}'"
                failures.append(f"{rule.field}: {error}")
        except Exception as e:
            passed = False
            error = str(e)
            logger.debug("Assert eval error field={} op={}: {}", rule.field, op, e)

        if not passed:
            failures.append(f"{rule.field} {op} {exp!r} (actual={actual!r})")

        results.append({
            "field":       rule.field,
            "operator":    op,
            "expected":    exp,
            "actual":      actual,
            "passed":      passed,
            "source":      source,
            "error":       error,
            "description": rule.description,
            "risk_level":  rule.risk_level,
        })

    all_passed = not failures
    return results, all_passed, "; ".join(failures)


# ── DAG 执行引擎 ──────────────────────────────────────────

# Phase 4: 失败诊断队列名
DIAGNOSE_FAILURE_QUEUE = "queue:diagnose_failure"

class DagExecutionEngine:
    def __init__(self, db: AsyncIOMotorDatabase, redis: Redis | None = None, ws_manager=None):
        self._db       = db
        self._redis    = redis
        self._api_col  = db["api_dsls"]
        self._exec_col = db["executions"]
        self._tmpl_col = db["data_templates"]
        # P0-2: WebSocket 管理器 + 当前执行 ID，用于执行中逐步骤广播进度
        self._ws_manager = ws_manager
        self._exec_id = ""

    async def _broadcast_step_status(
        self, step_id: str, status: str,
        latency_ms: int = 0, error: str = "",
    ) -> None:
        """P0-2: 广播单步骤执行状态到 exec:{exec_id} 频道，前端据此逐节点变色。
        无 ws_manager 或 exec_id 时静默跳过（兼容测试和不需进度的场景）。"""
        if not self._ws_manager or not self._exec_id:
            return
        try:
            await self._ws_manager.broadcast(f"exec:{self._exec_id}", {
                "type": "step_status",
                "exec_id": self._exec_id,
                "step_id": step_id,
                "status": status,  # running / passed / failed / skipped
                "latency_ms": latency_ms,
                "error": error[:200] if error else "",
            })
        except Exception as e:
            # 广播失败不影响执行流程，仅记录日志
            logger.debug("Step status broadcast failed: {}", e)

    # ── 公开入口 ──────────────────────────────────────────

    async def run_scenario(
        self,
        scenario: ScenarioDSL,
        initial_context: dict[str, Any] | None = None,
        trigger: str = "manual",
        owner: str = "",
        env_base_url: str = "",
        env_headers: dict[str, str] | None = None,
        env_variables: dict[str, str] | None = None,
        client_ip: str = "",
        exec_id: str = "",
    ) -> ExecutionRecord:
        # 创建每次执行的独立环境配置，避免并发执行时实例状态被覆盖
        exec_env = _ExecEnv(
            base_url=env_base_url,
            headers=env_headers or {},
            variables=env_variables or {},
        )
        started = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        record = ExecutionRecord(
            id=exec_id or str(uuid.uuid4()),
            scenario_id=scenario.id,
            type="scenario",
            started_at=started,
            trigger=trigger,
            executor=owner or scenario.owner or "",
            project_id=scenario.project_id,
            execution_ip=client_ip,
        )
        # P0-2: 设置当前执行 ID，供 _execute_step 广播步骤进度使用
        self._exec_id = record.id
        # 广播执行开始事件（前端据此重置所有节点为 idle 状态）
        if self._ws_manager:
            try:
                await self._ws_manager.broadcast(f"exec:{self._exec_id}", {
                    "type": "scenario_started",
                    "exec_id": self._exec_id,
                    "scenario_id": scenario.id,
                    "scenario_name": scenario.name,
                })
            except Exception:
                logger.warning("Failed to broadcast scenario_started for exec=%s scenario=%s", self._exec_id, getattr(scenario, 'id', '?'))
        context: dict[str, Any] = dict(initial_context or {})
        self._scenario_steps = list(scenario.steps)
        self._scenario_children = self._build_children_map(scenario.steps)

        try:
            groups = self._topo_sort(self._top_level_steps(scenario.steps))
            logger.info(
                "Scenario '{}' start: {} steps in {} wave(s)",
                scenario.name, len(scenario.steps), len(groups),
            )

            for group in groups:
                failed_record = await self._run_wave(group, context, record, exec_env)
                if failed_record is not None:
                    failed_record.finished_at  = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
                    failed_record.duration_ms  = int(
                        (failed_record.finished_at - started).total_seconds() * 1000
                    )
                    await self._save_record(failed_record)
                    # Phase 4: 失败场景入队等待 AI 诊断
                    await self._enqueue_diagnosis(failed_record)
                    return failed_record

            record.passed = all(s.passed or s.skipped for s in record.steps)

        except Exception as e:
            logger.error("Scenario execution error: {}", e)
            record.passed         = False
            record.failure_reason = str(e)

        record.finished_at = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        record.duration_ms = int((record.finished_at - started).total_seconds() * 1000)
        await self._save_record(record)
        # Phase 4: 失败场景入队等待 AI 诊断
        if not record.passed:
            await self._enqueue_diagnosis(record)
        logger.info(
            "Scenario '{}' done: passed={} duration={}ms",
            scenario.name, record.passed, record.duration_ms,
        )
        return record

    async def _run_wave(
        self,
        group: list[ScenarioStep],
        context: dict[str, Any],
        record: ExecutionRecord,
        env: _ExecEnv,
    ) -> ExecutionRecord | None:
        """
        并发执行一个波次内的所有步骤。
        任一步骤失败时，取消其余尚未完成的任务，立即返回失败记录。
        成功时更新 context 并追加步骤到 record。
        """
        if len(group) == 1:
            # 单步骤波次：直接执行，无需 asyncio.wait 管理开销
            results = await self._dispatch_step(group[0], context, env)
            for sr in results:
                record.steps.append(sr)
                if sr.extracted_vars:
                    self._merge_extracted(context, sr)
                if not sr.passed and not sr.skipped:
                    # 步骤失败 → 立即中止场景执行，返回失败记录
                    record.passed         = False
                    record.failure_reason = sr.error or f"step {sr.step_id} failed"
                    return record
            # 单步骤正常完成 → 返回 None 继续下一波次
            return None

        # 多步骤并发：用 asyncio.wait + FIRST_EXCEPTION 取消其他任务
        tasks = {
            asyncio.create_task(self._dispatch_step(step, context, env)): step
            for step in group
        }
        all_results: list[list[StepResult]] = []
        failed_record: ExecutionRecord | None = None

        try:
            done, pending = await asyncio.wait(
                tasks.keys(),
                return_when=asyncio.FIRST_EXCEPTION,
            )

            # 检查已完成任务是否有异常
            for task in done:
                exc = task.exception()
                if exc:
                    # 任一步骤抛异常 → 取消所有尚未完成的任务（FIRST_EXCEPTION 策略）
                    for p in pending:
                        p.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    record.passed         = False
                    record.failure_reason = str(exc)
                    failed_record = record
                    break
                # 正常完成的任务 → 收集结果
                all_results.append(task.result())

            # 若无异常则等待剩余未完成任务完成
            if failed_record is None and pending:
                remaining = await asyncio.gather(*pending, return_exceptions=True)
                for r in remaining:
                    if isinstance(r, Exception):
                        # 剩余任务中有异常（可能是被 cancel 或自身出错）
                        record.passed         = False
                        record.failure_reason = str(r)
                        failed_record = record
                        break
                    all_results.append(r)

        except Exception as e:
            # asyncio.wait 本身出错 → 取消所有任务，清理资源
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks.keys(), return_exceptions=True)
            record.passed         = False
            record.failure_reason = str(e)
            return record

        # 合并所有步骤结果：更新 context 中提取的变量，检测失败
        for step_results in all_results:
            for sr in step_results:
                record.steps.append(sr)
                if sr.extracted_vars:
                    self._merge_extracted(context, sr)
                if not sr.passed and not sr.skipped and failed_record is None:
                    # 首个未跳过且未通过的步骤 → 标记场景失败
                    record.passed         = False
                    record.failure_reason = sr.error or f"step {sr.step_id} failed"
                    failed_record = record

        return failed_record

    def _merge_extracted(self, context: dict[str, Any], sr: StepResult) -> None:
        """合并提取变量：同时提供 var 与 step_id.var，后者是新 DSL 的标准引用。"""
        for key, value in (sr.extracted_vars or {}).items():
            context[key] = value
            if sr.step_id:
                context[f"{sr.step_id}.{key}"] = value

    async def run_single(
        self,
        api: ApiDSL,
        override_params: dict[str, Any] | None = None,
        override_headers: dict[str, str] | None = None,
        trigger: str = "manual",
        owner: str = "",
        env_headers: dict[str, str] | None = None,
        env_variables: dict[str, str] | None = None,
        client_ip: str = "",
    ) -> ExecutionRecord:
        # 创建每次执行的独立环境配置，避免并发执行时实例状态被覆盖
        exec_env = _ExecEnv(headers=env_headers or {}, variables=env_variables or {})
        step = ScenarioStep(
            step_id="single",
            api_id=api.id,
            name=api.name,
            override_params=override_params or {},
            override_headers=override_headers or {},
        )
        started = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        results = await self._dispatch_step(step, {}, exec_env, api=api)
        sr = results[0] if results else StepResult(
            step_id="single", api_id=api.id, name=api.name, passed=False, error="no result"
        )
        finished = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        record = ExecutionRecord(
            id=str(uuid.uuid4()),
            api_id=api.id,
            type="single",
            steps=[sr],
            passed=sr.passed,
            started_at=started,
            finished_at=finished,
            duration_ms=int((finished - started).total_seconds() * 1000),
            trigger=trigger,
            executor=owner or "",
            project_id=api.project_id,
            failure_reason=sr.error if not sr.passed else "",
            execution_ip=client_ip,
        )
        await self._save_record(record)
        # Phase 4: 失败单接口执行入队等待 AI 诊断
        if not record.passed:
            await self._enqueue_diagnosis(record)
        return record

    # ── 步骤调度（条件 + 循环 + 数据注入）────────────────

    async def _dispatch_step(
        self,
        step: ScenarioStep,
        context: dict[str, Any],
        env: _ExecEnv,
        api: ApiDSL | None = None,
    ) -> list[StepResult]:
        step_type = step.type.value if hasattr(step.type, "value") else str(step.type or "api")

        if step_type == "start":
            for p in step.start_params or []:
                if p.name and p.name not in context:
                    context[p.name] = p.default
            await self._broadcast_step_status(step.step_id, "passed")
            return [StepResult(step_id=step.step_id, api_id="", name=step.name or "START", passed=True)]

        if step_type == "end":
            await self._broadcast_step_status(step.step_id, "passed")
            return [StepResult(step_id=step.step_id, api_id="", name=step.name or "END", passed=True)]

        if step_type == "condition":
            return await self._dispatch_condition_container(step, context, env)

        if step_type == "loop":
            return await self._dispatch_loop_container(step, context, env)

        # ── 条件分支 ──
        if step.condition:
            cond_ok = _eval_condition(step.condition, context)
            if not cond_ok:
                # 条件不满足 → 根据 on_false 策略决定行为
                action = step.condition.on_false
                logger.info("Step {} condition false → {}", step.step_id, action)
                if action == "skip":
                    # skip：跳过该步骤，标记为 skipped 且 passed（不阻塞场景）
                    # P0-2: 广播 skipped 状态，前端节点显示灰色跳过
                    await self._broadcast_step_status(step.step_id, "skipped")
                    return [StepResult(
                        step_id=step.step_id, api_id=step.api_id, name=step.name,
                        skipped=True, passed=True,
                    )]
                if action == "fail":
                    # fail：条件不满足直接导致步骤失败
                    # P0-2: 广播 failed 状态
                    await self._broadcast_step_status(step.step_id, "failed", error="condition not met")
                    return [StepResult(
                        step_id=step.step_id, api_id=step.api_id, name=step.name,
                        passed=False, error="condition not met",
                    )]

        # ── 数据工厂注入 ──
        if step.data_template_id and self._redis:
            context = await self._inject_factory_data(step, context)

        # ── 列表循环 ──
        if step.loop_var and step.loop_var in context:
            items = context[step.loop_var]
            if isinstance(items, list):
                # 遍历列表中每个元素，注入 __loop_item__ 和 __loop_index__ 到上下文
                results: list[StepResult] = []
                for idx, item in enumerate(items):
                    loop_ctx = {**context, "__loop_item__": item, "__loop_index__": idx}
                    sr = await self._execute_step(step, loop_ctx, env, api)
                    sr.loop_index = idx
                    results.append(sr)
                    if not sr.passed:
                        # 任一次迭代失败 → 提前终止循环（fail-fast）
                        break
                return results
            # loop_var 存在但不是 list → 跳过循环，走单次执行

        # ── 固定次数循环 ──
        if step.loop_count and step.loop_count > 1:
            results = []
            for idx in range(step.loop_count):
                loop_ctx = {**context, "__loop_index__": idx}
                sr = await self._execute_step(step, loop_ctx, env, api)
                sr.loop_index = idx
                results.append(sr)
                if not sr.passed:
                    # 任一次迭代失败 → 提前终止循环
                    break
            return results

        # ── 普通单次执行 ──
        return [await self._execute_step(step, context, env, api)]

    def _build_children_map(self, steps: list[ScenarioStep]) -> dict[str, list[ScenarioStep]]:
        children: dict[str, list[ScenarioStep]] = {}
        for step in steps:
            if step.parent_id:
                children.setdefault(step.parent_id, []).append(step)
        return children

    def _top_level_steps(self, steps: list[ScenarioStep]) -> list[ScenarioStep]:
        return [s for s in steps if not s.parent_id]

    async def _run_subgraph(
        self,
        children: list[ScenarioStep],
        context: dict[str, Any],
        record: ExecutionRecord,
        env: _ExecEnv,
    ) -> ExecutionRecord | None:
        groups = self._topo_sort(children)
        for group in groups:
            failed = await self._run_wave(group, context, record, env)
            if failed is not None:
                return failed
        return None

    async def _dispatch_condition_container(
        self,
        step: ScenarioStep,
        context: dict[str, Any],
        env: _ExecEnv,
    ) -> list[StepResult]:
        cond_ok = _eval_condition(step.condition, context) if step.condition else True
        if not cond_ok:
            action = step.condition.on_false if step.condition else "skip"
            if action == "fail":
                await self._broadcast_step_status(step.step_id, "failed", error="condition not met")
                return [StepResult(step_id=step.step_id, api_id="", name=step.name, passed=False, error="condition not met")]
            await self._broadcast_step_status(step.step_id, "skipped")
            return [StepResult(step_id=step.step_id, api_id="", name=step.name, skipped=True, passed=True)]

        await self._broadcast_step_status(step.step_id, "running")
        sub_record = ExecutionRecord(id=self._exec_id, scenario_id="", type="scenario")
        failed = await self._run_subgraph(self._scenario_children.get(step.step_id, []), context, sub_record, env)
        await self._broadcast_step_status(step.step_id, "failed" if failed else "passed", error=(failed.failure_reason if failed else ""))
        return [StepResult(step_id=step.step_id, api_id="", name=step.name, passed=failed is None, error=(failed.failure_reason if failed else "")), *sub_record.steps]

    async def _dispatch_loop_container(
        self,
        step: ScenarioStep,
        context: dict[str, Any],
        env: _ExecEnv,
    ) -> list[StepResult]:
        await self._broadcast_step_status(step.step_id, "running")
        loop = step.loop
        iterations: list[Any] = []
        if loop and loop.mode == "list":
            ref = loop.list_ref or ""
            value = self._resolve_template_ref(ref, context, {})
            iterations = value if isinstance(value, list) else []
        else:
            count = (loop.count if loop else None) or step.loop_count or 1
            iterations = list(range(max(1, int(count))))

        all_results: list[StepResult] = []
        for idx, item in enumerate(iterations):
            loop_ctx = {**context, "loop.index": idx, "loop.item": item}
            if loop and loop.item_alias:
                loop_ctx[f"loop.{loop.item_alias}"] = item
            sub_record = ExecutionRecord(id=self._exec_id, scenario_id="", type="scenario")
            failed = await self._run_subgraph(self._scenario_children.get(step.step_id, []), loop_ctx, sub_record, env)
            for sr in sub_record.steps:
                sr.loop_index = idx
            all_results.extend(sub_record.steps)
            context.update({k: v for k, v in loop_ctx.items() if k.startswith("loop.")})
            if failed:
                await self._broadcast_step_status(step.step_id, "failed", error=failed.failure_reason)
                return [StepResult(step_id=step.step_id, api_id="", name=step.name, passed=False, error=failed.failure_reason), *all_results]

        await self._broadcast_step_status(step.step_id, "passed")
        return [StepResult(step_id=step.step_id, api_id="", name=step.name, passed=True), *all_results]

    def _resolve_template_ref(self, text: str, context: dict[str, Any], env_vars: dict[str, str]) -> Any:
        if not isinstance(text, str):
            return text
        m = re.fullmatch(r"\{\{\s*([^}]+)\s*\}\}", text)
        key = m.group(1).strip() if m else text.strip()
        if key.startswith("env."):
            return _get_path_value(env_vars, key[4:])
        # 支持 response/extracted/sql/steps 等点路径引用；扁平 key 仍自然兼容。
        if key.startswith("steps."):
            key = key[6:]
        if key in context:
            return context.get(key)
        return _get_path_value(context, key)

    def _render_templates_detailed(self, value: Any, context: dict[str, Any], env_vars: dict[str, str]) -> _RenderResult:
        """渲染模板并收集未解析引用，供保存前预览和真实执行复用。"""
        unresolved: list[str] = []
        if isinstance(value, str):
            full = re.fullmatch(r"\{\{\s*([^}]+)\s*\}\}", value)
            if full:
                ref = full.group(1).strip()
                resolved = self._resolve_template_ref(value, context, env_vars)
                if resolved is None:
                    unresolved.append(ref)
                    return _RenderResult(value=value, unresolved=unresolved)
                return _RenderResult(value=resolved)

            def repl(match: re.Match) -> str:
                ref = match.group(1).strip()
                resolved = self._resolve_template_ref(match.group(0), context, env_vars)
                if resolved is None:
                    unresolved.append(ref)
                    return match.group(0)
                return str(resolved)

            rendered = _TEMPLATE_REF_RE.sub(repl, value)
            # 兼容旧花括号变量 {token}/{BASE_URL}，但不把普通 JSON/CSS 花括号误认为错误。
            for env_k, env_v in env_vars.items():
                rendered = rendered.replace(f"{{{env_k}}}", str(env_v))
            for ctx_k, ctx_v in context.items():
                rendered = rendered.replace(f"{{{ctx_k}}}", str(ctx_v))
            return _RenderResult(value=rendered, unresolved=unresolved)
        if isinstance(value, dict):
            rendered: dict[str, Any] = {}
            for k, v in value.items():
                child = self._render_templates_detailed(v, context, env_vars)
                rendered[k] = child.value
                unresolved.extend(child.unresolved)
            return _RenderResult(value=rendered, unresolved=unresolved)
        if isinstance(value, list):
            rendered_list = []
            for item in value:
                child = self._render_templates_detailed(item, context, env_vars)
                rendered_list.append(child.value)
                unresolved.extend(child.unresolved)
            return _RenderResult(value=rendered_list, unresolved=unresolved)
        return _RenderResult(value=value)

    def _render_templates(self, value: Any, context: dict[str, Any], env_vars: dict[str, str]) -> Any:
        """渲染新 DSL 模板：{{step.var}} / {{env.KEY}} / {{loop.item}}。"""
        return self._render_templates_detailed(value, context, env_vars).value

    def _build_rendered_request(
        self,
        step: ScenarioStep,
        api: ApiDSL,
        context: dict[str, Any],
        env: _ExecEnv,
    ) -> _RenderedRequest:
        """组装最终请求。pre_script 在这里先执行，确保 token/signature 可用于本次请求。"""
        req = api.request
        method = req.method.value
        query_overrides, path_overrides, body_overrides, local_vars, legacy_params = _split_override_params(step.override_params)
        rendered_vars = self._render_templates_detailed(local_vars, context, env.variables)
        render_context = {**context, **(rendered_vars.value if isinstance(rendered_vars.value, dict) else {})}
        script_results: list[dict[str, Any]] = []
        unresolved_refs: list[str] = list(rendered_vars.unresolved)
        if step.pre_script:
            pre_ctx, pre_details = _run_step_script_detailed(step.pre_script, render_context, phase="pre")
            script_results.extend(pre_details)
            if pre_ctx:
                render_context = {**render_context, **pre_ctx}

        params: dict[str, Any] = {}
        for k, v in req.query_params.items():
            params[k] = render_context.get(k, v)
        for k, v in legacy_params.items():
            if k in params or ((req.body is None or not isinstance(req.body, dict)) and k not in _path_placeholder_names(req.url)):
                params[k] = v
        params.update(query_overrides)
        rendered = self._render_templates_detailed(params, render_context, env.variables)
        params = rendered.value
        unresolved_refs.extend(rendered.unresolved)

        body: Any = None
        if req.body is not None:
            if isinstance(req.body, dict):
                body = {**req.body}
                for k in list(body.keys()):
                    if k in render_context:
                        body[k] = render_context[k]
                for k, v in legacy_params.items():
                    if k not in params and k not in _path_placeholder_names(req.url):
                        body[k] = v
                body.update(body_overrides)
                rendered = self._render_templates_detailed(body, render_context, env.variables)
                body = rendered.value
                unresolved_refs.extend(rendered.unresolved)
            else:
                rendered = self._render_templates_detailed(req.body, render_context, env.variables)
                body = rendered.value
                unresolved_refs.extend(rendered.unresolved)
        elif body_overrides:
            # 结构化 Body 参数允许为无样例请求补齐 JSON body，避免用户只能退回完整 _body 手写。
            rendered = self._render_templates_detailed(body_overrides, render_context, env.variables)
            body = rendered.value
            unresolved_refs.extend(rendered.unresolved)
        if "_body" in step.override_params:
            raw_body = step.override_params.get("_body")
            if step.override_params.get("_body_type") == "json" and isinstance(raw_body, str):
                try:
                    raw_body = json.loads(raw_body)
                except Exception:
                    logger.warning("Failed to parse _body as JSON for step_id=%s", step.step_id)
            rendered = self._render_templates_detailed(raw_body, render_context, env.variables)
            body = rendered.value
            unresolved_refs.extend(rendered.unresolved)

        url = req.url
        if api.base_url_override:
            from urllib.parse import urlparse
            orig = urlparse(url)
            url = api.base_url_override.rstrip("/") + orig.path
            if orig.query:
                url += "?" + orig.query
        url = url.split("?")[0]
        rendered = self._render_templates_detailed(url, render_context, env.variables)
        url = rendered.value
        unresolved_refs.extend(rendered.unresolved)
        path_names = _path_placeholder_names(url)
        path_params = {name: render_context.get(name) for name in path_names if name in render_context}
        path_params.update({k: v for k, v in legacy_params.items() if k in path_names})
        path_params.update(path_overrides)
        rendered = self._render_templates_detailed(path_params, render_context, env.variables)
        path_params = rendered.value if isinstance(rendered.value, dict) else {}
        unresolved_refs.extend(rendered.unresolved)
        url, path_unresolved = _replace_path_params(url, path_params)
        unresolved_refs.extend(path_unresolved)

        header_sources: dict[str, str] = {}
        merged_headers: dict[str, Any] = {}
        for hk, hv in (req.headers or {}).items():
            merged_headers[hk] = hv
            header_sources[hk.lower()] = "api"
        for hk, hv in (env.headers or {}).items():
            merged_headers[hk] = hv
            header_sources[hk.lower()] = "environment"
        for hk, hv in (step.override_headers or {}).items():
            merged_headers[hk] = hv
            header_sources[hk.lower()] = "manual"
        rendered = self._render_templates_detailed(merged_headers, render_context, env.variables)
        headers = rendered.value
        unresolved_refs.extend(rendered.unresolved)

        auth, auth_unresolved = _render_step_auth(self, step.auth, render_context, env.variables)
        unresolved_refs.extend(auth_unresolved)
        auth_sources: dict[str, Any] = {"type": auth.get("type", ""), "applied": [], "skipped": []}
        auth_headers = _build_auth_headers(auth)
        if auth_headers:
            for hk, hv in auth_headers.items():
                if hk.lower() not in {k.lower() for k in env.headers}:
                    headers[hk] = hv
                    header_sources[hk.lower()] = "step_auth"
                    auth_sources["applied"].append({"target": "header", "key": hk, "source": "step_auth"})
                else:
                    auth_sources["skipped"].append({"target": "header", "key": hk, "reason": "environment_header_priority"})
        auth_query = _build_auth_query(auth)
        if auth_query:
            params.update(auth_query)
            for qk in auth_query:
                auth_sources["applied"].append({"target": "query", "key": qk, "source": "step_auth"})

        return _RenderedRequest(
            request_sent={
                "method": method, "url": url,
                "params": params or None, "body": body, "headers": headers,
                "unresolved_refs": sorted(set(unresolved_refs)),
                "auth_sources": auth_sources,
                "header_sources": header_sources,
            },
            context=render_context,
            script_results=script_results,
            unresolved_refs=sorted(set(unresolved_refs)),
            auth_sources=auth_sources,
            header_sources=header_sources,
        )

    def preview_step_request(
        self,
        step: ScenarioStep,
        api: ApiDSL,
        context: dict[str, Any] | None = None,
        env: _ExecEnv | None = None,
    ) -> dict[str, Any]:
        rendered = self._build_rendered_request(step, api, context or {}, env or _ExecEnv())
        return {
            "request": rendered.request_sent,
            "context": rendered.context,
            "script_results": rendered.script_results,
            "unresolved_refs": rendered.unresolved_refs,
            "auth_sources": rendered.auth_sources,
            "header_sources": rendered.header_sources,
            "ok": not rendered.unresolved_refs,
        }

    async def _inject_factory_data(
        self, step: ScenarioStep, context: dict[str, Any]
    ) -> dict[str, Any]:
        from data_factory.factory import DataFactory
        doc = await self._tmpl_col.find_one({"id": step.data_template_id})
        if not doc:
            # 模板不存在 → 警告后原样返回 context，不阻塞步骤执行
            logger.warning("DataTemplate {} not found", step.data_template_id)
            return context
        template  = DataTemplate(**doc)
        factory   = DataFactory(self._redis)  # type: ignore[arg-type]
        # generate() 是 CPU 密集型同步调用（循环字段、调用 random/faker），
        # 在异步上下文中通过 to_thread 放入线程池执行，避免阻塞事件循环
        data_list = await asyncio.to_thread(factory.generate, template, context, count=1)
        if data_list:
            # 生成成功 → 将生成的数据合并到 context
            return {**context, **data_list[0]}
        # 生成结果为空 → 原样返回 context
        return context

    # ── 单步执行（含指数退避重试）─────────────────────────

    async def _execute_step(
        self,
        step: ScenarioStep,
        context: dict[str, Any],
        env: _ExecEnv,
        api: ApiDSL | None = None,
    ) -> StepResult:
        if api is None:
            # 从 DB 加载 API 定义（自动缓存由调用方负责时跳过）
            doc = await self._api_col.find_one({"id": step.api_id})
            if not doc:
                # API 不存在 → 直接返回失败，不进入重试循环
                # P0-2: 广播 failed（API 不存在）
                await self._broadcast_step_status(step.step_id, "failed", error=f"API {step.api_id} not found")
                return StepResult(
                    step_id=step.step_id, api_id=step.api_id, name=step.name,
                    passed=False, error=f"API {step.api_id} not found",
                )
            api = ApiDSL(**doc)

        # 执行环境：env_base_url 优先级低于 API 自身的 base_url_override
        if env.base_url and not api.base_url_override:
            api.base_url_override = env.base_url

        # P0-2: 广播 running 状态（前端节点显示蓝色运行中动画）
        await self._broadcast_step_status(step.step_id, "running")
        # 防御：retry 可能为负数，保护 range 非空，确保循环体至少执行一次
        retry = max(0, step.retry)
        last: StepResult | None = None
        for attempt in range(1, retry + 2):
            last = await self._http_call(step, api, context, env, attempt)
            if last.passed:
                # 步骤通过 → 立即返回，不再重试
                # P0-2: 广播 passed
                await self._broadcast_step_status(step.step_id, "passed", latency_ms=last.latency_ms)
                return last
            if attempt <= retry:
                # 指数退避延迟：第1次重试 wait=retry_delay_s*1, 第2次 retry_delay_s*2, 第3次 retry_delay_s*4
                delay = step.retry_delay_s * (2 ** (attempt - 1))
                logger.warning(
                    "Step {} attempt {}/{} failed, retry in {:.1f}s",
                    step.step_id, attempt, retry + 1, delay,
                )
                await asyncio.sleep(delay)
            # attempt > retry：最后一次尝试 → 循环结束，返回 last（失败结果）

        # P0-2: 广播 failed（重试耗尽）
        if last:
            await self._broadcast_step_status(step.step_id, "failed", latency_ms=last.latency_ms, error=last.error)
        return last  # type: ignore[return-value]

    async def _http_call(
        self,
        step: ScenarioStep,
        api: ApiDSL,
        context: dict[str, Any],
        env: _ExecEnv,
        attempt: int = 1,
    ) -> StepResult:
        req    = api.request
        method = req.method.value
        sql_results: dict[str, Any] = {}
        sql_failures: list[str] = []
        script_results: list[dict[str, Any]] = []

        if step.pre_sql:
            pre_sql_ctx = await _run_sql_queries(self._db, api.project_id, step.pre_sql, context)
            if pre_sql_ctx:
                pre_failures = pre_sql_ctx.pop("_sql_failures", [])
                sql_results.update({f"pre.{k}": v for k, v in pre_sql_ctx.get("sql", {}).items()})
                context = {**context, **pre_sql_ctx}
                if pre_failures:
                    # pre_sql 在请求组装前运行；失败且要求阻断时，不再发出 HTTP 请求，避免使用错误上下文继续污染流程。
                    return StepResult(
                        step_id=step.step_id, api_id=step.api_id, name=step.name,
                        request_sent={"method": method, "url": req.url},
                        passed=False,
                        error="SQL pre_sql failed: " + "; ".join(pre_failures),
                        sql_results=sql_results,
                        script_results=script_results,
                        attempt=attempt,
                    )

        # 统一组装请求：pre_script 必须先执行，产出的 token/signature 才能参与本次请求渲染。
        rendered_request = self._build_rendered_request(step, api, context, env)
        request_sent = rendered_request.request_sent
        context = rendered_request.context
        script_results = rendered_request.script_results
        url = request_sent["url"]
        params = request_sent.get("params")
        body = request_sent.get("body")
        headers = request_sent.get("headers") or {}

        # 日志记录（脱敏）
        log_body = _sanitize(body) if isinstance(body, (dict, list)) else body
        logger.debug(
            "HTTP {} {} attempt={} body={}",
            method, url, attempt,
            json.dumps(log_body, default=str)[:300],
        )

        start = asyncio.get_event_loop().time()
        try:
            async with httpx.AsyncClient(timeout=float(step.timeout_s)) as client:
                resp = await client.request(
                    method=method,
                    url=url,
                    params=params if params else None,
                    # 转为普通 dict 避免 dict 子类在 httpx 内部序列化时行为不一致
                    json=dict(body) if isinstance(body, dict) else None,
                    content=body.encode() if isinstance(body, str) else None,
                    headers=headers,
                )
            latency_ms = int((asyncio.get_event_loop().time() - start) * 1000)

            # ── 解析响应体 ──
            try:
                resp_body = resp.json()
            except Exception:
                # JSON 解析失败（可能是纯文本或非 JSON 格式）→ 使用原始文本
                resp_body = resp.text

            # ── 执行断言 ──
            assert_results, all_passed, failure_summary = run_asserts(
                api, resp.status_code, resp_body,
                latency_ms=latency_ms,
                response_headers=dict(resp.headers),
            )

            # ── 变量提取 ──
            extracted: dict[str, Any] = {}
            for var_name, jpath in step.extract.items():
                if isinstance(jpath, dict) and jpath.get("source") == "sql":
                    # SQL 提取：允许 extract.foo = {source: sql, sql_ref/sql_text, field/path}
                    # 这样 DB 校验值可以进入后续步骤变量链，而不是只能作为断言结果展示。
                    query = jpath.get("sql_query") or jpath
                    sql_name = query.get("target_var") or query.get("name") or var_name
                    sql_context_for_extract = {**context, "response": resp_body, "extracted": extracted}
                    try:
                        sql_result = await SqlRuntimeService(self._db).run_ref(api.project_id, query, sql_context_for_extract)
                    except Exception as e:
                        # SQL extract 属于增强提取，异常也要进入结果面板，并按 fail_on_error 决定是否阻断。
                        sql_result = _sql_exception_result(e, query)
                    sql_results[f"extract.{sql_name}"] = {**sql_result, "name": sql_name, "phase": "extract"}
                    if sql_result.get("error"):
                        logger.warning("SQL extract '{}' failed: {}", var_name, sql_result.get("error"))
                        if jpath.get("fail_on_error", True):
                            sql_failures.append(f"extract.{var_name}: {sql_result.get('message') or sql_result.get('error')}")
                        val = None
                    else:
                        field_path = jpath.get("path") or jpath.get("field") or f"sql.{sql_name}.scalar"
                        val = _sql_actual({"sql": {sql_name: sql_result}}, field_path)
                        if val is None:
                            warning = f"extract.{var_name}: SQL field '{field_path}' not found"
                            logger.warning(warning)
                            if jpath.get("fail_on_error", False):
                                sql_failures.append(warning)
                else:
                    val = extract_jsonpath(resp_body, str(jpath))
                if val is not None:
                    # 提取成功 → 存入 extracted 字典，后续合并到 context
                    extracted[var_name] = val
                else:
                    # 提取为 None（字段不存在或路径不匹配）→ 记录 debug 日志
                    logger.debug("Extract '{}' from '{}' → None", var_name, jpath)

            sql_context = {**context, "response": resp_body, "extracted": extracted}
            if step.post_sql:
                post_sql_ctx = await _run_sql_queries(self._db, api.project_id, step.post_sql, sql_context)
                if post_sql_ctx:
                    post_failures = post_sql_ctx.pop("_sql_failures", [])
                    sql_results.update({f"post.{k}": v for k, v in post_sql_ctx.get("sql", {}).items()})
                    sql_failures.extend([f"post_sql {item}" for item in post_failures])
                    sql_context = {**sql_context, **post_sql_ctx}
                    extracted.update({f"sql.{k}": summarize_sql_result(v) for k, v in post_sql_ctx.get("sql", {}).items()})

            # P0-1: 执行 post_script，将产出变量合并到 extracted（与 extract 同级，供后续步骤消费）。
            # post_script 可读取响应体（response=resp_body）和当前 context，常用于响应后处理、
            # 衍生变量计算（如 token 解析、签名生成、聚合统计）。
            if step.post_script:
                post_ctx, post_details = _run_step_script_detailed(step.post_script, context, response=resp_body, phase="post")
                script_results.extend(post_details)
                if post_ctx:
                    extracted.update(post_ctx)

            # P0-1: 执行 step.assertions（步骤断言，区别于 API 自身的 api.asserts）。
            # 步骤断言用于场景内对单次响应做额外校验（如 Postman Tests Tab），
            # 任一失败则标记该步骤失败（与 api.asserts 取 AND 语义）。
            # 不合法的断言规则被跳过而非报错，保证兼容旧数据。
            step_assert_results: list[dict[str, Any]] = []
            step_assert_passed = True
            for a in step.assertions or []:
                if not isinstance(a, dict):
                    continue
                field_path = a.get("path") or a.get("field") or ""
                op = a.get("operator", "eq")
                expected = a.get("expected")
                sql_failed = False
                # source 决定取值来源：status→状态码，header→响应头，其余→响应体
                source = a.get("source", "response")
                # 自动检测特殊字段（与 run_asserts 行为保持一致），避免 source 默认 "response" 导致
                # status_code 从响应体 jsonpath 取值返回 None（如健康检查接口返回 {"status":"ok"} 不含 status_code 字段）
                if field_path == "status_code":
                    source = "status"
                elif field_path == "$response_time_ms":
                    source = "performance"
                if op == "response_time_lt" or field_path == "$response_time_ms" or source == "performance":
                    # 性能断言直接使用本次请求耗时，避免用户必须从响应体里构造虚拟字段。
                    actual = latency_ms
                    if not field_path:
                        field_path = "$response_time_ms"
                elif source == "status":
                    actual: Any = resp.status_code
                elif source == "header":
                    actual = None
                    field_lower = field_path.lower()
                    for hk, hv in resp.headers.items():
                        if hk.lower() == field_lower:
                            actual = hv
                            break
                elif source == "sql":
                    query = a.get("sql_query") or a
                    sql_name = query.get("target_var") or query.get("name") or "assert_sql"
                    if "sql" not in sql_context or sql_name not in sql_context.get("sql", {}):
                        try:
                            sql_result = await SqlRuntimeService(self._db).run_ref(api.project_id, query, sql_context)
                        except Exception as e:
                            # SQL 断言失败要展示 SQL 名称与错误，不能让整个步骤落入非预期异常。
                            sql_result = _sql_exception_result(e, query)
                        sql_context.setdefault("sql", {})[sql_name] = sql_result
                    sql_results[f"assert.{sql_name}"] = {**sql_context.get("sql", {}).get(sql_name, {}), "name": sql_name, "phase": "assert"}
                    sql_failed = bool(sql_context.get("sql", {}).get(sql_name, {}).get("error"))
                    actual = _sql_actual({"sql": sql_context.get("sql", {})}, field_path or f"sql.{sql_name}.scalar")
                else:
                    actual = extract_jsonpath(resp_body, field_path)
                apass = (not sql_failed) and _eval_single_assert(op, actual, expected)
                step_assert_results.append({
                    "field": field_path, "operator": op,
                    "expected": expected, "actual": actual, "passed": apass,
                    "source": "sql" if source == "sql" else "step",  # 保留历史语义：步骤级断言统一归为 step，避免现有统计/测试断裂。
                    "origin_source": source,  # 新增真实取值来源，供前端解释 response/status/header/performance。
                    "sql_name": sql_name if source == "sql" else "",
                    "sql_error": sql_context.get("sql", {}).get(sql_name, {}).get("error", "") if source == "sql" else "",
                    "error": sql_context.get("sql", {}).get(sql_name, {}).get("error", "") if source == "sql" and sql_failed else "",
                })
                if not apass:
                    step_assert_passed = False
                    failure_summary += f"; step assert {field_path} {op} {expected!r} failed"
            # 合并步骤断言结果到 assert_results，all_passed 取 AND
            assert_results.extend(step_assert_results)
            all_passed = all_passed and step_assert_passed
            if sql_failures:
                all_passed = False
                failure_summary += "; " + "; ".join(sql_failures)

            return StepResult(
                step_id=step.step_id, api_id=step.api_id, name=step.name,
                request_sent=request_sent,
                response_received={
                    "status_code": resp.status_code,
                    "body":        resp_body,
                    "latency_ms":  latency_ms,
                    "headers":     dict(resp.headers),
                },
                assert_results=assert_results,
                passed=all_passed,
                latency_ms=latency_ms,
                extracted_vars=extracted,
                sql_results=sql_results,
                script_results=script_results,
                error=failure_summary if not all_passed else "",
                attempt=attempt,
            )

        except httpx.TimeoutException:
            # 超时：请求未在 step.timeout_s 内完成 → 可重试错误
            latency_ms = int((asyncio.get_event_loop().time() - start) * 1000)
            msg = f"Timeout after {step.timeout_s}s"
            logger.warning("Step {} {}", step.step_id, msg)
            return StepResult(
                step_id=step.step_id, api_id=step.api_id, name=step.name,
                request_sent=request_sent, passed=False,
                latency_ms=latency_ms, error=msg, sql_results=sql_results, script_results=script_results, attempt=attempt,
            )
        except httpx.ConnectError as e:
            # ConnectError: 目标服务不可达（网络不通/端口未开/DNS解析失败等），通常是环境问题而非临时故障
            latency_ms = int((asyncio.get_event_loop().time() - start) * 1000)
            url_info = f"{method} {url}"
            msg = (
                f"ConnectError: 目标服务不可达\n"
                f"  URL: {url_info}\n"
                f"  原因: {e}\n"
                f"  建议: 检查目标服务是否正常运行、网络是否可达、URL 是否正确"
            )
            logger.error("Step {} {}", step.step_id, msg)
            return StepResult(
                step_id=step.step_id, api_id=step.api_id, name=step.name,
                request_sent=request_sent, passed=False,
                latency_ms=latency_ms, error=msg, sql_results=sql_results, script_results=script_results, attempt=attempt,
            )
        except httpx.RequestError as e:
            # RequestError: 其他 httpx 请求错误（如无效URL、SSL错误、重定向循环等）
            latency_ms = int((asyncio.get_event_loop().time() - start) * 1000)
            msg = f"RequestError: {type(e).__name__}: {e}"
            logger.error("Step {} {}", step.step_id, msg)
            return StepResult(
                step_id=step.step_id, api_id=step.api_id, name=step.name,
                request_sent=request_sent, passed=False,
                latency_ms=latency_ms, error=msg, sql_results=sql_results, script_results=script_results, attempt=attempt,
            )
        except Exception as e:
            # 未预期异常（如 JSON 序列化错误等）→ 不重试，直接失败
            latency_ms = int((asyncio.get_event_loop().time() - start) * 1000)
            msg = f"Unexpected: {type(e).__name__}: {e}"
            logger.error("Step {} {}", step.step_id, msg)
            return StepResult(
                step_id=step.step_id, api_id=step.api_id, name=step.name,
                request_sent=request_sent, passed=False,
                latency_ms=latency_ms, error=msg, sql_results=sql_results, script_results=script_results, attempt=attempt,
            )

    # ── 拓扑排序（Kahn）──────────────────────────────────

    @staticmethod
    def _topo_sort(steps: list[ScenarioStep]) -> list[list[ScenarioStep]]:
        step_map = {s.step_id: s for s in steps}
        in_degree = {s.step_id: 0 for s in steps}
        dependents: dict[str, list[str]] = {s.step_id: [] for s in steps}

        for s in steps:
            for dep in s.depends_on:
                if dep in ("start", "end"):
                    continue
                if dep not in step_map:
                    # 未知依赖必须阻断执行，避免拼错 step_id 时被当作无依赖提前运行。
                    raise ValueError(f"unknown step dependency: {dep}")
                in_degree[s.step_id] += 1
                dependents[dep].append(s.step_id)

        groups: list[list[ScenarioStep]] = []
        # 初始就绪：入度为 0 的所有步骤（无依赖）
        ready = [sid for sid, deg in in_degree.items() if deg == 0]

        while ready:
            # 当前波次：所有就绪步骤可并发执行
            groups.append([step_map[sid] for sid in ready])
            next_ready: list[str] = []
            for sid in ready:
                for child in dependents[sid]:
                    # 子步骤入度 -1（当前步骤完成后该依赖被满足）
                    in_degree[child] -= 1
                    if in_degree[child] == 0:
                        # 子步骤所有依赖已满足 → 下一个波次就绪
                        next_ready.append(child)
            ready = next_ready

        if sum(len(g) for g in groups) != len(steps):
            # 排序后的步骤数 < 总步骤数 → 存在循环依赖，部分步骤永远无法入度=0
            raise ValueError("Circular dependency detected in scenario steps")
        return groups

    # ── 持久化 ────────────────────────────────────────────

    async def _save_record(self, record: ExecutionRecord) -> None:
        # 执行记录丢失意味着用户看不到结果，必须传播错误让上层感知
        try:
            await self._exec_col.insert_one(record.model_dump())
        except Exception as e:
            logger.error("Failed to save execution record {}: {}", record.id, e)
            raise

    # Phase 4: 失败执行入队等待 AI 诊断
    async def _enqueue_diagnosis(self, record: ExecutionRecord) -> None:
        """将失败的执行记录推入 Redis 队列，由 FailureDiagnoserService 异步消费诊断"""
        if not self._redis:
            return
        try:
            job_id = f"diagnose:{record.id}"
            payload = {
                "execution_id": record.id,
                "project_id": record.project_id or "default",
                "job_id": job_id,
                "status": "queued",
                "fail_count": 0,
            }
            await self._redis.rpush(DIAGNOSE_FAILURE_QUEUE, json.dumps(payload, ensure_ascii=False))
            await AiJobService(self._db).mark_queued(
                job_id=job_id,
                type="diagnose",
                project_id=record.project_id or "default",
                source="dag_engine",
                target_ids=[record.id],
                queue_key=DIAGNOSE_FAILURE_QUEUE,
                payload=payload,
            )
            # 同时标记诊断状态为 queued，前端可据此展示"诊断中"状态
            await self._exec_col.update_one(
                {"id": record.id},
                {"$set": {"diagnosis_status": "queued"}},
            )
            logger.info("Enqueued diagnosis for execution {}", record.id)
        except Exception as e:
            logger.warning("Failed to enqueue diagnosis for {}: {}", record.id, e)
