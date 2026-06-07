# CLAUDE.md

This file provides strict operational guidelines and architectural context for Claude Code (claude.ai/code) or any agentic system working within this repository. 

**Project:** Agentic Harness Engineering (AHE)
**Core Goal:** Automate the evolution of coding agent harnesses via an `evaluate → analyze → improve` loop inside E2B sandboxes.

---

## 1. Core Agent Directives (Behavior & Execution)

**Tradeoff:** Bias toward caution, evidence, and simplicity over speed or speculative features.

* **Evidence-Based Editing:** Every edit to the harness MUST be justified by failure evidence, root cause analysis, a targeted fix, and a predicted impact. Do not guess.
* **Surgical Changes Only:** Touch only what you must. Do not refactor adjacent code, change existing styles, or add speculative "flexibility." Clean up orphaned variables/imports caused by your changes.
* **Simplicity First:** Write the minimum code required to solve the problem. If you write 200 lines and it could be 50, rewrite it. 
* **Goal-Driven Verification:** Transform tasks into verifiable goals. Do not guess if it works; verify it. 
    * *Workflow:* `1. [Plan Step] → 2. [Execute] → 3. [Verify via tests/scripts]`
* **Stop on Ambiguity:** If a requirement is unclear, multiple interpretations exist, or a simpler approach is obvious—stop and ask for clarification.

---

## 2. NexAU Framework & Editing Boundaries

You will primarily edit the harness components of the `code_agent_simple`. You are restricted to making changes within the `workspace/` directory, which follows a strict 7-component orthogonal model:

1.  `systemprompt.md`
2.  `code_agent.yaml`
3.  `tool_descriptions/`
4.  `tools/`
5.  `middleware/`
6.  `skills/`
7.  `sub_agents/`
* **Memory:** `LongTermMEMORY.md` (Maintains state across evolutions)

*Note: The base LLM is fixed. You are evolving the components above, NOT the underlying model.*

---

## 3. System Architecture

### The Three Agents
* **`agents/code_agent_simple/`**: The target agent being evaluated and evolved. Built on NexAU framework.
* **`agents/evolve_agent/`**: The meta-agent analyzing traces and proposing evidence-backed edits. Utilizes its own middleware, skills (`agent-debugger-cli`), and tools.
* **`agents/explore_agent/`**: Explores upstream source/docs pre-iteration 1 to generate knowledge skills for the evolve agent.

### The Loop (`evolve.py`)
The orchestrator runs iterations in `runs/iteration_NNN/`.
* `input/`: Workspace from the previous iteration.
* `evolve/`: Proposed changes for the next iteration.
* *Termination:* Reaching `target_pass_rate` or `max_iterations`.

### Trace & Observability Flow
Each evaluation produces per-task logs used for root-cause analysis:
* `agent/nexau_in_memory_tracer.cleaned.json` (Full trace, normalized by `trace_converter.py`)
* `agent/nexau.txt` (Runtime log)
* `verifier/reward.txt` (Pass/Fail signal)

### Configuration System
* **Base:** `configs/base.yaml` (Shared defaults).
* **Experiments:** `configs/experiments/<config>.yaml` (Inherits via `_base: ../base.yaml` and overrides fields).
* **Environment:** `${ENV_VAR}` references in YAML are dynamically substituted from `.env`.

---

## 4. Environment & Execution Toolkit

### Prerequisites
* Python >= 3.13
* `uv` (Package manager)
* `tmux`

### Environment Variables (`.env`)
* **Required:** `LLM_API_KEY`, `LLM_BASE_URL`, `E2B_API_KEY`
* **Optional:** `ADB_LLM_*` (Agent Debugger), `SERPER_API_KEY` (Web search), `FEISHU_WEBHOOK` (Notifications), `LANGFUSE_*` (Observability).

### Common Commands

**Setup & Environment:**
```bash
uv sync  # Install dependencies