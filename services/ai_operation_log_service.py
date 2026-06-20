"""
AI 操作日志服务层 —— 异步写入 + 列表查询
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from models.ai_operation_log import AiOperationLog, AiOperationType, AiOperationStatus


class AiOperationLogService:
    """AI 操作日志写入与查询"""

    def __init__(self, db: AsyncIOMotorDatabase):
        self._col = db["ai_operation_logs"]

    async def log_operation(
        self,
        type: AiOperationType,
        status: AiOperationStatus,
        api_ids: list[str] | None = None,
        scenario_ids: list[str] | None = None,
        message: str = "",
        error: str = "",
        model: str = "",
        base_url: str = "",
        duration_ms: int = 0,
        llm_calls: int = 0,
        input_chars: int = 0,
        output_chars: int = 0,
        estimated_input_tokens: int = 0,
        estimated_output_tokens: int = 0,
        extra: dict[str, Any] | None = None,
        project_id: str = "default",
    ) -> None:
        """异步写入一条 AI 操作日志"""
        log = AiOperationLog(
            id=str(uuid.uuid4()),
            type=type,
            status=status,
            api_ids=api_ids or [],
            scenario_ids=scenario_ids or [],
            message=message,
            error=error,
            model=model,
            base_url=base_url,
            duration_ms=duration_ms,
            llm_calls=llm_calls,
            input_chars=input_chars,
            output_chars=output_chars,
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=estimated_output_tokens,
            extra=extra or {},
            project_id=project_id,
        )
        await self._col.insert_one(log.model_dump())

    async def list_logs(
        self,
        project_id: str = "default",
        api_id: str = "",
        type: str = "",
        status: str = "",
        skip: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """分页查询 AI 操作日志，按时间倒序，支持多维度过滤"""
        query: dict[str, Any] = {"project_id": project_id}
        if api_id:
            # api_ids 是数组，使用 $in 匹配包含该 api_id 的记录
            query["api_ids"] = {"$in": [api_id]}
        if type:
            query["type"] = type
        if status:
            query["status"] = status

        total = await self._col.count_documents(query)
        cursor = self._col.find(query).sort("created_at", -1).skip(skip).limit(limit)
        items = await cursor.to_list(length=limit)
        return {"total": total, "items": [_serialize(d) for d in items]}


def _serialize(doc: dict[str, Any]) -> dict[str, Any]:
    """将 MongoDB 文档转为 JSON 友好格式（处理 ObjectId / datetime 等类型）"""
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    if doc.get("created_at") and isinstance(doc["created_at"], datetime):
        doc["created_at"] = doc["created_at"].isoformat()
    return doc
