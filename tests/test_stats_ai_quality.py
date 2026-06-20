"""
Dashboard AI 产出采纳统计测试。

验证 generation_versions / ai_jobs 聚合成用户可读节点：采纳、部分采纳、放弃、待审核。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from services.stats_service import StatsService


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, _limit):
        return self.rows


class _Collection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query, _projection=None):
        project_id = query.get("project_id")
        created_at = query.get("created_at", {})
        since = created_at.get("$gte")
        rows = [
            row for row in self.rows
            if row.get("project_id") == project_id and (since is None or row.get("created_at") >= since)
        ]
        return _Cursor(rows)

    async def count_documents(self, query):
        count = 0
        for row in self.rows:
            ok = True
            for key, expected in query.items():
                if row.get(key) != expected:
                    ok = False
                    break
            if ok:
                count += 1
        return count


class _Db:
    def __init__(self, generations, jobs):
        self.cols = {
            "generation_versions": _Collection(generations),
            "ai_jobs": _Collection(jobs),
        }

    def __getitem__(self, name):
        return self.cols.get(name, _Collection([]))


@pytest.mark.asyncio
async def test_ai_quality_groups_generation_nodes_and_status_counts():
    now = datetime.now()
    generations = [
        {"project_id": "p1", "type": "doc", "status": "accepted", "latency_ms": 100, "input_tokens": 10, "output_tokens": 20, "created_at": now},
        {"project_id": "p1", "type": "doc", "status": "partially_accepted", "latency_ms": 200, "input_tokens": 20, "output_tokens": 20, "created_at": now},
        {"project_id": "p1", "type": "doc", "status": "rejected", "latency_ms": 300, "input_tokens": 5, "output_tokens": 5, "created_at": now},
        {"project_id": "p1", "type": "scenario", "status": "pending_review", "latency_ms": 400, "input_tokens": 30, "output_tokens": 40, "created_at": now},
        {"project_id": "p2", "type": "doc", "status": "accepted", "created_at": now},
    ]
    jobs = [
        {"project_id": "p1", "job_id": "j1", "type": "doc", "status": "pending_review", "created_at": now - timedelta(seconds=3), "finished_at": now},
        {"project_id": "p1", "job_id": "j2", "type": "scenario", "status": "dlq", "error": "bad json", "created_at": now - timedelta(seconds=2), "updated_at": now},
    ]
    result = await StatsService(_Db(generations, jobs)).get_ai_quality("p1", "30d")

    assert result["generations"]["total"] == 4
    assert result["generations"]["acceptance_rate_pct"] == 66.7
    assert result["quality"]["pending_review_backlog"] == 1
    assert result["quality"]["dlq_backlog"] == 1
    doc_node = next(item for item in result["nodes"] if item["type"] == "doc")
    assert doc_node["accepted"] == 1
    assert doc_node["partially_accepted"] == 1
    assert doc_node["rejected"] == 1
    assert doc_node["pending_review"] == 0
    assert result["recent_errors"][0]["job_id"] == "j2"
