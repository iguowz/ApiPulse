from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import ensure_project_access, get_current_user, user_has_permission, visible_project_id
from config.database import get_db, get_redis
from services.mock_service import TrafficService

router = APIRouter(tags=["Traffic"])


def get_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> TrafficService:
    return TrafficService(db)


def _username(user: dict[str, Any] | None) -> str:
    return (user or {}).get("username", "")


@router.post("/traffic/ingest")
async def ingest_traffic(
    body: dict[str, Any] = Body(...),
    request: Request = None,
    service: TrafficService = Depends(get_service),
):
    redis = await get_redis()
    user = getattr(request.state, "user", None) if request else None
    trusted_project_id = None
    if user:
        if not user_has_permission(user, "traffic:manage"):
            raise HTTPException(403, "Permission denied")
        trusted_project_id = visible_project_id(user, body.get("project_id", "default"))
    return await service.ingest(body, redis, trusted_project_id=trusted_project_id)


@router.post("/traffic/batch-ingest")
async def batch_ingest_traffic(
    body: dict[str, Any] = Body(...),
    request: Request = None,
    service: TrafficService = Depends(get_service),
):
    redis = await get_redis()
    user = getattr(request.state, "user", None) if request else None
    trusted_project_id = visible_project_id(user, body.get("project_id", "default")) if user else None
    if user and not user_has_permission(user, "traffic:manage"):
        raise HTTPException(403, "Permission denied")
    items = body.get("items") or []
    results = []
    for item in items:
        results.append(await service.ingest(
            {
                **item,
                "project_id": item.get("project_id") or body.get("project_id"),
                "access_key": item.get("access_key") or body.get("access_key"),
                "source_id": item.get("source_id") or body.get("source_id"),
            },
            redis,
            trusted_project_id=trusted_project_id,
        ))
    return {"total": len(items), "results": results}


@router.get("/traffic/records")
async def list_traffic_records(
    project_id: str = "default",
    limit: int = 100,
    source_id: str = "",
    method: str = "",
    path: str = "",
    status_code: int | None = Query(None),
    current_user: dict = Depends(get_current_user),
    service: TrafficService = Depends(get_service),
):
    project_id = visible_project_id(current_user, project_id)
    return {"items": await service.records(project_id, limit=min(limit, 500), source_id=source_id, method=method, path=path, status_code=status_code)}


@router.get("/traffic/sources")
async def list_sources(
    project_id: str = "default",
    current_user: dict = Depends(get_current_user),
    service: TrafficService = Depends(get_service),
):
    project_id = visible_project_id(current_user, project_id)
    return {"items": await service.list_sources(project_id)}


@router.post("/traffic/sources", status_code=201)
async def create_source(
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    service: TrafficService = Depends(get_service),
):
    project_id = visible_project_id(current_user, body.get("project_id", "default"))
    return await service.create_source(body, project_id, _username(current_user))


@router.post("/traffic/sources/{source_id}/access-key/rotate")
async def rotate_source_key(
    source_id: str,
    current_user: dict = Depends(get_current_user),
    service: TrafficService = Depends(get_service),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    old = await db["traffic_sources"].find_one({"id": source_id}, {"_id": 0})
    if not old:
        raise HTTPException(404, "Traffic source not found")
    ensure_project_access(current_user, old.get("project_id"))
    updated = await service.rotate_source_key(source_id, _username(current_user))
    if not updated:
        raise HTTPException(404, "Traffic source not found")
    return updated


@router.put("/traffic/sources/{source_id}")
async def update_source(
    source_id: str,
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    service: TrafficService = Depends(get_service),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    old = await db["traffic_sources"].find_one({"id": source_id}, {"_id": 0})
    if not old:
        raise HTTPException(404, "Traffic source not found")
    ensure_project_access(current_user, old.get("project_id"))
    updated = await service.update_source(source_id, body, _username(current_user))
    if not updated:
        raise HTTPException(404, "Traffic source not found")
    return updated


@router.delete("/traffic/sources/{source_id}")
async def delete_source(
    source_id: str,
    current_user: dict = Depends(get_current_user),
    service: TrafficService = Depends(get_service),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    old = await db["traffic_sources"].find_one({"id": source_id}, {"_id": 0})
    if not old:
        raise HTTPException(404, "Traffic source not found")
    ensure_project_access(current_user, old.get("project_id"))
    return {"deleted": await service.delete_source(source_id)}


@router.get("/traffic/rules")
async def list_rules(
    project_id: str = "default",
    source_id: str = "",
    current_user: dict = Depends(get_current_user),
    service: TrafficService = Depends(get_service),
):
    project_id = visible_project_id(current_user, project_id)
    return {"items": await service.list_rules(project_id, source_id=source_id)}


@router.post("/traffic/rules", status_code=201)
async def create_rule(
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    service: TrafficService = Depends(get_service),
):
    project_id = visible_project_id(current_user, body.get("project_id", "default"))
    return await service.create_rule(body, project_id)


@router.post("/traffic/rules/test")
async def test_rule(
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    service: TrafficService = Depends(get_service),
):
    project_id = visible_project_id(current_user, body.get("project_id", "default"))
    rule = {**(body.get("rule") or {}), "project_id": project_id}
    return service.evaluate_rule(rule, body.get("sample") or {})


@router.put("/traffic/rules/{rule_id}")
async def update_rule(
    rule_id: str,
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    service: TrafficService = Depends(get_service),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    old = await db["traffic_rules"].find_one({"id": rule_id}, {"_id": 0})
    if not old:
        raise HTTPException(404, "Traffic rule not found")
    ensure_project_access(current_user, old.get("project_id"))
    updated = await service.update_rule(rule_id, body, _username(current_user))
    if not updated:
        raise HTTPException(404, "Traffic rule not found")
    return updated


@router.delete("/traffic/rules/{rule_id}")
async def delete_rule(
    rule_id: str,
    current_user: dict = Depends(get_current_user),
    service: TrafficService = Depends(get_service),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    old = await db["traffic_rules"].find_one({"id": rule_id}, {"_id": 0})
    if not old:
        raise HTTPException(404, "Traffic rule not found")
    ensure_project_access(current_user, old.get("project_id"))
    return {"deleted": await service.delete_rule(rule_id)}


@router.get("/traffic/proxy-config")
async def proxy_config(
    source_id: str = Query(""),
    access_key: str = Query(""),
    service: TrafficService = Depends(get_service),
):
    return await service.proxy_config(source_id=source_id, access_key=access_key)
