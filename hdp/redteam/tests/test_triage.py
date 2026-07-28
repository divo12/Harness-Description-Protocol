"""$0, CI-safe tests for hdp.redteam.triage — no live LLM call in any path.

Split across two examples per the design: the triage-shortlist demo runs on openharness (three
tools of varying blast_radius), while resolve_source's real-code resolution is verified on
code-agent-simple's run-shell tool (a checked-in bundled adapter asset).
"""
from __future__ import annotations

import json
from pathlib import Path

from hdp.engine.core.loader import load
from hdp.redteam import EditCandidate
from hdp.redteam.triage import (
    CodeAwareReport,
    code_aware_redteam,
    resolve_source,
    triage_components,
)

REPO = Path(__file__).resolve().parents[3]
OPENHARNESS = REPO / "hdp" / "examples" / "openharness.hdp"
CODE_AGENT = REPO / "hdp" / "examples" / "code-agent-simple.hdp"


def _triage_reply(shortlist: set[str], all_ids: list[str]) -> str:
    return json.dumps({"triage": [
        {"component_id": cid, "shortlisted": cid in shortlist,
         "reason": "high blast_radius" if cid in shortlist else "low risk"}
        for cid in all_ids
    ]})


def test_triage_shortlist_strictly_smaller_openharness():
    doc = load(OPENHARNESS)
    all_ids = [c.id for _, c in doc.components()]
    # the three system-blast components are the natural shortlist; read-file/context/loop are not
    shortlist = {"bash", "write-file", "sandbox"}

    def fake_llm(prompt: str) -> str:
        assert "blast_radius" in prompt
        return _triage_reply(shortlist, all_ids)

    results = triage_components(doc, fake_llm)
    assert len(results) == len(all_ids)  # one verdict per component
    shortlisted = [t.component_id for t in results if t.shortlisted]
    assert set(shortlisted) == shortlist
    assert 0 < len(shortlisted) < len(all_ids)  # strictly smaller than the full component set


def test_resolve_source_real_code_run_shell():
    doc = load(CODE_AGENT)
    src = resolve_source(doc, "run-shell")  # tools.shell_tools:run_shell_command -> bundled asset
    assert src is not None
    assert "def run_shell_command(" in src  # known snippet of the resolved source


def test_resolve_source_embedded_file():
    doc = load(CODE_AGENT)
    src = resolve_source(doc, "system-rules-core")  # file: ./context/system-rules.md
    assert src is not None and src.strip()


def test_resolve_source_none_for_nameonly_tool():
    # openharness tools are name-only (no ref) — resolve_source honestly returns None, which
    # documents the coverage blind spot rather than fabricating source.
    doc = load(OPENHARNESS)
    assert resolve_source(doc, "bash") is None


def test_code_aware_redteam_returns_edit_candidates():
    doc = load(CODE_AGENT)
    all_ids = [c.id for _, c in doc.components()]
    candidates_reply = json.dumps({"candidates": [
        {"id": "ca1", "rationale": "shell tool has no command allowlist", "operator": "update",
         "layer": "tooling", "component_id": "run-shell", "expect_denied": True},
    ]})

    def fake_llm(prompt: str) -> str:
        # first call is triage, second is generation — the generation prompt uniquely carries
        # the resolved source under a "Shortlisted components" heading.
        if "Shortlisted components" in prompt:
            return candidates_reply
        return _triage_reply({"run-shell"}, all_ids)

    report = code_aware_redteam(doc, fake_llm)
    assert isinstance(report, CodeAwareReport)
    assert any(t.shortlisted for t in report.triage)
    # the shortlisted tool's real source was resolved and carried into context
    assert "run-shell" in report.resolved
    assert "def run_shell_command(" in report.resolved["run-shell"]
    assert report.candidates and isinstance(report.candidates[0], EditCandidate)
    assert report.candidates[0].component_id == "run-shell"
