"""hdp.engine.lift — backend harness → HDP document (ask #1: "Build HDP").

``lift(harness_dir, out_dir, target)`` dispatches to the framework adapter, which maps the
backend layout to ETCLOVG deterministically, then (optionally) runs an LLM pass to fill only
what structure can't give. The result validates against the typed model before being written.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from hdp.engine.core.loader import HDPDoc

NAME = "lift"
PHASE = "Phase 2 (lift)"

Strategy = Literal["heuristic", "llm_assisted", "agentic"]


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
    raise ValueError(f"no lifter for target '{target}'")


def lift(harness_dir: Path | str, out_dir: Path | str, *, target: str = "nexau",
         strategy: Strategy = "heuristic",
         llm: Callable[[str], str] | None = None, **kw) -> HDPDoc:
    """Recover an HDP document from *harness_dir*, writing it to *out_dir*; return the doc.

    Three strategies on a cost/fidelity spectrum:

    * ``heuristic`` (default) — today's deterministic structural mapping; *llm* is ignored.
    * ``llm_assisted`` — the deterministic mapping, then an LLM pass that resolves only the
      fields the mapper marked uncertain. Requires *llm*.
    * ``agentic`` — for a harness the mapper doesn't recognize, an LLM reads the repo broadly
      and proposes the document structure directly. Requires *llm*.

    ``llm_assisted``/``agentic`` raise ``ValueError`` if *llm* is None (fail loud, no fallback).
    """
    adapter = _adapter_for(target)
    harness_dir, out_dir = Path(harness_dir), Path(out_dir)
    if strategy == "heuristic":
        return adapter.lift(harness_dir, out_dir, **kw)          # llm ignored, by design
    if strategy == "llm_assisted":
        if llm is None:
            raise ValueError("strategy='llm_assisted' requires an llm callable (got None)")
        return adapter.lift(harness_dir, out_dir, llm=llm, **kw)
    if strategy == "agentic":
        if llm is None:
            raise ValueError("strategy='agentic' requires an llm callable (got None)")
        return adapter.agentic_lift(harness_dir, out_dir, llm, **kw)
    raise ValueError(f"unknown lift strategy {strategy!r}")


def smoke_step(run) -> None:
    """Phase 0 dry-pipeline stub (the real path is :func:`lift`)."""
    run.log("lift_components", 9, phase="lift")
