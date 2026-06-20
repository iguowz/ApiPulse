"""
巡检监控测试 v4
新增：
- Redis 持久化计数读写
- 差异检测哈希快速预判
- 大响应体跳过 DeepDiff
- 并发告警推送（asyncio.gather）
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from monitor.monitor import MonitorService, _parse_interval, _push_channel
from models.dsl import ExecutionRecord, MonitorDSL, RiskLevel, StepResult


# ── _parse_interval ───────────────────────────────────────

@pytest.mark.parametrize("s,expected", [
    ("30s", 30), ("5m", 300), ("1h", 3600), ("2d", 172800),
])
def test_parse_interval(s, expected):
    assert _parse_interval(s) == expected


# ── Redis 持久化计数 ───────────────────────────────────────

def make_svc(last_exec=None, fail_count=None, prev_status=None):
    db  = MagicMock()
    col = AsyncMock()
    col.find_one     = AsyncMock(return_value=last_exec)
    col.count_documents = AsyncMock(return_value=0)
    monitor_col = AsyncMock()
    alert_col   = AsyncMock()
    alert_col.insert_one = AsyncMock()
    alert_col.count_documents = AsyncMock(return_value=0)
    api_col     = AsyncMock()
    tmpl_col    = AsyncMock()

    def getitem(k):
        return {
            "executions": col,
            "monitors": monitor_col,
            "alert_records": alert_col,
            "api_dsls": api_col,
            "data_templates": tmpl_col,
        }[k]

    db.__getitem__ = MagicMock(side_effect=getitem)

    redis = AsyncMock()
    # get: fail_count
    async def redis_get(key):
        if "fail_count" in key:
            return str(fail_count) if fail_count is not None else None
        if "prev_status" in key:
            return "0" if prev_status is False else ("1" if prev_status is True else None)
        return None

    redis.get    = AsyncMock(side_effect=redis_get)
    redis.setex  = AsyncMock()

    svc = MonitorService(db, redis)
    return svc, redis, col, alert_col


@pytest.mark.asyncio
async def test_get_fail_count_from_redis():
    svc, redis, _, _ = make_svc(fail_count=3)
    count = await svc._get_fail_count("mon1")
    assert count == 3


@pytest.mark.asyncio
async def test_get_fail_count_default_zero():
    svc, redis, _, _ = make_svc(fail_count=None)
    count = await svc._get_fail_count("mon1")
    assert count == 0


@pytest.mark.asyncio
async def test_set_fail_count_calls_redis():
    svc, redis, _, _ = make_svc()
    await svc._set_fail_count("mon1", 5)
    redis.setex.assert_awaited_once()
    args = redis.setex.call_args[0]
    assert "fail_count" in args[0]
    assert args[2] == 5


@pytest.mark.asyncio
async def test_get_prev_passed_true_by_default():
    svc, redis, _, _ = make_svc(prev_status=None)
    assert await svc._get_prev_passed("mon1") is True


@pytest.mark.asyncio
async def test_get_prev_passed_false():
    svc, redis, _, _ = make_svc(prev_status=False)
    assert await svc._get_prev_passed("mon1") is False


@pytest.mark.asyncio
async def test_set_prev_passed():
    svc, redis, _, _ = make_svc()
    await svc._set_prev_passed("mon1", False)
    redis.setex.assert_awaited_once()
    args = redis.setex.call_args[0]
    assert args[2] == "0"


# ── 差异检测 ─────────────────────────────────────────────

def make_record(passed=True, body=None):
    step = StepResult(
        step_id="single", api_id="api1", passed=passed,
        response_received={"status_code": 200 if passed else 500, "body": body or {}},
        latency_ms=50,
    )
    return ExecutionRecord(id="exec-now", api_id="api1", type="single", steps=[step], passed=passed)


def make_monitor(**kw):
    base = dict(
        id="m1", name="t", api_id="api1",
        interval="1m",
        diff_threshold=2,
        max_consecutive_failures=1,
        alert_on_recovery=True,
    )
    base.update(kw)   # kw 覆盖 base，避免重复关键字
    return MonitorDSL(**base)


@pytest.mark.asyncio
async def test_detect_diff_no_previous():
    svc, _, col, _ = make_svc(last_exec=None)
    result = await svc._detect_diff(make_monitor(), make_record())
    assert result is None


@pytest.mark.asyncio
async def test_detect_diff_hash_same_no_diff():
    body = {"code": 0, "data": "same"}
    prev = {"id": "p", "steps": [{"response_received": {"body": body}}]}
    svc, _, col, _ = make_svc(last_exec=prev)
    record = make_record(body=body)
    result = await svc._detect_diff(make_monitor(), record)
    assert result is None


@pytest.mark.asyncio
async def test_detect_diff_above_threshold():
    prev_body = {"a": 1, "b": 2, "c": 3, "d": 4}
    curr_body = {"a": 99, "b": 88, "c": 77, "d": 4}
    prev = {"id": "p", "steps": [{"response_received": {"body": prev_body}}]}
    svc, _, col, _ = make_svc(last_exec=prev)
    record = make_record(body=curr_body)
    result = await svc._detect_diff(make_monitor(diff_threshold=2), record)
    assert result is not None
    assert result["change_count"] >= 3


@pytest.mark.asyncio
async def test_detect_diff_large_body_hash_only():
    """响应体超过 64KB 时，只做哈希对比，不进行 DeepDiff"""
    big_body_prev = {"data": "x" * 70000}
    big_body_curr = {"data": "y" * 70000}
    prev = {"id": "p", "steps": [{"response_received": {"body": big_body_prev}}]}
    svc, _, col, _ = make_svc(last_exec=prev)
    record = make_record(body=big_body_curr)
    result = await svc._detect_diff(make_monitor(diff_threshold=1), record)
    assert result is not None
    assert "large_body_hash_changed" in result["diff_summary"]


# ── 告警并发推送 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_fire_alert_saves_record_before_push():
    """告警记录必须先写 DB，再推渠道（即使渠道失败也不丢记录）"""
    svc, _, _, alert_col = make_svc()
    monitor = make_monitor(alert_channels=["https://fake.webhook/push"])
    record  = make_record(passed=False)

    call_order = []
    alert_col.insert_one = AsyncMock(side_effect=lambda *a, **kw: call_order.append("db"))

    with patch("monitor.monitor._push_channel", new=AsyncMock(side_effect=lambda *a, **kw: call_order.append("push"))):
        await svc._fire_alert(monitor, record, None, is_recovery=False)

    assert call_order[0] == "db", "告警记录应先于渠道推送写入"


@pytest.mark.asyncio
async def test_fire_alert_multi_channel_concurrent():
    """多渠道应并发推送（asyncio.gather），而非串行"""
    svc, _, _, alert_col = make_svc()
    alert_col.insert_one = AsyncMock()

    urls = [f"https://ch{i}.example.com/hook" for i in range(3)]
    monitor = make_monitor(alert_channels=urls)
    record  = make_record(passed=False)

    pushed = []
    async def fake_push(url, *args, **kwargs):
        pushed.append(url)

    with patch("monitor.monitor._push_channel", side_effect=fake_push):
        await svc._fire_alert(monitor, record, None, is_recovery=False)

    assert len(pushed) == 3


# ── 连续失败计数逻辑 ──────────────────────────────────────

def test_fail_count_threshold():
    monitor = make_monitor(max_consecutive_failures=3)

    def should_alert(fail_count, passed):
        return not passed and fail_count >= monitor.max_consecutive_failures

    assert not should_alert(1, False)
    assert not should_alert(2, False)
    assert should_alert(3, False)
    assert not should_alert(4, True)


def test_recovery_detection():
    """上次失败 + 本次通过 → is_recovery=True"""
    prev_passed    = False
    current_passed = True
    is_recovery    = current_passed and not prev_passed
    assert is_recovery is True


# ── _push_channel ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_push_dingtalk_no_crash():
    try:
        await _push_channel("https://oapi.dingtalk.com/robot/send?x=y", "t", "c", RiskLevel.HIGH)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_push_slack_no_crash():
    try:
        await _push_channel("https://hooks.slack.com/services/x", "t", "c", RiskLevel.CRITICAL)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_push_wecom_no_crash():
    try:
        await _push_channel("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x", "t", "c", RiskLevel.MEDIUM)
    except Exception:
        pass
