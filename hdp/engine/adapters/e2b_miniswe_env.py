"""Mini-swe-agent compatible environment that proxies bash execution into an existing E2B sandbox.

This lets the `mini` CLI run on the host machine (Lightning) while all bash execution
happens inside the E2B sandbox via the SDK. LLM API calls stay on the host, so they don't
burn the E2B sandbox timeout budget (3600 s cap).

This mirrors the nexau architecture: LLM on host, bash in sandbox.

Usage in mini config YAML (via MSWEA_MINI_CONFIG_PATH):

    environment:
      environment_class: hdp.engine.adapters.e2b_miniswe_env.E2BMinisweEnvironment
      sandbox_id: "abc123..."
      cwd: /app
      timeout: 120
      env:
        PAGER: cat
"""

from __future__ import annotations

import os
import platform
from typing import Any

from pydantic import BaseModel


class E2BMinisweEnvironmentConfig(BaseModel):
    sandbox_id: str
    cwd: str = "/app"
    timeout: int = 120
    env: dict[str, str] = {}


class E2BMinisweEnvironment:
    """Mini-swe Environment protocol implementation backed by an E2B sandbox.

    Connects to an existing sandbox by ID (created externally by harbor) so the same
    sandbox used for task setup and verification is used for agent bash execution.
    """

    def __init__(
        self,
        *,
        sandbox_id: str,
        cwd: str = "/app",
        timeout: int = 120,
        env: dict[str, str] | None = None,
        **_kwargs: Any,
    ) -> None:
        self.config = E2BMinisweEnvironmentConfig(
            sandbox_id=sandbox_id,
            cwd=cwd,
            timeout=timeout,
            env=env or {},
        )
        self._sandbox: Any | None = None

    def _get_sandbox(self) -> Any:
        if self._sandbox is None:
            from e2b import Sandbox  # type: ignore[import]
            self._sandbox = Sandbox.connect(self.config.sandbox_id)
        return self._sandbox

    def execute(self, action: dict[str, Any], cwd: str = "") -> dict[str, Any]:
        command = action.get("command", "")
        working_dir = cwd or self.config.cwd

        sandbox = self._get_sandbox()

        try:
            result = sandbox.commands.run(
                cmd=command,
                cwd=working_dir,
                envs=self.config.env or None,
                timeout=float(self.config.timeout),
                user="root",
            )
            output_text = (result.stdout or "") + (result.stderr or "")
            output: dict[str, Any] = {
                "output": output_text,
                "returncode": result.exit_code,
                "exception_info": "",
            }
        except Exception as exc:
            output = {
                "output": "",
                "returncode": -1,
                "exception_info": f"E2B execution error: {exc}",
            }

        self._check_finished(output)
        return output

    def _check_finished(self, output: dict[str, Any]) -> None:
        from minisweagent.exceptions import Submitted  # type: ignore[import]

        if output.get("returncode") != 0:
            return
        lines = output.get("output", "").lstrip().splitlines(keepends=True)
        if not lines:
            return
        first_line = lines[0].strip()
        if first_line in ("TASK_COMPLETE", "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"):
            submission = "".join(lines[1:])
            raise Submitted(
                {
                    "role": "exit",
                    "content": submission,
                    "extra": {"exit_status": "Submitted", "submission": submission},
                }
            )

    def get_template_vars(self, **kwargs: Any) -> dict[str, Any]:
        return {
            **self.config.model_dump(),
            **platform.uname()._asdict(),
            **os.environ,
            **kwargs,
        }

    def serialize(self) -> dict[str, Any]:
        return {
            "info": {
                "config": {
                    "environment": self.config.model_dump(mode="json"),
                    "environment_type": (
                        f"{self.__class__.__module__}.{self.__class__.__name__}"
                    ),
                }
            }
        }
