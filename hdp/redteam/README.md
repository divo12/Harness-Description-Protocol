# hdp.redteam — LLM-generated adversarial edits against the guard

An LLM proposes plausible-looking candidate edits to a real HDP document; every candidate is
replayed through the **real** guard (`hdp.engine.guard.govern`) under both engines
(`reconcile`, `atomic`) and both modes (`enforce`, `review`); the results are tabulated with
what was caught, in which tier. This **supplements** — it never replaces or modifies — the
hand-written fixture suite at `hdp/engine/tests/test_guard_redteam.py`.

**Detect-and-report only.** If a candidate slips through, the deliverable is a surfaced finding
in the report (and, if you want it enforced, a new failing regression test) — never a change to
the guard's policy logic.

## Pieces

- `llm_gen.py` — `EditCandidate`, `propose_candidates(doc, llm, n=20)` (pure; no guard calls),
  and `run_candidates(old, candidates, *, engines, modes)` (materializes each candidate as a
  doctored on-disk copy and replays it through `govern`).
- `report.py` — `CandidateOutcome`, `RedTeamReport` (with the `.bypasses` view) and
  `render_markdown(report)`.
- `regen.py` — the only path that calls a live LLM (see below).
- `fixtures/` — cached `<doc_id>_candidates.json` inputs; the default test suite reads these.

## `$0` by default

No test in the default `pytest` run ever makes a live LLM call:

- `propose_candidates` is unit-tested with a **fake** `llm` callable (a plain Python function
  returning canned JSON).
- `run_candidates` is exercised against a small set of **inline** `EditCandidate` objects.
- The fixture-driven replay test loads `fixtures/<doc_id>_candidates.json` **if present** and
  **skips** otherwise.

## Regenerating a fixture (live, spends money — never in CI)

```bash
export LLM_API_KEY=...            # required; regen refuses to run without it
export LLM_BASE_URL=...           # optional, defaults to https://api.openai.com/v1
export LLM_MODEL=...              # optional, defaults to gpt-5.2
python -m hdp.redteam.regen --example code-agent-simple -n 20
```

This writes `hdp/redteam/fixtures/code-agent-simple_candidates.json`. Re-running
`pytest hdp/redteam/` then executes the fixture-driven replay against it. `regen.py` uses only
the standard library for the HTTP call (no new dependency) and is never imported by any test.

## `.bypasses` semantics

`RedTeamReport.bypasses` lists candidates the guard **allowed in `enforce` mode** (under either
engine) **even though the LLM expected a denial** (`expect_denied=True`). This is the
security-meaningful slip-through set — not every correctly-allowed legitimate edit. The
`expect_denied` field is the LLM's own guess, kept purely for this calibration view.

## Caveat: tier/rule are engine-dependent

The **atomic** engine renders each denial reason as `"[tier] rule: message"`, so
`CandidateOutcome.tier`/`.rule` are populated from it. The **reconcile** engine emits bare
messages (e.g. `"read-only (execution)"`), so reconcile outcomes carry only `raw_reason` with
`tier=None`. Read tier/rule off the atomic rows. Likewise, the tree-hash invariant
(`tree_unchanged`) holds exactly for a *denied* candidate under the **atomic** engine (which
restores the whole tree on any violation); under reconcile, denied component deltas are rolled
back in-memory but the on-disk `hdp.yaml` is left for the loop to persist, so `tree_unchanged`
there reflects on-disk state rather than the reconciled document.
