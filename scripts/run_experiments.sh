#!/usr/bin/env bash
# Consolidated runner for the HDP A/B ablation suite (configs/hdp/ab-*.yaml).
#
# Runs the experiments IN ORDER, cheapest -> most expensive, driving the real evolve loop
# (hdp.engine.run evolve) for each one. The suite is 6 cost tiers, each with a matched
# {reconcile, atomic} guard-engine pair — the treatment-vs-control ablation.
#
# You rarely want all 12 on the first go. `--proportion P` runs only the first ceil(P * N)
# experiments in the ordered list, so a tiny P exercises just the cheapest tier(s) end to end
# — enough to confirm the LLM + workflow actually work — and you dial P up toward 1.0 later to
# run the full campaign.
#
# Cost note: each `evolve` runs the REAL evolve_agent proposer (spends LLM tokens) regardless of
# --dry-run. --dry-run only stubs the harbor/E2B eval, so it is the "proposer-only, no sandbox
# spend" middle ground. The default (no --dry-run) also runs the real harbor eval on E2B.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Ordered cheapest -> most expensive. reconcile before its atomic twin in each pair so a small
# proportion always yields a self-contained A/B slice for the tier(s) it selects.
EXPERIMENTS=(
    "configs/hdp/ab-multi.yaml"            # 3 tasks, k=1, 1 iter   (~3 rollouts/arm)
    "configs/hdp/ab-multi-atomic.yaml"
    "configs/hdp/ab-multi-k3.yaml"         # 3 tasks, k=3, 1 iter   (~9 rollouts/arm)
    "configs/hdp/ab-multi-k3-atomic.yaml"
    "configs/hdp/ab-multi5.yaml"           # 5 tasks, k=2, 1 iter   (~10 rollouts/arm)
    "configs/hdp/ab-multi5-atomic.yaml"
    "configs/hdp/ab-multi5-k3.yaml"        # 5 tasks, k=3, 1 iter   (~15 rollouts/arm)
    "configs/hdp/ab-multi5-k3-atomic.yaml"
    "configs/hdp/ab-overfull.yaml"         # 1 task,  k=1, 3 iters  (3 evolve iterations)
    "configs/hdp/ab-overfull-atomic.yaml"
    "configs/hdp/ab-tb2-k2.yaml"           # 89 tasks, k=2, 1 iter  (~178 rollouts/arm)
    "configs/hdp/ab-tb2-k2-atomic.yaml"
)

usage() {
    cat <<'EOF'
Usage: ./scripts/run_experiments.sh [options]

Run the HDP A/B ablation suite (configs/hdp/ab-*.yaml) in order, cheapest first, driving the
real evolve loop for each selected experiment.

Options:
  --proportion P   Fraction (0..1) of the ordered suite to run. Runs the first ceil(P*N)
                   experiments. Default: 0.1 (the cheapest reconcile+atomic pair). Env override:
                   AHE_PROPORTION. Increase toward 1.0 to run the whole suite.
  --dry-run        Stub the harbor/E2B eval (config's fake_reward). The evolve_agent PROPOSER
                   still runs for real and spends LLM tokens — this is the no-sandbox path.
  --stop-on-error  Abort the run at the first experiment that fails (default: keep going).
  --list           Print the ordered plan and which experiments the current proportion selects,
                   then exit without running anything.
  -h, --help       Show this help.

Examples:
  ./scripts/run_experiments.sh --list                 # see the plan, run nothing
  ./scripts/run_experiments.sh                         # cheapest tier, real eval (P=0.1)
  ./scripts/run_experiments.sh --proportion 0.1 --dry-run   # cheapest tier, proposer only
  ./scripts/run_experiments.sh --proportion 1.0        # full suite (spend-heavy)

Logs land in runs/_consolidated/<UTC-timestamp>/<experiment>.log with a summary at the end.
EOF
}

PROPORTION="${AHE_PROPORTION:-0.1}"
DRY_RUN=""
STOP_ON_ERROR=false
LIST_ONLY=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --proportion) PROPORTION="$2"; shift 2 ;;
        --dry-run)    DRY_RUN="--dry-run"; shift ;;
        --stop-on-error) STOP_ON_ERROR=true; shift ;;
        --list)       LIST_ONLY=true; shift ;;
        -h|--help)    usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

# --- Resolve how many experiments to run: N_run = ceil(PROPORTION * total), clamped. ---
total=${#EXPERIMENTS[@]}
n_run=$(awk -v p="$PROPORTION" -v t="$total" 'BEGIN{
    if (p+0 != p || p < 0 || p > 1) { print "ERR"; exit }
    n = p * t; ni = int(n); if (n > ni) ni++;      # ceil
    if (ni < 1 && p > 0) ni = 1;                    # any positive proportion runs >= 1
    if (ni > t) ni = t;
    print ni
}')
if [[ "$n_run" == "ERR" ]]; then
    echo "Error: --proportion must be a number in [0, 1] (got: $PROPORTION)" >&2
    exit 1
fi

echo "HDP A/B suite: $total experiments total; proportion=$PROPORTION -> running $n_run."
if $LIST_ONLY || [[ "$n_run" -gt 0 ]]; then
    echo "Ordered plan (cheapest -> most expensive):"
    for i in "${!EXPERIMENTS[@]}"; do
        mark="   "
        [[ $i -lt $n_run ]] && mark="=> "
        printf "  %s%2d. %s\n" "$mark" $((i+1)) "${EXPERIMENTS[$i]}"
    done
fi

if $LIST_ONLY; then exit 0; fi
if [[ "$n_run" -eq 0 ]]; then
    echo "Nothing to run (proportion=0)." >&2
    exit 0
fi

# --- Pick a Python runner: prefer `uv run python` (the target env), fall back to the repo venv,
#     then a bare `python`. Keeps the script working both on uv-based hosts and this conda env. ---
export PATH="$HOME/.local/bin:$PATH"
if command -v uv >/dev/null 2>&1; then
    RUNNER=(uv run python)   # uv puts the env's console scripts (harbor, ...) on PATH itself
elif [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    RUNNER=("$PROJECT_ROOT/.venv/bin/python")
    export PATH="$PROJECT_ROOT/.venv/bin:$PATH"   # so the eval's `harbor` subprocess resolves
elif command -v python >/dev/null 2>&1; then
    RUNNER=(python)
    export PATH="$(dirname "$(command -v python)"):$PATH"
else
    echo "Error: no Python runner found (looked for uv, .venv/bin/python, python)." >&2
    exit 1
fi

# --- Preflight (best-effort; warnings only). Real harbor eval needs LLM + E2B credentials;
#     accept them from the live environment OR from .env. ---
if [[ -z "$DRY_RUN" ]]; then
    for key in LLM_API_KEY LLM_BASE_URL E2B_API_KEY; do
        in_env="$(printenv "$key" 2>/dev/null || true)"
        in_dotenv=false
        [[ -f "$PROJECT_ROOT/.env" ]] && grep -qE "^${key}=.+" "$PROJECT_ROOT/.env" && in_dotenv=true
        if [[ -z "$in_env" ]] && ! $in_dotenv; then
            echo "  [warn] ${key} not found in environment or .env; the real harbor eval will fail without it (use --dry-run for proposer-only)."
        fi
    done
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="$PROJECT_ROOT/runs/_consolidated/$TS"
mkdir -p "$LOG_DIR"
echo "Logs -> $LOG_DIR"
[[ -n "$DRY_RUN" ]] && echo "Mode: --dry-run (real proposer, stubbed eval)" || echo "Mode: real eval (LLM + E2B spend)"
echo ""

cd "$PROJECT_ROOT"
declare -a STATUSES
overall_rc=0

for i in $(seq 0 $((n_run-1))); do
    cfg="${EXPERIMENTS[$i]}"
    name="$(basename "$cfg" .yaml)"
    log="$LOG_DIR/${name}.log"
    printf '=== [%d/%d] %s ===\n' $((i+1)) "$n_run" "$name"

    set +e
    "${RUNNER[@]}" -m hdp.engine.run evolve --config "$cfg" $DRY_RUN 2>&1 | tee "$log"
    rc=${PIPESTATUS[0]}
    set -e

    if [[ $rc -eq 0 ]]; then
        STATUSES+=("OK    $name")
        echo "--- $name: OK ---"
    else
        STATUSES+=("FAIL  $name (rc=$rc)")
        overall_rc=1
        echo "--- $name: FAILED (rc=$rc) — see $log ---"
        if $STOP_ON_ERROR; then
            echo "Stopping (--stop-on-error)." >&2
            break
        fi
    fi
    echo ""
done

echo "============================================"
echo "  Summary ($n_run/$total experiments, proportion=$PROPORTION)"
echo "============================================"
for s in "${STATUSES[@]}"; do echo "  $s"; done
echo "  Logs: $LOG_DIR"
echo "============================================"
exit $overall_rc
