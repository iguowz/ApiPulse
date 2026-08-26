from __future__ import annotations

import re
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routers import executions, generations
from ai_analyzer.failure_diagnoser import FailureDiagnoserService
from services.memory_service import MemoryService, _beijing_now


class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction):
        reverse = direction < 0
        self.docs.sort(key=lambda d: d.get(key) or datetime.min, reverse=reverse)
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    async def to_list(self, n):
        return self.docs[:n]


def _matches(doc, query):
    for key, expected in (query or {}).items():
        if key == "$or":
            if not any(_matches(doc, item) for item in expected):
                return False
            continue
        if key == "$and":
            if not all(_matches(doc, item) for item in expected):
                return False
            continue
        actual = _path_value(doc, key)
        if isinstance(expected, dict):
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$lt" in expected and not (actual is not None and actual < expected["$lt"]):
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$regex" in expected:
                pattern = re.compile(expected["$regex"])
                values = actual if isinstance(actual, list) else [actual]
                if not any(isinstance(v, str) and pattern.search(v) for v in values):
                    return False
        elif isinstance(actual, list):
            if expected not in actual:
                return False
        elif actual != expected:
            return False
    return True


def _path_value(doc, key):
    value = doc
    for part in str(key).split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list):
            values = []
            for item in value:
                if isinstance(item, dict):
                    values.append(item.get(part))
            value = values
        else:
            return None
    return value


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.updates = []

    def find(self, query=None, projection=None):
        return FakeCursor([d for d in self.docs if _matches(d, query or {})])

    async def find_one(self, query=None, projection=None):
        return next((d for d in self.docs if _matches(d, query or {})), None)

    async def insert_one(self, doc):
        inserted_id = f"ins-{len(self.docs) + 1}"
        stored = dict(doc)
        stored.setdefault("_id", inserted_id)
        self.docs.append(stored)
        return SimpleNamespace(inserted_id=inserted_id)

    def aggregate(self, pipeline):
        docs = list(self.docs)
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _matches(d, stage["$match"])]
            elif "$group" in stage:
                grouped = {}
                for doc in docs:
                    key = (
                        doc.get("project_id"),
                        doc.get("type"),
                        doc.get("title"),
                    )
                    grouped.setdefault(key, {"_id": {"project_id": key[0], "type": key[1], "title": key[2]}, "count": 0, "ids": []})
                    grouped[key]["count"] += 1
                    grouped[key]["ids"].append(doc.get("id"))
                docs = list(grouped.values())
            elif "$limit" in stage:
                docs = docs[:stage["$limit"]]
        docs = [d for d in docs if d.get("count", 0) > 1]
        return FakeCursor(docs)

    async def update_one(self, query, update, **kwargs):
        matched = next((d for d in self.docs if _matches(d, query)), None)
        if matched:
            matched.update(update.get("$set", {}))
        self.updates.append((query, update))
        return SimpleNamespace(matched_count=1 if matched else 0)


class FakeDb:
    def __init__(self, collections):
        self.collections = collections

    def __getitem__(self, key):
        return self.collections[key]

    def get_collection(self, key):
        return self.collections[key]


@pytest.mark.asyncio
async def test_batch_review_reject_updates_generation_and_audits(monkeypatch):
    generation_docs = {
        "gen-1": {"id": "gen-1", "project_id": "p1", "type": "doc", "api_id": "api-1"},
        "gen-2": {"id": "gen-2", "project_id": "p1", "type": "asserts", "api_id": "api-2"},
    }
    collection = FakeCollection(list(generation_docs.values()))
    audit = SimpleNamespace(log_action=AsyncMock())
    feedback_calls = []

    async def fake_load(generation_id):
        return generation_docs.get(generation_id)

    async def fake_get_db():
        return FakeDb({"generation_versions": collection})

    async def fake_record(gv_doc, decision, reviewer_id=None, extra=None):
        feedback_calls.append((gv_doc["id"], decision, reviewer_id, extra))

    monkeypatch.setattr(generations, "_load_generation_doc", fake_load)
    monkeypatch.setattr(generations, "get_db", fake_get_db)
    monkeypatch.setattr(generations, "_record_review_to_memory", fake_record)
    monkeypatch.setattr(generations._state, "_ai_analyzer", object())

    result = await generations.batch_review_generations(
        {"ids": ["gen-1", "gen-2", "missing"], "action": "reject", "feedback": "noisy"},
        request=None,
        current_user={"username": "alice", "role": "member", "project_id": "p1"},
        audit_service=audit,
    )

    assert result["ok"] == 2
    assert result["total"] == 3
    assert result["items"][-1] == {"id": "missing", "ok": False, "error": "not_found"}
    assert all(doc["status"] == "rejected" for doc in collection.docs)
    assert feedback_calls == [
        ("gen-1", "rejected", "alice", "noisy"),
        ("gen-2", "rejected", "alice", "noisy"),
    ]
    audit.log_action.assert_awaited_once()


@pytest.mark.asyncio
async def test_memory_cleanup_candidates_cover_quality_rules():
    old = _beijing_now() - timedelta(days=120)
    l2_docs = [
        {"id": "low", "project_id": "p1", "type": "chat", "title": "Low", "confidence": 0.1, "updated_at": _beijing_now(), "tags": []},
        {"id": "stale", "project_id": "p1", "type": "chat", "title": "Stale", "confidence": 0.8, "updated_at": old, "usage_count": 0, "tags": []},
        {"id": "dup-a", "project_id": "p1", "type": "pattern", "title": "Dup", "confidence": 0.8, "updated_at": _beijing_now(), "tags": []},
        {"id": "dup-b", "project_id": "p1", "type": "pattern", "title": "Dup", "confidence": 0.8, "updated_at": _beijing_now(), "tags": []},
        {"id": "orphan-l2", "project_id": "p1", "type": "api", "title": "Orphan", "confidence": 0.8, "updated_at": _beijing_now(), "tags": ["api:ghost"]},
    ]
    l3_docs = [
        {"session_id": "orphan-l3", "project_id": "p1", "summary": "session", "updated_at": _beijing_now(), "tags": ["api:ghost"]},
    ]
    db = FakeDb({
        "l2_memories": FakeCollection(l2_docs),
        "l3_memories": FakeCollection(l3_docs),
        "api_dsls": FakeCollection([{"id": "existing"}]),
    })
    service = MemoryService.__new__(MemoryService)
    service._db = db

    result = await service.cleanup_candidates("p1", stale_days=90, low_confidence=0.2, limit=20)
    reasons = {(item["id"], item["reason"]) for item in result["items"]}

    assert result["rules"] == {"stale_days": 90, "low_confidence": 0.2}
    assert ("low", "low_confidence") in reasons
    assert ("stale", "stale_unused") in reasons
    assert ("dup-b", "duplicate_title") in reasons
    assert ("orphan-l2", "orphan_api_reference") in reasons
    assert ("orphan-l3", "orphan_api_reference") in reasons


@pytest.mark.asyncio
async def test_failure_diagnosis_creates_pending_assertion_repair_generation():
    api_doc = {
        "id": "api-1",
        "project_id": "p1",
        "asserts": [
            {"field": "$.code", "operator": "eq", "expected": 0, "description": "业务码", "risk_level": "high"},
            {"field": "$.data.id", "operator": "not_empty", "expected": True, "description": "返回 ID", "risk_level": "medium"},
        ],
    }
    generation_col = FakeCollection([])
    link_col = FakeCollection([])
    db = FakeDb({
        "api_dsls": FakeCollection([api_doc]),
        "generation_versions": generation_col,
        "diagnosis_diff_links": link_col,
    })
    service = FailureDiagnoserService.__new__(FailureDiagnoserService)
    service._db = db
    service._model = "test-model"

    generation_id = await service._create_assertion_repair_generation(
        execution_id="exec-1",
        exec_doc={"id": "exec-1", "api_id": "api-1", "project_id": "p1", "failure_reason": "assert failed"},
        failed_step={"step_id": "step-1", "api_id": "api-1"},
        failed_asserts=[{"field": "$.code", "operator": "eq", "expected": 0, "actual": 1001, "passed": False}],
        diagnosis={"root_cause": "assertion_error", "confidence": 0.92, "suggested_fix": "更新业务码断言"},
    )

    assert generation_id == "ins-1"
    saved = generation_col.docs[0]
    assert saved["type"] == "asserts"
    assert saved["status"] == "pending_review"
    assert saved["source"] == "failure_diagnoser"
    assert saved["job_id"] == "diagnose:exec-1"
    assert saved["content"]["asserts"][0]["expected"] == 1001
    assert saved["content"]["asserts"][1]["expected"] is True
    assert saved["content"]["repair"]["execution_id"] == "exec-1"
    assert link_col.docs[0]["generation_id"] == generation_id
    assert link_col.docs[0]["status"] == "pending_review"


@pytest.mark.asyncio
async def test_execution_detail_enriches_ai_closure_context():
    db = FakeDb({
        "executions": FakeCollection([
            {"id": "exec-1", "project_id": "p1", "api_id": "api-1", "passed": False, "failure_reason": "assert failed", "started_at": datetime(2026, 1, 2)},
            {"id": "exec-2", "project_id": "p1", "api_id": "api-1", "passed": False, "failure_reason": "same api failed", "started_at": datetime(2026, 1, 1), "diagnosis_status": "done", "diagnosis": {"root_cause": "assertion_error"}},
            {"id": "exec-ok", "project_id": "p1", "api_id": "api-1", "passed": True, "started_at": datetime(2026, 1, 3)},
        ]),
        "diagnosis_diff_links": FakeCollection([
            {"execution_id": "exec-1", "api_id": "api-1", "generation_id": "gen-1", "status": "pending_review", "created_at": datetime(2026, 1, 2)},
        ]),
        "generation_versions": FakeCollection([
            {"_id": "gen-1", "project_id": "p1", "api_id": "api-1", "type": "asserts", "status": "pending_review", "job_id": "diagnose:exec-1", "summary": "修复断言", "content": {"repair": {"execution_id": "exec-1"}}, "created_at": datetime(2026, 1, 2)},
        ]),
        "import_diffs": FakeCollection([
            {"id": "diff-1", "project_id": "p1", "existing_api_id": "api-1", "status": "pending", "api_path": "/login", "method": "POST", "fields_diff": [{"field": "code"}], "created_at": datetime(2026, 1, 2)},
        ]),
    })

    result = await executions.get_execution(
        "exec-1",
        db=db,
        current_user={"username": "alice", "role": "member", "project_id": "p1"},
    )

    assert result["diagnosis_links"][0]["generation_id"] == "gen-1"
    assert result["pending_repair_generations"][0]["id"] == "gen-1"
    assert result["related_import_diffs"][0]["id"] == "diff-1"
    assert result["similar_failures"][0]["id"] == "exec-2"
    assert all(item["id"] != "exec-1" for item in result["similar_failures"])
