"""Tests for hdp.engine.eval — the harbor eval seam (mocked; $0, no E2B/LLM)."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from hdp.engine.eval import eval_harness


def test_dry_run_short_circuits_without_importing_evolve(monkeypatch):
    # Make any attempt to import evolve explode, proving dry-run never touches it.
    monkeypatch.setitem(sys.modules, "evolve", None)
    res = eval_harness({"harbor": {"k": 2}}, harness_dir=None, iteration_dir=None,
                       dry_run=True, fake_reward=0.629)
    assert res.pass_rate == 0.629
    assert res.job_dir is None and res.task_results == {}


def test_real_path_calls_run_harbor_and_compute_stats(monkeypatch, tmp_path):
    calls = {}

    def fake_run_harbor(cfg, workspace_dir, agent_cfg, iteration_dir):
        calls["workspace_dir"] = Path(workspace_dir)
        calls["agent_cfg"] = agent_cfg
        calls["iteration_dir"] = Path(iteration_dir)
        return tmp_path / "job"

    def fake_compute_stats(job_dir, k=1):
        calls["job_dir"] = Path(job_dir)
        calls["k"] = k
        return {"pass_rate": 0.77, "task_results": {"t1": 1, "t2": 0}}

    fake_evolve = types.ModuleType("evolve")
    fake_evolve.run_harbor = fake_run_harbor
    fake_evolve.compute_stats = fake_compute_stats
    monkeypatch.setitem(sys.modules, "evolve", fake_evolve)

    harness = tmp_path / "harness"
    itdir = tmp_path / "iter"
    res = eval_harness({"harbor": {"k": 2}, "agent_config_filename": "code_agent.yaml"},
                       harness, itdir, dry_run=False)

    assert res.pass_rate == 0.77
    assert res.task_results == {"t1": 1, "t2": 0}
    assert calls["workspace_dir"] == harness          # evals the GEN'D harness dir
    assert calls["agent_cfg"] == "code_agent.yaml"
    assert calls["iteration_dir"] == itdir
    assert calls["k"] == 2                             # k threaded from harbor config


def test_real_path_requires_dirs():
    with pytest.raises(ValueError):
        eval_harness({}, harness_dir=None, iteration_dir=None, dry_run=False)
