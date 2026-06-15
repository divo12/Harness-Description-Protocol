"""hdp.engine.eval — the eval seam (boundary decision: reuse evolve.py by direct import).

This is the ONE place the HDP engine reaches into ``evolve.py`` for backend-coupled machinery
(harbor rollout on E2B + pass@1 stats). It evaluates a *generated* harness directory — the
output of ``gen`` — exactly as the AHE loop evaluates a workspace. Keeping it a single, narrow
function is deliberate: this call site is the seam we extract into a shared module later, once
real use has proven the boundary (design §Implementation order, step 4).

``--dry-run`` short-circuits to the config's ``fake_reward`` so the whole loop wires and tests
for $0; the real path runs harbor (LLM + E2B spend) and is exercised only in a live campaign.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EvalResult:
    pass_rate: float
    job_dir: Path | None = None
    stats: dict = field(default_factory=dict)

    @property
    def task_results(self) -> dict:
        """Per-task pass/fail map (for attest's flipped/regressed diff), or {} under dry-run."""
        return self.stats.get("task_results", {}) if self.stats else {}


def _agent_config_filename(cfg: dict) -> str:
    # The NexAU generator always writes the assembled manifest as code_agent.yaml.
    return cfg.get("agent_config_filename", "code_agent.yaml")


def eval_harness(cfg: dict, harness_dir: Path | str | None, iteration_dir: Path | str | None,
                 *, dry_run: bool = False, fake_reward: float = 1.0) -> EvalResult:
    """Evaluate a generated harness: pass@1 via harbor + compute_stats. Dry-run returns
    ``fake_reward`` without importing evolve or touching the network."""
    if dry_run:
        return EvalResult(pass_rate=float(fake_reward))

    if harness_dir is None or iteration_dir is None:
        raise ValueError("eval_harness needs harness_dir and iteration_dir unless dry_run=True")

    # Imported lazily and locally: the seam to evolve.py lives here and nowhere else.
    from evolve import compute_stats, run_harbor

    k = int((cfg.get("harbor") or {}).get("k", 1))
    job_dir = run_harbor(cfg, Path(harness_dir), _agent_config_filename(cfg), Path(iteration_dir))
    stats = compute_stats(job_dir, k=k)
    return EvalResult(pass_rate=float(stats["pass_rate"]), job_dir=job_dir, stats=stats)
