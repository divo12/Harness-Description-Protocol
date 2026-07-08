"""hdp.engine.run — master entry point for the HDP engine.

Sub-commands:  lift | gen | port | evolve | bench | smoke

Driven by a master config (configs/hdp/master.yaml) that reuses the repo's existing
``_base:`` overlay + ``${ENV}`` substitution via :func:`evolve.load_config`. Every
sub-command opens a :class:`hdp.engine.metrics.Run` so all metrics flow through the one
JSONL sink. ``lift``/``gen``/``evolve`` are real (``evolve`` drives :func:`hdp.engine.loop.evolve`
with the retargeted evolve_agent + harbor eval); ``smoke`` still runs the stubbed 2-arm wiring
check and ``bench.build_table`` renders the 2-arm table from JSONL.

Usage:  ./scripts/hdp.sh <sub-command> [--config configs/hdp/master.yaml] [--smoke] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

# Reuse the repo's config loader (handles _base inheritance, deep merge, ${ENV}).
from ahe_control.evolve import load_config  # type: ignore
from hdp.engine import adapters, attest, bench, core, gen, guard, lift, track
from hdp.engine.metrics import Run

DEFAULT_CONFIG = "configs/hdp/master.yaml"


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _run_id(arm: str, ts: str) -> str:
    return f"hdp-{arm}-{ts}"


def _smoke_cfg(cfg: dict) -> dict:
    return ((cfg.get("run") or {}).get("smoke")) or {}


class _RealEvalNotWired(NotImplementedError):
    pass


def _eval_reward(cfg: dict, dry_run: bool, arm: str) -> float:
    """The single eval seam. Phase 0 only supports the dry-run fake reward; the real
    harbor eval is wired in Phase 5."""
    if dry_run:
        return float(_smoke_cfg(cfg).get("fake_reward", 1.0))
    raise _RealEvalNotWired(
        "real harbor eval lands in Phase 5; run smoke with --dry-run for the Phase 0 "
        "wiring check."
    )


def _gen_step(r: Run) -> None:
    core.smoke_step(r)
    adapters.smoke_step(r)
    gen.smoke_step(r)


def _evolve_step(r: Run) -> None:
    guard.smoke_step(r)
    track.smoke_step(r)
    attest.smoke_step(r)


def _pipeline(run: Run, cfg: dict, dry_run: bool) -> None:
    """Full stubbed chain: lift → gen → (one evolve iter: guard/track/attest) → bench."""
    steps: list[tuple[str, Callable[[Run], None]]] = [
        ("lift", lift.smoke_step),
        ("gen", _gen_step),
        ("evolve", _evolve_step),
    ]
    for phase, fn in run.progress(steps, desc=f"smoke[{run.arm}]"):
        run.set_phase(phase, iteration=1 if phase == "evolve" else None)
        fn(run)

    run.set_phase("bench", iteration=1)
    bench.smoke_step(run)
    reward = _eval_reward(cfg, dry_run, run.arm)
    run.log("pass_at_1", reward, phase="bench", iteration=1)
    run.log("tokens_per_accepted_edit", 500, phase="bench", iteration=1)


def cmd_smoke(cfg: dict, dry_run: bool) -> int:
    dry_run = dry_run or bool(_smoke_cfg(cfg).get("dry_run", False))
    seed = int((cfg.get("run") or {}).get("seed", 0))
    ts = _timestamp()
    run_dirs: list[Path] = []
    for arm in ("control", "treatment"):
        with Run(_run_id(arm, ts), arm, seed=seed, config=cfg) as run:
            _pipeline(run, cfg, dry_run)
            run_dirs.append(run.dir)
    print()
    print(bench.build_table(run_dirs))
    return 0


def cmd_lift(cfg: dict) -> int:
    """Real lift: backend harness -> HDP document under runs/<run_id>/lifted.hdp."""
    from hdp.engine.lift import lift as lift_harness

    hdp_cfg = cfg.get("hdp") or {}
    harness_path = hdp_cfg.get("harness", "agents/code_agent_simple")
    target = hdp_cfg.get("target", "nexau")
    arm = (cfg.get("run") or {}).get("arm", "treatment")
    seed = int((cfg.get("run") or {}).get("seed", 0))

    with Run(_run_id(arm, _timestamp()), arm, seed=seed, config=cfg) as run:
        run.set_phase("lift")
        out = run.dir / "lifted.hdp"
        doc = lift_harness(harness_path, out, target=target)
        n = sum(1 for _ in doc.components())
        run.log("lift_components", n, phase="lift", component_id=doc.model.meta.id)
        print(f"\nlifted {n} components ({target}) -> {out}/hdp.yaml")
    return 0


def cmd_gen(cfg: dict) -> int:
    """Real generation: HDP document -> backend harness under runs/<run_id>/harness."""
    from hdp.engine.core.loader import load
    from hdp.engine.gen import generate

    hdp_cfg = cfg.get("hdp") or {}
    doc_path = hdp_cfg.get("document", "hdp/examples/code-agent-simple.hdp")
    target = hdp_cfg.get("target", "nexau")
    arm = (cfg.get("run") or {}).get("arm", "treatment")
    seed = int((cfg.get("run") or {}).get("seed", 0))

    with Run(_run_id(arm, _timestamp()), arm, seed=seed, config=cfg) as run:
        run.set_phase("gen")
        doc = load(doc_path)
        out = run.dir / "harness"
        generate(doc, out, target=target)
        n = sum(1 for p in out.rglob("*") if p.is_file())
        run.log("gen_files", n, phase="gen", component_id=doc.model.meta.id)
        print(f"\ngenerated {n} files ({target}) -> {out}")
    return 0


def cmd_port(cfg: dict) -> int:
    """Real cross-backend port: source harness -> HDP -> destination harness, with the honest
    coverage report written beside it. Refuses (prints the issue list) on a blocking gap."""
    from hdp.engine.port import PortCoverageError
    from hdp.engine.port import port as port_harness

    hdp_cfg = cfg.get("hdp") or {}
    port_cfg = hdp_cfg.get("port") or {}
    harness_path = port_cfg.get("harness", hdp_cfg.get("harness", "agents/code_agent_simple"))
    source_target = port_cfg.get("source_target", "nexau")
    dest_target = port_cfg.get("dest_target", "mini-swe-agent")
    allow_partial = bool(port_cfg.get("allow_partial", False))
    arm = (cfg.get("run") or {}).get("arm", "treatment")
    seed = int((cfg.get("run") or {}).get("seed", 0))

    with Run(_run_id(arm, _timestamp()), arm, seed=seed, config=cfg) as run:
        run.set_phase("port")
        out = run.dir / "ported"
        try:
            result = port_harness(
                harness_path, out, source_target=source_target, dest_target=dest_target,
                allow_partial=allow_partial,
            )
        except PortCoverageError as e:
            run.log("port_blocked", len(e.report.blocking), phase="port")
            print(f"\nREFUSED: {e}")
            for i in e.report.issues:
                print(f"  {i.severity:<12} {i.component_id:<24} {i.reason}")
            return 1
        run.log("port_issues_found", len(result.coverage.issues), phase="port",
                component_id=result.hdp_doc.model.meta.id)
        print(f"\nported {source_target} -> {dest_target} "
              f"({len(result.coverage.issues)} coverage issue(s)) -> {result.harness_dir}")
    return 0


def cmd_evolve(cfg: dict, dry_run: bool, *, proposer=None, eval_fn=None) -> int:
    """Drive the real treatment-arm evolve loop (hdp.engine.loop) end to end.

    ``--dry-run`` fakes only the harbor eval; the proposer (the retargeted evolve_agent) is real
    and spends. ``proposer``/``eval_fn`` are injectable so this CLI wiring is tested without
    nexau/E2B/LLM."""
    from hdp.engine import loop
    from hdp.engine.eval import eval_harness
    from hdp.engine.propose import EvolveAgentProposer

    smoke = _smoke_cfg(cfg)
    dry_run = dry_run or bool(smoke.get("dry_run", False))
    run_cfg = cfg.get("run") or {}
    if dry_run or smoke.get("enabled"):
        max_it = int(smoke.get("max_iterations", 1))
    else:
        max_it = int(run_cfg.get("max_iterations", cfg.get("max_iterations", 1)))
    arm = run_cfg.get("arm", "treatment")
    seed = int(run_cfg.get("seed", 0))

    with Run(_run_id(arm, _timestamp()), arm, seed=seed, config=cfg) as run:
        run.set_phase("evolve")
        results = loop.evolve(
            cfg, proposer=proposer or EvolveAgentProposer(cfg), workdir=run.dir,
            dry_run=dry_run, max_iterations=max_it, eval_fn=eval_fn or eval_harness, run=run,
        )
        print()
        for r in results:
            print(f"  iter {r.iteration}: pass@1={r.pass_rate}  v{r.version}  "
                  f"guard_denied={r.guard_denied}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hdp.engine.run", description=__doc__)
    parser.add_argument(
        "command", choices=["lift", "gen", "port", "evolve", "bench", "smoke"],
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--smoke", action="store_true",
                        help="force smoke mode (tiny slice) for any sub-command")
    parser.add_argument("--dry-run", action="store_true",
                        help="stub the harbor eval with the config's fake reward")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    dry_run = args.dry_run

    if args.command == "smoke" or args.smoke:
        return cmd_smoke(cfg, dry_run)
    if args.command == "lift":
        return cmd_lift(cfg)
    if args.command == "gen":
        return cmd_gen(cfg)
    if args.command == "port":
        return cmd_port(cfg)
    if args.command == "evolve":
        return cmd_evolve(cfg, dry_run)
    if args.command == "bench":
        return cmd_smoke(cfg, dry_run)  # Phase 0: bench == 2-arm smoke table
    return 2


if __name__ == "__main__":
    sys.exit(main())
