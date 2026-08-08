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
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from tqdm import tqdm

from hdp.engine import attest, guard, track
from hdp.engine.core.loader import HDPDoc, load, save
from hdp.engine.eval import EvalResult, eval_harness
from hdp.engine.gen import generate
from hdp.engine.loop_logging import _log_iteration, append_guard_audit, setup_evolve_logger


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


def _passed(status) -> bool:
    """True iff a per-task result counts as a pass. eval's task_results are status strings
    ("pass"|"fail"|"exception"); tolerate numeric rewards too for stub-driven tests."""
    if isinstance(status, str):
        return status.strip().lower() == "pass"
    try:
        return float(status) >= 1
    except (TypeError, ValueError):
        return False


def _flips(prev: dict, cur: dict) -> tuple[set[str], set[str]]:
    """(flipped fail→pass, regressed pass→fail) between two per-task result maps."""
    flipped = {t for t, v in cur.items() if _passed(v) and not _passed(prev.get(t))}
    regressed = {t for t, v in cur.items() if not _passed(v) and _passed(prev.get(t))}
    return flipped, regressed


def _snapshot(doc: HDPDoc, dest: Path) -> HDPDoc:
    """A separate on-disk copy of the document, so old/new embedded files don't alias."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(doc.path, dest)
    return load(dest)


def _restore(dst: Path, src: Path) -> None:
    """Byte-copy the doc tree at *src* over *dst* (used to reinstate a snapshot on disk)."""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _warn_text(report) -> str:
    """A warning the proposer sees on a warn-and-retry: which of its edits the guard flagged."""
    return "GUARD FLAGGED your edit; revise it:\n" + "\n".join(
        f"- {d.component_id} ({d.layer}): {d.reason}" for d in report.denied)


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


def _apply_experiment_patch(cfg: dict, harness_dir: Path) -> None:
    """Apply the experiment-time ``code_agent_patch`` (e.g. ``reasoning.effort``) to the generated
    harness — the SAME inference overlay AHE's control applies to its workspace. Without it the
    generated agent runs gpt-5.x at default reasoning while control runs at ``effort: high``, so
    treatment would underperform control for a reason that has nothing to do with HDP. Live path
    only (keeps the dry-run "never import evolve" invariant)."""
    patch = cfg.get("code_agent_patch") or {}
    if not patch:
        return
    fname = cfg.get("agent_config_filename", "code_agent.yaml")
    try:
        from ahe_control.evolve import apply_code_agent_patch
        apply_code_agent_patch(harness_dir, fname, patch)
    except Exception as e:  # an inference overlay is best-effort; never fail the iteration over it
        print(f"[hdp-loop] code_agent_patch skipped: {e}")


def _failure_evidence(cfg: dict, ev: EvalResult, it_dir: Path, it: int, dry_run: bool) -> dict:
    """ADB root-cause overview for the failing tasks — the SAME signal AHE's control arm gets.
    Returns {} under dry-run / ADB disabled / no failures / unavailable adb."""
    adb_cfg = cfg.get("agent_debugger") or {}
    if dry_run or not adb_cfg.get("enabled") or ev.job_dir is None:
        return {}
    tasks = ev.task_results
    if not tasks or all(_passed(v) for v in tasks.values()):
        return {}  # nothing failed → nothing to analyze
    try:
        from ahe_control.evolve import run_parallel_adb_ask
        k = int((cfg.get("harbor") or {}).get("k", 1))
        overview = run_parallel_adb_ask(adb_cfg, ev.job_dir, tasks, Path(it_dir), it, k=k)
        return {"analysis_overview": overview} if overview else {}
    except Exception as e:  # ADB is best-effort evidence; never fail the iteration over it
        print(f"[hdp-loop] ADB analysis skipped: {e}")
        return {}


def evolve(cfg: dict, *, proposer: Proposer, workdir: Path | str, dry_run: bool = True,
           max_iterations: int = 2, eval_fn: EvalFn = eval_harness, run=None,
           log_dir: Path | None = None,
           show_progress: bool = True,
           do_commit: bool | None = None,
           guard_interaction: str | None = None,
           warn_and_retry: bool | None = None,
           warn_and_retry_max_attempts: int | None = None,
           ) -> list[IterationResult]:
    """Run the treatment-arm evolve loop on a working copy of the configured HDP document."""
    hdp_cfg = cfg.get("hdp") or {}
    target = hdp_cfg.get("target", "nexau")
    fake = float(((cfg.get("run") or {}).get("smoke") or {}).get("fake_reward", 1.0))
    guard_cfg = hdp_cfg.get("guard") or {}
    guard_engine = guard_cfg.get("engine", guard.DEFAULT_ENGINE)  # reconcile | atomic
    guard_mode = guard_cfg.get("mode", "enforce")                 # enforce | review (atomic only)

    # New loop options: config-driven (cfg["hdp"]["guard"|"track"][...]) with a kwarg override,
    # matching how guard_engine/guard_mode above are resolved. Defaults reproduce today exactly.
    track_cfg = hdp_cfg.get("track") or {}
    if do_commit is None:
        do_commit = bool(track_cfg.get("commit", False))
    if guard_interaction is None:
        guard_interaction = guard_cfg.get("interaction", "enforce")  # enforce | monitor
    if warn_and_retry is None:
        warn_and_retry = bool(guard_cfg.get("warn_and_retry", False))
    if warn_and_retry_max_attempts is None:
        warn_and_retry_max_attempts = int(guard_cfg.get("warn_and_retry_max_attempts", 1))
    if guard_interaction not in ("enforce", "monitor"):  # fail loud, never silently fall back
        raise ValueError(
            f"guard_interaction must be 'enforce' or 'monitor', got {guard_interaction!r}")

    workdir = Path(workdir)
    work_doc = workdir / "doc.hdp"
    if work_doc.exists():
        shutil.rmtree(work_doc)
    shutil.copytree(hdp_cfg["document"], work_doc)
    doc = load(work_doc)

    log_dir = Path(log_dir) if log_dir is not None else workdir
    logger = setup_evolve_logger(log_dir)
    audit_path = log_dir / "guard_audit.jsonl"
    cum_denied = 0
    bar = tqdm(total=max_iterations, desc="evolve") if (show_progress and sys.stderr.isatty()) \
        else None

    results: list[IterationResult] = []
    prev_manifest: dict | None = None
    prev_tasks: dict = {}

    for it in range(1, max_iterations + 1):
        t0 = time.monotonic()
        it_dir = workdir / f"iter-{it:03d}"
        generate(doc, it_dir / "harness", target=target)
        if not dry_run:  # apply the same inference overlay control uses, so the agents match
            _apply_experiment_patch(cfg, it_dir / "harness")
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

        # Propose (free-edit the doc), then govern the edit. govern() mutates the on-disk working
        # doc during reconciliation, so `monitor` mode snapshots the proposer's edit *before*
        # governing and restores it afterward. warn-and-retry (default OFF) re-invokes the
        # proposer on a denial with a warning appended to its evidence; every attempt is audited.
        old = _snapshot(doc, it_dir / "pre.hdp")
        attempt = 0
        while True:
            manifest = proposer(doc, evidence, it)
            new = load(doc.path)
            proposed = _snapshot(new, it_dir / "post.hdp") if guard_interaction == "monitor" \
                else None
            reconciled, report = guard.govern(old, new, manifest,
                                               engine=guard_engine, mode=guard_mode)
            append_guard_audit(audit_path, iteration=it, report=report,
                               engine=guard_engine, mode=guard_mode, attempt=attempt)
            if warn_and_retry and report.denied and attempt < warn_and_retry_max_attempts:
                attempt += 1
                evidence = {**evidence, "guard_warning": _warn_text(report)}
                _restore(work_doc, old.path)  # clean pre-edit base for the retry
                doc = load(work_doc)
                continue
            break

        if guard_interaction == "monitor":  # keep the proposer's unaltered edit, denials and all
            _restore(work_doc, proposed.path)
            forward = load(work_doc)
        else:                               # enforce: today's behavior, byte-for-byte
            save(reconciled)
            forward = reconciled

        tr = track.record(forward, manifest, do_commit=do_commit)
        doc = load(doc.path)

        if run is not None:
            run.log("pass_at_1", ev.pass_rate, phase="evolve", iteration=it)
            run.log("guard_denied", len(report.denied), phase="evolve", iteration=it)
            run.log("version_bumped", 1, phase="evolve", iteration=it)

        cum_denied += len(report.denied)
        _log_iteration(logger, bar, it, ev.pass_rate, report, tr.version,
                       time.monotonic() - t0, cum_denied)

        results.append(IterationResult(it, ev.pass_rate, tr.version,
                                       len(report.denied), manifest))
        prev_manifest, prev_tasks = manifest, ev.task_results

    if bar is not None:
        bar.close()
    return results
