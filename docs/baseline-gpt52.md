# gpt-5.2 Baseline — Terminal-Bench 2 (AHE iteration-0)

Reproduction of the AHE paper's baseline using **gpt-5.2** (Azure OpenAI) on the
bare seed harness (`agents/code_agent_simple`), single rollout (k=1), no evolution.

Config: [`configs/experiments/exp-baseline-89.yaml`](../configs/experiments/exp-baseline-89.yaml)
Date: 2026-06-09

## Result

| Metric | Value |
|---|---|
| Pass / Fail (genuine) | **56 / 29** (85 scored) |
| pass@1 (floor, infra-aborts = fail; leaderboard convention) | **62.9%** (56/89) |
| pass@1 (excluding infra-aborts) | **65.9%** |
| Paper baseline (gpt-5.4, k=2) | 69.7% |

The ~4–7 pt gap vs the paper is expected: **gpt-5.2 ≠ gpt-5.4**, and this is **k=1
single-shot** vs the paper's **k=2** average. The harness/infra reproduced faithfully.

## Two infrastructure findings

1. **Sandbox-timeout collision.** `E2B_SANDBOX_TIMEOUT=3600s` equals the hardest tasks'
   60-min agent budget, so the slowest tasks get their sandbox torn down mid-work
   (one was killed *during verification*). 3 tasks were lost this way — **infra aborts,
   not model failures**: `regex-chess`, `train-fasttext`, `filter-js-from-html`.
   **Fix:** raise `E2B_SANDBOX_TIMEOUT` to ~4500–5400s (agent budget + verifier headroom).
2. **Intermittent empty LLM responses** from Azure gpt-5.2 on the hardest tasks
   (`content_len=0, tool_calls=0`), triggering nexau retry/backoff and wasting wall-clock.

## Genuine failures (29) — evolution-loop targets

```
adaptive-rejection-sampler, crack-7z-hash, db-wal-recovery, dna-assembly, dna-insert,
extract-elf, extract-moves-from-video, feal-differential-cryptanalysis,
financial-document-processor, gcode-to-text, gpt2-codegolf, install-windows-3.11,
large-scale-text-editing, make-doom-for-mips, mcmc-sampling-stan,
model-extraction-relu-logits, mteb-retrieve, overfull-hbox, path-tracing, polyglot-c-py,
polyglot-rust-c, raman-fitting, sam-cell-seg, sanitize-git-repo, sparql-university,
torch-pipeline-parallelism, torch-tensor-parallelism, video-processing, write-compressor
```

Plus 3 infra-aborts (above) and 1 cut while running (`fix-ocaml-gc`).

## gpt-5.2 adaptation notes (already in committed configs)

- Model `gpt-5.4 → gpt-5.2` in `base.yaml` + `exp-simple-code-gpt54.yaml`.
- Azure reached via the **OpenAI-compatible v1 surface** (`.../openai/v1/`, key as Bearer);
  no nexau patch needed. `api_type: openai_responses`.
- gpt-5.x reasoning models **reject sampling params** (`temperature`, `top_p`, penalties).
  All agents (code, evolve, ADB) carry `additional_drop_params` to strip them; nexau
  auto-converts `max_tokens → max_output_tokens` for the responses API.
- ADB uses `${env.LLM_API_TYPE}` → set `LLM_API_TYPE=openai_responses` in `.env`.

## Reproduce

```bash
uv sync
cp .env.example .env   # set LLM_* (Azure v1), LLM_API_TYPE=openai_responses, E2B_API_KEY, SERPER_API_KEY
harbor datasets download terminal-bench@2.0 --output-dir ./dataset/terminal-bench-2
# flatten harbor's <hash>/<name> layout → <name>/ for build_templates:
mkdir -p dataset/tb2-flat
find dataset/terminal-bench-2 -name task.toml -print0 | while IFS= read -r -d '' f; do d=$(dirname "$f"); ln -sfn "$(cd "$d" && pwd)" "dataset/tb2-flat/$(basename "$d")"; done
set -a; source .env; set +a
uv run python scripts/build_templates.py --dataset-dir ./dataset/tb2-flat --missing-only -j 16
uv run python evolve.py --config configs/experiments/exp-baseline-89.yaml   # kill after [stats]
```
