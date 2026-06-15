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
from datetime import datetime
from pathlib import Path
from typing import Callable

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
    lines = [
        f"# HDP evolution — iteration {iteration}",
        "",
        f"Improve the agent by editing its HDP document at `{doc.path}`. Follow the "
        f"`{SKILL}` skill exactly: pick ONE highest-leverage failure, choose the most specific "
        "operator, write the manifest entry FIRST (with a falsifiable prediction), then edit.",
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


def setup_treatment_agent(workdir: Path | str, *, src: Path = EVOLVE_AGENT_DIR) -> Path:
    """Materialize a retargeted evolve_agent under *workdir*: copy the agent, then swap its
    ``skills:`` from ``nexau-evolution-guide`` to ``hdp-evolution-guide`` so it gets the HDP
    playbook (its file tools and sandbox work_dir are env-driven, so no other change is needed).
    Returns the path to the retargeted ``evolve_agent.yaml``. $0 — no agent is launched here."""
    dest = Path(workdir) / "evolve_agent"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    cfg_path = dest / "evolve_agent.yaml"
    y = YAML()
    y.preserve_quotes = True
    raw = y.load(cfg_path.read_text())
    raw["skills"] = [f"./skills/{SKILL}"]
    with cfg_path.open("w", encoding="utf-8") as f:
        y.dump(raw, f)
    if not (dest / "skills" / SKILL / "SKILL.md").is_file():
        raise FileNotFoundError(f"{SKILL} skill missing from {dest/'skills'}")
    return cfg_path


def _nexau_agent_factory(cfg_path: Path):
    from nexau import Agent  # imported lazily so $0 paths never need nexau
    return Agent.from_yaml(config_path=str(cfg_path))


def make_live_runner(*, src: Path = EVOLVE_AGENT_DIR, agent_factory=_nexau_agent_factory,
                     context_extra: dict | None = None) -> AgentRunner:
    """Build the live (spend-gated) runner: launch the retargeted evolve_agent at the doc's
    workdir with the HDP query. ``agent_factory`` is injectable so the launch wiring is tested
    without nexau/LLM; the default constructs the real NexAU agent (the campaign spend point)."""
    def runner(doc: HDPDoc, query: str, iteration: int) -> str:
        workdir = doc.path.parent
        cfg_path = setup_treatment_agent(workdir, src=src)
        os.environ["EVOLVE_WORK_DIR"] = str(workdir)
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

    def __init__(self, agent_runner: AgentRunner | None = None):
        self.run_agent = agent_runner or make_live_runner()

    def __call__(self, doc: HDPDoc, evidence: dict, iteration: int) -> dict:
        query = build_query(doc, evidence, iteration)
        self.run_agent(doc, query, iteration)
        return read_manifest(doc, iteration)
