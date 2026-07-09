"""CLI: python -m hdp.engine.lift <harness_dir> <out_dir.hdp> [--target nexau]
        [--strategy heuristic|llm_assisted|agentic]

Recover an HDP document from an existing backend harness.

Only ``--strategy heuristic`` (the default) runs from the CLI: the ``llm_assisted``/``agentic``
strategies need an ``llm`` callable, and this repo has no CLI convention for constructing one
(its only LLM path is the full NexAU agent). Selecting them here therefore surfaces the clear
"requires an llm callable" error from :func:`hdp.engine.lift.lift` — proving the flag routes,
without inventing LLM-calling infrastructure. Drive the LLM strategies from Python instead.
"""
import argparse
import sys
from pathlib import Path

from hdp.engine.lift import lift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hdp.engine.lift")
    parser.add_argument("harness_dir", help="path to the backend harness directory")
    parser.add_argument("out_dir", help="output <name>.hdp directory to write")
    parser.add_argument("--target", default="nexau")
    parser.add_argument("--strategy", default="heuristic",
                        choices=["heuristic", "llm_assisted", "agentic"])
    args = parser.parse_args(argv)

    try:
        doc = lift(args.harness_dir, args.out_dir, target=args.target, strategy=args.strategy)
    except ValueError as e:  # e.g. an llm-requiring strategy selected with no CLI llm
        print(f"lift failed: {e}", file=sys.stderr)
        return 2
    comps = sum(1 for _ in doc.components())
    print(f"lifted {comps} components -> {Path(args.out_dir).resolve()}/hdp.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
