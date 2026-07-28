"""hdp.redteam — LLM-generated adversarial edits replayed through the real guard.

Supplements the hand-written fixture suite (``hdp/engine/tests/test_guard_redteam.py``) with an
LLM that *proposes* candidate HDP-document edits, then replays every candidate through
``guard.govern`` under both engines and both modes. Detect-and-report only: a slip-through is a
surfaced finding, never a change to the guard.
"""
from hdp.redteam.llm_gen import EditCandidate, propose_candidates, run_candidates
from hdp.redteam.report import CandidateOutcome, RedTeamReport, render_markdown
from hdp.redteam.triage import (
    CodeAwareReport,
    TriageResult,
    code_aware_redteam,
    resolve_source,
    triage_components,
)

__all__ = [
    "EditCandidate",
    "CandidateOutcome",
    "RedTeamReport",
    "propose_candidates",
    "run_candidates",
    "render_markdown",
    "TriageResult",
    "CodeAwareReport",
    "triage_components",
    "resolve_source",
    "code_aware_redteam",
]
