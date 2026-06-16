"""Phase 0 north-star acceptance: `smoke --dry-run` exercises every engine module,
writes a valid metrics.jsonl + run.json per arm, and prints a 2-arm table — fast."""
import time

from hdp.engine import run as run_mod
from hdp.engine.metrics import Run

# Every engine module must log at least one metric during the smoke pipeline.
EXPECTED_METRICS = {
    "lift_components",            # lift
    "core_models_loaded",        # core
    "adapter_selected",          # adapters
    "gen_files",                 # gen
    "guard_allowed",             # guard
    "track_commits",             # track
    "attest_flips_attributed",   # attest
    "bench_reached",             # bench
    "pass_at_1",                 # eval seam
}


def test_smoke_dry_run(tmp_path, monkeypatch, capsys):
    # Redirect run output under the test's tmp dir so we don't pollute repo runs/.
    monkeypatch.setattr(run_mod.Run, "__init__", _patched_init(tmp_path), raising=True)

    t0 = time.time()
    rc = run_mod.main(["smoke", "--dry-run", "--config", "configs/hdp/master.yaml"])
    elapsed = time.time() - t0

    assert rc == 0
    assert elapsed < 30, f"smoke --dry-run too slow: {elapsed:.1f}s"

    out = capsys.readouterr().out
    assert "control" in out and "treatment" in out  # both arms in the table

    arm_dirs = sorted(p for p in tmp_path.iterdir() if p.is_dir())
    assert len(arm_dirs) == 2
    for d in arm_dirs:
        records = Run.read_metrics(d)
        assert records, f"no metrics for {d.name}"
        logged = {r["metric"] for r in records}
        assert EXPECTED_METRICS <= logged, f"missing: {EXPECTED_METRICS - logged}"
        assert (d / "run.json").exists()
        # every arm logged a pass@1
        assert any(r["metric"] == "pass_at_1" for r in records)


def _patched_init(tmp_path):
    """Wrap Run.__init__ to force runs_dir into tmp_path."""
    orig = Run.__init__

    def _init(self, run_id, arm, *, seed=0, config=None, runs_dir=None, phase="init"):
        orig(self, run_id, arm, seed=seed, config=config, runs_dir=tmp_path, phase=phase)

    return _init
