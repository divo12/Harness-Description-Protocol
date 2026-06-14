"""hdp.engine.attest — Phase 4 stub.

Will attribute each pass↔fail flip to a change_id (from hdp.component_id-tagged traces +
the manifest) and set each manifest verdict. Phase 0 exposes only a smoke step.
"""
NAME = "attest"
PHASE = "Phase 4 (track+attest)"


def smoke_step(run) -> None:
    """Stub: pretend one flip was attributed and the manifest verdict set."""
    run.log("attest_flips_attributed", 1, phase="evolve", change_id="chg-smoke")
