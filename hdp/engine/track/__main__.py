from hdp.engine.track import NAME, PHASE

print(
    f"hdp.engine.{NAME}: stub (lands in {PHASE}). "
    f"Run `python -m hdp.engine.track.manifest_shim <legacy.json>` for the manifest shim, "
    f"or `./scripts/hdp.sh smoke --dry-run` for the end-to-end wiring check."
)
