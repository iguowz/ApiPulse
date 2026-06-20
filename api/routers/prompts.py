"""
Prompt 模板管理路由 —— P1-6 Prompt 版本化

提供 prompt 的 CRUD + 版本激活 + 列表查询。
更新/激活后调用 invalidate_prompt_cache 使 AiAnalyzerService 下次读取新版本。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db
from models.prompt_template import PromptTemplate, PROMPT_TASK_TYPES
from models.audit import AuditAction, AuditResource
from services.audit_service import AuditService
from ai_analyzer.analyzer import AiAnalyzerService
from api.deps import _get_user_from_request, _get_client_ip

router = APIRouter(tags=["Prompts"])


def get_audit_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> AuditService:
    return AuditService(db)


async def _find_prompt_by_id(collection, prompt_id: str) -> dict[str, Any] | None:
    """兼容 Mongo _id 与业务 UUID id，避免前端列表返回 UUID 后详情/激活找不到。"""
    try:
        doc = await collection.find_one({"_id": ObjectId(prompt_id)})
    except Exception:
        doc = None
    if not doc:
        doc = await collection.find_one({"id": prompt_id})
    return doc


# ── 列表查询 ──────────────────────────────────────────────────

@router.get("/prompts")
async def list_prompts(
    task_type: str | None = Query(default=None, description="按任务类型筛选"),
    active_only: bool = Query(default=False, description="仅返回激活版本"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    """分页列出 prompt 模板，支持按 task_type 筛选。"""
    collection = (await get_db()).get_collection("prompt_templates")
    filt: dict[str, Any] = {}
    if task_type:
        if task_type not in PROMPT_TASK_TYPES:
            raise HTTPException(400, f"Invalid task_type, valid: {PROMPT_TASK_TYPES}")
        filt["task_type"] = task_type
    if active_only:
        filt["active"] = True

    total = await collection.count_documents(filt)
    cursor = collection.find(filt).sort("task_type", 1).sort("version", -1).skip(skip).limit(limit)
    items = []
    async for doc in cursor:
        if "_id" in doc:
            doc["id"] = str(doc.pop("_id"))
        items.append(doc)
    return {"total": total, "items": items}


# ── 详情 ──────────────────────────────────────────────────────

@router.get("/prompts/{prompt_id}")
async def get_prompt_detail(prompt_id: str):
    """获取单个 prompt 模板详情（含完整 content）。"""
    collection = (await get_db()).get_collection("prompt_templates")
    doc = await _find_prompt_by_id(collection, prompt_id)
    if not doc:
        raise HTTPException(404, "Prompt template not found")
    if "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    return doc


# ── 新建版本 ──────────────────────────────────────────────────

@router.post("/prompts", status_code=201)
async def create_prompt(
    body: dict[str, Any] = Body(...),
    request: Request = None,
    audit_service: AuditService = Depends(get_audit_service),
):
    """
    新建 prompt 模板版本。
    若 activate=true，自动停用同 task_type 的其他版本并激活新版本。
    """
    task_type = body.get("task_type", "")
    if task_type not in PROMPT_TASK_TYPES:
        raise HTTPException(400, f"Invalid task_type, valid: {PROMPT_TASK_TYPES}")
    content = body.get("content", "").strip()
    if not content:
        raise HTTPException(400, "content is required")

    collection = (await get_db()).get_collection("prompt_templates")
    # 计算下一个版本号：同 task_type 最大 version + 1
    last = await collection.find_one({"task_type": task_type}, sort=[("version", -1)])
    next_version = (last.get("version", 0) + 1) if last else 1

    import uuid
    user = _get_user_from_request(request) or {}
    now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    template = PromptTemplate(
        id=str(uuid.uuid4()),
        task_type=task_type,
        name=body.get("name", f"v{next_version}"),
        content=content,
        version=next_version,
        active=False,  # 默认不激活，需显式 activate
        project_id=body.get("project_id", ""),
        description=body.get("description", ""),
        created_by=user.get("username", ""),
        created_at=now,
        updated_at=now,
    )
    await collection.insert_one(template.model_dump())

    activate = body.get("activate", False)
    if activate:
        await _activate_version(collection, template.id, task_type)

    await audit_service.log_action(
        user=user, action=AuditAction.CREATE,
        resource=AuditResource.SETTINGS, resource_id=template.id,
        resource_name=f"prompt {task_type} v{next_version}",
        ip=_get_client_ip(request), extra={"task_type": task_type, "version": next_version},
    )
    return {"id": template.id, "version": next_version, "activated": activate}


# ── 编辑（仅未激活版本可编辑，避免误改线上 prompt） ───────────

@router.put("/prompts/{prompt_id}")
async def update_prompt(
    prompt_id: str,
    body: dict[str, Any] = Body(...),
    request: Request = None,
    audit_service: AuditService = Depends(get_audit_service),
):
    """编辑 prompt 内容。激活中的版本不允许直接编辑（应新建版本）。"""
    collection = (await get_db()).get_collection("prompt_templates")
    doc = await _find_prompt_by_id(collection, prompt_id)
    if not doc:
        raise HTTPException(404, "Prompt template not found")
    if doc.get("active"):
        raise HTTPException(409, "Cannot edit active version, create a new version instead")

    update_fields: dict[str, Any] = {"updated_at": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)}
    if "content" in body:
        if not body["content"].strip():
            raise HTTPException(400, "content cannot be empty")
        update_fields["content"] = body["content"]
    if "name" in body:
        update_fields["name"] = body["name"]
    if "description" in body:
        update_fields["description"] = body["description"]

    await collection.update_one({"_id": doc["_id"]}, {"$set": update_fields})

    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.UPDATE,
        resource=AuditResource.SETTINGS, resource_id=prompt_id,
        resource_name=f"prompt {doc.get('task_type')} v{doc.get('version')}",
        ip=_get_client_ip(request),
    )
    return {"updated": True}


# ── 激活版本（停用同 task_type 其他版本） ─────────────────────

@router.post("/prompts/{prompt_id}/activate")
async def activate_prompt(
    prompt_id: str,
    request: Request = None,
    audit_service: AuditService = Depends(get_audit_service),
):
    """激活指定版本，自动停用同 task_type 的其他版本，并失效缓存。"""
    collection = (await get_db()).get_collection("prompt_templates")
    doc = await _find_prompt_by_id(collection, prompt_id)
    if not doc:
        raise HTTPException(404, "Prompt template not found")

    task_type = doc["task_type"]
    await _activate_version(collection, prompt_id, task_type)

    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.UPDATE,
        resource=AuditResource.SETTINGS, resource_id=prompt_id,
        resource_name=f"activate prompt {task_type} v{doc.get('version')}",
        ip=_get_client_ip(request), extra={"task_type": task_type},
    )
    return {"activated": True, "task_type": task_type}


async def _activate_version(collection, prompt_id: str, task_type: str) -> None:
    """停用同 task_type 其他版本，激活指定版本，失效缓存。"""
    now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    # 先停用同 task_type 所有 active 版本
    await collection.update_many(
        {"task_type": task_type, "active": True},
        {"$set": {"active": False, "updated_at": now}},
    )
    # 再激活目标版本
    try:
        await collection.update_one(
            {"_id": ObjectId(prompt_id)},
            {"$set": {"active": True, "updated_at": now}},
        )
    except Exception:
        await collection.update_one(
            {"id": prompt_id},
            {"$set": {"active": True, "updated_at": now}},
        )
    # 失效 AiAnalyzerService 的内存缓存，下次分析读到新版本
    AiAnalyzerService.invalidate_prompt_cache(task_type)
    logger.info("Prompt {} activated, cache invalidated", task_type)


# ── 删除（仅未激活版本可删） ─────────────────────────────────

@router.delete("/prompts/{prompt_id}")
async def delete_prompt(
    prompt_id: str,
    request: Request = None,
    audit_service: AuditService = Depends(get_audit_service),
):
    """删除 prompt 版本。激活中的版本不允许删除。"""
    collection = (await get_db()).get_collection("prompt_templates")
    doc = await _find_prompt_by_id(collection, prompt_id)
    if not doc:
        raise HTTPException(404, "Prompt template not found")
    if doc.get("active"):
        raise HTTPException(409, "Cannot delete active version")

    await collection.delete_one({"_id": doc["_id"]})

    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.DELETE,
        resource=AuditResource.SETTINGS, resource_id=prompt_id,
        resource_name=f"prompt {doc.get('task_type')} v{doc.get('version')}",
        ip=_get_client_ip(request),
    )
    return {"deleted": True}


# ── 重置为默认（删除该 task_type 全部自定义版本，回退代码默认） ──
# P1-6: 用 /prompts/task-type/{task_type}/reset 路径，避免被 /prompts/{prompt_id} 捕获
@router.post("/prompts/task-type/{task_type}/reset")
async def reset_prompt_to_default(
    task_type: str,
    request: Request = None,
    audit_service: AuditService = Depends(get_audit_service),
):
    """重置指定 task_type 的所有自定义版本，回退到代码默认 prompt。"""
    if task_type not in PROMPT_TASK_TYPES:
        raise HTTPException(400, f"Invalid task_type, valid: {PROMPT_TASK_TYPES}")

    collection = (await get_db()).get_collection("prompt_templates")
    result = await collection.delete_many({"task_type": task_type})
    # 失效缓存，下次回退到代码默认值
    AiAnalyzerService.invalidate_prompt_cache(task_type)

    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.DELETE,
        resource=AuditResource.SETTINGS, resource_id=task_type,
        resource_name=f"reset prompt {task_type} to default",
        ip=_get_client_ip(request), extra={"task_type": task_type, "deleted": result.deleted_count},
    )
    return {"reset": True, "task_type": task_type, "deleted_versions": result.deleted_count}
