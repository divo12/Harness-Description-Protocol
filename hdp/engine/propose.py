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
from pathlib import Path
from typing import Callable

from hdp.engine.core.loader import HDPDoc

SKILL = "hdp-evolution-guide"

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


def _live_runner(doc: HDPDoc, query: str, iteration: int) -> str:
    """The live (spend-gated) launch of the retargeted evolve_agent. Verified only in a live
    campaign. Implementers: set EVOLVE_WORK_DIR to the run dir, register the `hdp-evolution-guide`
    skill on the evolve_agent (in place of `nexau-evolution-guide`), set the agent's working
    directory / workspace_path to `doc.path`, and call `agent.run(message=query, ...)` — mirroring
    evolve.run_evolve_agent. Pass this in via `EvolveAgentProposer(agent_runner=...)`."""
    raise NotImplementedError(
        "live evolve_agent launch is the spend-gated seam; inject agent_runner= for a campaign "
        "(see this function's docstring + evolve.run_evolve_agent)."
    )


class EvolveAgentProposer:
    """Loop ``Proposer``: build the query, run the (injected) agent, read back its manifest."""

    def __init__(self, agent_runner: AgentRunner = _live_runner):
        self.run_agent = agent_runner

    def __call__(self, doc: HDPDoc, evidence: dict, iteration: int) -> dict:
        query = build_query(doc, evidence, iteration)
        self.run_agent(doc, query, iteration)
        return read_manifest(doc, iteration)
