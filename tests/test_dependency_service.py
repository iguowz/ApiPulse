# -*- coding: utf-8 -*-
"""DependencyService 单元测试：静态挖掘 / 建图 / 聚类 / 依赖上下文 / 边合并。"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from services.dependency_service import DependencyService, _norm


# ── 内存版 fake db/collection（支持服务用到的 find/find_one/insert_one/update_one） ──
class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]):
        self._docs = docs

    # 支持链式 skip/limit（真实 mongo cursor 行为），供分页加载测试
    def skip(self, n: int):
        return _FakeCursor(self._docs[n:])

    def limit(self, n: int):
        return _FakeCursor(self._docs[:n])

    async def to_list(self, length: int = 0):
        return self._docs if length <= 0 else self._docs[:length]

    def __aiter__(self):
        return iter(self._docs)


def _match(doc: dict, f: dict) -> bool:
    for k, v in f.items():
        if isinstance(v, dict):
            if "$in" in v and doc.get(k) not in v["$in"]:
                return False
            if "$gte" in v and not (doc.get(k) or 0) >= v["$gte"]:
                return False
            if "$ne" in v and doc.get(k) == v["$ne"]:
                return False
        else:
            if doc.get(k) != v:
                return False
    return True


class _FakeCol:
    def __init__(self, docs: list[dict[str, Any]] | None = None):
        self.docs: list[dict[str, Any]] = docs or []

    async def find_one(self, f: dict, projection=None):
        for d in self.docs:
            if _match(d, f):
                return d
        return None

    def find(self, f: dict, projection=None):
        return _FakeCursor([d for d in self.docs if _match(d, f)])

    async def insert_one(self, doc: dict):
        doc = {**doc, "_id": len(self.docs)}
        self.docs.append(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    async def update_one(self, f: dict, update: dict):
        for d in self.docs:
            if _match(d, f):
                if "$set" in update:
                    d.update(update["$set"])
                if "$inc" in update:
                    for k, v in update["$inc"].items():
                        d[k] = (d.get(k) or 0) + v
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)


class _FakeDB:
    def __init__(self):
        self._cols: dict[str, _FakeCol] = {}

    def __getitem__(self, name: str) -> _FakeCol:
        if name not in self._cols:
            self._cols[name] = _FakeCol()
        return self._cols[name]


def _api(api_id: str, params: list[dict] | None = None, resp: list[dict] | None = None) -> dict:
    return {
        "id": api_id, "project_id": "p1",
        "doc": {"summary": "", "params": params or [], "response_fields": resp or [], "tags": []},
        "request": {"method": "GET", "path": "/" + api_id},
    }


def test_norm_and_score():
    assert _norm("userId") == _norm("user_id") == "userid"
    svc = DependencyService(_FakeDB())
    assert svc._score_match("userid", "string", "userid", "string") > 0.75
    # 无关联字段名不产边
    assert svc._score_match("token", "string", "userid", "string") == 0.0


@pytest.mark.asyncio
async def test_discover_static_edges_creates_candidate():
    db = _FakeDB()
    db["api_dsls"].docs = [
        _api("login", params=[{"name": "userId", "location": "query", "type": "string"}]),
        _api("user", resp=[{"name": "userId", "type": "string"}]),
    ]
    svc = DependencyService(db)
    n = await svc.discover_static_edges("p1")
    assert n >= 1
    edges = db["api_dependency_edges"].docs
    assert edges, "应至少生成一条依赖边"
    e = edges[0]
    assert e["upstream_api_id"] == "user"      # 提供字段
    assert e["downstream_api_id"] == "login"   # 消费字段
    assert e["field_map"].get("userId") == "$.userId"
    assert e["status"] == "candidate"
    assert e["confidence"] >= 0.4


@pytest.mark.asyncio
async def test_upsert_edge_merges_field_map():
    db = _FakeDB()
    svc = DependencyService(db)
    await svc.upsert_edge("p1", "user", "login", {"userId": "$.userId"}, confidence=0.8)
    await svc.upsert_edge("p1", "user", "login", {"token": "$.token"}, confidence=0.9, observed=2)
    edges = db["api_dependency_edges"].docs
    assert len(edges) == 1
    assert edges[0]["field_map"] == {"userId": "$.userId", "token": "$.token"}
    assert edges[0]["confidence"] == 0.9
    assert edges[0]["observed_count"] == 2
    assert edges[0]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_connected_components_groups():
    db = _FakeDB()
    col = db["api_dependency_edges"]
    async def seed(up, down, conf=0.9):
        await col.insert_one({"project_id": "p1", "upstream_api_id": up, "downstream_api_id": down,
                              "confidence": conf, "status": "candidate", "field_map": {} })
    await seed("a", "b")
    await seed("b", "c")
    await seed("x", "y")
    svc = DependencyService(db)
    clusters = await svc.connected_components("p1")
    comps = sorted((sorted(c) for c in clusters), key=len, reverse=True)
    assert ["a", "b", "c"] in comps
    assert ["x", "y"] in comps


@pytest.mark.asyncio
async def test_dependency_context_mentions_relevant_edges():
    db = _FakeDB()
    await db["api_dependency_edges"].insert_one({
        "project_id": "p1", "upstream_api_id": "user", "downstream_api_id": "login",
        "confidence": 0.9, "status": "candidate", "evidence": "static_name",
        "field_map": {"userId": "$.userId"},
    })
    svc = DependencyService(db)
    ctx = await svc.dependency_context("p1", ["login"])
    assert "login" in ctx and "userId" in ctx and "user" in ctx
def test_resolve_path():
    from services.dependency_service import _resolve_path
    assert _resolve_path({"data": {"user": {"id": 1}}}, "$.data.user.id") is True
    assert _resolve_path({"data": [{"id": 1}]}, "$.data[0].id") is True
    assert _resolve_path({"data": {"user": {"id": 1}}}, "$.data.user.missing") is False
    assert _resolve_path("not dict", "$.a") is False


@pytest.mark.asyncio
async def test_probe_dependencies_confirms_valid_edge():
    db = _FakeDB()
    # 上游 user 的响应样本含 userId
    db["api_dsls"].docs = [{"id": "user", "response": {"status_code": 200, "body": {"data": {"id": 1, "name": "x"}}}},
                           {"id": "login", "response": {"status_code": 200, "body": {}}}]
    await db["api_dependency_edges"].insert_one({
        "project_id": "p1", "upstream_api_id": "user", "downstream_api_id": "login",
        "confidence": 0.6, "status": "candidate", "evidence": "static_name", "field_map": {"userId": "$.data.id"},
    })
    svc = DependencyService(db)
    n = await svc.probe_dependencies("p1")
    assert n == 1
    edge = db["api_dependency_edges"].docs[0]
    assert edge["evidence"] == "dynamic_run"
    assert edge["confidence"] >= 0.7
    assert edge["status"] == "confirmed"
@pytest.mark.asyncio
async def test_update_edges_for_api_finds_both_directions():
    db = _FakeDB()
    # A 消费 userId（来自 B），A 提供 token（被 D 消费）
    db["api_dsls"].docs = [
        {"id": "A", "project_id": "p1", "doc": {"params": [{"name": "userId", "type": "string"}], "response_fields": [{"name": "token", "type": "string"}]}},
        {"id": "B", "project_id": "p1", "doc": {"params": [], "response_fields": [{"name": "userId", "type": "string"}]}},
        {"id": "C", "project_id": "p1", "doc": {"params": [], "response_fields": [{"name": "name", "type": "string"}]}},
        {"id": "D", "project_id": "p1", "doc": {"params": [{"name": "token", "type": "string"}], "response_fields": []}},
    ]
    svc = DependencyService(db)
    n = await svc.update_edges_for_api("A")
    assert n >= 2
    edges = {(e["upstream_api_id"], e["downstream_api_id"]) for e in db["api_dependency_edges"].docs}
    assert ("B", "A") in edges   # A 消费 B 的 userId
    assert ("A", "D") in edges   # D 消费 A 的 token
