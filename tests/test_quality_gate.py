# -*- coding: utf-8 -*-
"""quality_gate 纯函数单测：评分 / 分级代审决策 / 风险等级推导。"""
from __future__ import annotations
import pytest
from config.settings import get_settings
from services.quality_gate import score_gate_results, decide_auto_review, guess_risk_level

_S = get_settings()


def _set(enabled=True, conf_min=0.7, trial_req=True, reject=0.3):
    _S.auto_review_enabled = enabled
    _S.auto_review_min_confidence = conf_min
    _S.auto_review_trial_required = trial_req
    _S.auto_review_reject_threshold = reject


def test_score_all_pass_is_high():
    g = {"gate0": {"passed": True}, "gate1": {"passed": True, "issues": []},
         "gate2": {"pass_rate": 1.0}, "gate3": {"passed": True, "coverage": 1.0}}
    r = score_gate_results(g, "low")
    assert r["confidence"] >= 0.95


def test_score_gate0_fail_caps_confidence():
    g = {"gate0": {"passed": False, "issues": ["x"]}, "gate1": {"passed": True},
         "gate3": {"passed": True, "coverage": 1.0}}
    r = score_gate_results(g, "low")
    assert r["confidence"] <= 0.4


def test_score_no_trial_is_discounted():
    g = {"gate0": {"passed": True}, "gate1": {"passed": True, "issues": []}}
    r = score_gate_results(g, "low")
    assert r["confidence"] < 0.9


def test_score_risk_high_discounts():
    g = {"gate0": {"passed": True}, "gate1": {"passed": True},
         "gate2": {"pass_rate": 1.0}, "gate3": {"passed": True, "coverage": 1.0}}
    low = score_gate_results(g, "low")["confidence"]
    high = score_gate_results(g, "high")["confidence"]
    assert high < low


def test_decide_disabled_returns_manual():
    _set(enabled=False)
    assert decide_auto_review({"confidence": 0.95}) == "manual"


def test_decide_high_risk_manual():
    _set(enabled=True)
    assert decide_auto_review({"risk_level": "high", "confidence": 0.95}) == "manual"


def test_decide_low_conf_manual():
    _set(enabled=True)
    assert decide_auto_review({"risk_level": "low", "confidence": 0.4}) == "manual"


def test_decide_trial_required_manual_if_not_passed():
    _set(enabled=True, trial_req=True)
    assert decide_auto_review({"type": "scenario", "risk_level": "low", "confidence": 0.9, "trial_run": {"passed": False}}) == "manual"


def test_decide_auto_accept():
    _set(enabled=True, trial_req=True)
    assert decide_auto_review({"type": "scenario", "risk_level": "low", "confidence": 0.9, "trial_run": {"passed": True}}) == "auto_accept"


def test_decide_auto_reject_when_schema_invalid():
    _set(enabled=True)
    gv = {"risk_level": "low", "confidence": 0.1}
    assert decide_auto_review(gv, gate_results={"gate0": {"passed": False}}) == "auto_reject"


def test_guess_risk_level():
    assert guess_risk_level([], ["GET"]) == "low"
    assert guess_risk_level([], ["POST"]) == "medium"
    assert guess_risk_level([], ["DELETE"]) == "high"
