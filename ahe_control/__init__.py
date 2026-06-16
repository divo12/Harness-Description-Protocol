"""AHE control: the Agentic Harness Engineering evolve loop (the A/B *control* arm).

Run it directly with ``python -m ahe_control.evolve --config <cfg>``. The HDP engine reuses a
narrow slice of this module (config loading, harbor eval, pass@1 stats, the agent-debugger pass)
via explicit ``from ahe_control.evolve import ...`` at its seams — the one-way HDP -> AHE edge.
"""
