"""Phase 3 acceptance (guard): the red-team suite.

Every disallowed edit must leave the .hdp tree byte-for-byte unchanged. In ``enforce`` every
violation is a final structured Deny. In ``review`` the confinement CORE still hard-blocks, while
a SOFT-only edit is quarantined (tree untouched, staging kept) and only applies after a logged
human approval — re-run through the SAME PEP — emitting a first-class audit event.
"""
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from hdp.engine import guard
from hdp.engine.core.loader import load
from hdp.engine.guard import Edit, Tier, apply, approve, decide, dry_decide

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "hdp" / "examples" / "code-agent-simple.hdp"

# An execution-layer (read_only) component, cpus bumped 1 -> 2: a well-formed but un-permitted edit.
SANDBOX_CPUS2 = {
    "id": "e2b-sandbox", "type": "sandbox", "isolation": "microvm",
    "backend": {"kind": "builtin", "binding": "e2b"},
    "cpus": 2, "memory_mb": 2048, "timeout_sec": 3600,
}


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #
def _doc(tmp_path: Path, name: str = "code-agent-simple.hdp") -> Path:
    dst = tmp_path / name
    shutil.copytree(EXAMPLE, dst)
    return dst


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode())
            h.update(b"\0")
            h.update(p.read_bytes())
            h.update(b"\0")
    return h.hexdigest()


def _manifest(*component_ids: str) -> dict:
    return {
        "hdp": "0.1", "iteration": 1,
        "changes": [{"component_id": c, "change_id": f"chg-{c}"} for c in component_ids],
    }


def _doctor(dst: Path, mutate) -> Path:
    """Copy the example into *dst* and mutate hdp.yaml in place (round-trip preserved)."""
    shutil.copytree(EXAMPLE, dst)
    y = YAML()
    y.preserve_quotes = True
    raw = y.load((dst / "hdp.yaml").read_text(encoding="utf-8"))
    mutate(raw)
    with (dst / "hdp.yaml").open("w", encoding="utf-8") as f:
        y.dump(raw, f)
    return dst


# decide() takes (edit, old, new); for whole-document confinement edits (governance/meta) no
# component changes, so this placeholder edit only carries a covering manifest + valid operator.
_DUMMY = Edit(operator="update", layer="governance", component_id="-",
              manifest=_manifest("-"))


# --------------------------------------------------------------------------- #
#  the allow path (the gateway must let legitimate edits through)
# --------------------------------------------------------------------------- #
def test_allow_editable_edit_applies(tmp_path):
    d = _doc(tmp_path)
    edit = Edit(operator="update", layer="context", component_id="long-term-memory",
                embedded={"context/memory/long-term.md": "# updated by evolve agent\n"},
                manifest=_manifest("long-term-memory"))
    res = apply(edit, load(d))
    assert res.applied and res.status == "allow", res.decision.reasons
    assert (d / "context" / "memory" / "long-term.md").read_text() == "# updated by evolve agent\n"
    # the apply is recorded as a first-class audit event
    ev = json.loads((d / "evolution" / "audit.jsonl").read_text().splitlines()[-1])
    assert ev["decision"] == "allow" and ev["change_id"] == "chg-long-term-memory"


def test_dry_decide_allows_without_touching_tree(tmp_path):
    d = _doc(tmp_path)
    edit = Edit(operator="update", layer="lifecycle", component_id="main-loop",
                component={"id": "main-loop", "type": "loop", "max_iterations": 250,
                           "tool_call_mode": "openai"},
                manifest=_manifest("main-loop"))
    before = _tree_hash(d)
    dec = dry_decide(edit, load(d))
    assert dec.status == "allow", dec.reasons
    assert _tree_hash(d) == before  # decide-only never writes


# --------------------------------------------------------------------------- #
#  CORE denials (non-overridable) — tree must stay byte-identical
# --------------------------------------------------------------------------- #
def test_core_protected_component_denied(tmp_path):
    # The shipped example no longer protects system-rules-core (it is editable now), so doctor a
    # doc that does — the protected tier is what's under test, not the example's policy choice.
    d = _doctor(tmp_path / "code-agent-simple.hdp",
                lambda r: r["governance"]["evolution"]["protected"].append("system-rules-core"))
    before = _tree_hash(d)
    edit = Edit(operator="update", layer="context", component_id="system-rules-core",
                embedded={"context/system-rules.md": "tampered\n"},
                manifest=_manifest("system-rules-core"))
    res = apply(edit, load(d))
    assert not res.applied and res.status == "deny"
    assert any(v.tier == Tier.CORE and v.rule == "protected" for v in res.decision.violations)
    assert _tree_hash(d) == before


def test_core_secret_injection_denied(tmp_path):
    d = _doc(tmp_path)
    before = _tree_hash(d)
    edit = Edit(operator="update", layer="context", component_id="long-term-memory",
                embedded={"context/memory/long-term.md": "token = 'sk_0123456789abcdef'\n"},
                manifest=_manifest("long-term-memory"))
    res = apply(edit, load(d))
    assert not res.applied and res.status == "deny"
    assert any(v.tier == Tier.CORE and v.rule == "secret" for v in res.decision.violations)
    assert _tree_hash(d) == before


@pytest.mark.parametrize("mutate,rule", [
    (lambda r: r["governance"]["evolution"]["editable"].append("observability"), "confinement"),
    (lambda r: r["governance"]["evolution"]["read_only"].remove("execution"), "confinement"),
    (lambda r: r["governance"].__setitem__("blast_radius", "external"), "confinement"),
    (lambda r: r["meta"].__setitem__("base_model", "gpt-4o"), "model_config"),
    (lambda r: r["layers"]["tooling"][0].__setitem__("blast_radius", "external"), "blast_radius"),
], ids=["widen-editable", "narrow-readonly", "widen-blast", "model-config", "over-ceiling"])
def test_core_confinement_denied_in_both_modes(tmp_path, mutate, rule):
    old = load(EXAMPLE)
    new = load(_doctor(tmp_path / "doctored.hdp", mutate))
    dec = decide(_DUMMY, old, new, mode="enforce")
    assert dec.status == "deny"
    assert any(v.tier == Tier.CORE and v.rule == rule for v in dec.violations), dec.reasons
    # confinement core is never overridable: still denied in review mode
    assert decide(_DUMMY, old, new, mode="review").status == "deny"


# --------------------------------------------------------------------------- #
#  STRUCTURAL denial — a malformed staged document can never apply
# --------------------------------------------------------------------------- #
def test_structural_missing_file_denied(tmp_path):
    d = _doc(tmp_path)
    before = _tree_hash(d)
    edit = Edit(operator="add", layer="context", component_id="ghost-doc",
                component={"id": "ghost-doc", "type": "memory", "scope": "session",
                           "file": "./context/ghost.md"},
                manifest=_manifest("ghost-doc"))
    res = apply(edit, load(d))
    assert not res.applied and res.status == "deny"
    assert any(v.tier == Tier.STRUCTURAL for v in res.decision.violations)
    assert _tree_hash(d) == before


# --------------------------------------------------------------------------- #
#  SOFT denials — final in enforce, leave the tree unchanged
# --------------------------------------------------------------------------- #
def test_soft_readonly_layer_denied_in_enforce(tmp_path):
    d = _doc(tmp_path)
    before = _tree_hash(d)
    edit = Edit(operator="update", layer="execution", component_id="e2b-sandbox",
                component=SANDBOX_CPUS2, manifest=_manifest("e2b-sandbox"))
    res = apply(edit, load(d), mode="enforce")
    assert not res.applied and res.status == "deny"
    assert any(v.tier == Tier.SOFT and v.rule == "editability" for v in res.decision.violations)
    assert _tree_hash(d) == before


def test_soft_bad_operator_denied(tmp_path):
    d = _doc(tmp_path)
    before = _tree_hash(d)
    edit = Edit(operator="frobnicate", layer="context", component_id="long-term-memory",
                embedded={"context/memory/long-term.md": "x\n"},
                manifest=_manifest("long-term-memory"))
    res = apply(edit, load(d))
    assert not res.applied and res.status == "deny"
    assert any(v.tier == Tier.SOFT and v.rule == "operator" for v in res.decision.violations)
    assert _tree_hash(d) == before


def test_soft_missing_manifest_denied(tmp_path):
    d = _doc(tmp_path)
    before = _tree_hash(d)
    edit = Edit(operator="update", layer="context", component_id="long-term-memory",
                embedded={"context/memory/long-term.md": "x\n"})  # no manifest
    res = apply(edit, load(d))
    assert not res.applied and res.status == "deny"
    assert any(v.tier == Tier.SOFT and v.rule == "manifest" for v in res.decision.violations)
    assert _tree_hash(d) == before


# --------------------------------------------------------------------------- #
#  review mode — quarantine then logged approval through the SAME PEP
# --------------------------------------------------------------------------- #
def test_review_quarantines_then_approve_applies(tmp_path):
    d = _doc(tmp_path)
    before = _tree_hash(d)
    edit = Edit(operator="update", layer="execution", component_id="e2b-sandbox",
                component=SANDBOX_CPUS2, manifest=_manifest("e2b-sandbox"))

    res = apply(edit, load(d), mode="review")
    assert res.status == "review" and not res.applied
    assert res.review_dir is not None and res.review_dir.exists()
    assert _tree_hash(d) == before  # quarantined: working tree untouched

    res2 = approve(load(d), edit, res.review_dir, reviewer="lead-eng", reason="signed off")
    assert res2.applied and res2.status == "allow"
    assert load(d).component("e2b-sandbox").cpus == 2  # the override is now live

    ev = json.loads((d / "evolution" / "audit.jsonl").read_text().splitlines()[-1])
    assert ev["decision"] == "approved" and ev["reviewer"] == "lead-eng"
    assert ev["reason"] == "signed off" and ev["change_id"] == "chg-e2b-sandbox"


def test_review_core_violation_still_hardblocks(tmp_path):
    d = _doctor(tmp_path / "code-agent-simple.hdp",
                lambda r: r["governance"]["evolution"]["protected"].append("system-rules-core"))
    before = _tree_hash(d)
    edit = Edit(operator="update", layer="context", component_id="system-rules-core",
                embedded={"context/system-rules.md": "tampered\n"},
                manifest=_manifest("system-rules-core"))
    res = apply(edit, load(d), mode="review")
    assert res.status == "deny" and not res.applied  # protected is never quarantined
    assert res.review_dir is None
    assert _tree_hash(d) == before


# --------------------------------------------------------------------------- #
#  guard.govern — selectable engine (application study: reconcile vs atomic)
# --------------------------------------------------------------------------- #
def _old_new(tmp_path, mutate):
    """Two on-disk docs: pristine OLD and a NEW whose hdp.yaml is mutated by *mutate*."""
    old_dir = tmp_path / "old.hdp"
    shutil.copytree(EXAMPLE, old_dir)
    new_dir = tmp_path / "new.hdp"
    shutil.copytree(EXAMPLE, new_dir)
    y = YAML()
    y.preserve_quotes = True
    raw = y.load((new_dir / "hdp.yaml").read_text(encoding="utf-8"))
    mutate(raw)
    with (new_dir / "hdp.yaml").open("w", encoding="utf-8") as f:
        y.dump(raw, f)
    return load(old_dir), load(new_dir)


def _ctx(raw, cid):
    return next(c for c in raw["layers"]["context"] if c["id"] == cid)


def _ver(raw):
    return raw["layers"]["verification"][0]


def _gov_manifest(*entries):  # entries: (component_id, layer, operator)
    return {"hdp": "0.1", "iteration": 1, "changes": [
        {"component_id": c, "layer": lyr, "operator": o, "change_id": f"chg-{c}"}
        for c, lyr, o in entries]}


def _mixed(raw):
    _ctx(raw, "long-term-memory")["description"] = "allowed note"  # editable layer
    _ver(raw)["description"] = "illegal tamper"                    # read_only layer


def test_govern_reconcile_rolls_back_only_denied(tmp_path):
    old, new = _old_new(tmp_path, _mixed)
    manifest = _gov_manifest(("long-term-memory", "context", "update"),
                             ("tb2-verifier", "verification", "update"))
    reconciled, report = guard.govern(old, new, manifest, engine="reconcile")
    assert not report.ok and len(report.denied) == 1
    # the editable edit survives; only the read_only delta is reverted
    assert reconciled.component("long-term-memory").description == "allowed note"
    assert reconciled.component("tb2-verifier").description == \
        old.component("tb2-verifier").description
    assert reconciled.path == new.path


def test_govern_atomic_keeps_clean_edit(tmp_path):
    old, new = _old_new(tmp_path, lambda raw: _ctx(raw, "long-term-memory").__setitem__(
        "description", "clean note"))
    manifest = _gov_manifest(("long-term-memory", "context", "update"))
    reconciled, report = guard.govern(old, new, manifest, engine="atomic")
    assert report.ok and not report.denied
    assert reconciled.component("long-term-memory").description == "clean note"
    assert reconciled.path == new.path


def test_govern_atomic_reverts_whole_edit_on_any_violation(tmp_path):
    old, new = _old_new(tmp_path, _mixed)
    manifest = _gov_manifest(("long-term-memory", "context", "update"),
                             ("tb2-verifier", "verification", "update"))
    reconciled, report = guard.govern(old, new, manifest, engine="atomic")
    assert not report.ok and report.denied
    # all-or-nothing: even the otherwise-allowed edit is reverted (contrast with reconcile)
    assert reconciled.component("long-term-memory").description == \
        old.component("long-term-memory").description
    assert reconciled.component("tb2-verifier").description == \
        old.component("tb2-verifier").description
    assert reconciled.path == new.path
