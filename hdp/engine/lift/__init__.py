"""hdp.engine.lift — Phase 2 stub (ask #1: "Build HDP").

Will map a NexAU harness layout → an HDPDoc (deterministic structure + one LLM pass
for what structure can't give). Phase 0 exposes only a smoke step.
"""
NAME = "lift"
PHASE = "Phase 2 (lift)"


def smoke_step(run) -> None:
    """Stub: pretend to lift the seed harness into an HDP document."""
    run.log("lift_components", 9, phase="lift")
