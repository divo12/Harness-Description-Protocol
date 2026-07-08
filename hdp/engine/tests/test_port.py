"""End-to-end port() behavior — all $0 (no LLM / no E2B).

Between the two supported backends the real seeds cannot cross-port to completion (each backend's
tool binding is unresolvable by the other), so the honest outcomes are: a blocking report that
refuses, or a clean completion once the blocking binding is removed.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from hdp.engine.adapters.nexau import NexAUAdapter
from hdp.engine.core.loader import load, save
from hdp.engine.lift import lift
from hdp.engine.port import PortCoverageError, audit, port

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "hdp" / "examples" / "code-agent-simple.hdp"
NEXAU_SEED = REPO / "agents" / "code_agent_simple"


def _seed_without_tool_binding(tmp_path: Path) -> Path:
    """Copy the NexAU seed and drop its tool `binding` so the destination adapter has nothing
    unresolvable to reject — isolating the (non-blocking) prompt silent_drop."""
    dst = tmp_path / "seed"
    shutil.copytree(NEXAU_SEED, dst)
    y = YAML()
    y.preserve_quotes = True
    manifest = dst / "code_agent.yaml"
    data = y.load(manifest.read_text())
    for tool in data.get("tools") or []:
        tool.pop("binding", None)
    with open(manifest, "w", encoding="utf-8") as f:
        y.dump(data, f)
    return dst


def test_port_nexau_to_mini_swe_reports_binding_blocking_and_refuses(tmp_path):
    out = tmp_path / "out"
    with pytest.raises(PortCoverageError) as ei:
        port(NEXAU_SEED, out, source_target="nexau", dest_target="mini-swe-agent")
    issues = ei.value.report.issues
    assert any(i.severity == "silent_drop" and i.component_id == "system-rules-core" for i in issues)
    assert any(i.severity == "blocking" and i.type == "tool" for i in issues)
    # refused before generating: no harness written.
    assert not (out / "harness").exists()


def test_port_completes_with_silent_drop_when_tool_bindingless(tmp_path):
    src = _seed_without_tool_binding(tmp_path)
    result = port(src, tmp_path / "out", source_target="nexau", dest_target="mini-swe-agent")
    assert result.generated
    assert [(i.severity, i.component_id) for i in result.coverage.issues] == \
           [("silent_drop", "system-rules-core")]
    assert (result.harness_dir / "mini_swe_agent.yaml").is_file()
    assert (result.harness_dir / "hdp_port_coverage.json").is_file()


def test_port_coverage_sidecar_matches_report(tmp_path):
    src = _seed_without_tool_binding(tmp_path)
    result = port(src, tmp_path / "out", source_target="nexau", dest_target="mini-swe-agent")
    on_disk = json.loads((result.harness_dir / "hdp_port_coverage.json").read_text())
    assert on_disk == asdict(result.coverage)


def test_reject_unsupported_unchanged(tmp_path):
    # (a) a policy component still raises the identical NotImplementedError from _reject_unsupported.
    dst = tmp_path / "policy.hdp"
    shutil.copytree(EXAMPLE, dst)
    doc = load(dst)
    doc.raw["layers"]["verification"].append({"id": "test-policy", "type": "policy"})
    save(doc)
    doc = load(dst)
    with pytest.raises(NotImplementedError) as ei:
        NexAUAdapter()._reject_unsupported(doc)
    assert str(ei.value) == (
        "policy component 'test-policy': in-harness policy enforcement is not wired in v1"
    )

    # (b) an unresolvable binding still raises ValueError from generate() (NOT NotImplementedError).
    bad = load(EXAMPLE)
    bad.component("run-shell").implementation.binding = "tools.nope:missing"
    with pytest.raises(ValueError, match="cannot resolve ref binding"):
        NexAUAdapter().generate(bad, tmp_path / "boom")


def test_port_rejects_openharness_target(tmp_path):
    with pytest.raises(ValueError, match="unsupported dest_target 'openharness'"):
        port(NEXAU_SEED, tmp_path / "o", source_target="nexau", dest_target="openharness")
    with pytest.raises(ValueError, match="unsupported source_target 'openharness'"):
        port(NEXAU_SEED, tmp_path / "o2", source_target="openharness", dest_target="nexau")


def test_audit_never_touches_disk(tmp_path):
    d = lift(NEXAU_SEED, tmp_path / "l.hdp", target="nexau")
    before = set(tmp_path.rglob("*"))
    audit(d, "mini-swe-agent", source_target="nexau")
    assert set(tmp_path.rglob("*")) == before
