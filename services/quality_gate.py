"""
分层质量门 + 分级代审决策（Q2 / Q4）。

目标：把「准确率全押人工」改为「机器可自证 + 分级代审」。本模块只做**纯函数**的
评分与代审决策，可独立单测；Gate0/Gate1/Gate2/Gate3 的具体执行挂在 analyzer / trial_runner，
通过 gate_results 字典汇入这里统一核算。

设计约束：
  - 纯函数、无 IO，便于单测与复用；
  - 不改变现有审核主链：decide_auto_review 仅在 auto_review_enabled 时返回 auto_accept/auto_reject/manual。
"""
from __future__ import annotations

from typing import Any

from config.settings import get_settings

# 各门在综合分中的权重（Gate0 为硬条件，单独判定）
_WEIGHTS = {"gate1": 0.3, "gate2": 0.3, "gate3": 0.4}


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def score_gate_results(gate_results: dict[str, Any], risk_level: str, partial=None) -> dict[str, float]:
    """由各门结果核算 quality_score / confidence。

    gate_results 约定：
      - gate0: {"passed": bool, "issues": [...]}  （硬条件，未过则 confidence 封顶 0.4）
      - gate1: {"passed": bool, "issues": [...]}  语义自检
      - gate2: {"pass_rate": float}               一致性投票通过率（0..1）
      - gate3: {"passed": bool, "coverage": float} 试跑结果（passed 且 coverage>=阈值 记 1.0，否则按覆盖率折扣）
    """
    s = get_settings()
    g0 = gate_results.get("gate0") or {}
    g1 = gate_results.get("gate2") or gate_results.get("gate1") or {}
    g2 = gate_results.get("gate2") or {}
    g3 = gate_results.get("gate3") or {}

    parts: dict[str, float] = {}

    # Gate1：语义自检
    parts["gate1"] = 1.0 if g1.get("passed", True) and not g1.get("issues") else (0.5 if g1.get("passed") else 0.0)
    # Gate2：一致性
    parts["gate2"] = _clamp(float(g2.get("pass_rate") or (1.0 if g2.get("passed", True) else 0.0)))
    # Gate3：试跑
    if g3:
        if g3.get("passed"):
            parts["gate3"] = 1.0
        else:
            # 失败但覆盖率尚可 -> 部分分；完全无试跑 -> 0
            cov = float(g3.get("coverage") or 0)
            parts["gate3"] = _clamp(cov * 0.5)
    else:
        parts["gate3"] = 0.0

    # 未接入的门按中性 0.5 处理，避免缺口拉低质量分
    for k in _WEIGHTS:
        if k not in parts:
            parts[k] = 0.5

    conf = sum(_WEIGHTS[k] * parts[k] for k in _WEIGHTS)

    # Gate0 硬条件：未过则 confidence 封顶 0.4（必然 < 自动通过阈值）
    if g0 and not g0.get("passed", True):
        conf = min(conf, 0.4)
    # 风险调降：high/critical 适度下调置信度，倾向人工
    if risk_level == "high":
        conf *= 0.9
    elif risk_level == "critical":
        conf *= 0.8

    q = _clamp(conf)
    return {"quality_score": q, "confidence": q}


def decide_auto_review(gv: dict[str, Any], gate_results: dict[str, Any] | None = None) -> str:
    """决定某个 GenerationVersion 走自动通过 / 自动拒绝 / 人工。

    返回：'auto_accept' | 'auto_reject' | 'manual'
    三层护栏：置信度阈值 + 试跑通过(可选) + 风险档位。
    """
    s = get_settings()
    if not s.auto_review_enabled:
        return "manual"

    risk = str(gv.get("risk_level") or "medium")
    if risk in ("high", "critical"):
        return "manual"

    # 计算或复用已有 confidence
    conf = float(gv.get("confidence") or 0)
    if gate_results is not None and not conf:
        conf = score_gate_results(gate_results, risk)["confidence"]

    if conf < s.auto_review_reject_threshold:
        # 极低置信度且 schema 完全非法 → 自动拒绝（坏产物不占人工）
        g0 = (gate_results or {}).get("gate0") or {}
        if g0 and not g0.get("passed", True):
            return "auto_reject"
        return "manual"

    if conf < s.auto_review_min_confidence:
        return "manual"

    # 试跑要求仅对「场景」生效（doc/asserts 等不跑试跑，不应被拦）；场景才需试跑自证
    if s.auto_review_trial_required and gv.get("type") == "scenario":
        trial = gv.get("trial_run") or {}
        if not trial.get("passed"):
            return "manual"

    return "auto_accept"


def guess_risk_level(scenario_step_types: list[str], methods: list[str]) -> str:
    """由场景步骤/HTTP 方法粗略推导生成物风险等级（分级代审 & 试跑护栏复用）。"""
    write = {"POST", "PUT", "PATCH", "DELETE"}
    if any(m.upper() in write for m in methods):
        return "high" if any(m.upper() in {"PUT", "PATCH", "DELETE"} for m in methods) else "medium"
    if "critical" in scenario_step_types:
        return "critical"
    return "low"
