"""Gate 1 — iteration-0 round-trip identity for the mini-SWE-agent backend.

The sibling of ``test_roundtrip_identity.py`` (NexAU): if ``gen(lift(seed))`` does not reproduce
the mini-SWE-agent seed's behavior, the treatment arm diverges from control before evolution even
begins and the A/B is invalid. Pure lift → gen → compare against the real seed; $0 (no LLM / no E2B).

Identity is behavioral (same definition NexAU uses):
  * embedded/referenced/scaffold files MUST be byte-identical;
  * the manifest MUST be *semantically* identical (parsed YAML equal) — for mini-SWE-agent the
    prompts are inlined, so this is also where embedded-content byte-fidelity is checked;
  * the only extra artifact MAY be the non-runtime ``hdp_attribution.json`` sidecar.
"""
from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

from hdp.engine.core.loader import load
from hdp.engine.gen import generate
from hdp.engine.lift import lift

REPO = Path(__file__).resolve().parents[3]
SEED = REPO / "agents" / "mini_swe_agent"
TARGET = "mini-swe-agent"
MANIFEST = "mini_swe_agent.yaml"
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


def test_roundtrip_file_set_matches_seed(tmp_path):
    out = _roundtrip(tmp_path)
    seed_files, regen_files = _files(SEED), _files(out)
    assert regen_files - seed_files == {SIDECAR}, regen_files - seed_files
    assert seed_files - regen_files == set(), seed_files - regen_files


def test_roundtrip_non_manifest_files_byte_identical(tmp_path):
    out = _roundtrip(tmp_path)
    for rel in _files(SEED):
        if rel == MANIFEST:
            continue
        assert (out / rel).read_bytes() == (SEED / rel).read_bytes(), f"byte mismatch: {rel}"


def test_roundtrip_manifest_semantically_identical(tmp_path):
    out = _roundtrip(tmp_path)
    y = YAML(typ="safe")
    seed_cfg = y.load((SEED / MANIFEST).read_text())
    regen_cfg = y.load((out / MANIFEST).read_text())
    assert regen_cfg == seed_cfg, "generated manifest is not behaviorally identical"
