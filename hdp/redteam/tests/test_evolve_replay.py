"""$0 tests for hdp.redteam.evolve_replay — no LLM at all; purely replays edit records.

No real completed evolve run exists on disk, so the test builds a synthetic monitor-mode run
directory in tmp_path from the shipped example (house style: copytree + ruamel doctor), with one
known SOFT violation and one known CORE violation, and asserts the two are measured and reported
as SEPARATE tier figures.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from hdp.redteam.evolve_replay import load_evolve_run, measure_guard_against_run
from hdp.redteam.report import render_measurement_markdown

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "hdp" / "examples" / "code-agent-simple.hdp"


def _doctor(dst: Path, mutate) -> None:
    shutil.copytree(EXAMPLE, dst)
    y = YAML()
    y.preserve_quotes = True
    raw = y.load((dst / "hdp.yaml").read_text(encoding="utf-8"))
    mutate(raw)
    with (dst / "hdp.yaml").open("w", encoding="utf-8") as f:
        y.dump(raw, f)


def _manifest(cid: str, layer: str) -> dict:
    return {"hdp": "0.1", "iteration": 1, "changes": [
        {"component_id": cid, "layer": layer, "operator": "update", "change_id": f"chg-{cid}"}]}


def _build_run(tmp_path: Path) -> Path:
    """A monitor-mode run: iter-001 = SOFT (edit read_only execution), iter-002 = CORE (raise a
    tool's blast_radius above the ceiling). Each iteration has pre.hdp + post.hdp + a manifest."""
    run = tmp_path / "run"
    # per-iteration manifests live inside the working doc, as the real loop writes them
    mdir = run / "doc.hdp" / "evolution" / "manifests"
    mdir.mkdir(parents=True)
    (mdir / "001.json").write_text(json.dumps(_manifest("e2b-sandbox", "execution")))
    (mdir / "002.json").write_text(json.dumps(_manifest("run-shell", "tooling")))

    i1 = run / "iter-001"
    i1.mkdir()
    shutil.copytree(EXAMPLE, i1 / "pre.hdp")
    _doctor(i1 / "post.hdp", lambda r: r["layers"]["execution"][0].__setitem__("cpus", 2))

    i2 = run / "iter-002"
    i2.mkdir()
    shutil.copytree(EXAMPLE, i2 / "pre.hdp")
    _doctor(i2 / "post.hdp",
            lambda r: r["layers"]["tooling"][0].__setitem__("blast_radius", "external"))
    return run


def test_measure_splits_core_and_soft(tmp_path):
    run = _build_run(tmp_path)
    records = load_evolve_run(run)
    assert [r.iteration for r in records] == [1, 2]

    report = measure_guard_against_run(records)
    assert report.total_edits == 2
    assert report.doc_id == "code-agent-simple"

    # the two denials are reported as SEPARATE tier figures, not one blended number
    assert report.denied_by_tier["core"] == 1      # iter-002: blast_radius escalation
    assert report.denied_by_tier["soft"] == 1      # iter-001: edit of a read_only layer
    assert report.core_denial_rate == 0.5
    assert report.soft_denial_rate == 0.5
    assert report.atomic_denied == 2

    # rule distribution attributes the CORE denial to the blast_radius rule
    assert report.rule_distribution["core"] == {"blast_radius": 1}
    assert "editability" in report.rule_distribution["soft"]


def test_render_labels_core_and_soft_distinctly(tmp_path):
    report = measure_guard_against_run(load_evolve_run(_build_run(tmp_path)))
    md = render_measurement_markdown(report)
    assert "CORE-denial rate" in md and "SOFT-denial rate" in md
    assert "Guard measurement" in md and "code-agent-simple" in md


def test_load_evolve_run_requires_post_snapshot(tmp_path):
    # an enforce-mode run has no post.hdp -> not replayable; loader must fail loud
    run = tmp_path / "run"
    (run / "iter-001").mkdir(parents=True)
    shutil.copytree(EXAMPLE, run / "iter-001" / "pre.hdp")
    with pytest.raises(ValueError, match="monitor"):
        load_evolve_run(run)


def test_load_evolve_run_empty_dir_raises(tmp_path):
    (tmp_path / "run").mkdir()
    with pytest.raises(ValueError, match="no iter-"):
        load_evolve_run(tmp_path / "run")
