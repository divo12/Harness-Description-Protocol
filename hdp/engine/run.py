"""hdp.engine.run — master entry point for the HDP engine.

Sub-commands:  lift | gen | evolve | bench | smoke

Driven by a master config (configs/hdp/master.yaml) that reuses the repo's existing
``_base:`` overlay + ``${ENV}`` substitution via :func:`evolve.load_config`. Every
sub-command opens a :class:`hdp.engine.metrics.Run` so all metrics flow through the one
JSONL sink. Phase 0: lift/gen/evolve/guard/track/attest are stubs; ``smoke`` wires the
full chain across both arms and ``bench.build_table`` renders the 2-arm table.

Usage:  ./scripts/hdp.sh <sub-command> [--config configs/hdp/master.yaml] [--smoke] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

# Reuse the repo's config loader (handles _base inheritance, deep merge, ${ENV}).
from evolve import load_config  # type: ignore

from hdp.engine import adapters, attest, bench, core, gen, guard, lift, track
from hdp.engine.metrics import Run

DEFAULT_CONFIG = "configs/hdp/master.yaml"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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


def _pipeline(run: Run, cfg: dict, dry_run: bool) -> None:
    """Full stubbed chain: lift → gen → (one evolve iter: guard/track/attest) → bench."""
    steps = [
        ("lift", lift.smoke_step),
        ("gen", lambda r: (core.smoke_step(r), adapters.smoke_step(r), gen.smoke_step(r))),
        ("evolve", lambda r: (guard.smoke_step(r), track.smoke_step(r), attest.smoke_step(r))),
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


def _cmd_single(cfg: dict, dry_run: bool, phase: str, fn) -> int:
    """Run one sub-command (lift/gen/evolve/bench) as a single-arm stub."""
    arm = (cfg.get("run") or {}).get("arm", "treatment")
    with Run(_run_id(arm, _timestamp()), arm, seed=int((cfg.get("run") or {}).get("seed", 0)),
             config=cfg) as run:
        run.set_phase(phase)
        fn(run)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hdp.engine.run", description=__doc__)
    parser.add_argument(
        "command", choices=["lift", "gen", "evolve", "bench", "smoke"],
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
    if args.command == "evolve":
        return _cmd_single(cfg, dry_run, "evolve", lambda r: (
            guard.smoke_step(r), track.smoke_step(r), attest.smoke_step(r)))
    if args.command == "bench":
        return cmd_smoke(cfg, dry_run)  # Phase 0: bench == 2-arm smoke table
    return 2


if __name__ == "__main__":
    sys.exit(main())
