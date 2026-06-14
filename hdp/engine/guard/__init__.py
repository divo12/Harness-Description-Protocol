"""hdp.engine.guard — Phase 3 stub (ask #3: the security guarantee).

Will implement the PDP (decide allow/deny by reusing hdp_validate.py + SPEC §5.2),
monotonic-confinement, and the atomic PEP. Phase 0 exposes only a smoke step.
"""
NAME = "guard"
PHASE = "Phase 3 (guard)"


def smoke_step(run) -> None:
    """Stub: pretend the gateway allowed one edit and denied none."""
    run.log("guard_allowed", 1, phase="evolve", change_id="chg-smoke")
    run.log("guard_denied", 0, phase="evolve")
