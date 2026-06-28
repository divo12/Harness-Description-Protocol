# gpt-5.2 Baselines — Terminal-Bench 2 (k=2)

All runs: full TB2 dataset (89 tasks), gpt-5.2 (Azure), k=2 (pass@1 = task passes if ≥1 of 2 rollouts scores 1).

## Results

| Agent | Effort | pass@1 | Tasks | Date |
|-------|--------|--------|-------|------|
| nexau (AHE harness, AHE control loop) | high | **49.4%** (44/89) | 89 | 2026-06-28 |
| nexau (direct harbor, no AHE) | high | **42.0%** (37/88†) | 88† | 2026-06-28 |
| nexau (direct harbor, no AHE) | xhigh | **75.3%** (67/89) | 89 | 2026-06-28 |
| mini-swe-agent | xhigh | — | — | pending |
| openharness | — | — | — | pending |

† One task was still running when the run was aborted.

### nexau xhigh detail (67/89 = 75.3%)

Run dir: `runs/nexau-xhigh-20260628_143415` (Lightning)

**Exception taxonomy (39 rollout-level exceptions across 178 rollouts):**

| Exception type | Count | Scored as |
|----------------|-------|-----------|
| AgentTimeoutError (900 / 750 / 1200 / 1800 / 2400 / 3600 s) | 36 | **0** — genuine failure |
| `e2b.TimeoutException: sandbox was not found` | 3 | 0 — transient; 2 tasks already passed via other rollout |
| `httpx.LocalProtocolError: ConnectionState.CLOSED` | 2 | 0 — transient; both tasks already passed |
| `VerifierTimeoutError` (900 s) | 1 | 0 — transient; task failed other rollout too |

**Net transient failures that affected pass@1:** 2 tasks (`train-fasttext`, `filter-js-from-html`).  
Re-running those could push to at most 69/89 = **77.5%**.

### Reference: k=1 baseline (2026-06-09)

| Metric | Value |
|--------|-------|
| nexau, high, k=1 — pass@1 (infra-aborts = fail) | 62.9% (56/89) |
| nexus, high, k=1 — pass@1 (excl. infra-aborts) | 65.9% (56/85) |

## Notes

- E2B hard-caps sandbox timeout at 3600 s (7200 s returns HTTP 400).
- Harbor per-task agent timeout: 900 s default; most exceptions are genuine agent timeouts.
- xhigh effort dramatically reduces agent failures vs high: 36 genuine timeouts vs many more at high.
- mini-swe xhigh config requires TB2-specific template (`/app` cwd, TASK_COMPLETE output, no parallel_tool_calls).
