from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import ensure_project_access, get_current_user, visible_project_id
from config.database import get_db
from services.sql_runtime_service import SqlRuntimeService

router = APIRouter(tags=["DatabaseServices"])


def get_sql_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> SqlRuntimeService:
    return SqlRuntimeService(db)


def _username(user: dict[str, Any] | None) -> str:
    return (user or {}).get("username", "")


@router.get("/database-services")
async def list_database_services(
    project_id: str = "default",
    current_user: dict = Depends(get_current_user),
    service: SqlRuntimeService = Depends(get_sql_service),
):
    project_id = visible_project_id(current_user, project_id)
    return {"items": await service.list_services(project_id)}


@router.post("/database-services", status_code=201)
async def create_database_service(
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    service: SqlRuntimeService = Depends(get_sql_service),
):
    project_id = visible_project_id(current_user, body.get("project_id", "default"))
    return await service.create_service(body, project_id, _username(current_user))


@router.put("/database-services/{service_id}")
async def update_database_service(
    service_id: str,
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    service: SqlRuntimeService = Depends(get_sql_service),
):
    old = await service.get_service(service_id)
    if not old:
        raise HTTPException(404, "Database service not found")
    ensure_project_access(current_user, old.get("project_id"))
    updated = await service.update_service(service_id, body, _username(current_user))
    if not updated:
        raise HTTPException(404, "Database service not found")
    return updated


@router.delete("/database-services/{service_id}")
async def delete_database_service(
    service_id: str,
    current_user: dict = Depends(get_current_user),
    service: SqlRuntimeService = Depends(get_sql_service),
):
    old = await service.get_service(service_id)
    if not old:
        raise HTTPException(404, "Database service not found")
    ensure_project_access(current_user, old.get("project_id"))
    return {"deleted": await service.delete_service(service_id)}


@router.post("/database-services/{service_id}/test")
async def test_database_service(
    service_id: str,
    current_user: dict = Depends(get_current_user),
    service: SqlRuntimeService = Depends(get_sql_service),
):
    old = await service.get_service(service_id)
    if not old:
        raise HTTPException(404, "Database service not found")
    ensure_project_access(current_user, old.get("project_id"))
    return await service.test_service(service_id, old.get("project_id", "default"))


@router.get("/sql-snippets")
async def list_sql_snippets(
    project_id: str = "default",
    db_service_id: str = "",
    current_user: dict = Depends(get_current_user),
    service: SqlRuntimeService = Depends(get_sql_service),
):
    project_id = visible_project_id(current_user, project_id)
    return {"items": await service.list_snippets(project_id, db_service_id=db_service_id)}


@router.post("/sql-snippets", status_code=201)
async def create_sql_snippet(
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    service: SqlRuntimeService = Depends(get_sql_service),
):
    project_id = visible_project_id(current_user, body.get("project_id", "default"))
    return await service.create_snippet(body, project_id, _username(current_user))


@router.put("/sql-snippets/{snippet_id}")
async def update_sql_snippet(
    snippet_id: str,
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    service: SqlRuntimeService = Depends(get_sql_service),
):
    old = await service.get_snippet(snippet_id)
    if not old:
        raise HTTPException(404, "SQL snippet not found")
    ensure_project_access(current_user, old.get("project_id"))
    updated = await service.update_snippet(snippet_id, body, _username(current_user))
    if not updated:
        raise HTTPException(404, "SQL snippet not found")
    return updated


@router.delete("/sql-snippets/{snippet_id}")
async def delete_sql_snippet(
    snippet_id: str,
    current_user: dict = Depends(get_current_user),
    service: SqlRuntimeService = Depends(get_sql_service),
):
    old = await service.get_snippet(snippet_id)
    if not old:
        raise HTTPException(404, "SQL snippet not found")
    ensure_project_access(current_user, old.get("project_id"))
    return {"deleted": await service.delete_snippet(snippet_id)}


@router.post("/sql-snippets/{snippet_id}/run")
async def run_sql_snippet(
    snippet_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
    current_user: dict = Depends(get_current_user),
    service: SqlRuntimeService = Depends(get_sql_service),
):
    old = await service.get_snippet(snippet_id)
    if not old:
        raise HTTPException(404, "SQL snippet not found")
    ensure_project_access(current_user, old.get("project_id"))
    return await service.run_snippet(old.get("project_id", "default"), snippet_id, params=body.get("params") or {}, context=body.get("context") or {})


@router.post("/sql/run")
async def run_sql(
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    service: SqlRuntimeService = Depends(get_sql_service),
):
    project_id = visible_project_id(current_user, body.get("project_id", "default"))
    return await service.run_sql(
        project_id=project_id,
        db_service_id=body.get("db_service_id", ""),
        sql_text=body.get("sql") or body.get("sql_text") or "",
        params=body.get("params") or {},
        context=body.get("context") or {},
        timeout_ms=body.get("timeout_ms"),
        max_rows=body.get("max_rows"),
    )


@router.post("/sql/validate")
async def validate_sql(
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    service: SqlRuntimeService = Depends(get_sql_service),
):
    project_id = visible_project_id(current_user, body.get("project_id", "default"))
    query = body.get("query")
    if isinstance(query, dict):
        return await service.validate_ref(project_id, query, context=body.get("context") or {})
    return await service.validate_sql(
        project_id=project_id,
        db_service_id=body.get("db_service_id", ""),
        sql_text=body.get("sql") or body.get("sql_text") or "",
        params=body.get("params") or {},
        context=body.get("context") or {},
    )
