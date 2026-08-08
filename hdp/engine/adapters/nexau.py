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
import re
import shutil
from collections.abc import Callable
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from ruamel.yaml import YAML

from hdp.engine.adapters.base import FrameworkAdapter
from hdp.engine.adapters.uncertain import (
    Uncertain,
    finalize,
    iter_uncertain,
    refine_prompt,
    resolved,
)
from hdp.engine.core.loader import HDPDoc
from hdp.engine.core.models import Component

_BLAST_ORDER = ["read_only", "local", "session", "system", "external"]
_SHELL_TOKENS = ("shell", "bash", "exec", "command", "terminal", "subprocess")
_HOOK_KINDS = ("contract_injection", "skill_retrieval", "action_validation",
               "trajectory_regulation", "before_model", "after_model",
               "before_tool", "after_tool")


def _slug(s: str | None) -> str:
    s = s or ""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", s)  # split camelCase
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "x"


def _one_line(text: str, limit: int = 120) -> str:
    flat = " ".join(text.split())
    if ". " in flat:
        flat = flat.split(". ", 1)[0] + "."
    return flat[:limit]


def _has_env(s: str) -> bool:
    return "${" in s


def _infer_blast_radius(name: str | None, binding: str | None) -> str | Uncertain:
    blob = f"{name or ''} {binding or ''}".lower()
    if any(tok in blob for tok in _SHELL_TOKENS):
        return "system"
    # Nothing in the name/binding signals a blast radius: mark it uncertain (heuristic mode
    # collapses this back to "session"; llm_assisted resolves it).
    return Uncertain(default="session",
                     reason=f"blast_radius not inferable from tool name/binding ({name!r})",
                     candidates=tuple(_BLAST_ORDER))


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


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)


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

    # ====================================================================== #
    #  lift: NexAU harness -> HDP document  (Phase 2, ask #1)
    # ====================================================================== #
    def lift(
        self,
        harness_dir: Path,
        out_dir: Path,
        *,
        meta_id: str | None = None,
        version: str = "1.0.0",
        base_model: str | None = "gpt-5.2",
        llm: Callable[[str], str] | None = None,
    ) -> HDPDoc:
        from hdp.engine.core.loader import load

        harness_dir = Path(harness_dir).resolve()
        out_dir = Path(out_dir).resolve()
        cfg = _read_yaml(harness_dir / "code_agent.yaml")

        layers: dict[str, list] = {}
        protected: list[str] = []

        # -- context: system rules + memory (filename conventions) -------------
        context: list[dict] = []
        sp = cfg.get("system_prompt")
        if isinstance(sp, str) and not sp.startswith("${"):
            sp_src = harness_dir / sp.lstrip("./")
            if sp_src.is_file():
                _copy(sp_src, out_dir / "context/system-rules.md")
                context.append({"id": "system-rules-core", "type": "system_rules",
                                "file": "./context/system-rules.md"})
                protected.append("system-rules-core")
        for fname, cid, scope in (
            ("LongTermMEMORY.md", "long-term-memory", "persistent"),
            ("ShortTermMEMORY.md", "short-term-memory", "session"),
        ):
            src = harness_dir / fname
            if src.is_file():
                dest = f"./context/memory/{cid.replace('-memory', '')}.md"
                _copy(src, out_dir / dest.lstrip("./"))
                context.append({"id": cid, "type": "memory", "scope": scope, "file": dest})
        if context:
            layers["context"] = context

        # -- tooling: one component per tool, description embedded -------------
        tooling: list[dict] = []
        max_blast = "read_only"
        for entry in cfg.get("tools") or []:
            name = entry.get("name")
            yaml_path = entry.get("yaml_path")
            cid = _slug(name)
            comp: dict = {"id": cid, "type": "tool", "name": name}
            if yaml_path and not _has_env(yaml_path):
                tool_src = harness_dir / str(yaml_path).lstrip("./")
                if tool_src.is_file():
                    dest = f"./tooling/{cid}.tool.yaml"
                    _copy(tool_src, out_dir / dest.lstrip("./"))
                    comp["file"] = dest
                    desc = _read_yaml(tool_src).get("description")
                    if isinstance(desc, str):
                        comp["description"] = _one_line(desc)
            blast = _infer_blast_radius(name, entry.get("binding"))
            comp["blast_radius"] = blast          # may be an Uncertain sentinel
            max_blast = _max_blast(max_blast, resolved(blast))  # ceiling uses the conservative value
            if entry.get("binding"):
                comp["implementation"] = {"kind": "adapter", "binding": entry["binding"]}
            tooling.append(comp)
        if tooling:
            layers["tooling"] = tooling

        # -- lifecycle: loop + any middleware ---------------------------------
        lifecycle: list[dict] = [{
            "id": "main-loop", "type": "loop",
            "max_iterations": int(cfg.get("max_iterations", 300)),
            "tool_call_mode": cfg.get("tool_call_mode", "openai"),
        }]
        for idx, mw in enumerate(cfg.get("middlewares") or []):
            imp = mw.get("import")
            # An undeclared hook is a guess-point (mapper has no confident signal): emit the
            # same sentinel. Heuristic mode collapses it to "before_tool"; llm_assisted classifies.
            hook = mw["hook"] if "hook" in mw else Uncertain(
                default="before_tool",
                reason=f"middleware hook not declared for {imp or f'middleware-{idx}'}",
                candidates=_HOOK_KINDS)
            lifecycle.append({
                "id": _slug(imp.split(":")[-1]) if imp else f"middleware-{idx}",
                "type": "middleware",
                "hook": hook,
                "impl": {"kind": "builtin", "import": imp} if imp else {"kind": "builtin"},
            })
        layers["lifecycle"] = lifecycle

        # -- observability: tracers -------------------------------------------
        observability = []
        for tr in cfg.get("tracers") or []:
            imp = tr.get("import")
            if imp:
                observability.append({
                    "id": _slug(imp.split(":")[-1]), "type": "tracer",
                    "ref": {"kind": "builtin", "import": imp},
                })
        if observability:
            layers["observability"] = observability

        # -- governance: safe defaults (SPEC §5.2) ----------------------------
        ceiling = max_blast if max_blast != "read_only" else "session"
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

        # Optional LLM pass resolves only the uncertainty sentinels the mapper emitted; every
        # remaining sentinel then collapses to its conservative default. In heuristic mode (llm
        # is None) _llm_refine is skipped and finalize restores today's output byte-for-byte.
        if llm is not None:
            doc_dict = self._llm_refine(doc_dict, harness_dir, llm)
        finalize(doc_dict)

        # MUST validate against the typed model before writing (SPEC conformance).
        from hdp.engine.core.loader import HDPManifest
        HDPManifest.model_validate(doc_dict)

        out_dir.mkdir(parents=True, exist_ok=True)
        _write_yaml(out_dir / "hdp.yaml", doc_dict)
        return load(out_dir)

    def _llm_refine(self, doc_dict: dict, harness_dir: Path, llm) -> dict:
        """Resolve each ``Uncertain`` sentinel the heuristic mapper left (e.g. uncertain
        blast_radius, middleware hook classification) by asking the LLM one focused question.
        An answer outside the field's candidate set is rejected and the sentinel is left for
        ``finalize`` to collapse to its conservative default — the LLM never widens scope."""
        for container, key, unc in list(iter_uncertain(doc_dict)):
            answer = (llm(refine_prompt(str(key), unc)) or "").strip()
            if answer and (not unc.candidates or answer in unc.candidates):
                container[key] = answer
        return doc_dict

    def agentic_lift(
        self,
        harness_dir: Path,
        out_dir: Path,
        llm: Callable[[str], str],
        *,
        meta_id: str | None = None,
        version: str = "1.0.0",
        base_model: str | None = "gpt-5.2",
    ) -> HDPDoc:
        """Agentic strategy: for a harness the heuristic mapper doesn't recognize, let the LLM
        read the repo broadly and propose the HDP document directly. The proposal MUST validate
        against the typed model before writing — an invalid proposal raises, never coerced."""
        from hdp.engine.adapters.agentic import build_agentic_prompt, parse_agentic_doc
        from hdp.engine.core.loader import HDPManifest, load

        harness_dir = Path(harness_dir).resolve()
        out_dir = Path(out_dir).resolve()
        prompt = build_agentic_prompt(harness_dir, self.target, meta_id, version, base_model)
        doc_dict = parse_agentic_doc(llm(prompt))

        HDPManifest.model_validate(doc_dict)  # raise on an invalid agentic proposal
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_yaml(out_dir / "hdp.yaml", doc_dict)
        return load(out_dir)
