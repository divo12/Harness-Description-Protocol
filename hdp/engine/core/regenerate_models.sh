#!/usr/bin/env bash
# Regenerate the typed Pydantic models from the JSON Schemas (the source of truth).
# NEVER hand-edit models.py / manifest_models.py — change the schema and rerun this.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCHEMA_DIR="$(cd "$SCRIPT_DIR/../../schema" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$ROOT"

gen() {  # <schema-file> <output-file>
    uv run --no-sync datamodel-codegen \
        --input "$1" --input-file-type jsonschema \
        --output-model-type pydantic_v2.BaseModel \
        --field-constraints --use-schema-description --use-title-as-name \
        --output "$2"
}

gen "$SCHEMA_DIR/hdp.schema.json"            "$SCRIPT_DIR/models.py"
gen "$SCHEMA_DIR/change-manifest.schema.json" "$SCRIPT_DIR/manifest_models.py"
echo "Regenerated models.py and manifest_models.py from $SCHEMA_DIR"
