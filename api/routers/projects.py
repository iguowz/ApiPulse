"""
项目 CRUD 路由
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db
from models.project import Project
from models.audit import AuditAction, AuditResource
from services.audit_service import AuditService
from api.deps import _get_user_from_request, _get_client_ip

router = APIRouter(tags=["Projects"])


def get_audit_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> AuditService:
    return AuditService(db)


@router.post("/projects", status_code=201)
async def create_project(
    project: Project,
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    audit_service: AuditService = Depends(get_audit_service),
):
    project.id = str(uuid.uuid4())
    project.created_at = project.updated_at = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    if await db["projects"].find_one({"slug": project.slug}):
        raise HTTPException(409, f"Slug '{project.slug}' already exists")
    await db["projects"].insert_one(project.model_dump())
    # 审计日志：记录项目创建操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.CREATE,
        resource=AuditResource.PROJECT, resource_id=project.id, resource_name=project.name,
        ip=_get_client_ip(request),
    )
    return project


@router.get("/projects")
async def list_projects(
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    # 非管理员用户仅返回其所属项目，实现项目级数据隔离
    from api.deps import _get_user_from_request
    user = _get_user_from_request(request)
    if user and user.get("role") != "admin":
        user_project = user.get("project_id", "")
        if user_project:
            return await db["projects"].find({"id": user_project}, {"_id": 0}).to_list(200)
        return []  # 用户未分配项目时返回空列表
    return await db["projects"].find({}, {"_id": 0}).to_list(200)


@router.get("/projects/{project_id}")
async def get_project(project_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    doc = await db["projects"].find_one({"id": project_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Project not found")
    return doc


@router.put("/projects/{project_id}")
async def update_project(
    project_id: str,
    data: dict[str, Any] = Body(...),
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    audit_service: AuditService = Depends(get_audit_service),
):
    data.pop("id", None)
    # slug 变更时校验唯一性，避免多项目 slug 冲突
    if "slug" in data:
        existing = await db["projects"].find_one({"slug": data["slug"], "id": {"$ne": project_id}})
        if existing:
            raise HTTPException(409, f"Slug '{data['slug']}' already exists")
    data["updated_at"] = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    r = await db["projects"].update_one({"id": project_id}, {"$set": data})
    if not r.matched_count:
        raise HTTPException(404, "Project not found")
    # 审计日志：记录项目更新操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.UPDATE,
        resource=AuditResource.PROJECT, resource_id=project_id, resource_name=data.get("name", project_id),
        ip=_get_client_ip(request),
    )
    return {"updated": True}


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: str,
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    audit_service: AuditService = Depends(get_audit_service),
):
    r = await db["projects"].delete_one({"id": project_id})
    if not r.deleted_count:
        raise HTTPException(404, "Project not found")
    # 审计日志：记录项目删除操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.DELETE,
        resource=AuditResource.PROJECT, resource_id=project_id, resource_name=project_id,
        ip=_get_client_ip(request),
    )
    return {"deleted": True}
