# gpt-5.2 Baselines — Terminal-Bench 2 (k=2)

All runs: full TB2 dataset (89 tasks), gpt-5.2 (Azure), k=2 (pass@1 = task passes if ≥1 of 2 rollouts scores 1).

## Results

| Agent | Effort | pass@1 | Tasks | Date |
|-------|--------|--------|-------|------|
| nexau (AHE harness, AHE control loop) | high | **49.4%** (44/89) | 89 | 2026-06-28 |
| nexau (direct harbor, no AHE) | high | **42.0%** (37/88†) | 88† | 2026-06-28 |
| nexau (direct harbor, no AHE) | xhigh | **75.3%** (67/89) | 89 | 2026-06-28 |
| mini-swe-agent (E2BLocal, nexau-style) | xhigh | **48.3%** (43/89) | 89 | 2026-06-29 |
| openharness (evolved harness, full tool set) | xhigh | **28.1%** (25/89) | 89 | 2026-06-29 |
| openharness (baseline, stock oh) | xhigh | ~28%† (18/64 trials) | 89† | 2026-06-29 |

† One task was still running when the run was aborted. The openharness *baseline* run was killed at 64/178 trials (18 PASS → ~28% trial-level); never completed, shown for context only.

### openharness detail (evolved = 25/89 = 28.1%)

Run dir: `runs/openharness-evolved-xhigh-20260629_131951` (Lightning). Architecture: in-sandbox, same as nexau (`OpenHarnessHDP`, `BaseInstalledAgent` — `oh` installs & runs inside each E2B sandbox; LLM calls out from sandbox). n-concurrent 15.

- **Harness config (`agents/openharness_evolved/`)**: hand-authored system prompt directing use of the full openharness tool set (grep/glob/lsp/edit_file/notebook_edit/todo_write/web_search) + `allowed_tools` curating the productive coding set (excludes headless-hostile `ask_user_question`/`enter_plan_mode`/`agent`/`team_*`/`cron_*`/`mcp_*`/`send_message`); max_turns 250; no curl/wget/network denials. **No AHE-evolved artifact exists** — this is a hand-built "evolved-quality" harness, not evolve-loop output.
- **Trial-level**: 41 PASS / 134 FAIL / 3 EXC (178). Only 3 exceptions (2 VerifierTimeout, 1 sandbox) — not an infra issue.
- **Harness is not the lever**: evolved (28.1%) ≈ partial baseline (~28%). Enriching the prompt + exposing the full tool set barely moved the number. `allowed_tools` broke nothing (zero tool errors); the agent mostly used bash regardless (lsp/todo_write/web_search unused).
- **Root cause of the gap vs nexau (75.3%) / mini-swe (48.3%)**: openharness is a Claude-tuned agent driving **gpt-5.2** (OpenAI-compatible endpoint). It often *believes* it solved the task but fails verification (e.g. `nginx-request-logging`: declared success, scored 0). This is an agent×model fit problem, not a harness-config problem.
- **Measurement caveat**: the adapter saves only `oh`'s final answer (`openharness.txt`), not a per-step trajectory, so exact tool-call counts aren't recoverable.

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

### mini-swe-agent xhigh detail (43/89 = 48.3%)

Run dirs: `runs/mini-swe-e2blocal-xhigh-20260628_225102` (main) + `runs/mini-swe-e2blocal-xhigh-retry-20260629_104145` (retry of 4 exception tasks) (Lightning)

**Architecture (nexau-style):** `mini` CLI runs on the host (Lightning); only bash execution is proxied into the E2B sandbox via `E2BMinisweEnvironment`. LLM/reasoning calls stay on the host, so xhigh reasoning does not burn the 3600 s sandbox budget. Verified: sandboxes run only bash (no mini/LLM process inside). See `hdp/engine/adapters/{mini_swe_agent_harbor.py,e2b_miniswe_env.py}`.

| Metric | Value |
|--------|-------|
| **task-level pass@1** (≥1 of 2 rollouts) | **48.3%** (43/89) |
| trial-level (after retry) | 44.4% (79/178) |
| trial-level (main run only) | 43.3% (77/178) |

**Exit-status split (main run, 178 rollouts):** 85 `TimeExceeded` (mini self-exit at 840 s wall limit) vs 72 `Submitted` vs 17 `AgentTimeoutError` (harbor hard-cancel at 900 s) vs 5 true exceptions. **~49% of rollouts hit the time wall** — vs nexau xhigh ~22%. This timeout gap is the entire reason mini (48.3%) trails nexau (75.3%): mini at xhigh is slower per step and runs out harbor's 900 s cap roughly half the time. The harness is correct; it is less time-efficient.

**Orphan-leak fix (commit 24e0275):** harbor's 900 s cancel used to leave the `mini` subprocess running as an orphan. Fixed with `wall_time_limit_seconds=840` (clean self-exit) + `proc.kill()` on cancellation. Confirmed **0 orphans** across the full run (85 TimeExceeded + 22 cancels/exceptions).

**Exception taxonomy (5 true rollout exceptions, main run):**

| Exception type | Count | Retry outcome |
|----------------|-------|---------------|
| `httpx.LocalProtocolError: ConnectionState.CLOSED` | 3 | transient — re-ran clean (financial-document-processor ✓, modernize-scientific-stack ✓, gcode-to-text failed task legitimately) |
| `VerifierTimeoutError` (900 s) | 2 | **reproducible** — `filter-js-from-html` verifier hangs >900 s on every re-run (task/verifier-side, not the agent) |

Retry recovered 0 *new* pass@1 tasks (both httpx-affected tasks were already resolved by their other rollout), but cleared 3 transient exceptions and confirmed `filter-js-from-html` is a genuine verifier-side timeout.

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
