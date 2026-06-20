from __future__ import annotations

from loguru import logger

import asyncio
import hashlib
import json
import random
import re
import secrets
from datetime import datetime, timedelta, timezone
from string import Template
from typing import Any
from urllib.parse import parse_qsl, urlparse
from uuid import uuid4

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from har_parser.parser import _should_filter_by_domain
from models.dsl import ApiDSL, BodyType, HttpMethod, ParseStatus, RequestDSL, ResponseDSL
from models.mock_service import (
    MockCallLog,
    MockRoute,
    MockService,
    TrafficRecord,
    TrafficRule,
    TrafficSource,
)
from services.sql_runtime_service import SqlRuntimeService, summarize_sql_result


SENSITIVE_KEYS = {"authorization", "cookie", "set-cookie", "token", "password", "secret", "access_key", "mock_key", "x-mock-key"}
MAX_LOG_VALUE_LEN = 2000


def _now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def _slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned or "mock-service"


def generate_access_key() -> str:
    return secrets.token_urlsafe(24)


def _normalize_path(path: str) -> str:
    if not path:
        return "/"
    return path if path.startswith("/") else f"/{path}"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _limit_value(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= MAX_LOG_VALUE_LEN else f"{value[:MAX_LOG_VALUE_LEN]}..."
    if isinstance(value, list):
        return [_limit_value(v) for v in value[:50]]
    if isinstance(value, dict):
        return {k: _limit_value(v) for k, v in list(value.items())[:80]}
    return value


def _mask_sensitive(data: Any) -> Any:
    if isinstance(data, dict):
        masked: dict[str, Any] = {}
        for key, value in data.items():
            if str(key).lower() in SENSITIVE_KEYS:
                masked[key] = "***"
            else:
                masked[key] = _mask_sensitive(value)
        return _limit_value(masked)
    if isinstance(data, list):
        return [_mask_sensitive(v) for v in data[:50]]
    return _limit_value(data)


def _get_path_value(data: Any, path: str) -> Any:
    if not path:
        return None
    cur = data
    normalized = path.strip().replace("$.", "").replace("[", ".").replace("]", "")
    for part in normalized.split("."):
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


def _set_path_value(data: Any, path: str, value: Any) -> Any:
    if not isinstance(data, dict):
        return data
    normalized = path.strip().replace("$.", "").replace("[", ".").replace("]", "")
    parts = [part for part in normalized.split(".") if part]
    cur = data
    for part in parts[:-1]:
        if not isinstance(cur, dict):
            return data
        cur = cur.setdefault(part, {})
    if parts and isinstance(cur, dict):
        cur[parts[-1]] = value
    return data


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    op = (operator or "equals").lower()
    if op == "exists":
        return actual is not None
    if op == "not_exists":
        return actual is None
    if op == "in":
        values = expected if isinstance(expected, list) else [v.strip() for v in str(expected).split(",")]
        return str(actual) in {str(v) for v in values}
    if actual is None:
        return False
    if op == "contains":
        return str(expected) in str(actual)
    if op == "regex":
        try:
            return re.search(str(expected), str(actual)) is not None
        except re.error:
            return False
    if op in {"gt", "gte", "lt", "lte"}:
        try:
            left = float(actual)
            right = float(expected)
        except (TypeError, ValueError):
            return False
        return {
            "gt": left > right,
            "gte": left >= right,
            "lt": left < right,
            "lte": left <= right,
        }[op]
    return str(actual) == str(expected)


def _legacy_match_to_conditions(match: dict[str, Any]) -> list[dict[str, Any]]:
    conditions = list(match.get("conditions") or [])
    for section, target in (("query", "query"), ("headers", "header"), ("cookies", "cookie")):
        for key, value in (match.get(section) or {}).items():
            conditions.append({"target": target, "key": key, "operator": "in" if isinstance(value, list) else "equals", "value": value})
    for key, value in (match.get("body_fields") or {}).items():
        conditions.append({"target": "body_field", "key": key, "operator": "equals", "value": value})
    for key, value in (match.get("body_jsonpath") or {}).items():
        conditions.append({"target": "jsonpath", "key": key, "operator": "equals", "value": value})
    return conditions


def _condition_actual(condition: dict[str, Any], req: dict[str, Any]) -> Any:
    target = str(condition.get("target") or "query")
    key = str(condition.get("key") or "")
    if target == "method":
        return str(req.get("method") or "").upper()
    if target == "path":
        return req.get("path") or "/"
    if target in {"path_param", "path_params"}:
        return (req.get("path_params") or {}).get(key)
    if target == "query":
        return (req.get("query") or {}).get(key)
    if target == "header":
        headers = {str(k).lower(): v for k, v in (req.get("headers") or {}).items()}
        return headers.get(key.lower())
    if target == "cookie":
        return (req.get("cookies") or {}).get(key)
    if target == "body_field":
        body = req.get("body")
        return body.get(key) if isinstance(body, dict) else None
    if target == "jsonpath":
        return _get_path_value(req.get("body"), key)
    if target == "host":
        return urlparse(req.get("url") or "").netloc
    if target == "status_code":
        return req.get("status_code")
    return None


def _path_matches(route_path: str, req_path: str) -> bool:
    route_path = _normalize_path(route_path)
    req_path = _normalize_path(req_path)
    if route_path.endswith("*"):
        return req_path.startswith(route_path[:-1])
    if "{" in route_path and "}" in route_path:
        pattern = re.sub(r"\{[^/{}]+\}", r"[^/]+", re.escape(route_path).replace(r"\{", "{").replace(r"\}", "}"))
        return re.fullmatch(pattern, req_path) is not None
    return route_path == req_path


def _render_template(template: str, req: dict[str, Any], render_context: dict[str, Any] | None = None) -> str:
    if not template:
        return ""

    def replace_braces(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        return str(_template_value(expr, req, render_context or {}) or "")

    rendered = re.sub(r"\{\{\s*([^}]+)\s*\}\}", replace_braces, template)
    flat = {
        "method": req.get("method", ""),
        "path": req.get("path", ""),
        "timestamp": _now().isoformat(),
    }
    try:
        return Template(rendered).safe_substitute(flat)
    except Exception:
        return rendered


def _template_value(expr: str, req: dict[str, Any], render_context: dict[str, Any] | None = None) -> Any:
    ctx = render_context or {}
    if expr.startswith("query."):
        return (req.get("query") or {}).get(expr[6:])
    if expr.startswith("headers."):
        headers = {str(k).lower(): v for k, v in (req.get("headers") or {}).items()}
        return headers.get(expr[8:].lower())
    if expr.startswith("body."):
        return _get_path_value(req.get("body"), expr[5:])
    if expr.startswith("sql."):
        return _get_path_value(ctx.get("sql"), expr[4:])
    if expr.startswith("mock."):
        return _get_path_value(ctx.get("mock"), expr[5:])
    if expr.startswith("path_params."):
        return (ctx.get("path_params") or {}).get(expr[12:])
    if expr == "method":
        return req.get("method")
    if expr == "path":
        return req.get("path")
    if expr == "timestamp":
        return _now().isoformat()
    found = _get_path_value(ctx, expr)
    if found is not None:
        return found
    return ""


def _response_body(response: dict[str, Any], req: dict[str, Any], render_context: dict[str, Any] | None = None) -> tuple[Any, str]:
    body_type = str(response.get("body_type") or "").lower()
    template = response.get("body_template") or ""
    body = response.get("body", {"message": "mock response"})
    if response.get("source") == "mock_data" and (render_context or {}).get("mock") is not None and not template:
        return (render_context or {}).get("mock"), "json"
    if template:
        rendered = _render_template(template, req, render_context)
        if body_type == "json":
            try:
                return json.loads(rendered), "json"
            except json.JSONDecodeError:
                return rendered, "text"
        return rendered, body_type or "text"
    if body_type:
        return body, body_type
    if isinstance(body, (dict, list)):
        return body, "json"
    if body in (None, ""):
        return None, "empty"
    return body, "text"


def _path_params(route_path: str, req_path: str) -> dict[str, str]:
    route_path = _normalize_path(route_path)
    req_path = _normalize_path(req_path)
    if "{" not in route_path:
        return {}
    params: dict[str, str] = {}
    route_parts = [p for p in route_path.split("/") if p]
    req_parts = [p for p in req_path.split("/") if p]
    for idx, part in enumerate(route_parts):
        if idx >= len(req_parts):
            break
        if part.startswith("{") and part.endswith("}"):
            params[part[1:-1]] = req_parts[idx]
    return params


def _set_nested_value(target: dict[str, Any], path: str, value: Any) -> None:
    parts = [p for p in str(path or "").split(".") if p]
    if not parts:
        return
    cur = target
    for part in parts[:-1]:
        next_value = cur.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            cur[part] = next_value
        cur = next_value
    cur[parts[-1]] = value


def _mock_field_value(field: dict[str, Any], context: dict[str, Any]) -> Any:
    if "fixed_value" in field:
        return field.get("fixed_value")
    enum_values = field.get("enum_values") or field.get("values") or []
    if isinstance(enum_values, str):
        enum_values = [v.strip() for v in enum_values.split(",") if v.strip()]
    if enum_values:
        return random.choice(enum_values)
    source_path = field.get("source_path") or field.get("from")
    if source_path:
        found = _get_path_value(context, str(source_path))
        if found is not None:
            return found
    faker_method = str(field.get("faker_method") or field.get("type") or "string").lower()
    if faker_method in {"int", "integer", "number"}:
        return random.randint(int(field.get("min", 1)), int(field.get("max", 9999)))
    if faker_method in {"float", "decimal"}:
        return round(random.uniform(float(field.get("min", 0)), float(field.get("max", 1000))), 2)
    if faker_method in {"bool", "boolean"}:
        return random.choice([True, False])
    if faker_method in {"email"}:
        return f"user{random.randint(1000, 9999)}@example.com"
    if faker_method in {"phone", "phone_number"}:
        return f"138{random.randint(10000000, 99999999)}"
    if faker_method in {"timestamp", "datetime"}:
        return _now().isoformat()
    return f"mock_{field.get('name') or random.randint(1000, 9999)}"


def _generate_mock_data(config: dict[str, Any], context: dict[str, Any]) -> Any:
    fields = list(config.get("fields") or config.get("schema") or [])
    count = max(1, min(_safe_int(config.get("count"), 1), 200))

    def make_one() -> dict[str, Any]:
        item: dict[str, Any] = {}
        for field in fields:
            if not isinstance(field, dict):
                continue
            name = field.get("name") or field.get("path")
            if not name:
                continue
            _set_nested_value(item, str(name), _mock_field_value(field, context))
        return item

    if not fields:
        return {"id": str(uuid4()), "created_at": _now().isoformat()}
    data = [make_one() for _ in range(count)]
    return data if config.get("array", count > 1) else data[0]


def _normalize_headers(headers: dict[str, Any], body_type: str) -> dict[str, str]:
    normalized = {str(k): str(v) for k, v in (headers or {}).items()}
    lower = {k.lower() for k in normalized}
    if "content-type" not in lower and body_type == "json":
        normalized["content-type"] = "application/json"
    if "content-type" not in lower and body_type == "text":
        normalized["content-type"] = "text/plain; charset=utf-8"
    return normalized


def _apply_patch_list(data: dict[str, Any], patch_list: list[dict[str, Any]]) -> dict[str, Any]:
    result = json.loads(json.dumps(data, default=str))
    for patch in patch_list:
        target = patch.get("target")
        key = patch.get("key") or ""
        value = patch.get("value")
        if target in {"header", "headers"}:
            headers = result.setdefault("headers", {})
            if value is None:
                headers.pop(key, None)
            else:
                headers[key] = value
        elif target == "query":
            query = result.setdefault("query", {})
            if value is None:
                query.pop(key, None)
            else:
                query[key] = value
        elif target in {"body_jsonpath", "jsonpath"}:
            result["body"] = _set_path_value(result.get("body") or {}, key, value)
        elif target in {"body", "response_body"}:
            result["body"] = value
        elif target == "status_code":
            result["status_code"] = _safe_int(value, result.get("status_code") or 200)
    return result


class MockServiceManager:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def list_services(self, project_id: str) -> list[dict[str, Any]]:
        services = await self.db["mock_services"].find({"project_id": project_id}, {"_id": 0}).sort("updated_at", -1).to_list(200)
        for svc in services:
            svc["route_count"] = await self.db["mock_routes"].count_documents({"service_id": svc["id"]})
            svc["error_count"] = await self.db["mock_call_logs"].count_documents({"service_id": svc["id"], "status_code": {"$gte": 500}})
            last = await self.db["mock_call_logs"].find_one({"service_id": svc["id"]}, {"_id": 0}, sort=[("created_at", -1)])
            svc["last_called_at"] = last.get("created_at") if last else svc.get("last_called_at")
            svc["last_call_at"] = svc["last_called_at"]
        return services

    async def get_service(self, service_id: str) -> dict[str, Any] | None:
        return await self.db["mock_services"].find_one({"id": service_id}, {"_id": 0})

    def _validate_service_public_access(self, data: dict[str, Any]) -> dict[str, Any]:
        public_enabled = bool(data.get("public_enabled", False))
        if public_enabled and not str(data.get("access_key") or "").strip():
            raise HTTPException(422, "Public mock service requires access_key")
        return data

    async def create_service(self, data: dict[str, Any], project_id: str, username: str = "") -> dict[str, Any]:
        name = data.get("name") or "Mock Service"
        slug = _slugify(data.get("slug") or name)
        if await self.db["mock_services"].find_one({"project_id": project_id, "slug": slug}):
            raise HTTPException(409, "Mock service slug already exists")
        payload = self._validate_service_public_access({**data, "public_enabled": bool(data.get("public_enabled", False))})
        svc = MockService(
            **{
                **payload,
                "project_id": project_id,
                "slug": slug,
                "created_by": username,
                "updated_by": username,
            }
        )
        await self.db["mock_services"].insert_one(svc.model_dump())
        return svc.model_dump()

    async def update_service(self, service_id: str, data: dict[str, Any], username: str = "") -> dict[str, Any] | None:
        existing = await self.get_service(service_id)
        if not existing:
            return None
        update = {k: v for k, v in data.items() if k not in {"id", "project_id", "created_at", "created_by", "route_count", "error_count"}}
        merged = {**existing, **update}
        self._validate_service_public_access(merged)
        if "slug" in update:
            update["slug"] = _slugify(update["slug"])
            dup = await self.db["mock_services"].find_one({"project_id": existing["project_id"], "slug": update["slug"], "id": {"$ne": service_id}})
            if dup:
                raise HTTPException(409, "Mock service slug already exists")
        update["updated_by"] = username
        update["updated_at"] = _now()
        await self.db["mock_services"].update_one({"id": service_id}, {"$set": update})
        return await self.get_service(service_id)

    async def rotate_access_key(self, service_id: str, username: str = "") -> dict[str, Any] | None:
        existing = await self.get_service(service_id)
        if not existing:
            return None
        await self.db["mock_services"].update_one(
            {"id": service_id},
            {"$set": {"access_key": generate_access_key(), "updated_by": username, "updated_at": _now()}},
        )
        return await self.get_service(service_id)

    async def stats(self, service_id: str) -> dict[str, Any]:
        return {
            "route_count": await self.db["mock_routes"].count_documents({"service_id": service_id}),
            "disabled_route_count": await self.db["mock_routes"].count_documents({"service_id": service_id, "enabled": False}),
            "call_count": await self.db["mock_call_logs"].count_documents({"service_id": service_id}),
            "error_count": await self.db["mock_call_logs"].count_documents({"service_id": service_id, "status_code": {"$gte": 500}}),
        }

    async def delete_service(self, service_id: str) -> bool:
        result = await self.db["mock_services"].delete_one({"id": service_id})
        await self.db["mock_routes"].delete_many({"service_id": service_id})
        await self.db["mock_call_logs"].delete_many({"service_id": service_id})
        return result.deleted_count > 0

    async def list_routes(self, service_id: str) -> list[dict[str, Any]]:
        return await self.db["mock_routes"].find({"service_id": service_id}, {"_id": 0}).sort([("priority", 1), ("updated_at", -1)]).to_list(500)

    async def get_route(self, service_id: str, route_id: str) -> dict[str, Any] | None:
        return await self.db["mock_routes"].find_one({"id": route_id, "service_id": service_id}, {"_id": 0})

    def _normalize_route_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        payload = dict(data)
        payload["method"] = str(payload.get("method") or "GET").upper()
        payload["path"] = _normalize_path(payload.get("path") or "/")
        response = payload.get("response") or {}
        if "body_type" not in response:
            response["body_type"] = "json" if isinstance(response.get("body"), (dict, list)) else "text"
        payload["response"] = response
        return payload

    async def create_route(self, service: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        payload = self._normalize_route_payload(data)
        route = MockRoute(**{**payload, "project_id": service["project_id"], "service_id": service["id"]})
        await self.db["mock_routes"].insert_one(route.model_dump())
        await self.db["mock_services"].update_one({"id": service["id"]}, {"$set": {"updated_at": _now()}})
        return route.model_dump()

    async def update_route(self, service_id: str, route_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        existing = await self.get_route(service_id, route_id)
        if not existing:
            return None
        update = {k: v for k, v in self._normalize_route_payload(data).items() if k not in {"id", "project_id", "service_id", "created_at"}}
        update["updated_at"] = _now()
        await self.db["mock_routes"].update_one({"id": route_id, "service_id": service_id}, {"$set": update})
        return await self.get_route(service_id, route_id)

    async def delete_route(self, service_id: str, route_id: str) -> bool:
        result = await self.db["mock_routes"].delete_one({"id": route_id, "service_id": service_id})
        return result.deleted_count > 0

    async def import_api_route(self, service: dict[str, Any], api_id: str) -> dict[str, Any]:
        api = await self.db["api_dsls"].find_one({"id": api_id, "project_id": service["project_id"]}, {"_id": 0})
        if not api:
            raise HTTPException(404, "API not found")
        response = api.get("response") or {}
        request = api.get("request") or {}
        return await self.create_route(service, {
            "name": api.get("name") or f"{request.get('method', 'GET')} {request.get('path', '/')}",
            "method": request.get("method") or "GET",
            "path": request.get("path") or "/",
            "priority": 100,
            "enabled": True,
            "match": {"conditions": []},
            "response": {
                "status_code": response.get("status_code") or 200,
                "headers": response.get("headers") or {"content-type": "application/json"},
                "body_type": "json" if isinstance(response.get("body"), (dict, list)) else "text",
                "body": response.get("body") if response.get("body") is not None else {"message": "mock response"},
                "latency_ms": response.get("latency_ms") or 0,
            },
        })

    async def route_from_traffic(self, service: dict[str, Any], record_id: str, enabled: bool = False) -> dict[str, Any]:
        record = await self.db["traffic_records"].find_one({"id": record_id, "project_id": service["project_id"]}, {"_id": 0})
        if not record:
            raise HTTPException(404, "Traffic record not found")
        response = record.get("response") or {}
        return await self.create_route(service, {
            "name": f"{record.get('method', 'GET')} {record.get('path', '/')}",
            "enabled": enabled,
            "priority": 100,
            "method": record.get("method") or "GET",
            "path": record.get("path") or "/",
            "match": {"conditions": []},
            "response": {
                "status_code": response.get("status_code") or 200,
                "headers": response.get("headers") or {"content-type": "application/json"},
                "body_type": "json" if isinstance(response.get("body"), (dict, list)) else "text",
                "body": response.get("body") if response.get("body") is not None else {"message": "mock response"},
                "latency_ms": response.get("latency_ms") or 0,
            },
        })

    def _route_matches(self, route: dict[str, Any], req: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
        trace: list[dict[str, Any]] = []
        if not route.get("enabled", True):
            return False, [{"field": "enabled", "passed": False, "message": "route disabled"}]
        method = str(req.get("method", "GET")).upper()
        route_method = str(route.get("method", "GET")).upper()
        method_ok = route_method in {"ANY", method}
        trace.append({"field": "method", "expected": route_method, "actual": method, "passed": method_ok})
        if not method_ok:
            return False, trace
        path_ok = _path_matches(route.get("path") or "/", req.get("path") or "/")
        trace.append({"field": "path", "expected": route.get("path") or "/", "actual": req.get("path") or "/", "passed": path_ok})
        if not path_ok:
            return False, trace
        conditions = _legacy_match_to_conditions(route.get("match") or {})
        for condition in conditions:
            actual = _condition_actual(condition, req)
            passed = _compare(actual, condition.get("operator") or "equals", condition.get("value"))
            trace.append({**condition, "actual": actual, "passed": passed})
            if not passed:
                return False, trace
        return True, trace

    def _case_matches(self, case: dict[str, Any], req: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
        trace: list[dict[str, Any]] = []
        if not case.get("enabled", True):
            return False, [{"field": "enabled", "passed": False, "message": "case disabled"}]
        for condition in case.get("conditions") or []:
            actual = _condition_actual(condition, req)
            passed = _compare(actual, condition.get("operator") or "equals", condition.get("value"))
            trace.append({**condition, "actual": actual, "passed": passed})
            if not passed:
                return False, trace
        return True, trace

    def _select_response_case(self, route: dict[str, Any], req: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        case_trace: list[dict[str, Any]] = []
        cases = sorted(route.get("response_cases") or [], key=lambda item: _safe_int(item.get("priority"), 100))
        for case in cases:
            matched, trace = self._case_matches(case, req)
            case_trace.append({
                "case_id": case.get("id", ""),
                "case_name": case.get("name", ""),
                "priority": case.get("priority", 100),
                "matched": matched,
                "trace": trace,
            })
            if matched:
                return case, case_trace
        return None, case_trace

    async def _build_response_context(
        self,
        project_id: str,
        response: dict[str, Any],
        req: dict[str, Any],
        path_params: dict[str, str],
    ) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
        context: dict[str, Any] = {
            "request": req,
            "query": req.get("query") or {},
            "headers": req.get("headers") or {},
            "body": req.get("body"),
            "path_params": path_params,
        }
        sql_results: dict[str, Any] = {}
        sql_refs: list[str] = []
        for query in response.get("sql_queries") or []:
            if not isinstance(query, dict):
                continue
            name = query.get("target_var") or query.get("name") or query.get("sql_ref") or "result"
            result = await SqlRuntimeService(self.db).run_ref(project_id, query, {**context, "sql": sql_results})
            sql_results[str(name)] = result
            sql_refs.append(str(query.get("sql_ref") or query.get("db_service_id") or name))
        context["sql"] = sql_results
        mock_data = None
        if response.get("source") == "mock_data" or response.get("mock_data"):
            mock_data = _generate_mock_data(response.get("mock_data") or {}, context)
            context["mock"] = mock_data
        return context, sql_refs, {
            "sql": {key: summarize_sql_result(value) for key, value in sql_results.items()},
            "sql_errors": {key: value.get("error") for key, value in sql_results.items() if value.get("error")},
            "mock": _mask_sensitive(mock_data) if mock_data is not None else None,
        }

    async def evaluate(self, service: dict[str, Any], req: dict[str, Any], client_ip: str = "", write_log: bool = True) -> dict[str, Any]:
        start = datetime.now()
        routes = await self.list_routes(service["id"])
        matched_route = None
        match_trace: list[dict[str, Any]] = []
        for route in routes:
            matched, trace = self._route_matches(route, req)
            if matched:
                matched_route = route
                match_trace = trace
                break
            if not match_trace:
                match_trace = trace
        matched_case = None
        case_trace: list[dict[str, Any]] = []
        if matched_route:
            matched_case, case_trace = self._select_response_case(matched_route, req)
        response = (matched_case or {}).get("response") or (matched_route or {}).get("response") or service.get("default_response") or {}
        if not response:
            response = {"status_code": 200, "headers": {"content-type": "application/json"}, "body_type": "json", "body": {"message": "mock response"}, "latency_ms": 0}
        path_params = _path_params((matched_route or {}).get("path") or "", req.get("path") or "/") if matched_route else {}
        req = {**req, "path_params": path_params}
        render_context, sql_refs, generated_summary = await self._build_response_context(service["project_id"], response, req, path_params)
        latency_ms = max(0, min(_safe_int(response.get("latency_ms"), 0), 10000))
        if latency_ms > 0:
            await asyncio.sleep(latency_ms / 1000)
        body, body_type = _response_body(response, req, render_context)
        headers = _normalize_headers(response.get("headers") or {}, body_type)
        duration_ms = int((datetime.now() - start).total_seconds() * 1000)
        result = {
            "matched": bool(matched_route),
            "route": matched_route,
            "case": matched_case,
            "match_trace": match_trace,
            "case_trace": case_trace,
            "sql_results": generated_summary.get("sql") or {},
            "sql_errors": generated_summary.get("sql_errors") or {},
            "mock_data": generated_summary.get("mock"),
            "status_code": _safe_int(response.get("status_code"), 200),
            "headers": headers,
            "body": body,
            "body_type": body_type,
            "duration_ms": duration_ms,
        }
        if write_log:
            await self.db["mock_call_logs"].insert_one(MockCallLog(
                project_id=service["project_id"],
                service_id=service["id"],
                route_id=matched_route.get("id", "") if matched_route else "",
                method=str(req.get("method", "GET")).upper(),
                path=req.get("path", "/"),
                matched=bool(matched_route),
                request_summary={
                    "query": _mask_sensitive(req.get("query") or {}),
                    "headers": _mask_sensitive(req.get("headers") or {}),
                    "body": _mask_sensitive(req.get("body")),
                    "match_trace": _mask_sensitive(match_trace),
                    "case_trace": _mask_sensitive(case_trace),
                },
                response_summary={
                    "status_code": result["status_code"],
                    "matched_route": matched_route.get("name", "") if matched_route else "",
                    "matched_case": matched_case.get("name", "") if matched_case else "",
                    "sql_errors": generated_summary.get("sql_errors") or {},
                    "body": _mask_sensitive(body),
                },
                case_id=matched_case.get("id", "") if matched_case else "",
                case_name=matched_case.get("name", "") if matched_case else "",
                sql_refs=sql_refs,
                mock_data_summary=generated_summary.get("mock") or {},
                final_response_summary={"body": _mask_sensitive(body), "headers": _mask_sensitive(headers)},
                status_code=result["status_code"],
                duration_ms=duration_ms,
                client_ip=client_ip,
            ).model_dump())
            await self.db["mock_services"].update_one(
                {"id": service["id"]},
                {"$set": {"last_called_at": _now()}, "$inc": {"error_count": 1 if result["status_code"] >= 500 else 0}},
            )
        return result

    async def logs(
        self,
        service_id: str,
        status_code: int | None = None,
        matched: bool | None = None,
        route_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        q: dict[str, Any] = {"service_id": service_id}
        if status_code is not None:
            q["status_code"] = status_code
        if matched is not None:
            q["matched"] = matched
        if route_id:
            q["route_id"] = route_id
        return await self.db["mock_call_logs"].find(q, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)

    async def find_public_service(self, project_slug: str, service_slug: str) -> dict[str, Any] | None:
        # 先通过 slug 查找项目记录；若存在则用项目 UUID 查询
        project = await self.db["projects"].find_one({"slug": project_slug}, {"id": 1})
        if project:
            # 优先用 UUID 查询，同时兼容旧版将 slug 直接作为 project_id 的数据
            query = {
                "$or": [
                    {"project_id": project["id"]},
                    {"project_id": project_slug},
                ],
                "slug": service_slug, "enabled": True, "public_enabled": True,
            }
        else:
            # 项目记录不存在时，直接用 slug 作为 project_id 查询（旧版兼容）
            query = {"project_id": project_slug, "slug": service_slug, "enabled": True, "public_enabled": True}
        return await self.db["mock_services"].find_one(query, {"_id": 0})

    async def find_public_service_by_slug(self, service_slug: str) -> dict[str, Any] | None:
        """通过 service_slug 查找已启用的公开 Mock 服务（v4.0+ 简化路由，无需 project_slug）。"""
        return await self.db["mock_services"].find_one(
            {"slug": service_slug, "enabled": True, "public_enabled": True},
            {"_id": 0},
        )


class TrafficService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def list_sources(self, project_id: str) -> list[dict[str, Any]]:
        return await self.db["traffic_sources"].find({"project_id": project_id}, {"_id": 0}).sort("updated_at", -1).to_list(200)

    async def create_source(self, data: dict[str, Any], project_id: str, username: str = "") -> dict[str, Any]:
        payload = dict(data)
        if not payload.get("access_key"):
            payload["access_key"] = generate_access_key()
        source = TrafficSource(**{**payload, "project_id": project_id, "created_by": username, "updated_by": username})
        await self.db["traffic_sources"].insert_one(source.model_dump())
        return source.model_dump()

    async def update_source(self, source_id: str, data: dict[str, Any], username: str = "") -> dict[str, Any] | None:
        update = {k: v for k, v in data.items() if k not in {"id", "project_id", "created_by"}}
        update["updated_by"] = username
        update["updated_at"] = _now()
        result = await self.db["traffic_sources"].update_one({"id": source_id}, {"$set": update})
        if not result.matched_count:
            return None
        return await self.db["traffic_sources"].find_one({"id": source_id}, {"_id": 0})

    async def rotate_source_key(self, source_id: str, username: str = "") -> dict[str, Any] | None:
        result = await self.db["traffic_sources"].update_one(
            {"id": source_id},
            {"$set": {"access_key": generate_access_key(), "updated_by": username, "updated_at": _now()}},
        )
        if not result.matched_count:
            return None
        return await self.db["traffic_sources"].find_one({"id": source_id}, {"_id": 0})

    async def delete_source(self, source_id: str) -> bool:
        result = await self.db["traffic_sources"].delete_one({"id": source_id})
        await self.db["traffic_rules"].delete_many({"source_id": source_id})
        return result.deleted_count > 0

    async def list_rules(self, project_id: str, source_id: str = "") -> list[dict[str, Any]]:
        q: dict[str, Any] = {"project_id": project_id}
        if source_id:
            q["source_id"] = source_id
        return await self.db["traffic_rules"].find(q, {"_id": 0}).sort("priority", 1).to_list(500)

    async def create_rule(self, data: dict[str, Any], project_id: str) -> dict[str, Any]:
        rule = TrafficRule(**{**data, "project_id": project_id})
        await self.db["traffic_rules"].insert_one(rule.model_dump())
        return rule.model_dump()

    async def update_rule(self, rule_id: str, data: dict[str, Any], username: str = "") -> dict[str, Any] | None:
        update = {k: v for k, v in data.items() if k not in {"id", "project_id", "created_at"}}
        update["updated_at"] = _now()
        result = await self.db["traffic_rules"].update_one({"id": rule_id}, {"$set": update})
        if not result.matched_count:
            return None
        return await self.db["traffic_rules"].find_one({"id": rule_id}, {"_id": 0})

    async def delete_rule(self, rule_id: str) -> bool:
        result = await self.db["traffic_rules"].delete_one({"id": rule_id})
        return result.deleted_count > 0

    async def validate_source_key(self, source_id: str, access_key: str) -> dict[str, Any]:
        if not source_id or not access_key:
            raise HTTPException(403, "Traffic source_id and access_key are required")
        source = await self.db["traffic_sources"].find_one({"id": source_id}, {"_id": 0})
        if not source or not source.get("enabled", True):
            raise HTTPException(403, "Traffic source disabled or not found")
        if str(source.get("access_key") or "") != str(access_key or ""):
            raise HTTPException(403, "Invalid traffic source access key")
        return source

    async def proxy_config(self, source_id: str, access_key: str) -> dict[str, Any]:
        source = await self.validate_source_key(source_id, access_key)
        rules = await self.db["traffic_rules"].find(
            {"project_id": source["project_id"], "source_id": {"$in": ["", source_id]}, "enabled": True},
            {"_id": 0},
        ).sort("priority", 1).to_list(500)
        version_raw = json.dumps([r.get("id", "") + str(r.get("updated_at", "")) for r in rules], default=str)
        return {
            "project_id": source["project_id"],
            "source_id": source_id,
            "version": hashlib.sha256(version_raw.encode()).hexdigest()[:12],
            "updated_at": _now().isoformat(),
            "rules": rules,
        }

    async def ingest(self, payload: dict[str, Any], redis=None, trusted_project_id: str | None = None) -> dict[str, Any]:
        source_id = payload.get("source_id") or ""
        source = None
        if trusted_project_id:
            project_id = trusted_project_id
        else:
            source = await self.validate_source_key(source_id, payload.get("access_key") or "")
            project_id = source["project_id"]

        method = str(payload.get("method") or payload.get("request", {}).get("method") or "GET").upper()
        try:
            HttpMethod(method)
        except ValueError:
            raise HTTPException(422, f"Unsupported HTTP method: {method}")
        url = payload.get("url") or payload.get("request", {}).get("url") or ""
        parsed = urlparse(url)
        path = _normalize_path(payload.get("path") or payload.get("request", {}).get("path") or parsed.path or "/")
        host = parsed.netloc

        if source:
            if source.get("filter_host") and source["filter_host"].lower() not in host.lower():
                return {"status": "filtered", "reason": "source_host_filter"}
            if source.get("filter_url") and source["filter_url"].lower() not in url.lower():
                return {"status": "filtered", "reason": "source_url_filter"}

        project = await self.db["projects"].find_one({"id": project_id}, {"domain_allowlist": 1, "domain_blocklist": 1})
        if project and _should_filter_by_domain(url, project.get("domain_allowlist") or [], project.get("domain_blocklist") or []):
            return {"status": "filtered", "reason": "domain_policy"}

        record = TrafficRecord(
            project_id=project_id,
            source_id=source_id,
            source_type=payload.get("source_type") or (source.get("type") if source else "manual"),
            method=method,
            url=url,
            path=path,
            request=payload.get("request") or {},
            response=payload.get("response") or {},
            trace_id=payload.get("trace_id") or "",
            session_id=payload.get("session_id") or "",
            tags=payload.get("tags") or [],
        )
        await self.db["traffic_records"].insert_one(record.model_dump())

        mode = payload.get("record_mode") or (source or {}).get("record_mode") or "asset"
        api_id = ""
        route_id = ""
        if mode in {"asset", "asset_and_mock"}:
            api_id = await self._upsert_api_from_traffic(record, redis)
            await self.db["traffic_records"].update_one({"id": record.id}, {"$set": {"api_id": api_id}})
        if mode == "asset_and_mock":
            route_id = await self._create_mock_draft_from_record(record)
        return {"status": "ingested", "record_id": record.id, "api_id": api_id, "route_id": route_id, "project_id": project_id}

    async def _upsert_api_from_traffic(self, record: TrafficRecord, redis=None) -> str:
        parsed = urlparse(record.url)
        req = record.request or {}
        resp = record.response or {}
        body = req.get("body")
        body_type = req.get("body_type") or (BodyType.JSON if isinstance(body, (dict, list)) else BodyType.TEXT if body else BodyType.NONE)
        raw = f"{record.project_id}|{record.method}|{parsed.scheme}://{parsed.netloc}{record.path}|{json.dumps(body, sort_keys=True, default=str)}"
        source_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
        existing = await self.db["api_dsls"].find_one({"project_id": record.project_id, "source_hash": source_hash}, {"id": 1})
        if existing:
            return existing["id"]
        api = ApiDSL(
            id=str(uuid4()),
            name=f"{record.method} {record.path}",
            source_har=f"traffic://{record.source_type or 'ingest'}",
            source_hash=source_hash,
            request=RequestDSL(
                method=HttpMethod(record.method),
                url=record.url,
                path=record.path,
                query_params=req.get("query_params") or dict(parse_qsl(parsed.query, keep_blank_values=True)),
                headers=req.get("headers") or {},
                body=body,
                body_type=body_type,
            ),
            response=ResponseDSL(
                status_code=_safe_int(resp.get("status_code"), 0),
                headers=resp.get("headers") or {},
                body=resp.get("body"),
                latency_ms=_safe_int(resp.get("latency_ms"), 0),
            ),
            parse_status=ParseStatus.SUCCESS,
            project_id=record.project_id,
            created_at=_now(),
            updated_at=_now(),
        )
        await self.db["api_dsls"].insert_one(api.model_dump())
        if redis:
            await redis.rpush("queue:ai_analyze", json.dumps({"api_id": api.id, "ts": _now().isoformat()}))
        # 差异检测：流量录制新建 API 后对比已分析的同路径 API，覆盖所有接口修改方式
        try:
            from services.diff_service import DiffService
            diff_svc = DiffService(self.db, redis)
            await diff_svc.detect_and_record(api.request.path, api.request.method.value, api.id, api.project_id)
        except Exception:
            logger.warning("Diff detection failed for captured API id=%s path=%s", api.id, api.request.path)
            pass  # 差异检测失败不影响主流程
        return api.id

    async def _create_mock_draft_from_record(self, record: TrafficRecord) -> str:
        service = await self.db["mock_services"].find_one({"project_id": record.project_id, "source": "captured"}, {"_id": 0}, sort=[("updated_at", -1)])
        if not service:
            service = MockService(
                project_id=record.project_id,
                name="Captured Traffic Mock",
                slug=f"captured-{record.project_id[:8]}",
                enabled=True,
                public_enabled=False,
                source="captured",
            ).model_dump()
            await self.db["mock_services"].insert_one(service)
        manager = MockServiceManager(self.db)
        route = await manager.route_from_traffic(service, record.id, enabled=False)
        return route["id"]

    async def records(
        self,
        project_id: str,
        limit: int = 100,
        source_id: str = "",
        method: str = "",
        path: str = "",
        status_code: int | None = None,
    ) -> list[dict[str, Any]]:
        q: dict[str, Any] = {"project_id": project_id}
        if source_id:
            q["source_id"] = source_id
        if method:
            q["method"] = method.upper()
        if path:
            q["path"] = {"$regex": re.escape(path), "$options": "i"}
        if status_code is not None:
            q["response.status_code"] = status_code
        return await self.db["traffic_records"].find(q, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)

    def evaluate_rule(self, rule: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        req = {
            "method": sample.get("method") or sample.get("request", {}).get("method") or "GET",
            "url": sample.get("url") or sample.get("request", {}).get("url") or "",
            "path": sample.get("path") or sample.get("request", {}).get("path") or urlparse(sample.get("url") or "").path or "/",
            "query": sample.get("query") or sample.get("request", {}).get("query") or {},
            "headers": sample.get("headers") or sample.get("request", {}).get("headers") or {},
            "cookies": sample.get("cookies") or {},
            "body": sample.get("body") if "body" in sample else sample.get("request", {}).get("body"),
            "status_code": sample.get("response", {}).get("status_code"),
        }
        conditions = list(rule.get("conditions") or []) or _legacy_match_to_conditions(rule.get("match") or {})
        trace = []
        matched = True
        for condition in conditions:
            actual = _condition_actual(condition, req)
            passed = _compare(actual, condition.get("operator") or "equals", condition.get("value"))
            trace.append({**condition, "actual": actual, "passed": passed})
            if not passed:
                matched = False
        action = rule.get("action") or "pass_through"
        request_after = sample.get("request") or {"headers": req.get("headers") or {}, "query": req.get("query") or {}, "body": req.get("body")}
        response_after = sample.get("response") or {}
        patch_list = list(rule.get("patch_list") or [])
        patches = rule.get("patches") or {}
        for key, value in (patches.get("headers") or {}).items():
            patch_list.append({"target": "header", "key": key, "value": value})
        for key, value in (patches.get("query") or {}).items():
            patch_list.append({"target": "query", "key": key, "value": value})
        for key, value in (patches.get("body_jsonpath") or {}).items():
            patch_list.append({"target": "body_jsonpath", "key": key, "value": value})
        if "response_body" in patches:
            patch_list.append({"target": "response_body", "value": patches["response_body"]})
        if "status_code" in patches:
            patch_list.append({"target": "status_code", "value": patches["status_code"]})
        if matched and action in {"modify_request"}:
            request_after = _apply_patch_list(request_after, patch_list)
        if matched and action in {"modify_response", "mock_response"}:
            response_after = _apply_patch_list(response_after, patch_list)
        return {
            "matched": matched,
            "trace": trace,
            "action": action,
            "record": bool(rule.get("record", True)),
            "upstream": matched and action != "mock_response" and action != "drop",
            "request_after": _mask_sensitive(request_after),
            "response_after": _mask_sensitive(response_after),
        }
