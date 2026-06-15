"""hdp.engine.loop — the treatment-arm evolve loop (strings the phases into one iteration).

One iteration = gen → eval → attest(previous) → propose → guard → track, all over the HDP
*document* (a working copy under the run dir; the source document is never mutated):

    gen(doc)              -> harness/         compile the source-of-truth to a runnable harness
    eval(harness)         -> pass@1, tasks    reuse evolve.py (eval seam); dry-run for $0
    attest(prev_manifest) -> verdicts         settle last round's falsifiable predictions
    propose(doc)          -> edits + manifest  the retargeted evolve_agent (injectable for tests)
    guard.enforce(old,new)-> reconciled doc    roll back any edit that violates governance
    track.record          -> manifest+version  persist + bump (commit-per-iteration optional)

The proposer is injected (``Proposer`` protocol) so the loop is testable end-to-end with a stub
that makes a known governed edit — no LLM/E2B — while the real proposer (the evolve_agent
retargeted at the doc dir) drops in unchanged. The eval function is likewise injectable.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from hdp.engine import attest, guard, track
from hdp.engine.core.loader import HDPDoc, load, save
from hdp.engine.eval import EvalResult, eval_harness
from hdp.engine.gen import generate


class Proposer(Protocol):
    """Edits *doc*'s files in place and returns a declared change manifest (manifest-before-edit)."""
    def __call__(self, doc: HDPDoc, evidence: dict, iteration: int) -> dict: ...


EvalFn = Callable[..., EvalResult]


@dataclass
class IterationResult:
    iteration: int
    pass_rate: float
    version: str
    guard_denied: int
    manifest: dict


def _flips(prev: dict, cur: dict) -> tuple[set[str], set[str]]:
    """(flipped fail→pass, regressed pass→fail) between two per-task reward maps."""
    flipped = {t for t, v in cur.items() if float(v) >= 1 and float(prev.get(t, 0)) < 1}
    regressed = {t for t, v in cur.items() if float(v) < 1 and float(prev.get(t, 0)) >= 1}
    return flipped, regressed


def _snapshot(doc: HDPDoc, dest: Path) -> HDPDoc:
    """A separate on-disk copy of the document, so old/new embedded files don't alias."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(doc.path, dest)
    return load(dest)


def evolve(cfg: dict, *, proposer: Proposer, workdir: Path | str, dry_run: bool = True,
           max_iterations: int = 2, eval_fn: EvalFn = eval_harness,
           run=None) -> list[IterationResult]:
    """Run the treatment-arm evolve loop on a working copy of the configured HDP document."""
    hdp_cfg = cfg.get("hdp") or {}
    target = hdp_cfg.get("target", "nexau")
    fake = float(((cfg.get("run") or {}).get("smoke") or {}).get("fake_reward", 1.0))

    workdir = Path(workdir)
    work_doc = workdir / "doc.hdp"
    if work_doc.exists():
        shutil.rmtree(work_doc)
    shutil.copytree(hdp_cfg["document"], work_doc)
    doc = load(work_doc)

    results: list[IterationResult] = []
    prev_manifest: dict | None = None
    prev_tasks: dict = {}

    for it in range(1, max_iterations + 1):
        it_dir = workdir / f"iter-{it:03d}"
        generate(doc, it_dir / "harness", target=target)
        ev = eval_fn(cfg, it_dir / "harness", it_dir, dry_run=dry_run, fake_reward=fake)

        # Settle the previous iteration's predictions against this round's flips.
        if prev_manifest is not None:
            flipped, regressed = _flips(prev_tasks, ev.task_results)
            attest.reconcile(prev_manifest, flipped, regressed)
            track.write_manifest(doc, prev_manifest, iteration=it - 1)  # persist verdicts

        # Propose (free-edit the doc), then govern the edit.
        old = _snapshot(doc, it_dir / "pre.hdp")
        manifest = proposer(doc, {"pass_rate": ev.pass_rate, "iteration": it}, it)
        new = load(doc.path)
        reconciled, report = guard.enforce(old, new, manifest)
        save(reconciled)
        tr = track.record(reconciled, manifest, do_commit=False)
        doc = load(doc.path)

        if run is not None:
            run.log("pass_at_1", ev.pass_rate, phase="evolve", iteration=it)
            run.log("guard_denied", len(report.denied), phase="evolve", iteration=it)
            run.log("version_bumped", 1, phase="evolve", iteration=it)

        results.append(IterationResult(it, ev.pass_rate, tr.version,
                                       len(report.denied), manifest))
        prev_manifest, prev_tasks = manifest, ev.task_results

    return results
