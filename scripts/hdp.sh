#!/usr/bin/env bash
# Thin wrapper around the HDP engine master runner (hdp/engine/run.py).
# Mirrors scripts/evolve.sh style. The engine is driven by configs/hdp/master.yaml.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
    cat <<'EOF'
Usage: ./scripts/hdp.sh <command> [options]

Commands:
  lift     NexAU harness -> HDP document          (Phase 2; Phase 0: stub)
  gen      HDP document  -> NexAU harness          (Phase 1; Phase 0: stub)
  evolve   one governed evolve iteration           (Phase 3/4; Phase 0: stub)
  bench    treatment-vs-control A/B table          (Phase 5; Phase 0: 2-arm smoke table)
  smoke    full pipeline on a tiny slice           (end-to-end wiring check)

Options:
  --config PATH   master config (default: configs/hdp/master.yaml)
  --smoke         force the tiny smoke slice for any command
  --dry-run       stub the harbor eval with the config's fake reward (seconds, no spend)

Examples:
  ./scripts/hdp.sh smoke --dry-run
  ./scripts/hdp.sh bench --config configs/hdp/master.yaml
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || $# -eq 0 ]]; then
    usage
    exit 0
fi

cd "$PROJECT_ROOT"
exec uv run python -m hdp.engine.run "$@"
