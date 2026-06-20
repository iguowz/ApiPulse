"""
GenerationVersion 审核路由 —— Phase 1 AI 生成审核流程

提供 7 个端点：列表查询、详情、diff 对比、接受、部分接受、拒绝、编辑后接受。
审核操作会调用 AiAnalyzerService.apply_version() 将内容写入目标 DSL。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db
from models.audit import AuditAction, AuditResource
from services.audit_service import AuditService
from api.deps import (
    _get_user_from_request,
    _get_client_ip,
    ensure_project_access,
    get_current_user,
    visible_project_id,
)
import api.state as _state

router = APIRouter(tags=["Generations"])


def get_audit_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> AuditService:
    return AuditService(db)


async def _load_generation_doc(generation_id: str) -> dict[str, Any] | None:
    """按 Mongo _id 或业务 id 查找 GenerationVersion，避免审核入口重复写回退逻辑。"""
    collection = (await get_db()).get_collection("generation_versions")
    try:
        doc = await collection.find_one({"_id": ObjectId(generation_id)})
    except Exception:
        logger.warning("Failed to resolve generation_id as ObjectId: %s", generation_id)
        doc = None
    if not doc:
        doc = await collection.find_one({"id": generation_id})
    return doc


def _normalize_generation_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """把 Mongo _id 转成前端可消费的字符串 id。"""
    if "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    return doc


def _review_stats_filter(doc: dict[str, Any]) -> dict[str, Any]:
    """构造同一 AI 生成节点的统计范围，优先按 job_id，缺失时按 type/api/project 聚合。"""
    filt: dict[str, Any] = {
        "project_id": doc.get("project_id", "default"),
        "type": doc.get("type", ""),
    }
    if doc.get("job_id"):
        filt["job_id"] = doc.get("job_id")
        return filt
    if doc.get("api_id"):
        filt["api_id"] = doc.get("api_id")
    api_ids = [aid for aid in (doc.get("api_ids") or []) if aid]
    if api_ids:
        filt["api_ids"] = {"$in": api_ids}
    return filt


async def _review_stats(collection, doc: dict[str, Any]) -> dict[str, Any]:
    """统计同节点采纳/部分采纳/放弃数量，供审核面板展示闭环进度。"""
    filt = _review_stats_filter(doc)
    rows = await collection.aggregate([
        {"$match": filt},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]).to_list(20)
    by_status = {str(row.get("_id") or "unknown"): int(row.get("count") or 0) for row in rows}
    accepted = by_status.get("accepted", 0)
    partial = by_status.get("partially_accepted", 0)
    rejected = by_status.get("rejected", 0)
    pending = by_status.get("pending_review", 0)
    return {
        "scope": "job" if doc.get("job_id") else "target",
        "total": sum(by_status.values()),
        "accepted": accepted,
        "partially_accepted": partial,
        "rejected": rejected,
        "pending_review": pending,
        "applied": accepted + partial,
        "discarded": rejected,
        "by_status": by_status,
    }


def _json_equal(left: Any, right: Any) -> bool:
    """用稳定 JSON 表达比较复杂字段，避免 dict/list 顺序噪声影响 diff 状态。"""
    return json.dumps(left, ensure_ascii=False, sort_keys=True, default=str) == json.dumps(right, ensure_ascii=False, sort_keys=True, default=str)


def _field_diff(key: str, label: str, group: str, current: Any, generated: Any, current_exists: bool) -> dict[str, Any]:
    """构造前端字段级 diff 条目；key 必须与 accept-partial 的字段选择器一致。"""
    status = "added" if not current_exists else ("unchanged" if _json_equal(current, generated) else "modified")
    return {
        "key": key,
        "label": label,
        "group": group,
        "status": status,
        "current": current if current_exists else None,
        "generated": generated,
        "selectable": status != "unchanged",
    }


def _items_by_name(items: Any, name_key: str = "name") -> dict[str, dict[str, Any]]:
    """将带 name/field 的列表转成映射，供 params/asserts/data_template 字段级 diff 使用。"""
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(items, list):
        return result
    for item in items:
        if isinstance(item, dict) and item.get(name_key):
            result[str(item[name_key])] = item
    return result


def _monitor_selector(item: dict[str, Any], idx: int) -> str:
    """生成 monitor 部分采纳选择器，与 AiAnalyzerService.apply_version 保持一致。"""
    return str(item.get("id") or item.get("target_id") or item.get("api_id") or item.get("name") or idx)


def _build_generation_field_diffs(current: dict[str, Any], generated: dict[str, Any], gen_type: str) -> list[dict[str, Any]]:
    """
    生成审核中心字段级 diff。
    返回的 key 直接作为 accept-partial 的字段选择器，避免前端展示粒度和后端落地粒度不一致。
    """
    current = current or {}
    generated = generated or {}
    diffs: list[dict[str, Any]] = []

    if gen_type == "doc":
        for key in ("summary", "description", "tags"):
            if key in generated:
                diffs.append(_field_diff(key, f"doc.{key}", "doc", current.get(key), generated.get(key), key in current))
        for list_key in ("params", "response_fields"):
            current_map = _items_by_name(current.get(list_key), "name")
            for item in generated.get(list_key) or []:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                name = str(item["name"])
                diffs.append(_field_diff(
                    f"{list_key}:{name}",
                    f"doc.{list_key}.{name}",
                    list_key,
                    current_map.get(name),
                    item,
                    name in current_map,
                ))
        return diffs

    if gen_type == "asserts":
        current_map = _items_by_name(current.get("asserts"), "field")
        for idx, rule in enumerate((generated.get("asserts") if isinstance(generated, dict) else generated) or []):
            if not isinstance(rule, dict):
                continue
            field = str(rule.get("field") or idx)
            diffs.append(_field_diff(field, f"asserts.{field}", "asserts", current_map.get(field), rule, field in current_map))
        return diffs

    if gen_type == "data_template":
        current_map = _items_by_name(current.get("fields"), "name")
        for field in generated.get("fields") or []:
            if not isinstance(field, dict) or not field.get("name"):
                continue
            name = str(field["name"])
            diffs.append(_field_diff(name, f"fields.{name}", "fields", current_map.get(name), field, name in current_map))
        return diffs

    if gen_type == "monitor":
        current_items = current.get("monitors") or []
        current_by_selector = {
            _monitor_selector(item, idx): item
            for idx, item in enumerate(current_items)
            if isinstance(item, dict)
        }
        current_by_target = {
            (item.get("target_type") or "api", item.get("target_id") or item.get("api_id")): item
            for item in current_items
            if isinstance(item, dict) and (item.get("target_id") or item.get("api_id"))
        }
        monitors = generated.get("monitors", generated) if isinstance(generated, dict) else generated
        if isinstance(monitors, dict):
            monitors = [monitors]
        for idx, monitor in enumerate(monitors or []):
            if not isinstance(monitor, dict):
                continue
            selector = _monitor_selector(monitor, idx)
            target_key = (monitor.get("target_type") or "api", monitor.get("target_id") or monitor.get("api_id"))
            current_item = current_by_selector.get(selector) or current_by_target.get(target_key)
            label = monitor.get("name") or f"monitor.{target_key[0]}.{target_key[1] or selector}"
            diffs.append(_field_diff(selector, str(label), "monitors", current_item, monitor, current_item is not None))
        return diffs

    return diffs


# ── 列表查询 ──────────────────────────────────────────────────

@router.get("/generations")
async def list_generations(
    project_id: str = Query(default="default"),
    type: str | None = Query(default=None, description="筛选类型: doc/asserts/scenario"),
    status: str | None = Query(default=None, description="筛选状态: pending_review/accepted/partially_accepted/rejected"),
    api_id: str | None = Query(default=None, description="按关联 API ID 筛选"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    """
    分页列出 GenerationVersion 记录，支持按 type/status/api_id 筛选。
    仅返回摘要信息，完整 content 通过详情接口获取。
    """
    collection = (await get_db()).get_collection("generation_versions")
    project_id = visible_project_id(current_user, project_id)
    # 构建查询过滤器
    filt: dict[str, Any] = {"project_id": project_id}
    if type:
        filt["type"] = type
    if status:
        filt["status"] = status
    if api_id:
        # Bug5/10 修复：scenario 类型用 api_ids 数组关联多个 API，此前只匹配 api_id 单字段，
        # 导致场景类生成在按 API 维度查询时系统性丢失（API 详情审核 tab 无内容、审核中心看不到 scenario）。
        # 改为 $or 同时匹配 api_id 单字段或 api_ids 数组包含。
        filt["$or"] = [{"api_id": api_id}, {"api_ids": api_id}]

    total = await collection.count_documents(filt)
    # 按创建时间倒序，最新优先展示
    cursor = collection.find(filt).sort("created_at", -1).skip(skip).limit(limit)
    items = []
    async for doc in cursor:
        # _id 转换为 id 字符串，前端统一使用字符串 id
        if "_id" in doc:
            doc["id"] = str(doc.pop("_id"))
        # 移除完整 content 减少响应体积（列表页不需要完整内容）
        doc.pop("content", None)
        doc.pop("prompt", None)
        doc["review_stats"] = await _review_stats(collection, doc)
        items.append(doc)

    api_ids = list({item.get("api_id") for item in items if item.get("api_id")})
    if api_ids:
        api_docs = await (await get_db()).get_collection("api_dsls").find(
            {"id": {"$in": api_ids}},
            {"_id": 0, "id": 1, "name": 1, "request": 1},
        ).to_list(len(api_ids))
        api_map = {d.get("id"): d for d in api_docs}
        for item in items:
            api_doc = api_map.get(item.get("api_id")) or {}
            item["api_name"] = api_doc.get("name", "")
            item["api_path"] = (api_doc.get("request") or {}).get("path", "")
            item["api_method"] = (api_doc.get("request") or {}).get("method", "")

    return {"total": total, "items": items}


# ── 详情查询 ──────────────────────────────────────────────────

@router.get("/generations/{generation_id}")
async def get_generation(
    generation_id: str,
    current_user: dict = Depends(get_current_user),
):
    """获取 GenerationVersion 完整详情（含 content 和 prompt）"""
    doc = await _load_generation_doc(generation_id)
    if not doc:
        raise HTTPException(404, "Generation version not found")

    ensure_project_access(current_user, doc.get("project_id"))
    collection = (await get_db()).get_collection("generation_versions")
    doc["review_stats"] = await _review_stats(collection, doc)
    return _normalize_generation_doc(doc)


# ── Diff 对比 ─────────────────────────────────────────────────

@router.get("/generations/{generation_id}/diff")
async def get_generation_diff(
    generation_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    对比 AI 生成版本 vs 当前 DSL 实际内容。
    返回 generations 中的 content 和当前 API/场景中的对应字段，前端自行渲染差异。
    """
    gv_doc = await _load_generation_doc(generation_id)
    if not gv_doc:
        raise HTTPException(404, "Generation version not found")
    ensure_project_access(current_user, gv_doc.get("project_id"))

    gen_type = gv_doc.get("type")
    api_id = gv_doc.get("api_id")
    current: dict[str, Any] = {}
    future = gv_doc.get("content", {})

    # 根据类型获取当前 DSL 中的实际内容
    if gen_type in ("doc", "asserts"):
        api_col = (await get_db()).get_collection("api_dsls")
        api_doc = await api_col.find_one({"id": api_id})
        if api_doc:
            if gen_type == "doc":
                current = api_doc.get("doc") or {}
            elif gen_type == "asserts":
                current = {"asserts": api_doc.get("asserts") or []}
    elif gen_type == "scenario":
        # 场景类型：当前场景尚未创建（待审核），current 为空
        current = {}
    elif gen_type == "data_template":
        # 数据模板 diff 需要展示当前模板字段与 AI 建议字段，避免审核时只能看到生成侧。
        db = await get_db()
        template_id = (future or {}).get("template_id", "")
        tmpl_doc = await db.get_collection("data_templates").find_one({"id": template_id}, {"_id": 0})
        if tmpl_doc:
            ensure_project_access(current_user, tmpl_doc.get("project_id"))
            current = {
                "template_id": template_id,
                "name": tmpl_doc.get("name", ""),
                "fields": tmpl_doc.get("fields", []),
            }
    elif gen_type == "monitor":
        db = await get_db()
        monitors = (future or {}).get("monitors", [])
        target_pairs = [
            (m.get("target_type") or "api", m.get("target_id") or m.get("api_id"))
            for m in monitors if isinstance(m, dict) and (m.get("target_id") or m.get("api_id"))
        ]
        current_items = []
        for target_type, target_id in target_pairs:
            docs = await db.get_collection("monitors").find(
                {
                    "project_id": gv_doc.get("project_id", "default"),
                    "target_type": target_type,
                    "target_id": target_id,
                },
                {"_id": 0},
            ).to_list(20)
            current_items.extend(docs)
        current = {"monitors": current_items, "coverage": target_pairs}

    return {
        "generation_id": generation_id,
        "api_id": api_id,
        "type": gen_type,
        "template_id": (future or {}).get("template_id", "") if gen_type == "data_template" else "",
        "current": current,
        "generated": future,
        "field_diffs": _build_generation_field_diffs(current, future, gen_type),
    }


# ── 接受（全部接受） ──────────────────────────────────────────

@router.post("/generations/{generation_id}/accept")
async def accept_generation(
    generation_id: str,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    audit_service: AuditService = Depends(get_audit_service),
):
    """
    接受 AI 生成内容，将 GenerationVersion 内容完整应用到目标 DSL。
    成功后写入审计日志。
    """
    if _state._ai_analyzer is None:
        raise HTTPException(503, "AI service not available")

    gv_doc = await _load_generation_doc(generation_id)
    if not gv_doc:
        raise HTTPException(404, "Generation version not found")
    ensure_project_access(current_user, gv_doc.get("project_id"))

    user = current_user or _get_user_from_request(request)
    reviewer_id = user.get("username") if user else None

    ok = await _state._ai_analyzer.apply_version(
        generation_id=generation_id,
        reviewer_id=reviewer_id,
    )
    if not ok:
        raise HTTPException(404, "Generation version not found")

    # 审计日志
    await audit_service.log_action(
        user=user,
        action=AuditAction.UPDATE,
        resource=AuditResource.API,
        resource_id=generation_id,
        resource_name=f"accept generation {generation_id}",
        ip=_get_client_ip(request) if request else "",
    )
    return {"accepted": True}


# ── 部分接受 ──────────────────────────────────────────────────

@router.post("/generations/{generation_id}/accept-partial")
async def accept_partial_generation(
    generation_id: str,
    fields: list[str] = Body(..., description="要接受的字段名列表"),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    audit_service: AuditService = Depends(get_audit_service),
):
    """
    部分接受 AI 生成内容，仅将指定字段应用到目标 DSL。
    状态设为 partially_accepted。
    """
    if _state._ai_analyzer is None:
        raise HTTPException(503, "AI service not available")

    gv_doc = await _load_generation_doc(generation_id)
    if not gv_doc:
        raise HTTPException(404, "Generation version not found")
    ensure_project_access(current_user, gv_doc.get("project_id"))

    user = current_user or _get_user_from_request(request)
    reviewer_id = user.get("username") if user else None

    ok = await _state._ai_analyzer.apply_version(
        generation_id=generation_id,
        reviewer_id=reviewer_id,
        partial_fields=fields,
    )
    if not ok:
        raise HTTPException(404, "Generation version not found")

    await audit_service.log_action(
        user=user,
        action=AuditAction.UPDATE,
        resource=AuditResource.API,
        resource_id=generation_id,
        resource_name=f"partially accept generation {generation_id} fields={','.join(fields)}",
        ip=_get_client_ip(request) if request else "",
    )
    return {"accepted": True, "partial": True}


# ── 拒绝 ──────────────────────────────────────────────────────

@router.post("/generations/{generation_id}/reject")
async def reject_generation(
    generation_id: str,
    feedback: str = Body(default="", description="拒绝原因"),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    audit_service: AuditService = Depends(get_audit_service),
):
    """
    拒绝 AI 生成版本，记录拒绝原因。
    拒绝反馈写入 knowledge 系统作为隐式反馈，辅助后续生成质量提升。
    """
    if _state._ai_analyzer is None:
        raise HTTPException(503, "AI service not available")

    gv_doc = await _load_generation_doc(generation_id)
    if not gv_doc:
        raise HTTPException(404, "Generation version not found")
    ensure_project_access(current_user, gv_doc.get("project_id"))

    user = current_user or _get_user_from_request(request)
    reviewer_id = user.get("username") if user else None
    now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)

    # 更新 GenerationVersion 为 rejected 状态
    collection = (await get_db()).get_collection("generation_versions")
    try:
        result = await collection.update_one(
            {"_id": ObjectId(generation_id)},
            {"$set": {
                "status": "rejected",
                "reviewed_at": now,
                "reviewer_id": reviewer_id,
                "review_feedback": feedback,
            }},
        )
        if result.matched_count == 0:
            # 尝试字符串 id 回退
            result = await collection.update_one(
                {"id": generation_id},
                {"$set": {
                    "status": "rejected",
                    "reviewed_at": now,
                    "reviewer_id": reviewer_id,
                    "review_feedback": feedback,
                }},
            )
    except Exception:
        result = None

    if not result or result.matched_count == 0:
        raise HTTPException(404, "Generation version not found")

    # 将拒绝原因作为隐式反馈写入 knowledge 系统
    if feedback and _state._knowledge_service is not None:
        try:
            if gv_doc:
                await _state._knowledge_service.add_entry(
                    project_id=gv_doc.get("project_id", "default"),
                    type="rejection_feedback",
                    title=f"Rejected: {gv_doc.get('type', 'unknown')} for {gv_doc.get('api_id', '')}",
                    content=feedback,
                    source="human_review",
                    metadata={
                        "generation_id": generation_id,
                        "api_id": gv_doc.get("api_id", ""),
                        "gen_type": gv_doc.get("type", ""),
                    },
                )
        except Exception as e:
            # 知识写入失败不阻塞拒绝操作
            logger.warning("Failed to write rejection feedback to knowledge: {}", e)

    # 审计日志
    await audit_service.log_action(
        user=user,
        action=AuditAction.UPDATE,
        resource=AuditResource.API,
        resource_id=generation_id,
        resource_name=f"reject generation {generation_id}",
        ip=_get_client_ip(request) if request else "",
        extra={"feedback": feedback},
    )
    return {"rejected": True}


# ── 编辑后接受 ────────────────────────────────────────────────

@router.post("/generations/{generation_id}/edit")
async def edit_and_accept_generation(
    generation_id: str,
    content: dict[str, Any] = Body(..., description="编辑后的内容"),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    audit_service: AuditService = Depends(get_audit_service),
):
    """
    用户编辑 AI 生成内容后再接受：保存修改后的 content 到 GenerationVersion，
    然后 apply 到目标 DSL。
    """
    if _state._ai_analyzer is None:
        raise HTTPException(503, "AI service not available")

    gv_doc = await _load_generation_doc(generation_id)
    if not gv_doc:
        raise HTTPException(404, "Generation version not found")
    ensure_project_access(current_user, gv_doc.get("project_id"))

    user = current_user or _get_user_from_request(request)
    reviewer_id = user.get("username") if user else None
    now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)

    collection = (await get_db()).get_collection("generation_versions")
    # 先更新 GenerationVersion 的 content 为用户编辑后的内容
    try:
        result = await collection.update_one(
            {"_id": ObjectId(generation_id)},
            {"$set": {"content": content, "updated_at": now}},
        )
        if result.matched_count == 0:
            result = await collection.update_one(
                {"id": generation_id},
                {"$set": {"content": content, "updated_at": now}},
            )
    except Exception:
        result = None

    if not result or result.matched_count == 0:
        raise HTTPException(404, "Generation version not found")

    # 应用编辑后的版本到目标 DSL
    ok = await _state._ai_analyzer.apply_version(
        generation_id=generation_id,
        reviewer_id=reviewer_id,
        review_feedback="用户编辑后接受",
    )
    if not ok:
        raise HTTPException(500, "Failed to apply edited version")

    await audit_service.log_action(
        user=user,
        action=AuditAction.UPDATE,
        resource=AuditResource.API,
        resource_id=generation_id,
        resource_name=f"edit and accept generation {generation_id}",
        ip=_get_client_ip(request) if request else "",
    )
    return {"accepted": True, "edited": True}
