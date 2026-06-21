"""Gate 1 — iteration-0 round-trip identity for the OpenHarness backend.

The sibling of ``test_roundtrip_identity.py`` (NexAU) and ``test_roundtrip_identity_mini_swe_agent``:
if ``gen(lift(seed))`` does not reproduce the OpenHarness seed's behavior, the treatment arm diverges
from control before evolution even begins and the A/B is invalid. Pure lift → gen → compare against
the real seed; $0 (no LLM / no E2B).

Identity is behavioral (same definition the other backends use):
  * embedded project files (CLAUDE.md, project skills, MEMORY.md) MUST be byte-identical;
  * ``settings.json`` MUST be *semantically* identical — parsed-JSON equal (and, when the
    ``openharness`` package is installed, ``Settings.model_validate`` of seed and regen compare
    equal). Key order / whitespace re-rendered from the model do not count;
  * the only extra artifact MAY be the non-runtime ``hdp_attribution.json`` sidecar.
"""
from __future__ import annotations

import json
from pathlib import Path

from hdp.engine.core.loader import load
from hdp.engine.gen import generate
from hdp.engine.lift import lift

REPO = Path(__file__).resolve().parents[3]
SEED = REPO / "agents" / "openharness"
TARGET = "openharness"
SETTINGS = ".openharness/settings.json"
SIDECAR = "hdp_attribution.json"


def _files(root: Path) -> set[str]:
    return {
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and ".git" not in p.parts
    }


def _roundtrip(tmp_path: Path) -> Path:
    lift(SEED, tmp_path / "lifted.hdp", target=TARGET)
    out = tmp_path / "regen"
    generate(load(tmp_path / "lifted.hdp"), out, target=TARGET)
    return out


def _semantic(path: Path):
    """A representation that compares equal iff settings.json is behaviorally identical.

    Uses the real ``Settings`` model when ``openharness-ai`` is installed; otherwise plain parsed
    JSON — both ignore key order / whitespace and capture the behavioral content."""
    raw = json.loads(path.read_text())
    try:
        from openharness.config.settings import Settings  # type: ignore

        return Settings.model_validate(raw).model_dump()
    except Exception:
        return raw


def test_roundtrip_file_set_matches_seed(tmp_path):
    out = _roundtrip(tmp_path)
    seed_files, regen_files = _files(SEED), _files(out)
    assert regen_files - seed_files == {SIDECAR}, regen_files - seed_files
    assert seed_files - regen_files == set(), seed_files - regen_files


def test_roundtrip_non_settings_files_byte_identical(tmp_path):
    out = _roundtrip(tmp_path)
    for rel in _files(SEED):
        if rel == SETTINGS:
            continue
        assert (out / rel).read_bytes() == (SEED / rel).read_bytes(), f"byte mismatch: {rel}"


def test_roundtrip_settings_semantically_identical(tmp_path):
    out = _roundtrip(tmp_path)
    assert _semantic(out / SETTINGS) == _semantic(SEED / SETTINGS), (
        "generated settings.json is not behaviorally identical"
    )
