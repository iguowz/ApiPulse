"""
System 健康检查路由
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from loguru import logger
from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db, get_redis
from config.settings import get_settings
from api.deps import get_current_user, user_has_permission

router = APIRouter(tags=["System"])


@router.get("/health")
async def health(db: AsyncIOMotorDatabase = Depends(get_db)):
    s = get_settings()
    try:
        await db.command("ping")
        db_ok = True
    except Exception:
        logger.warning("DB ping failed in health check")
        db_ok = False
    # Redis 连接检查
    redis_ok = False
    try:
        redis = await get_redis()
        await redis.ping()
        redis_ok = True
    except Exception:
        logger.warning("Redis ping failed in health check")
        pass
    llm_configured = bool(s.openai_api_key)
    minio_configured = bool(s.minio_endpoint and s.minio_access_key and s.minio_secret_key)
    all_ok = db_ok and redis_ok
    return {
        "status": "ok" if all_ok else ("degraded" if db_ok or redis_ok else "error"),
        "db": "ok" if db_ok else "error",
        "redis": "ok" if redis_ok else "error",
        "llm": "configured" if llm_configured else "missing_api_key",
        "minio": "configured" if minio_configured else "missing_config",
        "security": {
            "production_safe": not (
                s.app_env != "development"
                and (
                    s.jwt_secret == "apipulse-jwt-secret-change-in-production"
                    or s.cors_origins.strip() == "*"
                    or not s.sql_secret_key
                )
            ),
            "cors_origins": "wildcard" if s.cors_origins.strip() == "*" else "restricted",
            "sql_secret": "configured" if s.sql_secret_key else ("development_fallback" if s.app_env == "development" else "missing"),
        },
        "timestamp": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None).isoformat(),
    }


@router.get("/system/queues")
async def queue_status(current_user: dict = Depends(get_current_user)):
    """长任务/队列可观测性：展示主队列和 DLQ 积压情况。"""
    if not user_has_permission(current_user, "stats:read"):
        from fastapi import HTTPException
        raise HTTPException(403, "权限不足")
    redis = await get_redis()
    from api.routers.dlq import get_queue_summary
    return await get_queue_summary(redis)
