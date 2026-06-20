"""
Knowledge (ReMe 记忆系统) 路由
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db
from models.knowledge import KnowledgeFeedback, KnowledgeUpdate
from models.audit import AuditAction, AuditResource
from services.audit_service import AuditService
from api.deps import _get_user_from_request, _get_client_ip, ensure_project_access, get_current_user, require_auth
# 使用 import api.state 而非 from import，确保读取到 startup() 后注入的实例
import api.state as _state

router = APIRouter(tags=["Knowledge"])


def get_audit_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> AuditService:
    return AuditService(db)


@router.get("/knowledge")
async def list_knowledge(
    project_id: str = Query(default="default"),
    type: str | None = Query(default=None),
    search: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=200),
    request: Request = None,
    user: dict = Depends(get_current_user),
):
    """分页列出知识库条目，支持类型筛选和关键词搜索"""
    ensure_project_access(user, project_id)
    if _state._knowledge_service is None:
        raise HTTPException(503, "Knowledge service not available")
    items, total = await _state._knowledge_service.list_entries(project_id, type, search, skip, limit)
    return {"total": total, "items": items}


@router.get("/knowledge/{entry_id}")
async def get_knowledge(entry_id: str, request: Request = None, user: dict = Depends(require_auth)):
    """获取单条记忆详情"""
    if _state._knowledge_service is None:
        raise HTTPException(503, "Knowledge service not available")
    doc = await _state._knowledge_service.get_entry(entry_id)
    if not doc:
        raise HTTPException(404, "Knowledge entry not found")
    return doc


@router.put("/knowledge/{entry_id}")
async def update_knowledge(
    entry_id: str,
    data: KnowledgeUpdate = Body(...),
    request: Request = None,
    audit_service: AuditService = Depends(get_audit_service),
    user: dict = Depends(require_auth),
):
    """更新记忆条目（用户手动编辑）"""
    if _state._knowledge_service is None:
        raise HTTPException(503, "Knowledge service not available")
    ok = await _state._knowledge_service.update_entry(entry_id, data.model_dump(exclude_none=True))
    if not ok:
        raise HTTPException(404, "Knowledge entry not found")
    # 审计日志：记录知识条目更新操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.UPDATE,
        resource=AuditResource.KNOWLEDGE, resource_id=entry_id, resource_name=data.title or entry_id,
        ip=_get_client_ip(request),
    )
    return {"updated": True}


@router.delete("/knowledge/batch-delete")
async def batch_delete_knowledge(
    data: dict[str, Any] = Body(...),
    request: Request = None,
    audit_service: AuditService = Depends(get_audit_service),
    user: dict = Depends(get_current_user),
):
    """批量删除记忆条目（需验证项目访问权限）"""
    if _state._knowledge_service is None:
        raise HTTPException(503, "Knowledge service not available")
    ids = data.get("ids", [])
    if not ids:
        raise HTTPException(400, "ids list is required")
    # 校验项目访问权限，防止跨项目删除
    project_id = data.get("project_id", "")
    if project_id:
        ensure_project_access(user, project_id)
    # 传入 project_id 确保只删除当前项目下的条目
    n = await _state._knowledge_service.batch_delete(ids, project_id=project_id if project_id else None)
    # 审计日志：记录批量删除知识条目操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.BATCH_DELETE,
        resource=AuditResource.KNOWLEDGE, resource_id="", resource_name=f"{n} entries",
        ip=_get_client_ip(request), extra={"ids": ids, "count": n},
    )
    return {"deleted": n}


@router.delete("/knowledge/{entry_id}")
async def delete_knowledge(
    entry_id: str,
    request: Request = None,
    audit_service: AuditService = Depends(get_audit_service),
    user: dict = Depends(require_auth),
):
    """删除单条记忆"""
    if _state._knowledge_service is None:
        raise HTTPException(503, "Knowledge service not available")
    ok = await _state._knowledge_service.delete_entry(entry_id)
    if not ok:
        raise HTTPException(404, "Knowledge entry not found")
    # 审计日志：记录知识条目删除操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.DELETE,
        resource=AuditResource.KNOWLEDGE, resource_id=entry_id, resource_name=entry_id,
        ip=_get_client_ip(request),
    )
    return {"deleted": True}


@router.post("/knowledge/{entry_id}/feedback")
async def feedback_knowledge(entry_id: str, data: KnowledgeFeedback = Body(...), request: Request = None, user: dict = Depends(require_auth)):
    """用户反馈：upvote 提升置信度，downvote 降低置信度"""
    if _state._knowledge_service is None:
        raise HTTPException(503, "Knowledge service not available")
    if data.action not in ("upvote", "downvote"):
        raise HTTPException(400, "action must be 'upvote' or 'downvote'")
    result = await _state._knowledge_service.submit_feedback(entry_id, data.action)
    if result is None:
        raise HTTPException(404, "Knowledge entry not found")
    return result


@router.post("/knowledge/extract/{api_id}")
async def extract_knowledge(
    api_id: str,
    request: Request = None,
    audit_service: AuditService = Depends(get_audit_service),
    user: dict = Depends(require_auth),
):
    """从单个已分析 API 手动提取记忆"""
    if _state._knowledge_service is None:
        raise HTTPException(503, "Knowledge service not available")
    if _state._ai_analyzer is None:
        raise HTTPException(503, "AI analyzer not available")
    n = await _state._knowledge_service.extract_from_api(api_id, _state._ai_analyzer._call_llm)
    # 审计日志：记录知识提取操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.EXTRACT,
        resource=AuditResource.KNOWLEDGE, resource_id=api_id, resource_name=api_id,
        ip=_get_client_ip(request), extra={"created": n},
    )
    return {"created": n, "api_id": api_id}


@router.post("/knowledge/batch-extract")
async def batch_extract_knowledge(
    data: dict[str, Any] = Body(...),
    request: Request = None,
    audit_service: AuditService = Depends(get_audit_service),
    user: dict = Depends(get_current_user),
):
    """批量提取项目下所有已分析 API 的记忆（异步，通过 WebSocket 推送进度）"""
    if _state._knowledge_service is None:
        raise HTTPException(503, "Knowledge service not available")
    if _state._ai_analyzer is None:
        raise HTTPException(503, "AI analyzer not available")
    project_id = data.get("project_id", "default")
    ensure_project_access(user, project_id)
    asyncio.create_task(_state._knowledge_service.batch_extract(project_id, _state._ai_analyzer._call_llm))
    # 审计日志：记录批量知识提取操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.BATCH_EXTRACT,
        resource=AuditResource.KNOWLEDGE, resource_id="", resource_name=project_id,
        ip=_get_client_ip(request), extra={"project_id": project_id},
    )
    return {"queued": True, "project_id": project_id}


@router.post("/knowledge/consolidate")
async def consolidate_knowledge(
    data: dict[str, Any] = Body(...),
    request: Request = None,
    audit_service: AuditService = Depends(get_audit_service),
    user: dict = Depends(get_current_user),
):
    """合并去重知识库条目"""
    if _state._knowledge_service is None:
        raise HTTPException(503, "Knowledge service not available")
    project_id = data.get("project_id", "default")
    ensure_project_access(user, project_id)
    result = await _state._knowledge_service.consolidate(project_id)
    # 审计日志：记录知识合并操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.UPDATE,
        resource=AuditResource.KNOWLEDGE, resource_id="", resource_name=f"consolidate {project_id}",
        ip=_get_client_ip(request), extra=result,
    )
    return result
