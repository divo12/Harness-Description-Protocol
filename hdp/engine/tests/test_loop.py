"""End-to-end test of the treatment-arm evolve loop (hdp.engine.loop) — $0, stub proposer."""
from __future__ import annotations

from pathlib import Path

from hdp.engine import loop
from hdp.engine.core.loader import HDPDoc, load, save
from hdp.engine.eval import EvalResult

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "hdp" / "examples" / "code-agent-simple.hdp"


def _cfg():
    return {"hdp": {"document": str(EXAMPLE), "target": "nexau"},
            "run": {"smoke": {"fake_reward": 0.5}}}


def _mem_proposer(doc: HDPDoc, evidence: dict, iteration: int) -> dict:
    """Governed edit: annotate long-term memory (an editable context component)."""
    mem = next(c for c in doc.raw["layers"]["context"] if c["id"] == "long-term-memory")
    mem["description"] = f"lesson learned in iter {iteration}"
    save(doc)
    return {
        "hdp": "0.1", "iteration": iteration,
        "changes": [{
            "change_id": f"chg-{iteration}", "operator": "update",
            "component_id": "long-term-memory", "layer": "context",
            "failure_evidence": "trace: missed t1", "root_cause": "no lesson",
            "repair_spec": {"editable_resources": ["context/memory/long-term.md"],
                            "validation_criteria": ["t1 flips to pass"]},
            "prediction": {"expected_fixes": ["t1"], "at_risk_regressions": []},
        }],
    }


def _fake_eval_factory(seq):
    seq = list(seq)

    def fake_eval(cfg, harness_dir, iteration_dir, *, dry_run=True, fake_reward=1.0):
        tasks = seq.pop(0)
        return EvalResult(pass_rate=sum(tasks.values()) / len(tasks),
                          stats={"task_results": tasks})
    return fake_eval


def test_full_loop_runs_and_attests_previous_iteration(tmp_path):
    # iter1: t1 fails; iter2: t1 passes -> iter1's predicted fix lands -> verdict 'effective'.
    eval_fn = _fake_eval_factory([{"t1": 0, "t2": 1}, {"t1": 1, "t2": 1}])
    results = loop.evolve(_cfg(), proposer=_mem_proposer, workdir=tmp_path,
                          dry_run=True, max_iterations=2, eval_fn=eval_fn)

    assert len(results) == 2
    assert [r.guard_denied for r in results] == [0, 0]          # both edits governed
    assert [r.version for r in results] == ["1.0.1", "1.0.2"]   # two patch bumps
    # iter1's manifest was settled during iter2 against the observed flip.
    assert results[0].manifest["changes"][0]["verdict"] == "effective"
    # the edit actually landed in the working document.
    final = load(tmp_path / "doc.hdp")
    assert "iter 2" in (final.component("long-term-memory").description or "")


def test_loop_guard_rolls_back_illegal_edit(tmp_path):
    def illegal_proposer(doc, evidence, iteration):
        # tamper with the read-only verification layer, then declare it.
        doc.raw["layers"]["verification"][0]["description"] = "tampered"
        save(doc)
        return {
            "hdp": "0.1", "iteration": iteration,
            "changes": [{
                "change_id": "chg-bad", "operator": "update",
                "component_id": "tb2-verifier", "layer": "verification",
                "failure_evidence": "e", "root_cause": "r",
                "repair_spec": {"editable_resources": ["x"], "validation_criteria": ["y"]},
                "prediction": {"expected_fixes": [], "at_risk_regressions": []},
            }],
        }

    eval_fn = _fake_eval_factory([{"t1": 1}])
    results = loop.evolve(_cfg(), proposer=illegal_proposer, workdir=tmp_path,
                          dry_run=True, max_iterations=1, eval_fn=eval_fn)

    assert results[0].guard_denied == 1                      # the illegal edit was denied
    final = load(tmp_path / "doc.hdp")
    # the read-only verifier was rolled back to its original (no 'tampered').
    assert (final.component("tb2-verifier").description or "") != "tampered"
