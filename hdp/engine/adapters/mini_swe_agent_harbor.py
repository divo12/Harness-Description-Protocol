"""hdp.engine.adapters.mini_swe_agent_harbor — harbor agent that runs the HDP-*generated*
mini-swe-agent manifest, instead of vanilla mini.

harbor's built-in ``mini-swe-agent`` agent runs ``mini -m <model> -t <task>`` — it never reads a
config file, so an HDP-evolved manifest (system/instance templates, step limit, tool wiring) is
ignored. This thin subclass injects the generated ``mini_swe_agent.yaml`` into the sandbox and
adds ``-c <manifest>``, so the evolved *harness* is the thing actually exercised on Terminal-Bench.

Wiring (set in the harbor config / eval seam)::

    --agent-import-path hdp.engine.adapters.mini_swe_agent_harbor:MiniSweAgentHDP
    --ak manifest_path=<path to the generated mini_swe_agent.yaml>

Everything else (model/API env, trajectory capture, ATIF conversion) is inherited unchanged, so
this stays a faithful drop-in for the built-in agent — the only difference is the ``-c`` flag.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from harbor.agents.installed.base import ExecInput
from harbor.agents.installed.mini_swe_agent import MiniSweAgent

# Where the manifest lands inside the sandbox before `mini -c` reads it.
_SANDBOX_MANIFEST_PATH = "/tmp/hdp_mini_swe_agent.yaml"
_HEREDOC_MARKER = "HDP_MINI_SWE_MANIFEST_EOF"

# harbor runs the `mini` CLI, which hard-codes InteractiveAgentConfig (run/mini.py). That config
# accepts system_template / instance_template / step_limit / cost_limit but NOT these two — they
# belong to mini's *batch* DefaultAgent. The HDP harness may model them for portability to other
# runtimes; strip them here so `mini -c` doesn't reject the manifest.
_AGENT_KEYS_UNSUPPORTED_BY_MINI = ("observation_template", "format_error_template")


class MiniSweAgentHDP(MiniSweAgent):
    """mini-swe-agent harbor agent that runs the HDP-generated manifest via ``mini -c``."""

    def __init__(self, *args: object, manifest_path: str | None = None, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._manifest_path = manifest_path

    @staticmethod
    def _manifest_for_mini(manifest_text: str) -> str:
        """Drop agent keys the `mini` CLI's InteractiveAgentConfig rejects, returning YAML text."""
        doc = yaml.safe_load(manifest_text)
        agent = (doc or {}).get("agent")
        if not isinstance(agent, dict):
            return manifest_text
        if not any(k in agent for k in _AGENT_KEYS_UNSUPPORTED_BY_MINI):
            return manifest_text
        for key in _AGENT_KEYS_UNSUPPORTED_BY_MINI:
            agent.pop(key, None)
        return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, width=100000)

    def create_run_agent_commands(self, instruction: str) -> list[ExecInput]:
        commands = super().create_run_agent_commands(instruction)
        if not self._manifest_path or not commands:
            return commands  # no manifest supplied → behave exactly like the built-in agent

        manifest = self._manifest_for_mini(Path(self._manifest_path).read_text(encoding="utf-8"))
        base = commands[0]
        # Inject `-c <manifest>` ahead of the model flag the parent emitted.
        run = base.command.replace("mini -m ", f"mini -c {_SANDBOX_MANIFEST_PATH} -m ", 1)
        command = (
            f"cat > {_SANDBOX_MANIFEST_PATH} <<'{_HEREDOC_MARKER}'\n"
            f"{manifest}\n"
            f"{_HEREDOC_MARKER}\n"
            f"{run}"
        )
        return [ExecInput(command=command, cwd=base.cwd, env=base.env, timeout_sec=base.timeout_sec)]
