from __future__ import annotations

import json
from pathlib import Path

from api.routers.ai_chat import _structured_chat_generation_content
from models.generation_version import GenerationType
from services.structured_output_service import parse_structured_output


def _samples() -> dict:
    path = Path(__file__).with_name("golden_ai_samples.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_golden_doc_sample_contract():
    sample = _samples()["doc"]
    parsed = parse_structured_output("doc", json.dumps(sample["raw"], ensure_ascii=False))
    assert parsed["summary"] == sample["expect"]["summary"]
    assert len(parsed["params"]) == sample["expect"]["param_count"]
    assert len(parsed["response_fields"]) == sample["expect"]["response_field_count"]


def test_golden_asserts_sample_contract():
    sample = _samples()["asserts"]
    parsed = parse_structured_output("asserts", json.dumps(sample["raw"], ensure_ascii=False))
    assert len(parsed) == sample["expect"]["count"]
    assert parsed[0]["field"] == sample["expect"]["first_field"]


def test_golden_scenario_sample_contract():
    sample = _samples()["scenario"]
    parsed = parse_structured_output("scenario", json.dumps(sample["raw"], ensure_ascii=False))
    assert len(parsed) == sample["expect"]["count"]
    assert parsed[0]["name"] == sample["expect"]["first_name"]


def test_golden_data_template_sample_contract():
    sample = _samples()["data_template"]
    parsed = parse_structured_output("data_template", json.dumps(sample["raw"], ensure_ascii=False))
    assert len(parsed["fields"]) == sample["expect"]["field_count"]
    assert parsed["fields"][0]["name"] == sample["expect"]["first_name"]


def test_golden_monitor_sample_contract():
    sample = _samples()["monitor"]
    parsed = parse_structured_output("monitor", json.dumps(sample["raw"], ensure_ascii=False))
    assert len(parsed["monitors"]) == sample["expect"]["count"]
    assert parsed["monitors"][0]["target_type"] == sample["expect"]["target_type"]


def test_golden_diff_sample_contract():
    sample = _samples()["diff"]
    parsed = parse_structured_output("diff", json.dumps(sample["raw"], ensure_ascii=False))
    assert parsed["root_cause"] == sample["expect"]["root_cause"]
    assert parsed["severity"] == sample["expect"]["severity"]
    assert bool(parsed.get("fix_suggestion")) is sample["expect"]["has_fix"]


def test_golden_doc_fix_sample_contract():
    sample = _samples()["doc_fix"]
    parsed = parse_structured_output("doc_fix", json.dumps(sample["raw"], ensure_ascii=False))
    assert len(parsed["params"]) == sample["expect"]["param_count"]
    assert len(parsed["response_fields"]) == sample["expect"]["response_field_count"]


def test_golden_diagnose_sample_contract():
    sample = _samples()["diagnose"]
    parsed = parse_structured_output("diagnose", json.dumps(sample["raw"], ensure_ascii=False))
    assert parsed["root_cause"] == sample["expect"]["root_cause"]
    assert parsed["confidence"] == sample["expect"]["confidence"]


def test_golden_chat_structured_content_contract():
    sample = _samples()["chat"]
    raw = sample["raw"]
    gen_type = GenerationType(raw["gen_type"])
    content = _structured_chat_generation_content(gen_type, raw["assistant_response"], raw["user_message"])
    assert content["summary"] == sample["expect"]["summary"]
    assert ("chat_suggestion" in content) is sample["expect"]["fallback"]
