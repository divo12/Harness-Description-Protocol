"""hdp.engine.track — version-controlled change tracker (SPEC §7.2-7.3).

After the proposer edits the document and ``guard`` reconciles it, ``track`` makes the change
durable and attributable:

* :func:`write_manifest` — validate the change manifest against
  ``hdp/schema/change-manifest.schema.json`` and write it under ``evolution/manifests/``;
* :func:`bump_version` — semver bump on ``meta.version`` (MINOR for add/remove, PATCH for
  update/narrow/gate), persisted in the document;
* :func:`commit` — one git commit per applied manifest (GitPython), operating on the document
  directory as its own work-tree (never the surrounding project repo);
* :func:`record` — do all three and return the artifacts.

The legacy→canonical converter lives in :mod:`hdp.engine.track.manifest_shim`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import jsonschema

from hdp.engine.core.loader import HDPDoc, save

NAME = "track"
PHASE = "Phase 4 (track+attest)"

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "change-manifest.schema.json"
_MINOR_OPERATORS = {"add", "remove"}


def _schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text())


def validate_manifest(manifest: dict) -> None:
    """Raise jsonschema.ValidationError if *manifest* is not a conformant change manifest."""
    jsonschema.Draft7Validator(_schema()).validate(manifest)


def _manifest_dir(doc: HDPDoc) -> Path:
    rel = "evolution/manifests"
    evo = doc.model.evolution
    if evo and evo.manifest_dir:
        rel = evo.manifest_dir
    return doc.path / rel


def write_manifest(doc: HDPDoc, manifest: dict, *, iteration: int | None = None) -> Path:
    """Validate and write *manifest* to ``evolution/manifests/<NNN>.json``; return the path."""
    validate_manifest(manifest)
    it = manifest.get("iteration", 0) if iteration is None else iteration
    out_dir = _manifest_dir(doc)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{int(it):03d}.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def _level(manifest: dict) -> str:
    ops = {c.get("operator") for c in manifest.get("changes", [])}
    return "minor" if ops & _MINOR_OPERATORS else "patch"


def bump_version(doc: HDPDoc, manifest: dict, *, persist: bool = True) -> str:
    """Bump ``meta.version`` per §7.3 based on the manifest's operators; return the new version.

    Mutates the document's ruamel raw (and typed model) and, if *persist*, writes hdp.yaml back.
    """
    major, minor, patch = (int(x) for x in doc.model.meta.version.split("."))
    if _level(manifest) == "minor":
        new = f"{major}.{minor + 1}.0"
    else:
        new = f"{major}.{minor}.{patch + 1}"
    doc.raw["meta"]["version"] = new
    doc.model.meta.version = new
    if persist:
        save(doc)
    return new


def commit(doc: HDPDoc, message: str, *, init_if_needed: bool = True) -> str | None:
    """Commit the document directory as its own git work-tree; return the commit sha (or None
    if git is unavailable / the dir is not and cannot become a repo)."""
    try:
        from git import Repo
        from git.exc import InvalidGitRepositoryError
    except Exception:
        return None
    try:
        repo = Repo(doc.path)  # NOT search_parent_directories — never touch the project repo
    except InvalidGitRepositoryError:
        if not init_if_needed:
            return None
        repo = Repo.init(doc.path)
    repo.git.add(A=True)
    if not repo.is_dirty(untracked_files=True) and repo.head.is_valid():
        return repo.head.commit.hexsha  # nothing to commit
    return repo.index.commit(message).hexsha


@dataclass
class TrackResult:
    manifest_path: Path
    version: str
    commit_sha: str | None


def record(doc: HDPDoc, manifest: dict, *, do_commit: bool = True) -> TrackResult:
    """Persist the manifest, bump the version, and (optionally) commit. One call per iteration."""
    path = write_manifest(doc, manifest)
    version = bump_version(doc, manifest)
    sha = commit(doc, f"hdp: iteration {manifest.get('iteration', '?')} -> v{version}") \
        if do_commit else None
    return TrackResult(manifest_path=path, version=version, commit_sha=sha)


def smoke_step(run) -> None:
    """Phase 0 dry-pipeline stub (the real path is :func:`record`)."""
    run.log("track_commits", 1, phase="evolve", change_id="chg-smoke")
