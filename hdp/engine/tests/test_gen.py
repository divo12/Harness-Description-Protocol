"""Phase 1 acceptance (gen): generate(code-agent-simple.hdp) faithfully reproduces the
NexAU seed harness (the ~62.9% gpt-5.2 baseline) and passes the AHE agent validator."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

from hdp.engine.core.loader import load
from hdp.engine.gen import generate

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "hdp" / "examples" / "code-agent-simple.hdp"
SEED = REPO / "agents" / "code_agent_simple"

# Embedded content + resolved assets must match the seed byte-for-byte (SPEC §10).
BYTE_IDENTICAL = [
    "systemprompt.md",
    "LongTermMEMORY.md",
    "ShortTermMEMORY.md",
    "tool_descriptions/run_shell_command.tool.yaml",
    "tools/__init__.py",
    "tools/shell_tools/__init__.py",
    "tools/shell_tools/run_shell_command.py",
    "start.py",
    "README.md",
    ".gitignore",
    "nexau.json",
]


@pytest.fixture(scope="module")
def harness(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("harness")
    return generate(load(EXAMPLE), out)


def test_embedded_and_assets_byte_identical(harness):
    for rel in BYTE_IDENTICAL:
        assert (harness / rel).read_bytes() == (SEED / rel).read_bytes(), f"{rel} differs"


def test_code_agent_yaml_semantically_matches_seed(harness):
    gen = yaml.safe_load((harness / "code_agent.yaml").read_text())
    seed = yaml.safe_load((SEED / "code_agent.yaml").read_text())
    assert gen == seed


def test_generated_harness_passes_ahe_validator(harness):
    validate = _load_validator()
    report = validate(str(harness / "code_agent.yaml"), check_python=True)
    errors = [i for i in report.issues if i.severity == "ERROR"]
    assert report.is_valid, f"validator errors: {errors}"


def test_attribution_sidecar(harness):
    sidecar = json.loads((harness / "hdp_attribution.json").read_text())
    assert sidecar["doc"] == {"id": "code-agent-simple", "version": "1.0.0"}
    # tool spans (by name) attribute to the tool component id (SPEC §8)
    assert sidecar["by_tool_name"]["run_shell_command"] == "run-shell"
    assert sidecar["by_file"]["systemprompt.md"] == "system-rules-core"


def test_unresolved_binding_fails_loud(tmp_path):
    from hdp.engine.adapters.nexau import NexAUAdapter

    doc = load(EXAMPLE)
    doc.component("run-shell").implementation.binding = "tools.nope:missing"
    with pytest.raises(ValueError, match="cannot resolve ref binding"):
        NexAUAdapter().generate(doc, tmp_path / "boom")


def _load_validator():
    path = (REPO / "agents" / "evolve_agent" / "skills" / "nexau-evolution-guide"
            / "scripts" / "validate_agent.py")
    spec = importlib.util.spec_from_file_location("ahe_validate_agent", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses in the module resolve types via sys.modules
    spec.loader.exec_module(mod)
    return mod.validate_agent_yaml
