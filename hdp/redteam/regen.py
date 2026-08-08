"""Regenerate a red-team candidate fixture with a LIVE LLM. Never run as part of pytest/CI.

    python -m hdp.redteam.regen [--example code-agent-simple] [-n 20]

Requires ``LLM_API_KEY`` (and optionally ``LLM_BASE_URL``, ``LLM_MODEL``) in the environment.
Raises loudly if the API key is unset — it never silently falls back to a canned/heuristic path.
Writes ``hdp/redteam/fixtures/<doc_id>_candidates.json``, the $0 cached input the default test
suite replays. Uses only the standard library for the HTTP call (no new dependency).
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from collections.abc import Callable
from pathlib import Path

from hdp.engine.core.loader import load
from hdp.redteam.llm_gen import propose_candidates

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "hdp" / "examples"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _live_llm() -> Callable[[str], str]:
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        raise RuntimeError(
            "regen requires LLM_API_KEY in the environment; refusing to run without it"
        )
    base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL", "gpt-5.2")

    def llm(prompt: str) -> str:
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310 (explicit, operator-invoked script)
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return str(content)

    return llm


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Regenerate a red-team candidate fixture from a LIVE LLM (spends money)."
    )
    ap.add_argument("--example", default="code-agent-simple",
                    help="example doc id under hdp/examples/ (without the .hdp suffix)")
    ap.add_argument("-n", type=int, default=20, help="number of candidates to request")
    args = ap.parse_args()

    doc = load(EXAMPLES / f"{args.example}.hdp")
    candidates = propose_candidates(doc, _live_llm(), n=args.n)

    FIXTURES.mkdir(parents=True, exist_ok=True)
    out = FIXTURES / f"{doc.model.meta.id}_candidates.json"
    out.write_text(
        json.dumps([c.to_dict() for c in candidates], indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(candidates)} candidates -> {out}")


if __name__ == "__main__":
    main()
