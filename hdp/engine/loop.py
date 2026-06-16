"""hdp.engine.loop — the treatment-arm evolve loop (strings the phases into one iteration).

One iteration = gen → eval → attest(previous) → propose → guard → track, all over the HDP
*document* (a working copy under the run dir; the source document is never mutated):

    gen(doc)              -> harness/         compile the source-of-truth to a runnable harness
    eval(harness)         -> pass@1, tasks    reuse evolve.py (eval seam); dry-run for $0
    attest(prev_manifest) -> verdicts         settle last round's falsifiable predictions
    propose(doc)          -> edits + manifest  the retargeted evolve_agent (injectable for tests)
    guard.govern(old,new) -> reconciled doc    engine=reconcile (per-delta) | atomic (all-or-none)
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


def _attestation_summary(manifest: dict, ares) -> str:
    """How the previous iteration's edits actually fared — fed back so the agent learns."""
    lines = [f"Your iteration-{manifest.get('iteration')} edit(s):"]
    for c in manifest.get("changes", []):
        r = c.get("result", {}) or {}
        lines.append(f"- {c.get('change_id')} ({c.get('component_id')}): {c.get('verdict', '?')}"
                     f" — fixed {r.get('fixes_verified', [])}, broke {r.get('regressions_observed', [])}")
    if getattr(ares, "unattributed_regressions", None):
        lines.append(f"- regressions nobody predicted: {ares.unattributed_regressions}")
    return "\n".join(lines)


def _failure_evidence(cfg: dict, ev: EvalResult, it_dir: Path, it: int, dry_run: bool) -> dict:
    """ADB root-cause overview for the failing tasks — the SAME signal AHE's control arm gets.
    Returns {} under dry-run / ADB disabled / no failures / unavailable adb."""
    adb_cfg = cfg.get("agent_debugger") or {}
    if dry_run or not adb_cfg.get("enabled") or ev.job_dir is None:
        return {}
    tasks = ev.task_results
    if not tasks or all(float(v) >= 1 for v in tasks.values()):
        return {}  # nothing failed → nothing to analyze
    try:
        from evolve import run_parallel_adb_ask
        k = int((cfg.get("harbor") or {}).get("k", 1))
        overview = run_parallel_adb_ask(adb_cfg, ev.job_dir, tasks, Path(it_dir), it, k=k)
        return {"analysis_overview": overview} if overview else {}
    except Exception as e:  # ADB is best-effort evidence; never fail the iteration over it
        print(f"[hdp-loop] ADB analysis skipped: {e}")
        return {}


def evolve(cfg: dict, *, proposer: Proposer, workdir: Path | str, dry_run: bool = True,
           max_iterations: int = 2, eval_fn: EvalFn = eval_harness,
           run=None) -> list[IterationResult]:
    """Run the treatment-arm evolve loop on a working copy of the configured HDP document."""
    hdp_cfg = cfg.get("hdp") or {}
    target = hdp_cfg.get("target", "nexau")
    fake = float(((cfg.get("run") or {}).get("smoke") or {}).get("fake_reward", 1.0))
    guard_cfg = hdp_cfg.get("guard") or {}
    guard_engine = guard_cfg.get("engine", guard.DEFAULT_ENGINE)  # reconcile | atomic
    guard_mode = guard_cfg.get("mode", "enforce")                 # enforce | review (atomic only)

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

        evidence: dict = {"pass_rate": ev.pass_rate, "iteration": it}

        # Settle the previous iteration's predictions; tell the agent how its last edit did.
        if prev_manifest is not None:
            flipped, regressed = _flips(prev_tasks, ev.task_results)
            ares = attest.reconcile(prev_manifest, flipped, regressed)
            track.write_manifest(doc, prev_manifest, iteration=it - 1)  # persist verdicts
            evidence["attestation"] = _attestation_summary(prev_manifest, ares)

        # Failure analysis (ADB) — the SAME evidence the control arm gets, so treatment is not
        # improving blind. Live only (needs real traces); reuses evolve.py's ADB phase.
        evidence.update(_failure_evidence(cfg, ev, it_dir, it, dry_run))

        # Propose (free-edit the doc), then govern the edit.
        old = _snapshot(doc, it_dir / "pre.hdp")
        manifest = proposer(doc, evidence, it)
        new = load(doc.path)
        reconciled, report = guard.govern(old, new, manifest,
                                           engine=guard_engine, mode=guard_mode)
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
