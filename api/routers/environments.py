"""
执行环境 CRUD 路由
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db
from models.dsl import Environment
from models.audit import AuditAction, AuditResource
from services.audit_service import AuditService
from api.deps import _get_user_from_request, _get_client_ip

router = APIRouter(tags=["Environments"])


def get_audit_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> AuditService:
    return AuditService(db)


async def _check_duplicate_url(db: AsyncIOMotorDatabase, project_id: str, base_url: str, exclude_id: str | None = None) -> None:
    """校验同一项目内 base_url 唯一，防止不同环境配置指向相同 URL"""
    if not base_url:
        return
    q: dict[str, Any] = {"project_id": project_id, "base_url": base_url}
    if exclude_id:
        q["id"] = {"$ne": exclude_id}
    if await db["environments"].count_documents(q) > 0:
        raise HTTPException(409, f"项目中已存在 base_url 为 '{base_url}' 的环境，请使用不同的 URL")


@router.get("/environments")
async def list_environments(
    project_id: str = "default",
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """获取指定项目的所有执行环境列表"""
    q = {"project_id": project_id}
    docs = await db["environments"].find(q, {"_id": 0}).sort("created_at", -1).to_list(100)
    return docs


@router.post("/environments", status_code=201)
async def create_environment(
    env: Environment = Body(...),
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    audit_service: AuditService = Depends(get_audit_service),
):
    """创建新的执行环境"""
    await _check_duplicate_url(db, env.project_id, env.base_url)
    env.id = str(uuid.uuid4())
    env.created_at = env.updated_at = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    await db["environments"].insert_one(env.model_dump())
    # 审计日志：记录环境创建操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.CREATE,
        resource=AuditResource.ENVIRONMENT, resource_id=env.id, resource_name=env.name,
        ip=_get_client_ip(request),
    )
    return env


@router.get("/environments/{env_id}")
async def get_environment(env_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    """获取单个执行环境详情"""
    doc = await db["environments"].find_one({"id": env_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Environment not found")
    return doc


@router.put("/environments/{env_id}")
async def update_environment(
    env_id: str,
    data: dict[str, Any] = Body(...),
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    audit_service: AuditService = Depends(get_audit_service),
):
    """更新执行环境配置"""
    data.pop("id", None)
    # 校验 base_url 不与其他环境重复（仅当更新包含 base_url 时）
    if "base_url" in data:
        existing = await db["environments"].find_one({"id": env_id}, {"project_id": 1})
        if existing:
            await _check_duplicate_url(db, existing["project_id"], data["base_url"], exclude_id=env_id)
    data["updated_at"] = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    r = await db["environments"].update_one({"id": env_id}, {"$set": data})
    if not r.matched_count:
        raise HTTPException(404, "Environment not found")
    # 审计日志：记录环境更新操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.UPDATE,
        resource=AuditResource.ENVIRONMENT, resource_id=env_id, resource_name=data.get("name", env_id),
        ip=_get_client_ip(request),
    )
    return {"updated": True}


@router.delete("/environments/{env_id}")
async def delete_environment(
    env_id: str,
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    audit_service: AuditService = Depends(get_audit_service),
):
    """删除执行环境"""
    r = await db["environments"].delete_one({"id": env_id})
    if not r.deleted_count:
        raise HTTPException(404, "Environment not found")
    # 审计日志：记录环境删除操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.DELETE,
        resource=AuditResource.ENVIRONMENT, resource_id=env_id, resource_name=env_id,
        ip=_get_client_ip(request),
    )
    return {"deleted": True}
