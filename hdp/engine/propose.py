"""hdp.engine.propose — the real proposer: AHE's evolve_agent, retargeted at the HDP document.

Boundary decision: REUSE the evolve_agent (same model, same ADB evidence) but point it at the
HDP *document* and give it the ``hdp-evolution-guide`` playbook instead of ``nexau-evolution-guide``.
The agent free-edits the document and writes a change manifest; ``guard`` then governs the edit.

This module is the loop's ``Proposer``. Two halves:
  * verifiable for $0 — :func:`build_query` (assemble the evolution prompt from evidence) and
    :func:`read_manifest` (read back what the agent declared);
  * the irreducible LLM launch — :meth:`EvolveAgentProposer.run_agent`, injectable so the loop is
    tested with a stub, and supplied by the live campaign via ``agent_runner=`` (the
    spend-gated seam).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ruamel.yaml import YAML

from hdp.engine.core.loader import HDPDoc

SKILL = "hdp-evolution-guide"
EVOLVE_AGENT_DIR = Path(__file__).resolve().parents[2] / "agents" / "evolve_agent"

# A runner launches the retargeted agent against *doc* with *query*; it edits doc files and
# writes evolution/manifests/<iteration>.json. Returns the agent's free-text result (unused).
AgentRunner = Callable[[HDPDoc, str, int], str]


def build_query(doc: HDPDoc, evidence: dict, iteration: int) -> str:
    """Assemble the evolution prompt: the governed task + the round's evidence + attestation."""
    gov = doc.model.governance
    evo = gov.evolution if gov else None
    editable = ", ".join(evo.editable or []) if evo else "(unset)"
    read_only = ", ".join(evo.read_only or []) if evo else "(unset)"
    protected = ", ".join(evo.protected or []) if evo else "(none)"
    docname = doc.path.name  # e.g. "doc.hdp" — your file tools are rooted at the run dir
    manifest_rel = f"{docname}/evolution/manifests/{iteration:03d}.json"
    lines = [
        f"# HDP evolution — iteration {iteration}",
        "",
        f"Improve the agent by editing its HDP document. Your file tools are rooted at this run "
        f"directory; the document is the `{docname}/` subdirectory (manifest at "
        f"`{docname}/hdp.yaml`, embedded content under `{docname}/context/`, `{docname}/tooling/`, "
        "etc.). Use these RELATIVE paths with your file tools.",
        "",
        f"Follow the `{SKILL}` skill exactly: pick ONE highest-leverage improvement, choose the "
        "most specific operator, then:",
        f"1. FIRST write your change manifest to `{manifest_rel}` (operator + evidence + a "
        "falsifiable prediction, per the skill's JSON shape);",
        "2. then make exactly that edit to the document;",
        "3. validate, then call complete_task.",
        "",
        "## Governance (guard rolls back any violation)",
        f"- editable: {editable}",
        f"- read_only (do NOT touch): {read_only}",
        f"- protected (do NOT remove): {protected}",
        "",
        "## This round's evidence",
        f"- pass@1 = {evidence.get('pass_rate')}",
    ]
    if evidence.get("analysis_overview"):
        lines += ["", "## Failure analysis (overview)", str(evidence["analysis_overview"])]
    if evidence.get("attestation"):
        lines += ["", "## Attestation of your previous edits", str(evidence["attestation"])]
    return "\n".join(lines)


def _manifest_path(doc: HDPDoc, iteration: int) -> Path:
    rel = "evolution/manifests"
    evo = doc.model.evolution
    if evo and evo.manifest_dir:
        rel = evo.manifest_dir
    return doc.path / rel / f"{iteration:03d}.json"


def read_manifest(doc: HDPDoc, iteration: int) -> dict:
    """Read the change manifest the agent declared this iteration."""
    path = _manifest_path(doc, iteration)
    if not path.is_file():
        raise FileNotFoundError(
            f"proposer wrote no manifest at {path} — manifest-before-edit was not followed"
        )
    return json.loads(path.read_text())


def _deep_merge(into: dict, patch: dict | None) -> None:
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(into.get(k), dict):
            _deep_merge(into[k], v)
        else:
            into[k] = v


def setup_treatment_agent(workdir: Path | str, *, src: Path = EVOLVE_AGENT_DIR,
                          evolve_patch: dict | None = None) -> Path:
    """Materialize a retargeted evolve_agent under *workdir*: copy the agent, swap its ``skills:``
    from ``nexau-evolution-guide`` to ``hdp-evolution-guide``, and deep-merge *evolve_patch* (the
    config's ``evolve_agent`` block — e.g. gpt-5.2's ``api_type: openai_responses`` + reasoning,
    without which gpt-5.x rejects ``max_tokens`` on chat-completions). Returns the retargeted
    ``evolve_agent.yaml`` path. $0 — no agent is launched here."""
    dest = Path(workdir) / "evolve_agent"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    cfg_path = dest / "evolve_agent.yaml"
    y = YAML()
    y.preserve_quotes = True
    raw = y.load(cfg_path.read_text())
    raw["skills"] = [f"./skills/{SKILL}"]
    _deep_merge(raw, evolve_patch)
    with cfg_path.open("w", encoding="utf-8") as f:
        y.dump(raw, f)
    if not (dest / "skills" / SKILL / "SKILL.md").is_file():
        raise FileNotFoundError(f"{SKILL} skill missing from {dest/'skills'}")
    return cfg_path


def _nexau_agent_factory(cfg_path: Path):
    from nexau import Agent  # imported lazily so $0 paths never need nexau
    return Agent.from_yaml(config_path=Path(cfg_path))  # nexau expects a Path (calls .exists())


def make_live_runner(cfg: dict | None = None, *, src: Path = EVOLVE_AGENT_DIR,
                     agent_factory=_nexau_agent_factory,
                     context_extra: dict | None = None) -> AgentRunner:
    """Build the live (spend-gated) runner: launch the retargeted evolve_agent at the doc's
    workdir with the HDP query. Applies the config's ``evolve_agent`` patch + sets the
    ``${env.LLM_*}`` the agent reads (mirrors evolve.run_evolve_agent). ``agent_factory`` is
    injectable so the launch wiring is tested without nexau/LLM; the default builds the real
    NexAU agent (the campaign spend point)."""
    cfg = cfg or {}

    def runner(doc: HDPDoc, query: str, iteration: int) -> str:
        workdir = doc.path.parent
        cfg_path = setup_treatment_agent(workdir, src=src, evolve_patch=cfg.get("evolve_agent"))
        # the evolve_agent.yaml binds `middleware.*`/`tools.*` relative to its dir (mirrors
        # evolve.run_evolve_agent, which inserts the agent dir on sys.path before launch).
        agent_dir = str(Path(cfg_path).parent)
        if agent_dir not in sys.path:
            sys.path.insert(0, agent_dir)
        os.environ["EVOLVE_WORK_DIR"] = str(workdir)
        if cfg.get("llm"):  # set LLM_MODEL/BASE_URL/API_KEY the evolve_agent.yaml references
            from ahe_control.evolve import get_llm_config, set_llm_env
            set_llm_env(get_llm_config(cfg, "evolve"))
        agent = agent_factory(cfg_path)
        result = agent.run(message=query, context={
            "date": datetime.now().strftime("%Y-%m-%d"),
            "working_directory": str(workdir),
            "document": doc.path.name,
            "iteration": iteration,
            **(context_extra or {}),
        })
        if isinstance(result, tuple):
            result = result[0] if result else ""
        return result or ""
    return runner


class EvolveAgentProposer:
    """Loop ``Proposer``: build the query, run the agent (real by default), read its manifest."""

    def __init__(self, cfg: dict | None = None, agent_runner: AgentRunner | None = None):
        self.run_agent = agent_runner or make_live_runner(cfg)

    def __call__(self, doc: HDPDoc, evidence: dict, iteration: int) -> dict:
        query = build_query(doc, evidence, iteration)
        self.run_agent(doc, query, iteration)
        return read_manifest(doc, iteration)
