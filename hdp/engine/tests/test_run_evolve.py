"""The `evolve` sub-command wires the CLI to the real loop (mocked loop; $0, no nexau/E2B)."""
from __future__ import annotations

from pathlib import Path

import hdp.engine.run as run_mod
from hdp.engine.propose import EvolveAgentProposer

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "hdp" / "examples" / "code-agent-simple.hdp"


def test_cmd_evolve_drives_the_real_loop(monkeypatch):
    captured: dict = {}

    def fake_evolve(cfg, *, proposer, workdir, dry_run, max_iterations, eval_fn, run):
        captured.update(proposer=proposer, dry_run=dry_run,
                        max_iterations=max_iterations, workdir=Path(workdir))
        return []

    monkeypatch.setattr("hdp.engine.loop.evolve", fake_evolve)

    cfg = {"hdp": {"document": str(EXAMPLE), "target": "nexau"},
           "run": {"arm": "treatment", "seed": 0,
                   "smoke": {"max_iterations": 3, "dry_run": True}}}
    rc = run_mod.cmd_evolve(cfg, dry_run=True)

    assert rc == 0
    assert isinstance(captured["proposer"], EvolveAgentProposer)   # real proposer by default
    assert captured["dry_run"] is True                              # --dry-run threaded
    assert captured["max_iterations"] == 3                          # from smoke config
    assert captured["workdir"].exists()                            # a run dir was created
