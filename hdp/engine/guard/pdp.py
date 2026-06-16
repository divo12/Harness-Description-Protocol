"""hdp.engine.guard.pdp — the Policy Decision Point (PDP): decide(edit) -> Allow|Deny|Review.

Reuses ``hdp_validate.py``'s rules (the policy) and adds SPEC §5.2 confinement on top. Rules
are **tiered** (per the project's guard decision):

  * CORE        — the confinement core: monotonic-confinement (no widening of governance /
                  permission surface), immutable model config, protected components, no new
                  literal secrets, no component exceeding the blast-radius ceiling. NEVER
                  overridable, in any mode.
  * STRUCTURAL  — full ``hdp_validate.py`` errors (duplicate ids, missing files, schema, …).
                  Always blocks: a malformed document cannot be applied, review or not.
  * SOFT        — operator set, editability targeting (editable/read_only), manifest-before-edit.
                  Denied in ``enforce`` mode; routed to human review in ``review`` mode.

In ``enforce`` (default, and what bench runs): any violation -> Deny.
In ``review``: CORE/STRUCTURAL -> Deny; SOFT-only -> Review (staged, awaiting approval).
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from hdp.engine.core.differ import diff_docs
from hdp.engine.core.loader import HDPDoc

OPERATORS = {"add", "update", "remove", "narrow", "gate"}
_VALIDATOR_PATH = Path(__file__).resolve().parents[2] / "validator" / "hdp_validate.py"
_v = None


def _validator():
    """Load hdp/validator/hdp_validate.py once (it is a script, not a package module)."""
    global _v
    if _v is None:
        spec = importlib.util.spec_from_file_location("hdp_validate_guard", _VALIDATOR_PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        _v = mod
    return _v


class Tier(StrEnum):
    CORE = "core"
    STRUCTURAL = "structural"
    SOFT = "soft"


@dataclass
class Violation:
    tier: Tier
    rule: str
    message: str

    def __str__(self) -> str:
        return f"[{self.tier.value}] {self.rule}: {self.message}"


@dataclass
class Decision:
    status: str  # "allow" | "deny" | "review"
    violations: list[Violation] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.status == "allow"

    @property
    def reasons(self) -> list[str]:
        return [str(v) for v in self.violations]


@dataclass
class Edit:
    """A proposed change to an HDP document (materialized into a staging dir by the PEP)."""

    operator: str                                   # add|update|remove|narrow|gate
    layer: str
    component_id: str
    component: dict | None = None                # new component mapping (add/update/narrow/gate)
    embedded: dict[str, str] | None = None       # rel-path -> new file content
    remove_files: list[str] | None = None        # rel-paths to delete (remove)
    manifest: dict | None = None                 # canonical change-manifest (manifest-before-edit)


# --------------------------------------------------------------------------- #
#  the decision
# --------------------------------------------------------------------------- #
def decide(edit: Edit, old_doc: HDPDoc, new_doc: HDPDoc, mode: str = "enforce") -> Decision:
    """Decide whether *edit* (turning *old_doc* into *new_doc*) is permitted under *mode*."""
    V: list[Violation] = []
    diff = diff_docs(old_doc, new_doc)

    # STRUCTURAL: reuse the full reference validator on the staged document.
    errors, _warnings = _validator().validate(new_doc.path)
    V += [Violation(Tier.STRUCTURAL, "hdp_validate", e) for e in errors]

    # SOFT S1: operator ∈ closed set.
    if edit.operator not in OPERATORS:
        V.append(Violation(Tier.SOFT, "operator",
                           f"'{edit.operator}' not in {sorted(OPERATORS)}"))

    # CORE C2: immutable model config (model identity is not an editable HDP component).
    if old_doc.model.meta.base_model != new_doc.model.meta.base_model:
        V.append(Violation(Tier.CORE, "model_config",
                           f"meta.base_model changed "
                           f"{old_doc.model.meta.base_model!r} -> {new_doc.model.meta.base_model!r}"))

    # CORE C1: monotonic confinement on the governance block.
    V += _confinement(old_doc, new_doc)

    # per-changed-component rules.
    protected = _evo_set(old_doc, "protected")
    editable = _evo_set(new_doc, "editable")
    read_only = _evo_set(new_doc, "read_only")
    changed_ids: list[str] = []
    for d in diff.components:
        changed_ids.append(d.component_id)
        # CORE C3: protected components must not be modified or removed.
        if d.component_id in protected and d.change in ("modified", "removed"):
            V.append(Violation(Tier.CORE, "protected",
                               f"component '{d.component_id}' is protected ({d.change})"))
        # SOFT S2: target must be editable and not read_only.
        permitted = ((d.layer in editable or d.component_id in editable)
                     and not (d.layer in read_only or d.component_id in read_only))
        if not permitted:
            V.append(Violation(Tier.SOFT, "editability",
                               f"{d.change} of '{d.component_id}' in layer '{d.layer}' "
                               "not permitted by governance.evolution"))

    # CORE C5: no component exceeds the blast-radius ceiling (permission widening).
    V += _blast_ceiling(new_doc)

    # CORE C4: no new literal secrets in embedded content.
    V += _secret_scan(new_doc)

    # SOFT S4: manifest-before-edit.
    if not _manifest_covers(edit, changed_ids):
        V.append(Violation(Tier.SOFT, "manifest",
                           "no change-manifest entry covering the edited component(s)"))

    return _resolve(V, mode)


def _resolve(violations: list[Violation], mode: str) -> Decision:
    if not violations:
        return Decision("allow", violations)
    hard = any(v.tier in (Tier.CORE, Tier.STRUCTURAL) for v in violations)
    if hard:
        return Decision("deny", violations)
    return Decision("review" if mode == "review" else "deny", violations)


# --------------------------------------------------------------------------- #
#  rule helpers
# --------------------------------------------------------------------------- #
def _evo(doc: HDPDoc):
    gov = doc.model.governance
    return gov.evolution if gov else None


def _evo_set(doc: HDPDoc, attr: str) -> set[str]:
    evo = _evo(doc)
    return set((getattr(evo, attr) if evo else None) or [])


def _confinement(old: HDPDoc, new: HDPDoc) -> list[Violation]:
    out: list[Violation] = []
    og, ng = old.model.governance, new.model.governance
    if og is not None and ng is None:
        return [Violation(Tier.CORE, "confinement", "governance block removed")]
    if og is None or ng is None:
        return out

    order = _validator().BLAST_ORDER
    ob = og.blast_radius.value if og.blast_radius else None
    nb = ng.blast_radius.value if ng.blast_radius else None
    if ob and nb and order.index(nb) > order.index(ob):
        out.append(Violation(Tier.CORE, "confinement",
                             f"governance.blast_radius widened {ob} -> {nb}"))

    def es(gov, attr) -> set[str]:
        evo = gov.evolution
        return set((getattr(evo, attr) if evo else None) or [])

    widened_editable = es(ng, "editable") - es(og, "editable")
    narrowed_readonly = es(og, "read_only") - es(ng, "read_only")
    narrowed_protected = es(og, "protected") - es(ng, "protected")
    if widened_editable:
        out.append(Violation(Tier.CORE, "confinement",
                             f"editable widened: +{sorted(widened_editable)}"))
    if narrowed_readonly:
        out.append(Violation(Tier.CORE, "confinement",
                             f"read_only narrowed: -{sorted(narrowed_readonly)}"))
    if narrowed_protected:
        out.append(Violation(Tier.CORE, "confinement",
                             f"protected narrowed: -{sorted(narrowed_protected)}"))
    return out


def _blast_ceiling(doc: HDPDoc) -> list[Violation]:
    gov = doc.model.governance
    ceiling = gov.blast_radius.value if gov and gov.blast_radius else None
    if not ceiling:
        return []
    order = _validator().BLAST_ORDER
    out: list[Violation] = []
    for _layer, comp in doc.components():
        br = comp.blast_radius.value if comp.blast_radius else None
        if br and order.index(br) > order.index(ceiling):
            out.append(Violation(Tier.CORE, "blast_radius",
                                 f"component '{comp.id}' blast_radius '{br}' "
                                 f"exceeds ceiling '{ceiling}'"))
    return out


def _secret_scan(doc: HDPDoc) -> list[Violation]:
    out: list[Violation] = []
    for _layer, comp in doc.components():
        if comp.file:
            p = doc.path / comp.file
            if p.exists():
                text = p.read_text(encoding="utf-8", errors="ignore")
                for pat in _validator().SECRET_PATTERNS:
                    if pat.search(text):
                        out.append(Violation(Tier.CORE, "secret",
                                             f"literal secret in {comp.file} (use ${{env.VAR}})"))
                        break
    return out


def _manifest_covers(edit: Edit, changed_ids: list[str]) -> bool:
    if not isinstance(edit.manifest, dict):
        return False
    entries = edit.manifest.get("changes", [])
    covered = {c.get("component_id") for c in entries}
    if not changed_ids:
        return bool(entries)
    return any(cid in covered for cid in changed_ids)
