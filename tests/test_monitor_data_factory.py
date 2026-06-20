"""
P0-3: 巡检 data_factory 分支测试

验证此前缺失的 data_factory 巡检分支现在正常工作：
1. _run_monitor 对 target_type='data_factory' 正确分发到 _run_monitor_data_factory
2. _run_monitor_data_factory 加载模板、造数、校验字段完整性
3. 异常值注入率校验：配置了注入率但 10 条全正常 → 判失败
4. 模板不存在 → 优雅跳过不崩溃
5. 构造的 ExecutionRecord 正确反映通过/失败状态
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from monitor.monitor import MonitorService
from models.dsl import (
    MonitorDSL, RiskLevel, FieldTemplate, DataTemplate,
)


def _make_monitor(target_id="tmpl-1") -> MonitorDSL:
    """构造 data_factory 类型的监控配置。"""
    return MonitorDSL(
        id="mon-df-1", name="数据工厂巡检",
        target_type="data_factory", target_id=target_id,
        interval="5m", risk_level=RiskLevel.MEDIUM,
        max_consecutive_failures=3, owner="tester",
    )


def _make_template(fields=None) -> DataTemplate:
    """构造数据模板，fields 默认两个正常字段。"""
    return DataTemplate(
        id="tmpl-1", name="用户数据模板", api_id="api-1",
        fields=fields or [
            FieldTemplate(name="username", faker_method="name"),
            FieldTemplate(name="email", faker_method="email"),
        ],
    )


def _make_svc(template_doc=None):
    """构造 MonitorService mock，data_templates 集合返回 template_doc。"""
    db = MagicMock()
    tmpl_col = AsyncMock()
    tmpl_col.find_one = AsyncMock(return_value=template_doc)
    exec_col = AsyncMock()
    exec_col.insert_one = AsyncMock()
    monitor_col = AsyncMock()

    def getitem(k):
        return {
            "data_templates": tmpl_col,
            "executions": exec_col,
            "monitors": monitor_col,
            "alert_records": AsyncMock(),
            "api_dsls": AsyncMock(),
        }[k]

    db.__getitem__ = MagicMock(side_effect=getitem)
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()
    svc = MonitorService(db, redis)
    return svc, tmpl_col, exec_col


@pytest.mark.asyncio
async def test_run_monitor_dispatches_data_factory():
    """_run_monitor 对 target_type=data_factory 应调用 _run_monitor_data_factory。
    _run_monitor 从 DB 加载 monitor 配置后分发，需 mock monitor_col 返回配置文档。"""
    template = _make_template()
    svc, tmpl_col, exec_col = _make_svc(template_doc=template.model_dump())
    monitor = _make_monitor()

    # mock monitors 集合返回 data_factory 类型的配置文档
    svc._db["monitors"].find_one = AsyncMock(return_value=monitor.model_dump())
    svc._evaluate_result = AsyncMock()

    # spy _run_monitor_data_factory 验证分发命中
    called = False
    original_method = svc._run_monitor_data_factory

    async def spy(m, mid):
        nonlocal called
        called = True
        return None

    svc._run_monitor_data_factory = spy

    await svc._run_monitor(monitor.id)
    assert called, "target_type=data_factory 应分发到 _run_monitor_data_factory"


@pytest.mark.asyncio
async def test_data_factory_monitor_generates_and_evaluates():
    """_run_monitor_data_factory 应造数、校验、构造 ExecutionRecord 并调用 _evaluate_result。"""
    template = _make_template()
    svc, tmpl_col, exec_col = _make_svc(template_doc=template.model_dump())
    monitor = _make_monitor()

    # mock DataFactory.generate 返回 10 条完整数据（字段齐全）
    fake_data = [{"username": f"user{i}", "email": f"u{i}@x.com"} for i in range(10)]
    svc._evaluate_result = AsyncMock()

    with patch("data_factory.factory.DataFactory") as MockFactory:
        instance = MockFactory.return_value
        instance.generate.return_value = fake_data
        await svc._run_monitor_data_factory(monitor, monitor.id)

    # 应插入一条执行记录
    exec_col.insert_one.assert_awaited_once()
    record_arg = exec_col.insert_one.call_args[0][0]
    assert record_arg["type"] == "data_factory"
    assert record_arg["passed"] is True, "字段齐全的造数应通过"
    assert record_arg["trigger"] == "monitor"
    # _evaluate_result 应被调用
    svc._evaluate_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_data_factory_monitor_detects_anomaly_injection_failure():
    """配置了 invalid_rate 但 10 条全正常 → 应判失败（注入逻辑失效告警）。"""
    # 字段配置 invalid_rate=0.5，但 mock 返回的数据全是正常值
    template = DataTemplate(
        id="tmpl-1", name="t", api_id="api-1",
        fields=[
            FieldTemplate(name="age", faker_method="random_int",
                          invalid_values=[-1, -999], invalid_rate=0.5),
        ],
    )
    svc, _, exec_col = _make_svc(template_doc=template.model_dump())
    monitor = _make_monitor()
    svc._evaluate_result = AsyncMock()

    # mock 返回的数据 age 全是正常值（无 -1/-999）→ 注入率失效
    fake_data = [{"age": 25} for _ in range(10)]

    with patch("data_factory.factory.DataFactory") as MockFactory:
        instance = MockFactory.return_value
        instance.generate.return_value = fake_data
        await svc._run_monitor_data_factory(monitor, monitor.id)

    record_arg = exec_col.insert_one.call_args[0][0]
    assert record_arg["passed"] is False, "配置注入率但无异常值应判失败"
    assert "anomaly" in record_arg["failure_reason"], "失败原因应提及注入失效"


@pytest.mark.asyncio
async def test_data_factory_monitor_detects_missing_fields():
    """生成的数据缺少模板定义的字段 → 应判失败（字段完整性）。"""
    template = _make_template(fields=[
        FieldTemplate(name="username", faker_method="name"),
        FieldTemplate(name="email", faker_method="email"),
    ])
    svc, _, exec_col = _make_svc(template_doc=template.model_dump())
    monitor = _make_monitor()
    svc._evaluate_result = AsyncMock()

    # mock 返回的数据缺少 email 字段
    fake_data = [{"username": f"u{i}"} for i in range(10)]

    with patch("data_factory.factory.DataFactory") as MockFactory:
        instance = MockFactory.return_value
        instance.generate.return_value = fake_data
        await svc._run_monitor_data_factory(monitor, monitor.id)

    record_arg = exec_col.insert_one.call_args[0][0]
    assert record_arg["passed"] is False
    assert "email" in record_arg["failure_reason"], "应报告缺失的 email 字段"


@pytest.mark.asyncio
async def test_data_factory_monitor_empty_generation_fails():
    """造数返回空列表 → 应判失败（严重故障）。"""
    template = _make_template()
    svc, _, exec_col = _make_svc(template_doc=template.model_dump())
    monitor = _make_monitor()
    svc._evaluate_result = AsyncMock()

    with patch("data_factory.factory.DataFactory") as MockFactory:
        instance = MockFactory.return_value
        instance.generate.return_value = []  # 空产出
        await svc._run_monitor_data_factory(monitor, monitor.id)

    record_arg = exec_col.insert_one.call_args[0][0]
    assert record_arg["passed"] is False
    assert "0 records" in record_arg["failure_reason"]


@pytest.mark.asyncio
async def test_data_factory_monitor_template_not_found_skips():
    """模板不存在（已删除）→ 应优雅跳过，不插入记录不崩溃。"""
    svc, tmpl_col, exec_col = _make_svc(template_doc=None)
    monitor = _make_monitor(target_id="deleted-tmpl")
    svc._evaluate_result = AsyncMock()

    await svc._run_monitor_data_factory(monitor, monitor.id)

    # 模板不存在 → 不应插入执行记录，不应调用评估
    exec_col.insert_one.assert_not_awaited()
    svc._evaluate_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_data_factory_monitor_anomaly_injection_passes_when_working():
    """配置 invalid_rate 且数据中确实有异常值 → 应通过（注入逻辑正常）。"""
    template = DataTemplate(
        id="tmpl-1", name="t", api_id="api-1",
        fields=[
            FieldTemplate(name="age", faker_method="random_int",
                          invalid_values=[-1], invalid_rate=0.5),
        ],
    )
    svc, _, exec_col = _make_svc(template_doc=template.model_dump())
    monitor = _make_monitor()
    svc._evaluate_result = AsyncMock()

    # mock 返回的数据中有异常值（-1）→ 注入正常
    fake_data = [{"age": 25}, {"age": -1}, {"age": 30}, {"age": -1}] + [{"age": 25}] * 6

    with patch("data_factory.factory.DataFactory") as MockFactory:
        instance = MockFactory.return_value
        instance.generate.return_value = fake_data
        await svc._run_monitor_data_factory(monitor, monitor.id)

    record_arg = exec_col.insert_one.call_args[0][0]
    assert record_arg["passed"] is True, "有异常值产出说明注入正常，应通过"
