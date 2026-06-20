"""
Capture (mitmproxy 实时抓包) 路由 —— 委托 services/capture_service.py
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db, get_redis
from models.audit import AuditAction, AuditResource
from services.capture_service import CaptureService
from services.diff_service import DiffService
from services.audit_service import AuditService
from api.deps import _get_user_from_request, _get_client_ip
from api.state import _capture_state, _capture_lock

router = APIRouter(tags=["Capture"])


# 依赖注入
async def get_capture_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> CaptureService:
    svc = CaptureService(db)
    # 注入差异检测服务，抓包后自动对比已分析 API 的字段变化
    try:
        redis = await get_redis()
        svc.set_diff_service(DiffService(db, redis))
    except Exception:
        logger.warning("Failed to inject DiffService into CaptureService (Redis unavailable)")
        pass  # Redis 不可用时静默跳过差异检测
    return svc


def get_audit_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> AuditService:
    return AuditService(db)


@router.post("/capture/ingest")
async def capture_ingest(
    payload: dict[str, Any] = Body(...),
    service: CaptureService = Depends(get_capture_service),
):
    """mitmproxy 插件上报的实时抓包数据，转为 ApiDSL 存入知识库"""
    # 检查抓包是否已关闭，避免禁用后仍写入数据
    async with _capture_lock:
        if not _capture_state["enabled"]:
            return {"status": "disabled"}
    try:
        redis = await get_redis()
        return await service.ingest(payload, _capture_state, _capture_lock, redis)
    except Exception as e:
        logger.error("Capture ingest failed: {}", e)
        raise HTTPException(400, f"Ingest failed: {e}")


@router.get("/capture/status")
async def capture_status():
    """查看抓包状态"""
    async with _capture_lock:
        return dict(_capture_state)


@router.post("/capture/toggle")
async def capture_toggle(
    payload: dict[str, Any] = Body(...),
    request: Request = None,
    audit_service: AuditService = Depends(get_audit_service),
):
    """开关抓包或更新过滤条件：{ "enabled": bool, "filter_host"?: str|null, "filter_url"?: str|null }"""
    async with _capture_lock:
        if "enabled" in payload:
            enabled = payload["enabled"]
            if not isinstance(enabled, bool):
                raise HTTPException(400, "enabled 字段需为 bool")
            _capture_state["enabled"] = enabled
        if "filter_host" in payload:
            _capture_state["filter_host"] = payload["filter_host"] or None
        if "filter_url" in payload:
            _capture_state["filter_url"] = payload["filter_url"] or None

        logger.info(
            "Capture config updated → enabled={} filter_host={} filter_url={}",
            _capture_state["enabled"], _capture_state["filter_host"], _capture_state["filter_url"],
        )
        # 审计日志：记录抓包开关操作
        await audit_service.log_action(
            user=_get_user_from_request(request), action=AuditAction.TOGGLE,
            resource=AuditResource.CAPTURE, resource_id="capture", resource_name="Capture Proxy",
            ip=_get_client_ip(request), extra=dict(_capture_state),
        )
        return dict(_capture_state)
