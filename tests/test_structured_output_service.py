"""
AI 结构化输出校验测试。

覆盖 parse → validate → normalize 的通用入口，避免坏 JSON 或 schema 不匹配结果进入审核/回写链路。
"""
from __future__ import annotations

import json

import pytest

from services.structured_output_service import StructuredOutputError, parse_structured_output


def test_parse_doc_accepts_markdown_json_and_normalizes():
    raw = """```json
{"summary":"登录","params":[{"name":"username","location":"body","type":"string"}],"response_fields":[]}
```"""
    doc = parse_structured_output("doc", raw)
    assert doc["summary"] == "登录"
    assert doc["params"][0]["required"] is False


def test_parse_asserts_filters_invalid_items_and_rejects_empty():
    raw = json.dumps([
        {"field": "status_code", "operator": "eq", "expected": 200, "risk_level": "critical"},
        {"field": "$.code", "operator": "bad_op", "expected": 0},
        {"operator": "eq"},
    ])
    rules = parse_structured_output("asserts", raw)
    assert len(rules) == 1
    assert rules[0]["operator"] == "eq"

    with pytest.raises(StructuredOutputError):
        parse_structured_output("asserts", json.dumps([{"operator": "eq"}]))


def test_parse_data_template_clamps_rates_and_keeps_valid_fields():
    result = parse_structured_output("data_template", json.dumps({
        "fields": [
            {"name": "email", "faker_method": "email", "invalid_rate": 1.5, "null_rate": -0.5},
            {"faker_method": "missing_name"},
        ],
        "summary": "增强字段",
    }))
    assert len(result["fields"]) == 1
    assert result["fields"][0]["invalid_rate"] == 1.0
    assert result["fields"][0]["null_rate"] == 0.0


def test_parse_monitor_requires_monitor_list():
    with pytest.raises(StructuredOutputError) as exc:
        parse_structured_output("monitor", json.dumps({"summary": "no monitors"}))
    assert "monitor list" in str(exc.value)


def test_parse_diff_rejects_unknown_root_cause_with_raw_preview():
    raw = json.dumps({"root_cause": "maybe", "severity": "low", "reasoning": "x"})
    with pytest.raises(StructuredOutputError) as exc:
        parse_structured_output("diff", raw)
    assert "invalid root_cause" in str(exc.value)
    assert exc.value.raw_output_preview


def test_parse_diagnose_and_alert_normalize_confidence():
    diagnosis = parse_structured_output("diagnose", json.dumps({
        "root_cause": "api_change",
        "explanation": "响应字段变化",
        "suggested_fix": "重新分析接口",
        "confidence": 1.8,
    }))
    assert diagnosis["confidence"] == 1.0

    alert = parse_structured_output("alert", json.dumps({
        "severity": "NOISE",
        "root_cause": "偶发超时",
        "confidence": -1,
    }))
    assert alert["severity"] == "noise"
    assert alert["confidence"] == 0.0
