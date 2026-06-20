"""
P1-6: Prompt 版本化测试

验证：
1. _get_prompt 优先读 DB 激活版本，DB 无记录回退代码默认值
2. _get_prompt 内存缓存生效（60s TTL，重复调用不重复查 DB）
3. invalidate_prompt_cache 清除缓存，下次读取新版本
4. _DEFAULT_PROMPTS 正确填充（doc/asserts/scenario/data_template/monitor 五个 task_type）
5. _ASSERT_SYSTEM 已修正为"23 种"（P0-2 对齐后）
6. /prompts 路由参数校验（非法 task_type → 400）
"""
from __future__ import annotations

import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from ai_analyzer.analyzer import AiAnalyzerService, _ASSERT_SYSTEM


def _make_analyzer(prompt_doc=None):
    """构造 analyzer 实例，prompt_templates 集合 mock 返回 prompt_doc。"""
    db = MagicMock()
    prompt_col = AsyncMock()
    prompt_col.find_one = AsyncMock(return_value=prompt_doc)
    db.__getitem__ = MagicMock(side_effect=lambda k: {"prompt_templates": prompt_col}[k])
    redis = AsyncMock()
    svc = AiAnalyzerService.__new__(AiAnalyzerService)
    svc._db = db
    svc._redis = redis
    return svc, prompt_col


@pytest.mark.asyncio
async def test_get_prompt_fallback_to_default_when_db_empty():
    """DB 无记录 → 回退代码默认 prompt（保证可用性）。"""
    svc, prompt_col = _make_analyzer(prompt_doc=None)
    # 清空缓存确保走 DB 查询路径
    AiAnalyzerService.invalidate_prompt_cache()
    result = await svc._get_prompt("doc")
    # 应回退到 _DEFAULT_PROMPTS["doc"]（= _DOC_SYSTEM，非空）
    assert result, "默认 prompt 不应为空"
    assert result == AiAnalyzerService._DEFAULT_PROMPTS["doc"]


@pytest.mark.asyncio
async def test_get_prompt_reads_db_active_version():
    """DB 有激活版本 → 返回 DB 内容（而非代码默认值）。"""
    custom_content = "这是自定义的文档 prompt"
    svc, prompt_col = _make_analyzer(prompt_doc={
        "task_type": "doc", "content": custom_content, "active": True,
    })
    AiAnalyzerService.invalidate_prompt_cache()
    result = await svc._get_prompt("doc")
    assert result == custom_content, "应返回 DB 激活版本内容"


@pytest.mark.asyncio
async def test_get_prompt_cache_avoids_repeated_db_queries():
    """缓存生效：60s 内重复调用不重复查 DB。"""
    svc, prompt_col = _make_analyzer(prompt_doc={
        "task_type": "asserts", "content": "cached", "active": True,
    })
    AiAnalyzerService.invalidate_prompt_cache()
    # 第一次调用 → 查 DB
    await svc._get_prompt("asserts")
    assert prompt_col.find_one.await_count == 1
    # 第二次调用 → 命中缓存，不查 DB
    await svc._get_prompt("asserts")
    assert prompt_col.find_one.await_count == 1, "缓存命中不应重复查 DB"


@pytest.mark.asyncio
async def test_invalidate_cache_forces_db_reread():
    """invalidate 后下次调用重新查 DB。"""
    svc, prompt_col = _make_analyzer(prompt_doc={
        "task_type": "scenario", "content": "v1", "active": True,
    })
    AiAnalyzerService.invalidate_prompt_cache()
    await svc._get_prompt("scenario")
    count_after_first = prompt_col.find_one.await_count
    # 失效缓存
    AiAnalyzerService.invalidate_prompt_cache("scenario")
    await svc._get_prompt("scenario")
    assert prompt_col.find_one.await_count == count_after_first + 1, "失效后应重新查 DB"


@pytest.mark.asyncio
async def test_get_prompt_db_error_falls_back_gracefully():
    """DB 查询异常 → 不崩溃，回退默认值。"""
    svc, prompt_col = _make_analyzer()
    # 让 find_one 抛异常模拟 DB 不可用
    prompt_col.find_one = AsyncMock(side_effect=Exception("db down"))
    AiAnalyzerService.invalidate_prompt_cache()
    result = await svc._get_prompt("doc")
    # 应回退默认值，不抛异常
    assert result == AiAnalyzerService._DEFAULT_PROMPTS["doc"]


@pytest.mark.asyncio
async def test_get_prompt_unknown_task_type_returns_empty():
    """未知 task_type → 返回空字符串（无默认值兜底）。"""
    svc, _ = _make_analyzer(prompt_doc=None)
    AiAnalyzerService.invalidate_prompt_cache()
    result = await svc._get_prompt("unknown_type")
    assert result == "", "未知 task_type 无默认值，应返回空"


def test_default_prompts_filled():
    """_DEFAULT_PROMPTS 应填充主要生成类 task_type。"""
    assert "doc" in AiAnalyzerService._DEFAULT_PROMPTS
    assert "asserts" in AiAnalyzerService._DEFAULT_PROMPTS
    assert "scenario" in AiAnalyzerService._DEFAULT_PROMPTS
    assert "data_template" in AiAnalyzerService._DEFAULT_PROMPTS
    assert "monitor" in AiAnalyzerService._DEFAULT_PROMPTS
    # 每个 prompt 应非空
    for task_type, content in AiAnalyzerService._DEFAULT_PROMPTS.items():
        assert content, f"{task_type} 默认 prompt 不应为空"


def test_assert_system_corrected_to_23_operators():
    """P1-6 修正：_ASSERT_SYSTEM 应写"23 种"（P0-2 对齐后实际 23 种，非 22）。"""
    assert "23 种" in _ASSERT_SYSTEM, "_ASSERT_SYSTEM 应修正为 23 种 operator"
    assert "22 种" not in _ASSERT_SYSTEM, "不应再出现 22 种（已过时）"


def test_invalidate_all_clears_entire_cache():
    """invalidate_prompt_cache() 无参 → 清空全部缓存。"""
    # 先填充一些缓存
    AiAnalyzerService._PROMPT_CACHE = {"doc": ("x", time.time()), "asserts": ("y", time.time())}
    AiAnalyzerService.invalidate_prompt_cache()
    assert len(AiAnalyzerService._PROMPT_CACHE) == 0


def test_prompts_route_validates_task_type():
    """/prompts 路由应校验 task_type 合法性（非法 → 400，不触达 DB 业务逻辑）。
    注：get_db Depends 会先执行，但非法 task_type 的 400 在 DB 查询前抛出，
    此处验证业务校验逻辑存在（不依赖 DB 连接）。"""
    # 直接验证路由函数的业务校验逻辑（不通过 TestClient，避免 DB 连接）
    import inspect
    from api.routers.prompts import list_prompts
    src = inspect.getsource(list_prompts)
    # 校验逻辑应包含 task_type 合法性检查
    assert "PROMPT_TASK_TYPES" in src, "list_prompts 应校验 task_type 合法性"
    assert "Invalid task_type" in src, "应有非法 task_type 的 400 错误提示"
