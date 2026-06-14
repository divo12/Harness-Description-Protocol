"""CLI: python -m hdp.engine.lift <harness_dir> <out_dir.hdp> [--target nexau]

Recover an HDP document from an existing backend harness.
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
    args = parser.parse_args(argv)

    doc = lift(args.harness_dir, args.out_dir, target=args.target)
    comps = sum(1 for _ in doc.components())
    print(f"lifted {comps} components -> {Path(args.out_dir).resolve()}/hdp.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
