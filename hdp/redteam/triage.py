"""hdp.redteam.triage — code-aware security triage over an HDP document.

HDP's typed structure lets a reviewer localize the small fraction of a harness worth reading the
actual code of, instead of auditing the whole codebase. This module does that in three steps:

  1. ``triage_components`` — an LLM shortlists which components warrant code-level review,
     judging primarily by ``blast_radius`` and whether the component references executable code.
  2. ``resolve_source`` — for a shortlisted component, resolve its ``ref``/binding (or embedded
     ``file:``) to the actual backend source and return the contents, **read-only** — source is
     never executed.
  3. ``code_aware_redteam`` — reason over schema + resolved code together and produce Stage-2
     :class:`~hdp.redteam.llm_gen.EditCandidate` proposals (which can chain into
     :func:`~hdp.redteam.llm_gen.run_candidates`).

Coverage caveat: this tool can only see components that ``lift`` captured. If ``lift`` dropped or
misrepresented something, this tool inherits that blind spot. It flags "worth a closer human
look", not a certification of safety. See ``hdp/redteam/README.md``.
"""
from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from hdp.engine.adapters.agentic import parse_agentic_doc
from hdp.engine.core.loader import HDPDoc
from hdp.engine.core.models import Component, Ref
from hdp.redteam.llm_gen import EditCandidate

REPO = Path(__file__).resolve().parents[2]
_SOURCE_PROMPT_CAP = 8000  # per-component source chars fed into the code-aware prompt

# meta.targets value -> the adapter module whose binding-resolution assets we reuse (read-only).
_ADAPTER_MODULES = {
    "nexau": "hdp.engine.adapters.nexau",
    "mini_swe_agent": "hdp.engine.adapters.mini_swe_agent",
    "mini-swe-agent": "hdp.engine.adapters.mini_swe_agent",
    "openharness": "hdp.engine.adapters.openharness",
}


@dataclass
class TriageResult:
    component_id: str
    layer: str
    blast_radius: str | None
    shortlisted: bool
    reason: str


@dataclass
class CodeAwareReport:
    doc_id: str
    triage: list[TriageResult]
    resolved: dict[str, str]          # component_id -> resolved source (shortlisted + resolvable)
    candidates: list[EditCandidate]

    @property
    def shortlisted(self) -> list[TriageResult]:
        return [t for t in self.triage if t.shortlisted]


# --------------------------------------------------------------------------- #
#  Component / ref helpers
# --------------------------------------------------------------------------- #
def _ref_of(comp: Component) -> Ref | None:
    """The first code reference carried by a component (tools use ``implementation``, tracers
    ``ref``, middleware ``impl``; ``backend``/``definition`` also exist)."""
    return comp.ref or comp.implementation or comp.impl or comp.backend or comp.definition


def _blast(comp: Component) -> str | None:
    br = comp.blast_radius
    return br.value if br is not None else None


def _adapter_for(doc: HDPDoc) -> ModuleType | None:
    targets = doc.model.meta.targets or []
    modname = _ADAPTER_MODULES.get(str(targets[0])) if targets else None
    return importlib.import_module(modname) if modname else None


# --------------------------------------------------------------------------- #
#  1. Triage
# --------------------------------------------------------------------------- #
def _triage_prompt(doc: HDPDoc) -> str:
    rows: list[str] = []
    for layer, comp in doc.components():
        rows.append(
            f"- {comp.id} (layer={layer}, type={comp.type.value}, "
            f"blast_radius={_blast(comp)}, code_ref={_ref_of(comp) is not None})"
        )
    return (
        "You are triaging an HDP agent-harness document for security review. HDP's typed "
        "structure lets you localize the small fraction of components worth reading the actual "
        "code of, instead of auditing the whole harness. Shortlist ONLY components that warrant "
        "code-level review, judging PRIMARILY by blast_radius (higher = more dangerous) and "
        "whether the component references executable code (code_ref). Governance tier is a "
        "secondary signal — a read_only component can still be dangerous, an editable one "
        "harmless. It is fine to shortlist ZERO components if the harness is genuinely low-risk.\n\n"
        f"## Components\n{chr(10).join(rows)}\n\n"
        'Return ONLY a JSON object {"triage": [{"component_id": ..., "shortlisted": true|false, '
        '"reason": "..."}]} with one entry per component. Reply with ONLY the JSON object — no '
        "markdown fences, no commentary."
    )


def triage_components(doc: HDPDoc, llm: Callable[[str], str]) -> list[TriageResult]:
    """Ask the LLM to shortlist components worth code-level review. Returns one result per
    component (shortlisted True/False)."""
    obj = parse_agentic_doc(llm(_triage_prompt(doc)))
    entries = obj.get("triage")
    if not isinstance(entries, list):
        raise ValueError("triage: LLM reply had no 'triage' array")
    verdict = {
        str(e["component_id"]): (bool(e.get("shortlisted", False)), str(e.get("reason", "")))
        for e in entries if isinstance(e, dict) and "component_id" in e
    }
    results: list[TriageResult] = []
    for layer, comp in doc.components():
        shortlisted, reason = verdict.get(comp.id, (False, "not assessed by triage"))
        results.append(TriageResult(comp.id, layer, _blast(comp), shortlisted, reason))
    return results


# --------------------------------------------------------------------------- #
#  2. Source resolution (read-only; source is never executed)
# --------------------------------------------------------------------------- #
def _resolve_dotted(dotted: str) -> str | None:
    """Best-effort dotted-path -> file resolution against the repo's source trees. Returns None
    for bare tokens (e.g. ``e2b``) or when the source tree is absent."""
    module_path = dotted.split(":", 1)[0]
    if "." not in module_path:
        return None  # bare token has no mechanical mapping
    top = module_path.split(".", 1)[0]
    rel = module_path.replace(".", "/")
    roots: list[Path] = []
    if top == "nexau":
        roots += sorted(REPO.glob(".code_sources/nexau@*"))  # gitignored; best-effort
    elif top == "tools":
        roots.append(REPO / "agents" / "code_agent_simple")
    for base in roots:
        for cand in (base / f"{rel}.py", base / rel / "__init__.py"):
            if cand.is_file():
                return cand.read_text(encoding="utf-8")
    return None


def _resolve_binding(doc: HDPDoc, dotted: str) -> str | None:
    # Reuse the target adapter's binding->asset map (checked-in, deterministic) when it has one,
    # otherwise fall back to dotted-path resolution against the repo source trees.
    adapter = _adapter_for(doc)
    if adapter is not None:
        assets: dict[str, list[str]] = getattr(adapter, "_BINDING_ASSETS", {})
        root: Path | None = getattr(adapter, "_ASSETS", None)
        rels = assets.get(dotted)
        if rels and root is not None:
            parts = [
                f"# ---- {rel} ----\n{(root / rel).read_text(encoding='utf-8')}"
                for rel in rels if (root / rel).is_file()
            ]
            if parts:
                return "\n\n".join(parts)
    return _resolve_dotted(dotted)


def resolve_source(doc: HDPDoc, component_id: str) -> str | None:
    """Resolve a component's actual backend source (read-only). Returns None when no resolvable
    source exists (e.g. a name-only tool, or a builtin binding with no user-authored code).

    Executable code (an ``implementation``/``ref`` binding) is preferred over an embedded
    descriptor ``file:`` — for a security review the implementation is what matters. Components
    with only a ``file:`` (e.g. context system_rules) resolve to that embedded content."""
    comp = doc.component(component_id)
    if comp is None:
        return None
    ref = _ref_of(comp)
    if ref is not None:
        dotted = ref.import_ or ref.binding
        if dotted:
            src = _resolve_binding(doc, dotted)
            if src is not None:
                return src
    if comp.file:  # embedded artifact (context content, or a tool descriptor), relative to .hdp
        try:
            return doc.read_embedded(comp)
        except OSError:
            return None
    return None


# --------------------------------------------------------------------------- #
#  3. Code-aware red-team
# --------------------------------------------------------------------------- #
def _code_aware_prompt(doc: HDPDoc, shortlisted: list[TriageResult],
                       resolved: dict[str, str]) -> str:
    from hdp.redteam.llm_gen import build_prompt  # governance + rule context, reused
    blocks: list[str] = []
    for t in shortlisted:
        src = resolved.get(t.component_id)
        body = (src[:_SOURCE_PROMPT_CAP] + "\n...[truncated]" if src and len(src) > _SOURCE_PROMPT_CAP
                else src) if src is not None else "(no resolvable source — schema only)"
        blocks.append(
            f"### {t.component_id} (layer={t.layer}, blast_radius={t.blast_radius})\n"
            f"triage reason: {t.reason}\n```\n{body}\n```"
        )
    return (
        build_prompt(doc, n=len(shortlisted) or 1)
        + "\n\n## Shortlisted components — actual source for code-level review\n"
        + ("\n\n".join(blocks) or "(triage shortlisted no components)")
        + "\n\nUsing the schema policy above AND the source shown here, propose EditCandidate "
        "objects (same JSON shape as before) that a code-level review reveals as risky. Return "
        'ONLY {"candidates": [ ... ]}.'
    )


def code_aware_redteam(doc: HDPDoc, llm: Callable[[str], str]) -> CodeAwareReport:
    """Triage the document, resolve source for the shortlisted components, and reason over schema
    + code together to produce EditCandidate proposals. Two LLM calls: triage, then generation."""
    triage = triage_components(doc, llm)
    shortlisted = [t for t in triage if t.shortlisted]
    resolved = {t.component_id: src for t in shortlisted
                if (src := resolve_source(doc, t.component_id)) is not None}
    obj = parse_agentic_doc(llm(_code_aware_prompt(doc, shortlisted, resolved)))
    raw = obj.get("candidates")
    if not isinstance(raw, list):
        raise ValueError("code-aware red-team: LLM reply had no 'candidates' array")
    candidates = [EditCandidate.from_dict(c) for c in raw]
    return CodeAwareReport(doc.model.meta.id, triage, resolved, candidates)
