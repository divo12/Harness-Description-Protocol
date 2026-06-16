"""CLI: python -m hdp.engine.gen <hdp_dir> <out_dir> [--target nexau]

Compile an HDP document into a backend harness.
"""
import argparse
import sys

from hdp.engine.core.loader import load
from hdp.engine.gen import generate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hdp.engine.gen")
    parser.add_argument("hdp_dir", help="path to the <name>.hdp document directory")
    parser.add_argument("out_dir", help="output directory for the generated harness")
    parser.add_argument("--target", default="nexau")
    args = parser.parse_args(argv)

    doc = load(args.hdp_dir)
    out = generate(doc, args.out_dir, target=args.target)
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    print(f"generated {len(files)} files -> {out}")
    for f in files:
        print(f"  {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
