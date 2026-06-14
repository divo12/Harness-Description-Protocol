"""Phase 0 acceptance: legacy AHE change manifest -> canonical HDP, schema-valid."""
import json
from pathlib import Path

import jsonschema

from hdp.engine.track.manifest_shim import legacy_to_hdp

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "schema" / "change-manifest.schema.json").read_text()
)

LEGACY = {
    "iteration": 3,
    "changes": [
        {
            "id": "chg-1",
            "type": "improvement",
            "description": "Narrow run_shell so cleanup can't delete verified output.",
            "files": ["tools/shell_tools/run_shell_command.py"],
            "failure_pattern": "agent deletes verified output during cleanup",
            "predicted_fixes": ["task-a", "task-b"],
            "risk_tasks": ["task-c"],
            "constraint_level": "tool_impl",
            "why_this_component": "the deletion happens inside the tool implementation",
        },
        {
            "id": "chg-2",
            "type": "new",
            "description": "Add a skill for git hygiene.",
            "files": ["skills/git-hygiene/SKILL.md"],
            "failure_pattern": "dirty working tree breaks the verifier",
            "predicted_fixes": ["task-d"],
            "risk_tasks": [],
            "constraint_level": "skill",
            "why_this_component": "guidance-level fix",
        },
    ],
}


def test_legacy_to_hdp_is_schema_valid():
    canonical = legacy_to_hdp(LEGACY)
    # Clean mapping -> no lossy-inference warnings.
    assert "_warnings" not in canonical
    jsonschema.Draft7Validator(SCHEMA).validate(canonical)


def test_field_mapping():
    canonical = legacy_to_hdp(LEGACY)
    assert canonical["hdp"] == "0.1"
    assert canonical["iteration"] == 3

    c1 = canonical["changes"][0]
    assert c1["change_id"] == "chg-1"
    assert c1["operator"] == "update"            # improvement -> update
    assert c1["layer"] == "tooling"              # tool_impl -> tooling
    assert c1["failure_evidence"].startswith("agent deletes")
    assert c1["root_cause"].startswith("the deletion")
    assert c1["repair_spec"]["editable_resources"] == [
        "tools/shell_tools/run_shell_command.py"
    ]
    assert c1["prediction"]["expected_fixes"] == ["task-a", "task-b"]
    assert c1["prediction"]["at_risk_regressions"] == ["task-c"]
    assert c1["repair_spec"]["validation_criteria"] == [
        "task-a flips to pass",
        "task-b flips to pass",
    ]
    assert c1["verdict"] == "pending"

    c2 = canonical["changes"][1]
    assert c2["operator"] == "add"               # new -> add
    assert c2["layer"] == "context"              # skill -> context


def test_unknown_constraint_level_warns_but_validates():
    legacy = {
        "iteration": 0,
        "changes": [
            {
                "id": "chg-x",
                "type": "improvement",
                "files": [],
                "constraint_level": "mystery",
                "failure_pattern": "f",
                "predicted_fixes": [],
                "risk_tasks": [],
            }
        ],
    }
    canonical = legacy_to_hdp(legacy)
    assert canonical["changes"][0]["layer"] == "context"   # safe default
    assert canonical["changes"][0]["component_id"] == "unknown"
    assert canonical["_warnings"]
