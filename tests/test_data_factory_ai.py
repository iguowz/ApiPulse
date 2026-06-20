"""
P1-1: 数据工厂 AI 化测试

验证：
1. infer_data_template 正确加载模板 + API 样本，调用 LLM，生成 GenerationVersion(data_template)
2. infer_data_template 字段校验：过滤缺 name 的无效字段，rate 钳制到 [0,1]
3. infer_data_template 模板不存在 → 返回 None
4. infer_data_template LLM 空响应 → 返回 None
5. apply_version 的 data_template 分支：审核通过后更新 data_templates 集合
6. apply_version data_template 支持字段级部分接受
7. _DATA_TEMPLATE_SYSTEM prompt 包含边界值/异常值指导
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ai_analyzer.analyzer import AiAnalyzerService, _DATA_TEMPLATE_SYSTEM, _safe_truncate_json
from models.generation_version import GenerationType, GenerationStatus
from services.structured_output_service import StructuredOutputError


def _make_analyzer(template_doc=None, api_doc=None, llm_response=None, generation_insert_id="gv-1"):
    """构造 analyzer mock：data_templates/api_dsls/generation_versions 集合 + LLM mock。"""
    db = MagicMock()
    tmpl_col = AsyncMock()
    tmpl_col.find_one = AsyncMock(return_value=template_doc)
    tmpl_col.update_one = AsyncMock(matched_count=1)
    api_col = AsyncMock()
    api_col.find_one = AsyncMock(return_value=api_doc)
    gen_col = AsyncMock()
    gen_col.insert_one = AsyncMock(return_value=MagicMock(inserted_id=generation_insert_id))

    def getitem(k):
        return {"data_templates": tmpl_col, "api_dsls": api_col, "generation_versions": gen_col}[k]
    db.__getitem__ = MagicMock(side_effect=getitem)

    redis = AsyncMock()
    svc = AiAnalyzerService.__new__(AiAnalyzerService)
    svc._db = db
    svc._redis = redis
    svc._model = "gpt-4o-mini"
    svc._api_col = api_col
    svc._generation_col = gen_col

    # mock _call_llm_stream 返回预设响应（跳过真实 LLM）
    async def mock_stream(*args, **kwargs):
        return llm_response or ""
    svc._call_llm_stream = mock_stream
    # mock _broadcast 避免 WS 报错
    svc._broadcast = AsyncMock()
    return svc, tmpl_col, gen_col


@pytest.mark.asyncio
async def test_infer_data_template_generates_generation_version():
    """正常流程：模板 + 样本 → LLM → GenerationVersion(data_template)。"""
    template_doc = {
        "id": "tmpl-1", "name": "用户数据", "api_id": "api-1",
        "fields": [{"name": "email", "faker_method": "word"}],
    }
    api_doc = {
        "id": "api-1", "project_id": "proj-1",
        "request": {"method": "POST", "path": "/login", "body": {"email": "test@x.com"}},
        "response": {"body": {"code": 0, "data": {"id": 1}}},
    }
    llm_response = json.dumps({
        "fields": [
            {"name": "email", "faker_method": "email", "invalid_values": ["not_an_email"], "invalid_rate": 0.1},
            {"name": "id", "faker_method": "random_int", "boundary_min": 1, "boundary_max": 9999},
        ],
        "summary": "增强 email 和 id 字段",
    })
    svc, _, gen_col = _make_analyzer(template_doc, api_doc, llm_response)
    gv_id = await svc.infer_data_template("tmpl-1", "proj-1")
    assert gv_id == "gv-1"
    # 应保存 GenerationVersion
    gen_col.insert_one.assert_awaited_once()
    saved = gen_col.insert_one.call_args[0][0]
    assert saved["type"] == GenerationType.DATA_TEMPLATE.value
    assert saved["status"] == GenerationStatus.PENDING_REVIEW.value
    assert len(saved["content"]["fields"]) == 2


@pytest.mark.asyncio
async def test_infer_data_template_filters_invalid_fields():
    """缺 name 的字段应被过滤，rate 钳制到 [0,1]。"""
    template_doc = {"id": "t", "name": "t", "api_id": "a", "fields": []}
    api_doc = {"id": "a", "request": {"body": {}}, "response": {}}
    llm_response = json.dumps({
        "fields": [
            {"name": "valid_field", "faker_method": "name"},
            {"faker_method": "no_name"},  # 缺 name → 过滤
            {"name": "bad_rate", "invalid_rate": 1.5, "null_rate": -0.5},  # rate 钳制
        ],
        "summary": "test",
    })
    svc, _, gen_col = _make_analyzer(template_doc, api_doc, llm_response)
    await svc.infer_data_template("t")
    saved = gen_col.insert_one.call_args[0][0]
    fields = saved["content"]["fields"]
    assert len(fields) == 2, "应过滤掉缺 name 的字段"
    # rate 应被钳制
    bad_rate_field = next(f for f in fields if f["name"] == "bad_rate")
    assert bad_rate_field["invalid_rate"] == 1.0, "invalid_rate 应钳制到 1.0"
    assert bad_rate_field["null_rate"] == 0.0, "null_rate 应钳制到 0.0"


@pytest.mark.asyncio
async def test_infer_data_template_template_not_found():
    """模板不存在 → 返回 None。"""
    svc, _, gen_col = _make_analyzer(template_doc=None)
    result = await svc.infer_data_template("missing-tmpl")
    assert result is None
    gen_col.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_infer_data_template_empty_llm_response():
    """LLM 空响应 → 返回 None，不创建 GenerationVersion。"""
    template_doc = {"id": "t", "name": "t", "api_id": "a", "fields": [{"name": "x"}]}
    api_doc = {"id": "a", "request": {"body": {}}, "response": {}}
    svc, _, gen_col = _make_analyzer(template_doc, api_doc, llm_response="")
    result = await svc.infer_data_template("t")
    assert result is None
    gen_col.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_infer_data_template_no_fields_in_response():
    """LLM 返回但无 fields → 抛结构化错误，交给 worker 重试/DLQ。"""
    template_doc = {"id": "t", "name": "t", "api_id": "a", "fields": [{"name": "x"}]}
    api_doc = {"id": "a", "request": {"body": {}}, "response": {}}
    llm_response = json.dumps({"summary": "no fields here"})
    svc, _, gen_col = _make_analyzer(template_doc, api_doc, llm_response)
    with pytest.raises(StructuredOutputError):
        await svc.infer_data_template("t")
    gen_col.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_version_data_template_updates_collection():
    """apply_version 的 data_template 分支：审核通过后更新 data_templates 集合。"""
    from bson import ObjectId
    oid = ObjectId()
    gv_doc = {
        "_id": oid, "id": str(oid),
        "api_id": "api-1", "project_id": "proj-1",
        "type": GenerationType.DATA_TEMPLATE.value,
        "status": GenerationStatus.PENDING_REVIEW.value,
        "content": {
            "template_id": "tmpl-1", "name": "AI 增强",
            "fields": [{"name": "email", "faker_method": "email"}],
            "description": "AI 增强",
        },
    }
    db = MagicMock()
    gen_col = AsyncMock()
    gen_col.find_one = AsyncMock(return_value=gv_doc)
    gen_col.update_one = AsyncMock()
    tmpl_col = AsyncMock()
    tmpl_col.find_one = AsyncMock(return_value={"id": "tmpl-1", "fields": [{"name": "old", "faker_method": "word"}]})
    tmpl_col.update_one = AsyncMock(matched_count=1)

    def getitem(k):
        return {"generation_versions": gen_col, "data_templates": tmpl_col, "api_dsls": AsyncMock()}[k]
    db.__getitem__ = MagicMock(side_effect=getitem)

    svc = AiAnalyzerService.__new__(AiAnalyzerService)
    svc._db = db
    svc._generation_col = gen_col
    svc._api_col = AsyncMock()
    svc._broadcast = AsyncMock()

    ok = await svc.apply_version(str(oid), reviewer_id="user1")
    assert ok is True
    # 应更新 data_templates 集合
    tmpl_col.update_one.assert_awaited_once()
    call_args = tmpl_col.update_one.call_args[0]
    assert call_args[0] == {"id": "tmpl-1"}
    assert "fields" in call_args[1]["$set"]


@pytest.mark.asyncio
async def test_apply_version_data_template_partial_fields():
    """data_template 支持字段级部分接受（partial_fields 过滤）。"""
    from bson import ObjectId
    oid = ObjectId()
    gv_doc = {
        "_id": oid, "id": str(oid),
        "api_id": "api-1", "project_id": "p",
        "type": GenerationType.DATA_TEMPLATE.value,
        "status": GenerationStatus.PENDING_REVIEW.value,
        "content": {
            "template_id": "tmpl-1", "name": "t",
            "fields": [
                {"name": "email", "faker_method": "email"},
                {"name": "phone", "faker_method": "phone_number"},
                {"name": "id", "faker_method": "random_int"},
            ],
        },
    }
    db = MagicMock()
    gen_col = AsyncMock()
    gen_col.find_one = AsyncMock(return_value=gv_doc)
    gen_col.update_one = AsyncMock()
    tmpl_col = AsyncMock()
    tmpl_col.find_one = AsyncMock(return_value={"id": "tmpl-1", "fields": []})
    tmpl_col.update_one = AsyncMock(matched_count=1)

    def getitem(k):
        return {"generation_versions": gen_col, "data_templates": tmpl_col, "api_dsls": AsyncMock()}[k]
    db.__getitem__ = MagicMock(side_effect=getitem)

    svc = AiAnalyzerService.__new__(AiAnalyzerService)
    svc._db = db
    svc._generation_col = gen_col
    svc._api_col = AsyncMock()
    svc._broadcast = AsyncMock()

    # 仅接受 email 和 id 字段
    ok = await svc.apply_version(str(oid), reviewer_id="u", partial_fields=["email", "id"])
    assert ok is True
    # 验证只应用了 2 个字段
    call_args = tmpl_col.update_one.call_args[0]
    applied_fields = call_args[1]["$set"]["fields"]
    assert len(applied_fields) == 2
    applied_names = {f["name"] for f in applied_fields}
    assert applied_names == {"email", "id"}


def test_data_template_prompt_contains_guidance():
    """_DATA_TEMPLATE_SYSTEM 应包含边界值和异常值的指导（AI 增强核心能力）。"""
    assert "boundary_min" in _DATA_TEMPLATE_SYSTEM, "应指导生成边界值"
    assert "invalid_values" in _DATA_TEMPLATE_SYSTEM, "应指导生成异常值候选"
    assert "invalid_rate" in _DATA_TEMPLATE_SYSTEM, "应指导设置注入率"
    # 应有具体的异常值示例（email/phone/id）
    assert "not_an_email" in _DATA_TEMPLATE_SYSTEM or "email" in _DATA_TEMPLATE_SYSTEM.lower()


def test_safe_truncate_json_handles_large_objects():
    """_safe_truncate_json 应截断大对象，小对象原样返回。"""
    small = {"a": 1, "b": "hello"}
    assert _safe_truncate_json(small, 1000) == small
    large = {"data": "x" * 5000}
    result = _safe_truncate_json(large, 100)
    assert isinstance(result, str)
    assert "(truncated)" in result
    assert len(result) <= 120  # 截断后含提示


def test_safe_truncate_json_none():
    """None 输入应返回 None。"""
    assert _safe_truncate_json(None) is None
