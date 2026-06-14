"""hdp.engine.adapters — Phase 1 stub.

Will define the ``FrameworkAdapter`` ABC (``generate(doc, out_dir)`` /
``lift(harness_dir) -> HDPDoc``) and ``NexAUAdapter`` — the vendor-neutrality seam.
"""
NAME = "adapters"
PHASE = "Phase 1 (core+gen)"


def smoke_step(run) -> None:
    """Stub: pretend the NexAU adapter was selected for this harness."""
    run.log("adapter_selected", "nexau", phase="gen")
