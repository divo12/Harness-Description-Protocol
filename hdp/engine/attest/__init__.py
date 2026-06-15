"""hdp.engine.attest — decision-observability reconciliation (SPEC §7.2; AHE RQ3b).

Each iteration, ``attest`` settles the PREVIOUS iteration's change manifest against what the
eval actually did. Every change carried a falsifiable prediction (``expected_fixes`` /
``at_risk_regressions``); ``attest`` intersects those with the observed task flips and sets a
verdict per change — turning every edit into a contract the next round confirms or refutes.

It also surfaces **unattributed regressions** — tasks that regressed though no change predicted
them — the direct measure of the loop's regression blindness (AHE finds this is where
self-attribution is weakest).

A later refinement keys an observed regression to a *component* via the generator's
``hdp_attribution.json`` sidecar; v1 reconciles by the declared per-change predictions, exactly
as the AHE loop does.
"""
from __future__ import annotations

from dataclasses import dataclass, field

NAME = "attest"
PHASE = "Phase 4 (track+attest)"

Verdict = str  # one of: effective | partial | ineffective | harmful (pending until attested)


def _verdict(expected_fixes: list[str], at_risk: list[str],
             flipped: set[str], regressed: set[str]) -> tuple[Verdict, list[str], list[str]]:
    fixes_hit = [t for t in expected_fixes if t in flipped]
    regr_hit = [t for t in at_risk if t in regressed]
    n_fix, n_pred, n_regr = len(fixes_hit), len(expected_fixes), len(regr_hit)
    if n_regr and not n_fix:
        verdict = "harmful"
    elif n_regr and n_fix:
        verdict = "partial"
    elif n_pred and n_fix == n_pred:
        verdict = "effective"
    elif n_fix:
        verdict = "partial"
    else:
        verdict = "ineffective"
    return verdict, fixes_hit, regr_hit


def _ratio(hit: int, total: int) -> float:
    return hit / total if total else 0.0


@dataclass
class AttestResult:
    manifest: dict                                   # manifest with verdicts + results filled in
    unattributed_regressions: list[str] = field(default_factory=list)
    fix_precision: float = 0.0
    fix_recall: float = 0.0
    regression_precision: float = 0.0
    regression_recall: float = 0.0


def reconcile(manifest: dict, flipped: set[str], regressed: set[str]) -> AttestResult:
    """Settle *manifest*'s predictions against the observed *flipped* (fail→pass) and
    *regressed* (pass→fail) task sets. Returns the updated manifest + attribution metrics."""
    changes = manifest.get("changes", [])
    all_predicted_fixes: set[str] = set()
    all_predicted_regr: set[str] = set()

    for chg in changes:
        pred = chg.get("prediction", {}) or {}
        expected = list(pred.get("expected_fixes") or [])
        at_risk = list(pred.get("at_risk_regressions") or [])
        all_predicted_fixes |= set(expected)
        all_predicted_regr |= set(at_risk)

        verdict, fixes_hit, regr_hit = _verdict(expected, at_risk, flipped, regressed)
        chg["verdict"] = verdict
        chg["result"] = {
            "fixes_verified": fixes_hit,
            "regressions_observed": regr_hit,
        }

    unattributed = sorted(regressed - all_predicted_regr)

    return AttestResult(
        manifest=manifest,
        unattributed_regressions=unattributed,
        fix_precision=_ratio(len(all_predicted_fixes & flipped), len(all_predicted_fixes)),
        fix_recall=_ratio(len(all_predicted_fixes & flipped), len(flipped)),
        regression_precision=_ratio(len(all_predicted_regr & regressed), len(all_predicted_regr)),
        regression_recall=_ratio(len(all_predicted_regr & regressed), len(regressed)),
    )


def smoke_step(run) -> None:
    """Phase 0 dry-pipeline stub (the real path is :func:`reconcile`)."""
    run.log("attest_flips_attributed", 1, phase="evolve", change_id="chg-smoke")
