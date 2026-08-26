"""
依赖图路由（Q1/D3）——数据驱动依赖发现的查看与人工确认。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db
from services.dependency_service import DependencyService
from api.deps import ensure_project_access, get_current_user
from api import state as api_state

router = APIRouter(tags=["Dependencies"])


def get_dependency_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> DependencyService:
    return DependencyService(db)


@router.get("/projects/{project_id}/dependency-graph")
async def dependency_graph(
    project_id: str,
    current_user: dict = Depends(get_current_user),
    service: DependencyService = Depends(get_dependency_service),
):
    """返回项目依赖图边集（置信度 >= 阈值、未拒绝），供前端拓扑展示。"""
    ensure_project_access(current_user, project_id)
    edges = await service.build_graph(project_id)
    return {"project_id": project_id, "edges": edges, "count": len(edges)}


@router.get("/projects/{project_id}/dependencies")
async def list_dependencies(
    project_id: str,
    status: str = Query(default="", description="candidate/confirmed/rejected"),
    current_user: dict = Depends(get_current_user),
    service: DependencyService = Depends(get_dependency_service),
):
    """列出依赖边（可筛选状态），默认返回全部。"""
    ensure_project_access(current_user, project_id)
    edges = await service.list_edges(project_id, status)
    return {"project_id": project_id, "edges": edges, "count": len(edges)}


@router.post("/dependencies/{edge_id}/confirm")
async def confirm_dependency(
    edge_id: str,
    current_user: dict = Depends(get_current_user),
    service: DependencyService = Depends(get_dependency_service),
):
    """人工确认依赖边为真（升级 evidence=human_confirm）。"""
    ok = await service.set_edge_status(edge_id, "confirmed")
    if not ok:
        raise HTTPException(status_code=404, detail="Dependency edge not found")
    await service.promote_evidence(edge_id, "human_confirm")
    return {"ok": True, "edge_id": edge_id, "status": "confirmed"}


@router.post("/dependencies/{edge_id}/reject")
async def reject_dependency(
    edge_id: str,
    current_user: dict = Depends(get_current_user),
    service: DependencyService = Depends(get_dependency_service),
):
    """人工否决依赖边（之后不再进入图/生成约束）。"""
    ok = await service.set_edge_status(edge_id, "rejected")
    if not ok:
        raise HTTPException(status_code=404, detail="Dependency edge not found")
    return {"ok": True, "edge_id": edge_id, "status": "rejected"}


@router.post("/projects/{project_id}/dependencies/rebuild")
async def rebuild_dependencies(
    project_id: str,
    current_user: dict = Depends(get_current_user),
    service: DependencyService = Depends(get_dependency_service),
):
    """重新做一次静态依赖挖掘（幂等，按 (up,down) 合并）。"""
    ensure_project_access(current_user, project_id)
    n = await service.discover_static_edges(project_id, force=True)
    return {"project_id": project_id, "discovered_or_updated": n}


@router.post("/projects/{project_id}/dependencies/probe")
async def probe_dependencies(
    project_id: str,
    current_user: dict = Depends(get_current_user),
    service: DependencyService = Depends(get_dependency_service),
):
    """P2 动态探测：用真实响应样本校验候选依赖边的字段路径（近似动态证据）。"""
    ensure_project_access(current_user, project_id)
    n = await service.probe_dependencies(project_id)
    return {"project_id": project_id, "confirmed": n}


@router.post("/generations/{generation_id}/trial-run")
async def trial_run_generation(
    generation_id: str,
    current_user: dict = Depends(get_current_user),
):
    """手动触发某生成版本的试跑（Gate3 自证），供审阅前确认。"""
    if api_state._ai_analyzer is None:
        raise HTTPException(status_code=503, detail="AI analyzer not ready")
    from services.trial_runner import trial_run_generation as _trial_run
    ok = await _trial_run(api_state._ai_analyzer, generation_id)
    if not ok:
        raise HTTPException(status_code=422, detail="Trial run failed or not executable")
    return {"ok": True, "generation_id": generation_id}
