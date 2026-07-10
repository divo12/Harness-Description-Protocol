"""hdp.redteam.report — outcome records for a red-team replay and a Markdown renderer.

A :class:`RedTeamReport` holds the per-candidate × (engine, mode) :class:`CandidateOutcome`
rows produced by :func:`hdp.redteam.llm_gen.run_candidates`, plus the ``.bypasses`` view — the
security-meaningful slip-throughs. Rendering is detached from replay so the same report can be
tabulated without re-running the guard.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid a runtime import cycle with llm_gen (which imports this module)
    from hdp.redteam.llm_gen import EditCandidate


@dataclass
class CandidateOutcome:
    """One candidate replayed under one (engine, mode) pair through ``guard.govern``."""

    candidate_id: str
    engine: str          # reconcile | atomic
    mode: str            # enforce | review
    denied: bool
    tier: str | None     # parsed from the guard reason (atomic engine only); else None
    rule: str | None
    raw_reason: str
    tree_unchanged: bool  # did the governed tree end byte-identical to OLD?


@dataclass
class RedTeamReport:
    doc_id: str
    candidates: list[EditCandidate] = field(default_factory=list)
    outcomes: list[CandidateOutcome] = field(default_factory=list)

    @property
    def bypasses(self) -> list[EditCandidate]:
        """Candidates the guard ALLOWED in ``enforce`` mode (under either engine) even though the
        LLM expected a denial (``expect_denied=True``) — the security-meaningful slip-throughs,
        not merely every correctly-allowed edit."""
        by_id = {c.id: c for c in self.candidates}
        out: dict[str, EditCandidate] = {}
        for o in self.outcomes:
            if o.mode != "enforce" or o.denied:
                continue
            c = by_id.get(o.candidate_id)
            if c is not None and c.expect_denied:
                out[c.id] = c
        return list(out.values())


def render_markdown(report: RedTeamReport) -> str:
    """Render a report as a Markdown table plus an explicit Bypasses section."""
    lines: list[str] = [
        f"# Red-team replay — `{report.doc_id}`",
        "",
        f"{len(report.candidates)} candidate(s), {len(report.outcomes)} outcome(s).",
        "",
        "| candidate | engine | mode | denied | tier | rule | tree_unchanged |",
        "|---|---|---|---|---|---|---|",
    ]
    for o in report.outcomes:
        lines.append(
            f"| {o.candidate_id} | {o.engine} | {o.mode} | "
            f"{'yes' if o.denied else 'no'} | {o.tier or '-'} | {o.rule or '-'} | "
            f"{'yes' if o.tree_unchanged else 'no'} |"
        )

    bypasses = report.bypasses
    lines += ["", f"## Bypasses ({len(bypasses)})", ""]
    if not bypasses:
        lines.append("_None — every expected-denied candidate was denied in enforce mode._")
    else:
        lines.append(
            "Candidates the LLM expected to be denied but the guard ALLOWED in enforce mode:"
        )
        lines.append("")
        for c in bypasses:
            lines.append(
                f"- **{c.id}** ({c.operator} {c.layer}/{c.component_id}) — {c.rationale}"
            )
    lines.append("")
    return "\n".join(lines)
