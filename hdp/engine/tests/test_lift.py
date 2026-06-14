"""Phase 2 acceptance (lift): NexAU seed -> HDP doc is conformant, and the round-trip
lift -> generate reproduces the seed byte-for-byte."""
import importlib.util
import sys
from pathlib import Path

import yaml

from hdp.engine.gen import generate
from hdp.engine.lift import lift

REPO = Path(__file__).resolve().parents[3]
SEED = REPO / "agents" / "code_agent_simple"

BYTE_IDENTICAL = [
    "systemprompt.md",
    "LongTermMEMORY.md",
    "ShortTermMEMORY.md",
    "tool_descriptions/run_shell_command.tool.yaml",
    "tools/shell_tools/run_shell_command.py",
    "tools/__init__.py",
    "tools/shell_tools/__init__.py",
    "start.py",
    "README.md",
    ".gitignore",
    "nexau.json",
]


def test_lift_produces_conformant_doc(tmp_path):
    doc = lift(SEED, tmp_path / "lifted.hdp")

    ids = {c.id for _l, c in doc.components()}
    assert {"system-rules-core", "long-term-memory", "short-term-memory",
            "run-shell-command", "main-loop", "in-memory-tracer"} == ids

    tool = doc.component("run-shell-command")
    assert tool.blast_radius.value == "system"               # shell tool inferred
    assert tool.implementation.binding == "tools.shell_tools:run_shell_command"

    gov = doc.model.governance
    assert gov.blast_radius.value == "system"
    assert gov.audit.log_all_tool_calls is True              # required at system ceiling
    assert "system-rules-core" in gov.evolution.protected

    # the HDP reference validator must report zero errors
    errors, _warnings = _hdp_validate()(doc.path)
    assert errors == [], errors


def test_round_trip_lift_then_generate_reproduces_seed(tmp_path):
    doc = lift(SEED, tmp_path / "lifted.hdp")
    harness = generate(doc, tmp_path / "regen")

    for rel in BYTE_IDENTICAL:
        assert (harness / rel).read_bytes() == (SEED / rel).read_bytes(), f"{rel} differs"
    assert yaml.safe_load((harness / "code_agent.yaml").read_text()) == \
        yaml.safe_load((SEED / "code_agent.yaml").read_text())


def _hdp_validate():
    path = REPO / "hdp" / "validator" / "hdp_validate.py"
    spec = importlib.util.spec_from_file_location("hdp_validate_mod", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.validate
