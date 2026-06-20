"""
告警渠道 CRUD 路由
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db
from models.audit import AuditAction, AuditResource
from services.audit_service import AuditService
from api.deps import _get_user_from_request, _get_client_ip

router = APIRouter(tags=["AlertChannels"])


def get_audit_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> AuditService:
    return AuditService(db)


@router.post("/alert-channels", status_code=201)
async def create_alert_channel(
    data: dict[str, Any] = Body(...),
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    audit_service: AuditService = Depends(get_audit_service),
):
    """创建告警渠道（钉钉/企微/Slack/自定义 webhook）"""
    channel_id = str(uuid.uuid4())
    now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    channel = {
        "id": channel_id,
        "name": data.get("name", "未命名渠道"),
        "type": data.get("type", "custom"),  # dingtalk / wechat / slack / custom
        "url": data.get("url", ""),
        "enabled": data.get("enabled", True),
        "project_id": data.get("project_id", "default"),
        "created_at": now,
        "updated_at": now,
    }
    if not channel["url"]:
        raise HTTPException(400, "url is required")
    await db["alert_channels"].insert_one(channel)
    # Bug 修复：insert_one 会给 channel dict 添加 _id（ObjectId 类型），
    # 直接返回会导致 FastAPI JSON 序列化失败（ObjectId 不可序列化），
    # 表现为前端报错但数据实际已入库。返回前 pop 掉 _id。
    channel.pop("_id", None)
    # 审计日志：记录告警渠道创建操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.CREATE,
        resource=AuditResource.ALERT_CHANNEL, resource_id=channel_id, resource_name=channel["name"],
        ip=_get_client_ip(request),
    )
    return channel


@router.get("/alert-channels")
async def list_alert_channels(
    project_id: str | None = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """列出所有告警渠道"""
    q: dict[str, Any] = {}
    if project_id:
        q["project_id"] = project_id
    docs = await db["alert_channels"].find(q, {"_id": 0}).to_list(200)
    total = await db["alert_channels"].count_documents(q)
    return {"items": docs, "total": total}


@router.get("/alert-channels/{channel_id}")
async def get_alert_channel(channel_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    doc = await db["alert_channels"].find_one({"id": channel_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Alert channel not found")
    return doc


@router.put("/alert-channels/{channel_id}")
async def update_alert_channel(
    channel_id: str,
    data: dict[str, Any] = Body(...),
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    audit_service: AuditService = Depends(get_audit_service),
):
    data.pop("id", None)
    data["updated_at"] = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    r = await db["alert_channels"].update_one({"id": channel_id}, {"$set": data})
    if not r.matched_count:
        raise HTTPException(404, "Alert channel not found")
    # 审计日志：记录告警渠道更新操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.UPDATE,
        resource=AuditResource.ALERT_CHANNEL, resource_id=channel_id, resource_name=data.get("name", channel_id),
        ip=_get_client_ip(request),
    )
    return {"updated": True}


@router.delete("/alert-channels/{channel_id}")
async def delete_alert_channel(
    channel_id: str,
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    audit_service: AuditService = Depends(get_audit_service),
):
    r = await db["alert_channels"].delete_one({"id": channel_id})
    if not r.deleted_count:
        raise HTTPException(404, "Alert channel not found")
    # 审计日志：记录告警渠道删除操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.DELETE,
        resource=AuditResource.ALERT_CHANNEL, resource_id=channel_id, resource_name=channel_id,
        ip=_get_client_ip(request),
    )
    return {"deleted": True}
