"""Phase 1 acceptance (core): typed load, round-trip save, component diff."""
import shutil
from pathlib import Path

from hdp.engine.core.differ import diff_docs
from hdp.engine.core.loader import load, save

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "hdp" / "examples" / "code-agent-simple.hdp"


def test_load_validates_and_indexes():
    doc = load(EXAMPLE)
    assert doc.model.meta.id == "code-agent-simple"
    assert doc.model.meta.version == "1.0.0"
    ids = [c.id for _layer, c in doc.components()]
    # unique + expected anchors present
    assert len(ids) == len(set(ids))
    assert {"run-shell", "system-rules-core", "main-loop", "in-memory-tracer"} <= set(ids)
    assert doc.component("run-shell").implementation.binding == "tools.shell_tools:run_shell_command"


def test_round_trip_preserves_content(tmp_path):
    doc = load(EXAMPLE)
    out = tmp_path / "rt.hdp"
    out.mkdir()
    save(doc, out)
    written = (out / "hdp.yaml").read_text(encoding="utf-8")

    # Semantics preserved exactly (the typed model is unchanged after a round-trip).
    assert load(out).model == doc.model
    # Comments and key order are preserved (ruamel round-trip), so diffs stay reviewable.
    assert written.startswith("# The AHE seed harness")
    assert written.index("meta:") < written.index("layers:") < written.index("governance:")
    # Block-sequence indentation matches the source style (4-space items under a key).
    assert "  execution:\n    - id: e2b-sandbox" in written


def test_differ_detects_field_and_embedded_changes(tmp_path):
    old = load(EXAMPLE)

    mod = tmp_path / "mod.hdp"
    shutil.copytree(EXAMPLE, mod)
    # field change: max_iterations on the loop component
    hdp_yaml = (mod / "hdp.yaml").read_text(encoding="utf-8").replace(
        "max_iterations: 300", "max_iterations: 250"
    )
    (mod / "hdp.yaml").write_text(hdp_yaml, encoding="utf-8")
    # embedded change: append a line to the system rules
    sr = mod / "context" / "system-rules.md"
    sr.write_text(sr.read_text(encoding="utf-8") + "\nextra rule\n", encoding="utf-8")

    new = load(mod)
    diff = diff_docs(old, new)
    assert not diff.is_empty

    by_id = {d.component_id: d for d in diff.components}
    assert by_id["main-loop"].change == "modified"
    assert "max_iterations" in by_id["main-loop"].fields_changed
    assert by_id["system-rules-core"].change == "modified"
    assert by_id["system-rules-core"].embedded_changed is True


def test_differ_empty_for_identical_docs():
    assert diff_docs(load(EXAMPLE), load(EXAMPLE)).is_empty
