"""
AI 操作日志查询路由 —— 委托 services/ai_operation_log_service.py
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db
from services.ai_operation_log_service import AiOperationLogService

router = APIRouter(tags=["AI Operation Logs"])


# 依赖注入
def get_ai_ops_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> AiOperationLogService:
    return AiOperationLogService(db)


@router.get("/ai-logs")
async def list_ai_ops_logs(
    request: Request,
    project_id: str = "default",
    api_id: str = "",
    type: str = "",
    status: str = "",
    skip: int = 0,
    limit: int = Query(50, ge=1, le=200),
    service: AiOperationLogService = Depends(get_ai_ops_service),
):
    """分页查询 AI 操作日志"""
    result = await service.list_logs(
        project_id=project_id,
        api_id=api_id,
        type=type,
        status=status,
        skip=skip,
        limit=limit,
    )
    return {**result, "skip": skip, "limit": limit}
