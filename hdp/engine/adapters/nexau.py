"""hdp.engine.adapters.nexau — compile an HDP document into a NexAU harness.

Three faithful sources, per SPEC §2.1 (embed vs. reference):
  * embedded content (system rules, tool descriptions, memory) → copied byte-for-byte;
  * the assembled manifest ``code_agent.yaml`` → rendered from the typed model via Jinja2
    plus NexAU's fixed ``llm_config`` (model config is NOT an HDP component, SPEC §5.2);
  * referenced executables (tool implementations, package scaffolding) → resolved from this
    adapter's bundled NexAU target library (``nexau_assets/``), keyed by the ``ref`` binding.

A non-runtime ``hdp_attribution.json`` sidecar records component_id ↔ harness-artifact so
the §8 trace-attribution convention is realizable (Phase 4 attest joins it against the
trace) without altering harness behavior — keeping the generator score-faithful.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from hdp.engine.adapters.base import FrameworkAdapter
from hdp.engine.core.loader import HDPDoc
from hdp.engine.core.models import Component

_ASSETS = Path(__file__).resolve().parent / "nexau_assets"
_TEMPLATES = Path(__file__).resolve().parents[1] / "gen" / "templates"

# NexAU target defaults that are not HDP components (model/runtime boilerplate).
_AGENT_NAME = "nexau_code_agent"
_REGISTRY_KEY = "code_agent"
_MAX_CONTEXT_TOKENS = 200000
_SYSTEM_PROMPT_TYPE = "jinja"

# Adapter `ref` bindings → the asset files (relative to nexau_assets/) that realize them.
# A generator MUST fail, not skip, on a binding it cannot resolve (SPEC §10).
_BINDING_ASSETS: dict[str, list[str]] = {
    "tools.shell_tools:run_shell_command": [
        "tools/__init__.py",
        "tools/shell_tools/__init__.py",
        "tools/shell_tools/run_shell_command.py",
    ],
}

# Scaffolding always materialized (source-name -> harness-name).
_SCAFFOLD = {
    "scaffold/start.py": "start.py",
    "scaffold/README.md": "README.md",
    "scaffold/gitignore": ".gitignore",
}


def _jinja() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


class NexAUAdapter(FrameworkAdapter):
    target = "nexau"

    # -- component -> NexAU file path (embedded content destinations) ----------
    def _embedded_dest(self, layer: str, comp: Component) -> str:
        if comp.type.value == "system_rules":
            return "systemprompt.md"
        if comp.type.value == "memory":
            scope = comp.scope.value if comp.scope else "session"
            return "LongTermMEMORY.md" if scope == "persistent" else "ShortTermMEMORY.md"
        if comp.type.value == "tool":
            return f"tool_descriptions/{comp.name}.tool.yaml"
        if comp.type.value == "skill":
            return f"skills/{comp.id}/SKILL.md"
        raise NotImplementedError(
            f"NexAU adapter has no embedded destination for component '{comp.id}' "
            f"(type={comp.type.value})"
        )

    def generate(self, doc: HDPDoc, out_dir: Path) -> Path:
        out_dir = Path(out_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        self._reject_unsupported(doc)

        attribution: list[dict] = []
        by_tool_name: dict[str, str] = {}
        by_file: dict[str, str] = {}

        # 1. Embedded content — byte-for-byte.
        for layer, comp in doc.components():
            content = doc.read_embedded(comp)
            if content is None:
                continue
            dest = self._embedded_dest(layer, comp)
            dest_path = out_dir / dest
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_text(content, encoding="utf-8", newline="")
            by_file[dest] = comp.id
            attribution.append(
                {"id": comp.id, "layer": layer, "type": comp.type.value, "artifact": dest}
            )

        # 2. Referenced executables — resolved from the adapter's target library.
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

        # 3. Scaffolding (start.py, README, .gitignore) + entry registry (nexau.json).
        for src, dst in _SCAFFOLD.items():
            shutil.copyfile(_ASSETS / src, out_dir / dst)
        (out_dir / "nexau.json").write_text(
            json.dumps({"agents": {_REGISTRY_KEY: "code_agent.yaml"}}, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )

        # 4. Assembled manifest code_agent.yaml (rendered from the typed model).
        (out_dir / "code_agent.yaml").write_text(self._render_agent(doc), encoding="utf-8")

        # 5. Non-runtime attribution sidecar (realizes SPEC §8 without touching behavior).
        sidecar = {
            "hdp": doc.model.hdp,
            "doc": {"id": doc.model.meta.id, "version": doc.model.meta.version},
            "by_tool_name": by_tool_name,
            "by_file": by_file,
            "components": attribution,
        }
        (out_dir / "hdp_attribution.json").write_text(
            json.dumps(sidecar, indent=2) + "\n", encoding="utf-8"
        )
        return out_dir

    # -- helpers ----------------------------------------------------------------
    def _reject_unsupported(self, doc: HDPDoc) -> None:
        """Fail loud on components this adapter cannot faithfully produce (no silent skip)."""
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
                f"NexAU adapter cannot resolve ref binding '{binding}' "
                f"(known: {sorted(_BINDING_ASSETS)})"
            )
        for rel in assets:
            dst = out_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_ASSETS / rel, dst)

    def _render_agent(self, doc: HDPDoc) -> str:
        loop = self._first(doc, "loop")
        tools = [
            {
                "name": c.name,
                "yaml_path": f"./tool_descriptions/{c.name}.tool.yaml",
                "binding": c.implementation.binding if c.implementation else None,
            }
            for _l, c in doc.components() if c.type.value == "tool"
        ]
        tracers = [
            {"import": c.ref.import_ if c.ref else None}
            for _l, c in doc.components() if c.type.value == "tracer"
        ]
        sys_rules = self._first(doc, "system_rules")
        template = _jinja().get_template("code_agent.yaml.j2")
        return template.render(
            agent_name=_AGENT_NAME,
            max_context_tokens=_MAX_CONTEXT_TOKENS,
            system_prompt="./systemprompt.md" if sys_rules else None,
            system_prompt_type=_SYSTEM_PROMPT_TYPE,
            tool_call_mode=(loop.tool_call_mode if loop else "openai"),
            max_iterations=(loop.max_iterations if loop else 300),
            tools=tools,
            tracers=tracers,
        )

    @staticmethod
    def _first(doc: HDPDoc, type_name: str) -> Component | None:
        for _l, comp in doc.components():
            if comp.type.value == type_name:
                return comp
        return None

    def lift(self, harness_dir: Path) -> HDPDoc:  # Phase 2
        raise NotImplementedError("NexAUAdapter.lift lands in Phase 2 (lift)")
