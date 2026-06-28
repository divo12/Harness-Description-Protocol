"""hdp.engine.adapters.mini_swe_agent_harbor — harbor agents for mini-swe-agent.

Two classes live here:

MiniSweAgentHDP
    Runs the HDP-generated manifest via ``mini -c`` inside the E2B sandbox (original behavior).

MiniSweAgentE2BLocal
    Nexau-style architecture: runs ``mini`` on the host (Lightning), routing bash execution into
    the E2B sandbox via the E2B SDK. LLM calls stay on the host, so they don't consume the E2B
    sandbox timeout budget (3600 s cap). Use this for xhigh reasoning where each step takes
    ~9 min — running in-sandbox would exhaust the budget in ~6 steps.

    Wiring::

        harbor run --agent mini-swe-agent \\
            --agent-import-path "hdp.engine.adapters.mini_swe_agent_harbor:MiniSweAgentE2BLocal" \\
            --ak "reasoning_effort=xhigh" ...
"""
from __future__ import annotations

import asyncio
import os
import shlex
from pathlib import Path

import yaml
from harbor.agents.installed.base import ExecInput
from harbor.agents.installed.mini_swe_agent import MiniSweAgent
from harbor.utils.templating import render_prompt_template

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


# ---------------------------------------------------------------------------
# Mini-SWE-Agent on Lightning, bash proxied into E2B (nexau-style)
# ---------------------------------------------------------------------------

_E2B_MINI_SYSTEM_TEMPLATE = (
    "You are a helpful assistant that can interact with a computer shell to solve tasks."
)

_E2B_MINI_INSTANCE_TEMPLATE = """\
<task>
{{task}}
</task>

<instructions>
## Overview

You're a software engineer interacting with a computer shell. Complete the task described above.
The working environment is already set up.

For each response:
1. Include a THOUGHT section explaining your reasoning
2. Provide bash tool calls to execute

## Important

- Work in whatever directory the task requires (check with pwd and ls first)
- Actually run your code, do not just write files without executing them
- Install any missing dependencies without asking
- When installing Python packages use: python3 -m pip install <pkg> --break-system-packages
- Verify your solution works before finishing

## Command Execution

Each response MUST include AT LEAST ONE bash tool call.
Directory changes are not persistent between responses.
Prefix commands with cd /path && as needed.

## Completion

When you have finished the task, run:
```bash
echo TASK_COMPLETE
```

Do NOT create a git patch. Do NOT run git diff.
Just complete the task and run echo TASK_COMPLETE.
</instructions>"""

_E2B_MINI_ENV_VARS = {
    "PAGER": "cat",
    "MANPAGER": "cat",
    "LESS": "-R",
    "PIP_PROGRESS_BAR": "off",
    "TQDM_DISABLE": "1",
    "BASH_ENV": "/root/.bashrc",
}


class MiniSweAgentE2BLocal(MiniSweAgent):
    """Run ``mini`` on the host machine, routing all bash execution into an existing E2B sandbox.

    When the harbor E2B environment is detected, this class:
    1. Reads the sandbox ID from the already-started harbor sandbox.
    2. Writes a mini config YAML that sets environment_class to E2BMinisweEnvironment with
       that sandbox ID, so the ``mini`` process never runs bash locally.
    3. Runs ``mini`` as a local subprocess (LLM calls happen on Lightning, within budget).
    4. Saves the trajectory to a local path and delegates context extraction to the parent.

    Falls back to the original in-sandbox behavior for non-E2B environments.
    """

    async def run(
        self,
        instruction: str,
        environment: object,
        context: object,
    ) -> None:
        from harbor.environments.e2b import E2BEnvironment as HarborE2BEnvironment  # type: ignore[import]

        if not isinstance(environment, HarborE2BEnvironment) or environment._sandbox is None:  # type: ignore[union-attr]
            return await super().run(instruction, environment, context)

        sandbox_id: str = environment._sandbox.sandbox_id  # type: ignore[union-attr]

        self.logs_dir.mkdir(parents=True, exist_ok=True)
        local_traj = self.logs_dir / "mini-swe-agent.trajectory.json"

        config: dict = {
            "agent": {
                "system_template": _E2B_MINI_SYSTEM_TEMPLATE,
                "instance_template": _E2B_MINI_INSTANCE_TEMPLATE,
                "step_limit": 250,
                "cost_limit": 20.0,
            },
            "environment": {
                "environment_class": (
                    "hdp.engine.adapters.e2b_miniswe_env.E2BMinisweEnvironment"
                ),
                "sandbox_id": sandbox_id,
                "cwd": "/app",
                "timeout": 120,
                "env": _E2B_MINI_ENV_VARS,
            },
        }
        if self._reasoning_effort:
            config["model"] = {
                "model_kwargs": {
                    "reasoning_effort": self._reasoning_effort,
                    "drop_params": True,
                }
            }

        config_path = self.logs_dir / "mini-e2b-config.yaml"
        config_path.write_text(
            yaml.dump(config, sort_keys=False, allow_unicode=True, default_flow_style=False)
        )

        # Build subprocess env: inherit everything + inject API credentials + PYTHONPATH.
        proc_env = dict(os.environ)
        proc_env["MSWEA_CONFIGURED"] = "true"
        proc_env["MSWEA_SILENT_STARTUP"] = "1"
        proc_env["MSWEA_MINI_CONFIG_PATH"] = str(config_path)

        # Ensure uv tool bins (where mini lives) are in PATH even if harbor's venv
        # shadows ~/.local/bin.
        uv_tool_bin = str(Path.home() / ".local" / "bin")
        existing_path = proc_env.get("PATH", "")
        if uv_tool_bin not in existing_path:
            proc_env["PATH"] = f"{uv_tool_bin}:{existing_path}"

        if "MSWEA_API_KEY" in os.environ:
            proc_env["OPENAI_API_KEY"] = os.environ["MSWEA_API_KEY"]
        if "OPENAI_API_BASE" in os.environ:
            proc_env["OPENAI_BASE_URL"] = os.environ["OPENAI_API_BASE"]
            proc_env["OPENAI_API_BASE"] = os.environ["OPENAI_API_BASE"]

        # Ensure our hdp package is importable from the mini subprocess.
        repo_root = str(Path(__file__).parent.parent.parent.parent)
        existing = proc_env.get("PYTHONPATH", "")
        proc_env["PYTHONPATH"] = f"{repo_root}:{existing}" if existing else repo_root

        rendered = (
            render_prompt_template(self._prompt_template_path, instruction)
            if self._prompt_template_path
            else instruction
        )

        mini_cmd = [
            "mini",
            "-m", self.model_name,
            "-t", rendered,
            "-y",
            "-o", str(local_traj),
            "-l", "0",
            "--exit-immediately",
        ]

        command_dir = self.logs_dir / "command-0"
        command_dir.mkdir(parents=True, exist_ok=True)
        (command_dir / "command.txt").write_text(shlex.join(mini_cmd))

        proc = await asyncio.create_subprocess_exec(
            *mini_cmd,
            env=proc_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout_bytes, _ = await proc.communicate()
        output_text = stdout_bytes.decode("utf-8", errors="replace")

        (command_dir / "stdout.txt").write_text(output_text)
        (command_dir / "return-code.txt").write_text(str(proc.returncode))
        (self.logs_dir / "mini-swe-agent.txt").write_text(output_text)

        self.populate_context_post_run(context)
