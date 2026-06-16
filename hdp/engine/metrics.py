"""hdp.engine.metrics — the single metrics sink for the HDP engine.

Every module logs through a :class:`Run`. Each metric event does exactly three things
(implementation brief, "Logging & metrics"):

  1. update the live tqdm postfix,
  2. emit a stdlib ``logging`` line (via ``tqdm.write`` so progress bars aren't broken),
  3. append one JSONL record to ``runs/<run_id>/metrics.jsonl``.

No metric is computed without being logged through this sink. The Phase 5 A/B table is
built ONLY by reading ``metrics.jsonl`` (see :func:`Run.read_metrics`) — never re-derived.

Each run also writes ``runs/<run_id>/run.json`` (config snapshot, git SHA, arm, seed,
start/end) so any result is traceable to exact inputs. No external tracking service.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tqdm import tqdm

logger = logging.getLogger("hdp.engine")

# .../agentic-harness-engineering  (repo root: hdp/engine/metrics.py -> parents[2])
REPO_ROOT = Path(__file__).resolve().parents[2]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


_SECRET_KEY = re.compile(r"api[_-]?key|secret|token|password|webhook|\bkey\b", re.IGNORECASE)


def _redact_secrets(obj: Any) -> Any:
    """Recursively mask values under secret-looking keys so resolved ${ENV} credentials
    never land in run.json (constraint 5: no secrets on disk)."""
    if isinstance(obj, dict):
        return {
            k: ("***redacted***" if isinstance(k, str) and _SECRET_KEY.search(k)
                else _redact_secrets(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact_secrets(v) for v in obj]
    return obj


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


class Run:
    """A single run scope: owns ``runs/<run_id>/`` with its metrics sink and header.

    One :class:`Run` corresponds to one arm of one invocation; ``arm`` rides on every
    metric record so the bench table can be reconstructed from JSONL alone.
    """

    def __init__(
        self,
        run_id: str,
        arm: str,
        *,
        seed: int = 0,
        config: dict | None = None,
        runs_dir: Path | None = None,
        phase: str = "init",
    ) -> None:
        self.run_id = run_id
        self.arm = arm
        self.seed = seed
        self.phase = phase
        self.iteration: int | None = None

        base = Path(runs_dir) if runs_dir is not None else REPO_ROOT / "runs"
        self.dir = base / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.metrics_path = self.dir / "metrics.jsonl"
        self.header_path = self.dir / "run.json"

        self._bar: tqdm | None = None
        self._postfix: dict[str, Any] = {}
        self._header = {
            "run_id": run_id,
            "arm": arm,
            "seed": seed,
            "git_sha": _git_sha(),
            "config": _redact_secrets(config or {}),
            "start": _utc_now(),
            "end": None,
        }
        self._write_header()

    # -- header -----------------------------------------------------------------
    def _write_header(self) -> None:
        self.header_path.write_text(
            json.dumps(self._header, indent=2, default=str), encoding="utf-8"
        )

    def set_phase(self, phase: str, iteration: int | None = None) -> None:
        self.phase = phase
        if iteration is not None:
            self.iteration = iteration

    # -- the one logging entry point -------------------------------------------
    def log(
        self,
        metric: str,
        value: Any,
        *,
        phase: str | None = None,
        iteration: int | None = None,
        component_id: str | None = None,
        change_id: str | None = None,
    ) -> dict:
        rec: dict[str, Any] = {
            "ts": _utc_now(),
            "run_id": self.run_id,
            "arm": self.arm,
            "phase": phase or self.phase,
            "iteration": iteration if iteration is not None else self.iteration,
            "metric": metric,
            "value": value,
        }
        if component_id is not None:
            rec["component_id"] = component_id
        if change_id is not None:
            rec["change_id"] = change_id

        # 3. append to JSONL sink
        with self.metrics_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")

        # 1. live tqdm postfix
        self._postfix[metric] = value
        if self._bar is not None:
            self._bar.set_postfix(self._postfix, refresh=True)

        # 2. stdlib logging line, via tqdm.write so bars aren't broken
        extra = "".join(
            f" {k}={rec[k]}" for k in ("component_id", "change_id") if k in rec
        )
        line = f"[{rec['arm']}/{rec['phase']}] {metric}={value}{extra}"
        tqdm.write(line)
        logger.info(line)
        return rec

    # -- progress ---------------------------------------------------------------
    def progress(
        self, iterable: Iterable, desc: str, total: int | None = None, **postfix: Any
    ) -> Iterator:
        """Wrap *iterable* in a tqdm bar bound to this run's postfix."""
        self._postfix.update(postfix)
        self._bar = tqdm(iterable, desc=desc, total=total, postfix=self._postfix or None)
        try:
            yield from self._bar
        finally:
            self._bar.close()
            self._bar = None

    # -- lifecycle --------------------------------------------------------------
    def close(self) -> None:
        self._header["end"] = _utc_now()
        self._write_header()

    def __enter__(self) -> Run:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- read side (bench table is built from this, never re-derived) -----------
    @staticmethod
    def read_metrics(run_dir: Path | str) -> list[dict]:
        path = Path(run_dir) / "metrics.jsonl"
        if not path.exists():
            return []
        out: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out
