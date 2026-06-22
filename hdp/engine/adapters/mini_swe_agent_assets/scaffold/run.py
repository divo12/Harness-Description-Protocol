#!/usr/bin/env python3
"""Entry point for this mini-swe-agent harness (the analogue of NexAU's start.py).

Loads the bundled manifest and runs mini-SWE-agent's default control loop against a
single task. Requires `pip install mini-swe-agent` (the harness references the package
by dotted path, exactly as the real backend does).
"""

from pathlib import Path

from minisweagent.agents.default import DefaultAgent
from minisweagent.config import get_config_from_spec
from minisweagent.environments import get_environment
from minisweagent.models import get_model


def main() -> None:
    manifest = Path(__file__).parent / "mini_swe_agent.yaml"
    config = get_config_from_spec(str(manifest))
    model = get_model(config=config.get("model", {}))
    env = get_environment(config.get("environment", {}), default_type="local")
    agent = DefaultAgent(model, env, **config.get("agent", {}))
    task = input("Enter your task: ")
    print(agent.run(task))


if __name__ == "__main__":
    main()
