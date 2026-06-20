"""
统一 AI Job 持久化服务。

生产场景不能只依赖 Redis list 和日志来判断 AI 任务状态；这里把 job_id/type/status
等关键字段落入 ai_jobs 集合，供设置页、DLQ 恢复和后续详情页状态面板复用。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase


def _now():
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


class AiJobService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db
        try:
            self._col = db["ai_jobs"]
        except Exception as e:
            # Job 观测不能阻断主业务；测试 mock 或临时 DB 异常时降级为空操作。
            logger.debug("ai_jobs collection unavailable: {}", e)
            self._col = None

    async def upsert(
        self,
        *,
        job_id: str,
        type: str,
        status: str,
        project_id: str = "default",
        source: str = "",
        target_ids: list[str] | None = None,
        queue_key: str = "",
        retry_count: int = 0,
        error: str = "",
        generation_ids: list[str] | None = None,
        payload: dict[str, Any] | None = None,
        user_id: str = "",
        finished: bool = False,
    ) -> None:
        if not job_id:
            return
        if self._col is None:
            return
        now = _now()
        update: dict[str, Any] = {
            "job_id": job_id,
            "type": type,
            "status": status,
            "project_id": project_id or "default",
            "source": source,
            "target_ids": target_ids or [],
            "queue_key": queue_key,
            "retry_count": retry_count,
            "error": error,
            "updated_at": now,
        }
        if payload is not None:
            update["payload"] = payload
        if user_id:
            update["user_id"] = user_id
        if finished:
            update["finished_at"] = now
        update_doc: dict[str, Any] = {
            "$set": update,
            "$setOnInsert": {"created_at": now},
        }
        if generation_ids is not None:
            # 一个 job 可能产出多个审核版本（例如 doc + asserts），这里追加而不是覆盖。
            update_doc["$addToSet"] = {"generation_ids": {"$each": generation_ids}}
        try:
            await self._col.update_one(
                {"job_id": job_id},
                update_doc,
                upsert=True,
            )
        except Exception as e:
            # 生产观测记录失败不应让 AI worker 丢任务或中断审核闭环。
            logger.warning("Failed to persist ai_job {}: {}", job_id, e)

    async def mark_queued(self, **kwargs) -> None:
        await self.upsert(status="queued", **kwargs)

    async def mark_running(self, **kwargs) -> None:
        await self.upsert(status="running", **kwargs)

    async def mark_retry(self, **kwargs) -> None:
        await self.upsert(status="retry", **kwargs)

    async def mark_pending_review(self, **kwargs) -> None:
        await self.upsert(status="pending_review", finished=True, **kwargs)

    async def mark_done(self, **kwargs) -> None:
        await self.upsert(status="done", finished=True, **kwargs)

    async def mark_failed(self, **kwargs) -> None:
        await self.upsert(status="failed", finished=True, **kwargs)

    async def mark_dlq(self, **kwargs) -> None:
        await self.upsert(status="dlq", finished=True, **kwargs)
