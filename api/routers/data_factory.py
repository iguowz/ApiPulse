"""
数据工厂 (DataFactory) 路由 —— 模板 CRUD + 数据生成 + 模板推断
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db, get_redis
from data_factory.factory import DataFactory, get_faker_methods
from models.dsl import ApiDSL, DataTemplate, Dataset, ScenarioDSL, ScenarioStep, ScenarioStatus, ScenarioStepType
from models.audit import AuditAction, AuditResource
from services.audit_service import AuditService
from services.ai_job_service import AiJobService
from services.scenario_service import ScenarioService
from api.deps import (
    _get_user_from_request,
    _get_client_ip,
    ensure_project_access,
    get_current_user,
    visible_project_id,
)

router = APIRouter(tags=["DataFactory"])


def get_audit_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> AuditService:
    return AuditService(db)


async def _ensure_api_in_project(db: AsyncIOMotorDatabase, api_id: str, project_id: str) -> dict[str, Any] | None:
    if not api_id:
        return None
    api_doc = await db["api_dsls"].find_one({"id": api_id}, {"_id": 0})
    if not api_doc:
        raise HTTPException(404, "API not found")
    if api_doc.get("project_id", "default") != project_id:
        raise HTTPException(403, "API does not belong to current project")
    return api_doc


async def _load_template_or_404(db: AsyncIOMotorDatabase, template_id: str, current_user: dict) -> dict[str, Any]:
    doc = await db["data_templates"].find_one({"id": template_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Template not found")
    ensure_project_access(current_user, doc.get("project_id", "default"))
    return doc


async def _load_dataset_or_404(db: AsyncIOMotorDatabase, dataset_id: str, current_user: dict) -> dict[str, Any]:
    doc = await db["datasets"].find_one({"id": dataset_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Dataset not found")
    ensure_project_access(current_user, doc.get("project_id", "default"))
    return doc


def _validation_or_422(template: DataTemplate) -> None:
    issues = DataFactory.validate_template(template)
    if any(i.get("level") == "error" for i in issues):
        raise HTTPException(status_code=422, detail={"valid": False, "issues": issues})


def _template_copy_name(source_name: str, explicit_name: str = "") -> str:
    """复制模板时生成默认名称，避免用户快速复制后难以区分来源。"""
    name = (explicit_name or "").strip()
    if name:
        return name
    base = (source_name or "数据模板").strip()
    return f"{base} - copy"


def _scenario_name_from_template(template_name: str, explicit_name: str = "") -> str:
    """从数据模板创建场景时生成默认名称，体现该场景依赖的数据工厂模板。"""
    name = (explicit_name or "").strip()
    if name:
        return name
    base = (template_name or "数据工厂模板").strip()
    return f"{base} 数据场景"


@router.post("/templates", status_code=201)
async def create_template(
    template: DataTemplate = Body(...),
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    audit_service: AuditService = Depends(get_audit_service),
):
    project_id = visible_project_id(current_user, template.project_id)
    # 支持选择场景：校验 scenario 归属项目，若未指定 api_id 则从场景首个 API 步骤自动填充
    if template.scenario_id:
        scenario_doc = await db["scenarios"].find_one({"id": template.scenario_id}, {"_id": 0})
        if not scenario_doc:
            raise HTTPException(404, "Scenario not found")
        if scenario_doc.get("project_id", "default") != project_id:
            raise HTTPException(403, "Scenario does not belong to current project")
        if not template.api_id:
            # 从场景第一个 API 步骤自动填充 api_id
            steps = scenario_doc.get("steps") or []
            for step in steps:
                if step.get("type") in ("api", None) and step.get("api_id"):
                    template.api_id = step["api_id"]
                    break
    await _ensure_api_in_project(db, template.api_id, project_id)
    template.id = str(uuid.uuid4())
    template.project_id = project_id
    template.source = template.source or "manual"
    template.updated_by = (current_user or {}).get("username", "")
    template.created_at = template.updated_at = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    _validation_or_422(template)
    await db["data_templates"].insert_one(template.model_dump())
    # 审计日志：记录模板创建操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.CREATE,
        resource=AuditResource.TEMPLATE, resource_id=template.id, resource_name=template.name,
        ip=_get_client_ip(request),
    )
    return template


# P0-1: faker 方法分组元数据端点（前端动态渲染分组下拉，消除前后端不一致）
@router.get("/datafactory/faker-methods")
async def list_faker_methods():
    """返回 faker 方法分组列表（12 组共 94 个方法），前端按分组渲染下拉。
    替代前端硬编码的 25 个方法，确保前后端白名单一致。"""
    return {"groups": get_faker_methods()}


@router.get("/templates")
async def list_templates(
    api_id: str | None = None,
    project_id: str | None = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    project_id = visible_project_id(current_user, project_id)
    q: dict[str, Any] = {"project_id": project_id}
    if api_id:
        await _ensure_api_in_project(db, api_id, project_id)
        q["api_id"] = api_id
    return await db["data_templates"].find(q, {"_id": 0}).to_list(200)


@router.get("/datasets")
async def list_datasets(
    template_id: str | None = None,
    api_id: str | None = None,
    project_id: str | None = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    project_id = visible_project_id(current_user, project_id)
    q: dict[str, Any] = {"project_id": project_id}
    if template_id:
        # 先校验模板归属，避免跨项目通过 template_id 枚举数据集。
        await _load_template_or_404(db, template_id, current_user)
        q["template_id"] = template_id
    if api_id:
        await _ensure_api_in_project(db, api_id, project_id)
        q["api_id"] = api_id
    # 列表页只展示元数据，records 在详情接口按需加载，避免大数据集拖慢页面。
    return await db["datasets"].find(q, {"_id": 0, "records": 0}).sort("created_at", -1).to_list(200)


@router.get("/datasets/{dataset_id}")
async def get_dataset(
    dataset_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await _load_dataset_or_404(db, dataset_id, current_user)


@router.post("/datasets", status_code=201)
async def create_dataset(
    body: dict[str, Any] = Body(...),
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    audit_service: AuditService = Depends(get_audit_service),
):
    records = body.get("records") or []
    if not isinstance(records, list) or not records:
        raise HTTPException(status_code=400, detail="records must be a non-empty list")
    if len(records) > 1000:
        raise HTTPException(status_code=400, detail="records exceed max size 1000")
    if not all(isinstance(item, dict) for item in records):
        raise HTTPException(status_code=400, detail="each record must be an object")

    project_id = visible_project_id(current_user, body.get("project_id"))
    template_id = str(body.get("template_id") or "")
    api_id = str(body.get("api_id") or "")
    if template_id:
        # 使用模板归属作为数据集归属，解决前端传 project_id 不准时的数据漂移问题。
        tmpl_doc = await _load_template_or_404(db, template_id, current_user)
        project_id = tmpl_doc.get("project_id", "default")
        template_api_id = tmpl_doc.get("api_id", "")
        if api_id and template_api_id and api_id != template_api_id:
            raise HTTPException(status_code=400, detail="api_id does not match template")
        api_id = template_api_id or api_id
        if api_id and not template_api_id:
            # 模板未绑定 API 但请求显式传入时，仍要校验接口归属。
            await _ensure_api_in_project(db, api_id, project_id)
    elif api_id:
        await _ensure_api_in_project(db, api_id, project_id)

    now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    dataset = Dataset(
        id=str(uuid.uuid4()),
        name=str(body.get("name") or f"dataset-{now.strftime('%Y%m%d%H%M%S')}"),
        template_id=template_id,
        api_id=api_id,
        project_id=project_id,
        source=str(body.get("source") or "generated"),
        records=records,
        count=len(records),
        created_by=(current_user or {}).get("username", ""),
        created_at=now,
    )
    await db["datasets"].insert_one(dataset.model_dump())
    # 审计日志：记录数据集创建，便于追踪生成数据被保存和复用的入口。
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.CREATE,
        resource=AuditResource.DATASET, resource_id=dataset.id, resource_name=dataset.name,
        ip=_get_client_ip(request),
    )
    return dataset


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(
    dataset_id: str,
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    audit_service: AuditService = Depends(get_audit_service),
):
    doc = await _load_dataset_or_404(db, dataset_id, current_user)
    r = await db["datasets"].delete_one({"id": dataset_id, "project_id": doc.get("project_id", "default")})
    if not r.deleted_count:
        raise HTTPException(404, "Dataset not found")
    # 审计日志：记录数据集删除，防止测试数据资产被静默移除。
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.DELETE,
        resource=AuditResource.DATASET, resource_id=dataset_id, resource_name=doc.get("name", dataset_id),
        ip=_get_client_ip(request),
    )
    return {"deleted": True}


@router.post("/templates/{template_id}/duplicate", status_code=201)
async def duplicate_template(
    template_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    audit_service: AuditService = Depends(get_audit_service),
):
    old = await _load_template_or_404(db, template_id, current_user)
    project_id = old.get("project_id", "default")
    api_id = str(body.get("api_id") or old.get("api_id", ""))
    if api_id:
        await _ensure_api_in_project(db, api_id, project_id)

    now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    # 复制模板：保留字段生成规则，重置身份/时间/AI job，便于用户快速改出相似数据场景。
    copied = DataTemplate(**{
        **old,
        "id": str(uuid.uuid4()),
        "name": _template_copy_name(old.get("name", ""), str(body.get("name") or "")),
        "api_id": api_id,
        "project_id": project_id,
        "source": "copied",
        "job_id": "",
        "updated_by": (current_user or {}).get("username", ""),
        "created_at": now,
        "updated_at": now,
    })
    _validation_or_422(copied)
    await db["data_templates"].insert_one(copied.model_dump())
    # 审计日志：记录模板复制来源，方便追踪相似模板的派生关系。
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.CREATE,
        resource=AuditResource.TEMPLATE, resource_id=copied.id, resource_name=copied.name,
        ip=_get_client_ip(request), project_id=project_id,
        extra={"source_template_id": template_id, "action": "duplicate"},
    )
    return copied


@router.post("/templates/{template_id}/scenario", status_code=201)
async def create_scenario_from_template(
    template_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    audit_service: AuditService = Depends(get_audit_service),
):
    tmpl_doc = await _load_template_or_404(db, template_id, current_user)
    project_id = tmpl_doc.get("project_id", "default")
    api_id = tmpl_doc.get("api_id", "")
    if not api_id:
        raise HTTPException(status_code=400, detail="Template must be linked to an API before creating scenario")
    api_doc = await _ensure_api_in_project(db, api_id, project_id)

    api_name = (api_doc or {}).get("name") or ((api_doc or {}).get("request") or {}).get("path") or api_id
    scenario = ScenarioDSL(
        name=_scenario_name_from_template(tmpl_doc.get("name", ""), str(body.get("name") or "")),
        description=str(body.get("description") or f"Created from data template: {tmpl_doc.get('name', template_id)}"),
        project_id=project_id,
        status=ScenarioStatus.DRAFT,
        scenario_type="single",
        source_api_ids=[api_id],
        tags=["data_factory"],
        steps=[
            ScenarioStep(
                step_id="start",
                type=ScenarioStepType.START,
                name="Start",
                depends_on=[],
            ),
            ScenarioStep(
                step_id="step_1",
                type=ScenarioStepType.API,
                api_id=api_id,
                name=api_name,
                depends_on=["start"],
                data_template_id=template_id,
            ),
            ScenarioStep(
                step_id="end",
                type=ScenarioStepType.END,
                name="End",
                depends_on=["step_1"],
            ),
        ],
    )
    # 服务端创建前复用同一套场景校验，避免数据工厂入口生成不可执行 DSL。
    validation = await ScenarioService(db).validate_scenario_doc(scenario.model_dump(mode="json"), project_id)
    if not validation.get("valid"):
        raise HTTPException(status_code=422, detail=validation)
    created = await ScenarioService(db).create_scenario(scenario, project_id)
    # 审计日志：记录数据工厂模板派生场景，串起模板 → 场景的用户操作链路。
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.CREATE,
        resource=AuditResource.SCENARIO, resource_id=created.id, resource_name=created.name,
        ip=_get_client_ip(request), project_id=project_id,
        extra={"template_id": template_id, "api_id": api_id, "action": "from_data_template"},
    )
    return created


@router.get("/templates/{template_id}")
async def get_template(
    template_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return await _load_template_or_404(db, template_id, current_user)


@router.put("/templates/{template_id}")
async def update_template(
    template_id: str,
    data: dict[str, Any] = Body(...),
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    audit_service: AuditService = Depends(get_audit_service),
):
    old = await _load_template_or_404(db, template_id, current_user)
    data.pop("id", None)
    data.pop("project_id", None)
    project_id = old.get("project_id", "default")
    api_id = data.get("api_id", old.get("api_id", ""))
    await _ensure_api_in_project(db, api_id, project_id)
    data["updated_at"] = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    data["updated_by"] = (current_user or {}).get("username", "")
    draft = DataTemplate(**{**old, **data, "project_id": project_id})
    _validation_or_422(draft)
    r = await db["data_templates"].update_one({"id": template_id, "project_id": project_id}, {"$set": data})
    if not r.matched_count:
        raise HTTPException(404, "Template not found")
    # 审计日志：记录模板更新操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.UPDATE,
        resource=AuditResource.TEMPLATE, resource_id=template_id, resource_name=data.get("name", template_id),
        ip=_get_client_ip(request),
    )
    return {"updated": True}


@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: str,
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    audit_service: AuditService = Depends(get_audit_service),
):
    doc = await _load_template_or_404(db, template_id, current_user)
    r = await db["data_templates"].delete_one({"id": template_id, "project_id": doc.get("project_id", "default")})
    if not r.deleted_count:
        raise HTTPException(404, "Template not found")
    # 审计日志：记录模板删除操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.DELETE,
        resource=AuditResource.TEMPLATE, resource_id=template_id, resource_name=template_id,
        ip=_get_client_ip(request),
    )
    return {"deleted": True}


@router.post("/datafactory/generate")
async def generate_data(
    body: dict[str, Any] = Body(...),
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    api_id = body.get("api_id", "")
    template_id = body.get("template_id")
    template_body = body.get("template")
    count = min(int(body.get("count", 1)), 100)
    context = body.get("context", {})
    requested_project_id = body.get("project_id")
    project_id = visible_project_id(current_user, requested_project_id)

    redis = await get_redis()
    factory = DataFactory(redis)

    if isinstance(template_body, dict):
        template_body = {**template_body, "project_id": project_id}
        await _ensure_api_in_project(db, template_body.get("api_id", ""), project_id)
        template = DataTemplate(**template_body)
        _validation_or_422(template)
    elif template_id:
        doc = await _load_template_or_404(db, template_id, current_user)
        template = DataTemplate(**doc)
    else:
        api_doc = await _ensure_api_in_project(db, api_id, project_id)
        template = DataFactory.infer_template(api_id, ApiDSL(**api_doc).request.body)
        template.project_id = project_id

    cache_key, data = await factory.generate_and_cache(template, context=context, count=count)
    return {"cache_key": cache_key, "data": data, "count": len(data), "template_id": template.id, "project_id": template.project_id}


@router.post("/datafactory/infer")
async def infer_template(
    body: dict[str, Any] = Body(...),
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    api_id = body.get("api_id", "")
    project_id = visible_project_id(current_user, body.get("project_id"))
    doc = await _ensure_api_in_project(db, api_id, project_id)
    template = DataFactory.infer_template(api_id, ApiDSL(**doc).request.body)
    template.project_id = project_id
    return {"template": template.model_dump(), "issues": DataFactory.validate_template(template), "source_api": {"id": api_id, "name": doc.get("name", ""), "path": doc.get("request", {}).get("path", "")}}


# P1-1: AI 增强数据模板 —— 异步入队，结果走 GenerationVersion 审核闭环
@router.post("/templates/{template_id}/ai-enhance")
async def ai_enhance_template(
    template_id: str,
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    audit_service: AuditService = Depends(get_audit_service),
):
    """
    AI 增强数据模板字段配置。
    异步流程：入队 → worker 调 LLM → 生成 GenerationVersion(data_template) → 用户审核。
    解决问题：纯正则推断（infer_template）无法理解业务语义，无法生成针对性边界值/异常值。
    """
    import api.state as _state
    if _state._ai_analyzer is None:
        raise HTTPException(503, "AI service not available")
    # 校验模板存在
    tmpl_doc = await _load_template_or_404(db, template_id, current_user)
    project_id = tmpl_doc.get("project_id", "default")

    redis = await get_redis()
    job_id = f"data_template:{template_id}:{uuid.uuid4().hex[:8]}"
    # 入队 data_template AI 增强任务，并持久化 job 观测记录用于设置页恢复。
    import json
    task = {
        "template_id": template_id,
        "project_id": project_id,
        "job_id": job_id,
        "status": "queued",
    }
    payload = json.dumps(task, ensure_ascii=False)
    await redis.rpush("queue:data_template", payload)
    await AiJobService(db).mark_queued(
        job_id=job_id,
        type="data_template",
        project_id=project_id,
        source="data_factory",
        target_ids=[template_id],
        queue_key="queue:data_template",
        payload=task,
        user_id=current_user.get("user_id", ""),
    )
    await db["data_templates"].update_one(
        {"id": template_id, "project_id": project_id},
        {"$set": {"job_id": job_id, "updated_at": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)}},
    )

    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.UPDATE,
        resource=AuditResource.TEMPLATE, resource_id=template_id,
        resource_name=tmpl_doc.get("name", template_id),
        ip=_get_client_ip(request), extra={"action": "ai_enhance"},
    )
    return {"queued": True, "job_id": job_id, "template_id": template_id, "project_id": project_id, "status": "queued"}
