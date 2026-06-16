"""hdp.engine.guard.pep — the Policy Enforcement Point (PEP): the atomic edit transaction.

``apply(edit, doc)`` is the **only** write path to the harness for the evolve agent. It:

  1. stages a full copy of the document and materializes the edit into it,
  2. runs the PDP (:func:`hdp.engine.guard.pdp.decide`) — which itself runs full hdp_validate,
  3. on Allow, atomically swaps the staged copy into place (rename-based, same filesystem) and
     hands off to *on_apply* (the Phase-4 tracker); on Deny, discards the staging and leaves the
     working tree byte-for-byte unchanged; on Review, keeps the staging for human approval
     without touching the working tree.

The working tree is never left partial: the swap is two renames with restore-on-failure.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from ruamel.yaml import YAML

from hdp.engine.core.loader import HDPDoc, load
from hdp.engine.guard.pdp import Decision, Edit, Tier, Violation, decide

if TYPE_CHECKING:  # avoid importing the metrics stack at runtime; we only call run.log()
    from hdp.engine.metrics import Run


@dataclass
class ApplyResult:
    decision: Decision
    applied: bool
    review_dir: Optional[Path] = None  # staging kept when status == "review"

    @property
    def status(self) -> str:
        return self.decision.status


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _stage(edit: Edit, doc: HDPDoc) -> Path:
    """Copy the document to a sibling staging dir and materialize *edit* into it."""
    suffix = f".staging-{os.getpid()}-{int(time.time() * 1000)}"
    staging = doc.path.parent / f"{doc.path.name}{suffix}"
    shutil.copytree(doc.path, staging)

    manifest_path = staging / "hdp.yaml"
    y = _yaml()
    with manifest_path.open(encoding="utf-8") as f:
        manifest = y.load(f)

    layers = manifest.setdefault("layers", {})
    comps = layers.setdefault(edit.layer, [])
    idx = next((i for i, c in enumerate(comps) if c.get("id") == edit.component_id), None)

    if edit.operator == "remove":
        if idx is not None:
            del comps[idx]
        for rel in edit.remove_files or []:
            fp = staging / rel
            if fp.exists():
                fp.unlink()
    elif edit.component is not None:
        if idx is None:
            comps.append(edit.component)
        else:
            comps[idx] = edit.component

    with manifest_path.open("w", encoding="utf-8") as f:
        y.dump(manifest, f)

    for rel, content in (edit.embedded or {}).items():
        fp = staging / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")

    return staging


def _atomic_swap(current: Path, staged: Path) -> None:
    """Replace *current* with *staged* via two renames, restoring on any failure."""
    backup = current.parent / f"{current.name}.bak-{os.getpid()}-{int(time.time() * 1000)}"
    os.rename(current, backup)
    try:
        os.rename(staged, current)
    except BaseException:
        os.rename(backup, current)  # restore the original tree
        raise
    shutil.rmtree(backup, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  audit trail + metrics (every decision/approval is traceable)
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _change_id(edit: Edit) -> Optional[str]:
    """The change_id of the (first) manifest entry, if a manifest was supplied."""
    changes = (edit.manifest or {}).get("changes") or []
    return changes[0].get("change_id") if changes else None


def _write_audit(doc_path: Path, event: dict) -> Path:
    """Append a first-class audit event to ``evolution/audit.jsonl`` in the (changed) tree.

    Only called when the tree is changing (allow / approve), so it never violates the
    tree-unchanged guarantee that holds on deny and review.
    """
    trail = doc_path / "evolution" / "audit.jsonl"
    trail.parent.mkdir(parents=True, exist_ok=True)
    with trail.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, default=str) + "\n")
    return trail


def _log(run: Optional["Run"], metric: str, change_id: Optional[str]) -> None:
    if run is not None:
        run.log(metric, 1, phase="evolve", change_id=change_id)


def apply(
    edit: Edit,
    doc: HDPDoc,
    mode: str = "enforce",
    on_apply: Optional[Callable[[Path, Edit], None]] = None,
    *,
    run: Optional["Run"] = None,
    reviewer: str = "auto",
) -> ApplyResult:
    """Stage → decide → (swap | discard | hold-for-review). The sole harness write path."""
    staged = _stage(edit, doc)
    cid = _change_id(edit)

    try:
        new_doc = load(staged)
    except Exception as exc:  # malformed staged document is unloadable -> structural deny
        shutil.rmtree(staged, ignore_errors=True)
        _log(run, "guard_denied", cid)
        return ApplyResult(
            Decision("deny", [Violation(Tier.STRUCTURAL, "load", str(exc))]), applied=False
        )

    decision = decide(edit, doc, new_doc, mode=mode)

    if decision.status == "allow":
        _atomic_swap(doc.path, staged)
        _write_audit(doc.path, {"change_id": cid, "reviewer": reviewer,
                                "decision": "allow", "reason": "", "ts": _now()})
        _log(run, "guard_allowed", cid)
        if on_apply is not None:
            on_apply(doc.path, edit)
        return ApplyResult(decision, applied=True)

    if decision.status == "review":
        _log(run, "guard_review", cid)
        return ApplyResult(decision, applied=False, review_dir=staged)

    shutil.rmtree(staged, ignore_errors=True)
    _log(run, "guard_denied", cid)
    return ApplyResult(decision, applied=False)


def dry_decide(edit: Edit, doc: HDPDoc, mode: str = "enforce") -> Decision:
    """Decide *edit* against *doc* without ever touching the working tree (stage → decide → discard)."""
    staged = _stage(edit, doc)
    try:
        try:
            new_doc = load(staged)
        except Exception as exc:
            return Decision("deny", [Violation(Tier.STRUCTURAL, "load", str(exc))])
        return decide(edit, doc, new_doc, mode=mode)
    finally:
        shutil.rmtree(staged, ignore_errors=True)


def approve(
    doc: HDPDoc,
    edit: Edit,
    review_dir: Path,
    *,
    reviewer: str,
    reason: str,
    on_apply: Optional[Callable[[Path, Edit], None]] = None,
    run: Optional["Run"] = None,
) -> ApplyResult:
    """Re-submit a quarantined (review-mode) edit through the SAME PEP after human approval.

    The confinement core is non-overridable: full validation is re-run, and any CORE/STRUCTURAL
    violation still hard-blocks regardless of the approval. Only when no CORE/STRUCTURAL
    violation remains (the SOFT rule being overridden) does the staged copy swap into place,
    emitting a first-class audit event ``{change_id, reviewer, decision, reason, ts}``.
    """
    cid = _change_id(edit)
    new_doc = load(review_dir)
    decision = decide(edit, doc, new_doc, mode="review")
    blocking = [v for v in decision.violations if v.tier in (Tier.CORE, Tier.STRUCTURAL)]
    if blocking:  # confinement core can never be promoted by a human
        shutil.rmtree(review_dir, ignore_errors=True)
        _log(run, "guard_denied", cid)
        return ApplyResult(Decision("deny", blocking), applied=False)

    _atomic_swap(doc.path, review_dir)
    _write_audit(doc.path, {"change_id": cid, "reviewer": reviewer,
                            "decision": "approved", "reason": reason, "ts": _now()})
    _log(run, "guard_review_approved", cid)
    if on_apply is not None:
        on_apply(doc.path, edit)
    return ApplyResult(Decision("allow", decision.violations), applied=True)
