"""hdp.engine.adapters.openharness_harbor — harbor agent for the OpenHarness CLI (`oh`).

harbor has no built-in OpenHarness agent, so this is a from-scratch installed agent: it pip-installs
``openharness-ai`` in the sandbox and runs ``oh -p`` headless on the task, applying the HDP-generated
harness (the evolved ``settings.json`` — system prompt, permission policy, turn limit). The Azure
gpt-5.x endpoint is OpenAI-compatible, so the model is wired via ``--api-format openai`` + the
``--base-url`` / ``--api-key`` flags (no interactive ``oh setup`` needed).

Wiring (harbor config / eval seam)::

    --agent-import-path hdp.engine.adapters.openharness_harbor:OpenHarnessHDP
    --ak config_dir=<the gen'd harness dir (has .openharness/settings.json + CLAUDE.md)>
    --model openai/gpt-5.2          # provider/model; only the model id is passed to `oh -m`

Credentials are read from the harbor process env (``OPENAI_API_KEY`` / ``OPENAI_API_BASE``) and
forwarded into the sandbox, so the literal key never appears in the command string.
"""
from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from harbor.agents.installed.base import BaseInstalledAgent, ExecInput
from harbor.models.agent.context import AgentContext

_SANDBOX_SETTINGS_PATH = "/tmp/hdp_openharness_settings.json"
_SETTINGS_MARKER = "HDP_OPENHARNESS_SETTINGS_EOF"
# `oh` already runs *inside* the E2B sandbox; its own sandbox section would try to nest a container.
_DROP_SETTINGS_KEYS = ("sandbox",)

# Injected via --append-system-prompt so the model never pauses to ask for confirmation.
_HEADLESS_SYSTEM_PROMPT = (
    "You are running fully headless — there is NO human available to respond. "
    "NEVER ask for confirmation, permission, or user input of any kind. "
    "NEVER say 'please confirm', 'is it OK', 'should I', or similar. "
    "Always install required tools and packages and proceed immediately. "
    "When installing Python packages use `python3 -m pip install <pkg> --break-system-packages` "
    "so they are available to all processes in this environment. "
    "Complete the task autonomously without any human interaction."
)


class OpenHarnessHDP(BaseInstalledAgent):
    """Runs the OpenHarness ``oh`` CLI headless on a task, applying the HDP-generated config."""

    def __init__(
        self,
        logs_dir: Path,
        prompt_template_path: Path | str | None = None,
        version: str | None = None,
        config_dir: str | None = None,
        max_turns: int = 100,
        effort: str = "xhigh",
        *args: object,
        **kwargs: object,
    ) -> None:
        super().__init__(logs_dir, prompt_template_path, version, *args, **kwargs)
        self._config_dir = config_dir
        self._max_turns = int(max_turns)
        self._effort = effort

    @staticmethod
    def name() -> str:
        return "openharness-hdp"

    @property
    def _install_agent_template_path(self) -> Path:
        return Path(__file__).parent / "install-openharness.sh.j2"

    def populate_context_post_run(self, context: AgentContext) -> None:
        pass  # token/cost accounting is best-effort; `oh` headless output is captured to the log.

    def _settings_setup(self) -> tuple[str, str]:
        """(shell-prefix that writes the cleaned settings into the sandbox, --settings flag)."""
        if not self._config_dir:
            return "", ""
        settings_file = Path(self._config_dir) / ".openharness" / "settings.json"
        if not settings_file.exists():
            return "", ""
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        for key in _DROP_SETTINGS_KEYS:
            settings.pop(key, None)
        body = json.dumps(settings, indent=2)
        prefix = f"cat > {_SANDBOX_SETTINGS_PATH} <<'{_SETTINGS_MARKER}'\n{body}\n{_SETTINGS_MARKER}\n"
        return prefix, f"--settings {_SANDBOX_SETTINGS_PATH} "

    def create_run_agent_commands(self, instruction: str) -> list[ExecInput]:
        model = (self.model_name or "gpt-5.2").split("/")[-1]
        key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OH_API_KEY", "")
        base = os.environ.get("OPENAI_API_BASE") or os.environ.get("OH_BASE_URL", "")
        # `oh` reads OPENAI_API_KEY / OPENAI_BASE_URL natively → set them in the sandbox env so we
        # don't depend on the shell expanding a flag (a shell-expanded --api-key silently emptied →
        # oh hung). Disable oh's own (docker) sandbox: we're already inside the E2B sandbox. Force
        # headless git so tool commands (merge/commit/rebase) never block on an editor or prompt.
        env = {
            "OPENAI_API_KEY": key,
            "OPENAI_BASE_URL": base,
            "OPENHARNESS_SANDBOX_ENABLED": "false",
            "GIT_EDITOR": "true",
            "GIT_TERMINAL_PROMPT": "0",
            "EDITOR": "true",
            "PAGER": "cat",
        }

        setup, settings_flag = self._settings_setup()
        effort_flag = f"--effort {shlex.quote(self._effort)} " if self._effort else ""
        headless_flag = f"--append-system-prompt {shlex.quote(_HEADLESS_SYSTEM_PROMPT)} "
        run = (
            f"oh -p {shlex.quote(instruction)} --base-url \"$OPENAI_BASE_URL\" --api-format openai "
            f"-m {model} {effort_flag}{settings_flag}--dangerously-skip-permissions "
            f"--max-turns {self._max_turns} --output-format text "
            f"{headless_flag}"
            # `</dev/null` keeps oh headless; tee captures the final answer to the agent log.
            f"</dev/null 2>&1 | tee /logs/agent/openharness.txt"
        )
        return [ExecInput(command=setup + run, env=env)]
