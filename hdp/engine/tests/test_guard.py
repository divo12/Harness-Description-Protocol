"""Tests for hdp.engine.guard — the governed-edit PDP/PEP."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

import pytest

from hdp.engine import guard
from hdp.engine.core.loader import HDPDoc, load, save

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "hdp" / "examples" / "code-agent-simple.hdp"


@pytest.fixture()
def old(tmp_path) -> HDPDoc:
    dst = tmp_path / "old.hdp"
    shutil.copytree(EXAMPLE, dst)
    return load(dst)


def _edited(tmp_path, mutate: Callable[[object], None]) -> HDPDoc:
    """Copy the example, apply *mutate* to its ruamel raw, save+reload → the 'new' doc."""
    dst = tmp_path / "new.hdp"
    if not dst.exists():
        shutil.copytree(EXAMPLE, dst)
    doc = load(dst)
    mutate(doc.raw)
    save(doc)
    return load(dst)


def _protected_old(tmp_path) -> HDPDoc:
    """A baseline doc that protects system-rules-core. The shipped example no longer does (it is
    editable), so the protected-tier tests doctor it back in — the tier is what's under test."""
    dst = tmp_path / "old.hdp"
    shutil.copytree(EXAMPLE, dst)
    doc = load(dst)
    doc.raw["governance"]["evolution"]["protected"].append("system-rules-core")
    save(doc)
    return load(dst)


def _ctx(raw, cid):
    return next(c for c in raw["layers"]["context"] if c["id"] == cid)


def _ver(raw):
    return raw["layers"]["verification"][0]


def m(cid, op):  # one-entry change manifest
    return {"changes": [{"component_id": cid, "operator": op}]}


def test_allowed_edit_in_editable_layer(old, tmp_path):
    new = _edited(tmp_path, lambda raw: _ctx(raw, "long-term-memory").__setitem__(
        "description", "now with a note"))
    rep = guard.evaluate(old, new, m("long-term-memory", "update"))
    assert rep.ok
    assert rep.allowed and not rep.denied


def test_denied_read_only_layer(old, tmp_path):
    new = _edited(tmp_path, lambda raw: _ver(raw).__setitem__("description", "tampered"))
    rep = guard.evaluate(old, new, m("tb2-verifier", "update"))
    assert not rep.ok
    assert rep.denied[0].component_id == "tb2-verifier"
    assert "read-only" in rep.denied[0].reason


def test_denied_protected_remove(tmp_path):
    old = _protected_old(tmp_path)
    def drop_protected(raw):
        raw["governance"]["evolution"]["protected"].append("system-rules-core")
        ctx = raw["layers"]["context"]
        ctx[:] = [c for c in ctx if c["id"] != "system-rules-core"]
    new = _edited(tmp_path, drop_protected)
    rep = guard.evaluate(old, new, m("system-rules-core", "remove"))
    assert not rep.ok
    assert rep.denied[0].reason == "protected component"


def test_denied_undeclared_edit(old, tmp_path):
    new = _edited(tmp_path, lambda raw: _ctx(raw, "long-term-memory").__setitem__(
        "description", "sneaky"))
    rep = guard.evaluate(old, new, manifest=None)  # no manifest → manifest-before-edit fails
    assert not rep.ok
    assert "undeclared" in rep.denied[0].reason


def test_denied_operator_mismatch(old, tmp_path):
    new = _edited(tmp_path, lambda raw: _ctx(raw, "long-term-memory").__setitem__(
        "description", "x"))
    rep = guard.evaluate(old, new, m("long-term-memory", "add"))  # 'add' vs a 'modified' change
    assert not rep.ok
    assert "inconsistent" in rep.denied[0].reason


def test_governance_self_edit_denied(old, tmp_path):
    new = _edited(tmp_path, lambda raw: raw["governance"].__setitem__("blast_radius", "external"))
    rep = guard.evaluate(old, new, manifest=None)
    assert rep.governance_self_edit_denied
    assert not rep.ok


def test_enforce_rolls_back_denied_keeps_allowed(old, tmp_path):
    def mutate(raw):
        _ctx(raw, "long-term-memory")["description"] = "allowed note"   # editable
        _ver(raw)["description"] = "illegal tamper"                      # read-only
    new = _edited(tmp_path, mutate)
    manifest = {"changes": [
        {"component_id": "long-term-memory", "operator": "update"},
        {"component_id": "tb2-verifier", "operator": "update"},
    ]}
    reconciled, rep = guard.enforce(old, new, manifest)
    assert not rep.ok and len(rep.denied) == 1

    # allowed edit survived; denied edit reverted to the old value.
    new_mem = reconciled.component("long-term-memory")
    new_ver = reconciled.component("tb2-verifier")
    old_ver = old.component("tb2-verifier")
    assert new_mem.description == "allowed note"
    assert new_ver.description == old_ver.description  # rolled back


def test_enforce_restores_embedded_file_on_denied_edit(tmp_path):
    # Regression (found by the live smoke): a denied edit whose change lives entirely in the
    # embedded FILE (here: the protected system-rules.md) must be restored, not just the manifest.
    old = _protected_old(tmp_path)
    new_dir = tmp_path / "new.hdp"
    shutil.copytree(old.path, new_dir)  # same protected baseline; then tamper the embedded file
    (new_dir / "context" / "system-rules.md").write_text("TAMPERED BY AGENT", encoding="utf-8")
    new = load(new_dir)

    reconciled, rep = guard.enforce(old, new, m("system-rules-core", "update"))

    assert not rep.ok and rep.denied[0].component_id == "system-rules-core"  # protected
    assert (reconciled.path / "context" / "system-rules.md").read_text() == \
           (old.path / "context" / "system-rules.md").read_text()           # file restored
