"""hdp.engine.port — cross-backend harness porting with an honest coverage report.

This is the missing half of the porting diagram: ``lift_H → HDP → gen_G`` where ``G ≠ H``.
Same-backend round-trip (``gen_H``) is already proven per backend; this package turns that into an
actual cross-backend port with a paper trail. It provides:

  * :func:`audit` — a pre-flight check enumerating *every* component the destination adapter cannot
    faithfully represent (unsupported types, id-keyed silent drops, destination collisions,
    unresolvable bindings), without raising and without stopping at the first issue.
  * :func:`port` — ``lift → audit → generate`` in one traceable operation that refuses to generate
    when the audit finds a blocking gap, and always writes the coverage report to disk beside the
    ported harness.

Scope: nexau ↔ mini-swe-agent only. openharness is intentionally excluded from the port infra (its
tool code lives in the installed package and its ``system_rules`` slots raise rather than drop), so
:func:`_adapter_for` rejects it — a deliberate, single-gate descope.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from hdp.engine.core.loader import HDPDoc
from hdp.engine.gen import generate
from hdp.engine.lift import lift
from hdp.engine.port.coverage import CoverageReport

NAME = "port"
PHASE = "Phase 6 (port)"

#: backends this port infra supports as source or destination (openharness is out of scope).
_SUPPORTED = ("nexau", "mini-swe-agent")


def _adapter_for(target: str):
    # Deliberately duplicated from lift/__init__.py and gen/__init__.py (adapters stay uncoupled;
    # no shared registry). Only the two supported port targets are dispatched here.
    if target == "nexau":
        from hdp.engine.adapters.nexau import NexAUAdapter
        return NexAUAdapter()
    if target == "mini-swe-agent":
        from hdp.engine.adapters.mini_swe_agent import MiniSweAgentAdapter
        return MiniSweAgentAdapter()
    raise ValueError(
        f"no port adapter for target '{target}' (supported: {list(_SUPPORTED)})"
    )


def audit(doc: HDPDoc, dest_target: str, *, source_target: str | None = None) -> CoverageReport:
    """Pre-flight coverage check: what would be lost or collided if *doc* were generated for
    *dest_target*. Never mutates *doc*; never touches disk."""
    targets = doc.model.meta.targets or []
    src = source_target or (targets[0] if targets else None)
    issues = _adapter_for(dest_target).capability_issues(doc)
    return CoverageReport(
        source_target=src,
        dest_target=dest_target,
        total_components=sum(1 for _ in doc.components()),
        issues=issues,
    )


class PortCoverageError(RuntimeError):
    def __init__(self, report: CoverageReport):
        self.report = report
        super().__init__(
            f"{len(report.blocking)} blocking issue(s) porting "
            f"{report.source_target}->{report.dest_target}"
        )


@dataclass
class PortResult:
    hdp_doc: HDPDoc
    harness_dir: Path
    coverage: CoverageReport
    generated: bool


def port(
    source_harness_dir: Path | str,
    out_dir: Path | str,
    *,
    source_target: str,
    dest_target: str,
    keep_hdp: bool = False,
    allow_partial: bool = False,
) -> PortResult:
    """``lift(source_target) → audit(dest_target) → generate(dest_target)``.

    Raises :class:`PortCoverageError` *before* generating if any BLOCKING issue exists, unless
    ``allow_partial=True``. ``silent_drop`` / ``collision`` issues never raise — they are always
    written to the coverage sidecar so a port is never silently lossy without a paper trail, but
    the operation still completes. (``allow_partial=True`` bypasses the blocking refusal; a truly
    unresolvable tool binding will then surface as ``generate``'s ``ValueError`` — honest, not
    hidden.) Making ported harnesses byte-perfect is out of scope; making gaps visible is the job.
    """
    for role, target in (("source", source_target), ("dest", dest_target)):
        if target not in _SUPPORTED:
            raise ValueError(
                f"unsupported {role}_target '{target}' (supported: {list(_SUPPORTED)})"
            )

    out_dir = Path(out_dir)
    hdp_dir = (out_dir / "_lifted.hdp") if not keep_hdp else out_dir.with_suffix(".hdp")
    doc = lift(source_harness_dir, hdp_dir, target=source_target)

    report = audit(doc, dest_target, source_target=source_target)
    if report.blocking and not allow_partial:
        raise PortCoverageError(report)

    harness_dir = (out_dir / "harness") if not keep_hdp else out_dir
    generate(doc, harness_dir, target=dest_target)

    (harness_dir / "hdp_port_coverage.json").write_text(
        json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8"
    )
    if not keep_hdp:
        shutil.rmtree(hdp_dir, ignore_errors=True)

    return PortResult(hdp_doc=doc, harness_dir=harness_dir, coverage=report, generated=True)


def smoke_step(run) -> None:
    """Phase 0 dry-pipeline stub (the real path is :func:`port`)."""
    run.log("port_issues_found", 0, phase="port")
