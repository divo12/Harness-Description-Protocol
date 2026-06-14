"""hdp.engine.core.loader — load/save an HDP document with round-trip fidelity.

Loads ``hdp.yaml`` via ruamel (round-trip mode: preserves key order, comments, quoting →
minimal, reviewable git diffs) and validates it against the generated Pydantic model
(belt-and-suspenders over the runtime jsonschema check in hdp_validate.py). The typed
:class:`HDPDoc.model` view drives logic (gen, differ); the ruamel :attr:`HDPDoc.raw`
view is what gets written back, so edits keep the file's formatting.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from ruamel.yaml import YAML

# The generated model's root class has an auto-derived name; alias it once here so the
# rest of the engine imports a clean symbol. (Never hand-edit the generated file.)
from .models import HarnessDefinitionProtocolManifestHdpYaml as HDPManifest
from .models import Component

LAYER_ORDER = (
    "execution", "tooling", "context", "lifecycle",
    "observability", "verification", "governance",
)


def _yaml() -> YAML:
    y = YAML()  # round-trip by default
    y.preserve_quotes = True
    y.width = 4096  # don't reflow long scalars (e.g. tool descriptions)
    y.indent(mapping=2, sequence=4, offset=2)  # match the worked example's list style
    return y


@dataclass
class HDPDoc:
    """An in-memory HDP document: its directory, the round-trip YAML, and the typed model."""

    path: Path           # the <name>.hdp directory
    raw: Any             # ruamel CommentedMap — round-trip view of hdp.yaml
    model: HDPManifest   # validated typed view

    @property
    def manifest_path(self) -> Path:
        return self.path / "hdp.yaml"

    def components(self) -> Iterator[tuple[str, Component]]:
        """Yield ``(layer, component)`` across all layers, in canonical layer order."""
        layers = self.model.layers
        for layer in LAYER_ORDER:
            for comp in (getattr(layers, layer) or []):
                yield layer, comp

    def component(self, component_id: str) -> Component | None:
        for _, comp in self.components():
            if comp.id == component_id:
                return comp
        return None

    def read_embedded(self, comp: Component) -> str | None:
        """Return the byte-faithful embedded content for *comp*, or None if not embedded."""
        if comp.file:
            return (self.path / comp.file).read_text(encoding="utf-8")
        return None


def load(hdp_dir: Path | str) -> HDPDoc:
    hdp_dir = Path(hdp_dir).resolve()
    manifest_path = hdp_dir / "hdp.yaml"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"no hdp.yaml in {hdp_dir}")
    with manifest_path.open(encoding="utf-8") as f:
        raw = _yaml().load(f)
    # ruamel CommentedMap/Seq are dict/list subclasses, so the typed model validates them
    # directly; this raises pydantic.ValidationError on a malformed document.
    model = HDPManifest.model_validate(raw)
    return HDPDoc(path=hdp_dir, raw=raw, model=model)


def save(doc: HDPDoc, hdp_dir: Path | str | None = None) -> Path:
    """Write the round-trip YAML back, preserving formatting. Returns the manifest path."""
    out_dir = Path(hdp_dir).resolve() if hdp_dir is not None else doc.path
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "hdp.yaml"
    with manifest_path.open("w", encoding="utf-8") as f:
        _yaml().dump(doc.raw, f)
    return manifest_path
