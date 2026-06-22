"""The guard is backend-agnostic: it reads the HDP document diff, never backend files.

These tests lift the mini-SWE-agent seed into an HDP document and run the *unmodified*
``hdp.engine.guard`` against it, showing it produces the same CORE/STRUCTURAL denials it does for
NexAU (see ``test_guard.py``) — the generality claim the second backend is meant to demonstrate.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from hdp.engine import guard
from hdp.engine.core.loader import HDPDoc, load, save
from hdp.engine.lift import lift

REPO = Path(__file__).resolve().parents[3]
SEED = REPO / "agents" / "mini_swe_agent"


@pytest.fixture()
def old(tmp_path) -> HDPDoc:
    return lift(SEED, tmp_path / "old.hdp", target="mini-swe-agent")


def _edited(tmp_path, mutate: Callable[[object], None]) -> HDPDoc:
    """Copy the lifted doc, apply *mutate* to its ruamel raw, save+reload → the 'new' doc."""
    dst = tmp_path / "new.hdp"
    if not dst.exists():
        lift(SEED, dst, target="mini-swe-agent")
    doc = load(dst)
    mutate(doc.raw)
    save(doc)
    return load(dst)


def _ctx(raw, cid):
    return next(c for c in raw["layers"]["context"] if c["id"] == cid)


def m(cid, op):  # one-entry change manifest
    return {"changes": [{"component_id": cid, "operator": op}]}


def test_allowed_edit_in_editable_layer(old, tmp_path):
    new = _edited(tmp_path, lambda raw: _ctx(raw, "instance-template").__setitem__(
        "description", "now with a note"))
    rep = guard.evaluate(old, new, m("instance-template", "update"))
    assert rep.ok
    assert rep.allowed and not rep.denied


def test_denied_protected_remove(old, tmp_path):
    def drop_protected(raw):
        ctx = raw["layers"]["context"]
        ctx[:] = [c for c in ctx if c["id"] != "system-template"]
    new = _edited(tmp_path, drop_protected)
    rep = guard.evaluate(old, new, m("system-template", "remove"))
    assert not rep.ok
    assert rep.denied[0].reason == "protected component"


def test_governance_self_edit_denied(old, tmp_path):
    new = _edited(tmp_path, lambda raw: raw["governance"].__setitem__("blast_radius", "external"))
    rep = guard.evaluate(old, new, manifest=None)
    assert rep.governance_self_edit_denied
    assert not rep.ok
