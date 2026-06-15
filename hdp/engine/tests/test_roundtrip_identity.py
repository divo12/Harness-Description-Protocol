"""Gate 1 — iteration-0 round-trip identity (HDP A/B experiment design §Validity gates).

The treatment arm starts from ``lift(seed)`` and runs ``gen`` every iteration. If
``gen(lift(seed))`` does not reproduce the seed's *behavior*, the two arms diverge before
evolution even begins and the A/B is invalid. This gate verifies that round-trip for $0
(no LLM / no E2B): pure lift → gen → file compare against the real NexAU seed harness.

Identity is defined behaviorally, not byte-for-byte:
  * embedded content + referenced code + scaffold files MUST be byte-identical;
  * ``code_agent.yaml`` MUST be *semantically* identical (parsed YAML equal) — YAML comments
    and blank lines are re-rendered from the gen template and do not affect how NexAU loads it;
  * the only extra artifact MAY be the non-runtime ``hdp_attribution.json`` sidecar.
"""
from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

from hdp.engine.core.loader import load
from hdp.engine.gen import generate
from hdp.engine.lift import lift

REPO = Path(__file__).resolve().parents[3]
SEED = REPO / "agents" / "code_agent_simple"
MANIFEST = "code_agent.yaml"
SIDECAR = "hdp_attribution.json"


def _files(root: Path) -> set[str]:
    return {
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and ".git" not in p.parts
    }


def _roundtrip(tmp_path: Path) -> Path:
    lift(SEED, tmp_path / "lifted.hdp", target="nexau")
    out = tmp_path / "regen"
    generate(load(tmp_path / "lifted.hdp"), out, target="nexau")
    return out


def test_roundtrip_file_set_matches_seed(tmp_path):
    out = _roundtrip(tmp_path)
    seed_files, regen_files = _files(SEED), _files(out)
    # treatment may add only the non-runtime sidecar; it must not drop or rename anything.
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
    assert regen_cfg == seed_cfg, "generated code_agent.yaml is not behaviorally identical"
