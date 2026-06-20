"""
Memory (4-tier 记忆) 路由 —— 浏览和检索 L1/L2/L3 记忆

L1: 长期记忆（跨项目，永久）—— 关键字检索
L2: 项目记忆（按 project_id 隔离）—— 语义+关键词检索
L3: 会话记忆（按项目隔离，30 天保留）—— 关键词检索
L4: 对话记忆（Redis，仅活跃会话）—— 不暴露给前端
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.deps import ensure_project_access, get_current_user, require_auth

# 使用 import api.state 而非 from import，确保读取到 startup() 后注入的实例
import api.state as _state

router = APIRouter(tags=["Memory"])


def _get_memory():
    """获取 MemoryService 实例，未就绪则抛 503。"""
    if _state._memory_service is None:
        raise HTTPException(503, "Memory service not available")
    return _state._memory_service


# ── L1 长期记忆 ──────────────────────────────────────────────

@router.get("/memory/l1")
async def list_l1(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    search: str | None = Query(default=None),
    api_id: str | None = Query(default=None),
    request: Request = None,
    user: dict = Depends(require_auth),
):
    """分页列出 L1 长期记忆，支持关键词搜索和按 api_id 过滤。"""
    memory = _get_memory()
    items, total = await memory.list_l1(skip=skip, limit=limit, api_id=api_id)
    # 按关键词过滤（MongoDB 不支持全文索引时前端侧过滤）
    if search:
        ql = search.lower()
        filtered = [
            it for it in items
            if ql in it.get("content", "").lower()
            or ql in it.get("key", "").lower()
            or any(ql in (t or "").lower() for t in it.get("tags", []))
        ]
        return {"total": len(filtered), "items": filtered}
    return {"total": total, "items": items}


@router.delete("/memory/l1/{key}")
async def delete_l1(key: str, request: Request = None, user: dict = Depends(require_auth)):
    """删除 L1 长期记忆条目（按 key）。"""
    memory = _get_memory()
    deleted = await memory.delete_l1(key)
    if not deleted:
        raise HTTPException(404, "L1 memory entry not found")
    return {"ok": True, "key": key}


# ── L2 项目记忆 ──────────────────────────────────────────────

@router.get("/memory/l2")
async def list_l2(
    project_id: str = Query(default="default"),
    search: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
    api_id: str | None = Query(default=None),
    request: Request = None,
    user: dict = Depends(get_current_user),
):
    """列出/检索 L2 项目记忆（按 project_id 隔离），可选按 api_id 过滤。"""
    ensure_project_access(user, project_id)
    memory = _get_memory()
    items = await memory.search_l2(project_id, search or "", limit=limit, api_id=api_id)
    return {"total": len(items), "items": items}


@router.delete("/memory/l2/{entry_id}")
async def delete_l2(entry_id: str, request: Request = None, user: dict = Depends(require_auth)):
    """删除 L2 项目记忆条目。"""
    memory = _get_memory()
    deleted = await memory.delete_l2(entry_id)
    if not deleted:
        raise HTTPException(404, "L2 memory entry not found")
    return {"ok": True, "id": entry_id}


# ── L3 会话记忆 ──────────────────────────────────────────────

@router.get("/memory/l3")
async def list_l3(
    project_id: str = Query(default="default"),
    search: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    api_id: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
    request: Request = None,
    user: dict = Depends(get_current_user),
):
    """列出/检索 L3 会话记忆（仅有效期内的条目），可选按 api_id 过滤。"""
    ensure_project_access(user, project_id)
    memory = _get_memory()
    items = await memory.search_l3(project_id, search or "", user_id=user_id, api_id=api_id, limit=limit)
    return {"total": len(items), "items": items}


# ── 聚合检索 ──────────────────────────────────────────────────

@router.post("/memory/search")
async def search_memory(
    project_id: str = Query(default="default"),
    query: str = Query(default=""),
    limit: int = Query(default=10, ge=1, le=50),
    request: Request = None,
    user: dict = Depends(get_current_user),
):
    """聚合检索 L1+L2+L3 + ReMe 语义检索。"""
    ensure_project_access(user, project_id)
    memory = _get_memory()
    results = await memory.retrieve(project_id, query, limit=limit)
    return results
