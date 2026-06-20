"""
导入差异告警路由 —— 差异记录查询、详情、对比、处理
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db, get_redis
from models.audit import AuditAction, AuditResource
from services.diff_service import DiffService
from services.audit_service import AuditService
from api.deps import _get_user_from_request, _get_client_ip

router = APIRouter(prefix="/diff-alerts", tags=["Diff Alerts"])


# 依赖注入
async def get_diff_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> DiffService:
    redis = await get_redis()
    return DiffService(db, redis)


def get_audit_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> AuditService:
    return AuditService(db)


@router.get("")
async def list_diff_alerts(
    project_id: str = Query(default="default"),
    status: str | None = Query(default=None, description="按状态过滤: pending/confirmed/auto_fixed/dismissed"),
    severity: str | None = Query(default=None, description="按严重程度过滤: low/medium/high/critical"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    service: DiffService = Depends(get_diff_service),
):
    """分页查询差异告警列表，支持按状态和严重程度过滤"""
    return await service.list_diffs(
        project_id=project_id, status=status, severity=severity,
        skip=skip, limit=limit,
    )


@router.get("/count")
async def count_diff_alerts(
    project_id: str = Query(default="default"),
    status: str | None = Query(default="pending", description="按状态过滤，默认统计 pending"),
    service: DiffService = Depends(get_diff_service),
):
    """统计差异记录数量，用于前端徽章展示（默认统计待处理数）"""
    total = await service.count_diffs(project_id=project_id, status=status)
    return {"total": total}


@router.get("/{diff_id}")
async def get_diff_alert(
    diff_id: str,
    service: DiffService = Depends(get_diff_service),
):
    """获取单条差异记录详情"""
    diff = await service.get_diff(diff_id)
    if not diff:
        raise HTTPException(404, "差异记录不存在")
    return diff


@router.get("/{diff_id}/comparison")
async def get_diff_comparison(
    diff_id: str,
    service: DiffService = Depends(get_diff_service),
):
    """获取差异对比数据：返回新旧两个 API 的文档 JSON 供前端展示"""
    data = await service.get_diff_comparison(diff_id)
    if not data:
        raise HTTPException(404, "差异记录不存在或关联 API 已删除")
    return data


@router.put("/{diff_id}/resolve")
async def resolve_diff_alert(
    diff_id: str,
    payload: dict[str, Any] = Body(...),
    request: Request = None,
    service: DiffService = Depends(get_diff_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    """
    用户手动处理差异记录。
    payload: { "action": "dismiss" | "confirm" }
    """
    action = payload.get("action", "")
    if action not in ("dismiss", "confirm"):
        raise HTTPException(400, f"不支持的操作: {action}，可选: dismiss, confirm")

    ok = await service.resolve_diff(diff_id, action)
    if not ok:
        raise HTTPException(400, "处理失败，差异记录不存在或操作无效")

    # 审计日志：记录差异处理操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.UPDATE,
        resource=AuditResource.DIFF_ALERT, resource_id=diff_id, resource_name=diff_id,
        ip=_get_client_ip(request), extra={"action": action},
    )
    return {"status": "ok", "action": action}
