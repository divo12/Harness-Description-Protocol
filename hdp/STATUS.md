# HDP Engine — implementation status

**Branch:** `ahe-explore` · **Merge:** `5f00d4a` (parents `5965774` + `72075a2`)
**Backup (pre-merge revert point):** `ahe-explore-backup` @ `5965774`

## Phase coverage (code present, imports clean, tests green)

| Phase | Modules | Status |
|---|---|---|
| 0 — consolidate | `metrics`, `run`, `track.manifest_shim`, `scripts/hdp.sh`, `configs/hdp/master.yaml` | ✅ |
| 1 — core + gen | `core.loader/differ/models`, `adapters`, `gen` | ✅ |
| 2 — lift | `lift`, `adapters.nexau.lift` | ✅ |
| 3 — guard | `guard` (see note) | ✅ |
| 4 — track + attest | `track` (manifest/semver/commit/record), `attest` (verdicts) | ✅ |
| 5 — bench | `loop`, `propose`, `eval`, `bench`, `run evolve` | ✅ |

**Verification:** 67 tests pass; `./scripts/hdp.sh smoke --dry-run` green (2-arm A/B table);
all phase modules import; guard CLI works.

## Open items (do not block push)

1. **Phase 3 guard is implemented twice and not yet reconciled.** Both gateways coexist over
   the same governance policy: the reconciliation gateway (`evaluate`/`enforce`, used by the
   evolve loop) and the atomic-transaction gateway (`decide`/`apply`/`approve`, tiered hard-block
   + `review` mode + audit trail). Both are tested. Collapsing them into one design is deferred.
2. **Results not yet demonstrated against the real benchmark bars (spend-gated).** Status is
   verified at unit-test + dry-run level only. Still to run with real LLM/E2B spend:
   - Phase 1 score-match: generated harness ≈ 62.9% on Terminal-Bench 2.
   - Phase 5 live A/B: real treatment-vs-control campaign (per `c72e7aa`,
     "verifiable core done, live campaign spend-gated").
