"""hdp.engine.gen — Phase 1 stub (the keystone: HDPDoc → harness).

Will render code-agent-simple.hdp → a NexAU harness via Jinja2 and inject the
``hdp.component_id`` trace tag into generated wrappers. Phase 0 exposes only a smoke step.
"""
NAME = "gen"
PHASE = "Phase 1 (core+gen)"


def smoke_step(run) -> None:
    """Stub: pretend to generate the NexAU harness from the HDP document."""
    run.log("gen_files", 6, phase="gen")
