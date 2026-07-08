"""CLI: python -m hdp.engine.port <source_harness_dir> <out_dir> \
        --source-target nexau --dest-target mini-swe-agent [--keep-hdp] [--allow-partial]

Port a harness across backends, printing the honest coverage report. On a blocking coverage issue,
prints the full issue list (one line per issue) and exits 1 — no Python traceback.
"""
import argparse
import sys
from pathlib import Path

from hdp.engine.port import _SUPPORTED, PortCoverageError, port


def _print_issues(issues) -> None:
    for i in issues:
        print(f"  {i.severity:<12} {i.component_id:<24} {i.reason}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hdp.engine.port")
    parser.add_argument("source_harness_dir", help="path to the source backend harness directory")
    parser.add_argument("out_dir", help="output directory for the ported harness")
    parser.add_argument("--source-target", required=True, choices=list(_SUPPORTED))
    parser.add_argument("--dest-target", required=True, choices=list(_SUPPORTED))
    parser.add_argument("--keep-hdp", action="store_true", help="keep the intermediate .hdp doc")
    parser.add_argument("--allow-partial", action="store_true",
                        help="port even with blocking issues (bypasses the refusal)")
    args = parser.parse_args(argv)

    try:
        result = port(
            args.source_harness_dir, args.out_dir,
            source_target=args.source_target, dest_target=args.dest_target,
            keep_hdp=args.keep_hdp, allow_partial=args.allow_partial,
        )
    except PortCoverageError as e:
        print(f"REFUSED: {e}")
        _print_issues(e.report.issues)
        return 1

    cov = result.coverage
    print(f"ported {cov.source_target} -> {cov.dest_target}: {cov.total_components} components, "
          f"{len(cov.issues)} coverage issue(s) -> {Path(result.harness_dir).resolve()}")
    _print_issues(cov.issues)
    return 0


if __name__ == "__main__":
    sys.exit(main())
