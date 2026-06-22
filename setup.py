#!/usr/bin/env python3
"""setup.py — from-scratch environment bootstrap for Agentic Harness Engineering (AHE / HDP).

This file does double duty:

1. **Bootstrap installer (the primary, documented path).** Run it directly to set up a complete,
   ready-to-run environment in whatever Python interpreter executes it (a venv, a conda env, or a
   Lightning AI Studio's environment)::

       python setup.py                 # install everything (runtime + dev/test + project)
       python setup.py --no-dev        # runtime + project only (skip ruff/mypy/pytest/codegen)
       python setup.py --no-project    # deps only; don't install this repo as an editable package

   It installs *every* dependency the repository needs — including the two git deps, the local
   agent-debugger source, the uv-only version constraint, and pytest (needed by the test suite but
   absent from pyproject) — none of which a plain ``pip install -r`` would get right.

2. **setuptools metadata** for ``pip install .`` / ``pip install -e .`` (only the PyPI runtime deps
   are declared as ``install_requires``; the git/local deps are handled by the bootstrap path so
   pip never tries to resolve the local-only ``agent_debugger_core`` from PyPI).

Designed for Lightning AI: all pip calls use ``sys.executable`` so packages land in the active
Studio env, the Python-version check fails loudly if the Studio isn't on 3.13+, and tmux (a system
prerequisite of the AHE harness runner) is installed best-effort via apt when missing.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
MIN_PYTHON = (3, 13)

# --------------------------------------------------------------------------- #
#  The complete dependency set (single source of truth — keep in sync with pyproject.toml).
# --------------------------------------------------------------------------- #
# Runtime deps available on PyPI (pyproject [project].dependencies, minus the git/local ones).
RUNTIME_PYPI = [
    "pyyaml>=6.0",
    "python-dotenv>=1.0",
    "e2b>=1.0.0",
    "jsonschema>=4",
    "tqdm>=4",
    "ruamel.yaml>=0.18",
    "pydantic>=2",
    "jinja2>=3",
    "GitPython>=3",
]

# Runtime deps installed from git (pyproject's ``pkg @ git+https://...`` entries).
RUNTIME_GIT = [
    "nexau @ git+https://github.com/nex-agi/NexAU.git@v0.3.9",
    "harbor @ git+https://github.com/Curry09/harbor-LJH.git",
]

# Local path dep (uv [tool.uv.sources]): the agent-debugger core ships inside the repo, not PyPI.
LOCAL_PATHS = [
    REPO / "agents" / "evolve_agent" / "skills" / "agent-debugger-cli" / "_source",
]

# uv [tool.uv] constraint-dependencies — pinned via a pip constraints file applied to every install.
CONSTRAINTS = [
    "claude-agent-sdk<0.1.49",
]

# Dev / test deps: pyproject [dependency-groups].dev + pytest (the test suite needs it but it is
# not declared in pyproject — uv pulls it transitively; pip must be told explicitly).
DEV = [
    "datamodel-code-generator>=0.25",
    "ruff>=0.6",
    "mypy>=1.11",
    "pytest>=8",
]


# --------------------------------------------------------------------------- #
#  Bootstrap path
# --------------------------------------------------------------------------- #
def _log(msg: str) -> None:
    print(f"\n\033[1m==>\033[0m {msg}", flush=True)


def _pip(*args: str, constraints: Path | None = None) -> None:
    cmd = [sys.executable, "-m", "pip", "install"]
    if constraints is not None:
        cmd += ["-c", str(constraints)]
    cmd += list(args)
    print("    $ " + " ".join(cmd), flush=True)
    subprocess.check_call(cmd)


def _check_python() -> None:
    if sys.version_info < MIN_PYTHON:
        sys.exit(
            f"ERROR: Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]} is required, but this interpreter is "
            f"{sys.version.split()[0]} ({sys.executable}).\n"
            "On Lightning AI, select a 3.13+ environment (or create one) and re-run with that "
            "interpreter, e.g.  python3.13 setup.py"
        )
    _log(f"Python {sys.version.split()[0]} at {sys.executable} (OK)")


def _ensure_pip() -> None:
    _log("Ensuring pip / setuptools / wheel")
    try:
        import pip  # noqa: F401
    except ModuleNotFoundError:
        subprocess.check_call([sys.executable, "-m", "ensurepip", "--upgrade"])
    _pip("--upgrade", "pip", "setuptools", "wheel")


def _ensure_tmux() -> None:
    """tmux is a system prerequisite of the AHE harness runner — install best-effort, never fatal."""
    if shutil.which("tmux"):
        _log("tmux present (OK)")
        return
    _log("tmux not found — attempting a best-effort system install")
    apt = shutil.which("apt-get")
    if not apt:
        print("    (no apt-get; install tmux yourself, e.g. `brew install tmux`)")
        return
    is_root = getattr(os, "geteuid", lambda: 1)() == 0
    prefix = [] if is_root else (["sudo"] if shutil.which("sudo") else [])
    try:
        subprocess.check_call([*prefix, apt, "update"])
        subprocess.check_call([*prefix, apt, "install", "-y", "tmux"])
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("    (could not auto-install tmux; install it manually before real eval runs)")


def _write_constraints() -> Path:
    # A temp file (not a repo dir) so the bootstrap leaves no artifacts behind.
    fd, name = tempfile.mkstemp(prefix="ahe-pip-constraints-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(CONSTRAINTS) + "\n")
    return Path(name)


def _verify_imports() -> None:
    _log("Verifying key imports")
    checks = ["yaml", "dotenv", "jsonschema", "tqdm", "ruamel.yaml", "pydantic", "jinja2", "git",
              "hdp.engine"]
    failed = []
    for mod in checks:
        try:
            subprocess.check_call([sys.executable, "-c", f"import {mod}"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"    ok  {mod}")
        except subprocess.CalledProcessError:
            failed.append(mod)
            print(f"     X   {mod}")
    if failed:
        sys.exit(f"ERROR: these imports failed after setup: {failed}")


def bootstrap(argv: list[str]) -> int:
    install_dev = "--no-dev" not in argv
    install_project = "--no-project" not in argv

    _check_python()
    _ensure_pip()
    _ensure_tmux()
    constraints = _write_constraints()

    _log(f"Installing {len(RUNTIME_PYPI)} runtime deps (PyPI)")
    _pip(*RUNTIME_PYPI, constraints=constraints)

    _log(f"Installing {len(RUNTIME_GIT)} runtime deps (git)")
    _pip(*RUNTIME_GIT, constraints=constraints)

    _log(f"Installing {len(LOCAL_PATHS)} local path dep(s)")
    for path in LOCAL_PATHS:
        if not (path / "pyproject.toml").is_file() and not (path / "setup.py").is_file():
            sys.exit(f"ERROR: expected a Python project at {path} (agent_debugger_core source)")
        _pip(str(path), constraints=constraints)

    if install_dev:
        _log(f"Installing {len(DEV)} dev/test deps")
        _pip(*DEV, constraints=constraints)
    else:
        _log("Skipping dev/test deps (--no-dev)")

    if install_project:
        _log("Installing this repository as an editable package (no extra deps)")
        _pip("-e", ".", "--no-deps", constraints=constraints)
    else:
        _log("Skipping editable project install (--no-project)")

    _verify_imports()

    _log("Environment ready. Next steps:")
    print("    python -m pytest hdp/engine/tests -q      # expect all green")
    print("    ruff check hdp/ ahe_control/  &&  mypy hdp")
    print("    python -m hdp.engine.run smoke --dry-run  # end-to-end wiring check ($0)")
    print("    # real eval runs also need a .env (LLM_API_KEY / LLM_BASE_URL / E2B_API_KEY)")
    return 0


# --------------------------------------------------------------------------- #
#  Dispatch: setuptools build command  ->  setup();  otherwise  ->  bootstrap.
# --------------------------------------------------------------------------- #
_SETUPTOOLS_COMMANDS = {
    "install", "develop", "egg_info", "bdist_wheel", "sdist", "build", "build_ext",
    "build_py", "dist_info", "editable_wheel", "bdist_egg", "check",
}


def _run_setuptools() -> None:
    # Lets pip build/install this repo (e.g. the bootstrap's `pip install -e . --no-deps`).
    # All metadata — name, packages, deps, console script — comes from pyproject.toml's PEP 621
    # [project] table, which setuptools reads automatically; we don't restate it here (and must
    # not, or it would shadow pyproject and try to resolve the local-only agent_debugger_core).
    from setuptools import setup

    setup()


if __name__ == "__main__":
    if any(arg in _SETUPTOOLS_COMMANDS for arg in sys.argv[1:]):
        _run_setuptools()
    else:
        raise SystemExit(bootstrap(sys.argv[1:]))
