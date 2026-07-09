"""hdp.engine.loop_logging — durable, human-inspectable guard-verdict logging for the evolve loop.

Keeps :mod:`hdp.engine.loop` readable by owning three concerns:

  * ``setup_evolve_logger`` — an INFO-level ``evolve.log`` file handler (stdlib ``logging``),
  * ``append_guard_audit`` — one JSONL row per :class:`~hdp.engine.guard.GuardDecision`,
  * ``_log_iteration`` — one human-readable summary line per iteration (and tqdm postfix).

None of this changes guard evaluation; it only persists what ``guard.govern`` already produces.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tqdm import tqdm


def setup_evolve_logger(log_dir: Path) -> logging.Logger:
    """INFO-level ``FileHandler`` at ``log_dir/evolve.log``.

    Idempotent: uses a dedicated ``hdp.engine.evolve`` logger and clears prior handlers so
    repeated ``evolve()`` calls in one process (e.g. the test suite) don't stack handlers or
    leak open files.
    """
    logger = logging.getLogger("hdp.engine.evolve")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    fh = logging.FileHandler(log_dir / "evolve.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)
    return logger


def append_guard_audit(audit_path: Path, *, iteration: int, report, engine: str,
                       mode: str, attempt: int = 0) -> None:
    """Append ONE JSONL line per ``GuardDecision`` (``report.decisions`` — allowed AND denied).

    One growing file across the whole run, easy to ``tail``/``grep``. Each row carries the full
    itemized verdict the guard produced for a single component delta.
    """
    ts = datetime.now(UTC).isoformat()
    with audit_path.open("a", encoding="utf-8") as f:
        for d in report.decisions:
            f.write(json.dumps({
                "ts": ts, "iteration": iteration, "attempt": attempt,
                "component_id": d.component_id, "layer": d.layer, "change": d.change,
                "allowed": d.allowed, "reason": d.reason, "operator": d.operator,
                "engine": engine, "mode": mode,
            }) + "\n")


def _log_iteration(logger: logging.Logger, bar: tqdm | None, iteration: int, pass_rate: float,
                   report, version: str, elapsed: float, cum_denied: int) -> None:
    """Emit one legible summary line for *iteration* to ``evolve.log`` (and, if a progress bar
    is live, mirror it via ``tqdm.write`` and refresh the bar's postfix)."""
    allowed = report.allowed
    denied = report.denied
    summary = (f"iter {iteration}: pass_rate={pass_rate:.4g} edits={len(report.decisions)} "
               f"allowed={len(allowed)} denied={len(denied)} version={version} "
               f"elapsed={elapsed:.2f}s")
    if denied:
        detail = "; ".join(f"{d.component_id} ({d.layer}): {d.reason}" for d in denied)
        summary += f" | denials: {detail}"
    logger.info(summary)
    if bar is not None:
        postfix: dict[str, Any] = {"pass_rate": round(pass_rate, 4), "guard_denied": cum_denied}
        bar.set_postfix(postfix, refresh=True)
        tqdm.write(summary)
        bar.update(1)
