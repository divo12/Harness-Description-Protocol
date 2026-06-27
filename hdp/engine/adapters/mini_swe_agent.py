"""hdp.engine.adapters.mini_swe_agent — compile an HDP document into a mini-SWE-agent harness.

This is the engine's second backend (the "OpenHarness slot" of the build brief, filled with the
real `mini-SWE-agent <https://github.com/SWE-agent/mini-swe-agent>`_ backend). It is a sibling of
:mod:`hdp.engine.adapters.nexau` and implements the same :class:`FrameworkAdapter` contract; it
imports nothing from ``nexau`` (shared-looking helpers are deliberately duplicated to keep the two
adapters uncoupled in v1).

Step 0 — backend study & mapping
================================
A mini-SWE-agent run is driven entirely by ONE YAML manifest (the analogue of NexAU's
``code_agent.yaml``). ``run/mini.py`` loads it, builds ``model`` / ``environment`` / ``agent``,
and ``agents/default.py`` runs the loop: query the model, execute the single action it emits — a
**bash** command — via ``environments/*.execute``, append the observation, repeat. There is no tool
registry; bash is the entire action space.

Manifest keys → HDP (ETCLOVG). Runtime boilerplate is NOT an HDP component (SPEC §5.2): the
``environment`` block and the non-prompt ``model`` keys (``model_name``, ``model_kwargs``) are baked
into the gen template exactly as NexAU bakes ``llm_config``.

  ┌───────────────────────────────────┬──────────────────────────┬───────────┬──────────────┐
  │ mini-SWE-agent artifact           │ HDP component            │ layer     │ embed / ref  │
  ├───────────────────────────────────┼──────────────────────────┼───────────┼──────────────┤
  │ agent.system_template             │ system_rules             │ context   │ embedded     │
  │ agent.instance_template           │ system_rules             │ context   │ embedded     │
  │ agent.observation_template        │ system_rules             │ context   │ embedded     │
  │ agent.format_error_template       │ system_rules             │ context   │ embedded     │
  │ bash action (env.execute)         │ tool (id=bash)           │ tooling   │ ref binding  │
  │ agent.step_limit                  │ lifecycle.loop.max_iter  │ lifecycle │ manifest     │
  │ agent.{cost_limit, …residual}     │ lifecycle.loop.params    │ lifecycle │ manifest     │
  │ environment.* , model.model_name, │ (runtime boilerplate —   │   —       │ baked in     │
  │ model.model_kwargs                │  not a component)        │           │ template     │
  └───────────────────────────────────┴──────────────────────────┴───────────┴──────────────┘

Notable shape difference from NexAU (this is the point — it proves the HDP brain is
backend-agnostic): mini-SWE-agent is prompt-heavy / tool-thin. Its prompts live *inline* in the
manifest (not as separate files), so the generated harness has no standalone prompt files — the
embedded content is re-inlined into the manifest, while the only ``ref``-materialized file is the
bash executor. ``blast_radius`` is inferred with the same shell-token heuristic as NexAU (bash →
``system``). A non-runtime ``hdp_attribution.json`` sidecar records component_id ↔ artifact so the
SPEC §8 trace-attribution convention stays realizable without altering harness behavior.
"""
from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from ruamel.yaml import YAML

from hdp.engine.adapters.base import FrameworkAdapter
from hdp.engine.core.loader import HDPDoc
from hdp.engine.core.models import Component

_BLAST_ORDER = ["read_only", "local", "session", "system", "external"]
_SHELL_TOKENS = ("shell", "bash", "exec", "command", "terminal", "subprocess")


def _slug(s: str | None) -> str:
    s = s or ""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", s)  # split camelCase
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "x"


def _infer_blast_radius(name: str | None, binding: str | None) -> str:
    blob = f"{name or ''} {binding or ''}".lower()
    if any(tok in blob for tok in _SHELL_TOKENS):
        return "system"
    return "session"  # conservative default; the LLM pass refines when uncertain


def _max_blast(a: str, b: str) -> str:
    return a if _BLAST_ORDER.index(a) >= _BLAST_ORDER.index(b) else b


def _read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return YAML(typ="safe").load(f) or {}


def _write_yaml(path: Path, data: dict) -> None:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    y.default_flow_style = False
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        y.dump(data, f)


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


def _yaml_block(value: str, indent: int = 4) -> str:
    """Render *value* as the body of a YAML literal (``|``) block at *indent* spaces.

    A ``|`` block always re-adds exactly one trailing newline (clip), and a mini-SWE-agent
    template parsed from such a block always ends in ``\\n`` — so dropping the final empty
    segment here and letting the template's own newline restore it reproduces the value
    byte-for-byte on re-parse. Deterministic; relied on by the round-trip identity gate.
    """
    pad = " " * indent
    lines = value.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return "\n".join(pad + ln if ln else "" for ln in lines)


_ASSETS = Path(__file__).resolve().parent / "mini_swe_agent_assets"
_TEMPLATES = Path(__file__).resolve().parents[1] / "gen" / "templates"

# mini-SWE-agent target defaults that are not HDP components (model/runtime boilerplate).
_MANIFEST_NAME = "mini_swe_agent.yaml"
_DEFAULT_MODEL = "anthropic/claude-sonnet-4-5-20250929"

# Manifest prompt templates ↔ context components. Each entry is (component_id, section, key):
# the section/key locate the scalar in the manifest; the id is the HDP component id. Driving
# round-trip off this single table keeps lift and generate symmetric.
_PROMPT_FIELDS: list[tuple[str, str, str]] = [
    ("system-template", "agent", "system_template"),
    ("instance-template", "agent", "instance_template"),
    ("observation-template", "agent", "observation_template"),
    ("format-error-template", "agent", "format_error_template"),
]

# The single bash tool's `ref` binding → asset files (relative to mini_swe_agent_assets/) that
# realize it. A generator MUST fail, not skip, on a binding it cannot resolve (SPEC §10).
_BASH_BINDING = "minisweagent.environments.local:LocalEnvironment"
_BINDING_ASSETS: dict[str, list[str]] = {
    _BASH_BINDING: ["tools/bash.py"],
}

# Scaffolding always materialized (source-name -> harness-name).
_SCAFFOLD = {
    "scaffold/run.py": "run.py",
    "scaffold/README.md": "README.md",
    "scaffold/gitignore": ".gitignore",
}


def _jinja() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["yaml_block"] = _yaml_block
    return env


class MiniSweAgentAdapter(FrameworkAdapter):
    target = "mini-swe-agent"

    # ====================================================================== #
    #  generate: HDP document -> mini-SWE-agent harness
    # ====================================================================== #
    def generate(self, doc: HDPDoc, out_dir: Path) -> Path:
        out_dir = Path(out_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        self._reject_unsupported(doc)

        attribution: list[dict] = []
        by_tool_name: dict[str, str] = {}

        # 1. Embedded prompt content — re-inlined into the manifest byte-for-byte.
        prompts: dict[str, str] = {}  # component_id -> content
        for _layer, comp in doc.components():
            if comp.type.value != "system_rules":
                continue
            content = doc.read_embedded(comp)
            if content is None:
                continue
            prompts[comp.id] = content
            attribution.append(
                {"id": comp.id, "layer": "context", "type": comp.type.value,
                 "artifact": f"{_MANIFEST_NAME}#{self._field_for(comp.id)}"}
            )

        # 2. Referenced executable — the bash tool, resolved from the target library.
        for _layer, comp in doc.components():
            if comp.type.value != "tool":
                continue
            impl = comp.implementation
            if impl is None:
                continue
            if impl.kind.value == "adapter":
                self._resolve_binding(impl.binding, out_dir)
                if comp.name:
                    by_tool_name[comp.name] = comp.id
                attribution.append(
                    {"id": comp.id, "layer": "tooling", "type": "tool",
                     "artifact": _BINDING_ASSETS[impl.binding][0]}
                )

        # 3. Scaffolding (run.py, README, .gitignore).
        for src, dst in _SCAFFOLD.items():
            shutil.copyfile(_ASSETS / src, out_dir / dst)

        # 4. Assembled manifest (rendered from the typed model + baked runtime boilerplate).
        (out_dir / _MANIFEST_NAME).write_text(self._render_manifest(doc, prompts), encoding="utf-8")

        # 5. Non-runtime attribution sidecar (realizes SPEC §8 without touching behavior).
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
    def _field_for(cid: str) -> str:
        for c, section, key in _PROMPT_FIELDS:
            if c == cid:
                return f"{section}.{key}"
        return cid

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

    def _resolve_binding(self, binding: str | None, out_dir: Path) -> None:
        assets = _BINDING_ASSETS.get(binding or "")
        if assets is None:
            raise ValueError(
                f"mini-SWE-agent adapter cannot resolve ref binding '{binding}' "
                f"(known: {sorted(_BINDING_ASSETS)})"
            )
        for rel in assets:
            dst = out_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_ASSETS / rel, dst)

    def _render_manifest(self, doc: HDPDoc, prompts: dict[str, str]) -> str:
        loop = self._first(doc, "loop")
        params = (loop.params or {}) if loop else {}
        template = _jinja().get_template("mini_swe_agent_manifest.yaml.j2")
        return template.render(
            system_template=prompts.get("system-template"),
            instance_template=prompts.get("instance-template"),
            observation_template=prompts.get("observation-template"),
            format_error_template=prompts.get("format-error-template"),
            step_limit=(loop.max_iterations if loop else 0),
            cost_limit=params.get("cost_limit", 0.0),
        )

    @staticmethod
    def _first(doc: HDPDoc, type_name: str) -> Component | None:
        for _l, comp in doc.components():
            if comp.type.value == type_name:
                return comp
        return None

    # ====================================================================== #
    #  lift: mini-SWE-agent harness -> HDP document
    # ====================================================================== #
    def lift(
        self,
        harness_dir: Path,
        out_dir: Path,
        *,
        meta_id: str | None = None,
        version: str = "1.0.0",
        base_model: str | None = _DEFAULT_MODEL,
        llm: Callable[[str], str] | None = None,
    ) -> HDPDoc:
        from hdp.engine.core.loader import load

        harness_dir = Path(harness_dir).resolve()
        out_dir = Path(out_dir).resolve()
        cfg = _read_yaml(harness_dir / _MANIFEST_NAME)
        agent = cfg.get("agent") or {}
        model = cfg.get("model") or {}
        sections = {"agent": agent, "model": model}

        layers: dict[str, list] = {}
        protected: list[str] = []

        # -- context: each manifest prompt template -> an embedded system_rules component ----
        context: list[dict] = []
        prompt_keys: set[tuple[str, str]] = set()
        for cid, section, key in _PROMPT_FIELDS:
            value = sections.get(section, {}).get(key)
            if not isinstance(value, str):
                continue
            prompt_keys.add((section, key))
            dest = f"./context/{cid}.md"
            out_path = out_dir / dest.lstrip("./")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(value, encoding="utf-8", newline="")
            context.append({"id": cid, "type": "system_rules", "file": dest})
        if context:
            layers["context"] = context
        protected.append("system-template")  # the system prompt is protected by default

        # -- tooling: mini-SWE-agent's single implicit tool is bash --------------------------
        blast = _infer_blast_radius("bash", _BASH_BINDING)
        layers["tooling"] = [{
            "id": "bash", "type": "tool", "name": "bash",
            "description": "Execute a bash command in the environment; the agent's only tool.",
            "blast_radius": blast,
            "implementation": {"kind": "adapter", "binding": _BASH_BINDING},
        }]

        # -- lifecycle: the agent loop (step_limit + residual agent scalars) ------------------
        residual = {
            k: v for k, v in agent.items()
            if ("agent", k) not in prompt_keys and k != "step_limit"
        }
        loop: dict = {"id": "main-loop", "type": "loop",
                      "max_iterations": int(agent.get("step_limit") or 0) or 1}
        if residual:
            loop["params"] = residual
        layers["lifecycle"] = [loop]

        # -- governance: safe defaults (SPEC §5.2), mirroring the NexAU adapter ---------------
        ceiling = blast if blast != "read_only" else "session"
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
                "name": cfg.get("name"),
                "base_model": base_model,
                "targets": [self.target],
            },
            "layers": layers,
            "governance": governance,
        }
        _prune_none(doc_dict)

        if llm is not None:
            doc_dict = self._llm_refine(doc_dict, harness_dir, llm)

        # MUST validate against the typed model before writing (SPEC conformance).
        from hdp.engine.core.loader import HDPManifest
        HDPManifest.model_validate(doc_dict)

        out_dir.mkdir(parents=True, exist_ok=True)
        _write_yaml(out_dir / "hdp.yaml", doc_dict)
        return load(out_dir)

    def _llm_refine(self, doc_dict: dict, harness_dir: Path, llm) -> dict:
        """Extension point: ask an LLM to fill ambiguous fields. The deterministic mapping
        already handles mini-SWE-agent seeds, so this is a no-op unless wired."""
        return doc_dict
