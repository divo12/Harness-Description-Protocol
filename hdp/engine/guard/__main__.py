"""CLI: python -m hdp.engine.guard <hdp_dir> --edit edit.json [--mode enforce|review] [--apply]

The secure edit gateway (PDP + PEP). By default it *decides* an edit without touching the tree
(dry); ``--apply`` enforces it atomically. ``edit.json`` holds the
:class:`hdp.engine.guard.pdp.Edit` fields (operator, layer, component_id, component?, embedded?,
remove_files?, manifest?). Exit code 0 = allow/review, 1 = deny.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hdp.engine.core.loader import load
from hdp.engine.guard.pdp import Edit
from hdp.engine.guard.pep import apply, dry_decide


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m hdp.engine.guard",
                                description="HDP secure edit gateway (PDP + PEP).")
    p.add_argument("hdp_dir", help="path to the <name>.hdp document directory")
    p.add_argument("--edit", required=True, help="path to a JSON file of Edit fields")
    p.add_argument("--mode", choices=["enforce", "review"], default="enforce")
    p.add_argument("--apply", action="store_true",
                   help="enforce the edit atomically (default: decide-only, tree untouched)")
    args = p.parse_args(argv)

    doc = load(args.hdp_dir)
    edit = Edit(**json.loads(Path(args.edit).read_text(encoding="utf-8")))

    if args.apply:
        res = apply(edit, doc, mode=args.mode)
        out = {"status": res.status, "applied": res.applied,
               "reasons": res.decision.reasons,
               "review_dir": str(res.review_dir) if res.review_dir else None}
    else:
        dec = dry_decide(edit, doc, mode=args.mode)
        out = {"status": dec.status, "applied": False, "reasons": dec.reasons}

    print(json.dumps(out, indent=2))
    return 1 if out["status"] == "deny" else 0


if __name__ == "__main__":
    sys.exit(main())
