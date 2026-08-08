"""Stage 1 acceptance — pluggable lift strategies (heuristic / llm_assisted / agentic).

All $0: no test here makes a live LLM call. The llm_assisted/agentic paths are exercised with a
plain fake ``llm`` callable (a Python function with a call counter), never a real API.

The load-bearing guarantee is the regression guard: ``strategy="heuristic"`` (and the omitted
default) must reproduce today's deterministic mapping exactly — the uncertainty-sentinel plumbing
is invisible unless an ``llm`` is supplied.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from hdp.engine.adapters import mini_swe_agent as mini_mod
from hdp.engine.adapters import nexau as nexau_mod
from hdp.engine.adapters import openharness as oh_mod
from hdp.engine.adapters.uncertain import Uncertain, finalize
from hdp.engine.lift import lift

REPO = Path(__file__).resolve().parents[3]
SEED = REPO / "agents" / "code_agent_simple"


# --------------------------------------------------------------------------- regression guard
def test_heuristic_matches_default(tmp_path):
    """strategy='heuristic' and the omitted default produce byte-identical output — the
    sentinel finalize step restores exactly today's concrete defaults."""
    default_doc = lift(SEED, tmp_path / "default.hdp")                       # no strategy arg
    heuristic_doc = lift(SEED, tmp_path / "heuristic.hdp", strategy="heuristic")

    assert (tmp_path / "default.hdp" / "hdp.yaml").read_bytes() == \
           (tmp_path / "heuristic.hdp" / "hdp.yaml").read_bytes()
    assert {c.id for _l, c in default_doc.components()} == \
           {c.id for _l, c in heuristic_doc.components()}
    # confident values are untouched by the sentinel machinery
    assert heuristic_doc.component("run-shell-command").blast_radius.value == "system"


def test_heuristic_ignores_llm(tmp_path):
    """heuristic mode never invokes a passed llm (it's documented as ignored)."""
    calls: list[str] = []

    def fake_llm(prompt: str) -> str:
        calls.append(prompt)
        return "external"

    lift(SEED, tmp_path / "h.hdp", strategy="heuristic", llm=fake_llm)
    assert calls == []


# --------------------------------------------------------------------------- fail-loud contract
def test_llm_assisted_requires_llm(tmp_path):
    with pytest.raises(ValueError, match="llm_assisted"):
        lift(SEED, tmp_path / "x.hdp", strategy="llm_assisted")


def test_agentic_requires_llm(tmp_path):
    with pytest.raises(ValueError, match="agentic"):
        lift(SEED, tmp_path / "x.hdp", strategy="agentic")


# --------------------------------------------------------------------------- sentinel emission
def test_infer_blast_radius_emits_sentinel_when_unsure():
    # confident branch -> concrete string (no sentinel)
    assert nexau_mod._infer_blast_radius("run_shell_command", "tools.shell:run") == "system"
    assert mini_mod._infer_blast_radius("bash", "env:execute") == "system"
    assert oh_mod._infer_blast_radius("reader", "pkg:Reader", True) == "read_only"

    # no signal -> Uncertain sentinel, same shape in every adapter
    for unc in (nexau_mod._infer_blast_radius("mystery", "pkg:Thing"),
                mini_mod._infer_blast_radius("mystery", "pkg:Thing"),
                oh_mod._infer_blast_radius("mystery", "pkg:Thing", False)):
        assert isinstance(unc, Uncertain)
        assert unc.default == "session"
        assert "session" in unc.candidates


def test_nexau_middleware_hook_sentinel():
    """An undeclared middleware hook is a guess-point -> the NexAU mapper emits the same
    sentinel (heuristic collapses it back to 'before_tool')."""
    hook = Uncertain(default="before_tool", reason="x", candidates=nexau_mod._HOOK_KINDS)
    assert "before_tool" in hook.candidates and "after_model" in hook.candidates
    doc = {"layers": {"lifecycle": [{"id": "mw", "type": "middleware", "hook": hook}]}}
    finalize(doc)
    assert doc["layers"]["lifecycle"][0]["hook"] == "before_tool"


# ------------------------------------------------------------------- _llm_refine (fake llm)
def _tooling_doc(unc: Uncertain) -> dict:
    return {"layers": {"tooling": [{"id": "t", "blast_radius": unc}]}}


@pytest.mark.parametrize("adapter", [
    nexau_mod.NexAUAdapter(), mini_mod.MiniSweAgentAdapter(), oh_mod.OpenHarnessAdapter()])
def test_llm_refine_resolves_sentinel(adapter):
    """A valid llm answer replaces the sentinel; the llm is actually invoked."""
    calls: list[str] = []

    def fake_llm(prompt: str) -> str:
        calls.append(prompt)
        return "local\n"     # a legal blast_radius, with whitespace to be stripped

    unc = Uncertain(default="session", reason="r", candidates=tuple(nexau_mod._BLAST_ORDER))
    doc = _tooling_doc(unc)
    out = adapter._llm_refine(doc, Path("."), fake_llm)
    assert out["layers"]["tooling"][0]["blast_radius"] == "local"
    assert len(calls) == 1


def test_llm_refine_rejects_out_of_candidates_then_finalizes_to_default():
    """An answer outside the candidate set is not applied; finalize collapses to the default."""
    def fake_llm(prompt: str) -> str:
        return "not_a_blast_radius"

    unc = Uncertain(default="session", reason="r", candidates=("session", "system"))
    doc = {"x": unc}
    nexau_mod.NexAUAdapter()._llm_refine(doc, Path("."), fake_llm)
    assert isinstance(doc["x"], Uncertain)     # rejected, still a sentinel
    finalize(doc)
    assert doc["x"] == "session"               # conservative default, never invalid


# --------------------------------------------------------------- llm_assisted end-to-end
def _synthetic_nexau_harness(d: Path) -> Path:
    """A minimal NexAU harness whose single tool has no blast-radius signal -> Uncertain."""
    d.mkdir(parents=True, exist_ok=True)
    (d / "sp.md").write_text("You are a helpful agent.", encoding="utf-8")
    (d / "code_agent.yaml").write_text(
        "name: syn\n"
        "system_prompt: ./sp.md\n"
        "tools:\n"
        "  - name: mystery_tool\n"
        "    binding: pkg.mystery:Thing\n",
        encoding="utf-8")
    return d


def test_llm_assisted_resolves_uncertain_blast_radius(tmp_path):
    harness = _synthetic_nexau_harness(tmp_path / "harness")
    calls: list[str] = []

    def fake_llm(prompt: str) -> str:
        calls.append(prompt)
        return "local"

    assisted = lift(harness, tmp_path / "assisted.hdp", strategy="llm_assisted", llm=fake_llm)
    assert assisted.component("mystery-tool").blast_radius.value == "local"
    assert len(calls) >= 1                       # llm was genuinely invoked

    # heuristic on the same harness leaves the conservative default
    heuristic = lift(harness, tmp_path / "heuristic.hdp")
    assert heuristic.component("mystery-tool").blast_radius.value == "session"


# ----------------------------------------------------------------------- agentic (fake llm)
def test_agentic_produces_valid_doc(tmp_path):
    """The LLM proposes a full HDP doc; a schema-valid proposal is written. We feed back a
    known-valid document (obtained by lifting the seed) as the fake proposal."""
    ref = lift(SEED, tmp_path / "ref.hdp")
    ref_dict = yaml.safe_load((tmp_path / "ref.hdp" / "hdp.yaml").read_text())
    proposal = json.dumps(ref_dict)
    calls: list[str] = []

    def fake_llm(prompt: str) -> str:
        calls.append(prompt)
        return proposal

    empty_repo = tmp_path / "unknown_repo"
    empty_repo.mkdir()
    doc = lift(empty_repo, tmp_path / "agentic.hdp", strategy="agentic", llm=fake_llm)
    assert len(calls) == 1
    assert (tmp_path / "agentic.hdp" / "hdp.yaml").exists()
    assert {c.id for _l, c in doc.components()} == {c.id for _l, c in ref.components()}


def test_agentic_invalid_proposal_raises(tmp_path):
    empty_repo = tmp_path / "unknown_repo"
    empty_repo.mkdir()

    # not JSON at all -> parse failure (fail loud, never coerced)
    with pytest.raises(ValueError):
        lift(empty_repo, tmp_path / "a.hdp", strategy="agentic", llm=lambda p: "no json here")

    # valid JSON but not a valid HDP document -> typed-model validation raises
    with pytest.raises(ValidationError):
        lift(empty_repo, tmp_path / "b.hdp", strategy="agentic", llm=lambda p: '{"nonsense": true}')
