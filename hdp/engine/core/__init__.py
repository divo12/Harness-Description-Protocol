"""hdp.engine.core — Phase 1 stub.

Will hold the datamodel-codegen Pydantic models (from hdp/schema/*.json), the ruamel
round-trip loader, and the component-level differ. Phase 0 exposes only a smoke step.
"""
NAME = "core"
PHASE = "Phase 1 (core+gen)"


def smoke_step(run) -> None:
    """Stub: pretend to load the typed HDP document model."""
    run.log("core_models_loaded", 1, phase="gen", component_id="hdp-doc")
