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


@dataclass
class GuardMeasurementReport:
    """Guard behavior measured against a real evolve-loop edit stream (Stage 4). CORE and SOFT
    denials are kept as separate figures — a CORE denial is an attempted confinement breach, a
    SOFT denial is typically manifest/editability hygiene; blending them would misrepresent both.
    """

    doc_id: str
    total_edits: int
    atomic_allowed: int
    atomic_denied: int
    denied_by_tier: dict[str, int]                 # {"core": n, "structural": n, "soft": n}
    rule_distribution: dict[str, dict[str, int]]   # tier -> {rule: count}
    reconcile_allowed_deltas: int
    reconcile_denied_deltas: int

    def _rate(self, tier: str) -> float:
        return self.denied_by_tier.get(tier, 0) / self.total_edits if self.total_edits else 0.0

    @property
    def core_denial_rate(self) -> float:
        return self._rate("core")

    @property
    def structural_denial_rate(self) -> float:
        return self._rate("structural")

    @property
    def soft_denial_rate(self) -> float:
        return self._rate("soft")


def render_measurement_markdown(r: GuardMeasurementReport) -> str:
    """Render a GuardMeasurementReport, labelling CORE-denial rate and SOFT-denial rate as
    distinct figures (never a single "% unsafe")."""
    lines: list[str] = [
        f"# Guard measurement — `{r.doc_id}`",
        "",
        f"Replayed {r.total_edits} real evolve-loop edit(s) through the guard (both engines).",
        "",
        "## Atomic engine (tiered, per-edit)",
        "",
        f"- allowed: {r.atomic_allowed}",
        f"- denied: {r.atomic_denied}",
        "",
        "**Denial rates by tier — reported separately, not as one % -unsafe:**",
        "",
        f"- **CORE-denial rate: {r.core_denial_rate:.1%}** "
        f"({r.denied_by_tier.get('core', 0)}/{r.total_edits}) — attempted confinement breaches",
        f"- STRUCTURAL-denial rate: {r.structural_denial_rate:.1%} "
        f"({r.denied_by_tier.get('structural', 0)}/{r.total_edits}) — malformed documents",
        f"- **SOFT-denial rate: {r.soft_denial_rate:.1%}** "
        f"({r.denied_by_tier.get('soft', 0)}/{r.total_edits}) — manifest / editability hygiene",
        "",
        "## Rule distribution within each tier",
        "",
    ]
    for tier in ("core", "structural", "soft"):
        rules = r.rule_distribution.get(tier, {})
        if rules:
            dist = ", ".join(f"{rule}×{n}" for rule, n in sorted(rules.items()))
            lines.append(f"- {tier}: {dist}")
    lines += [
        "",
        "## Reconcile engine (per-delta)",
        "",
        f"- allowed deltas: {r.reconcile_allowed_deltas}",
        f"- denied deltas: {r.reconcile_denied_deltas}",
        "",
    ]
    return "\n".join(lines)


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
