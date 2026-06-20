"""
API DSL CRUD + 执行 + AI 分析 + 断言管理路由 —— 委托 services/api_service.py
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db, get_redis
from models.dsl import (
    AssertRule, ApiDSL, get_assert_operators, ASSERT_TYPE_CANDIDATES,
    HttpMethod, RequestDSL, ResponseDSL, BodyType,
)
from services.curl_parser import parse_curl
from models.audit import AuditAction, AuditResource
from services.api_service import ApiService
from services.audit_service import AuditService
from services.diff_service import DiffService
from api.deps import _get_user_from_request, _get_client_ip, ensure_project_access, get_current_user, visible_project_id
from dag_engine.engine import _eval_single_assert, extract_jsonpath

from api.state import _ws

router = APIRouter(tags=["APIs"])


async def _record_implicit_feedback(
    db: AsyncIOMotorDatabase, api_id: str,
    edit_field: str, old_value: Any, new_value: Any,
) -> None:
    """
    记录用户编辑 AI 生成内容时的隐式反馈。
    写入 ai_feedback 集合供知识系统后续分析。
    字段截断到 500 字符防止膨胀。
    """
    if not old_value or not new_value:
        return  # 空值跳过
    if old_value == new_value:
        return  # 无变化跳过

    try:
        await db["ai_feedback"].insert_one({
            "api_id": api_id,
            "edit_field": edit_field,
            "old_value": str(old_value)[:500],
            "new_value": str(new_value)[:500],
            "source": "implicit",
            "created_at": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None),
        })
        logger.debug("Implicit feedback recorded: api={} field={}", api_id, edit_field)
    except Exception as e:
        logger.warning("Failed to record implicit feedback for api={}: {}", api_id, e)


# 依赖注入
def get_api_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> ApiService:
    return ApiService(db)


def get_audit_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> AuditService:
    return AuditService(db)


async def _detect_diff_for_api(db: AsyncIOMotorDatabase, api_id: str, project_id: str) -> None:
    """
    对单个 API 变更触发差异检测（fire-and-forget，不阻塞主流程）。
    覆盖手动创建/编辑、cURL导入、OpenAPI导入等所有修改路径，
    确保差异对比对所有接口修改方式都有效。
    """
    try:
        redis = await get_redis()
        diff_svc = DiffService(db, redis)
        api = await db["api_dsls"].find_one({"id": api_id}, {"request.path": 1, "request.method": 1})
        if api:
            path = (api.get("request") or {}).get("path", "")
            method = (api.get("request") or {}).get("method", "GET")
            if path:
                await diff_svc.detect_and_record(path, method, api_id, project_id)
    except Exception:
        logger.warning("Diff detection failed for api_id=%s", api_id)
        pass  # 差异检测失败不影响主流程


async def _detect_diff_for_batch(db: AsyncIOMotorDatabase, api_ids: list[str], project_id: str) -> None:
    """批量差异检测，用于 OpenAPI 导入等一次产生多个 API 的场景。"""
    try:
        redis = await get_redis()
        diff_svc = DiffService(db, redis)
        await diff_svc.detect_for_batch(api_ids, project_id)
    except Exception:
        logger.warning("Batch diff detection failed for %d api_ids", len(api_ids))
        pass  # 差异检测失败不影响主流程


@router.get("/apis")
async def list_apis(
    project_id: str = "default",
    analysis_status: str | None = None,
    tag: str | None = None,
    method: str | None = None,
    search: str | None = None,
    source: str | None = None,
    status_code_min: int | None = None,
    status_code_max: int | None = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    skip: int = 0,
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
    service: ApiService = Depends(get_api_service),
):
    project_id = visible_project_id(current_user, project_id)
    return await service.list_apis(
        project_id=project_id, analysis_status=analysis_status, tag=tag,
        method=method, search=search, source=source,
        status_code_min=status_code_min, status_code_max=status_code_max,
        sort_by=sort_by, sort_order=sort_order, skip=skip, limit=limit,
    )


# P0-2：断言操作符元数据下发端点。
# 必须放在 /apis/{api_id} 之前注册，否则 "assert-operators" 会被当作 api_id 捕获。
# 前端通过此接口动态渲染断言编辑器控件，消除前后端 operator 列表不一致问题。
@router.get("/apis/assert-operators")
async def list_assert_operators():
    """
    返回全量断言操作符元数据（单一来源），供前端动态渲染。
    - operators: 操作符列表，含 op/group/label_key/expected_type/help_zh
    - type_candidates: type_match 操作符的类型候选（int/float/str/bool/list/dict/null）
    label_key 是 i18n key，前端用 $t(label_key) 取本地化文案。
    """
    return {
        "operators": get_assert_operators(),
        "type_candidates": ASSERT_TYPE_CANDIDATES,
    }


def _assert_actual_from_sample(assertion: dict[str, Any], sample: dict[str, Any]) -> Any:
    """从响应样本中提取断言 actual，复用执行链路的来源语义。"""
    source = assertion.get("source", "response")
    path = assertion.get("path") or assertion.get("field") or ""
    if assertion.get("operator") == "response_time_lt" or source == "performance" or path == "$response_time_ms":
        return sample.get("latency_ms", 0)
    if source == "status":
        return sample.get("status_code")
    if source == "header":
        headers = sample.get("headers") or {}
        for hk, hv in headers.items():
            if hk.lower() == str(path).lower():
                return hv
        return None
    return extract_jsonpath(sample.get("body"), path)


def _eval_assertion_for_sample(assertion: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
    """单条断言试算，返回 actual/expected/pass/fail 和错误信息。"""
    op = assertion.get("operator") or "eq"
    path = assertion.get("path") or assertion.get("field") or ""
    expected = assertion.get("expected")
    actual = _assert_actual_from_sample(assertion, sample)
    error = ""
    passed = False
    if op == "json_schema":
        try:
            import jsonschema
            target = sample.get("body") if path in ("$", "$.body", "body", "") else actual
            jsonschema.validate(target, expected or {})
            passed = True
        except Exception as e:
            error = str(e)[:300]
            passed = False
    elif op == "header_eq":
        passed = str(actual).lower() == str(expected).lower() if actual is not None else False
    elif op == "header_contains":
        passed = str(expected).lower() in str(actual).lower() if actual is not None else False
    else:
        passed = _eval_single_assert(op, actual, expected)
    return {
        "source": assertion.get("source", "response"),
        "field": path,
        "operator": op,
        "expected": expected,
        "actual": actual,
        "passed": passed,
        "error": error,
    }


@router.post("/apis/assertions/dry-run")
async def dry_run_assertions(
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """断言 dry-run：用响应样本即时试算 actual/expected/pass/fail。"""
    if not current_user:
        raise HTTPException(401, "Unauthorized")
    assertions = body.get("assertions") or []
    sample = body.get("sample") or {}
    if not isinstance(assertions, list):
        raise HTTPException(422, "assertions must be array")
    if not isinstance(sample, dict):
        raise HTTPException(422, "sample must be object")
    sample = {
        "status_code": sample.get("status_code", 200),
        "headers": sample.get("headers") or {},
        "body": sample.get("body"),
        "latency_ms": sample.get("latency_ms", 0),
    }
    results = [_eval_assertion_for_sample(a, sample) for a in assertions if isinstance(a, dict)]
    return {
        "ok": all(r.get("passed") for r in results),
        "total": len(results),
        "passed": sum(1 for r in results if r.get("passed")),
        "failed": sum(1 for r in results if not r.get("passed")),
        "results": results,
    }


# P1-5b: 手动新建 API DSL。
# 解决问题：此前只能通过 HAR/抓包/cURL 导入，无法从零定义接口。
# 用途：对接未抓到流量的接口、内部系统接口文档化、Mock 接口。
# 支持两种输入：完整 ApiDSL JSON（power user）或精简表单（method/url/path + 可选 body）。
@router.post("/apis", status_code=201)
async def create_api(
    body: dict[str, Any] = Body(...),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    service: ApiService = Depends(get_api_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    """
    手动新建接口定义。请求体支持：
    - 完整 ApiDSL 结构（含 request/response/asserts）
    - 精简结构：{method, url, path, name, project_id, query_params?, headers?, body?, body_type?}
    """
    project_id = body.get("project_id", "default")
    project_id = visible_project_id(current_user, project_id)
    method_str = str(body.get("method", "GET")).upper()
    try:
        method = HttpMethod(method_str)
    except ValueError:
        raise HTTPException(400, f"Unsupported HTTP method: {method_str}")

    # 从 body 构造 RequestDSL（支持精简字段 + 完整 request 对象）
    req_data = body.get("request")
    if isinstance(req_data, dict):
        # 完整 request 对象模式
        request_dsl = RequestDSL(**{**req_data, "method": method})
    else:
        # 精简字段模式：从顶层提取 query_params/headers/body/body_type
        body_type_str = str(body.get("body_type", "none")).lower()
        try:
            body_type = BodyType(body_type_str)
        except ValueError:
            body_type = BodyType.NONE
        request_dsl = RequestDSL(
            method=method,
            url=body.get("url", ""),
            path=body.get("path", ""),
            query_params=body.get("query_params") or {},
            headers=body.get("headers") or {},
            body=body.get("body"),
            body_type=body_type,
        )

    # 校验 URL 必填（path 可空，便于仅用 base_url_override 场景）
    if not request_dsl.url:
        raise HTTPException(400, "url is required")

    # 构造 ResponseDSL：优先使用客户端传入的响应数据（如 gen_test_data 预设的响应报文）
    # 无传入时使用空响应，后续可通过执行或 AI 分析补充
    resp_data = body.get("response")
    if isinstance(resp_data, dict):
        response_dsl = ResponseDSL(**resp_data)
    else:
        response_dsl = ResponseDSL(status_code=0)

    api = ApiDSL(
        name=body.get("name", "") or f"{method.value} {request_dsl.path or request_dsl.url}",
        request=request_dsl,
        response=response_dsl,
        project_id=project_id,
        tags=body.get("tags") or [],
    )
    # 可选：用户预设的 asserts/doc 直接写入
    if body.get("asserts"):
        api.asserts = [AssertRule(**a) for a in body["asserts"] if isinstance(a, dict)]

    created = await service.create_api(api)

    # 审计日志
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.CREATE,
        resource=AuditResource.API, resource_id=api.id, resource_name=api.name,
        ip=_get_client_ip(request), extra={"source": "manual"},
    )

    # 新建接口后自动触发 AI 分析（文档 + 断言），与其他创建路径（抓包/Mock）保持一致
    redis = await get_redis()
    await service.enqueue_analyze(created["id"], redis, force=False)

    # 差异检测：手动创建 API 后对比已分析的同路径 API，覆盖所有接口修改方式
    await _detect_diff_for_api(db, created["id"], project_id)

    return created


# P1-5: OpenAPI/Swagger JSON 导入。
# 静态路由必须放在 /apis/{api_id} 前，避免 "import-openapi" 被动态 api_id 捕获。
@router.post("/apis/import-openapi", status_code=201)
async def import_openapi(
    body: dict[str, Any] = Body(...),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    service: ApiService = Depends(get_api_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    """
    从 OpenAPI 3.x / Swagger 2.0 JSON 导入接口定义。
    请求体支持：
    - {spec, project_id, source_name}
    - 直接传 OpenAPI/Swagger JSON（前端默认使用第一种）
    """
    spec = body.get("spec") if isinstance(body.get("spec"), dict) else body
    project_id = visible_project_id(current_user, body.get("project_id", "default"))
    source_name = str(body.get("source_name") or "openapi.json")
    try:
        result = await service.import_openapi(spec, project_id, source_name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    # 导入后自动触发 AI 分析（文档 + 断言），与其他创建路径保持一致
    redis = await get_redis()
    for api_id in result.get("api_ids", []):
        await service.enqueue_analyze(api_id, redis, force=False)

    # 差异检测：OpenAPI 导入后批量对比已分析的同路径 API，覆盖所有接口修改方式
    await _detect_diff_for_batch(db, result.get("api_ids", []), project_id)

    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.IMPORT,
        resource=AuditResource.API, resource_id="", resource_name=source_name,
        ip=_get_client_ip(request), extra={**result, "source": "openapi"},
    )
    return result


# P1-5: 选中 API 批量导出 OpenAPI JSON。
# 与场景导出保持一致，返回 attachment，便于用户交给研发/网关/文档系统复用。
@router.post("/apis/export-openapi")
async def export_openapi(
    ids: list[str] = Body(...),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    service: ApiService = Depends(get_api_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    project_id = visible_project_id(current_user, None)
    try:
        result = await service.export_openapi(ids, project_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.EXPORT,
        resource=AuditResource.API, resource_id="", resource_name=f"{result.get('exported', 0)} APIs",
        ip=_get_client_ip(request), extra={"ids": ids, "count": result.get("exported", 0), "source": "openapi"},
    )
    content = json.dumps(result["openapi"], ensure_ascii=False, default=str)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=apis-openapi.json"},
    )


@router.get("/apis/{api_id}")
async def get_api(
    api_id: str,
    current_user: dict = Depends(get_current_user),
    service: ApiService = Depends(get_api_service),
):
    doc = await service.get_api(api_id)
    if not doc:
        raise HTTPException(404, "API not found")
    ensure_project_access(current_user, doc.get("project_id"))
    return doc


@router.put("/apis/{api_id}")
async def update_api(
    api_id: str,
    request: Request,
    data: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    service: ApiService = Depends(get_api_service),
    audit_service: AuditService = Depends(get_audit_service),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    # 更新前读取旧值，用于隐式反馈检测
    old_doc = await service.get_api(api_id)
    if old_doc:
        ensure_project_access(current_user, old_doc.get("project_id"))
    old_doc_data = (old_doc.get("doc") or {}) if old_doc else {}

    ok = await service.update_api(api_id, data)
    if not ok:
        raise HTTPException(404, "API not found")
    # 审计日志：记录 API 更新操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.UPDATE,
        resource=AuditResource.API, resource_id=api_id, resource_name=data.get("name", api_id),
        ip=_get_client_ip(request),
    )

    # 隐式反馈：检测用户修改了哪些 AI 生成的文档字段
    # 仅当更新数据中包含 doc 子字段时触发（params/response_fields/summary/description）
    new_doc_data = data.get("doc") or {}
    doc_fields_to_check = ["params", "response_fields", "summary", "description"]
    for field in doc_fields_to_check:
        old_val = old_doc_data.get(field)
        new_val = new_doc_data.get(field)
        if old_val is not None and new_val is not None and old_val != new_val:
            await _record_implicit_feedback(
                db, api_id, f"doc.{field}", old_val, new_val,
            )

    # 差异检测：手动编辑 API 后对比已分析的同路径 API，覆盖所有接口修改方式
    # 仅当修改涉及 request 或 response 字段时触发（非纯元数据修改）
    if any(k in data for k in ("request", "response", "doc")):
        await _detect_diff_for_api(db, api_id, old_doc.get("project_id", "default") if old_doc else "default")

    return {"updated": True}


@router.delete("/apis/{api_id}")
async def delete_api(
    api_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    service: ApiService = Depends(get_api_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    old_doc = await service.get_api(api_id)
    if old_doc:
        ensure_project_access(current_user, old_doc.get("project_id"))
    ok = await service.delete_api(api_id)
    if not ok:
        raise HTTPException(404, "API not found")
    # 审计日志：记录 API 删除操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.DELETE,
        resource=AuditResource.API, resource_id=api_id, resource_name=api_id,
        ip=_get_client_ip(request),
    )
    return {"deleted": True}


@router.post("/apis/batch_delete")
async def batch_delete_apis(
    request: Request,
    ids: list[str] = Body(...),
    current_user: dict = Depends(get_current_user),
    service: ApiService = Depends(get_api_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    for api_id in ids:
        doc = await service.get_api(api_id)
        if doc:
            ensure_project_access(current_user, doc.get("project_id"))
    n = await service.batch_delete_apis(ids)
    # 审计日志：记录批量删除 API 操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.BATCH_DELETE,
        resource=AuditResource.API, resource_id="", resource_name=f"{n} APIs",
        ip=_get_client_ip(request), extra={"ids": ids, "count": n},
    )
    return {"deleted": n}


@router.post("/apis/{api_id}/run")
async def run_single_api(
    api_id: str,
    request: Request,
    body: dict[str, Any] = Body(default={}),
    current_user: dict = Depends(get_current_user),
    service: ApiService = Depends(get_api_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    redis = await get_redis()
    # 从请求中提取当前用户名作为执行人
    user = _get_user_from_request(request) or {}
    owner = user.get("username", "")
    doc = await service.get_api(api_id)
    if doc:
        ensure_project_access(current_user, doc.get("project_id"))
    result = await service.run_single_api(
        api_id, redis,
        override_params=body.get("override_params"),
        override_headers=body.get("override_headers"),
        environment_id=body.get("environment_id", ""),
        owner=owner,
        client_ip=_get_client_ip(request),
    )
    if result is None:
        raise HTTPException(404, "API not found")
    record, exec_id = result
    # 审计日志：记录 API 执行操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.EXECUTE,
        resource=AuditResource.API, resource_id=api_id, resource_name=record.get("name", api_id),
        ip=_get_client_ip(request),
    )
    await _ws.broadcast(f"exec:{exec_id}", {"type": "done", "record": record})
    return record


@router.post("/apis/{api_id}/analyze")
async def reanalyze_api(
    api_id: str,
    force: bool = Query(False, description="强制重新分析，覆盖已有文档和断言"),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    service: ApiService = Depends(get_api_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    redis = await get_redis()
    doc = await service.get_api(api_id)
    if doc:
        ensure_project_access(current_user, doc.get("project_id"))
    # skip_dedup=True: 手动点击分析按钮，绕过去重检查，始终允许入队
    ok = await service.enqueue_analyze(api_id, redis, force=force, skip_dedup=True)
    if not ok:
        raise HTTPException(404, "API not found")
    # 审计日志：记录 AI 分析请求
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.ANALYZE,
        resource=AuditResource.API, resource_id=api_id, resource_name=api_id,
        ip=_get_client_ip(request), extra={"force": force},
    )
    return {"queued": True}


@router.post("/apis/{api_id}/analyze-doc")
async def analyze_doc_only(
    api_id: str,
    force: bool = Query(False, description="强制重新生成文档，覆盖已有文档"),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    service: ApiService = Depends(get_api_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    """仅入队 AI 文档生成任务（不生成断言）"""
    redis = await get_redis()
    doc = await service.get_api(api_id)
    if doc:
        ensure_project_access(current_user, doc.get("project_id"))
    # skip_dedup=True: 手动点击分析按钮，绕过去重检查，始终允许入队
    ok = await service.enqueue_analyze_doc(api_id, redis, force=force, skip_dedup=True)
    if not ok:
        raise HTTPException(404, "API not found")
    # 审计日志：记录 AI 文档生成请求
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.ANALYZE,
        resource=AuditResource.API, resource_id=api_id, resource_name=f"{api_id} (doc only)",
        ip=_get_client_ip(request), extra={"force": force, "mode": "doc"},
    )
    return {"queued": True}


@router.post("/apis/{api_id}/analyze-asserts")
async def analyze_asserts_only(
    api_id: str,
    force: bool = Query(False, description="强制重新生成断言，覆盖已有断言"),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    service: ApiService = Depends(get_api_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    """仅入队 AI 断言生成任务（不生成文档）"""
    redis = await get_redis()
    doc = await service.get_api(api_id)
    if doc:
        ensure_project_access(current_user, doc.get("project_id"))
    # skip_dedup=True: 手动点击分析按钮，绕过去重检查，始终允许入队
    ok = await service.enqueue_analyze_asserts(api_id, redis, force=force, skip_dedup=True)
    if not ok:
        raise HTTPException(404, "API not found")
    # 审计日志：记录 AI 断言生成请求
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.ANALYZE,
        resource=AuditResource.API, resource_id=api_id, resource_name=f"{api_id} (asserts only)",
        ip=_get_client_ip(request), extra={"force": force, "mode": "asserts"},
    )
    return {"queued": True}


# ── 断言管理 ──


@router.get("/apis/{api_id}/asserts")
async def get_asserts(
    api_id: str,
    current_user: dict = Depends(get_current_user),
    service: ApiService = Depends(get_api_service),
):
    doc = await service.get_api(api_id)
    if doc:
        ensure_project_access(current_user, doc.get("project_id"))
    asserts = await service.get_asserts(api_id)
    if asserts is None:
        raise HTTPException(404, "API not found")
    return asserts


@router.put("/apis/{api_id}/asserts")
async def replace_asserts(
    api_id: str,
    asserts: list[AssertRule] = Body(...),
    current_user: dict = Depends(get_current_user),
    service: ApiService = Depends(get_api_service),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    # 更新前读取旧断言，用于隐式反馈检测
    doc = await service.get_api(api_id)
    if doc:
        ensure_project_access(current_user, doc.get("project_id"))
    old_asserts = await service.get_asserts(api_id)

    ok = await service.replace_asserts(api_id, asserts)
    if not ok:
        raise HTTPException(404, "API not found")

    # 隐式反馈：检测断言变更（新增/修改/删除）
    if old_asserts is not None:
        new_asserts_raw = [a.model_dump() if hasattr(a, "model_dump") else a for a in asserts]
        if old_asserts != new_asserts_raw:
            await _record_implicit_feedback(
                db, api_id, "asserts", old_asserts, new_asserts_raw,
            )

    return {"updated": len(asserts)}


@router.post("/apis/{api_id}/asserts")
async def add_assert(
    api_id: str,
    rule: AssertRule = Body(...),
    current_user: dict = Depends(get_current_user),
    service: ApiService = Depends(get_api_service),
):
    existing = await service.get_api(api_id)
    if not existing:
        raise HTTPException(404, "API not found")
    ensure_project_access(current_user, existing.project_id)

    ok = await service.add_assert(api_id, rule)
    if not ok:
        raise HTTPException(404, "API not found")
    return {"added": True}


async def _load_api_for_mock_case(db: AsyncIOMotorDatabase, api_id: str, current_user: dict) -> ApiDSL:
    """加载并校验 API 权限，供 mock case CRUD 复用。"""
    doc = await db["api_dsls"].find_one({"id": api_id})
    if not doc:
        raise HTTPException(404, "API not found")
    ensure_project_access(current_user, doc.get("project_id"))
    return ApiDSL(**doc)


def _normalize_mock_case_payload(api: ApiDSL, body: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """规范化 mock case，统一保存响应结构并保留版本元信息。"""
    response = body.get("response") if isinstance(body.get("response"), dict) else {}
    if not response:
        response = {
            "status_code": body.get("status_code", api.response.status_code or 200),
            "headers": body.get("headers") or {},
            "body": body.get("body", {}),
        }
    return {
        "name": body.get("name") or (existing or {}).get("name") or "Mock Case",
        "description": body.get("description", (existing or {}).get("description", "")),
        "enabled": bool(body.get("enabled", (existing or {}).get("enabled", True))),
        "tags": body.get("tags", (existing or {}).get("tags", [])) or [],
        "response": {
            "status_code": int(response.get("status_code") or 200),
            "headers": response.get("headers") or {},
            "body": response.get("body", {}),
            "latency_ms": int(response.get("latency_ms") or 0),
        },
    }


@router.get("/apis/{api_id}/mock-cases")
async def list_mock_cases(
    api_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """列出 API 保存的多个 mock case。"""
    await _load_api_for_mock_case(db, api_id, current_user)
    return {
        "items": await db["api_mock_cases"].find(
            {"api_id": api_id},
            {"_id": 0},
        ).sort("updated_at", -1).to_list(200)
    }


@router.post("/apis/{api_id}/mock-cases", status_code=201)
async def create_mock_case(
    api_id: str,
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """创建一个 API mock case；未传 response 时可基于文档字段生成。"""
    api = await _load_api_for_mock_case(db, api_id, current_user)
    if body.get("from_doc") and not body.get("response"):
        if not api.doc.response_fields:
            raise HTTPException(400, "No response_fields in doc, run AI analysis first")
        body = {**body, "response": ApiService.generate_mock(api, status_code=body.get("status_code"))}
    now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    payload = _normalize_mock_case_payload(api, body)
    doc = {
        "id": str(uuid.uuid4()),
        "api_id": api_id,
        "project_id": api.project_id,
        **payload,
        "created_at": now,
        "updated_at": now,
    }
    await db["api_mock_cases"].insert_one(doc)
    doc.pop("_id", None)
    return doc


@router.put("/apis/{api_id}/mock-cases/{case_id}")
async def update_mock_case(
    api_id: str,
    case_id: str,
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """更新 API mock case。"""
    api = await _load_api_for_mock_case(db, api_id, current_user)
    existing = await db["api_mock_cases"].find_one({"id": case_id, "api_id": api_id}, {"_id": 0})
    if not existing:
        raise HTTPException(404, "Mock case not found")
    payload = _normalize_mock_case_payload(api, body, existing)
    payload["updated_at"] = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    await db["api_mock_cases"].update_one({"id": case_id, "api_id": api_id}, {"$set": payload})
    return {"updated": True}


@router.delete("/apis/{api_id}/mock-cases/{case_id}")
async def delete_mock_case(
    api_id: str,
    case_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """删除 API mock case。"""
    await _load_api_for_mock_case(db, api_id, current_user)
    result = await db["api_mock_cases"].delete_one({"id": case_id, "api_id": api_id})
    if not result.deleted_count:
        raise HTTPException(404, "Mock case not found")
    return {"deleted": True}


# P2-4: Mock 生成 —— 基于 doc.response_fields 生成类型正确的 Mock 响应
@router.get("/apis/{api_id}/mock")
async def get_mock_response(
    api_id: str,
    status_code: int | None = None,
    case_id: str | None = None,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    生成 Mock 响应（前端联调用）。
    优先用 doc.response_fields 的 example 值，无则按 type 生成默认值。
    status_code 参数可覆盖默认状态码。
    """
    doc = await db["api_dsls"].find_one({"id": api_id})
    if not doc:
        raise HTTPException(404, "API not found")
    ensure_project_access(current_user, doc.get("project_id"))
    if case_id:
        case = await db["api_mock_cases"].find_one({"id": case_id, "api_id": api_id, "enabled": True}, {"_id": 0})
        if not case:
            raise HTTPException(404, "Mock case not found")
        response = case.get("response") or {}
        return {
            "status_code": response.get("status_code", 200),
            "headers": response.get("headers") or {},
            "body": response.get("body", {}),
            "latency_ms": response.get("latency_ms", 0),
            "case_id": case_id,
            "case_name": case.get("name", ""),
        }
    api = ApiDSL(**doc)
    # 无 response_fields（未 AI 分析）→ 提示用户先分析
    if not api.doc.response_fields:
        raise HTTPException(400, "No response_fields in doc, run AI analysis first")
    return ApiService.generate_mock(api, status_code=status_code)


# P2-4: 契约校验 —— 实际响应 vs doc.response_fields 结构比对
@router.post("/apis/{api_id}/contract-check")
async def check_contract(
    api_id: str,
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    契约校验：对比实际响应体与 doc 定义的字段结构。
    请求体：{response_body: {...}}，传入要校验的实际响应。
    返回：{passed, missing_fields, type_mismatches, extra_fields, summary}
    """
    doc = await db["api_dsls"].find_one({"id": api_id})
    if not doc:
        raise HTTPException(404, "API not found")
    ensure_project_access(current_user, doc.get("project_id"))
    api = ApiDSL(**doc)
    if not api.doc.response_fields:
        raise HTTPException(400, "No response_fields in doc, run AI analysis first")
    actual_body = body.get("response_body")
    if actual_body is None:
        raise HTTPException(400, "response_body is required")
    return ApiService.check_contract(actual_body, api)


@router.post("/apis/import-curl", status_code=201)
async def import_curl(
    body: dict[str, Any] = Body(...),
    db: AsyncIOMotorDatabase = Depends(get_db),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    service: ApiService = Depends(get_api_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    """从 cURL 命令字符串导入单个 API"""
    curl_command = (body.get("curl") or "").strip()
    if not curl_command:
        raise HTTPException(400, "curl command is required")
    project_id = visible_project_id(current_user, body.get("project_id", "default"))

    try:
        api = parse_curl(curl_command)
    except Exception as e:
        raise HTTPException(400, f"Failed to parse curl: {str(e)}") from e

    # 填充项目、时间戳等字段
    api.project_id = project_id
    now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    api.created_at = now
    api.updated_at = now

    import uuid
    api.id = str(uuid.uuid4())

    # P1-5b 修复：此前写入 db["apis"]，但所有查询用 api_dsls 集合，
    # 导致 cURL 导入的接口实际查不出来。修正为 api_dsls 保持一致。
    await db["api_dsls"].insert_one(api.model_dump())

    # cURL 导入后自动触发 AI 分析（文档 + 断言），与其他创建路径保持一致
    redis = await get_redis()
    await service.enqueue_analyze(api.id, redis, force=False)

    # 差异检测：cURL 导入后对比已分析的同路径 API，覆盖所有接口修改方式
    await _detect_diff_for_api(db, api.id, project_id)

    # 审计日志
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.CREATE,
        resource=AuditResource.API, resource_id=api.id, resource_name=api.name,
        ip=_get_client_ip(request), extra={"source": "curl"},
    )

    return api.model_dump()
