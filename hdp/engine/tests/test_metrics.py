"""Phase 0 acceptance: the metrics sink writes well-formed JSONL + a valid run.json."""
import json

from hdp.engine.metrics import Run

REQUIRED_KEYS = {"ts", "run_id", "arm", "phase", "iteration", "metric", "value"}


def test_log_writes_jsonl_and_header(tmp_path):
    with Run("hdp-test-001", "treatment", seed=7, config={"k": 1}, runs_dir=tmp_path) as run:
        run.set_phase("bench", iteration=2)
        run.log("pass_at_1", 0.71)
        run.log("tokens_per_accepted_edit", 500, component_id="run-shell", change_id="chg-1")

    records = Run.read_metrics(run.dir)
    assert len(records) == 2
    for rec in records:
        assert REQUIRED_KEYS <= set(rec)
        assert rec["run_id"] == "hdp-test-001"
        assert rec["arm"] == "treatment"

    # second record carried the optional attribution keys
    assert records[1]["component_id"] == "run-shell"
    assert records[1]["change_id"] == "chg-1"

    header = json.loads((run.dir / "run.json").read_text())
    assert header["arm"] == "treatment"
    assert header["seed"] == 7
    assert header["start"] and header["end"]
    assert "git_sha" in header
    assert header["config"] == {"k": 1}
