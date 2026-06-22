"""hdp.engine.adapters.openharness — govern the OpenHarness backend through its public config.

This is a sibling of :mod:`hdp.engine.adapters.nexau` and :mod:`hdp.engine.adapters.mini_swe_agent`
and implements the same :class:`FrameworkAdapter` contract. It imports nothing from the other
adapters (shared-looking helpers are deliberately duplicated to keep the adapters uncoupled).

Step 0 — backend study & mapping
================================
OpenHarness (``openharness-ai``, an open-source port of Claude Code; entry points ``oh`` /
``openharness``) is **not** a file-workspace like NexAU. Its entire declarative harness surface is
``settings.json`` (the Pydantic ``openharness.config.settings.Settings`` model, read from the dir
named by ``OPENHARNESS_CONFIG_DIR``), plus a per-project ``.openharness/`` and project context files
(``CLAUDE.md`` etc.). **OpenHarness itself is never modified**: the adapter drives it purely through
that public config surface and the public headless CLI ``oh -p "<task>"``.

``settings.json`` keys → HDP (ETCLOVG). Runtime boilerplate is NOT an HDP component (SPEC §5.2):
``provider`` is baked here (the seed matches), ``model`` → ``meta.base_model``, ``max_turns`` →
the lifecycle loop. The confinement-bearing keys are mapped onto the *generic* surfaces the
unmodified guard already enforces (``governance.blast_radius`` + ``evolution`` + per-component
``blast_radius``), so the safety brain governs OpenHarness for free:

  ┌────────────────────────────────────┬──────────────────────────┬───────────┬───────────────┐
  │ settings.json key                  │ HDP component / field    │ layer     │ embed / source│
  ├────────────────────────────────────┼──────────────────────────┼───────────┼───────────────┤
  │ system_prompt (inline override)    │ system_rules (id=system- │ context   │ embedded →    │
  │                                    │   prompt)                │           │ re-inlined    │
  │ project CLAUDE.md                  │ system_rules (id=project-│ context   │ embedded file │
  │                                    │   instructions)          │           │               │
  │ permission.allowed_tools           │ one tool component each  │ tooling   │ descriptive   │
  │ permission.mode                    │ governance.blast_radius  │ —         │ ceiling       │
  │ permission.{denied_tools,          │ sandbox component params │ execution │ protected     │
  │   denied_commands, path_rules}     │   (the confinement comp) │           │               │
  │ sandbox.{network,filesystem,…}     │ sandbox component params │ execution │ protected     │
  │ max_turns                          │ lifecycle.loop.max_iter  │ lifecycle │ settings      │
  │ model                              │ meta.base_model          │ —         │ meta          │
  │ provider                           │ (runtime boilerplate)    │ —         │ baked here    │
  └────────────────────────────────────┴──────────────────────────┴───────────┴───────────────┘

Notable shape difference from NexAU/mini (the point — it proves the HDP brain is backend-agnostic):
OpenHarness owns its tool *code* (installed in the ``openharness`` package), so tool components are
**descriptive, not authoritative** and are never serialized back into ``settings.json``; what
``generate`` writes back is the ``permission`` deny/allow surface. Refs are dotted import paths, not
bundled assets — there is no ``openharness_assets/`` tree. ``generate`` "resolves" a ref by checking
the import path exists in the installed ``openharness`` package and **fails (not skips)** if it does
not (SPEC §10); when the package is absent (e.g. CI / the $0 round-trip gate) there is nothing to
resolve against, so ref-verification is vacuously skipped — symmetric to ``lift``'s graceful degrade.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hdp.engine.adapters.base import FrameworkAdapter
from hdp.engine.core.loader import HDPDoc
from hdp.engine.core.models import Component

_BLAST_ORDER = ["read_only", "local", "session", "system", "external"]
_SHELL_TOKENS = ("shell", "bash", "exec", "command", "terminal", "subprocess", "process")
_NET_TOKENS = ("network", "web", "http", "url", "fetch", "mcp", "send", "message", "request")
_DESTRUCTIVE_TOKENS = ("write", "delete", "remove", "unlink", "overwrite")

# settings.json lives under the OPENHARNESS_CONFIG_DIR (= <dir>/.openharness); see §7.
_CONFIG_SUBDIR = ".openharness"
_SETTINGS_NAME = "settings.json"

# OpenHarness runtime boilerplate that is not an HDP component (SPEC §5.2); the seed matches.
_PROVIDER = "openai"

# Component ids with a fixed settings.json destination (drives symmetric lift/generate).
_SYSTEM_PROMPT_ID = "system-prompt"          # ↔ settings.json "system_prompt" (re-inlined)
_PROJECT_INSTRUCTIONS_ID = "project-instructions"  # ↔ project-root CLAUDE.md
_PROJECT_FILE = "CLAUDE.md"
_CONFINEMENT_ID = "sandbox"                  # the protected execution component

# permission.mode ↔ governance.blast_radius (the confinement ladder). Escalating the mode is a
# widening of governance.blast_radius, which the unmodified guard denies as a CORE violation.
_MODE_TO_BLAST = {"plan": "session", "default": "system", "full_auto": "external"}
_BLAST_TO_MODE = {v: k for k, v in _MODE_TO_BLAST.items()}


def _slug(s: str | None) -> str:
    s = s or ""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", s)  # split camelCase
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "x"


def _infer_blast_radius(name: str | None, binding: str | None, is_read_only: bool | None) -> str:
    if is_read_only:
        return "read_only"
    blob = f"{name or ''} {binding or ''}".lower()
    if any(tok in blob for tok in _NET_TOKENS):
        return "external"
    if any(tok in blob for tok in _SHELL_TOKENS):
        return "system"
    if any(tok in blob for tok in _DESTRUCTIVE_TOKENS):
        return "system"
    return "session"  # conservative default; the LLM pass refines when uncertain


def _max_blast(a: str, b: str) -> str:
    return a if _BLAST_ORDER.index(a) >= _BLAST_ORDER.index(b) else b


def _config_dir(harness_dir: Path) -> Path:
    """The dir holding settings.json: ``<harness_dir>/.openharness`` (§7), falling back to the
    harness root if a flat layout is used."""
    nested = harness_dir / _CONFIG_SUBDIR / _SETTINGS_NAME
    return harness_dir / _CONFIG_SUBDIR if nested.is_file() else harness_dir


def _read_settings(harness_dir: Path) -> dict:
    """Read settings.json. Validate via the real ``Settings`` model when the package is installed;
    otherwise parse the JSON directly so lift still works without ``openharness-ai`` present."""
    path = _config_dir(harness_dir) / _SETTINGS_NAME
    raw = json.loads(path.read_text(encoding="utf-8"))
    try:  # best-effort fidelity when the package is available; never required.
        from openharness.config.settings import Settings  # type: ignore
        return Settings.model_validate(raw).model_dump(exclude_none=True)
    except Exception:
        return raw


def _prune_none(obj) -> None:
    if isinstance(obj, dict):
        for k in list(obj):
            if obj[k] is None:
                del obj[k]
            else:
                _prune_none(obj[k])
    elif isinstance(obj, list):
        for item in obj:
            _prune_none(item)


def _write_yaml(path: Path, data: dict) -> None:
    from ruamel.yaml import YAML

    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    y.default_flow_style = False
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        y.dump(data, f)


class OpenHarnessAdapter(FrameworkAdapter):
    target = "openharness"

    # ====================================================================== #
    #  generate: HDP document -> OpenHarness config dir
    # ====================================================================== #
    def generate(self, doc: HDPDoc, out_dir: Path) -> Path:
        out_dir = Path(out_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        self._reject_unsupported(doc)

        attribution: list[dict] = []
        by_tool_name: dict[str, str] = {}

        # 1. Embedded context. The system prompt is re-inlined into settings.json; project files
        #    are written byte-for-byte to their project locations.
        inline_prompt: str | None = None
        for _layer, comp in doc.components():
            if comp.type.value != "system_rules":
                continue
            content = doc.read_embedded(comp)
            if content is None:
                continue
            if comp.id == _SYSTEM_PROMPT_ID:
                inline_prompt = content
                artifact = f"{_CONFIG_SUBDIR}/{_SETTINGS_NAME}#system_prompt"
            else:
                dest = out_dir / self._project_dest(comp.id)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8", newline="")
                artifact = self._project_dest(comp.id)
            attribution.append(
                {"id": comp.id, "layer": "context", "type": "system_rules", "artifact": artifact}
            )

        # 2. Tools are descriptive (their code lives in the installed package). We do not serialize
        #    them into settings.json; we only verify any declared ref import path resolves.
        tool_names: list[str] = []
        for _layer, comp in doc.components():
            if comp.type.value != "tool":
                continue
            if comp.name:
                tool_names.append(comp.name)
                by_tool_name[comp.name] = comp.id
            impl = comp.implementation
            if impl is not None and impl.kind.value == "adapter":
                self._resolve_ref(impl.binding)
            attribution.append(
                {"id": comp.id, "layer": "tooling", "type": "tool",
                 "artifact": f"{_CONFIG_SUBDIR}/{_SETTINGS_NAME}#permission.allowed_tools"}
            )

        # 3. Assemble settings.json from the typed model + baked runtime boilerplate.
        settings = self._render_settings(doc, inline_prompt, tool_names)
        cfg_dir = out_dir / _CONFIG_SUBDIR
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / _SETTINGS_NAME).write_text(
            json.dumps(settings, indent=2) + "\n", encoding="utf-8"
        )

        # 4. Non-runtime attribution sidecar (realizes SPEC §8 without touching behavior).
        sidecar = {
            "hdp": doc.model.hdp,
            "doc": {"id": doc.model.meta.id, "version": doc.model.meta.version},
            "by_tool_name": by_tool_name,
            "components": attribution,
        }
        (out_dir / "hdp_attribution.json").write_text(
            json.dumps(sidecar, indent=2) + "\n", encoding="utf-8"
        )
        return out_dir

    # -- helpers ----------------------------------------------------------------
    @staticmethod
    def _project_dest(cid: str) -> str:
        if cid == _PROJECT_INSTRUCTIONS_ID:
            return _PROJECT_FILE
        raise NotImplementedError(
            f"OpenHarness adapter has no project destination for context component '{cid}'"
        )

    def _reject_unsupported(self, doc: HDPDoc) -> None:
        """Fail loud on components this adapter cannot faithfully produce (no silent skip).
        Same v1 conformance rejections as the NexAU adapter."""
        for _layer, comp in doc.components():
            t = comp.type.value
            if t == "verifier" and comp.trigger and comp.trigger.value != "external":
                raise NotImplementedError(
                    f"verifier '{comp.id}' trigger={comp.trigger.value}: only external "
                    "verifiers (handled by the eval harness) are supported in v1"
                )
            if t == "policy":
                raise NotImplementedError(
                    f"policy component '{comp.id}': in-harness policy enforcement is not "
                    "wired in v1"
                )

    @staticmethod
    def _resolve_ref(binding: str | None) -> None:
        """Verify a ``module.path:attr`` ref resolves in the installed ``openharness`` package.

        Fails (not skips) on an unresolvable import path when the package is present (SPEC §10).
        When ``openharness`` is not installed at all (CI / round-trip gate), there is nothing to
        resolve against, so verification is vacuously skipped — symmetric to lift's degrade."""
        if not binding:
            return
        mod_path = binding.split(":", 1)[0]
        top = mod_path.split(".", 1)[0]
        if importlib.util.find_spec(top) is None:
            return  # package absent in this environment; the experiment env has it.
        module = importlib.import_module(mod_path)
        attr = binding.split(":", 1)[1] if ":" in binding else None
        if attr and not hasattr(module, attr):
            raise ValueError(
                f"OpenHarness adapter cannot resolve ref binding '{binding}': "
                f"'{attr}' not found in '{mod_path}'"
            )

    def _render_settings(
        self, doc: HDPDoc, inline_prompt: str | None, tool_names: list[str]
    ) -> dict:
        loop = self._first(doc, "loop")
        confinement = doc.component(_CONFINEMENT_ID)
        params = (confinement.params or {}) if confinement else {}
        gov = doc.model.governance
        ceiling = gov.blast_radius.value if gov and gov.blast_radius else "system"

        settings: dict = {
            "model": doc.model.meta.base_model,
            "provider": _PROVIDER,
        }
        if loop is not None:
            settings["max_turns"] = loop.max_iterations
        if inline_prompt is not None:
            settings["system_prompt"] = inline_prompt

        permission: dict[str, Any] = {"mode": _BLAST_TO_MODE.get(ceiling, "default")}
        if tool_names:
            permission["allowed_tools"] = sorted(tool_names)
        permission.update(params.get("permission") or {})
        settings["permission"] = permission

        sandbox = params.get("sandbox")
        if sandbox is not None:
            settings["sandbox"] = sandbox
        return settings

    @staticmethod
    def _first(doc: HDPDoc, type_name: str) -> Component | None:
        for _l, comp in doc.components():
            if comp.type.value == type_name:
                return comp
        return None

    # ====================================================================== #
    #  lift: OpenHarness config dir -> HDP document
    # ====================================================================== #
    def lift(
        self,
        harness_dir: Path,
        out_dir: Path,
        *,
        meta_id: str | None = None,
        version: str = "1.0.0",
        base_model: str | None = None,
        llm: Callable[[str], str] | None = None,
    ) -> HDPDoc:
        from hdp.engine.core.loader import HDPManifest, load

        harness_dir = Path(harness_dir).resolve()
        out_dir = Path(out_dir).resolve()
        settings = _read_settings(harness_dir)
        permission = settings.get("permission") or {}

        layers: dict[str, list] = {}
        protected: list[str] = []

        # -- context: inline system prompt + project instruction files (embedded byte-for-byte) --
        context: list[dict] = []
        sp = settings.get("system_prompt")
        if isinstance(sp, str):
            dest = f"./context/{_SYSTEM_PROMPT_ID}.md"
            out_path = out_dir / dest.lstrip("./")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(sp, encoding="utf-8", newline="")
            context.append({"id": _SYSTEM_PROMPT_ID, "type": "system_rules", "file": dest})
            protected.append(_SYSTEM_PROMPT_ID)
        project_file = harness_dir / _PROJECT_FILE
        if project_file.is_file():
            dest = f"./context/{_PROJECT_INSTRUCTIONS_ID}.md"
            out_path = out_dir / dest.lstrip("./")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(project_file, out_path)
            context.append(
                {"id": _PROJECT_INSTRUCTIONS_ID, "type": "system_rules", "file": dest}
            )
        if context:
            layers["context"] = context

        # -- tooling: one descriptive component per allowed tool ------------------------------- #
        tooling: list[dict] = []
        max_tool_blast = "read_only"
        for name, is_ro, binding in self._enumerate_tools(permission):
            cid = _slug(name)
            blast = _infer_blast_radius(name, binding, is_ro)
            max_tool_blast = _max_blast(max_tool_blast, blast)
            comp: dict = {
                "id": cid, "type": "tool", "name": name,
                "description": f"OpenHarness '{name}' tool (allowed by permission policy).",
                "blast_radius": blast,
            }
            if binding:
                comp["implementation"] = {"kind": "adapter", "binding": binding}
            tooling.append(comp)
        if tooling:
            layers["tooling"] = tooling

        # -- execution: the protected confinement component (sandbox + permission deny-lists) --- #
        #    Loosening any of it = modifying a protected component = a CORE guard denial; the
        #    permission *mode* is carried separately as the governance.blast_radius ceiling.
        confinement_params: dict = {}
        if settings.get("sandbox") is not None:
            confinement_params["sandbox"] = settings["sandbox"]
        deny = {k: permission[k] for k in ("denied_tools", "denied_commands", "path_rules")
                if k in permission}
        if deny:
            confinement_params["permission"] = deny
        if confinement_params:
            layers["execution"] = [{
                "id": _CONFINEMENT_ID, "type": "sandbox",
                "description": "OpenHarness sandbox + permission confinement (non-widening).",
                "blast_radius": "system",
                "params": confinement_params,
            }]
            protected.append(_CONFINEMENT_ID)

        # -- lifecycle: the agent loop (max_turns) --------------------------------------------- #
        layers["lifecycle"] = [{
            "id": "main-loop", "type": "loop",
            "max_iterations": int(settings.get("max_turns") or 0) or 1,
        }]

        # -- governance: ceiling from permission.mode; safe evolution defaults (SPEC §5.2) ----- #
        ceiling = _MODE_TO_BLAST.get(permission.get("mode", "default"), "system")
        ceiling = _max_blast(ceiling, max_tool_blast)
        governance = {
            "blast_radius": ceiling,
            "evolution": {
                "editable": ["context", "tooling", "lifecycle"],
                "read_only": ["verification", "governance", "execution"],
                "protected": protected,
            },
            "audit": {"log_all_tool_calls": ceiling in ("system", "external")},
        }

        doc_dict: dict = {
            "hdp": "0.1",
            "meta": {
                "id": meta_id or _slug(harness_dir.name),
                "version": version,
                "name": settings.get("name"),
                "base_model": base_model or settings.get("model"),
                "targets": [self.target],
            },
            "layers": layers,
            "governance": governance,
        }
        _prune_none(doc_dict)

        if llm is not None:
            doc_dict = self._llm_refine(doc_dict, harness_dir, llm)

        # MUST validate against the typed model before writing (SPEC conformance).
        HDPManifest.model_validate(doc_dict)

        out_dir.mkdir(parents=True, exist_ok=True)
        _write_yaml(out_dir / "hdp.yaml", doc_dict)
        return load(out_dir)

    @staticmethod
    def _enumerate_tools(permission: dict) -> list[tuple[str, bool | None, str | None]]:
        """Yield ``(name, is_read_only, binding)`` per tool.

        When ``openharness`` is importable, enumerate the real registry (accurate ``is_read_only``
        → ``blast_radius`` and a dotted-import ``binding``). Otherwise **degrade gracefully** to the
        tools implied by ``permission.allowed_tools`` (descriptive only — no binding)."""
        try:
            from openharness.tools import create_default_tool_registry  # type: ignore

            allowed = set(permission.get("allowed_tools") or [])
            out: list[tuple[str, bool | None, str | None]] = []
            for tool in create_default_tool_registry():
                if allowed and tool.name not in allowed:
                    continue
                binding = f"{type(tool).__module__}:{type(tool).__name__}"
                out.append((tool.name, getattr(tool, "is_read_only", None), binding))
            if out:
                return out
        except Exception:
            pass
        return [(name, None, None) for name in (permission.get("allowed_tools") or [])]

    def _llm_refine(self, doc_dict: dict, harness_dir: Path, llm) -> dict:
        """Extension point: ask an LLM to fill ambiguous fields. The deterministic mapping already
        handles OpenHarness seeds, so this is a no-op unless wired."""
        return doc_dict
