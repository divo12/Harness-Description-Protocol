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

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ruamel.yaml import YAML

from hdp.engine.core.loader import HDPDoc, load
from hdp.engine.guard.pdp import Decision, Edit, Tier, Violation, decide


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


def apply(
    edit: Edit,
    doc: HDPDoc,
    mode: str = "enforce",
    on_apply: Optional[Callable[[Path, Edit], None]] = None,
) -> ApplyResult:
    """Stage → decide → (swap | discard | hold-for-review). The sole harness write path."""
    staged = _stage(edit, doc)

    try:
        new_doc = load(staged)
    except Exception as exc:  # malformed staged document is unloadable -> structural deny
        shutil.rmtree(staged, ignore_errors=True)
        return ApplyResult(
            Decision("deny", [Violation(Tier.STRUCTURAL, "load", str(exc))]), applied=False
        )

    decision = decide(edit, doc, new_doc, mode=mode)

    if decision.status == "allow":
        _atomic_swap(doc.path, staged)
        if on_apply is not None:
            on_apply(doc.path, edit)
        return ApplyResult(decision, applied=True)

    if decision.status == "review":
        return ApplyResult(decision, applied=False, review_dir=staged)

    shutil.rmtree(staged, ignore_errors=True)
    return ApplyResult(decision, applied=False)
