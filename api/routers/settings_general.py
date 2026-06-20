"""
Settings / 通用设置路由
管理 auto_trigger_ai（审核后自动触发下游AI流程）和 auto_review_flow（AI产出进入审核流程）开关。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db

router = APIRouter(tags=["Settings"])

# 默认值：两个开关均开启
_DEFAULTS = {
    "auto_trigger_ai": True,
    "auto_review_flow": True,
}


@router.get("/settings/general")
async def get_general_settings(
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """获取通用设置（auto_trigger_ai / auto_review_flow），无记录时返回默认值"""
    doc = await db["settings"].find_one({"key": "general_settings"})
    if doc:
        return {
            "auto_trigger_ai": doc.get("auto_trigger_ai", _DEFAULTS["auto_trigger_ai"]),
            "auto_review_flow": doc.get("auto_review_flow", _DEFAULTS["auto_review_flow"]),
        }
    return dict(_DEFAULTS)


@router.put("/settings/general")
async def update_general_settings(
    data: dict[str, Any] = Body(...),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """保存通用设置"""
    auto_trigger_ai = bool(data.get("auto_trigger_ai", _DEFAULTS["auto_trigger_ai"]))
    auto_review_flow = bool(data.get("auto_review_flow", _DEFAULTS["auto_review_flow"]))

    config_doc = {
        "key": "general_settings",
        "auto_trigger_ai": auto_trigger_ai,
        "auto_review_flow": auto_review_flow,
        "updated_at": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None).isoformat(),
    }
    await db["settings"].update_one(
        {"key": "general_settings"},
        {"$set": config_doc},
        upsert=True,
    )
    return {
        "status": "saved",
        "auto_trigger_ai": auto_trigger_ai,
        "auto_review_flow": auto_review_flow,
    }
