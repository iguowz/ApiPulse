"""
QuarantineStore —— 解析失败的 HAR 文件存入隔离区。
优先使用 MinIO；若 MinIO 不可用，降级写本地 /tmp/quarantine。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from loguru import logger
from minio import Minio
from minio.error import S3Error

from config.settings import get_settings


class QuarantineStore:
    def __init__(self):
        s = get_settings()
        self._bucket = s.minio_bucket_quarantine
        self._local_fallback = Path("/tmp/quarantine")
        try:
            self._client = Minio(
                s.minio_endpoint,
                access_key=s.minio_access_key,
                secret_key=s.minio_secret_key,
                secure=False,
            )
            self._ensure_bucket()
            self._minio_ok = True
            logger.info("QuarantineStore: MinIO ready (bucket={})", self._bucket)
        except Exception as e:
            logger.warning("QuarantineStore: MinIO unavailable, using local fallback. {}", e)
            self._minio_ok = False
            self._local_fallback.mkdir(parents=True, exist_ok=True)

    def _ensure_bucket(self):
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    async def save(self, filename: str, content: bytes, reason: str) -> str:
        import asyncio
        ts = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None).strftime("%Y%m%dT%H%M%S")
        object_name = f"{ts}_{filename}"

        if self._minio_ok:
            # MinIO 客户端是同步的，放到线程池执行避免阻塞事件循环
            return await asyncio.to_thread(self._save_minio, object_name, content, reason)
        return await asyncio.to_thread(self._save_local, object_name, content, reason)

    def _save_minio(self, object_name: str, content: bytes, reason: str) -> str:
        import io
        try:
            self._client.put_object(
                self._bucket,
                object_name,
                io.BytesIO(content),
                length=len(content),
                metadata={"x-quarantine-reason": reason[:200]},
            )
            path = f"minio://{self._bucket}/{object_name}"
            logger.info("Quarantine saved to MinIO: {}", path)
            return path
        except S3Error as e:
            logger.error("MinIO put_object failed: {}", e)
            return self._save_local(object_name, content, reason)

    def _save_local(self, object_name: str, content: bytes, reason: str) -> str:
        target = self._local_fallback / object_name
        target.write_bytes(content)
        reason_file = target.with_suffix(".reason.txt")
        reason_file.write_text(reason)
        path = str(target)
        logger.info("Quarantine saved locally: {}", path)
        return path

    async def list_items(self, skip: int = 0, limit: int = 100) -> list[dict]:
        """列出隔离区中的失败 HAR 文件（MinIO 操作通过线程池执行避免阻塞事件循环）"""
        import asyncio
        if self._minio_ok:
            return await asyncio.to_thread(self._list_items_minio, skip, limit)
        return await asyncio.to_thread(self._list_items_local, skip, limit)

    def _list_items_minio(self, skip: int = 0, limit: int = 100) -> list[dict]:
        """通过 MinIO list_objects 列出隔离区文件"""
        items = []
        try:
            objects = self._client.list_objects(self._bucket, recursive=True)
            for obj in sorted(objects, key=lambda o: o.last_modified, reverse=True):
                if not obj.object_name:
                    continue
                # 提取元数据中的失败原因
                try:
                    stat = self._client.stat_object(self._bucket, obj.object_name)
                    reason = stat.metadata.get("x-quarantine-reason", "")
                except S3Error:
                    reason = ""
                items.append({
                    "filename": obj.object_name,
                    "size": obj.size or 0,
                    "reason": reason,
                    "path": f"minio://{self._bucket}/{obj.object_name}",
                })
        except Exception as e:
            logger.warning("MinIO list_objects failed, falling back to local: {}", e)
            return self._list_items_local(skip, limit)
        # 分页截取：支持 skip/limit 参数化，不再硬编码 [:100]
        return items[skip:skip + limit]

    def _list_items_local(self, skip: int = 0, limit: int = 100) -> list[dict]:
        """本地降级目录列出隔离区文件"""
        items = []
        if self._local_fallback.exists():
            for f in sorted(self._local_fallback.glob("*.har"), key=lambda p: p.stat().st_mtime, reverse=True):
                reason_file = f.with_suffix(".reason.txt")
                reason = reason_file.read_text() if reason_file.exists() else ""
                items.append({
                    "filename": f.name,
                    "size": f.stat().st_size,
                    "reason": reason,
                    "path": str(f),
                })
        # 分页截取：支持 skip/limit 参数化，不再硬编码 [:100]
        return items[skip:skip + limit]
