"""
死信队列 (DLQ) 与 AI Job 管理路由。

P0 生产落地：集中维护 AI 队列注册表，通用 list/retry/remove 覆盖所有 AI
相关 worker，同时保留旧 /ai/dlq 与 /ai/scenario-dlq 接口，避免现有页面断连。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any

from loguru import logger
from fastapi import APIRouter, Depends, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import get_current_user, user_has_permission, visible_project_id
from config.database import get_db, get_redis
from services.ai_job_service import AiJobService

# 使用 import api.state 而非 from import，确保读取到 startup() 后注入的实例
import api.state as _state

router = APIRouter(tags=["AI"])


QUEUE_DEFS: dict[str, dict[str, str]] = {
    "ai_analyze": {
        "id": "ai_analyze",
        "label": "AI Analysis",
        "queue_key": "queue:ai_analyze",
        "dlq_key": "queue:ai_analyze:dlq",
    },
    "ai_analyze_doc": {
        "id": "ai_analyze_doc",
        "label": "Doc Generation",
        "queue_key": "queue:ai_analyze_doc",
        "dlq_key": "queue:ai_analyze:dlq",
    },
    "ai_analyze_asserts": {
        "id": "ai_analyze_asserts",
        "label": "Assert Generation",
        "queue_key": "queue:ai_analyze_asserts",
        "dlq_key": "queue:ai_analyze:dlq",
    },
    "ai_scenario": {
        "id": "ai_scenario",
        "label": "Scenario Generation",
        "queue_key": "queue:ai_scenario",
        "dlq_key": "queue:ai_scenario:dlq",
    },
    "data_template": {
        "id": "data_template",
        "label": "Data Template",
        "queue_key": "queue:data_template",
        "dlq_key": "queue:data_template:dlq",
    },
    "ai_monitor": {
        "id": "ai_monitor",
        "label": "Monitor Generation",
        "queue_key": "queue:ai_monitor",
        "dlq_key": "queue:ai_monitor:dlq",
    },
    "diff_evaluate": {
        "id": "diff_evaluate",
        "label": "Diff Evaluation",
        "queue_key": "queue:diff_evaluate",
        "dlq_key": "queue:diff_evaluate:dlq",
    },
    "diagnose_failure": {
        "id": "diagnose_failure",
        "label": "Failure Diagnosis",
        "queue_key": "queue:diagnose_failure",
        "dlq_key": "queue:diagnose_failure:dlq",
    },
    "alert_analyze": {
        "id": "alert_analyze",
        "label": "Alert Analysis",
        "queue_key": "queue:alert_analyze",
        "dlq_key": "queue:alert_analyze:dlq",
    },
}


def _now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None).isoformat()


def _decode_json(raw: bytes | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {"raw": data}
    except Exception:
        logger.warning("Failed to decode JSON payload from queue")
        return {"raw": text}


def _job_id_for_payload(payload: dict[str, Any]) -> str:
    """从不同 worker payload 中稳定推导 job_id，供前端统一追踪。"""
    return (
        str(payload.get("job_id") or "")
        or (f"api:{payload['api_id']}" if payload.get("api_id") else "")
        or (f"diff:{payload['diff_id']}" if payload.get("diff_id") else "")
        or (f"diagnose:{payload['execution_id']}" if payload.get("execution_id") else "")
        or (f"alert:{payload['alert_id']}" if payload.get("alert_id") else "")
        or (f"data_template:{payload['template_id']}" if payload.get("template_id") else "")
        or ""
    )


def _payload_matches_queue(payload: dict[str, Any], queue_id: str) -> bool:
    """共享 DLQ（doc/asserts/analyze）按 target_queue 精准归属，旧 payload 默认归 ai_analyze。"""
    qdef = QUEUE_DEFS[queue_id]
    target_queue = payload.get("target_queue")
    if qdef["dlq_key"] != "queue:ai_analyze:dlq":
        return True
    if queue_id == "ai_analyze":
        return not target_queue or target_queue == qdef["queue_key"]
    return target_queue == qdef["queue_key"]


async def _list_dlq_items(redis, queue_id: str) -> list[dict[str, Any]]:
    if queue_id not in QUEUE_DEFS:
        raise HTTPException(404, f"Unknown queue: {queue_id}")
    qdef = QUEUE_DEFS[queue_id]
    raw_items = await redis.lrange(qdef["dlq_key"], 0, -1)
    items: list[dict[str, Any]] = []
    for raw_index, raw in enumerate(raw_items):
        payload = _decode_json(raw)
        if not _payload_matches_queue(payload, queue_id):
            continue
        items.append({
            "index": len(items),
            "raw_index": raw_index,
            "queue": queue_id,
            "queue_key": qdef["queue_key"],
            "dlq_key": qdef["dlq_key"],
            "job_id": _job_id_for_payload(payload),
            **payload,
        })
    return items


async def _remove_raw_dlq_item(redis, dlq_key: str, raw_index: int) -> dict[str, Any] | None:
    raw_items = await redis.lrange(dlq_key, 0, -1)
    if raw_index < 0 or raw_index >= len(raw_items):
        return None
    payload = _decode_json(raw_items[raw_index])
    placeholder = f"__DELETED__:{dlq_key}:{raw_index}:{_now_iso()}"
    await redis.lset(dlq_key, raw_index, placeholder)
    await redis.lrem(dlq_key, 1, placeholder)
    return payload


async def _retry_dlq_item(redis, queue_id: str, index: int) -> bool:
    items = await _list_dlq_items(redis, queue_id)
    if index < 0 or index >= len(items):
        return False
    item = items[index]
    payload = await _remove_raw_dlq_item(redis, item["dlq_key"], item["raw_index"])
    if payload is None:
        return False
    qdef = QUEUE_DEFS[queue_id]
    retry_queue = payload.pop("target_queue", None) or qdef["queue_key"]
    payload["fail_count"] = 0
    payload["retried_at"] = _now_iso()
    await redis.rpush(retry_queue, json.dumps(payload, ensure_ascii=False, default=str))
    return True


async def _remove_dlq_item(redis, queue_id: str, index: int) -> bool:
    items = await _list_dlq_items(redis, queue_id)
    if index < 0 or index >= len(items):
        return False
    item = items[index]
    removed = await _remove_raw_dlq_item(redis, item["dlq_key"], item["raw_index"])
    return removed is not None


async def get_queue_summary(redis) -> dict[str, Any]:
    """系统面板复用的队列摘要。"""
    queues: dict[str, Any] = {}
    dlq_seen: dict[str, Any] = {}
    for queue_id, qdef in QUEUE_DEFS.items():
        try:
            pending = await redis.llen(qdef["queue_key"])
            dlq_items = await _list_dlq_items(redis, queue_id)
            queues[qdef["queue_key"]] = {
                "id": queue_id,
                "label": qdef["label"],
                "pending": pending,
                "dlq": len(dlq_items),
                "dlq_key": qdef["dlq_key"],
                "recent_dlq": dlq_items[:3],
            }
        except Exception as e:
            queues[qdef["queue_key"]] = {
                "id": queue_id,
                "label": qdef["label"],
                "pending": None,
                "dlq": None,
                "error": str(e)[:120],
            }
        if qdef["dlq_key"] not in dlq_seen:
            try:
                recent = await redis.lrange(qdef["dlq_key"], 0, 2)
                dlq_seen[qdef["dlq_key"]] = {
                    "total": await redis.llen(qdef["dlq_key"]),
                    "recent": [
                        r.decode() if isinstance(r, bytes) else str(r)
                        for r in recent
                    ],
                }
            except Exception as e:
                dlq_seen[qdef["dlq_key"]] = {"total": None, "error": str(e)[:120]}
    return {
        "definitions": list(QUEUE_DEFS.values()),
        "queues": queues,
        "dlq": dlq_seen,
    }


@router.get("/ai/dlq")
async def list_dlq():
    """兼容旧入口：列出 ai_analyze DLQ，同时沿用通用 DLQ 的字段结构。"""
    redis = await get_redis()
    items = await _list_dlq_items(redis, "ai_analyze")
    return {"items": items, "total": len(items), "definitions": list(QUEUE_DEFS.values())}


@router.post("/ai/dlq/{index}/retry")
async def retry_dlq(index: int):
    """兼容旧入口：从 ai_analyze DLQ 重新入队。"""
    redis = await get_redis()
    ok = await _retry_dlq_item(redis, "ai_analyze", index)
    if not ok:
        raise HTTPException(404, "DLQ index out of range")
    return {"retried": True}


@router.delete("/ai/dlq/{index}")
async def remove_dlq(index: int):
    """兼容旧入口：从 ai_analyze DLQ 删除任务。"""
    redis = await get_redis()
    ok = await _remove_dlq_item(redis, "ai_analyze", index)
    if not ok:
        raise HTTPException(404, "DLQ index out of range")
    return {"deleted": True}


@router.get("/ai/scenario-dlq")
async def list_scenario_dlq():
    """兼容旧入口：列出场景死信队列。"""
    redis = await get_redis()
    items = await _list_dlq_items(redis, "ai_scenario")
    return {"items": items, "total": len(items), "definitions": list(QUEUE_DEFS.values())}


@router.post("/ai/scenario-dlq/{index}/retry")
async def retry_scenario_dlq(index: int):
    """兼容旧入口：从场景死信队列重新入队。"""
    redis = await get_redis()
    ok = await _retry_dlq_item(redis, "ai_scenario", index)
    if not ok:
        raise HTTPException(404, "Scenario DLQ index out of range")
    return {"retried": True}


@router.delete("/ai/scenario-dlq/{index}")
async def remove_scenario_dlq(index: int):
    """兼容旧入口：从场景死信队列删除任务。"""
    redis = await get_redis()
    ok = await _remove_dlq_item(redis, "ai_scenario", index)
    if not ok:
        raise HTTPException(404, "Scenario DLQ index out of range")
    return {"deleted": True}


@router.get("/ai/dlq/{queue}")
async def list_generic_dlq(
    queue: str,
    current_user: dict = Depends(get_current_user),
):
    """按队列列出 DLQ，覆盖所有 AI worker。"""
    if not user_has_permission(current_user, "stats:read"):
        raise HTTPException(403, "权限不足")
    redis = await get_redis()
    items = await _list_dlq_items(redis, queue)
    return {
        "queue": queue,
        "items": items,
        "total": len(items),
        "definitions": list(QUEUE_DEFS.values()),
    }


@router.post("/ai/dlq/{queue}/{index}/retry")
async def retry_generic_dlq(
    queue: str,
    index: int,
    current_user: dict = Depends(get_current_user),
):
    """从任意 AI DLQ 重新入队。"""
    if not user_has_permission(current_user, "dlq:manage"):
        raise HTTPException(403, "权限不足")
    redis = await get_redis()
    ok = await _retry_dlq_item(redis, queue, index)
    if not ok:
        raise HTTPException(404, "DLQ index out of range")
    return {"retried": True, "queue": queue, "index": index}


@router.delete("/ai/dlq/{queue}/{index}")
async def remove_generic_dlq(
    queue: str,
    index: int,
    current_user: dict = Depends(get_current_user),
):
    """删除任意 AI DLQ 条目。"""
    if not user_has_permission(current_user, "dlq:manage"):
        raise HTTPException(403, "权限不足")
    redis = await get_redis()
    ok = await _remove_dlq_item(redis, queue, index)
    if not ok:
        raise HTTPException(404, "DLQ index out of range")
    return {"deleted": True, "queue": queue, "index": index}


async def _queued_jobs(redis, project_id: str, limit: int) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for queue_id, qdef in QUEUE_DEFS.items():
        try:
            raw_items = await redis.lrange(qdef["queue_key"], 0, max(limit - 1, 0))
        except Exception:
            logger.warning("Failed to read Redis queue key=%s", qdef["queue_key"])
            raw_items = []
        for raw in raw_items:
            payload = _decode_json(raw)
            if payload.get("project_id") and payload.get("project_id") != project_id:
                continue
            jobs.append({
                "job_id": _job_id_for_payload(payload),
                "queue": queue_id,
                "queue_key": qdef["queue_key"],
                "status": payload.get("status") or "queued",
                "project_id": payload.get("project_id", project_id),
                "target_ids": payload.get("api_ids") or [
                    payload.get(k)
                    for k in ("api_id", "template_id", "diff_id", "execution_id", "alert_id")
                    if payload.get(k)
                ],
                "retry_count": payload.get("fail_count", 0),
                "error": payload.get("error", ""),
                "payload": payload,
            })
            if len(jobs) >= limit:
                return jobs
    return jobs


async def _dlq_jobs(redis, project_id: str, limit: int) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for queue_id, qdef in QUEUE_DEFS.items():
        for item in await _list_dlq_items(redis, queue_id):
            if item.get("project_id") and item.get("project_id") != project_id:
                continue
            jobs.append({
                "job_id": item.get("job_id"),
                "queue": queue_id,
                "queue_key": qdef["queue_key"],
                "status": "dlq",
                "project_id": item.get("project_id", project_id),
                "target_ids": item.get("api_ids") or [
                    item.get(k)
                    for k in ("api_id", "template_id", "diff_id", "execution_id", "alert_id")
                    if item.get(k)
                ],
                "retry_count": item.get("fail_count", 0),
                "error": item.get("error", ""),
                "dlq_index": item.get("index"),
                "payload": item,
            })
            if len(jobs) >= limit:
                return jobs
    return jobs


async def _generation_jobs(db: AsyncIOMotorDatabase, project_id: str, limit: int) -> list[dict[str, Any]]:
    cursor = db["generation_versions"].find(
        {"project_id": project_id, "job_id": {"$ne": ""}},
        {"content": 0, "prompt": 0},
    ).sort("created_at", -1).limit(limit)
    rows = await cursor.to_list(limit)
    jobs = []
    for doc in rows:
        generation_id = str(doc.get("_id") or doc.get("id") or "")
        jobs.append({
            "job_id": doc.get("job_id", ""),
            "queue": doc.get("type", ""),
            "status": doc.get("status", "pending_review"),
            "project_id": project_id,
            "target_ids": doc.get("api_ids") or ([doc.get("api_id")] if doc.get("api_id") else []),
            "generation_ids": [generation_id],
            "summary": doc.get("summary", ""),
            "created_at": doc.get("created_at"),
            "finished_at": doc.get("created_at"),
            "source": doc.get("source", ""),
        })
    return jobs


async def _persistent_jobs(db: AsyncIOMotorDatabase, project_id: str, limit: int) -> list[dict[str, Any]]:
    """读取 ai_jobs 持久化集合，作为统一 AI Job 视图的第一来源。"""
    cursor = db["ai_jobs"].find(
        {"project_id": project_id},
        {"_id": 0},
    ).sort("updated_at", -1).limit(limit)
    rows = await cursor.to_list(limit)
    jobs: list[dict[str, Any]] = []
    for doc in rows:
        jobs.append({
            "job_id": doc.get("job_id", ""),
            "queue": doc.get("type", ""),
            "queue_key": doc.get("queue_key", ""),
            "status": doc.get("status", "queued"),
            "project_id": doc.get("project_id", project_id),
            "target_ids": doc.get("target_ids", []),
            "retry_count": doc.get("retry_count", 0),
            "error": doc.get("error", ""),
            "generation_ids": doc.get("generation_ids", []),
            "source": doc.get("source", ""),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
            "finished_at": doc.get("finished_at"),
            "payload": doc.get("payload", {}),
        })
    return jobs


def _merge_jobs(*groups: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """按 job_id 去重合并；持久化记录优先，旧 Redis/Generation 聚合做兜底。"""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for job in group:
            job_id = job.get("job_id")
            if job_id and job_id in seen:
                continue
            if job_id:
                seen.add(job_id)
            merged.append(job)
            if len(merged) >= limit:
                return merged
    return merged


@router.get("/ai/jobs")
async def list_ai_jobs(
    project_id: str = Query(default="default"),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """统一 AI Job 视图：聚合 queued / dlq / generation pending_review。"""
    if not user_has_permission(current_user, "generation:read"):
        raise HTTPException(403, "权限不足")
    project_id = visible_project_id(current_user, project_id)
    redis = await get_redis()
    persisted = await _persistent_jobs(db, project_id, limit)
    queued = await _queued_jobs(redis, project_id, limit)
    dlq = await _dlq_jobs(redis, project_id, limit)
    generations = await _generation_jobs(db, project_id, limit)
    jobs = _merge_jobs(persisted, queued, dlq, generations, limit=limit)
    if status:
        jobs = [j for j in jobs if j.get("status") == status]
    jobs = jobs[:limit]
    return {"items": jobs, "total": len(jobs), "definitions": list(QUEUE_DEFS.values())}


@router.get("/ai/jobs/{job_id}")
async def get_ai_job(
    job_id: str,
    project_id: str = Query(default="default"),
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """查询单个 AI job 的可见状态。"""
    project_id = visible_project_id(current_user, project_id)
    persisted = await db["ai_jobs"].find_one({"job_id": job_id, "project_id": project_id}, {"_id": 0})
    if persisted:
        return {
            "job_id": persisted.get("job_id", ""),
            "queue": persisted.get("type", ""),
            "queue_key": persisted.get("queue_key", ""),
            "status": persisted.get("status", "queued"),
            "project_id": persisted.get("project_id", project_id),
            "target_ids": persisted.get("target_ids", []),
            "retry_count": persisted.get("retry_count", 0),
            "error": persisted.get("error", ""),
            "generation_ids": persisted.get("generation_ids", []),
            "source": persisted.get("source", ""),
            "created_at": persisted.get("created_at"),
            "updated_at": persisted.get("updated_at"),
            "finished_at": persisted.get("finished_at"),
            "payload": persisted.get("payload", {}),
        }
    rows = (await list_ai_jobs(project_id=project_id, status=None, limit=200, current_user=current_user, db=db))["items"]
    for job in rows:
        if job.get("job_id") == job_id:
            return job
    gen_doc = await db["generation_versions"].find_one({"job_id": job_id, "project_id": project_id})
    if gen_doc:
        gen_doc["id"] = str(gen_doc.pop("_id", gen_doc.get("id", "")))
        gen_doc.pop("content", None)
        gen_doc.pop("prompt", None)
        return gen_doc
    raise HTTPException(404, "AI job not found")


@router.post("/ai/jobs/{job_id}/retry")
async def retry_ai_job(
    job_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """按 job_id 从 DLQ 重试，供生产观测页一键恢复。"""
    if not user_has_permission(current_user, "dlq:manage"):
        raise HTTPException(403, "权限不足")
    redis = await get_redis()
    for queue_id in QUEUE_DEFS:
        items = await _list_dlq_items(redis, queue_id)
        for item in items:
            if item.get("job_id") == job_id:
                ok = await _retry_dlq_item(redis, queue_id, item["index"])
                if not ok:
                    raise HTTPException(404, "AI job DLQ item disappeared")
                await AiJobService(db).mark_retry(
                    job_id=job_id,
                    type=queue_id,
                    project_id=item.get("project_id", "default"),
                    source="dlq_retry",
                    target_ids=item.get("api_ids") or [
                        item.get(k)
                        for k in ("api_id", "template_id", "diff_id", "execution_id", "alert_id")
                        if item.get(k)
                    ],
                    queue_key=QUEUE_DEFS[queue_id]["queue_key"],
                    retry_count=0,
                    payload=item,
                )
                return {"retried": True, "job_id": job_id, "queue": queue_id}
    raise HTTPException(404, "AI job is not in DLQ")
