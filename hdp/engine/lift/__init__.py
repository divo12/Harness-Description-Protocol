"""hdp.engine.lift — backend harness → HDP document (ask #1: "Build HDP").

``lift(harness_dir, out_dir, target)`` dispatches to the framework adapter, which maps the
backend layout to ETCLOVG deterministically, then (optionally) runs an LLM pass to fill only
what structure can't give. The result validates against the typed model before being written.
"""
from __future__ import annotations

from pathlib import Path

from hdp.engine.core.loader import HDPDoc

NAME = "lift"
PHASE = "Phase 2 (lift)"


def _adapter_for(target: str):
    from hdp.engine.adapters.nexau import NexAUAdapter

    if target == "nexau":
        return NexAUAdapter()
    raise ValueError(f"no lifter for target '{target}'")


def lift(harness_dir: Path | str, out_dir: Path | str, target: str = "nexau", **kw) -> HDPDoc:
    """Recover an HDP document from *harness_dir*, writing it to *out_dir*; return the doc."""
    return _adapter_for(target).lift(Path(harness_dir), Path(out_dir), **kw)


def smoke_step(run) -> None:
    """Phase 0 dry-pipeline stub (the real path is :func:`lift`)."""
    run.log("lift_components", 9, phase="lift")
