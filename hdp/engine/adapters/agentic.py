"""hdp.engine.adapters.agentic — shared mechanics for the ``agentic`` lift strategy.

The heuristic mapper only looks at a backend's fixed file slots. For a harness that doesn't
match those conventions, the ``agentic`` strategy lets an LLM read the repository broadly and
propose the HDP document directly. This module holds the backend-agnostic mechanics (read the
repo, build the prompt, parse the reply); each adapter keeps its own ``agentic_lift`` method
(passing its own ``target``/``base_model``) so adapter behavior stays uncoupled and independently
testable — only these mechanics are shared, exactly like :mod:`hdp.engine.adapters.uncertain`.
"""
from __future__ import annotations

import json
from pathlib import Path

_TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".txt", ".toml", ".cfg",
                  ".ini", ".sh", ".j2", ".jinja", ".jinja2", ""}
_SKIP_DIRS = {"__pycache__", ".git", ".venv", "node_modules", ".mypy_cache", ".pytest_cache"}


def read_harness_files(harness_dir: Path, *, max_files: int = 60,
                       max_bytes: int = 20_000) -> list[tuple[str, str]]:
    """Broadly read text files under *harness_dir* as ``(relpath, content)``, size/count-capped
    so the prompt stays bounded. Binary/oversized/hidden-dir files are skipped."""
    out: list[tuple[str, str]] = []
    for path in sorted(harness_dir.rglob("*")):
        if len(out) >= max_files:
            break
        if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(harness_dir).parts):
            continue
        try:
            content = path.read_text(encoding="utf-8")[:max_bytes]
        except (UnicodeDecodeError, OSError):
            continue
        out.append((str(path.relative_to(harness_dir)), content))
    return out


def build_agentic_prompt(harness_dir: Path, target: str, meta_id: str | None,
                         version: str, base_model: str | None) -> str:
    """Assemble the single-shot prompt asking the LLM to propose an HDP document."""
    files = read_harness_files(harness_dir)
    tree = "\n".join(name for name, _ in files) or "(no readable files)"
    blobs = "\n\n".join(f"### {name}\n{content}" for name, content in files)
    return (
        "You are lifting a coding-agent harness into an HDP document (a typed YAML/JSON schema "
        "describing an agent harness across the ETCLOVG taxonomy: execution, tooling, context, "
        "lifecycle, observability, verification, governance).\n\n"
        f"Target backend: {target}\n"
        f"Use meta.id={meta_id or Path(harness_dir).name!r}, meta.version={version!r}, "
        f"meta.base_model={base_model!r}, meta.targets=[{target!r}].\n\n"
        "Read the repository below and identify the harness-relevant structures (system "
        "prompts/rules, tools, memory, the agent loop, middleware, tracers, governance). Propose "
        "a single HDP document as ONE JSON object with keys: hdp (\"0.1\"), meta, layers "
        "(context/tooling/lifecycle/observability as applicable), and governance.\n\n"
        f"## Repository file tree\n{tree}\n\n## File contents\n{blobs}\n\n"
        "Reply with ONLY the JSON object — no markdown fences, no commentary."
    )


def parse_agentic_doc(text: str) -> dict:
    """Extract the JSON object from an LLM reply. Raise ``ValueError`` if it isn't a JSON dict —
    an unparseable agentic proposal fails loud, it is never coerced into something valid."""
    s = (text or "").strip()
    if s.startswith("```"):  # tolerate a fenced ```json block despite the instruction
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.strip("`")
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("agentic lift: LLM reply contained no JSON object")
    try:
        doc = json.loads(s[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"agentic lift: LLM reply was not valid JSON: {e}") from e
    if not isinstance(doc, dict):
        raise ValueError("agentic lift: LLM reply JSON was not an object")
    return doc
