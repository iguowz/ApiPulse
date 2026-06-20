"""
告警记录路由
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db
from api.deps import require_permission, visible_project_id

router = APIRouter(tags=["Alerts"])


@router.get("/alerts")
async def list_alerts(
    project_id: str | None = None,
    monitor_id: str | None = None,
    is_recovery: bool | None = None,
    risk_level: str | None = None,
    ai_severity: str | None = None,
    skip: int = 0,
    limit: int = 50,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_permission("monitor:read")),
):
    project_id = visible_project_id(current_user, project_id)
    q: dict[str, Any] = {"project_id": project_id}
    # project_id 按项目隔离告警数据，前端 Dashboard 和 Monitor 页面均依赖此过滤
    if monitor_id:
        q["monitor_id"] = monitor_id
    if is_recovery is not None:
        q["is_recovery"] = is_recovery
    if risk_level:
        q["risk_level"] = risk_level
    if ai_severity:
        q["ai_severity"] = ai_severity
    docs = await db["alert_records"].find(q, {"_id": 0}).sort("sent_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db["alert_records"].count_documents(q)
    return {"total": total, "items": docs}
