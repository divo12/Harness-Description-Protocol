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
- `triage.py` — code-aware security triage (see below).
- `regen.py` — the only path that calls a live LLM (see below).
- `fixtures/` — cached `<doc_id>_candidates.json` inputs; the default test suite reads these.

## Code-aware triage (`triage.py`)

The pitch: HDP's typed structure lets a reviewer localize the small fraction of a harness worth
reading the actual code of, instead of auditing the whole codebase.

- `triage_components(doc, llm)` → one `TriageResult` per component; the LLM shortlists those
  worth code-level review, judging **primarily by `blast_radius`** and whether the component
  references executable code (governance tier is a secondary signal). It may legitimately
  shortlist **zero** components for a genuinely low-risk harness.
- `resolve_source(doc, component_id)` → the component's **actual backend source**, read-only.
  Executable code (an `implementation`/`ref` binding) is preferred over an embedded descriptor
  `file:`; a component with only a `file:` (e.g. context system_rules) resolves to that content.
  Returns `None` when nothing is resolvable (a name-only tool, or a builtin with no
  user-authored code). **Source is never executed.**
- `code_aware_redteam(doc, llm)` → runs triage, resolves source for the shortlisted components,
  and reasons over schema + code together to produce Stage-2 `EditCandidate` proposals (which
  chain straight into `run_candidates`). Two LLM calls: triage, then generation.

Resolution notes: known bindings are resolved via the target adapter's checked-in
`_BINDING_ASSETS` map (deterministic); `nexau.*` dotted imports are best-effort against the
gitignored `.code_sources/` tree (returns `None` if absent, never relied on in tests);
`tools.*` adapter bindings resolve under `agents/code_agent_simple/`.

### Coverage-completeness caveat (important)

This tool can only ever see components that **`lift` captured**. If `lift` silently dropped or
misrepresented a component, the triage inherits that blind spot — a clean triage is **not** a
certification of safety, only "these components are worth a closer human look." It is not a
substitute for a full manual security audit, and `resolve_source` honestly returns `None` for a
component whose source it cannot resolve rather than fabricating coverage (e.g. the openharness
example's name-only tools).

## Guard measurement against a real evolve run (`evolve_replay.py`)

Where Stage 2 generates adversarial edits, this measures the guard against the **genuine** edit
stream of a completed AHE evolve run (`hdp.engine.loop.evolve`), and reports allow/deny **split
separately by tier**.

- `load_evolve_run(run_dir)` → `list[EvolveEditRecord]`, reading the loop's on-disk shape:
  `iter-NNN/pre.hdp` (old), `iter-NNN/post.hdp` (raw proposed new), and per-iteration manifests
  under `doc.hdp/evolution/manifests/`. `post.hdp` is snapshotted **only in
  `guard_interaction="monitor"` mode**, so only a monitor-mode run is replayable — the loader
  **fails loud** if `post.hdp` is missing rather than silently guessing.
- `measure_guard_against_run(records)` → `GuardMeasurementReport`, replaying each
  `(old, new, manifest)` through `guard.govern` under **both engines**. The tier split
  (CORE/STRUCTURAL/SOFT) comes from the atomic engine's tiered PDP; the reconcile engine
  contributes per-delta allow/deny counts. `render_measurement_markdown(report)` labels the
  **CORE-denial rate and SOFT-denial rate as distinct figures** — a CORE denial is an attempted
  confinement breach; a SOFT denial is typically manifest/editability hygiene. They are never
  blended into one "% unsafe".

Why replay snapshots instead of trusting the run's logged `guard_audit.jsonl` verdicts: a run
logs verdicts under whatever engine it used, and the **reconcile engine records no tier**. Re-
running each edit through the tiered atomic PDP re-derives the CORE/SOFT split regardless of the
run's engine. This stage involves no LLM at all — it purely replays existing edit records.

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
