"""
Bot 配置 CRUD 路由 —— Phase 3 Bot 集成。

提供机器人接入配置的增删改查，供前端设置页面使用。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from config.database import get_db
from models.bot_config import BotConfig, BotPlatform
from api.deps import get_current_user, user_has_permission

router = APIRouter(prefix="/bot-configs", tags=["bot-configs"])


class BotConfigCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    platform: str = Field(..., description="wecom/qq/feishu")
    project_id: str = Field(default="default")
    verify_token: str = Field(default="")
    app_secret: str = Field(default="")
    encoding_aes_key: str = Field(default="")
    corp_id: str = Field(default="")


class BotConfigUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    verify_token: str | None = None
    app_secret: str | None = None
    encoding_aes_key: str | None = None
    corp_id: str | None = None


@router.get("", summary="获取 Bot 配置列表")
async def list_bot_configs(
    project_id: str = Query(default="", description="按项目筛选"),
    platform: str = Query(default="", description="按平台筛选"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
):
    """获取当前用户的 Bot 配置列表，支持按项目、平台筛选，分页返回。"""
    filt: dict[str, Any] = {}
    if project_id:
        filt["project_id"] = project_id
    if platform and platform in ("wecom", "qq", "feishu"):
        filt["platform"] = platform

    total = await db["bot_configs"].count_documents(filt)
    cursor = db["bot_configs"].find(filt, {"_id": 0}).sort("created_at", -1)
    cursor.skip((page - 1) * page_size).limit(page_size)
    items = await cursor.to_list(page_size)

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    }


@router.post("", summary="创建 Bot 配置")
async def create_bot_config(
    data: BotConfigCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
):
    """创建新的机器人接入配置。"""
    if not user_has_permission(user, "bot:manage"):
        raise HTTPException(status_code=403, detail="无权管理 Bot 配置")

    if data.platform not in ("wecom", "qq", "feishu"):
        raise HTTPException(status_code=422, detail=f"不支持的平台: {data.platform}")

    now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    config = BotConfig(
        id=uuid.uuid4().hex[:12],
        project_id=data.project_id,
        platform=BotPlatform(data.platform),
        name=data.name,
        enabled=True,
        verify_token=data.verify_token,
        app_secret=data.app_secret,
        encoding_aes_key=data.encoding_aes_key,
        corp_id=data.corp_id,
        created_at=now,
        updated_at=now,
    )
    doc = config.model_dump()
    await db["bot_configs"].insert_one(doc)
    doc.pop("_id", None)
    logger.info(f"Bot config created: id={config.id}, platform={data.platform}, name={data.name}")
    return doc


@router.put("/{config_id}", summary="更新 Bot 配置")
async def update_bot_config(
    config_id: str,
    data: BotConfigUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
):
    """更新 Bot 配置。"""
    if not user_has_permission(user, "bot:manage"):
        raise HTTPException(status_code=403, detail="无权管理 Bot 配置")

    old = await db["bot_configs"].find_one({"id": config_id})
    if not old:
        raise HTTPException(status_code=404, detail="Bot 配置不存在")

    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    updates["updated_at"] = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    await db["bot_configs"].update_one({"id": config_id}, {"$set": updates})

    updated = await db["bot_configs"].find_one({"id": config_id}, {"_id": 0})
    logger.info(f"Bot config updated: id={config_id}, fields={list(updates.keys())}")
    return updated


@router.delete("/{config_id}", summary="删除 Bot 配置")
async def delete_bot_config(
    config_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
):
    """删除 Bot 配置。"""
    if not user_has_permission(user, "bot:manage"):
        raise HTTPException(status_code=403, detail="无权管理 Bot 配置")

    result = await db["bot_configs"].delete_one({"id": config_id})
    if not result.deleted_count:
        raise HTTPException(status_code=404, detail="Bot 配置不存在")
    logger.info(f"Bot config deleted: id={config_id}")
    return {"ok": True, "message": "已删除"}


@router.get("/{config_id}/webhook-url", summary="获取 Webhook URL")
async def get_webhook_url(
    config_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
):
    """获取 Bot 的 Webhook 回调 URL（供用户复制到平台后台配置）。"""
    config = await db["bot_configs"].find_one({"id": config_id})
    if not config:
        raise HTTPException(status_code=404, detail="Bot 配置不存在")

    from config.settings import get_settings
    s = get_settings()
    # 从 settings 获取部署域名，默认 localhost
    base_url = getattr(s, "deploy_host", "http://localhost:8000")
    platform = config.get("platform", "wecom")
    webhook_url = f"{base_url}/bot/webhook/{platform}?config_id={config_id}"
    return {"webhook_url": webhook_url, "config_id": config_id}
