"""hdp.engine.bench — Phase 5 stub (prove the thesis: treatment vs control).

Phase 0 ships :func:`build_table`, which builds the 2-arm comparison table ONLY by
reading each run's ``metrics.jsonl`` (never by re-deriving numbers). Phase 5 adds the
real per-iteration quality/cost metrics and the multi-seed driver.
"""
from pathlib import Path

from hdp.engine.metrics import Run

NAME = "bench"
PHASE = "Phase 5 (bench)"


def smoke_step(run) -> None:
    """Stub: record that the bench phase was reached for this arm."""
    run.log("bench_reached", 1, phase="bench")


def _aggregate(run_dir: Path | str) -> tuple[str | None, dict]:
    """Collapse a run's metric stream to the last value seen per metric name."""
    arm: str | None = None
    agg: dict[str, object] = {}
    for rec in Run.read_metrics(run_dir):
        arm = rec.get("arm", arm)
        agg[rec["metric"]] = rec["value"]
    return arm, agg


def build_table(run_dirs: list[Path | str]) -> str:
    """Render a text table comparing arms, one column per metric, from JSONL alone."""
    rows: dict[str, dict] = {}
    for d in run_dirs:
        arm, agg = _aggregate(d)
        if arm is not None:
            rows[arm] = agg

    metrics = sorted({m for agg in rows.values() for m in agg})
    arm_w = max([len("arm")] + [len(a) for a in rows]) if rows else len("arm")
    col_w = {m: max(len(m), *(len(str(rows[a].get(m, "-"))) for a in rows)) for m in metrics} \
        if rows else {}

    def fmt_row(label: str, cells: list[str]) -> str:
        out = label.ljust(arm_w)
        for m, cell in zip(metrics, cells):
            out += "  " + cell.ljust(col_w[m])
        return out.rstrip()

    lines = ["HDP A/B (Phase 0 smoke — fake numbers under --dry-run)"]
    lines.append(fmt_row("arm", list(metrics)))
    lines.append("-" * (len(lines[-1]) or 1))
    for arm in sorted(rows):
        lines.append(fmt_row(arm, [str(rows[arm].get(m, "-")) for m in metrics]))
    return "\n".join(lines)
