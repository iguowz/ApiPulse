"""
监控 CRUD + AI 生成 + 校验 + 试跑路由
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db, get_redis
from models.audit import AuditAction, AuditResource
from models.dsl import MonitorDSL
from services.ai_job_service import AiJobService
from services.audit_service import AuditService
from api.deps import (
    _get_client_ip,
    ensure_project_access,
    get_current_user,
    require_permission,
    visible_project_id,
)
import api.state as _state

router = APIRouter(tags=["Monitor"])

MONITOR_GENERATE_QUEUE = "queue:ai_monitor"
_RUN_JOBS: dict[str, dict[str, Any]] = {}
_VALID_TARGET_TYPES = {"api", "scenario", "data_factory"}
_ASSERT_OPERATORS = {
    "eq", "ne", "gt", "gte", "lt", "lte", "contains", "not_contains",
    "starts_with", "ends_with", "regex", "exists", "not_exists", "empty",
    "not_empty", "in", "not_in", "type_match", "length", "json_schema",
    "header_eq", "header_contains", "response_time_lt",
}


def get_audit_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> AuditService:
    return AuditService(db)


def _now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def _issue(level: str, code: str, field: str, message: str, action: str = "") -> dict[str, str]:
    return {"level": level, "code": code, "field": field, "message": message, "action": action}


def _parse_interval_seconds(value: str) -> int | None:
    if not value or not isinstance(value, str) or len(value.strip()) < 2:
        return None
    raw = value.strip()
    unit = raw[-1].lower()
    try:
        num = int(raw[:-1])
    except Exception:
        logger.warning("Failed to parse interval seconds from value=%r", value)
        return None
    if num <= 0 or unit not in {"s", "m", "h", "d"}:
        return None
    return num * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def _normalize_monitor_payload(data: dict[str, Any], project_id: str, username: str = "") -> dict[str, Any]:
    """将外部输入收敛为新版 target_type + target_id 结构。"""
    payload = dict(data)
    payload.pop("id", None)
    target_type = payload.get("target_type") or "api"
    if target_type not in _VALID_TARGET_TYPES:
        target_type = "api"
    target_id = payload.get("target_id") or ""
    if target_type == "api":
        target_id = target_id or payload.get("api_id") or ""
        payload["api_id"] = target_id
    else:
        payload["api_id"] = ""
    payload["target_type"] = target_type
    payload["target_id"] = target_id
    payload["project_id"] = project_id
    payload["updated_by"] = username or payload.get("updated_by", "")
    payload["source"] = payload.get("source") or "manual"
    return payload


async def _load_monitor_or_404(db: AsyncIOMotorDatabase, monitor_id: str, user: dict[str, Any]) -> dict[str, Any]:
    doc = await db["monitors"].find_one({"id": monitor_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Monitor not found")
    ensure_project_access(user, doc.get("project_id", "default"))
    return doc


async def _validate_monitor_payload(
    db: AsyncIOMotorDatabase,
    payload: dict[str, Any],
    project_id: str,
    monitor_id: str = "",
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    target_type = payload.get("target_type") or "api"
    target_id = payload.get("target_id") or ""

    if target_type not in _VALID_TARGET_TYPES:
        issues.append(_issue("error", "invalid_target_type", "target_type", "目标类型无效", "选择 api/scenario/data_factory"))
    if not target_id:
        issues.append(_issue("error", "missing_target", "target_id", "请选择巡检目标", "选择一个项目内目标"))

    if target_id and target_type == "api":
        api_doc = await db["api_dsls"].find_one({"id": target_id}, {"_id": 0, "project_id": 1})
        if not api_doc:
            issues.append(_issue("error", "target_not_found", "target_id", "API 不存在", "重新选择 API"))
        elif api_doc.get("project_id", "default") != project_id:
            issues.append(_issue("error", "target_cross_project", "target_id", "API 不属于当前项目", "选择当前项目 API"))
    elif target_id and target_type == "scenario":
        scenario = await db["scenarios"].find_one({"id": target_id}, {"_id": 0, "project_id": 1, "steps": 1})
        if not scenario:
            issues.append(_issue("error", "target_not_found", "target_id", "场景不存在", "重新选择场景"))
        elif scenario.get("project_id", "default") != project_id:
            issues.append(_issue("error", "target_cross_project", "target_id", "场景不属于当前项目", "选择当前项目场景"))
        else:
            api_ids = [s.get("api_id") for s in scenario.get("steps", []) if s.get("api_id")]
            if api_ids:
                count = await db["api_dsls"].count_documents({"id": {"$in": api_ids}, "project_id": project_id})
                if count != len(set(api_ids)):
                    issues.append(_issue("error", "scenario_api_cross_project", "target_id", "场景包含跨项目或不存在的 API", "修复场景步骤 API"))
    elif target_id and target_type == "data_factory":
        tmpl = await db["data_templates"].find_one({"id": target_id}, {"_id": 0, "project_id": 1})
        if not tmpl:
            issues.append(_issue("error", "target_not_found", "target_id", "数据模板不存在", "重新选择数据模板"))
        elif tmpl.get("project_id", "default") != project_id:
            issues.append(_issue("error", "target_cross_project", "target_id", "数据模板不属于当前项目", "选择当前项目模板"))

    interval_s = _parse_interval_seconds(payload.get("interval", ""))
    if not payload.get("cron") and (interval_s is None or interval_s < 30):
        issues.append(_issue("error", "invalid_interval", "interval", "巡检间隔必须不少于 30 秒", "选择有效间隔"))
    if payload.get("cron"):
        try:
            parts = str(payload["cron"]).split()
            if len(parts) != 5:
                raise ValueError("cron must contain 5 fields")
            CronTrigger(
                minute=parts[0], hour=parts[1], day=parts[2],
                month=parts[3], day_of_week=parts[4], timezone="Asia/Shanghai",
            )
        except Exception:
            logger.warning("Invalid cron expression: %r", payload.get("cron"))
            issues.append(_issue("error", "invalid_cron", "cron", "cron 表达式无效", "修正为 5 段 cron 表达式"))

    env_id = payload.get("environment_id") or ""
    if env_id:
        env = await db["environments"].find_one({"id": env_id}, {"_id": 0, "project_id": 1})
        if not env:
            issues.append(_issue("error", "environment_not_found", "environment_id", "执行环境不存在", "重新选择环境"))
        elif env.get("project_id", "default") != project_id:
            issues.append(_issue("error", "environment_cross_project", "environment_id", "执行环境不属于当前项目", "选择当前项目环境"))

    channels = payload.get("alert_channels") or []
    if channels:
        channel_docs = await db["alert_channels"].find(
            {"$or": [{"id": {"$in": channels}}, {"url": {"$in": channels}}]},
            {"_id": 0, "id": 1, "url": 1, "project_id": 1},
        ).to_list(len(channels))
        known = {d.get("id") for d in channel_docs} | {d.get("url") for d in channel_docs}
        for ch in channels:
            if ch in known:
                doc = next((d for d in channel_docs if d.get("id") == ch or d.get("url") == ch), None)
                if doc and doc.get("project_id", "default") != project_id:
                    issues.append(_issue("error", "channel_cross_project", "alert_channels", "告警渠道不属于当前项目", "移除跨项目渠道"))
            elif str(ch).startswith("http"):
                issues.append(_issue("warning", "manual_webhook", "alert_channels", "手动 webhook 无法校验项目归属", "建议使用已保存渠道"))
            else:
                issues.append(_issue("error", "channel_not_found", "alert_channels", "告警渠道不存在", "重新选择渠道"))

    for idx, rule in enumerate(payload.get("asserts") or []):
        if not rule.get("field"):
            issues.append(_issue("error", "assert_missing_field", f"asserts.{idx}.field", "断言字段不能为空", "填写断言字段"))
        if rule.get("operator") not in _ASSERT_OPERATORS:
            issues.append(_issue("error", "assert_invalid_operator", f"asserts.{idx}.operator", "断言操作符无效", "选择支持的操作符"))

    duplicate_q = {
        "project_id": project_id,
        "target_type": target_type,
        "target_id": target_id,
        "id": {"$ne": monitor_id},
    }
    if target_id and await db["monitors"].count_documents(duplicate_q) > 0:
        issues.append(_issue("warning", "duplicate_coverage", "target_id", "该目标已有巡检监控", "确认是否需要重复覆盖"))

    return issues


def _raise_if_invalid(issues: list[dict[str, str]]) -> None:
    if any(i.get("level") == "error" for i in issues):
        raise HTTPException(422, {"valid": False, "issues": issues})


async def _broadcast_monitor(project_id: str, payload: dict[str, Any]) -> None:
    ws = getattr(_state, "_ws", None)
    if ws:
        await ws.broadcast(f"monitor:events:{project_id}", {"project_id": project_id, **payload})


@router.post("/monitors", status_code=201)
async def create_monitor(
    monitor: MonitorDSL = Body(...),
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_permission("monitor:create")),
    audit_service: AuditService = Depends(get_audit_service),
):
    if not _state._monitor_service:
        raise HTTPException(503, "Monitor service not ready")
    project_id = visible_project_id(current_user, monitor.project_id)
    payload = _normalize_monitor_payload(monitor.model_dump(), project_id, current_user.get("username", ""))
    issues = await _validate_monitor_payload(db, payload, project_id)
    _raise_if_invalid(issues)
    now = _now()
    monitor = MonitorDSL(**{
        **payload,
        "id": str(uuid.uuid4()),
        "created_at": now,
        "updated_at": now,
    })
    mid = await _state._monitor_service.register_monitor(monitor)
    await audit_service.log_action(
        user=current_user, action=AuditAction.CREATE,
        resource=AuditResource.MONITOR, resource_id=mid, resource_name=monitor.name,
        ip=_get_client_ip(request),
    )
    return {"monitor_id": mid, "project_id": project_id, "issues": issues}


@router.get("/monitors")
async def list_monitors(
    project_id: str | None = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_permission("monitor:read")),
):
    project_id = visible_project_id(current_user, project_id)
    return await db["monitors"].find({"project_id": project_id}, {"_id": 0}).sort("updated_at", -1).to_list(500)


@router.get("/monitors/{monitor_id}")
async def get_monitor(
    monitor_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_permission("monitor:read")),
):
    return await _load_monitor_or_404(db, monitor_id, current_user)


@router.put("/monitors/{monitor_id}")
async def update_monitor(
    monitor_id: str,
    data: dict[str, Any] = Body(...),
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_permission("monitor:update")),
    audit_service: AuditService = Depends(get_audit_service),
):
    old = await _load_monitor_or_404(db, monitor_id, current_user)
    project_id = old.get("project_id", "default")
    payload = _normalize_monitor_payload({**old, **data}, project_id, current_user.get("username", ""))
    issues = await _validate_monitor_payload(db, payload, project_id, monitor_id=monitor_id)
    _raise_if_invalid(issues)
    payload.pop("created_at", None)
    payload["updated_at"] = _now()
    r = await db["monitors"].update_one({"id": monitor_id, "project_id": project_id}, {"$set": payload})
    if not r.matched_count:
        raise HTTPException(404, "Monitor not found")
    await audit_service.log_action(
        user=current_user, action=AuditAction.UPDATE,
        resource=AuditResource.MONITOR, resource_id=monitor_id, resource_name=payload.get("name", monitor_id),
        ip=_get_client_ip(request),
    )
    if _state._monitor_service:
        doc = await db["monitors"].find_one({"id": monitor_id})
        if doc and doc.get("enabled", False):
            _state._monitor_service._add_job(MonitorDSL(**doc))
        else:
            try:
                _state._monitor_service._scheduler.remove_job(f"monitor_{monitor_id}")
            except Exception:
                logger.warning("Failed to remove scheduler job for monitor {}", monitor_id)
    return {"updated": True, "issues": issues}


@router.delete("/monitors/{monitor_id}")
async def delete_monitor(
    monitor_id: str,
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_permission("monitor:delete")),
    audit_service: AuditService = Depends(get_audit_service),
):
    doc = await _load_monitor_or_404(db, monitor_id, current_user)
    if _state._monitor_service:
        try:
            _state._monitor_service._scheduler.remove_job(f"monitor_{monitor_id}")
        except Exception:
            logger.warning("Failed to remove scheduler job for monitor {}", monitor_id)
    r = await db["monitors"].delete_one({"id": monitor_id, "project_id": doc.get("project_id")})
    if not r.deleted_count:
        raise HTTPException(404, "Monitor not found")
    await audit_service.log_action(
        user=current_user, action=AuditAction.DELETE,
        resource=AuditResource.MONITOR, resource_id=monitor_id, resource_name=doc.get("name", monitor_id),
        ip=_get_client_ip(request),
    )
    return {"deleted": True}


@router.post("/monitors/{monitor_id}/toggle")
async def toggle_monitor(
    monitor_id: str,
    body: dict[str, Any] = Body(...),
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_permission("monitor:update")),
    audit_service: AuditService = Depends(get_audit_service),
):
    enabled = bool(body.get("enabled", True))
    doc = await _load_monitor_or_404(db, monitor_id, current_user)
    await db["monitors"].update_one(
        {"id": monitor_id, "project_id": doc.get("project_id")},
        {"$set": {"enabled": enabled, "updated_at": _now(), "updated_by": current_user.get("username", "")}},
    )
    await audit_service.log_action(
        user=current_user, action=AuditAction.TOGGLE,
        resource=AuditResource.MONITOR, resource_id=monitor_id, resource_name=doc.get("name", monitor_id),
        ip=_get_client_ip(request), extra={"enabled": enabled},
    )
    if _state._monitor_service:
        monitor = MonitorDSL(**{**doc, "enabled": enabled})
        if enabled:
            _state._monitor_service._add_job(monitor)
        else:
            try:
                _state._monitor_service._scheduler.remove_job(f"monitor_{monitor_id}")
            except Exception:
                logger.warning("Failed to remove scheduler job for monitor {}", monitor_id)
    return {"monitor_id": monitor_id, "enabled": enabled, "project_id": doc.get("project_id")}


@router.get("/monitors/{monitor_id}/stats")
async def monitor_stats(
    monitor_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_permission("monitor:read")),
):
    if not _state._monitor_service:
        raise HTTPException(503, "Monitor service not ready")
    await _load_monitor_or_404(db, monitor_id, current_user)
    return await _state._monitor_service.get_monitor_stats(monitor_id)


@router.post("/monitors/validate")
async def validate_monitor_draft(
    data: dict[str, Any] = Body(...),
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_permission("monitor:read")),
):
    project_id = visible_project_id(current_user, data.get("project_id"))
    payload = _normalize_monitor_payload(data, project_id, current_user.get("username", ""))
    issues = await _validate_monitor_payload(db, payload, project_id, monitor_id=data.get("id", ""))
    return {"valid": not any(i.get("level") == "error" for i in issues), "issues": issues}


@router.get("/monitors/{monitor_id}/validate")
async def validate_monitor_existing(
    monitor_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_permission("monitor:read")),
):
    doc = await _load_monitor_or_404(db, monitor_id, current_user)
    issues = await _validate_monitor_payload(db, doc, doc.get("project_id", "default"), monitor_id=monitor_id)
    return {"valid": not any(i.get("level") == "error" for i in issues), "issues": issues}


@router.post("/monitors/generate")
async def generate_monitors(
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncIOMotorDatabase = Depends(get_db),
    redis=Depends(get_redis),
    current_user: dict = Depends(require_permission("monitor:create")),
):
    project_id = visible_project_id(current_user, body.get("project_id"))
    target_type = body.get("target_type") or ""
    target_ids = [str(x) for x in (body.get("target_ids") or []) if x]
    job_id = f"monitor:{uuid.uuid4().hex[:12]}"
    if target_type and target_type not in _VALID_TARGET_TYPES:
        raise HTTPException(422, "invalid target_type")
    if target_ids:
        for tid in target_ids:
            issues = await _validate_monitor_payload(db, {"target_type": target_type or "api", "target_id": tid, "interval": "5m"}, project_id)
            _raise_if_invalid(issues)
    task = {
        "project_id": project_id,
        "target_type": target_type,
        "target_ids": target_ids,
        "goal": body.get("goal", ""),
        "risk_preference": body.get("risk_preference", ""),
        "schedule_preference": body.get("schedule_preference", ""),
        "job_id": job_id,
        "user_id": current_user.get("user_id", ""),
        "fail_count": 0,
    }
    await redis.rpush(MONITOR_GENERATE_QUEUE, json.dumps(task, ensure_ascii=False))
    await AiJobService(db).mark_queued(
        job_id=job_id,
        type="monitor",
        project_id=project_id,
        source="monitor",
        target_ids=target_ids,
        queue_key=MONITOR_GENERATE_QUEUE,
        payload=task,
        user_id=current_user.get("user_id", ""),
    )
    await _broadcast_monitor(project_id, {"type": "monitor_generation", "job_id": job_id, "status": "queued", "target_ids": target_ids})
    return {"queued": True, "job_id": job_id, "project_id": project_id, "status": "queued"}


@router.post("/monitors/{monitor_id}/run-now")
async def run_monitor_now(
    monitor_id: str,
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_permission("monitor:update")),
    audit_service: AuditService = Depends(get_audit_service),
):
    if not _state._monitor_service:
        raise HTTPException(503, "Monitor service not ready")
    doc = await _load_monitor_or_404(db, monitor_id, current_user)
    issues = await _validate_monitor_payload(db, doc, doc.get("project_id", "default"), monitor_id=monitor_id)
    _raise_if_invalid(issues)
    job_id = f"monitor-run:{uuid.uuid4().hex[:12]}"
    project_id = doc.get("project_id", "default")
    _RUN_JOBS[job_id] = {
        "job_id": job_id, "monitor_id": monitor_id, "project_id": project_id,
        "status": "queued", "created_at": _now(), "execution": None, "alert": None, "error": "",
    }

    async def _run() -> None:
        try:
            _RUN_JOBS[job_id]["status"] = "running"
            await _broadcast_monitor(project_id, {"type": "monitor_run", "job_id": job_id, "monitor_id": monitor_id, "status": "running"})
            await _state._monitor_service._run_monitor(monitor_id)
            query = {"trigger": "monitor", "project_id": project_id}
            target_type = doc.get("target_type") or "api"
            if target_type == "scenario":
                query.update({"type": "scenario", "scenario_id": doc.get("target_id", "")})
            elif target_type == "data_factory":
                query.update({"type": "data_factory", "api_id": doc.get("target_id", "")})
            else:
                query.update({"type": "single", "api_id": doc.get("target_id") or doc.get("api_id", "")})
            latest = await _state._monitor_service._exec_col.find_one(query, {"_id": 0}, sort=[("started_at", -1)])
            alert = await db["alert_records"].find_one({"monitor_id": monitor_id, "project_id": project_id}, {"_id": 0}, sort=[("sent_at", -1)])
            status = "passed" if latest and latest.get("passed") else "failed"
            _RUN_JOBS[job_id].update({"status": status, "execution": latest, "alert": alert, "updated_at": _now()})
            await _broadcast_monitor(project_id, {
                "type": "monitor_run",
                "job_id": job_id,
                "monitor_id": monitor_id,
                "execution_id": (latest or {}).get("id", ""),
                "alert_id": (alert or {}).get("id", ""),
                "status": status,
            })
        except Exception as e:
            _RUN_JOBS[job_id].update({"status": "failed", "error": str(e), "updated_at": _now()})
            await _broadcast_monitor(project_id, {"type": "monitor_run", "job_id": job_id, "monitor_id": monitor_id, "status": "failed", "error": str(e)})

    asyncio.create_task(_run())
    await audit_service.log_action(
        user=current_user, action=AuditAction.UPDATE,
        resource=AuditResource.MONITOR, resource_id=monitor_id,
        resource_name=doc.get("name", monitor_id),
        ip=_get_client_ip(request), extra={"action": "run_now", "job_id": job_id},
    )
    return {"job_id": job_id, "monitor_id": monitor_id, "project_id": project_id, "status": "queued"}


@router.get("/monitors/jobs/{job_id}")
async def get_monitor_job(
    job_id: str,
    current_user: dict = Depends(require_permission("monitor:read")),
):
    job = _RUN_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Monitor job not found")
    ensure_project_access(current_user, job.get("project_id"))
    return job
