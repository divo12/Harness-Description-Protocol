"""hdp.redteam.evolve_replay — measure the guard against a REAL evolve-loop edit stream.

Unlike Stage 2 (LLM-generated adversarial edits) this replays the genuine proposed edits from a
completed AHE evolve run (``hdp.engine.loop.evolve``) through the guard, and reports allow/deny
**split separately by tier** — CORE (attempted confinement breach) reported distinctly from SOFT
(manifest/editability hygiene), never blended into one "% unsafe".

On-disk shape (written by the Stage 0 loop, ``hdp/engine/loop.py``): a run ``workdir`` holds
``iter-NNN/pre.hdp`` (the OLD doc, always) and ``iter-NNN/post.hdp`` (the raw proposed NEW doc,
**only in ``guard_interaction="monitor"`` mode**), plus per-iteration manifests under
``doc.hdp/evolution/manifests/``. Only a monitor-mode run snapshots ``post.hdp``, so only those
are replayable; ``load_evolve_run`` fails loud if ``post.hdp`` is missing rather than guessing.

Replaying (rather than trusting the run's logged ``guard_audit.jsonl`` verdicts) is deliberate:
a run logs verdicts under whatever engine it used, and the reconcile engine records no tier — so
re-running each edit through the tiered atomic PDP re-derives the CORE/SOFT split regardless.
This module is measurement-only; it never modifies the guard or the run.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from hdp.engine import guard
from hdp.engine.core.loader import load
from hdp.redteam.llm_gen import _summarize
from hdp.redteam.report import GuardMeasurementReport

_TIERS = ("core", "structural", "soft")


@dataclass
class EvolveEditRecord:
    iteration: int
    component_id: str | None
    layer: str | None
    operator: str | None
    manifest: dict | None
    old_path: Path                # iter-NNN/pre.hdp
    new_path: Path                # iter-NNN/post.hdp (monitor-mode snapshot)


# --------------------------------------------------------------------------- #
#  Loading a completed run
# --------------------------------------------------------------------------- #
def _first_change(manifest: dict | None) -> tuple[str | None, str | None, str | None]:
    changes = (manifest or {}).get("changes") or []
    if changes and isinstance(changes[0], dict):
        c = changes[0]
        return c.get("component_id"), c.get("layer"), c.get("operator")
    return None, None, None


def _manifest_dir(run_dir: Path) -> Path | None:
    doc_root = run_dir / "doc.hdp"
    if not doc_root.is_dir():
        docs = [p for p in run_dir.glob("*.hdp") if p.is_dir()]
        doc_root = docs[0] if docs else doc_root
    mdir = doc_root / "evolution" / "manifests"
    return mdir if mdir.is_dir() else None


def _load_manifest(run_dir: Path, iteration: int) -> dict | None:
    mdir = _manifest_dir(run_dir)
    if mdir is None:
        return None
    for cand in sorted(mdir.glob(f"{iteration:03d}*.json")):
        return json.loads(cand.read_text(encoding="utf-8"))
    return None


def load_evolve_run(run_dir: Path | str) -> list[EvolveEditRecord]:
    """Parse a completed evolve run's workdir into replayable edit records. Requires a
    ``monitor``-mode run (per-iteration ``post.hdp`` snapshots); raises if one is missing."""
    run_dir = Path(run_dir)
    records: list[EvolveEditRecord] = []
    for it_dir in sorted(run_dir.glob("iter-*")):
        if not it_dir.is_dir():
            continue
        try:
            iteration = int(it_dir.name.split("-", 1)[1])
        except ValueError:
            continue
        pre, post = it_dir / "pre.hdp", it_dir / "post.hdp"
        if not pre.is_dir():
            raise ValueError(f"{it_dir}: missing pre.hdp snapshot — not a valid evolve run dir")
        if not post.is_dir():
            raise ValueError(
                f"{it_dir}: missing post.hdp — the run must use guard_interaction='monitor' "
                "for its edit stream to be replayable through the guard"
            )
        manifest = _load_manifest(run_dir, iteration)
        cid, layer, op = _first_change(manifest)
        records.append(EvolveEditRecord(iteration, cid, layer, op, manifest, pre, post))
    if not records:
        raise ValueError(f"no iter-*/ directories found under {run_dir}")
    return records


# --------------------------------------------------------------------------- #
#  Measurement
# --------------------------------------------------------------------------- #
@contextmanager
def _working_copy(src: Path) -> Iterator[Path]:
    """A fresh copy of *src* (govern mutates the NEW tree, so each replay needs its own)."""
    base = Path(tempfile.mkdtemp(prefix="evolve-replay-"))
    dst = base / src.name
    shutil.copytree(src, dst)
    try:
        yield dst
    finally:
        shutil.rmtree(base, ignore_errors=True)


def measure_guard_against_run(records: Iterable[EvolveEditRecord]) -> GuardMeasurementReport:
    """Replay each edit through ``guard.govern`` under both engines and tabulate allow/deny.
    The tier split (CORE/STRUCTURAL/SOFT) comes from the atomic engine's tiered PDP; the
    reconcile engine contributes per-delta allow/deny counts."""
    records = list(records)
    denied_by_tier = {t: 0 for t in _TIERS}
    rule_distribution: dict[str, dict[str, int]] = {t: {} for t in _TIERS}
    atomic_allowed = atomic_denied = 0
    reconcile_allowed = reconcile_denied = 0
    doc_id = ""

    for rec in records:
        old = load(rec.old_path)
        doc_id = doc_id or old.model.meta.id

        with _working_copy(rec.new_path) as new_dir:
            _, atomic_report = guard.govern(
                old, load(new_dir), rec.manifest, engine="atomic", mode="enforce"
            )
        denied, tier, rule, _ = _summarize(atomic_report)  # most-severe tier per edit
        if denied:
            atomic_denied += 1
            key = tier if tier in denied_by_tier else "structural"
            denied_by_tier[key] += 1
            if rule:
                rule_distribution[key][rule] = rule_distribution[key].get(rule, 0) + 1
        else:
            atomic_allowed += 1

        with _working_copy(rec.new_path) as new_dir:
            _, reconcile_report = guard.govern(
                old, load(new_dir), rec.manifest, engine="reconcile", mode="enforce"
            )
        reconcile_denied += len(reconcile_report.denied)
        reconcile_allowed += len(reconcile_report.allowed)

    return GuardMeasurementReport(
        doc_id=doc_id,
        total_edits=len(records),
        atomic_allowed=atomic_allowed,
        atomic_denied=atomic_denied,
        denied_by_tier=denied_by_tier,
        rule_distribution=rule_distribution,
        reconcile_allowed_deltas=reconcile_allowed,
        reconcile_denied_deltas=reconcile_denied,
    )
