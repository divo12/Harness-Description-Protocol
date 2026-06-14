"""hdp.engine — the active HDP harness-edit engine.

Phase 0 stands up the package skeleton plus the cross-cutting plumbing that every
later phase plugs into: the single metrics sink (:mod:`hdp.engine.metrics`), the
master runner (:mod:`hdp.engine.run`), and the legacy→canonical change-manifest
shim (:mod:`hdp.engine.track.manifest_shim`).

Build order (see hdp/SPEC.md and the implementation brief):
  Phase 0 scaffold → core+gen → lift → guard → track+attest → bench.
The lift/gen/guard/track/attest/bench packages are stubs in Phase 0; each exposes a
``smoke_step(run)`` that logs one metric so ``smoke --dry-run`` exercises every module.
"""
