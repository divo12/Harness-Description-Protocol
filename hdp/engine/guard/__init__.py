"""hdp.engine.guard — governed-edit enforcement (SPEC §5.2; design Gates 2-3).

The proposer free-edits the HDP document; ``guard`` is the ring around it that makes the edit
*governed* rather than the agent *caged* — realizing HarnessFix's "constrained edits beat
unconstrained" as a post-edit check, not a prompt restriction.

This module ships two complementary gateways over the same governance policy:

* **Reconciliation gateway** (:func:`evaluate` / :func:`enforce`, in this file) — diff old→new and
  roll *denied* component deltas back to their old state, keeping the allowed ones. Used by the
  evolve loop (:mod:`hdp.engine.loop`, :mod:`hdp.engine.propose`).
* **Atomic transaction gateway** (:func:`decide` / :func:`apply`, in :mod:`~hdp.engine.guard.pdp`
  and :mod:`~hdp.engine.guard.pep`) — tiered hard-block (CORE / STRUCTURAL / SOFT) with an atomic
  stage→swap, ``review`` mode, and an audit trail. The confinement CORE is never overridable;
  SOFT rules are denied in ``enforce`` (default, what bench runs) and routed to human review in
  ``review``. ``apply()`` is the only write path for an edit that must succeed-or-not-at-all.

The ``evaluate``/``decide`` split mirrors a policy decision point (PDP); ``enforce``/``apply`` a
policy enforcement point (PEP).
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import Any

from hdp.engine.core.differ import ComponentDelta, diff_docs
from hdp.engine.core.loader import HDPDoc, HDPManifest, load

# Atomic-transaction gateway (tiered hard-block + review + audit). Additive: leaves the
# reconciliation gateway below untouched.
from hdp.engine.guard.pdp import Decision, Edit, Tier, Violation, decide
from hdp.engine.guard.pep import ApplyResult, apply, approve, dry_decide

NAME = "guard"
PHASE = "Phase 3 (guard)"

__all__ = [
    # reconciliation gateway
    "evaluate", "enforce", "GuardReport", "GuardDecision",
    # atomic-transaction gateway
    "Decision", "Edit", "Tier", "Violation", "decide",
    "ApplyResult", "apply", "approve", "dry_decide",
    # loop seam (selects the engine) + shared
    "govern", "mode_from_config", "smoke_step",
]

# Default guard engine for the evolve loop; overridable via cfg.hdp.guard.engine.
DEFAULT_ENGINE = "reconcile"

# change-kind → the operators that may legitimately declare it (SPEC §7.1).
_OPERATOR_FOR_CHANGE = {
    "added": {"add"},
    "removed": {"remove"},
    "modified": {"update", "narrow", "gate"},
}


@dataclass
class GuardDecision:
    component_id: str
    layer: str
    change: str
    allowed: bool
    reason: str = ""
    operator: str | None = None


@dataclass
class GuardReport:
    decisions: list[GuardDecision] = field(default_factory=list)
    governance_self_edit_denied: bool = False
    meta_changed: list[str] = field(default_factory=list)

    @property
    def denied(self) -> list[GuardDecision]:
        return [d for d in self.decisions if not d.allowed]

    @property
    def allowed(self) -> list[GuardDecision]:
        return [d for d in self.decisions if d.allowed]

    @property
    def ok(self) -> bool:
        return not self.denied and not self.governance_self_edit_denied


def _policy(old: HDPDoc) -> tuple[set[str], set[str], set[str]]:
    gov = old.model.governance
    evo = gov.evolution if gov else None
    editable = set(evo.editable or []) if evo else set()
    read_only = set(evo.read_only or []) if evo else set()
    protected = set(evo.protected or []) if evo else set()
    return editable, read_only, protected


def _declared_operators(manifest: dict | None) -> dict[str, set[str]]:
    """component_id → declared operator(s), from a change manifest (or {} if none given)."""
    if not manifest:
        return {}
    out: dict[str, set[str]] = {}
    for chg in manifest.get("changes", []):
        cid, op = chg.get("component_id"), chg.get("operator")
        if cid and op:
            out.setdefault(cid, set()).add(op)
    return out


def _deny(d: GuardDecision, reason: str) -> GuardDecision:
    d.allowed, d.reason = False, reason
    return d


def _decide(delta: ComponentDelta, editable: set[str], read_only: set[str],
            protected: set[str], declared: dict[str, set[str]],
            require_manifest: bool) -> GuardDecision:
    cid, layer, change = delta.component_id, delta.layer, delta.change
    ops = declared.get(cid, set())
    op = sorted(ops)[0] if ops else None
    d = GuardDecision(cid, layer, change, allowed=True, operator=op)

    # 1. protected components are inviolable for remove/modify (SPEC §5.2 rule 4).
    if cid in protected and change in ("removed", "modified"):
        return _deny(d, "protected component")
    # 2. read-only layer/component (rule 2; verification/governance default here via lift).
    if layer in read_only or cid in read_only:
        return _deny(d, f"read-only ({layer})")
    # 3. editable allowlist, when one is declared.
    if editable and layer not in editable and cid not in editable:
        return _deny(d, "not in editable allowlist")
    # 4. manifest-before-edit (rule 8): declared, with an operator consistent with the change.
    if require_manifest:
        if not ops:
            return _deny(d, "undeclared edit (no manifest entry)")
        if not (ops & _OPERATOR_FOR_CHANGE[change]):
            return _deny(d, f"operator {sorted(ops)} inconsistent with '{change}'")
    return d


def evaluate(old: HDPDoc, new: HDPDoc, manifest: dict | None = None,
             *, require_manifest: bool = True) -> GuardReport:
    """PDP: decide allow/deny for every component delta old→new under OLD's governance."""
    editable, read_only, protected = _policy(old)
    declared = _declared_operators(manifest)
    diff = diff_docs(old, new)
    report = GuardReport(meta_changed=diff.meta_changed)
    for delta in diff.components:
        report.decisions.append(
            _decide(delta, editable, read_only, protected, declared, require_manifest)
        )
    # governance self-edit: an edit may not change its own governance unless explicitly editable.
    if diff.governance_changed and "governance" not in editable:
        report.governance_self_edit_denied = True
    return report


# --------------------------------------------------------------------------- #
#  PEP — roll back denied component deltas on the ruamel raw view.
# --------------------------------------------------------------------------- #
def _raw_layer(raw: Any, layer: str) -> list:
    layers = raw.setdefault("layers", {})
    return layers.setdefault(layer, [])


def _raw_find(seq: list, cid: str) -> int:
    for i, item in enumerate(seq):
        if item.get("id") == cid:
            return i
    return -1


def _old_raw_component(old: HDPDoc, cid: str, layer: str):
    for item in (old.raw.get("layers", {}) or {}).get(layer, []) or []:
        if item.get("id") == cid:
            return item
    return None


def enforce(old: HDPDoc, new: HDPDoc, manifest: dict | None = None,
            *, require_manifest: bool = True) -> tuple[HDPDoc, GuardReport]:
    """PEP: return (reconciled_doc, report). Denied component deltas are reverted to OLD;
    allowed deltas are kept. The reconciled doc is re-validated against the typed model."""
    report = evaluate(old, new, manifest, require_manifest=require_manifest)
    raw = new.raw
    for d in report.denied:
        seq = _raw_layer(raw, d.layer)
        idx = _raw_find(seq, d.component_id)
        if d.change == "added":               # added illegally → drop it
            if idx >= 0:
                del seq[idx]
        else:                                  # removed/modified illegally → restore OLD
            old_item = _old_raw_component(old, d.component_id, d.layer)
            if old_item is not None:
                if idx >= 0:
                    seq[idx] = old_item
                else:
                    seq.append(old_item)
        if not seq and d.layer in raw.get("layers", {}):
            del raw["layers"][d.layer]
    # governance self-edit denied → restore OLD governance block wholesale.
    if report.governance_self_edit_denied:
        if "governance" in old.raw:
            raw["governance"] = old.raw["governance"]
        elif "governance" in raw:
            del raw["governance"]
    model = HDPManifest.model_validate(raw)
    return HDPDoc(path=new.path, raw=raw, model=model), report


# --------------------------------------------------------------------------- #
#  loop seam — pick the enforcement engine (application study: reconcile vs atomic)
# --------------------------------------------------------------------------- #
def govern(old: HDPDoc, new: HDPDoc, manifest: dict | None = None, *,
           engine: str = DEFAULT_ENGINE, mode: str = "enforce",
           require_manifest: bool = True) -> tuple[HDPDoc, GuardReport]:
    """Govern an edit old→new and return ``(reconciled_doc, report)`` (``reconciled_doc.path``
    is always ``new.path``). The *engine* selects the enforcement policy:

    * ``"reconcile"`` (default) — per-delta rollback (:func:`enforce`): denied component deltas
      revert to OLD, allowed deltas survive. Soft / partial.
    * ``"atomic"`` — all-or-nothing via the tiered PDP (:func:`decide`, adds confinement / secret
      / blast / structural checks + ``review`` mode): if ANY violation, the whole edit reverts to
      OLD; otherwise NEW is kept.
    """
    if engine == "atomic":
        return _govern_atomic(old, new, manifest, mode=mode)
    return enforce(old, new, manifest, require_manifest=require_manifest)


def _govern_atomic(old: HDPDoc, new: HDPDoc, manifest: dict | None,
                   *, mode: str) -> tuple[HDPDoc, GuardReport]:
    decision = decide(Edit(operator="update", layer="", component_id="", manifest=manifest),
                      old, new, mode=mode)
    report = GuardReport(meta_changed=diff_docs(old, new).meta_changed)
    if decision.allowed:
        return new, report
    for v in decision.violations:  # one denied "decision" per violation → len(report.denied) is meaningful
        report.decisions.append(
            GuardDecision("*", "*", "reverted", allowed=False, reason=str(v))
        )
    # all-or-nothing: restore the working doc wholesale from the OLD snapshot.
    shutil.rmtree(new.path)
    shutil.copytree(old.path, new.path)
    return load(new.path), report


def mode_from_config(cfg: dict, *, force_enforce: bool = False) -> str:
    """Resolve the atomic-gateway mode from config. Bench passes force_enforce=True (runs enforce)."""
    if force_enforce:
        return "enforce"
    mode = ((cfg.get("hdp") or {}).get("guard") or {}).get("mode", "enforce")
    return mode if mode in ("enforce", "review") else "enforce"


def smoke_step(run) -> None:
    """Phase 0 dry-pipeline stub (the real paths are :func:`enforce` / :func:`apply`)."""
    run.log("guard_allowed", 1, phase="evolve", change_id="chg-smoke")
    run.log("guard_denied", 0, phase="evolve")
