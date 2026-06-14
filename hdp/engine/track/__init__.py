"""hdp.engine.track — Phase 4 stub (ask #2: version-controlled tracker).

Will write change manifests, GitPython commit-per-edit, semver bump (SPEC §7.3), and a
history/diff/rollback query API. Phase 0 ships the canonical change-manifest shim
(:mod:`hdp.engine.track.manifest_shim`) and a smoke step.
"""
NAME = "track"
PHASE = "Phase 4 (track+attest)"


def smoke_step(run) -> None:
    """Stub: pretend one approved edit was committed and the version bumped."""
    run.log("track_commits", 1, phase="evolve", change_id="chg-smoke")
