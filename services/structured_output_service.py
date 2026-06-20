"""
AI 结构化输出校验服务。

LLM 输出先经过 safe_parse_json，再按 task_type 做业务 schema 校验和标准化。
失败时抛出 StructuredOutputError，并保留 raw_output_preview，供 worker 重试/DLQ 可见化排查。
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from ai_analyzer.utils import safe_parse_json
from models.dsl import (
    ASSERT_OPERATORS,
    ApiDoc,
    AssertRule,
    FieldTemplate,
    MonitorDSL,
    RiskLevel,
)


_ASSERT_OPERATOR_SET = {item["op"] for item in ASSERT_OPERATORS}
_DIFF_ROOT_CAUSES = {"ai_doc_error", "api_evolution", "api_breaking_change", "api_new_field"}
_DIAGNOSE_ROOT_CAUSES = {"env_mismatch", "timeout", "assertion_error", "api_change", "data_issue", "unknown"}
_SEVERITIES = {"low", "medium", "high", "critical"}
_ALERT_SEVERITIES = _SEVERITIES | {"noise"}


class StructuredOutputError(ValueError):
    """结构化输出解析/校验失败，携带原始输出摘要。"""

    def __init__(self, task_type: str, message: str, raw_output: str = "", parsed: Any = None):
        super().__init__(message)
        self.task_type = task_type
        self.raw_output_preview = _preview(raw_output)
        self.parsed_preview = _preview(json.dumps(parsed, ensure_ascii=False, default=str)) if parsed is not None else ""


class DataTemplateOutput(BaseModel):
    fields: list[FieldTemplate] = Field(default_factory=list)
    summary: str = ""


class StepRecommendOutput(BaseModel):
    asserts: list[AssertRule] = Field(default_factory=list)
    extract: dict[str, str] = Field(default_factory=dict)
    summary: str = ""


class DiffEvaluationOutput(BaseModel):
    is_valid: bool = True
    root_cause: str = "api_evolution"
    severity: str = "low"
    reasoning: str = ""
    fix_suggestion: dict[str, Any] | None = None

    @field_validator("root_cause")
    @classmethod
    def _valid_root_cause(cls, value: str) -> str:
        value = str(value or "").strip()
        if value not in _DIFF_ROOT_CAUSES:
            raise ValueError(f"invalid root_cause: {value}")
        return value

    @field_validator("severity")
    @classmethod
    def _valid_severity(cls, value: str) -> str:
        value = str(value or "").lower().strip()
        if value not in _SEVERITIES:
            raise ValueError(f"invalid severity: {value}")
        return value


class DiagnosisOutput(BaseModel):
    root_cause: str = "unknown"
    explanation: str = "AI 无法确定具体原因"
    suggested_fix: str = "请人工排查执行日志"
    confidence: float = 0.5

    @field_validator("root_cause")
    @classmethod
    def _valid_root_cause(cls, value: str) -> str:
        value = str(value or "unknown").strip()
        return value if value in _DIAGNOSE_ROOT_CAUSES else "unknown"

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.5
        return max(0.0, min(1.0, number))


class AlertAssessmentOutput(BaseModel):
    severity: str
    root_cause: str = ""
    confidence: float = 0.0

    @field_validator("severity")
    @classmethod
    def _valid_severity(cls, value: str) -> str:
        value = str(value or "").lower().strip()
        if value not in _ALERT_SEVERITIES:
            raise ValueError(f"invalid severity: {value}")
        return value

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        return max(0.0, min(1.0, number))


def parse_structured_output(task_type: str, raw_output: str) -> Any:
    """
    解析并校验 LLM 输出。

    分支说明：每个 task_type 对应一个可审核或可回写的业务对象；这里集中校验，避免各 worker
    自己容错导致坏 JSON 被静默保存成空 GenerationVersion。
    """
    if not raw_output or not raw_output.strip():
        raise StructuredOutputError(task_type, "LLM returned empty structured output", raw_output)

    try:
        parsed = safe_parse_json(raw_output)
    except Exception as exc:
        raise StructuredOutputError(task_type, f"failed to parse JSON: {exc}", raw_output) from exc

    try:
        if task_type == "doc":
            return _parse_doc(parsed, raw_output)
        if task_type == "asserts":
            return _parse_asserts(parsed, raw_output)
        if task_type == "step_recommend":
            return _parse_step_recommend(parsed, raw_output)
        if task_type == "scenario":
            return _parse_scenarios(parsed, raw_output)
        if task_type == "data_template":
            return _parse_data_template(parsed, raw_output)
        if task_type == "monitor":
            return _parse_monitors(parsed, raw_output)
        if task_type == "diff":
            return _parse_diff(parsed, raw_output)
        if task_type == "doc_fix":
            return _parse_doc_fix(parsed, raw_output)
        if task_type == "diagnose":
            return _parse_diagnosis(parsed, raw_output)
        if task_type == "alert":
            return _parse_alert(parsed, raw_output)
    except ValidationError as exc:
        raise StructuredOutputError(task_type, _validation_message(exc), raw_output, parsed) from exc
    except ValueError as exc:
        raise StructuredOutputError(task_type, str(exc), raw_output, parsed) from exc

    raise StructuredOutputError(task_type, f"unsupported structured output type: {task_type}", raw_output, parsed)


def _parse_doc(parsed: Any, raw: str) -> dict[str, Any]:
    if isinstance(parsed, list):
        if parsed and isinstance(parsed[0], dict):
            parsed = parsed[0]
        else:
            raise StructuredOutputError("doc", "doc output list has no usable object", raw, parsed)
    if not isinstance(parsed, dict):
        raise ValueError(f"doc output must be object, got {type(parsed).__name__}")
    if not parsed.get("summary") and not parsed.get("params"):
        raise ValueError("doc output has no summary or params")
    doc = ApiDoc(**parsed)
    return doc.model_dump()


def _parse_asserts(parsed: Any, raw: str) -> list[dict[str, Any]]:
    if not isinstance(parsed, list):
        raise ValueError(f"asserts output must be list, got {type(parsed).__name__}")
    rules = _collect_assert_rules(parsed)
    if not rules:
        raise ValueError("asserts output has no valid rules")
    return rules


def _parse_step_recommend(parsed: Any, raw: str) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        raise ValueError(f"step recommendation output must be object, got {type(parsed).__name__}")
    rules = _collect_assert_rules(parsed.get("asserts") or [])
    extract: dict[str, str] = {}
    raw_extract = parsed.get("extract") or {}
    if isinstance(raw_extract, dict):
        for key, value in raw_extract.items():
            if key and value:
                extract[str(key)] = str(value)
    output = StepRecommendOutput(asserts=[AssertRule(**r) for r in rules], extract=extract, summary=str(parsed.get("summary", ""))[:500])
    return {
        "asserts": [rule.model_dump() for rule in output.asserts],
        "extract": output.extract,
        "summary": output.summary,
    }


def _parse_scenarios(parsed: Any, raw: str) -> list[dict[str, Any]]:
    items = [parsed] if isinstance(parsed, dict) else parsed
    if not isinstance(items, list) or not items:
        raise ValueError("scenario output must be a non-empty object/list")
    scenarios: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        scene = dict(item)
        scene["name"] = scene.get("name") or "未命名场景"
        scene["description"] = scene.get("description", "")
        scene["steps"] = scene.get("steps") if isinstance(scene.get("steps"), list) else []
        scene["coverage_tags"] = scene.get("coverage_tags") or scene.get("tags") or []
        scenarios.append(scene)
    if not scenarios:
        raise ValueError("scenario output has no valid scenarios")
    return scenarios


def _parse_data_template(parsed: Any, raw: str) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        raise ValueError(f"data_template output must be object, got {type(parsed).__name__}")
    fields = [FieldTemplate(**field).model_dump() for field in (parsed.get("fields") or []) if isinstance(field, dict) and field.get("name")]
    if not fields:
        raise ValueError("data_template output has no valid fields")
    output = DataTemplateOutput(fields=[FieldTemplate(**field) for field in fields], summary=str(parsed.get("summary", ""))[:500])
    return {"fields": [field.model_dump() for field in output.fields], "summary": output.summary}


def _parse_monitors(parsed: Any, raw: str) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        raise ValueError(f"monitor output must be object, got {type(parsed).__name__}")
    raw_monitors = parsed.get("monitors")
    if not isinstance(raw_monitors, list) or not raw_monitors:
        raise ValueError("monitor output has no monitor list")
    monitors = []
    for item in raw_monitors:
        if not isinstance(item, dict):
            continue
        monitor = MonitorDSL(
            name=item.get("name") or "AI 巡检",
            target_type=item.get("target_type") or ("api" if item.get("api_id") else "api"),
            target_id=item.get("target_id") or item.get("api_id") or "",
            api_id=item.get("api_id") or "",
            interval=item.get("interval") or "5m",
            cron=item.get("cron") or "",
            asserts=item.get("asserts") if isinstance(item.get("asserts"), list) else [],
            alert_channels=[c for c in (item.get("alert_channels") or []) if isinstance(c, str)],
            enabled=bool(item.get("enabled", True)),
            risk_level=item.get("risk_level") if item.get("risk_level") in _SEVERITIES else "medium",
            max_consecutive_failures=int(item.get("max_consecutive_failures") or 3),
            diff_threshold=int(item.get("diff_threshold") or 3),
            diff_ignore_paths=item.get("diff_ignore_paths") if isinstance(item.get("diff_ignore_paths"), list) else [],
            description=item.get("description", ""),
        ).model_dump()
        monitors.append(monitor)
    if not monitors:
        raise ValueError("monitor output has no valid monitor configs")
    return {"monitors": monitors, "summary": str(parsed.get("summary", ""))[:500]}


def _parse_diff(parsed: Any, raw: str) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        raise ValueError(f"diff output must be object, got {type(parsed).__name__}")
    return DiffEvaluationOutput(**parsed).model_dump()


def _parse_doc_fix(parsed: Any, raw: str) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        raise ValueError(f"doc_fix output must be object, got {type(parsed).__name__}")
    params = parsed.get("doc.params") if "doc.params" in parsed else parsed.get("params", [])
    response_fields = parsed.get("doc.response_fields") if "doc.response_fields" in parsed else parsed.get("response_fields", [])
    if not isinstance(params, list) and not isinstance(response_fields, list):
        raise ValueError("doc_fix output must include params or response_fields list")
    return {
        "params": [item for item in (params or []) if isinstance(item, dict)],
        "response_fields": [item for item in (response_fields or []) if isinstance(item, dict)],
    }


def _parse_diagnosis(parsed: Any, raw: str) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        raise ValueError(f"diagnosis output must be object, got {type(parsed).__name__}")
    output = DiagnosisOutput(**parsed)
    return {
        "root_cause": output.root_cause,
        "explanation": str(output.explanation)[:500],
        "suggested_fix": str(output.suggested_fix)[:500],
        "confidence": round(output.confidence, 2),
    }


def _parse_alert(parsed: Any, raw: str) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        raise ValueError(f"alert output must be object, got {type(parsed).__name__}")
    output = AlertAssessmentOutput(**parsed)
    return {
        "severity": output.severity,
        "root_cause": str(output.root_cause)[:500],
        "confidence": output.confidence,
    }


def _assert_rule(item: dict[str, Any]) -> dict[str, Any]:
    if not item.get("field") or not item.get("operator"):
        raise ValueError("assert rule missing field/operator")
    operator = str(item.get("operator", "")).strip()
    if operator not in _ASSERT_OPERATOR_SET:
        raise ValueError(f"invalid assert operator: {operator}")
    risk_level = str(item.get("risk_level") or "medium").lower()
    if risk_level not in {level.value for level in RiskLevel}:
        risk_level = "medium"
    return AssertRule(
        field=str(item.get("field")),
        operator=operator,
        expected=item.get("expected"),
        description=str(item.get("description", ""))[:500],
        risk_level=RiskLevel(risk_level),
    ).model_dump()


def _collect_assert_rules(items: Any) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return rules
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            rules.append(_assert_rule(item))
        except ValueError:
            # 保留有效断言项；若最终一个都没有，上层会失败并进入可见重试/DLQ。
            continue
    return rules


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(part) for part in first.get("loc", []))
    msg = first.get("msg", str(exc))
    return f"{loc}: {msg}" if loc else msg


def _preview(value: str, limit: int = 2000) -> str:
    value = value or ""
    return value[:limit]
