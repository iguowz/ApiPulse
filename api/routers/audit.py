"""
操作审计日志查询路由 —— 委托 services/audit_service.py
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db
from services.audit_service import AuditService

router = APIRouter(tags=["Audit"])


# 依赖注入
def get_audit_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> AuditService:
    return AuditService(db)


@router.get("/audit-logs")
async def list_audit_logs(
    request: Request,
    project_id: str = "default",
    skip: int = 0,
    limit: int = Query(50, ge=1, le=200),
    resource: str = "",
    action: str = "",
    username: str = "",
    service: AuditService = Depends(get_audit_service),
):
    """分页查询操作审计日志"""
    logs = await service.list_logs(
        project_id=project_id, skip=skip, limit=limit,
        resource=resource, action=action, username=username,
    )
    total = await service.count_logs(
        project_id=project_id, resource=resource,
        action=action, username=username,
    )
    return {"logs": logs, "total": total, "skip": skip, "limit": limit}
