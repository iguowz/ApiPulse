from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import ensure_project_access, get_current_user, visible_project_id
from config.database import get_db
from services.mock_service import MockServiceManager

router = APIRouter(tags=["MockServices"])


def get_manager(db: AsyncIOMotorDatabase = Depends(get_db)) -> MockServiceManager:
    return MockServiceManager(db)


def _username(user: dict[str, Any] | None) -> str:
    return (user or {}).get("username", "")


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


@router.get("/mock-services")
async def list_mock_services(
    project_id: str = "default",
    current_user: dict = Depends(get_current_user),
    manager: MockServiceManager = Depends(get_manager),
):
    project_id = visible_project_id(current_user, project_id)
    return {"items": await manager.list_services(project_id)}


@router.post("/mock-services", status_code=201)
async def create_mock_service(
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    manager: MockServiceManager = Depends(get_manager),
):
    project_id = visible_project_id(current_user, body.get("project_id", "default"))
    return await manager.create_service(body, project_id, _username(current_user))


@router.get("/mock-services/{service_id}")
async def get_mock_service(
    service_id: str,
    current_user: dict = Depends(get_current_user),
    manager: MockServiceManager = Depends(get_manager),
):
    service = await manager.get_service(service_id)
    if not service:
        raise HTTPException(404, "Mock service not found")
    ensure_project_access(current_user, service.get("project_id"))
    return service


@router.put("/mock-services/{service_id}")
async def update_mock_service(
    service_id: str,
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    manager: MockServiceManager = Depends(get_manager),
):
    service = await manager.get_service(service_id)
    if not service:
        raise HTTPException(404, "Mock service not found")
    ensure_project_access(current_user, service.get("project_id"))
    return await manager.update_service(service_id, body, _username(current_user))


@router.delete("/mock-services/{service_id}")
async def delete_mock_service(
    service_id: str,
    current_user: dict = Depends(get_current_user),
    manager: MockServiceManager = Depends(get_manager),
):
    service = await manager.get_service(service_id)
    if not service:
        raise HTTPException(404, "Mock service not found")
    ensure_project_access(current_user, service.get("project_id"))
    return {"deleted": await manager.delete_service(service_id)}


@router.post("/mock-services/{service_id}/access-key/rotate")
async def rotate_mock_service_key(
    service_id: str,
    current_user: dict = Depends(get_current_user),
    manager: MockServiceManager = Depends(get_manager),
):
    service = await manager.get_service(service_id)
    if not service:
        raise HTTPException(404, "Mock service not found")
    ensure_project_access(current_user, service.get("project_id"))
    return await manager.rotate_access_key(service_id, _username(current_user))


@router.get("/mock-services/{service_id}/stats")
async def get_mock_service_stats(
    service_id: str,
    current_user: dict = Depends(get_current_user),
    manager: MockServiceManager = Depends(get_manager),
):
    service = await manager.get_service(service_id)
    if not service:
        raise HTTPException(404, "Mock service not found")
    ensure_project_access(current_user, service.get("project_id"))
    return await manager.stats(service_id)


@router.get("/mock-services/{service_id}/routes")
async def list_mock_routes(
    service_id: str,
    current_user: dict = Depends(get_current_user),
    manager: MockServiceManager = Depends(get_manager),
):
    service = await manager.get_service(service_id)
    if not service:
        raise HTTPException(404, "Mock service not found")
    ensure_project_access(current_user, service.get("project_id"))
    return {"items": await manager.list_routes(service_id)}


@router.post("/mock-services/{service_id}/routes", status_code=201)
async def create_mock_route(
    service_id: str,
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    manager: MockServiceManager = Depends(get_manager),
):
    service = await manager.get_service(service_id)
    if not service:
        raise HTTPException(404, "Mock service not found")
    ensure_project_access(current_user, service.get("project_id"))
    return await manager.create_route(service, body)


@router.put("/mock-services/{service_id}/routes/{route_id}")
async def update_mock_route(
    service_id: str,
    route_id: str,
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    manager: MockServiceManager = Depends(get_manager),
):
    service = await manager.get_service(service_id)
    if not service:
        raise HTTPException(404, "Mock service not found")
    ensure_project_access(current_user, service.get("project_id"))
    route = await manager.update_route(service_id, route_id, body)
    if not route:
        raise HTTPException(404, "Mock route not found")
    return route


@router.delete("/mock-services/{service_id}/routes/{route_id}")
async def delete_mock_route(
    service_id: str,
    route_id: str,
    current_user: dict = Depends(get_current_user),
    manager: MockServiceManager = Depends(get_manager),
):
    service = await manager.get_service(service_id)
    if not service:
        raise HTTPException(404, "Mock service not found")
    ensure_project_access(current_user, service.get("project_id"))
    deleted = await manager.delete_route(service_id, route_id)
    if not deleted:
        raise HTTPException(404, "Mock route not found")
    return {"deleted": True}


@router.post("/mock-services/{service_id}/routes/import-api", status_code=201)
async def import_api_as_mock_route(
    service_id: str,
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    manager: MockServiceManager = Depends(get_manager),
):
    service = await manager.get_service(service_id)
    if not service:
        raise HTTPException(404, "Mock service not found")
    ensure_project_access(current_user, service.get("project_id"))
    return await manager.import_api_route(service, body.get("api_id", ""))


@router.post("/mock-services/{service_id}/routes/from-traffic", status_code=201)
async def create_mock_route_from_traffic(
    service_id: str,
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    manager: MockServiceManager = Depends(get_manager),
):
    service = await manager.get_service(service_id)
    if not service:
        raise HTTPException(404, "Mock service not found")
    ensure_project_access(current_user, service.get("project_id"))
    return await manager.route_from_traffic(service, body.get("record_id", ""), enabled=bool(body.get("enabled", False)))


@router.post("/mock-services/{service_id}/test")
async def test_mock_service(
    service_id: str,
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    manager: MockServiceManager = Depends(get_manager),
):
    service = await manager.get_service(service_id)
    if not service:
        raise HTTPException(404, "Mock service not found")
    ensure_project_access(current_user, service.get("project_id"))
    req = {
        "method": body.get("method", "GET"),
        "path": body.get("path", "/"),
        "query": body.get("query") or {},
        "headers": body.get("headers") or {},
        "cookies": body.get("cookies") or {},
        "body": body.get("body"),
    }
    return await manager.evaluate(service, req, write_log=False)


@router.post("/mock-services/{service_id}/execute")
async def execute_mock_service(
    service_id: str,
    request: Request,
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    manager: MockServiceManager = Depends(get_manager),
):
    service = await manager.get_service(service_id)
    if not service:
        raise HTTPException(404, "Mock service not found")
    ensure_project_access(current_user, service.get("project_id"))
    req = {
        "method": body.get("method", "GET"),
        "path": body.get("path", "/"),
        "url": body.get("url", ""),
        "query": body.get("query") or {},
        "headers": body.get("headers") or {},
        "cookies": body.get("cookies") or {},
        "body": body.get("body"),
    }
    return await manager.evaluate(service, req, client_ip=_client_ip(request), write_log=True)


@router.get("/mock-services/{service_id}/logs")
async def list_mock_logs(
    service_id: str,
    status_code: int | None = Query(None),
    matched: bool | None = Query(None),
    route_id: str = "",
    limit: int = 100,
    current_user: dict = Depends(get_current_user),
    manager: MockServiceManager = Depends(get_manager),
):
    service = await manager.get_service(service_id)
    if not service:
        raise HTTPException(404, "Mock service not found")
    ensure_project_access(current_user, service.get("project_id"))
    return {"items": await manager.logs(service_id, status_code=status_code, matched=matched, route_id=route_id, limit=min(limit, 500))}


@router.api_route("/mock-api/{project_slug}/{service_slug}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
@router.api_route("/mock-api/{project_slug}/{service_slug}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def public_mock_service(
    project_slug: str,
    service_slug: str,
    request: Request,
    path: str = "",
    manager: MockServiceManager = Depends(get_manager),
):
    service = await manager.find_public_service(project_slug, service_slug)
    # 回退：长路由未匹配时，尝试用短路由方式查找（project_slug 作为 service_slug）
    if not service:
        service = await manager.find_public_service_by_slug(project_slug)
        if service:
            # 将原 service_slug 拼入 path 前面，保持完整路径
            path = f"{service_slug}/{path}" if path else service_slug
    if not service:
        raise HTTPException(404, "Mock service not found")
    access_key = service.get("access_key") or ""
    # mock_key 可选：仅当服务配置了 access_key 时才校验
    if access_key:
        # 同时支持 Header X-Mock-Key、查询参数 mock_key（下划线）和 mock-key（连字符）
        supplied = (
            request.headers.get("X-Mock-Key")
            or request.query_params.get("mock_key")
            or request.query_params.get("mock-key")
            or ""
        )
        if supplied != access_key:
            raise HTTPException(403, "Invalid mock access key")

    return await _eval_mock_service(service, path, request, manager)


# 简化路由：无需 project_slug，仅通过 service_slug 访问（v4.0+）
@router.api_route("/mock-api/{service_slug}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
@router.api_route("/mock-api/{service_slug}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def public_mock_service_short(
    service_slug: str,
    request: Request,
    path: str = "",
    manager: MockServiceManager = Depends(get_manager),
):
    service = await manager.find_public_service_by_slug(service_slug)
    if not service:
        raise HTTPException(404, "Mock service not found")
    access_key = service.get("access_key") or ""
    if access_key:
        # 同时支持 Header X-Mock-Key、查询参数 mock_key（下划线）和 mock-key（连字符）
        supplied = (
            request.headers.get("X-Mock-Key")
            or request.query_params.get("mock_key")
            or request.query_params.get("mock-key")
            or ""
        )
        if supplied != access_key:
            raise HTTPException(403, "Invalid mock access key")

    return await _eval_mock_service(service, path, request, manager)


async def _eval_mock_service(service: dict, path: str, request: Request, manager: MockServiceManager):
    body: Any = None
    try:
        raw = await request.body()
        if raw:
            content_type = request.headers.get("content-type", "")
            body = await request.json() if "json" in content_type else raw.decode("utf-8", errors="replace")
    except Exception:
        body = None
    start = time.time()
    result = await manager.evaluate(
        service,
        {
            "method": request.method,
            "path": "/" + path if path else "/",
            "url": str(request.url),
            "query": dict(request.query_params),
            "headers": dict(request.headers),
            "cookies": dict(request.cookies),
            "body": body,
        },
        client_ip=_client_ip(request),
    )
    result["duration_ms"] = int((time.time() - start) * 1000)
    if request.method == "HEAD" or result.get("body_type") == "empty":
        return Response(status_code=result["status_code"], headers=result["headers"])
    if result.get("body_type") == "json":
        return JSONResponse(status_code=result["status_code"], content=result["body"], headers=result["headers"])
    content = result["body"] if isinstance(result["body"], (str, bytes)) else str(result["body"])
    return Response(status_code=result["status_code"], content=content, headers=result["headers"])
