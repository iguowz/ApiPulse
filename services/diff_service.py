"""
导入差异检测服务 —— 比较新导入 API 与已分析 API 的字段差异，记录并推送 AI 评估队列
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase

from models.dsl import FieldDiff, ImportDiffRecord, ImportDiffStatus
from services.ai_job_service import AiJobService

# Redis 队列名：差异评估任务
DIFF_EVALUATE_QUEUE = "queue:diff_evaluate"


def _now() -> datetime:
    """返回东八区当前时间（不带时区信息，与 ApiDSL 保持一致）"""
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


class DiffService:
    """导入差异检测核心逻辑 —— 同步检测 + 异步 AI 评估"""

    def __init__(self, db: AsyncIOMotorDatabase, redis=None):
        self.db = db
        self._redis = redis

    # ── 差异检测入口 ──────────────────────────────────────

    async def detect_and_record(
        self,
        api_path: str,
        method: str,
        new_api_id: str,
        project_id: str = "default",
    ) -> str | None:
        """
        对指定 path+method 的新 API，查找已分析的同路径同方法 API 进行字段对比。
        有差异时写入 import_diffs 集合并推送 Redis 队列，返回 diff_id；无差异返回 None。
        """
        # 查询已应用分析结果（analysis_status=applied/done）的同 path + 同 method API，排除自身
        existing = await self.db["api_dsls"].find_one({
            "request.path": api_path,
            "request.method": method.upper(),
            "analysis_status": {"$in": ["applied", "done"]},
            "id": {"$ne": new_api_id},
            "project_id": project_id,
        }, sort=[("updated_at", -1)])  # 取最近更新的那个

        if not existing:
            return None

        # 加载新 API
        new_api = await self.db["api_dsls"].find_one({"id": new_api_id})
        if not new_api:
            return None

        # 对比请求参数和响应字段
        diffs: list[FieldDiff] = []
        diffs.extend(self._compare_params(
            (existing.get("doc") or {}).get("params", []),
            (new_api.get("doc") or {}).get("params", []),
        ))
        diffs.extend(self._compare_response_fields(
            (existing.get("doc") or {}).get("response_fields", []),
            (new_api.get("doc") or {}).get("response_fields", []),
        ))

        # 如果两个 API 都还没有 AI 文档（doc.params 为空），退化为对比原始请求体字段
        if not diffs:
            diffs.extend(self._compare_raw_body(
                existing.get("request", {}).get("body"),
                new_api.get("request", {}).get("body"),
            ))
            diffs.extend(self._compare_raw_body(
                existing.get("response", {}).get("body"),
                new_api.get("response", {}).get("body"),
                location="response",
            ))

        if not diffs:
            return None

        # 写入差异记录
        diff_record = ImportDiffRecord(
            id=str(uuid.uuid4()),
            existing_api_id=existing["id"],
            new_api_id=new_api_id,
            api_path=api_path,
            method=method.upper(),
            fields_diff=diffs,
            status=ImportDiffStatus.PENDING,
            project_id=project_id,
            created_at=_now(),
            updated_at=_now(),
        )
        await self.db["import_diffs"].insert_one(diff_record.model_dump())
        logger.info(
            "DiffRecord created: {} (path={}, fields={})",
            diff_record.id, api_path, len(diffs),
        )

        # 推送差异评估任务到 Redis 队列（异步 AI 评估）
        if self._redis:
            job_id = f"diff:{diff_record.id}"
            payload = {
                "diff_id": diff_record.id,
                "project_id": project_id,
                "job_id": job_id,
                "status": "queued",
                "ts": _now().isoformat(),
            }
            await self._redis.rpush(DIFF_EVALUATE_QUEUE, json.dumps(payload, ensure_ascii=False))
            await AiJobService(self.db).mark_queued(
                job_id=job_id,
                type="diff",
                project_id=project_id,
                source="diff_service",
                target_ids=[diff_record.id],
                queue_key=DIFF_EVALUATE_QUEUE,
                payload=payload,
            )

        return diff_record.id

    async def detect_for_batch(
        self,
        api_ids: list[str],
        project_id: str = "default",
    ) -> list[str]:
        """
        批量检测一组新导入 API 的差异。对每个 API 单独调用 detect_and_record。
        返回创建的 diff_id 列表。
        """
        diff_ids = []
        for api_id in api_ids:
            api = await self.db["api_dsls"].find_one({"id": api_id}, {"request.path": 1, "request.method": 1})
            if not api:
                continue
            path = (api.get("request") or {}).get("path", "")
            method = (api.get("request") or {}).get("method", "GET")
            if not path:
                continue
            try:
                diff_id = await self.detect_and_record(path, method, api_id, project_id)
                if diff_id:
                    diff_ids.append(diff_id)
            except Exception as e:
                logger.warning("Batch diff detection failed for api_id={}: {}", api_id, e)
        return diff_ids

    # ── 字段对比逻辑 ──────────────────────────────────────

    @staticmethod
    def _compare_params(existing_params: list[dict], new_params: list[dict]) -> list[FieldDiff]:
        """
        对比请求参数列表（name+location 作为唯一键）。
        检测：added / removed / type_changed / required_changed
        """
        diffs: list[FieldDiff] = []
        # 构建以 (name, location) 为键的映射
        existing_map: dict[tuple, dict] = {}
        for p in existing_params:
            key = (p.get("name", ""), p.get("location", "query"))
            existing_map[key] = p

        new_map: dict[tuple, dict] = {}
        for p in new_params:
            key = (p.get("name", ""), p.get("location", "query"))
            new_map[key] = p

        # 检测新增
        for key, new_p in new_map.items():
            if key not in existing_map:
                diffs.append(FieldDiff(
                    field_path=f"request.{new_p.get('location', 'query')}.{key[0]}",
                    location="request",
                    difference_type="added",
                    new_value={"type": new_p.get("type"), "required": new_p.get("required")},
                ))

        # 检测移除
        for key, old_p in existing_map.items():
            if key not in new_map:
                diffs.append(FieldDiff(
                    field_path=f"request.{old_p.get('location', 'query')}.{key[0]}",
                    location="request",
                    difference_type="removed",
                    existing_value={"type": old_p.get("type"), "required": old_p.get("required")},
                ))

        # 检测变更（type / required）
        for key in existing_map.keys() & new_map.keys():
            old_p, new_p = existing_map[key], new_map[key]
            if old_p.get("type") != new_p.get("type"):
                diffs.append(FieldDiff(
                    field_path=f"request.{old_p.get('location', 'query')}.{key[0]}",
                    location="request",
                    difference_type="type_changed",
                    field_type_diff=f"{old_p.get('type', '?')}→{new_p.get('type', '?')}",
                    existing_value={"type": old_p.get("type")},
                    new_value={"type": new_p.get("type")},
                ))
            if old_p.get("required") != new_p.get("required"):
                diffs.append(FieldDiff(
                    field_path=f"request.{old_p.get('location', 'query')}.{key[0]}",
                    location="request",
                    difference_type="required_changed",
                    required_diff=f"{old_p.get('required')}→{new_p.get('required')}",
                    existing_value={"required": old_p.get("required")},
                    new_value={"required": new_p.get("required")},
                ))

        return diffs

    @staticmethod
    def _compare_response_fields(existing_fields: list[dict], new_fields: list[dict]) -> list[FieldDiff]:
        """
        对比响应字段列表（name 作为唯一键）。
        检测逻辑与请求参数一致。
        """
        diffs: list[FieldDiff] = []
        existing_map = {f.get("name", ""): f for f in existing_fields}
        new_map = {f.get("name", ""): f for f in new_fields}

        for name, new_f in new_map.items():
            if not name:
                continue
            if name not in existing_map:
                diffs.append(FieldDiff(
                    field_path=f"response.body.{name}",
                    location="response",
                    difference_type="added",
                    new_value={"type": new_f.get("type"), "required": new_f.get("required")},
                ))

        for name, old_f in existing_map.items():
            if not name:
                continue
            if name not in new_map:
                diffs.append(FieldDiff(
                    field_path=f"response.body.{name}",
                    location="response",
                    difference_type="removed",
                    existing_value={"type": old_f.get("type"), "required": old_f.get("required")},
                ))

        for name in existing_map.keys() & new_map.keys():
            if not name:
                continue
            old_f, new_f = existing_map[name], new_map[name]
            if old_f.get("type") != new_f.get("type"):
                diffs.append(FieldDiff(
                    field_path=f"response.body.{name}",
                    location="response",
                    difference_type="type_changed",
                    field_type_diff=f"{old_f.get('type', '?')}→{new_f.get('type', '?')}",
                    existing_value={"type": old_f.get("type")},
                    new_value={"type": new_f.get("type")},
                ))
            if old_f.get("required") != new_f.get("required"):
                diffs.append(FieldDiff(
                    field_path=f"response.body.{name}",
                    location="response",
                    difference_type="required_changed",
                    required_diff=f"{old_f.get('required')}→{new_f.get('required')}",
                    existing_value={"required": old_f.get("required")},
                    new_value={"required": new_f.get("required")},
                ))

        return diffs

    @staticmethod
    def _compare_raw_body(
        old_body: Any,
        new_body: Any,
        location: str = "request",
    ) -> list[FieldDiff]:
        """
        无 AI 文档时退化为对比原始请求体/响应体的顶层 JSON 字段。
        只对比顶层 key（不递归），避免过度报告差异。
        """
        diffs: list[FieldDiff] = []
        if not isinstance(old_body, dict) or not isinstance(new_body, dict):
            return diffs

        old_keys = set(old_body.keys())
        new_keys = set(new_body.keys())
        prefix = f"{location}.body"

        for k in new_keys - old_keys:
            diffs.append(FieldDiff(
                field_path=f"{prefix}.{k}",
                location=location,
                difference_type="added",
            ))
        for k in old_keys - new_keys:
            diffs.append(FieldDiff(
                field_path=f"{prefix}.{k}",
                location=location,
                difference_type="removed",
            ))
        for k in old_keys & new_keys:
            old_type = type(old_body[k]).__name__
            new_type = type(new_body[k]).__name__
            if old_type != new_type:
                diffs.append(FieldDiff(
                    field_path=f"{prefix}.{k}",
                    location=location,
                    difference_type="type_changed",
                    field_type_diff=f"{old_type}→{new_type}",
                    existing_value=old_body[k],
                    new_value=new_body[k],
                ))

        return diffs

    # ── 查询 + 解决 ──────────────────────────────────────

    async def list_diffs(
        self,
        project_id: str = "default",
        status: str | None = None,
        severity: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """分页查询差异记录列表，支持按状态和严重程度过滤"""
        q: dict[str, Any] = {"project_id": project_id}
        if status:
            q["status"] = status
        if severity:
            q["severity"] = severity
        total = await self.db["import_diffs"].count_documents(q)
        cursor = self.db["import_diffs"].find(q).sort("created_at", -1).skip(skip).limit(limit)
        items = await cursor.to_list(length=limit)
        return {"items": items, "total": total}

    async def count_diffs(self, project_id: str = "default", status: str | None = None) -> int:
        """统计差异记录数量，可选按状态过滤（如 status=pending 统计未处理数）"""
        q: dict[str, Any] = {"project_id": project_id}
        if status:
            q["status"] = status
        return await self.db["import_diffs"].count_documents(q)

    async def get_diff(self, diff_id: str) -> dict | None:
        """获取单条差异记录"""
        return await self.db["import_diffs"].find_one({"id": diff_id})

    async def get_diff_comparison(self, diff_id: str) -> dict | None:
        """
        获取差异对比数据：返回新旧两个 API 的文档 JSON 供前端展示。
        """
        diff = await self.db["import_diffs"].find_one({"id": diff_id})
        if not diff:
            return None
        old_api = await self.db["api_dsls"].find_one(
            {"id": diff["existing_api_id"]},
            {"id": 1, "name": 1, "request": 1, "response": 1, "doc": 1, "asserts": 1},
        )
        new_api = await self.db["api_dsls"].find_one(
            {"id": diff["new_api_id"]},
            {"id": 1, "name": 1, "request": 1, "response": 1, "doc": 1, "asserts": 1},
        )
        return {
            "diff": diff,
            "old_api": old_api,
            "new_api": new_api,
        }

    async def resolve_diff(self, diff_id: str, action: str) -> bool:
        """
        用户手动处理差异记录：
        - confirm  → 状态改为 confirmed（手动确认差异有效）
        - dismiss → 状态改为 dismissed
        """
        valid_actions = {"confirm", "dismiss"}
        if action not in valid_actions:
            return False
        status_map = {"confirm": ImportDiffStatus.CONFIRMED, "dismiss": ImportDiffStatus.DISMISSED}
        result = await self.db["import_diffs"].update_one(
            {"id": diff_id},
            {"$set": {
                "status": status_map[action].value,
                "updated_at": _now(),
            }},
        )
        return result.modified_count > 0

    # ── 自动修复（由 DiffEvaluatorService 调用） ─────────

    async def apply_auto_fix(
        self,
        diff_id: str,
        fix_updates: dict[str, Any],
    ) -> bool:
        """
        AI 评估为 ai_doc_error 时，将修复内容写入旧 API 文档。
        fix_updates 包含需要更新的字段路径和值，例如：
        {"doc.params": [...], "doc.response_fields": [...]}
        """
        diff = await self.db["import_diffs"].find_one({"id": diff_id})
        if not diff:
            return False
        existing_api_id = diff["existing_api_id"]
        # 更新旧 API 文档
        result = await self.db["api_dsls"].update_one(
            {"id": existing_api_id},
            {"$set": {**fix_updates, "updated_at": _now()}},
        )
        # 标记差异记录为已自动修复
        await self.db["import_diffs"].update_one(
            {"id": diff_id},
            {"$set": {
                "status": ImportDiffStatus.AUTO_FIXED.value,
                "updated_at": _now(),
            }},
        )
        logger.info("Auto-fixed diff_id={} for api_id={}", diff_id, existing_api_id)
        return result.modified_count > 0
