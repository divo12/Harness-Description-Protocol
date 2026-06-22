"""hdp.engine.gen — HDP document → backend harness (the keystone).

``generate(doc, out_dir, target)`` dispatches to the matching framework adapter, which
renders the harness (Jinja2 templates live under ``gen/templates/``). Faithful generation
is the Phase 1 acceptance: ``generate(code-agent-simple.hdp)`` reproduces the NexAU seed,
which scores the known gpt-5.2 Terminal-Bench 2 baseline.
"""
from __future__ import annotations

from pathlib import Path

from hdp.engine.core.loader import HDPDoc

NAME = "gen"
PHASE = "Phase 1 (core+gen)"


def _adapter_for(target: str):
    from hdp.engine.adapters.nexau import NexAUAdapter

    if target == "nexau":
        return NexAUAdapter()
    if target == "mini-swe-agent":
        from hdp.engine.adapters.mini_swe_agent import MiniSweAgentAdapter
        return MiniSweAgentAdapter()
    if target == "openharness":
        from hdp.engine.adapters.openharness import OpenHarnessAdapter
        return OpenHarnessAdapter()
    raise ValueError(f"no generator for target '{target}'")


def generate(doc: HDPDoc, out_dir: Path | str, target: str = "nexau") -> Path:
    """Compile *doc* into a harness under *out_dir* for *target*; return *out_dir*."""
    return _adapter_for(target).generate(doc, Path(out_dir))


def smoke_step(run) -> None:
    """Phase 0 dry-pipeline stub (the real path is :func:`generate`)."""
    run.log("gen_files", 6, phase="gen")
