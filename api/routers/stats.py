"""
统计 & Dashboard 增强端点路由 —— 委托 services/stats_service.py
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db
from services.stats_service import StatsService
from api.deps import get_current_user, visible_project_id

router = APIRouter(tags=["Stats"])


# 依赖注入：每个请求创建新的 StatsService 实例
def get_stats_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> StatsService:
    return StatsService(db)


@router.get("/stats/overview")
async def stats_overview(
    project_id: str = "default",
    current_user: dict = Depends(get_current_user),
    service: StatsService = Depends(get_stats_service),
):
    project_id = visible_project_id(current_user, project_id)
    return await service.get_overview(project_id)


@router.get("/stats/workbench")
async def stats_workbench(
    project_id: str = "default",
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """质量工作台待办：把导入、审核、覆盖、监控和失败执行收敛成下一步动作。"""
    project_id = visible_project_id(current_user, project_id)
    api_col = db["api_dsls"]
    pending_generations = await db["generation_versions"].count_documents({
        "project_id": project_id,
        "status": "pending_review",
    })
    unanalysed = await api_col.count_documents({
        "project_id": project_id,
        "analysis_status": {"$in": ["idle", "queued", "running", "failed"]},
    })
    low_quality_docs = await api_col.find(
        {"project_id": project_id},
        {"_id": 0, "id": 1, "name": 1, "request": 1, "doc": 1, "asserts": 1, "analysis_status": 1},
    ).limit(200).to_list(200)
    low_quality = [
        {
            "id": d.get("id"),
            "title": d.get("name") or (d.get("request") or {}).get("path"),
            "reason": "missing_doc_or_business_asserts",
            "action": "review_or_analyze",
        }
        for d in low_quality_docs
        if not (d.get("doc") or {}).get("summary") or not [
            a for a in (d.get("asserts") or [])
            if (a.get("field") or "") not in ("status_code", "$response_time_ms")
        ]
    ][:20]

    scenario_api_ids = set()
    async for item in db["scenarios"].aggregate([
        {"$match": {"project_id": project_id}},
        {"$unwind": "$steps"},
        {"$group": {"_id": "$steps.api_id"}},
    ]):
        if item.get("_id"):
            scenario_api_ids.add(item["_id"])
    api_ids = [d.get("id") for d in await api_col.find({"project_id": project_id}, {"_id": 0, "id": 1}).to_list(1000)]
    no_scenario = [api_id for api_id in api_ids if api_id not in scenario_api_ids][:20]

    monitor_api_ids = set()
    async for m in db["monitors"].find({"project_id": project_id}, {"_id": 0, "api_id": 1, "target_id": 1}):
        if m.get("api_id"):
            monitor_api_ids.add(m["api_id"])
        if m.get("target_id"):
            monitor_api_ids.add(m["target_id"])
    no_monitor = [api_id for api_id in api_ids if api_id not in monitor_api_ids][:20]

    recent_failed = await db["executions"].find(
        {"project_id": project_id, "passed": False},
        {"_id": 0},
    ).sort("started_at", -1).limit(10).to_list(10)
    recent_alerts = await db["alert_records"].find(
        {"project_id": project_id, "is_recovery": {"$ne": True}},
        {"_id": 0},
    ).sort("sent_at", -1).limit(10).to_list(10)
    failed_ai_apis = await api_col.count_documents({
        "project_id": project_id,
        "analysis_status": "failed",
    })
    failed_diagnoses = await db["executions"].count_documents({
        "project_id": project_id,
        "diagnosis_status": "failed",
    })

    return {
        "project_id": project_id,
        "counts": {
            "unanalysed_apis": unanalysed,
            "pending_generations": pending_generations,
            "low_quality": len(low_quality),
            "no_scenario": len(no_scenario),
            "no_monitor": len(no_monitor),
            "recent_failed": len(recent_failed),
            "recent_alerts": len(recent_alerts),
            "failed_ai_tasks": failed_ai_apis,
            "failed_diagnoses": failed_diagnoses,
        },
        "tasks": [
            {"type": "unanalysed_apis", "count": unanalysed, "action": "/apis?analysis_status=idle"},
            {"type": "pending_generations", "count": pending_generations, "action": "/generations?status=pending_review"},
            {"type": "low_quality", "items": low_quality, "action": "/apis"},
            {"type": "no_scenario", "api_ids": no_scenario, "action": "/scenarios"},
            {"type": "no_monitor", "api_ids": no_monitor, "action": "/monitor"},
            {"type": "recent_failed", "items": recent_failed, "action": "/executions"},
            {"type": "recent_alerts", "items": recent_alerts, "action": "/monitor?tab=alerts"},
            {"type": "failed_ai_tasks", "count": failed_ai_apis, "action": "/apis?analysis_status=failed"},
            {"type": "failed_diagnoses", "count": failed_diagnoses, "action": "/executions?passed=false"},
        ],
    }


@router.get("/stats/api/{api_id}")
async def stats_api(
    api_id: str,
    limit: int = 50,
    service: StatsService = Depends(get_stats_service),
):
    return await service.get_api_stats(api_id, limit)


@router.get("/stats/scenario/{scenario_id}")
async def stats_scenario(
    scenario_id: str,
    limit: int = 20,
    service: StatsService = Depends(get_stats_service),
):
    return await service.get_scenario_stats(scenario_id, limit)


# ── Dashboard 增强统计端点 ──


@router.get("/stats/dashboard/top-failing")
async def stats_top_failing(
    project_id: str = "default",
    limit: int = Query(10, ge=1, le=50),
    hours: int = Query(24, ge=1, le=720),
    current_user: dict = Depends(get_current_user),
    service: StatsService = Depends(get_stats_service),
):
    """Top N 失败 API —— 聚合指定时间窗口内失败次数最多的 API"""
    project_id = visible_project_id(current_user, project_id)
    return await service.get_top_failing(project_id, limit, hours)


@router.get("/stats/dashboard/health-scores")
async def stats_health_scores(
    project_id: str = "default",
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
    service: StatsService = Depends(get_stats_service),
):
    """API 健康评分 —— 综合成功率 + 响应时间 + 最近活跃度，0-100 分"""
    project_id = visible_project_id(current_user, project_id)
    return await service.get_health_scores(project_id, limit)


@router.get("/stats/dashboard/trends")
async def stats_trends(
    project_id: str = "default",
    period: str = Query("24h", pattern="^(24h|7d|30d)$"),
    granularity: str = Query("hour", pattern="^(hour|day)$"),
    current_user: dict = Depends(get_current_user),
    service: StatsService = Depends(get_stats_service),
):
    """时间聚合趋势 —— 按时/天粒度统计执行通过率趋势"""
    project_id = visible_project_id(current_user, project_id)
    return await service.get_trends(project_id, period, granularity)


@router.get("/stats/dashboard/sla")
async def stats_sla(
    project_id: str = "default",
    period: str = Query("30d", pattern="^(7d|30d|90d)$"),
    current_user: dict = Depends(get_current_user),
    service: StatsService = Depends(get_stats_service),
):
    """SLA 报告 —— 按 API 计算可用性百分比"""
    project_id = visible_project_id(current_user, project_id)
    return await service.get_sla(project_id, period)


@router.get("/stats/dashboard/ai-quality")
async def stats_ai_quality(
    project_id: str = "default",
    period: str = Query("30d", pattern="^(7d|30d|90d)$"),
    current_user: dict = Depends(get_current_user),
    service: StatsService = Depends(get_stats_service),
):
    """AI 生成内容采纳统计 —— 按内容节点展示采纳/部分采纳/放弃/待审核数量。"""
    project_id = visible_project_id(current_user, project_id)
    return await service.get_ai_quality(project_id, period)


@router.get("/stats/dashboard/team-activity")
async def stats_team_activity(
    project_id: str = "default",
    days: int = Query(30, ge=7, le=90),
    current_user: dict = Depends(get_current_user),
    service: StatsService = Depends(get_stats_service),
):
    """团队活跃度 —— 按日聚合场景执行次数、AI分析次数、手动执行次数"""
    project_id = visible_project_id(current_user, project_id)
    return await service.get_team_activity(project_id, days)


@router.get("/stats/coverage")
async def stats_coverage(
    project_id: str = "default",
    current_user: dict = Depends(get_current_user),
    service: StatsService = Depends(get_stats_service),
):
    """覆盖度矩阵 —— 按模块聚合 5 维度测试覆盖率"""
    project_id = visible_project_id(current_user, project_id)
    return await service.get_coverage(project_id)
