"""hdp.engine.core.differ — component-level diff between two HDP documents.

The unit of observability and rollback is the component (SPEC §2). This computes which
components were added / removed / modified between two :class:`HDPDoc`s, comparing both
the typed component fields *and* the embedded file content (a change can live entirely in
the embedded file while the manifest entry is unchanged). Track/attest (Phase 4) consume
this to attribute edits and reconcile verdicts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .loader import HDPDoc
from .models import Component

ChangeKind = Literal["added", "removed", "modified"]


@dataclass
class ComponentDelta:
    component_id: str
    layer: str
    change: ChangeKind
    fields_changed: list[str] = field(default_factory=list)
    embedded_changed: bool = False


@dataclass
class HDPDiff:
    components: list[ComponentDelta] = field(default_factory=list)
    meta_changed: list[str] = field(default_factory=list)
    governance_changed: bool = False

    @property
    def is_empty(self) -> bool:
        return not (self.components or self.meta_changed or self.governance_changed)

    def by_kind(self, kind: ChangeKind) -> list[ComponentDelta]:
        return [d for d in self.components if d.change == kind]


def _index(doc: HDPDoc) -> dict[str, tuple[str, Component]]:
    return {comp.id: (layer, comp) for layer, comp in doc.components()}


def _changed_fields(old: Component, new: Component) -> list[str]:
    a = old.model_dump(exclude_none=True)
    b = new.model_dump(exclude_none=True)
    keys = sorted(set(a) | set(b))
    return [k for k in keys if a.get(k) != b.get(k)]


def diff_docs(old: HDPDoc, new: HDPDoc) -> HDPDiff:
    old_idx, new_idx = _index(old), _index(new)
    diff = HDPDiff()

    for cid, (layer, comp) in new_idx.items():
        if cid not in old_idx:
            diff.components.append(ComponentDelta(cid, layer, "added"))
            continue
        old_comp = old_idx[cid][1]
        fields = _changed_fields(old_comp, comp)
        embedded_changed = old.read_embedded(old_comp) != new.read_embedded(comp)
        if fields or embedded_changed:
            diff.components.append(
                ComponentDelta(cid, layer, "modified",
                               fields_changed=fields, embedded_changed=embedded_changed)
            )

    for cid, (layer, _comp) in old_idx.items():
        if cid not in new_idx:
            diff.components.append(ComponentDelta(cid, layer, "removed"))

    # meta + governance (whole-block compares; these gate evolution policy)
    om, nm = old.model.meta.model_dump(), new.model.meta.model_dump()
    diff.meta_changed = [k for k in sorted(set(om) | set(nm)) if om.get(k) != nm.get(k)]
    og = old.model.governance.model_dump() if old.model.governance else None
    ng = new.model.governance.model_dump() if new.model.governance else None
    diff.governance_changed = og != ng

    diff.components.sort(key=lambda d: (d.layer, d.component_id))
    return diff
