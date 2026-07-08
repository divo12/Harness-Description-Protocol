"""Adapter/audit-level coverage: hdp.engine.port.audit + FrameworkAdapter.capability_issues.

All $0 (no LLM / no E2B): lift the real seeds under agents/ and doctor the shipped example doc.
These lock in the §3 findings the port infra reports and the behavior-preserving refactor of
``_reject_unsupported`` (nexau ↔ mini-swe-agent only; openharness is out of scope).
"""
from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from hdp.engine.adapters.mini_swe_agent import MiniSweAgentAdapter
from hdp.engine.adapters.nexau import NexAUAdapter
from hdp.engine.core.loader import load, save
from hdp.engine.lift import lift
from hdp.engine.port import audit

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "hdp" / "examples" / "code-agent-simple.hdp"
NEXAU_SEED = REPO / "agents" / "code_agent_simple"
MINI_SEED = REPO / "agents" / "mini_swe_agent"


def _doctored(tmp_path: Path, mutate: Callable[[object], None]):
    """Copy the example doc, apply *mutate* to its ruamel raw, save+reload (the test_guard pattern)."""
    dst = tmp_path / "doctored.hdp"
    if not dst.exists():
        shutil.copytree(EXAMPLE, dst)
    doc = load(dst)
    mutate(doc.raw)
    save(doc)
    return load(dst)


def test_no_issues_for_self_port(tmp_path):
    doc = lift(NEXAU_SEED, tmp_path / "l.hdp", target="nexau")
    report = audit(doc, "nexau", source_target="nexau")
    assert report.ok
    assert report.dest_target == "nexau" and report.source_target == "nexau"
    assert report.total_components == sum(1 for _ in doc.components())


def test_policy_still_blocking_with_identical_reason(tmp_path):
    doc = _doctored(
        tmp_path,
        lambda raw: raw["layers"]["verification"].append({"id": "test-policy", "type": "policy"}),
    )
    issues = [i for i in NexAUAdapter().capability_issues(doc)
              if i.severity == "blocking" and i.component_id == "test-policy"]
    assert len(issues) == 1
    # byte-identical to what the pre-refactor _reject_unsupported raised.
    assert issues[0].reason == (
        "policy component 'test-policy': in-harness policy enforcement is not wired in v1"
    )


def test_nonexternal_verifier_still_blocking_with_identical_reason(tmp_path):
    def flip(raw):
        for c in raw["layers"]["verification"]:
            if c["id"] == "tb2-verifier":
                c["trigger"] = "on_demand"
    doc = _doctored(tmp_path, flip)
    issues = [i for i in NexAUAdapter().capability_issues(doc)
              if i.severity == "blocking" and i.component_id == "tb2-verifier"]
    assert len(issues) == 1
    assert issues[0].reason == (
        "verifier 'tb2-verifier' trigger=on_demand: only external "
        "verifiers (handled by the eval harness) are supported in v1"
    )


def test_mini_swe_silent_drop_for_unrecognized_system_rules_id(tmp_path):
    doc = lift(NEXAU_SEED, tmp_path / "l.hdp", target="nexau")  # id = system-rules-core
    drops = [i for i in MiniSweAgentAdapter().capability_issues(doc) if i.severity == "silent_drop"]
    assert len(drops) == 1
    assert drops[0].component_id == "system-rules-core"
    assert drops[0].type == "system_rules"


def test_nexau_collision_for_multiple_system_rules(tmp_path):
    # mini-swe seed lifts to 4 system_rules (the prompt templates); NexAU maps all to systemprompt.md.
    doc = lift(MINI_SEED, tmp_path / "l.hdp", target="mini-swe-agent")
    collisions = [i for i in NexAUAdapter().capability_issues(doc) if i.severity == "collision"]
    assert len(collisions) == 3  # 4 components share one dest -> 3 after the first
    assert all("systemprompt.md" in c.reason for c in collisions)
    assert all(c.type == "system_rules" for c in collisions)


def test_both_adapters_flag_the_others_binding_as_blocking(tmp_path):
    nexau_doc = lift(NEXAU_SEED, tmp_path / "n.hdp", target="nexau")
    mini_doc = lift(MINI_SEED, tmp_path / "m.hdp", target="mini-swe-agent")

    mini_view = [i for i in MiniSweAgentAdapter().capability_issues(nexau_doc)
                 if i.severity == "blocking" and i.type == "tool"]
    assert mini_view and "cannot resolve ref binding" in mini_view[0].reason

    nexau_view = [i for i in NexAUAdapter().capability_issues(mini_doc)
                  if i.severity == "blocking" and i.type == "tool"]
    assert nexau_view and "cannot resolve ref binding" in nexau_view[0].reason
