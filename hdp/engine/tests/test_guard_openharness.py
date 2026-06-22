"""The guard is backend-agnostic: it reads the HDP document diff, never backend files.

These tests lift the OpenHarness seed into an HDP document and run the *unmodified* tiered guard
(``hdp.engine.guard.decide``) against it, showing it produces the same CORE denials it does for
NexAU (``test_guard_redteam.py::test_core_confinement_denied_in_both_modes``) — the generality
claim the OpenHarness backend is meant to demonstrate (build-brief acceptance criterion #6).

The four confinement mutations from the ETCLOVG mapping are each blocked, and each lands on a
*generic* CORE rule (no OpenHarness-specific guard code exists). The confinement core is never
overridable, so each is denied in ``review`` mode too:
  * escalating permission.mode (→ governance.blast_radius external)        → CORE confinement;
  * widening allowed_tools (adding a higher-blast tool over the ceiling)    → CORE blast_radius;
  * shrinking denied_commands (editing the protected confinement component) → CORE protected;
  * widening sandbox.network.allowed_domains (same protected component)     → CORE protected.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from hdp.engine.core.loader import HDPDoc, load, save
from hdp.engine.guard import Edit, Tier, decide
from hdp.engine.lift import lift

REPO = Path(__file__).resolve().parents[3]
SEED = REPO / "agents" / "openharness"


@pytest.fixture()
def old(tmp_path) -> HDPDoc:
    return lift(SEED, tmp_path / "old.hdp", target="openharness")


def _edited(tmp_path, mutate: Callable[[object], None]) -> HDPDoc:
    """Copy the lifted doc, apply *mutate* to its ruamel raw, save+reload → the 'new' doc."""
    dst = tmp_path / "new.hdp"
    if not dst.exists():
        lift(SEED, dst, target="openharness")
    doc = load(dst)
    mutate(doc.raw)
    save(doc)
    return load(dst)


def _comp(raw, layer, cid):
    return next(c for c in raw["layers"][layer] if c["id"] == cid)


def _edit(*cids: str) -> Edit:
    """A covering change-manifest edit; CORE violations fire regardless of its operator."""
    manifest = {"changes": [{"component_id": c, "operator": "update",
                             "change_id": f"chg-{c}"} for c in (cids or ("-",))]}
    return Edit(operator="update", layer="-", component_id=cids[0] if cids else "-",
                manifest=manifest)


def test_clean_editable_edit_allowed(old, tmp_path):
    new = _edited(tmp_path, lambda raw: _comp(raw, "context", "project-instructions").__setitem__(
        "description", "now with a note"))
    dec = decide(_edit("project-instructions"), old, new, mode="enforce")
    assert dec.status == "allow", dec.reasons


def _assert_core(old, new, rule: str) -> None:
    dec = decide(_edit("sandbox", "network-request", "-"), old, new, mode="enforce")
    assert dec.status == "deny"
    assert any(v.tier == Tier.CORE and v.rule == rule for v in dec.violations), dec.reasons
    # confinement core is never overridable: still denied in review mode.
    assert decide(_edit("sandbox", "network-request", "-"), old, new, mode="review").status == "deny"


def test_core_escalate_permission_mode(old, tmp_path):
    # permission.mode full_auto ↔ governance.blast_radius external: a widening of the ceiling.
    new = _edited(tmp_path, lambda raw: raw["governance"].__setitem__("blast_radius", "external"))
    _assert_core(old, new, "confinement")


def test_core_widen_allowed_tools(old, tmp_path):
    # Adding a network tool (blast_radius external) exceeds the system ceiling.
    def add_tool(raw):
        raw["layers"]["tooling"].append({
            "id": "network-request", "type": "tool", "name": "network_request",
            "description": "make an outbound network request", "blast_radius": "external",
        })
    _assert_core(old, _edited(tmp_path, add_tool), "blast_radius")


def test_core_shrink_denied_commands(old, tmp_path):
    # Dropping an entry from the protected confinement component's denied_commands.
    def loosen(raw):
        _comp(raw, "execution", "sandbox")["params"]["permission"]["denied_commands"] = ["rm -rf /"]
    _assert_core(old, _edited(tmp_path, loosen), "protected")


def test_core_widen_sandbox_network(old, tmp_path):
    # Widening sandbox.network.allowed_domains on the protected confinement component.
    def widen(raw):
        _comp(raw, "execution", "sandbox")["params"]["sandbox"]["network"]["allowed_domains"] = ["*"]
    _assert_core(old, _edited(tmp_path, widen), "protected")
