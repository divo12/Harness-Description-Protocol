"""Tests for hdp.engine.attest — verdict reconciliation + attribution metrics."""
from __future__ import annotations

from hdp.engine import attest


def _manifest(changes: list[dict]) -> dict:
    return {"hdp": "0.1", "iteration": 1, "changes": changes}


def _chg(cid: str, fixes: list[str], risks: list[str]) -> dict:
    return {
        "change_id": cid, "operator": "update", "component_id": cid, "layer": "context",
        "failure_evidence": "e", "root_cause": "r",
        "repair_spec": {"editable_resources": ["x"], "validation_criteria": ["y"]},
        "prediction": {"expected_fixes": fixes, "at_risk_regressions": risks},
    }


def test_effective_when_all_fixes_land_and_no_regression():
    m = _manifest([_chg("c1", ["t1", "t2"], [])])
    attest.reconcile(m, flipped={"t1", "t2"}, regressed=set())
    assert m["changes"][0]["verdict"] == "effective"
    assert m["changes"][0]["result"]["fixes_verified"] == ["t1", "t2"]


def test_partial_when_some_fixes_land():
    m = _manifest([_chg("c1", ["t1", "t2"], [])])
    attest.reconcile(m, flipped={"t1"}, regressed=set())
    assert m["changes"][0]["verdict"] == "partial"


def test_ineffective_when_no_fix_lands():
    m = _manifest([_chg("c1", ["t1"], [])])
    attest.reconcile(m, flipped=set(), regressed=set())
    assert m["changes"][0]["verdict"] == "ineffective"


def test_harmful_when_predicted_regression_hits_and_no_fix():
    m = _manifest([_chg("c1", ["t1"], ["t9"])])
    attest.reconcile(m, flipped=set(), regressed={"t9"})
    assert m["changes"][0]["verdict"] == "harmful"
    assert m["changes"][0]["result"]["regressions_observed"] == ["t9"]


def test_unattributed_regressions_surface_blindness():
    # t9 was predicted (at risk); t5 regressed but nobody predicted it -> unattributed.
    m = _manifest([_chg("c1", ["t1"], ["t9"])])
    res = attest.reconcile(m, flipped={"t1"}, regressed={"t9", "t5"})
    assert res.unattributed_regressions == ["t5"]


def test_attribution_metrics():
    # predicted fixes {t1,t2}; flipped {t1,t3}. precision 1/2, recall 1/2.
    # predicted regr {t9}; regressed {t9}. precision 1/1, recall 1/1.
    m = _manifest([_chg("c1", ["t1", "t2"], ["t9"])])
    res = attest.reconcile(m, flipped={"t1", "t3"}, regressed={"t9"})
    assert res.fix_precision == 0.5 and res.fix_recall == 0.5
    assert res.regression_precision == 1.0 and res.regression_recall == 1.0


def test_zero_division_safe():
    m = _manifest([_chg("c1", [], [])])
    res = attest.reconcile(m, flipped=set(), regressed=set())
    assert res.fix_precision == 0.0 and res.regression_recall == 0.0
