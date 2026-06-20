"""场景模块优化测试（P0-1 状态流转 + P0-2 实时进度广播）

验证：
1. run_scenario 更新场景状态（running → done/failed）
2. batch_run_scenarios 并发执行 + 状态流转
3. engine._broadcast_step_status 无 ws_manager 时静默跳过（兼容性）
4. engine.run_scenario 接受 exec_id 参数并用作 record.id
5. _broadcast_step_status 广播正确格式
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from models.dsl import (
    ApiDSL, HttpMethod, RequestDSL, ResponseDSL,
    ScenarioDSL, ScenarioStep, ExecutionRecord,
)
from dag_engine.engine import DagExecutionEngine
from services.scenario_service import ScenarioService


class _FakeCursor:
    """测试用 cursor，只实现 sort/limit/to_list 链式调用。"""

    def __init__(self, docs):
        self.docs = docs

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    async def to_list(self, length=None):
        return self.docs[:length] if length else self.docs


def _make_engine(ws_manager=None):
    """构造引擎实例（不依赖真实 DB/Redis）。"""
    engine = DagExecutionEngine.__new__(DagExecutionEngine)
    engine._db = MagicMock()
    engine._redis = None
    engine._api_col = AsyncMock()
    engine._exec_col = AsyncMock()
    engine._tmpl_col = AsyncMock()
    engine._ws_manager = ws_manager
    engine._exec_id = ""
    return engine


def _minimal_steps():
    """当前场景校验要求 start/end 各一个；状态流转测试用最小合法场景。"""
    return [
        {"step_id": "start", "type": "start", "depends_on": []},
        {"step_id": "end", "type": "end", "depends_on": []},
    ]


# ── _broadcast_step_status 兼容性 ──────────────────────────

@pytest.mark.asyncio
async def test_broadcast_step_status_silent_without_ws_manager():
    """无 ws_manager 时应静默跳过，不抛异常（兼容测试和不需进度的场景）。"""
    engine = _make_engine(ws_manager=None)
    engine._exec_id = "exec-1"
    # 应不抛异常
    await engine._broadcast_step_status("step-1", "running")
    await engine._broadcast_step_status("step-1", "passed", latency_ms=100)


@pytest.mark.asyncio
async def test_broadcast_step_status_silent_without_exec_id():
    """无 exec_id 时应静默跳过。"""
    ws = AsyncMock()
    engine = _make_engine(ws_manager=ws)
    engine._exec_id = ""
    await engine._broadcast_step_status("step-1", "running")
    ws.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_broadcast_step_status_broadcasts_correct_format():
    """有 ws_manager + exec_id 时应广播正确格式。"""
    ws = AsyncMock()
    engine = _make_engine(ws_manager=ws)
    engine._exec_id = "exec-1"
    await engine._broadcast_step_status("step-1", "running")
    ws.broadcast.assert_awaited_once()
    call_args = ws.broadcast.call_args[0]
    assert call_args[0] == "exec:exec-1"
    data = call_args[1]
    assert data["type"] == "step_status"
    assert data["exec_id"] == "exec-1"
    assert data["step_id"] == "step-1"
    assert data["status"] == "running"


@pytest.mark.asyncio
async def test_broadcast_step_status_includes_latency_and_error():
    """passed/failed 状态应包含 latency_ms 和 error。"""
    ws = AsyncMock()
    engine = _make_engine(ws_manager=ws)
    engine._exec_id = "exec-1"
    await engine._broadcast_step_status("step-1", "failed", latency_ms=500, error="timeout")
    data = ws.broadcast.call_args[0][1]
    assert data["latency_ms"] == 500
    assert "timeout" in data["error"]


@pytest.mark.asyncio
async def test_broadcast_step_status_broadcast_failure_doesnt_crash():
    """ws_manager.broadcast 抛异常时不应影响执行流程。"""
    ws = AsyncMock()
    ws.broadcast = AsyncMock(side_effect=Exception("ws down"))
    engine = _make_engine(ws_manager=ws)
    engine._exec_id = "exec-1"
    # 应不抛异常（静默捕获）
    await engine._broadcast_step_status("step-1", "running")


# ── run_scenario exec_id 参数 ──────────────────────────────

@pytest.mark.asyncio
async def test_run_scenario_uses_provided_exec_id():
    """run_scenario 应使用传入的 exec_id 作为 record.id（P0-2 透传链路）。"""
    engine = _make_engine()
    # 空 steps 场景（快速完成，不依赖 API 调用）
    scenario = ScenarioDSL(id="s1", name="test", steps=[])
    record = await engine.run_scenario(scenario, exec_id="my-exec-id")
    assert record.id == "my-exec-id"


@pytest.mark.asyncio
async def test_run_scenario_generates_exec_id_when_not_provided():
    """未传 exec_id 时应自动生成 uuid。"""
    engine = _make_engine()
    scenario = ScenarioDSL(id="s1", name="test", steps=[])
    record = await engine.run_scenario(scenario)
    assert record.id  # 非空
    assert len(record.id) == 36  # uuid 格式


# ── 状态流转（service 层 mock）─────────────────────────────

@pytest.mark.asyncio
async def test_service_updates_status_running_then_done():
    """service.run_scenario 应在执行前更新 running，完成后更新 done/failed。"""
    db = MagicMock()
    scenarios_col = AsyncMock()
    scenarios_col.find_one = AsyncMock(return_value={
        "id": "s1", "name": "test", "steps": _minimal_steps(), "project_id": "p1",
    })
    scenarios_col.update_one = AsyncMock()
    environments_col = AsyncMock()

    def getitem(k):
        return {"scenarios": scenarios_col, "environments": environments_col}.get(k, AsyncMock())
    db.__getitem__ = MagicMock(side_effect=getitem)

    redis = AsyncMock()
    service = ScenarioService(db)

    # mock engine.run_scenario 返回通过的 record
    mock_record = ExecutionRecord(id="r1", scenario_id="s1", passed=True, project_id="p1")
    with pytest.MonkeyPatch.context() as m:
        m.setattr("services.scenario_service.DagExecutionEngine", lambda *a, **k: MagicMock(
            run_scenario=AsyncMock(return_value=mock_record)
        ))
        result = await service.run_scenario("s1", redis)

    # 应有两次 update_one：running + done
    assert scenarios_col.update_one.await_count >= 2
    # 验证最终状态是 done
    last_call = scenarios_col.update_one.call_args_list[-1]
    update_set = last_call[0][1]["$set"]
    assert update_set["status"] == "done"


@pytest.mark.asyncio
async def test_service_updates_status_failed_on_failure():
    """执行失败时应更新状态为 failed。"""
    db = MagicMock()
    scenarios_col = AsyncMock()
    scenarios_col.find_one = AsyncMock(return_value={
        "id": "s1", "name": "test", "steps": _minimal_steps(), "project_id": "p1",
    })
    scenarios_col.update_one = AsyncMock()

    def getitem(k):
        return {"scenarios": scenarios_col, "environments": AsyncMock()}.get(k, AsyncMock())
    db.__getitem__ = MagicMock(side_effect=getitem)

    redis = AsyncMock()
    service = ScenarioService(db)
    mock_record = ExecutionRecord(id="r1", scenario_id="s1", passed=False, failure_reason="err", project_id="p1")
    with pytest.MonkeyPatch.context() as m:
        m.setattr("services.scenario_service.DagExecutionEngine", lambda *a, **k: MagicMock(
            run_scenario=AsyncMock(return_value=mock_record)
        ))
        await service.run_scenario("s1", redis)

    last_call = scenarios_col.update_one.call_args_list[-1]
    assert last_call[0][1]["$set"]["status"] == "failed"


@pytest.mark.asyncio
async def test_update_scenario_saves_previous_version_snapshot():
    """P2-1: update_scenario 保存前应先把旧场景写入 scenario_versions。"""
    db = MagicMock()
    old_doc = {"id": "s1", "name": "old", "description": "old d", "steps": [{"step_id": "a"}], "project_id": "p1"}
    scenarios_col = AsyncMock()
    scenarios_col.find_one = AsyncMock(return_value=old_doc)
    scenarios_col.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    versions_col = AsyncMock()
    versions_col.find_one = AsyncMock(return_value=None)
    versions_col.insert_one = AsyncMock()

    def getitem(k):
        return {"scenarios": scenarios_col, "scenario_versions": versions_col}.get(k, AsyncMock())
    db.__getitem__ = MagicMock(side_effect=getitem)

    service = ScenarioService(db)
    ok = await service.update_scenario("s1", {"name": "new", "_actor": "alice"}, "p1")

    assert ok is True
    versions_col.insert_one.assert_awaited_once()
    version_doc = versions_col.insert_one.call_args.args[0]
    assert version_doc["scenario_id"] == "s1"
    assert version_doc["version"] == 1
    assert version_doc["snapshot"]["name"] == "old"
    assert version_doc["actor"] == "alice"


@pytest.mark.asyncio
async def test_list_scenario_versions_hides_snapshot():
    """版本列表应隐藏完整 snapshot，避免列表响应过大。"""
    db = MagicMock()
    scenarios_col = AsyncMock()
    scenarios_col.find_one = AsyncMock(return_value={"id": "s1", "project_id": "p1"})
    versions_col = MagicMock()
    versions_col.find = MagicMock(return_value=_FakeCursor([{"id": "v1", "version": 1, "name": "old"}]))

    def getitem(k):
        return {"scenarios": scenarios_col, "scenario_versions": versions_col}.get(k, AsyncMock())
    db.__getitem__ = MagicMock(side_effect=getitem)

    service = ScenarioService(db)
    result = await service.list_scenario_versions("s1", "p1")

    assert result["total"] == 1
    assert result["items"][0]["id"] == "v1"
    find_args = versions_col.find.call_args.args
    assert find_args[1] == {"_id": 0, "snapshot": 0}


@pytest.mark.asyncio
async def test_restore_scenario_version_saves_current_and_replaces_snapshot():
    """回滚前应保存当前版本，然后用目标 snapshot replace 当前场景。"""
    db = MagicMock()
    current = {"id": "s1", "name": "current", "steps": [{"step_id": "cur"}], "project_id": "p1"}
    snapshot = {"id": "s1", "name": "old", "steps": [{"step_id": "old"}], "project_id": "p1"}
    scenarios_col = AsyncMock()
    scenarios_col.find_one = AsyncMock(return_value=current)
    scenarios_col.replace_one = AsyncMock(return_value=MagicMock(matched_count=1))
    versions_col = AsyncMock()
    versions_col.find_one = AsyncMock(side_effect=[
        {"id": "v1", "scenario_id": "s1", "project_id": "p1", "version": 3, "snapshot": snapshot},
        {"version": 3},
    ])
    versions_col.insert_one = AsyncMock()

    def getitem(k):
        return {"scenarios": scenarios_col, "scenario_versions": versions_col}.get(k, AsyncMock())
    db.__getitem__ = MagicMock(side_effect=getitem)

    service = ScenarioService(db)
    restored = await service.restore_scenario_version("s1", "v1", "p1", actor="bob")

    versions_col.insert_one.assert_awaited_once()
    assert versions_col.insert_one.call_args.args[0]["reason"] == "restore:3"
    scenarios_col.replace_one.assert_awaited_once()
    replace_doc = scenarios_col.replace_one.call_args.args[1]
    assert replace_doc["name"] == "old"
    assert replace_doc["updated_at"]
    assert restored["steps"] == [{"step_id": "old"}]
