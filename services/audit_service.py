"""
审计服务层 —— 异步写入审计日志 + 列表查询
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from models.audit import AuditLog, AuditAction, AuditResource


class AuditService:
    """审计日志写入与查询，所有写入操作均为异步 fire-and-forget"""

    def __init__(self, db: AsyncIOMotorDatabase):
        self._col = db["audit_logs"]

    async def log_action(
        self,
        user: dict[str, Any] | None,
        action: AuditAction,
        resource: AuditResource,
        resource_id: str = "",
        resource_name: str = "",
        detail: str = "",
        ip: str = "",
        project_id: str = "default",
        extra: dict[str, Any] | None = None,
    ) -> None:
        """
        异步写入一条审计日志。
        user 为 None 时记为 anonymous（如 API Key 认证场景）。
        """
        username = user.get("username", "anonymous") if user else "anonymous"
        user_id = user.get("user_id", "") if user else ""

        log = AuditLog(
            id=str(uuid.uuid4()),
            user_id=user_id,
            username=username,
            action=action,
            resource=resource,
            resource_id=resource_id,
            resource_name=resource_name,
            detail=detail or f"{action.value} {resource.value} {resource_name}".strip(),
            ip=ip,
            project_id=project_id,
            extra=extra,
        )
        await self._col.insert_one(log.model_dump())

    async def list_logs(
        self,
        project_id: str = "default",
        skip: int = 0,
        limit: int = 50,
        resource: str = "",
        action: str = "",
        username: str = "",
    ) -> list[dict[str, Any]]:
        """分页查询审计日志，按时间倒序"""
        query: dict[str, Any] = {"project_id": project_id}
        if resource:
            query["resource"] = resource
        if action:
            query["action"] = action
        if username:
            query["username"] = username

        cursor = self._col.find(query).sort("created_at", -1).skip(skip).limit(limit)
        docs = await cursor.to_list(length=limit)
        return [_serialize_log(d) for d in docs]

    async def count_logs(
        self,
        project_id: str = "default",
        resource: str = "",
        action: str = "",
        username: str = "",
    ) -> int:
        """统计审计日志总数"""
        query: dict[str, Any] = {"project_id": project_id}
        if resource:
            query["resource"] = resource
        if action:
            query["action"] = action
        if username:
            query["username"] = username
        return await self._col.count_documents(query)


def _serialize_log(doc: dict[str, Any]) -> dict[str, Any]:
    """将 MongoDB 文档转为 JSON 友好格式（处理 ObjectId / datetime 等类型）"""
    # 将 BSON ObjectId 转为字符串，否则 FastAPI jsonable_encoder 会报错
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    if doc.get("created_at") and isinstance(doc["created_at"], datetime):
        doc["created_at"] = doc["created_at"].isoformat()
    return doc
