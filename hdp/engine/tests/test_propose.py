"""Tests for hdp.engine.propose — the retargeted-evolve_agent proposer ($0, runner injected)."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from hdp.engine import loop, propose
from hdp.engine.core.loader import load, save
from hdp.engine.eval import EvalResult

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "hdp" / "examples" / "code-agent-simple.hdp"


@pytest.fixture()
def doc(tmp_path):
    dst = tmp_path / "doc.hdp"
    shutil.copytree(EXAMPLE, dst)
    return load(dst)


def test_build_query_carries_governance_and_evidence(doc):
    q = propose.build_query(doc, {"pass_rate": 0.62, "attestation": "chg-1: effective"}, 3)
    assert "iteration 3" in q
    assert "hdp-evolution-guide" in q
    assert "read_only" in q and "verification" in q   # governance surfaced
    assert "0.62" in q
    assert "chg-1: effective" in q                     # attestation surfaced


def test_read_manifest_missing_raises(doc):
    with pytest.raises(FileNotFoundError):
        propose.read_manifest(doc, 9)


def test_proposer_runs_agent_then_reads_manifest(doc):
    # fake runner = what the retargeted evolve_agent would do: edit the doc + write a manifest.
    def fake_runner(d, query, iteration):
        mem = next(c for c in d.raw["layers"]["context"] if c["id"] == "long-term-memory")
        mem["description"] = "agent edit"
        save(d)
        mpath = d.path / "evolution" / "manifests" / f"{iteration:03d}.json"
        mpath.parent.mkdir(parents=True, exist_ok=True)
        mpath.write_text(json.dumps({
            "hdp": "0.1", "iteration": iteration,
            "changes": [{"change_id": f"chg-{iteration}", "operator": "update",
                         "component_id": "long-term-memory", "layer": "context",
                         "failure_evidence": "e", "root_cause": "r",
                         "repair_spec": {"editable_resources": ["x"], "validation_criteria": ["y"]},
                         "prediction": {"expected_fixes": ["t1"], "at_risk_regressions": []}}],
        }))
        return "done"

    proposer = propose.EvolveAgentProposer(agent_runner=fake_runner)
    manifest = proposer(doc, {"pass_rate": 0.5}, 1)
    assert manifest["changes"][0]["operator"] == "update"
    assert load(doc.path).component("long-term-memory").description == "agent edit"


def test_proposer_plugs_into_the_loop(tmp_path):
    """The real proposer class drives the full loop (with an injected runner) — same slot."""
    def fake_runner(d, query, iteration):
        # an editable-but-non-protected-field edit on a protected component is allowed as 'update'
        d.raw["layers"]["context"].append({
            "id": f"skill-{iteration}", "type": "skill",
            "file": "./context/system-rules.md", "name": f"s{iteration}"})
        save(d)
        mpath = d.path / "evolution" / "manifests" / f"{iteration:03d}.json"
        mpath.parent.mkdir(parents=True, exist_ok=True)
        mpath.write_text(json.dumps({
            "hdp": "0.1", "iteration": iteration,
            "changes": [{"change_id": f"chg-{iteration}", "operator": "add",
                         "component_id": f"skill-{iteration}", "layer": "context",
                         "failure_evidence": "e", "root_cause": "r",
                         "repair_spec": {"editable_resources": ["x"], "validation_criteria": ["y"]},
                         "prediction": {"expected_fixes": [], "at_risk_regressions": []}}],
        }))
        return "done"

    cfg = {"hdp": {"document": str(EXAMPLE), "target": "nexau"}, "run": {"smoke": {"fake_reward": 1.0}}}
    def eval_fn(*a, **k):
        return EvalResult(pass_rate=1.0, stats={"task_results": {"t1": 1}})
    results = loop.evolve(cfg, proposer=propose.EvolveAgentProposer(agent_runner=fake_runner),
                          workdir=tmp_path, dry_run=True, max_iterations=1, eval_fn=eval_fn)
    assert results[0].guard_denied == 0
    assert results[0].version == "1.1.0"   # an 'add' → minor bump


def test_setup_treatment_agent_swaps_skill(tmp_path):
    from ruamel.yaml import YAML
    cfg_path = propose.setup_treatment_agent(tmp_path)
    assert cfg_path.is_file() and cfg_path.name == "evolve_agent.yaml"
    raw = YAML().load(cfg_path.read_text())
    assert raw["skills"] == ["./skills/hdp-evolution-guide"]          # retargeted
    assert "nexau-evolution-guide" not in str(raw["skills"])          # not the NexAU guide
    assert (cfg_path.parent / "skills" / "hdp-evolution-guide" / "SKILL.md").is_file()


def test_make_live_runner_wires_launch_without_nexau(tmp_path):
    workdir = tmp_path / "run"
    doc_dir = workdir / "doc.hdp"
    shutil.copytree(EXAMPLE, doc_dir)
    doc = load(doc_dir)
    seen: dict = {}

    class FakeAgent:
        def run(self, message, context):
            seen.update(message=message, working_directory=context["working_directory"],
                        iteration=context["iteration"])
            return "ok"

    def fake_factory(cfg_path):
        seen["cfg_path"] = Path(cfg_path)
        return FakeAgent()

    runner = propose.make_live_runner(agent_factory=fake_factory)
    out = runner(doc, "evolve please (iteration 1)", 1)

    assert out == "ok"
    assert seen["cfg_path"].name == "evolve_agent.yaml"               # retargeted config launched
    assert Path(seen["working_directory"]) == workdir
    assert os.environ["EVOLVE_WORK_DIR"] == str(workdir)              # file tools target the doc dir
    assert "evolve please" in seen["message"] and seen["iteration"] == 1
