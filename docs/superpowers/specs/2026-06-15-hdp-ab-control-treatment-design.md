# HDP A/B — Control vs Treatment (experiment design, approved 2026-06-15)

The HDP engine (`hdp/engine/`) exists to run one controlled experiment: **does routing
AHE's evolution through the HDP abstraction cost anything on pass@1?** The thesis is parity —
treatment matches control while adding governance, attribution, and portability. This document
fixes the two arms, what is held identical, the validity gates, and how the action-space
confound is handled. It is the contract the `guard`/`track`/`attest`/`bench` phases implement.

## The two arms

| | **Control** (`hdp.enabled=false`) | **Treatment** (`hdp.enabled=true`) |
|---|---|---|
| Start | `agents/code_agent_simple` workspace | `lift(seed)` → HDP document (once) |
| Per iteration | eval(workspace) → ADB → evolve_agent edits **NexAU files** → attribute → commit | `gen(doc)`→harness → eval(harness) → ADB → evolve_agent edits **the document** → guard → track → commit |
| Edit playbook | `nexau-evolution-guide` skill | `hdp-evolution-guide` skill (operator vocabulary) |
| Edits land on | NexAU workspace files | the HDP document; `gen` compiles to a harness |

Treatment **evolves the document** (not merely round-trips the workspace through HDP — that would
test only generator fidelity). `master.yaml`'s `hdp.document` is the gen input and the proposer's
edit target.

## Held identical (the experiment isolates exactly one variable)

- base model + reasoning effort;
- the eval harness (harbor / E2B), task set, `k`, and seed;
- iteration count and the evolve_agent's model;
- the ADB configuration **and the evidence it produces** for the proposer;
- the **starting harness behavior** (via Gate 1 below).

**The one variable:** the edit substrate — NexAU workspace files (control) vs the HDP document
(treatment).

## Validity gates (a failed gate invalidates the experiment)

1. **Iteration-0 identity.** `gen(lift(seed))` MUST reproduce the seed's behavior, and therefore
   the same baseline (~62.9% gpt-5.2 TB2). Identity is *behavioral*: every embedded/referenced/
   scaffold file byte-identical to `agents/code_agent_simple`; `code_agent.yaml` *semantically*
   identical (parsed-YAML equal — comments/blank-lines re-rendered from the gen template do not
   change how NexAU loads it); the only permitted extra artifact is the non-runtime
   `hdp_attribution.json` sidecar. Verified **before** any campaign, **$0** (pure round-trip +
   file compare). **Status: PASSING** — locked by `hdp/engine/tests/test_roundtrip_identity.py`.
   If it ever fails, fix the adapter — do not run the campaign.
2. **Proposer equivalence.** Same model, same ADB evidence; the two playbooks differ ONLY in
   edit-target and edit-vocabulary — same goal framing, same evidence format, same iteration budget.
3. **Governance mirrors AHE controllability.** Treatment's `governance.evolution`
   (read-only `verification`/`governance`/`execution`; protected `system-rules-core`) is set to
   match exactly what AHE already forbids (read-only verifier/model config, non-deletable seed
   prompt). Governance is neither an extra handicap nor an advantage.

## The action-space confound (decision: accept + scope + log the gap)

Control's evolve_agent edits files freely; treatment's proposer can only make edits HDP + the
NexAU adapter can **represent** (v1 rejects `policy` and non-external `verifier`). Treatment's
action-space is therefore a subset of control's, so treatment could lose pass@1 for a non-thesis
reason. Handling:

- Both arms run at full range; control is **not** caged.
- After the campaign, classify each of **control's** component edits against HDP coverage to build
  the **expressibility gap-list** — edits control made that treatment could not represent.
  Mechanism: `lift` control's evolved workspace and diff against the HDP document it would produce;
  un-liftable or adapter-rejected edits are the gap.
- The claim is scoped: *within HDP-supported component types, treatment matches control on pass@1,
  while adding governance, attribution, and portability.* The gap-list is reported as the HDP v2
  backlog (and answers the survey's "what's missing" call).

## Why the comparison is clean

If Gate 1 holds and Gates 2–3 keep the proposer and governance equivalent, any pass@1 difference
comes from only (a) the HDP indirection or (b) action-space narrowing; the gap-list isolates (b).
The residual is the **true cost of the abstraction**, which the thesis predicts is ≈ 0.

## Metrics (what `bench` records, JSONL-only)

- `pass_at_1` per arm per iteration (primary).
- `tokens_per_accepted_edit` (cost of evolution).
- `guard_rollbacks` (governance violations caught) — treatment only.
- `attest_fix_precision` / `attest_regression_recall` (decision-observability quality, AHE §RQ3b).
- `expressibility_gap` count (control edits HDP couldn't represent).

## Implementation order (phased; each phase verifiable, most for $0)

0. **Gate 1 — round-trip identity** (`lift`→`gen`→file-compare vs seed). No spend. *First.*
1. **guard** — post-edit governance enforcement via `differ.diff_docs`: every `ComponentDelta`
   must land in an `editable` layer, not touch `protected`/`read_only`, and match a declared
   manifest operator; violations roll that component back. Unit-testable, no spend.
2. **track** — write `change_manifest.json` (operators + repair_spec + AHE predictions), git-commit,
   bump `meta.version`. Unit-testable, no spend.
3. **attest** — reconcile prior-iteration predictions vs this eval's deltas → verdict; key failures
   to components via `hdp_attribution.json`. Unit-testable with fixtures, no spend.
4. **eval seam** — `run.py::_eval_reward` calls `evolve.run_harbor` + `compute_stats` on the gen'd
   harness dir (import-directly decision). Real spend; smoke with `--dry-run` first.
5. **proposer retarget** — `hdp-evolution-guide` skill + launch evolve_agent with
   `EVOLVE_WORK_DIR=<doc dir>`. Real spend.
6. **bench** — real treatment-vs-control table + multi-seed; the gap-list classifier.

This document is the acceptance contract for phases 0–6.

## Implementation status (2026-06-15)

| Phase | Status |
|---|---|
| 0 Gate 1 identity | ✅ DONE — `test_roundtrip_identity` (3) |
| 1 guard | ✅ DONE — PDP/PEP, `test_guard` (7) |
| 2 track | ✅ DONE — manifest/semver/commit, `test_track` (7) |
| 3 attest | ✅ DONE — verdicts + metrics, `test_attest` (7) |
| 4 eval seam | ✅ DONE (mock-verified) — `hdp/engine/eval.py`, `test_eval_seam` (3) |
| — treatment loop | ✅ DONE (end-to-end $0) — `hdp/engine/loop.py`, `test_loop` (2) |
| 5 proposer retarget | ◑ PARTIAL — `hdp-evolution-guide` skill + `propose.py` done & tested (4); only the
  LLM launch (`_live_runner`) is the spend-gated seam (inject `agent_runner=`) |
| 6 bench + live A/B campaign | ⏳ SPEND-GATED — needs the live runner + real harbor eval (E2B+LLM) |

49 engine tests pass; dry-run A/B smoke wires both arms. Everything verifiable without spend is
implemented. The only remaining work is the paid live campaign (and its live runner).
