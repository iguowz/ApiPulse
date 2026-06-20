"""
统计 & Dashboard 服务层 —— 聚合管线、健康评分、SLA 计算
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.settings import get_settings


def _generation_type_label(gen_type: str) -> str:
    labels = {
        "doc": "接口文档",
        "asserts": "断言规则",
        "scenario": "业务场景",
        "data_template": "数据模板",
        "monitor": "巡检配置",
        "chat_suggestion": "聊天建议",
    }
    return labels.get(gen_type, gen_type or "unknown")


class StatsService:
    """统计与仪表盘业务逻辑，不依赖 HTTP 层"""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    # ── 概览 ─────────────────────────────────────────────────

    async def get_overview(self, project_id: str = "default") -> dict[str, Any]:
        """项目全局概览：API/场景/执行/监控/告警/LLM 统计"""
        pq = {"project_id": project_id}

        # 并行计数：分 5 组执行以减少串行等待
        total_apis = await self.db["api_dsls"].count_documents(pq)
        status_done = await self.db["api_dsls"].count_documents({**pq, "analysis_status": {"$in": ["applied", "done"]}})
        status_run = await self.db["api_dsls"].count_documents({**pq, "analysis_status": "running"})
        status_queue = await self.db["api_dsls"].count_documents({**pq, "analysis_status": "queued"})
        status_fail = await self.db["api_dsls"].count_documents({**pq, "analysis_status": "failed"})
        status_idle = await self.db["api_dsls"].count_documents({**pq, "analysis_status": "idle"})
        total_scenarios = await self.db["scenarios"].count_documents(pq)
        ai_scenarios = await self.db["scenarios"].count_documents({**pq, "ai_generated": True})
        scenario_draft = await self.db["scenarios"].count_documents({**pq, "status": "draft"})
        scenario_ready = await self.db["scenarios"].count_documents({**pq, "status": "ready"})
        scenario_done = await self.db["scenarios"].count_documents({**pq, "status": "done"})
        scenario_failed = await self.db["scenarios"].count_documents({**pq, "status": "failed"})
        total_execs = await self.db["executions"].count_documents(pq)
        passed_execs = await self.db["executions"].count_documents({**pq, "passed": True})
        execs_single = await self.db["executions"].count_documents({**pq, "type": "single"})
        execs_scenario = await self.db["executions"].count_documents({**pq, "type": "scenario"})
        execs_monitor = await self.db["executions"].count_documents({**pq, "type": "monitor"})
        active_monitors = await self.db["monitors"].count_documents({**pq, "enabled": True})
        total_alerts = await self.db["alert_records"].count_documents(pq)

        # AI Jobs 聚合：按 type + status 分组统计（场景/巡检 AI 生成任务状态分布）
        ai_jobs_pipeline = [
            {"$match": {**pq, "type": {"$in": ["scenario", "monitor"]}}},
            {"$group": {
                "_id": {"type": "$type", "status": "$status"},
                "count": {"$sum": 1},
            }},
        ]
        ai_jobs_rows = await self.db["ai_jobs"].aggregate(ai_jobs_pipeline).to_list(100)
        # 构建嵌套结果 { scenario: { completed, running, queued, failed }, monitor: {...} }
        ai_jobs: dict[str, dict[str, int]] = {}
        for row in ai_jobs_rows:
            job_type = row["_id"]["type"]
            job_status = row["_id"]["status"]
            if job_type not in ai_jobs:
                ai_jobs[job_type] = {"completed": 0, "running": 0, "queued": 0, "failed": 0}
            # 将 MongoDB 状态映射为前端分组
            if job_status in ("done", "pending_review"):
                ai_jobs[job_type]["completed"] += row["count"]
            elif job_status in ("queued", "retry"):
                ai_jobs[job_type]["queued"] += row["count"]
            elif job_status == "running":
                ai_jobs[job_type]["running"] += row["count"]
            elif job_status in ("failed", "dlq"):
                ai_jobs[job_type]["failed"] += row["count"]
        # 确保返回的两种 type 都有默认值
        for t in ("scenario", "monitor"):
            ai_jobs.setdefault(t, {"completed": 0, "running": 0, "queued": 0, "failed": 0})

        # LLM 配置状态
        llm_config = await self.db["settings"].find_one({"key": "llm_config"})
        s = get_settings()
        llm_configured = bool((llm_config and llm_config.get("api_key")) or s.openai_api_key)
        llm_model = (llm_config.get("model") if llm_config else None) or s.openai_model

        return {
            "project_id": project_id,
            "apis": {
                "total": total_apis,
                "statuses": {
                    "done": status_done,
                    "running": status_run,
                    "queued": status_queue,
                    "failed": status_fail,
                    "idle": status_idle,
                },
            },
            "scenarios": {
                "total": total_scenarios,
                "ai_generated": ai_scenarios,
                "statuses": {
                    "draft": scenario_draft,
                    "ready": scenario_ready,
                    "done": scenario_done,
                    "failed": scenario_failed,
                },
            },
            "executions": {
                "total": total_execs,
                "passed": passed_execs,
                "failed": total_execs - passed_execs,
                "pass_rate_pct": round(passed_execs / total_execs * 100, 1) if total_execs else 0.0,
                "by_type": {
                    "single": execs_single,
                    "scenario": execs_scenario,
                    "monitor": execs_monitor,
                },
            },
            "monitors": {"active": active_monitors},
            "alerts": {"total": total_alerts},
            "ai_jobs": ai_jobs,
            "llm": {
                "model": llm_model,
                "configured": llm_configured,
            },
        }

    # ── 单 API 统计 ──────────────────────────────────────────

    async def get_api_stats(self, api_id: str, limit: int = 50) -> dict[str, Any]:
        """单个 API 的执行趋势统计"""
        docs = await self.db["executions"].find(
            {"api_id": api_id, "type": "single"},
            {"_id": 0, "passed": 1, "started_at": 1, "duration_ms": 1},
        ).sort("started_at", -1).limit(limit).to_list(limit)

        trend = [
            {"started_at": d["started_at"], "passed": d["passed"],
             "latency_ms": d.get("duration_ms", 0)}
            for d in reversed(docs)
        ]
        total = len(trend)
        passed = sum(1 for t in trend if t["passed"])
        avg_lat = round(sum(t["latency_ms"] for t in trend) / total, 1) if total else 0
        return {
            "api_id": api_id, "total": total, "passed": passed,
            "pass_rate_pct": round(passed / total * 100, 1) if total else 0,
            "avg_latency_ms": avg_lat, "trend": trend,
        }

    # ── 场景统计 ────────────────────────────────────────────

    async def get_scenario_stats(self, scenario_id: str, limit: int = 20) -> dict[str, Any]:
        """单个场景的执行统计"""
        docs = await self.db["executions"].find(
            {"scenario_id": scenario_id, "type": "scenario"},
            {"_id": 0, "passed": 1, "started_at": 1, "duration_ms": 1, "failure_reason": 1},
        ).sort("started_at", -1).limit(limit).to_list(limit)
        total = len(docs)
        passed = sum(1 for d in docs if d["passed"])
        return {
            "scenario_id": scenario_id, "total": total, "passed": passed,
            "pass_rate_pct": round(passed / total * 100, 1) if total else 0,
            "recent": list(reversed(docs)),
        }

    # ── Dashboard: Top N 失败 API ────────────────────────────

    async def get_top_failing(
        self, project_id: str = "default", limit: int = 10, hours: int = 24,
    ) -> dict[str, Any]:
        """聚合指定时间窗口内失败次数最多的 API"""
        since = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None) - timedelta(hours=hours)

        pipeline = [
            {"$match": {
                "project_id": project_id,
                "passed": False,
                "started_at": {"$gte": since},
            }},
            {"$group": {
                "_id": "$api_id",
                "fail_count": {"$sum": 1},
                "avg_duration_ms": {"$avg": "$duration_ms"},
            }},
            {"$sort": {"fail_count": -1}},
            {"$limit": limit},
        ]
        rows = await self.db["executions"].aggregate(pipeline).to_list(limit)

        # 收集所有 api_id，用 $in 一次查询补充 API 基础信息，避免 N+1 问题
        api_ids = []
        for r in rows:
            api_id = str(r["_id"]) if isinstance(r["_id"], ObjectId) else r["_id"]
            if api_id:
                api_ids.append(api_id)
        api_docs = {}
        if api_ids:
            cursor = self.db["api_dsls"].find({"id": {"$in": api_ids}}, {"_id": 0, "id": 1, "name": 1, "request.method": 1, "request.path": 1})
            async for doc in cursor:
                api_docs[doc["id"]] = doc

        result = []
        for r in rows:
            api_id = str(r["_id"]) if isinstance(r["_id"], ObjectId) else r["_id"]
            if not api_id:
                continue
            api_doc = api_docs.get(api_id)
            # method/path 存储在嵌套的 request 对象中（ApiDSL.request.method / ApiDSL.request.path）
            req = (api_doc.get("request") or {}) if api_doc else {}
            result.append({
                "api_id": api_id,
                "name": api_doc.get("name", "") if api_doc else "",
                "method": req.get("method", ""),
                "path": req.get("path", ""),
                "fail_count": r["fail_count"],
                "avg_duration_ms": round(r.get("avg_duration_ms", 0) or 0, 1),
            })

        return {"project_id": project_id, "hours": hours, "items": result}

    # ── Dashboard: 健康评分 ──────────────────────────────────

    async def get_health_scores(
        self, project_id: str = "default", limit: int = 50,
    ) -> dict[str, Any]:
        """综合成功率 + 响应时间 + 最近活跃度，计算每个 API 0-100 健康评分"""
        # 限制 500 条，防止项目 API 过多时内存溢出
        api_docs = await self.db["api_dsls"].find(
            {"project_id": project_id}, {"_id": 0, "id": 1, "name": 1, "request.method": 1, "request.path": 1},
        ).to_list(500)

        if not api_docs:
            return {"project_id": project_id, "items": []}

        # 获取全局平均响应时间作为基准线
        overall_avg_pipeline = [
            {"$match": {"project_id": project_id}},
            {"$group": {"_id": None, "avg_ms": {"$avg": "$duration_ms"}}},
        ]
        overall_row = await self.db["executions"].aggregate(overall_avg_pipeline).to_list(1)
        global_avg_ms = overall_row[0]["avg_ms"] if overall_row else 500

        scores = []
        for api in api_docs:
            api_id = api["id"]
            execs = await self.db["executions"].find(
                {"api_id": api_id, "type": "single", "project_id": project_id},
                {"_id": 0, "passed": 1, "duration_ms": 1},
            ).sort("started_at", -1).limit(50).to_list(50)

            total = len(execs)
            if total == 0:
                continue

            passed = sum(1 for e in execs if e.get("passed"))
            pass_rate = passed / total
            avg_ms = sum(e.get("duration_ms", 0) for e in execs) / total

            # 健康评分公式（0-100）：成功率60% + 响应时间30% + 活跃度10%
            pass_score = pass_rate * 60
            if avg_ms <= 0:
                latency_score = 30
            else:
                ratio = avg_ms / max(global_avg_ms, 1)
                latency_score = max(0, min(30, 30 * (3 - ratio) / 2))
            activity_score = min(10, total / 5)

            health = round(pass_score + latency_score + activity_score, 1)
            if health >= 80:
                grade = "excellent"
            elif health >= 60:
                grade = "good"
            elif health >= 40:
                grade = "fair"
            else:
                grade = "poor"

            # method/path 存储在嵌套的 request 对象中（ApiDSL.request.method / ApiDSL.request.path）
            req = api.get("request") or {}
            scores.append({
                "api_id": api_id,
                "name": api.get("name", ""),
                "method": req.get("method", ""),
                "path": req.get("path", ""),
                "health_score": health,
                "grade": grade,
                "pass_rate_pct": round(pass_rate * 100, 1),
                "avg_latency_ms": round(avg_ms, 1),
                "sample_count": total,
            })

        scores.sort(key=lambda x: x["health_score"])
        return {"project_id": project_id, "items": scores[:limit]}

    # ── Dashboard: 时间趋势 ─────────────────────────────────

    async def get_trends(
        self, project_id: str = "default", period: str = "24h", granularity: str = "hour",
    ) -> dict[str, Any]:
        """按时/天粒度统计执行通过率趋势"""
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        if period == "24h":
            since = now - timedelta(hours=24)
        elif period == "7d":
            since = now - timedelta(days=7)
        else:  # 30d
            since = now - timedelta(days=30)

        date_fmt = "%Y-%m-%dT%H" if granularity == "hour" else "%Y-%m-%d"

        pipeline = [
            {"$match": {
                "project_id": project_id,
                "started_at": {"$gte": since},
            }},
            {"$group": {
                "_id": {"$dateToString": {"format": date_fmt, "date": "$started_at"}},
                "total": {"$sum": 1},
                "passed": {"$sum": {"$cond": ["$passed", 1, 0]}},
            }},
            {"$sort": {"_id": 1}},
        ]
        rows = await self.db["executions"].aggregate(pipeline).to_list(1000)

        trend = []
        for r in rows:
            # bucket 是 $dateToString 产生的字符串，加 str() 防御 ObjectId 场景
            trend.append({
                "bucket": str(r["_id"]) if isinstance(r["_id"], ObjectId) else r["_id"],
                "total": r["total"],
                "passed": r["passed"],
                "failed": r["total"] - r["passed"],
                "pass_rate_pct": round(r["passed"] / r["total"] * 100, 1) if r["total"] else 0.0,
            })

        return {
            "project_id": project_id,
            "period": period,
            "granularity": granularity,
            "trend": trend,
        }

    # ── Dashboard: 团队活跃度 ───────────────────────────────

    async def get_team_activity(
        self, project_id: str = "default", days: int = 30,
    ) -> dict[str, Any]:
        """团队活跃度：按日聚合场景执行次数、AI分析次数、手动执行次数"""
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        since = now - timedelta(days=days)
        date_fmt = "%Y-%m-%d"

        # 按天聚合各类型执行次数
        exec_pipeline = [
            {"$match": {
                "project_id": project_id,
                "started_at": {"$gte": since},
            }},
            {"$group": {
                "_id": {
                    "date": {"$dateToString": {"format": date_fmt, "date": "$started_at"}},
                    "type": "$type",
                },
                "count": {"$sum": 1},
            }},
            {"$sort": {"_id.date": 1}},
        ]
        exec_rows = await self.db["executions"].aggregate(exec_pipeline).to_list(300)

        # 按天聚合 AI 分析完成次数
        ai_pipeline = [
            {"$match": {
                "project_id": project_id,
                "updated_at": {"$gte": since},
                "analysis_status": {"$in": ["applied", "done"]},
            }},
            {"$group": {
                "_id": {"$dateToString": {"format": date_fmt, "date": "$updated_at"}},
                "count": {"$sum": 1},
            }},
            {"$sort": {"_id": 1}},
        ]
        ai_rows = await self.db["api_dsls"].aggregate(ai_pipeline).to_list(300)

        # 构建按日趋势数组
        daily: dict[str, dict] = {}
        for r in exec_rows:
            d = r["_id"]["date"]
            t = r["_id"]["type"]
            if d not in daily:
                daily[d] = {"date": d, "scenario": 0, "single": 0, "monitor": 0}
            if t in daily[d]:
                daily[d][t] = r["count"]

        # 合并 AI 分析数据
        for r in ai_rows:
            d = r["_id"]
            if d not in daily:
                daily[d] = {"date": d, "scenario": 0, "single": 0, "monitor": 0}
            daily[d]["ai_analysis"] = r["count"]

        trend = sorted(daily.values(), key=lambda x: x["date"])

        # 汇总
        total_execs = sum(t.get("scenario", 0) + t.get("single", 0) + t.get("monitor", 0) for t in trend)
        total_ai = sum(t.get("ai_analysis", 0) for t in trend)
        active_days = len([t for t in trend if (t.get("scenario", 0) + t.get("single", 0) + t.get("monitor", 0)) > 0])

        return {
            "project_id": project_id,
            "days": days,
            "total_executions": total_execs,
            "total_ai_analyses": total_ai,
            "active_days": active_days,
            "daily_trend": trend,
        }

    # ── Dashboard: SLA 报告 ──────────────────────────────────

    async def get_sla(
        self, project_id: str = "default", period: str = "30d",
    ) -> dict[str, Any]:
        """按 API 计算可用性百分比"""
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        days = 30 if period == "30d" else (7 if period == "7d" else 90)
        since = now - timedelta(days=days)

        pipeline = [
            {"$match": {
                "project_id": project_id,
                "started_at": {"$gte": since},
                "type": "single",
            }},
            {"$group": {
                "_id": "$api_id",
                "total": {"$sum": 1},
                "passed": {"$sum": {"$cond": ["$passed", 1, 0]}},
                "avg_duration_ms": {"$avg": "$duration_ms"},
            }},
            {"$sort": {"total": -1}},
            {"$limit": 100},
        ]
        rows = await self.db["executions"].aggregate(pipeline).to_list(100)

        # 收集所有 api_id，用 $in 一次查询补充 API 基础信息，避免 N+1 问题
        api_ids = []
        for r in rows:
            api_id = str(r["_id"]) if isinstance(r["_id"], ObjectId) else r["_id"]
            if api_id:
                api_ids.append(api_id)
        api_docs = {}
        if api_ids:
            cursor = self.db["api_dsls"].find({"id": {"$in": api_ids}}, {"_id": 0, "id": 1, "name": 1, "request.method": 1, "request.path": 1})
            async for doc in cursor:
                api_docs[doc["id"]] = doc

        result = []
        for r in rows:
            # 聚合 _id 可能为 ObjectId，统一转为字符串避免 FastAPI 序列化失败
            api_id = str(r["_id"]) if isinstance(r["_id"], ObjectId) else r["_id"]
            if not api_id:
                continue
            total = r["total"]
            passed = r["passed"]
            sla_pct = round(passed / total * 100, 2) if total else 100.0
            api_doc = api_docs.get(api_id)
            # method/path 存储在嵌套的 request 对象中（ApiDSL.request.method / ApiDSL.request.path）
            req = (api_doc.get("request") or {}) if api_doc else {}
            result.append({
                "api_id": api_id,
                "name": api_doc.get("name", "") if api_doc else "",
                "method": req.get("method", ""),
                "path": req.get("path", ""),
                "total_execs": total,
                "passed": passed,
                "failed": total - passed,
                "sla_pct": sla_pct,
                "sla_met": sla_pct >= 99.0,
                "avg_duration_ms": round(r.get("avg_duration_ms", 0) or 0, 1),
            })

        all_total = sum(r["total_execs"] for r in result)
        all_passed = sum(r["passed"] for r in result)
        global_sla = round(all_passed / all_total * 100, 2) if all_total else 100.0
        met_count = sum(1 for r in result if r["sla_met"])

        return {
            "project_id": project_id,
            "period": period,
            "global_sla_pct": global_sla,
            "sla_met_count": met_count,
            "total_api_count": len(result),
            "items": result,
        }

    # ── Dashboard: AI 成本与质量指标 ─────────────────────────

    async def get_ai_quality(self, project_id: str = "default", period: str = "30d") -> dict[str, Any]:
        """聚合 AI 调用量、成功率、token 估算、审核积压和 DLQ 趋势。"""
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        days = 7 if period == "7d" else (90 if period == "90d" else 30)
        since = now - timedelta(days=days)

        generations = await self._find_recent(
            "generation_versions",
            {"project_id": project_id, "created_at": {"$gte": since}},
            {
                "_id": 0, "type": 1, "status": 1, "latency_ms": 1,
                "input_tokens": 1, "output_tokens": 1, "created_at": 1,
            },
            5000,
        )
        jobs = await self._find_recent(
            "ai_jobs",
            {"project_id": project_id, "created_at": {"$gte": since}},
            {
                "_id": 0, "job_id": 1, "type": 1, "status": 1, "error": 1,
                "created_at": 1, "updated_at": 1, "finished_at": 1,
            },
            5000,
        )

        pending_review_backlog = await self._safe_count("generation_versions", {
            "project_id": project_id,
            "status": "pending_review",
        })
        dlq_backlog = await self._safe_count("ai_jobs", {
            "project_id": project_id,
            "status": "dlq",
        })

        generation_summary = self._summarize_generations(generations)
        job_summary = self._summarize_ai_jobs(jobs)
        nodes = self._summarize_generation_nodes(generations)

        return {
            "project_id": project_id,
            "period": period,
            "since": since.isoformat(),
            "jobs": job_summary,
            "generations": {
                **generation_summary,
                "pending_review_backlog": pending_review_backlog,
            },
            "quality": {
                "success_rate_pct": job_summary["success_rate_pct"],
                "acceptance_rate_pct": generation_summary["acceptance_rate_pct"],
                "pending_review_backlog": pending_review_backlog,
                "dlq_backlog": dlq_backlog,
            },
            "nodes": nodes,
            "by_type": nodes,
            "dlq_trend": self._dlq_trend(jobs, days),
            "recent_errors": self._recent_ai_errors(jobs),
        }

    async def _find_recent(self, collection: str, query: dict[str, Any], projection: dict[str, int], limit: int) -> list[dict[str, Any]]:
        """读取统计所需字段；统计失败不阻断 Dashboard 基础数据。"""
        try:
            return await self.db[collection].find(query, projection).to_list(limit)
        except Exception:
            return []

    async def _safe_count(self, collection: str, query: dict[str, Any]) -> int:
        try:
            return await self.db[collection].count_documents(query)
        except Exception:
            return 0

    @staticmethod
    def _summarize_generations(docs: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(docs)
        input_tokens = sum(int(d.get("input_tokens") or 0) for d in docs)
        output_tokens = sum(int(d.get("output_tokens") or 0) for d in docs)
        latencies = [int(d.get("latency_ms") or 0) for d in docs if int(d.get("latency_ms") or 0) > 0]
        accepted = sum(1 for d in docs if d.get("status") == "accepted")
        partially_accepted = sum(1 for d in docs if d.get("status") == "partially_accepted")
        rejected = sum(1 for d in docs if d.get("status") == "rejected")
        pending = sum(1 for d in docs if d.get("status") == "pending_review")
        reviewed = accepted + partially_accepted + rejected
        accepted_like = accepted + partially_accepted
        return {
            "total": total,
            "pending_review": pending,
            "accepted": accepted,
            "partially_accepted": partially_accepted,
            "rejected": rejected,
            "reviewed": reviewed,
            "acceptance_rate_pct": round(accepted_like / reviewed * 100, 1) if reviewed else 0.0,
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "tokens_per_generation": round((input_tokens + output_tokens) / total, 1) if total else 0.0,
        }

    @staticmethod
    def _summarize_ai_jobs(docs: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(docs)
        success_statuses = {"done", "pending_review"}
        failed_statuses = {"failed", "dlq"}
        active_statuses = {"queued", "running", "retry"}
        success = sum(1 for d in docs if d.get("status") in success_statuses)
        failed = sum(1 for d in docs if d.get("status") in failed_statuses)
        active = sum(1 for d in docs if d.get("status") in active_statuses)
        completed = success + failed
        durations = []
        for d in docs:
            created = d.get("created_at")
            finished = d.get("finished_at") or d.get("updated_at")
            if isinstance(created, datetime) and isinstance(finished, datetime) and finished >= created:
                durations.append((finished - created).total_seconds() * 1000)
        return {
            "total": total,
            "success": success,
            "failed": failed,
            "active": active,
            "dlq": sum(1 for d in docs if d.get("status") == "dlq"),
            "success_rate_pct": round(success / completed * 100, 1) if completed else 0.0,
            "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else 0.0,
        }

    @classmethod
    def _summarize_generation_nodes(cls, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for doc in docs:
            grouped.setdefault(str(doc.get("type") or "unknown"), []).append(doc)
        rows = []
        for gen_type, items in grouped.items():
            summary = cls._summarize_generations(items)
            rows.append({
                "type": gen_type,
                "label": _generation_type_label(gen_type),
                "action": f"/generations?type={gen_type}",
                "count": summary["total"],
                "pending_review": summary["pending_review"],
                "accepted": summary["accepted"],
                "partially_accepted": summary["partially_accepted"],
                "rejected": summary["rejected"],
                "acceptance_rate_pct": summary["acceptance_rate_pct"],
                "avg_latency_ms": summary["avg_latency_ms"],
                "total_tokens": summary["total_tokens"],
            })
        rows.sort(key=lambda item: item["count"], reverse=True)
        return rows

    @staticmethod
    def _dlq_trend(jobs: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for job in jobs:
            if job.get("status") != "dlq":
                continue
            bucket_dt = job.get("finished_at") or job.get("updated_at") or job.get("created_at")
            if not isinstance(bucket_dt, datetime):
                continue
            bucket = bucket_dt.strftime("%Y-%m-%d")
            counts[bucket] = counts.get(bucket, 0) + 1
        return [
            {"bucket": bucket, "count": counts[bucket]}
            for bucket in sorted(counts.keys())[-days:]
        ]

    @staticmethod
    def _recent_ai_errors(jobs: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
        rows = [
            job for job in jobs
            if job.get("status") in {"failed", "dlq"} and job.get("error")
        ]
        rows.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or datetime.min, reverse=True)
        return [
            {
                "job_id": str(job.get("job_id") or ""),
                "type": str(job.get("type") or ""),
                "status": str(job.get("status") or ""),
                "error": str(job.get("error") or "")[:300],
                "updated_at": job.get("updated_at") or job.get("created_at"),
            }
            for job in rows[:limit]
        ]

    # ── 覆盖度矩阵 —— 按模块 + 维度聚合测试覆盖率 ──────────

    async def get_coverage(self, project_id: str = "default") -> dict[str, Any]:
        """按 API 路径首段(模块)聚合 5 个维度的覆盖率矩阵

        维度定义:
          doc:      有 doc.summary 或 doc.description 的 API 占比
          asserts:  asserts 数组非空的 API 占比
          scenario: 被场景引用的 API 占比
          monitor:  有活跃监控器的 API 占比
          execute:  有至少一次执行记录的 API 占比
        """
        # 1. 加载项目下所有 API 文档（仅投影覆盖度计算所需字段）
        api_docs = await self.db["api_dsls"].find(
            {"project_id": project_id},
            {"id": 1, "request.path": 1, "doc": 1, "asserts": 1, "_id": 0},
        ).to_list(length=None)

        if not api_docs:
            return {"modules": [], "dimensions": ["doc", "asserts", "scenario", "monitor", "execute"], "matrix": []}

        # 提取 API ID 集合 & 按模块分组
        api_ids = set()
        module_apis: dict[str, list[str]] = {}  # module -> [api_id, ...]
        for doc in api_docs:
            aid = doc.get("id")
            if not aid:
                continue
            api_ids.add(aid)
            # 模块名 = URL path 第一段，去除前后斜杠
            path = (doc.get("request") or {}).get("path", "")
            if isinstance(path, str) and path:
                module = path.strip("/").split("/")[0].lower() or "_root"
            else:
                module = "_unclassified"
            module_apis.setdefault(module, []).append(aid)

        # 2. 一次性查询场景、监控器、执行记录中的 API 引用
        scenario_api_ids = set()
        monitor_api_ids = set()
        execute_api_ids = set()

        # 场景引用: scenarios.steps[].api_id + scenarios.api_ids
        scenario_docs = await self.db["scenarios"].find(
            {"project_id": project_id},
            {"steps.api_id": 1, "api_ids": 1, "_id": 0},
        ).to_list(length=None)
        for s in scenario_docs:
            for step in s.get("steps") or []:
                said = step.get("api_id")
                if said:
                    scenario_api_ids.add(said)
            for aid in s.get("api_ids") or []:
                scenario_api_ids.add(aid)

        # 监控器引用: monitors.api_ids
        monitor_docs = await self.db["monitors"].find(
            {"project_id": project_id, "status": "active"},
            {"api_ids": 1, "scenario_ids": 1, "_id": 0},
        ).to_list(length=None)
        for m in monitor_docs:
            # monitors 通过 api_ids 或 scenario_ids 间接覆盖 API，这里先聚合场景覆盖的 API
            # 对 monitor 而言：直接引用 api_ids，或间接通过场景的 api_ids
            for aid in m.get("api_ids") or []:
                monitor_api_ids.add(aid)
            # 场景覆盖：monitor 引用的 scenario 会导致其下的 API 也被监控覆盖
            for sid in m.get("scenario_ids") or []:
                # 已在 scenario 查询中获取，此处只需标记 scenario 下的 API
                pass

        # 执行记录: executions.api_id（去重）
        exec_pipeline = [
            {"$match": {"project_id": project_id}},
            {"$group": {"_id": "$api_id"}},
        ]
        exec_rows = await self.db["executions"].aggregate(exec_pipeline).to_list(length=None)
        execute_api_ids = {r["_id"] for r in exec_rows if r.get("_id")}

        # 3. 按模块计算各维度覆盖率
        matrix = []
        for module in sorted(module_apis.keys()):
            mids = module_apis[module]
            total = len(mids)
            if total == 0:
                continue

            # doc 覆盖率：有 summary 或 description
            doc_covered = 0
            asserts_covered = 0
            for doc in api_docs:
                aid = doc.get("id")
                if aid not in set(mids):
                    continue
                # doc 维度
                api_doc = doc.get("doc") or {}
                if api_doc.get("summary") or api_doc.get("description"):
                    doc_covered += 1
                # asserts 维度
                asserts = doc.get("asserts") or []
                if len(asserts) > 0:
                    asserts_covered += 1

            # scenario/monitor/execute 维度：计算交集
            scenario_covered = sum(1 for aid in mids if aid in scenario_api_ids)
            monitor_covered = sum(1 for aid in mids if aid in monitor_api_ids)
            execute_covered = sum(1 for aid in mids if aid in execute_api_ids)

            matrix.append({
                "module": module,
                "total": total,
                "doc": round(doc_covered / total * 100 if total else 0),
                "asserts": round(asserts_covered / total * 100 if total else 0),
                "scenario": round(scenario_covered / total * 100 if total else 0),
                "monitor": round(monitor_covered / total * 100 if total else 0),
                "execute": round(execute_covered / total * 100 if total else 0),
            })

        # 全局汇总
        total_apis = len(api_ids)
        matrix.append({
            "module": "_all",
            "total": total_apis,
            "doc": round(sum(1 for d in api_docs if (d.get("doc") or {}).get("summary") or (d.get("doc") or {}).get("description")) / max(total_apis, 1) * 100),
            "asserts": round(sum(1 for d in api_docs if len(d.get("asserts") or []) > 0) / max(total_apis, 1) * 100),
            "scenario": round(len(scenario_api_ids & api_ids) / max(total_apis, 1) * 100),
            "monitor": round(len(monitor_api_ids & api_ids) / max(total_apis, 1) * 100),
            "execute": round(len(execute_api_ids & api_ids) / max(total_apis, 1) * 100),
        })

        return {
            "project_id": project_id,
            "dimensions": ["doc", "asserts", "scenario", "monitor", "execute"],
            "modules": [m["module"] for m in matrix],
            "matrix": matrix,
        }
