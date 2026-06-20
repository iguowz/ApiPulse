"""
场景 CRUD + 执行 + 导入导出 + AI 生成路由 —— 委托 services/scenario_service.py
"""
from __future__ import annotations

from typing import Any

import json
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db, get_redis
from models.dsl import ScenarioDSL
from models.audit import AuditAction, AuditResource
from services.scenario_service import ScenarioService
from services.audit_service import AuditService
from api.deps import (
    _get_user_from_request, _get_client_ip,
    ensure_project_access, get_current_user, visible_project_id,
)

from api.state import _ws
from dag_engine.engine import _run_step_script_detailed

router = APIRouter(tags=["Scenarios"])


# 依赖注入
def get_scenario_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> ScenarioService:
    return ScenarioService(db)


def get_audit_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> AuditService:
    return AuditService(db)


# P1-4: 场景步骤内联 AI 辅助 —— 推荐断言和 extract 规则
# 静态路由，必须在 /scenarios/{id} 前注册避免被捕获
@router.post("/scenarios/ai-recommend")
async def ai_recommend_step_config(
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    为场景步骤推荐断言和 extract 规则（内联辅助）。
    请求体：{api_id: "..."}，基于该 API 最近响应样本推荐。
    返回：{asserts: [...], extract: {...}, summary: "..."}
    用户在 StepEditor 内确认后填入，不走审核闭环（场景编辑是即时操作）。
    """
    import api.state as _state
    if _state._ai_analyzer is None:
        raise HTTPException(503, "AI service not available")
    api_id = body.get("api_id", "")
    if not api_id:
        raise HTTPException(400, "api_id is required")
    api_doc = await db["api_dsls"].find_one({"id": api_id}, {"project_id": 1, "name": 1, "request": 1})
    if not api_doc:
        raise HTTPException(404, "API not found")
    ensure_project_access(current_user, api_doc.get("project_id"))
    result = await _state._ai_analyzer.recommend_step_config(api_id)
    result["references"] = [{
        "type": "api",
        "id": api_id,
        "title": api_doc.get("name") or (api_doc.get("request") or {}).get("path") or api_id,
        "path": (api_doc.get("request") or {}).get("path", ""),
        "route": f"/apis/{api_id}",
    }]
    return result


@router.post("/scenarios/steps/script/dry-run")
async def dry_run_step_script(
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """脚本 dry-run：复用 DAG 引擎声明式脚本执行器，不访问外部接口。"""
    if not current_user:
        raise HTTPException(401, "Unauthorized")
    script = body.get("script", "")
    phase = body.get("phase") or "script"
    if phase not in {"pre", "post", "script"}:
        raise HTTPException(422, "phase must be pre/post/script")
    context = body.get("context") or {}
    response = body.get("response")
    if not isinstance(context, dict):
        raise HTTPException(422, "context must be object")
    started = time.perf_counter()
    output, details = _run_step_script_detailed(script, context, response=response, phase=phase)
    duration_ms = int((time.perf_counter() - started) * 1000)
    return {
        "ok": all(item.get("ok", False) for item in details) if details else True,
        "phase": phase,
        "output": output,
        "details": details,
        "duration_ms": duration_ms,
        "context_after": {**context, **output},
    }


@router.get("/scenarios")
async def list_scenarios(
    project_id: str = "default",
    skip: int = 0,
    limit: int = 50,
    api_id: str = "",
    search: str = "",
    status: str = "",
    scenario_type: str = "",  # 场景类型筛选：single=单接口，multi=多步骤场景
    current_user: dict = Depends(get_current_user),
    service: ScenarioService = Depends(get_scenario_service),
):
    project_id = visible_project_id(current_user, project_id)
    return await service.list_scenarios(
        project_id=project_id, skip=skip, limit=limit,
        api_id=api_id, search=search, status=status,
        scenario_type=scenario_type,
    )


@router.get("/scenarios/{scenario_id}")
async def get_scenario(
    scenario_id: str,
    current_user: dict = Depends(get_current_user),
    service: ScenarioService = Depends(get_scenario_service),
):
    doc = await service.get_scenario(scenario_id)
    if not doc:
        raise HTTPException(404, "Scenario not found")
    ensure_project_access(current_user, doc.get("project_id"))
    return doc


@router.get("/scenarios/{scenario_id}/validate")
async def validate_scenario(
    scenario_id: str,
    current_user: dict = Depends(get_current_user),
    service: ScenarioService = Depends(get_scenario_service),
):
    doc = await service.get_scenario(scenario_id)
    if not doc:
        raise HTTPException(404, "Scenario not found")
    ensure_project_access(current_user, doc.get("project_id"))
    return await service.validate_scenario_doc(doc, doc.get("project_id", "default"))


@router.post("/scenarios/{scenario_id}/validate")
async def validate_scenario_draft(
    scenario_id: str,
    data: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    service: ScenarioService = Depends(get_scenario_service),
):
    old = await service.get_scenario(scenario_id)
    if not old:
        raise HTTPException(404, "Scenario not found")
    ensure_project_access(current_user, old.get("project_id"))
    # 校验前端当前草稿，避免用户看到 Start/End 节点但接口仍校验数据库旧版本。
    draft = {**old, **data, "project_id": old.get("project_id", "default")}
    return await service.validate_scenario_doc(draft, old.get("project_id", "default"))


@router.post("/scenarios/{scenario_id}/steps/preview")
async def preview_scenario_step_request(
    scenario_id: str,
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(get_current_user),
    service: ScenarioService = Depends(get_scenario_service),
):
    doc = await service.get_scenario(scenario_id)
    if not doc:
        raise HTTPException(404, "Scenario not found")
    ensure_project_access(current_user, doc.get("project_id"))
    step = body.get("step") or {}
    if not isinstance(step, dict):
        raise HTTPException(422, "step must be object")
    return await service.preview_step_request(
        scenario_id=scenario_id,
        step_data=step,
        project_id=doc.get("project_id", "default"),
        context=body.get("context") or {},
        environment_id=body.get("environment_id", ""),
    )


@router.post("/scenarios", status_code=201)
async def create_scenario(
    scenario: ScenarioDSL = Body(...),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    service: ScenarioService = Depends(get_scenario_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    project_id = visible_project_id(current_user, scenario.project_id)
    result = await service.create_scenario(scenario, project_id)
    # 审计日志：记录场景创建操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.CREATE,
        resource=AuditResource.SCENARIO, resource_id=scenario.id, resource_name=scenario.name,
        ip=_get_client_ip(request),
    )
    return result


@router.put("/scenarios/{scenario_id}")
async def update_scenario(
    scenario_id: str,
    data: dict[str, Any] = Body(...),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    service: ScenarioService = Depends(get_scenario_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    old = await service.get_scenario(scenario_id)
    if not old:
        raise HTTPException(404, "Scenario not found")
    ensure_project_access(current_user, old.get("project_id"))
    if data.get("steps") is not None:
        # 保存前用服务端同一套校验兜底；warning 允许保存，error 阻断。
        validation = await service.validate_scenario_doc({**old, **data}, old.get("project_id", "default"))
        if not validation.get("valid"):
            raise HTTPException(status_code=422, detail=validation)
    user = _get_user_from_request(request) or current_user or {}
    data["_actor"] = user.get("username", "")
    ok = await service.update_scenario(scenario_id, data, old.get("project_id", "default"))
    if not ok:
        raise HTTPException(404, "Scenario not found")
    # 审计日志：记录场景更新操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.UPDATE,
        resource=AuditResource.SCENARIO, resource_id=scenario_id, resource_name=data.get("name", scenario_id),
        ip=_get_client_ip(request),
    )
    return {"updated": True}


@router.get("/scenarios/{scenario_id}/versions")
async def list_scenario_versions(
    scenario_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    service: ScenarioService = Depends(get_scenario_service),
):
    """P2-1: 查询场景历史版本快照列表（不返回完整 snapshot）。"""
    doc = await service.get_scenario(scenario_id)
    if not doc:
        raise HTTPException(404, "Scenario not found")
    ensure_project_access(current_user, doc.get("project_id"))
    result = await service.list_scenario_versions(scenario_id, doc.get("project_id", "default"), limit)
    if result is None:
        raise HTTPException(404, "Scenario not found")
    return result


@router.post("/scenarios/{scenario_id}/versions/{version_id}/restore")
async def restore_scenario_version(
    scenario_id: str,
    version_id: str,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    service: ScenarioService = Depends(get_scenario_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    """P2-1: 将场景恢复到指定历史版本；恢复前会保存当前版本，支持反向撤回。"""
    doc = await service.get_scenario(scenario_id)
    if not doc:
        raise HTTPException(404, "Scenario not found")
    ensure_project_access(current_user, doc.get("project_id"))
    user = _get_user_from_request(request) or current_user or {}
    restored = await service.restore_scenario_version(
        scenario_id,
        version_id,
        doc.get("project_id", "default"),
        actor=user.get("username", ""),
    )
    if not restored:
        raise HTTPException(404, "Scenario not found")
    await audit_service.log_action(
        user=user,
        action=AuditAction.UPDATE,
        resource=AuditResource.SCENARIO,
        resource_id=scenario_id,
        resource_name=f"restore scenario version {version_id}",
        ip=_get_client_ip(request) if request else "",
        extra={"version_id": version_id},
    )
    return {"restored": True, "scenario": restored}


@router.delete("/scenarios/{scenario_id}")
async def delete_scenario(
    scenario_id: str,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    service: ScenarioService = Depends(get_scenario_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    doc = await service.get_scenario(scenario_id)
    if not doc:
        raise HTTPException(404, "Scenario not found")
    ensure_project_access(current_user, doc.get("project_id"))
    ok = await service.delete_scenario(scenario_id, doc.get("project_id", "default"))
    if not ok:
        raise HTTPException(404, "Scenario not found")
    # 审计日志：记录场景删除操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.DELETE,
        resource=AuditResource.SCENARIO, resource_id=scenario_id, resource_name=scenario_id,
        ip=_get_client_ip(request),
    )
    return {"deleted": True}


@router.post("/scenarios/batch_delete")
async def batch_delete_scenarios(
    ids: list[str] = Body(...),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    service: ScenarioService = Depends(get_scenario_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    """批量删除场景。按 ID 逐个校验项目访问权限，再统一删除，避免 visible_project_id(None) 回退到 JWT 默认项目导致跨项目删不到。"""
    # 逐个校验每个场景的项目归属权限（对齐 apis/batch_delete 模式）
    for sid in ids:
        doc = await service.get_scenario(sid)
        if doc:
            ensure_project_access(current_user, doc.get("project_id"))
    n = await service.batch_delete_scenarios(ids)
    # 审计日志：记录批量删除场景操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.BATCH_DELETE,
        resource=AuditResource.SCENARIO, resource_id="", resource_name=f"{n} scenarios",
        ip=_get_client_ip(request), extra={"ids": ids, "count": n},
    )
    return {"deleted_count": n}


@router.post("/scenarios/batch_run")
async def batch_run_scenarios(
    ids: list[str] = Body(...),
    environment_id: str = "",
    async_run: bool = Query(False, alias="async"),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    service: ScenarioService = Depends(get_scenario_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    """批量执行场景（并发执行，返回每个场景的执行结果）"""
    redis = await get_redis()
    user = _get_user_from_request(request) or {}
    owner = user.get("username", "")
    project_id = visible_project_id(current_user, None)
    if async_run:
        result = await service.enqueue_batch_run(
            ids, redis, project_id=project_id, environment_id=environment_id,
            owner=owner, client_ip=_get_client_ip(request), ws_manager=_ws,
        )
    else:
        results = await service.batch_run_scenarios(
            ids, redis, environment_id=environment_id, owner=owner,
            client_ip=_get_client_ip(request), project_id=project_id,
        )
        result = {"results": results}
    # 审计日志：记录批量执行场景操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.BATCH_EXECUTE,
        resource=AuditResource.SCENARIO, resource_id="", resource_name=f"{len(ids)} scenarios",
        ip=_get_client_ip(request), extra={"ids": ids, "count": len(ids), "async": async_run},
    )
    return result


@router.post("/scenarios/import")
async def import_scenarios(
    payload: dict[str, Any] = Body(...),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    service: ScenarioService = Depends(get_scenario_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    """批量导入场景（JSON 数组）"""
    items = payload.get("scenarios", payload.get("items", []))
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "scenarios must be a non-empty array")
    project_id = visible_project_id(current_user, payload.get("project_id"))
    result = await service.import_scenarios(items, project_id)
    # 审计日志：记录场景导入操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.IMPORT,
        resource=AuditResource.SCENARIO, resource_id="", resource_name=f"{result.get('imported', 0)} scenarios",
        ip=_get_client_ip(request), extra=result,
    )
    return result


@router.post("/scenarios/export")
async def export_scenarios(
    ids: list[str] = Body(...),
    current_user: dict = Depends(get_current_user),
    service: ScenarioService = Depends(get_scenario_service),
):
    """导出当前项目内指定场景；跨项目 ID 会被自动过滤。"""
    project_id = visible_project_id(current_user, None)
    docs = await service.db["scenarios"].find(
        {"id": {"$in": ids}, "project_id": project_id},
        {"_id": 0},
    ).sort("updated_at", -1).to_list(length=len(ids))
    content = json.dumps({"scenarios": docs, "project_id": project_id}, ensure_ascii=False, default=str)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=scenarios.json"},
    )


@router.post("/scenarios/{scenario_id}/run")
async def run_scenario(
    scenario_id: str,
    body: dict[str, Any] = Body(default={}),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    service: ScenarioService = Depends(get_scenario_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    """P0-2: 异步执行场景 —— 立即返回 exec_id，后台执行并通过 WS 实时广播步骤进度。
    前端拿到 exec_id 后订阅 /ws/execution/{exec_id}，逐节点接收 running/passed/failed 状态。"""
    redis = await get_redis()
    user = _get_user_from_request(request) or {}
    owner = user.get("username", "")

    # 预校验场景存在（快速返回 404，不进入异步执行）
    scenario_doc = await service.db["scenarios"].find_one({"id": scenario_id})
    if not scenario_doc:
        raise HTTPException(404, "Scenario not found")
    ensure_project_access(current_user, scenario_doc.get("project_id"))
    project_id = scenario_doc.get("project_id", "default")
    validation = await service.validate_scenario_doc(scenario_doc, project_id)
    if not validation.get("valid"):
        raise HTTPException(status_code=422, detail=validation)

    # 预生成 exec_id，立即返回给前端（前端据此建立 WS 订阅）
    import uuid as _uuid
    exec_id = str(_uuid.uuid4())

    # P0-2: 后台异步执行，不阻塞 HTTP 响应；执行中通过 WS 广播步骤进度
    async def _run_in_background():
        try:
            record = await service.run_scenario(
                scenario_id, redis,
                initial_context=body.get("context"),
                environment_id=body.get("environment_id", ""),
                owner=owner,
                client_ip=_get_client_ip(request),
                ws_manager=_ws,
                exec_id=exec_id,  # P0-2: 透传预生成的 exec_id，使 WS 频道一致
                project_id=project_id,
            )
            if record is None:
                # 场景在异步窗口被删除 → 广播失败事件
                await _ws.broadcast(f"exec:{exec_id}", {"type": "error", "message": "Scenario not found"})
                return
            # 广播执行完成事件（含完整 record，前端据此填充 result tab）
            await _ws.broadcast(f"exec:{exec_id}", {"type": "done", "record": record.model_dump()})
            # 失败时通知负责人
            if not record.passed and scenario_doc.get("owner"):
                await _ws.broadcast(f"scenario:events:{project_id}", {
                    "type": "execution_failed", "scenario_id": scenario_id,
                    "scenario_name": scenario_doc.get("name", ""), "exec_id": exec_id,
                    "project_id": project_id,
                    "failure_reason": record.failure_reason[:200],
                })
        except Exception as e:
            await _ws.broadcast(f"exec:{exec_id}", {"type": "error", "message": str(e)[:200]})

    import asyncio as _asyncio
    _asyncio.create_task(_run_in_background())

    # 审计日志：记录场景执行操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.EXECUTE,
        resource=AuditResource.SCENARIO, resource_id=scenario_id, resource_name=scenario_doc.get("name", scenario_id),
        ip=_get_client_ip(request),
    )
    # 立即返回 exec_id，前端订阅 WS 接收实时进度
    return {"exec_id": exec_id, "scenario_id": scenario_id, "project_id": project_id, "status": "running"}


@router.post("/scenarios/generate")
async def generate_scenarios(
    body: dict[str, Any] = Body(...),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    service: ScenarioService = Depends(get_scenario_service),
    audit_service: AuditService = Depends(get_audit_service),
):
    """异步化：推入 queue:ai_scenario，由后台 worker 处理"""
    api_ids = body.get("api_ids", [])
    project_id = visible_project_id(current_user, body.get("project_id", "default"))
    scenario_type = body.get("scenario_type", "")  # single/multi/complex，控制场景生成策略
    # 审计日志：记录 AI 场景生成请求
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.GENERATE,
        resource=AuditResource.SCENARIO, resource_id="", resource_name=f"from {len(api_ids)} APIs",
        ip=_get_client_ip(request), extra={"api_ids": api_ids},
    )
    redis = await get_redis()
    return await service.enqueue_generate(api_ids, project_id, redis, scenario_type, user_id=(current_user or {}).get("user_id", ""))
