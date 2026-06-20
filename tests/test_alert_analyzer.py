"""
P1-2: 巡检 AI 降噪测试

验证：
1. _parse_assessment 解析合法/非法 LLM 输出
2. _parse_assessment 校验 severity 合法值，confidence 钳制
3. _parse_assessment 容忍 markdown 围栏
4. _build_user_prompt 包含告警信息和历史执行记录
5. _fire_alert 触发后入队 queue:alert_analyze
6. 恢复通知不入队 AI 评估
7. _ALERT_SYSTEM prompt 包含误报识别和严重度分级指导
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ai_analyzer.alert_analyzer import (
    AlertAnalyzerService, _ALERT_SYSTEM, ALERT_ANALYZE_QUEUE,
)
from models.dsl import AlertRecord, RiskLevel


def _make_svc():
    """构造 AlertAnalyzerService（不连真实 LLM）。"""
    db = MagicMock()
    redis = AsyncMock()
    svc = AlertAnalyzerService.__new__(AlertAnalyzerService)
    svc._db = db
    svc._redis = redis
    svc._ws = None
    svc._model = "gpt-4o-mini"
    svc._temperature = 0.1
    svc._max_tokens = 1024
    return svc, db, redis


# ── _parse_assessment 解析 ─────────────────────────────────

def test_parse_assessment_valid_json():
    """合法 JSON 应正确解析。"""
    raw = json.dumps({
        "severity": "high",
        "root_cause": "业务码异常，疑似下游服务故障",
        "confidence": 0.85,
    })
    result = AlertAnalyzerService._parse_assessment(raw)
    assert result is not None
    assert result["severity"] == "high"
    assert "业务码" in result["root_cause"]
    assert result["confidence"] == 0.85


def test_parse_assessment_invalid_severity():
    """非法 severity 值 → 返回 None。"""
    raw = json.dumps({"severity": "extreme", "root_cause": "x", "confidence": 0.5})
    assert AlertAnalyzerService._parse_assessment(raw) is None


def test_parse_assessment_confidence_clamped():
    """confidence 超出 [0,1] 应钳制。"""
    raw = json.dumps({"severity": "low", "root_cause": "x", "confidence": 1.5})
    result = AlertAnalyzerService._parse_assessment(raw)
    assert result is not None
    assert result["confidence"] == 1.0
    # 负数也应钳制
    raw2 = json.dumps({"severity": "low", "root_cause": "x", "confidence": -0.5})
    result2 = AlertAnalyzerService._parse_assessment(raw2)
    assert result2["confidence"] == 0.0


def test_parse_assessment_tolerates_markdown_fence():
    """LLM 可能加 markdown 围栏，应容忍剥离。"""
    raw = '```json\n{"severity":"noise","root_cause":"偶发抖动","confidence":0.9}\n```'
    result = AlertAnalyzerService._parse_assessment(raw)
    assert result is not None
    assert result["severity"] == "noise"


def test_parse_assessment_invalid_json():
    """非 JSON → 返回 None。"""
    assert AlertAnalyzerService._parse_assessment("not json at all") is None
    assert AlertAnalyzerService._parse_assessment("") is None


def test_parse_assessment_all_severity_values():
    """所有合法 severity 值都应接受。"""
    for sev in ("critical", "high", "medium", "low", "noise"):
        raw = json.dumps({"severity": sev, "root_cause": "x", "confidence": 0.5})
        result = AlertAnalyzerService._parse_assessment(raw)
        assert result is not None
        assert result["severity"] == sev


# ── _build_user_prompt ─────────────────────────────────────

def test_build_user_prompt_contains_alert_info():
    """prompt 应包含告警标题、消息、历史执行记录。"""
    svc, _, _ = _make_svc()
    alert_doc = {
        "title": "[告警] 用户登录监控",
        "message": "HTTP: 500\n错误: timeout",
        "risk_level": "high",
        "is_recovery": False,
    }
    recent = [{"passed": True, "latency_ms": 100}, {"passed": False, "latency_ms": 5000}]
    prompt = svc._build_user_prompt(alert_doc, recent)
    assert "用户登录监控" in prompt
    assert "timeout" in prompt
    assert "passed" in prompt  # 历史执行记录


# ── assess_alert 核心流程 ──────────────────────────────────

@pytest.mark.asyncio
async def test_assess_alert_writes_back_fields():
    """assess_alert 应回写 ai_severity/ai_root_cause/ai_confidence 到告警记录。"""
    svc, db, _ = _make_svc()
    alert_col = AsyncMock()
    alert_col.find_one = AsyncMock(return_value={
        "id": "alert-1", "monitor_id": "mon-1",
        "title": "[告警] x", "message": "HTTP 500", "risk_level": "high",
    })

    # executions 集合：find 返回 MagicMock 链式（sort/limit 返回自身），
    # 并支持 async for 迭代返回空列表（需 async iterator，非普通 iter）
    exec_col = MagicMock()
    exec_cursor = MagicMock()
    exec_cursor.sort = MagicMock(return_value=exec_cursor)
    exec_cursor.limit = MagicMock(return_value=exec_cursor)

    # 构造真正的空 async iterator
    class _EmptyAsyncIter:
        def __aiter__(self):
            return self
        async def __anext__(self):
            raise StopAsyncIteration
    exec_cursor.__aiter__ = lambda self: _EmptyAsyncIter()
    exec_col.find = MagicMock(return_value=exec_cursor)

    def getitem(k):
        if k == "alert_records":
            return alert_col
        if k == "executions":
            return exec_col
        return AsyncMock()
    db.__getitem__ = MagicMock(side_effect=getitem)

    # mock _call_llm 返回合法评估
    async def mock_llm(*a, **kw):
        return json.dumps({"severity": "noise", "root_cause": "偶发网络抖动", "confidence": 0.8})
    svc._call_llm = mock_llm

    ok = await svc.assess_alert("alert-1")
    assert ok is True
    # 应更新告警记录
    alert_col.update_one.assert_awaited_once()
    update_set = alert_col.update_one.call_args[0][1]["$set"]
    assert update_set["ai_severity"] == "noise"
    assert "偶发" in update_set["ai_root_cause"]
    assert update_set["ai_confidence"] == 0.8
    assert "ai_assessed_at" in update_set


@pytest.mark.asyncio
async def test_assess_alert_not_found_returns_false():
    """告警不存在 → 返回 False。"""
    svc, db, _ = _make_svc()
    alert_col = AsyncMock()
    alert_col.find_one = AsyncMock(return_value=None)
    db.__getitem__ = MagicMock(side_effect=lambda k: alert_col if k == "alert_records" else AsyncMock())

    ok = await svc.assess_alert("missing")
    assert ok is False


@pytest.mark.asyncio
async def test_assess_alert_empty_llm_returns_false():
    """LLM 空响应 → 返回 False。"""
    svc, db, _ = _make_svc()
    alert_col = AsyncMock()
    alert_col.find_one = AsyncMock(return_value={
        "id": "a", "monitor_id": "m", "title": "t", "message": "m", "risk_level": "high",
    })
    # executions mock（与 writes_back_fields 一致，空 async iterator）
    exec_col = MagicMock()
    exec_cursor = MagicMock()
    exec_cursor.sort = MagicMock(return_value=exec_cursor)
    exec_cursor.limit = MagicMock(return_value=exec_cursor)
    class _EmptyAsyncIter:
        def __aiter__(self):
            return self
        async def __anext__(self):
            raise StopAsyncIteration
    exec_cursor.__aiter__ = lambda self: _EmptyAsyncIter()
    exec_col.find = MagicMock(return_value=exec_cursor)

    def getitem(k):
        if k == "alert_records":
            return alert_col
        if k == "executions":
            return exec_col
        return AsyncMock()
    db.__getitem__ = MagicMock(side_effect=getitem)

    async def mock_llm(*a, **kw):
        return ""
    svc._call_llm = mock_llm

    ok = await svc.assess_alert("a")
    assert ok is False


# ── _fire_alert 入队验证 ───────────────────────────────────

@pytest.mark.asyncio
async def test_fire_alert_enqueues_ai_assessment():
    """_fire_alert 告警触发后应入队 queue:alert_analyze。"""
    from monitor.monitor import MonitorService
    from models.dsl import MonitorDSL, ExecutionRecord, StepResult

    db = MagicMock()
    alert_col = AsyncMock()
    alert_col.insert_one = AsyncMock()
    exec_col = AsyncMock()
    monitor_col = AsyncMock()

    def getitem(k):
        return {
            "alert_records": alert_col, "executions": exec_col,
            "monitors": monitor_col, "api_dsls": AsyncMock(), "data_templates": AsyncMock(),
        }[k]
    db.__getitem__ = MagicMock(side_effect=getitem)

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()
    redis.rpush = AsyncMock()

    svc = MonitorService(db, redis)
    monitor = MonitorDSL(
        id="mon-1", name="test", api_id="api-1",
        risk_level=RiskLevel.HIGH, project_id="p1",
        alert_channels=["https://hook.example.com"],
    )
    record = ExecutionRecord(
        id="exec-1", api_id="api-1", type="single",
        steps=[StepResult(step_id="s", api_id="api-1", passed=False, error="timeout")],
        passed=False, failure_reason="timeout", project_id="p1",
    )

    # mock 渠道推送和去重，避免真实 HTTP
    with patch("monitor.monitor._push_channel", new=AsyncMock()):
        await svc._fire_alert(monitor, record, diff_info=None, is_recovery=False)

    # 应入队 AI 评估
    rpush_calls = [c for c in redis.rpush.call_args_list if len(c.args) >= 2 and "alert_analyze" in str(c.args[0])]
    assert len(rpush_calls) > 0, "应入队 queue:alert_analyze"
    # 验证载荷包含 alert_id
    payload = json.loads(rpush_calls[0].args[1])
    assert "alert_id" in payload


@pytest.mark.asyncio
async def test_fire_alert_recovery_does_not_enqueue_ai():
    """恢复通知（is_recovery=True）不应入队 AI 评估。"""
    from monitor.monitor import MonitorService
    from models.dsl import MonitorDSL, ExecutionRecord, StepResult

    db = MagicMock()
    alert_col = AsyncMock()
    alert_col.insert_one = AsyncMock()
    exec_col = AsyncMock()

    def getitem(k):
        return {
            "alert_records": alert_col, "executions": exec_col,
            "monitors": AsyncMock(), "api_dsls": AsyncMock(), "data_templates": AsyncMock(),
        }[k]
    db.__getitem__ = MagicMock(side_effect=getitem)

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()
    redis.rpush = AsyncMock()

    svc = MonitorService(db, redis)
    monitor = MonitorDSL(
        id="mon-1", name="test", api_id="api-1",
        risk_level=RiskLevel.MEDIUM, project_id="p1",
    )
    record = ExecutionRecord(
        id="exec-1", api_id="api-1", type="single",
        steps=[StepResult(step_id="s", api_id="api-1", passed=True)],
        passed=True, project_id="p1",
    )

    await svc._fire_alert(monitor, record, diff_info=None, is_recovery=True)

    # 恢复通知不应入队 AI 评估
    ai_enqueues = [c for c in redis.rpush.call_args_list
                   if len(c.args) >= 2 and "alert_analyze" in str(c.args[0])]
    assert len(ai_enqueues) == 0, "恢复通知不应入队 AI 评估"


# ── prompt 完整性 ──────────────────────────────────────────

def test_alert_system_contains_noise_severity():
    """_ALERT_SYSTEM 应包含 noise（误报）严重度指导。"""
    assert "noise" in _ALERT_SYSTEM, "应包含 noise 误报识别"
    assert "误报" in _ALERT_SYSTEM or "偶发" in _ALERT_SYSTEM, "应包含误报识别指导"


def test_alert_system_contains_severity_levels():
    """prompt 应列出所有 severity 级别。"""
    for level in ("critical", "high", "medium", "low", "noise"):
        assert level in _ALERT_SYSTEM, f"应包含 severity={level}"
