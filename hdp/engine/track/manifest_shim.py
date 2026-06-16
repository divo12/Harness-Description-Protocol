"""hdp.engine.track.manifest_shim — legacy AHE → canonical HDP change manifest.

Phase 0 consolidation: the HDP change manifest (hdp/schema/change-manifest.schema.json:
``change_id``/``operator``/``layer``/``repair_spec``/``prediction``) is canonical. The legacy
AHE manifest shape (``id``/``type``/``constraint_level``/``failure_pattern``, described in
agents/evolve_agent/evolve_prompt.md) is converted here, one-way. Nothing new should emit the
legacy form; this shim migrates anything that still does.

CLI:  python -m hdp.engine.track.manifest_shim <legacy.json>   # prints canonical JSON to stdout
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "schema" / "change-manifest.schema.json"
)

# legacy `type` -> canonical `operator`. `rollback` reverts edited content; the real
# revert path is track.rollback(change_id), so as an operator it is an `update`.
_OPERATOR = {"new": "add", "improvement": "update", "rollback": "update"}
_CANONICAL_OPERATORS = {"add", "update", "remove", "narrow", "gate"}

# legacy `constraint_level` -> canonical ETCLOVG `layer`.
_LAYER = {
    "middleware": "lifecycle",
    "tool_impl": "tooling",
    "tool_desc": "tooling",
    "skill": "context",
    "prompt": "context",
}


def _infer_component_id(change: dict, warnings: list[str]) -> str:
    """Best-effort component id from the legacy entry; legacy has no stable id field."""
    files = change.get("files") or []
    if files:
        # e.g. "tools/shell_tools/run_shell_command.py" -> "run-shell-command"
        stem = Path(files[0]).stem.replace("_", "-")
        if stem:
            return stem
    warnings.append(f"{change.get('id')}: could not infer component_id; using 'unknown'")
    return "unknown"


def _convert_change(change: dict, warnings: list[str]) -> dict:
    op_raw = change.get("type", "improvement")
    # Pass through values that are already canonical operators (partially-migrated input).
    operator = _OPERATOR.get(op_raw) or (op_raw if op_raw in _CANONICAL_OPERATORS else None)
    if operator is None:
        warnings.append(f"{change.get('id')}: unknown type '{op_raw}'; defaulting to 'update'")
        operator = "update"

    level = change.get("constraint_level", "")
    layer = _LAYER.get(level)
    if layer is None:
        warnings.append(
            f"{change.get('id')}: unknown constraint_level '{level}'; defaulting to 'context'"
        )
        layer = "context"

    predicted = change.get("predicted_fixes") or []
    return {
        "change_id": change.get("id", "chg-unknown"),
        "operator": operator,
        "component_id": _infer_component_id(change, warnings),
        "layer": layer,
        "summary": change.get("description", ""),
        "failure_evidence": change.get("failure_pattern", ""),
        "root_cause": change.get("why_this_component", ""),
        "repair_spec": {
            "editable_resources": list(change.get("files") or []),
            "validation_criteria": [f"{t} flips to pass" for t in predicted]
            or ["no regression on suite"],
        },
        "prediction": {
            "expected_fixes": list(predicted),
            "at_risk_regressions": list(change.get("risk_tasks") or []),
            "rationale": change.get("why_this_component", ""),
        },
        "verdict": "pending",
    }


def legacy_to_hdp(legacy: dict) -> dict:
    """Convert a legacy AHE change manifest dict into a canonical HDP one.

    Returns the canonical dict; raises ``jsonschema.ValidationError`` if the result does
    not conform to change-manifest.schema.json. Warnings (lossy inferences) are attached
    under the non-schema key ``_warnings`` only when present, and stripped before validation.
    """
    warnings: list[str] = []
    canonical = {
        "hdp": "0.1",
        "iteration": int(legacy.get("iteration", 0)),
        "changes": [_convert_change(c, warnings) for c in legacy.get("changes", [])],
    }

    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.Draft7Validator(schema).validate(canonical)

    if warnings:
        canonical["_warnings"] = warnings  # diagnostic only; not part of the schema
    return canonical


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print(__doc__)
        return 2
    legacy = json.loads(Path(argv[0]).read_text())
    canonical = legacy_to_hdp(legacy)
    for w in canonical.get("_warnings", []):
        print(f"  WARN  {w}", file=sys.stderr)
    print(json.dumps(canonical, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
