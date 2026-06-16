"""hdp.engine.guard — the secure edit gateway (ask #3, the security guarantee).

PDP (:mod:`hdp.engine.guard.pdp`) decides; PEP (:mod:`hdp.engine.guard.pep`) enforces atomically.
``apply()`` is the evolve agent's ONLY write path to the harness. Rules are tiered: the
confinement CORE is never overridable; SOFT rules are denied in ``enforce`` mode (default, and
what bench runs) and routed to human review in ``review`` mode.
"""
from __future__ import annotations

from hdp.engine.guard.pdp import Decision, Edit, Tier, Violation, decide
from hdp.engine.guard.pep import ApplyResult, apply, approve, dry_decide

NAME = "guard"
PHASE = "Phase 3 (guard)"

__all__ = ["Decision", "Edit", "Tier", "Violation", "decide",
           "ApplyResult", "apply", "approve", "dry_decide",
           "mode_from_config", "smoke_step"]


def mode_from_config(cfg: dict, *, force_enforce: bool = False) -> str:
    """Resolve the guard mode from config. Bench passes force_enforce=True (bench runs enforce)."""
    if force_enforce:
        return "enforce"
    mode = ((cfg.get("hdp") or {}).get("guard") or {}).get("mode", "enforce")
    return mode if mode in ("enforce", "review") else "enforce"


def smoke_step(run) -> None:
    """Phase 0 dry-pipeline stub (the real path is :func:`apply`)."""
    run.log("guard_allowed", 1, phase="evolve", change_id="chg-smoke")
    run.log("guard_denied", 0, phase="evolve")
