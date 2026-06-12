#!/usr/bin/env python3
"""HDP v0.1 reference validator.

Usage:  python hdp_validate.py <path-to-.hdp-directory>

Checks (SPEC.md §10 document conformance):
  1. hdp.yaml validates against hdp.schema.json
  2. component ids unique document-wide
  3. embedded `file:` paths and `ref.path` paths exist
  4. blast-radius ordering: no component exceeds governance.blast_radius
  5. governance.evolution entries reference real layers or component ids
  6. `protected` ids exist; audit rule for system/external blast radius
  7. evaluation block present when evolution block is present
  8. no literal secrets in embedded files (must use ${env.VAR})
  9. change manifests validate against change-manifest.schema.json;
     non-`add` entries must target existing component ids

Exit code 0 = conformant, 1 = errors (warnings alone do not fail).
"""

import json
import re
import sys
from pathlib import Path

import jsonschema
import yaml

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"
BLAST_ORDER = ["read_only", "local", "session", "system", "external"]
LAYERS = ["execution", "tooling", "context", "lifecycle",
          "observability", "verification", "governance"]
SECRET_PATTERNS = [
    re.compile(r"(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}",
               re.IGNORECASE),
    re.compile(r"\b(?:sk|e2b|ghp|xoxb)_[A-Za-z0-9]{10,}"),
]


def load_yaml(path: Path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def iter_components(manifest):
    for layer, comps in (manifest.get("layers") or {}).items():
        for comp in comps or []:
            yield layer, comp


def validate(doc_dir: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    manifest_path = doc_dir / "hdp.yaml"
    if not manifest_path.exists():
        return [f"missing manifest: {manifest_path}"], warnings
    manifest = load_yaml(manifest_path)

    # 1. schema validation
    schema = json.loads((SCHEMA_DIR / "hdp.schema.json").read_text())
    for err in jsonschema.Draft7Validator(schema).iter_errors(manifest):
        errors.append(f"schema: {'/'.join(str(p) for p in err.absolute_path)}: {err.message}")
    if errors:
        return errors, warnings  # structural checks assume a schema-valid manifest

    # 2. unique ids
    ids: dict[str, str] = {}
    for layer, comp in iter_components(manifest):
        cid = comp["id"]
        if cid in ids:
            errors.append(f"duplicate component id '{cid}' (in {ids[cid]} and {layer})")
        ids[cid] = layer

    # 3. files and path refs exist
    for layer, comp in iter_components(manifest):
        if "file" in comp and not (doc_dir / comp["file"]).exists():
            errors.append(f"{comp['id']}: file not found: {comp['file']}")
        for key in ("ref", "implementation", "impl", "backend", "definition"):
            ref = comp.get(key)
            if isinstance(ref, dict) and ref.get("kind") == "path":
                if not (doc_dir / ref.get("path", "")).exists():
                    errors.append(f"{comp['id']}: {key}.path not found: {ref.get('path')}")

    # 4. blast-radius ordering
    gov = manifest.get("governance") or {}
    ceiling = gov.get("blast_radius")
    if ceiling:
        for layer, comp in iter_components(manifest):
            br = comp.get("blast_radius")
            if br and BLAST_ORDER.index(br) > BLAST_ORDER.index(ceiling):
                errors.append(f"{comp['id']}: blast_radius '{br}' exceeds ceiling '{ceiling}'")
    else:
        if any(c.get("blast_radius") for _, c in iter_components(manifest)):
            warnings.append("components declare blast_radius but governance.blast_radius unset")

    # 5. evolution editability entries reference real layers/ids
    evo_gov = gov.get("evolution") or {}
    for field in ("editable", "read_only", "protected"):
        for entry in evo_gov.get(field) or []:
            if entry not in LAYERS and entry not in ids:
                errors.append(f"governance.evolution.{field}: unknown layer/id '{entry}'")

    # 6. audit rule
    if ceiling in ("system", "external"):
        if not (gov.get("audit") or {}).get("log_all_tool_calls"):
            errors.append("blast_radius system/external requires audit.log_all_tool_calls: true")

    # 7. evaluation required with evolution
    if manifest.get("evolution") and not manifest.get("evaluation"):
        errors.append("evolution block present but evaluation block missing (SPEC §6)")

    # 8. secret scan over embedded files
    for layer, comp in iter_components(manifest):
        if "file" in comp and (doc_dir / comp["file"]).exists():
            text = (doc_dir / comp["file"]).read_text(encoding="utf-8", errors="ignore")
            for pat in SECRET_PATTERNS:
                if pat.search(text):
                    errors.append(f"{comp['id']}: possible literal secret in {comp['file']} "
                                  f"(use ${{env.VAR}})")
                    break

    # 9. change manifests
    cm_schema = json.loads((SCHEMA_DIR / "change-manifest.schema.json").read_text())
    manifest_dir = doc_dir / (manifest.get("evolution") or {}).get("manifest_dir",
                                                                   "evolution/manifests")
    if manifest_dir.exists():
        for cm_path in sorted(manifest_dir.glob("*.json")):
            cm = json.loads(cm_path.read_text())
            for err in jsonschema.Draft7Validator(cm_schema).iter_errors(cm):
                errors.append(f"{cm_path.name}: {err.message}")
                continue
            for chg in cm.get("changes", []):
                if chg.get("operator") != "add" and chg.get("component_id") not in ids:
                    errors.append(f"{cm_path.name}/{chg.get('change_id')}: "
                                  f"unknown component_id '{chg.get('component_id')}'")
                if chg.get("operator") == "remove" and \
                        chg.get("component_id") in (evo_gov.get("protected") or []):
                    errors.append(f"{cm_path.name}/{chg.get('change_id')}: "
                                  f"remove targets protected component (safety rule 4)")

    return errors, warnings


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    doc_dir = Path(sys.argv[1]).resolve()
    errors, warnings = validate(doc_dir)
    for w in warnings:
        print(f"  WARN  {w}")
    for e in errors:
        print(f"  ERROR {e}")
    if errors:
        print(f"\n{doc_dir.name}: NOT conformant ({len(errors)} error(s))")
        sys.exit(1)
    print(f"{doc_dir.name}: conformant with HDP 0.1 "
          f"({len(warnings)} warning(s))")


if __name__ == "__main__":
    main()
