"""Tests for hdp.engine.track — manifest writing, semver bump, git commit."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import jsonschema
import pytest

from hdp.engine import track
from hdp.engine.core.loader import load

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "hdp" / "examples" / "code-agent-simple.hdp"


def _manifest(operator: str = "update", iteration: int = 1) -> dict:
    return {
        "hdp": "0.1",
        "iteration": iteration,
        "changes": [{
            "change_id": "chg-1",
            "operator": operator,
            "component_id": "long-term-memory",
            "layer": "context",
            "failure_evidence": "trace t-1: forgot a boundary case",
            "root_cause": "no persisted lesson for that case",
            "repair_spec": {
                "editable_resources": ["context/memory/long-term.md"],
                "validation_criteria": ["t-1 flips to pass"],
            },
            "prediction": {"expected_fixes": ["t-1"], "at_risk_regressions": []},
        }],
    }


@pytest.fixture()
def doc(tmp_path):
    dst = tmp_path / "doc.hdp"
    shutil.copytree(EXAMPLE, dst)
    return load(dst)


def test_write_manifest_validates_and_writes(doc):
    path = track.write_manifest(doc, _manifest(iteration=3))
    assert path.exists() and path.name == "003.json"
    written = json.loads(path.read_text())
    assert written["changes"][0]["operator"] == "update"


def test_write_manifest_rejects_invalid(doc):
    bad = _manifest()
    del bad["changes"][0]["prediction"]  # required field
    with pytest.raises(jsonschema.ValidationError):
        track.write_manifest(doc, bad)


def test_bump_version_patch_for_update(doc):
    new = track.bump_version(doc, _manifest("update"))
    assert new == "1.0.1"
    assert load(doc.path).model.meta.version == "1.0.1"  # persisted


@pytest.mark.parametrize("operator,expected", [("add", "1.1.0"), ("remove", "1.1.0")])
def test_bump_version_minor_for_add_remove(doc, operator, expected):
    assert track.bump_version(doc, _manifest(operator)) == expected


def test_commit_creates_a_commit(doc):
    sha = track.commit(doc, "test commit", init_if_needed=True)
    if sha is None:
        pytest.skip("GitPython/git unavailable")
    from git import Repo
    repo = Repo(doc.path)
    assert repo.head.commit.hexsha == sha
    assert "test commit" in str(repo.head.commit.message)


def test_record_end_to_end(doc):
    res = track.record(doc, _manifest("add", iteration=1), do_commit=True)
    assert res.manifest_path.exists()
    assert res.version == "1.1.0"
    # version bump landed in the persisted document
    assert load(doc.path).model.meta.version == "1.1.0"
