---
name: hdp-evolution-guide
description: Evolve a coding agent by editing its HDP document (not framework files). Use during treatment-arm evolution. Covers the ETCLOVG layers, the closed operator vocabulary, the governance contract, and the mandatory manifest-before-edit discipline.
---

# HDP Evolution Guide — Evolve the Document, Not the Framework

You improve the agent by editing its **HDP document** — a vendor-neutral, source-of-truth
description of the harness. A generator compiles the document into a runnable harness; you never
touch framework files directly. This indirection is the point: every edit is typed, governed, and
falsifiable.

Your working directory is an `*.hdp` directory:

```
<name>.hdp/
├── hdp.yaml                  # the manifest — ETCLOVG layers + typed components + governance
├── context/                  # embedded: system-rules.md, memory/*.md, skills/<id>/SKILL.md
├── tooling/*.tool.yaml        # embedded tool DESCRIPTIONS (code is referenced, not here)
├── verification/  governance/ # verifier contracts, policies
└── evolution/manifests/*.json # YOUR change manifests (one per iteration)
```

## The seven layers (ETCLOVG) and what lives in each

| `layers.` key | Put here | Typical edits |
|---|---|---|
| `execution` | sandbox profile | rarely (usually read-only) |
| `tooling` | `tool` — description (embedded) + `implementation` (a `ref`) | add a tool, **narrow** a tool's schema, sharpen a description |
| `context` | `system_rules`, `skill`, `memory` | add a skill, append a memory lesson, tighten the system rules |
| `lifecycle` | `loop`, `middleware`, `sub_agent` | add a middleware hook, a sub-agent |
| `observability` | `tracer` | rarely |
| `verification` | `verifier` contracts | usually read-only |
| `governance` | `policy` + blast-radius | usually read-only |

**Embed vs reference:** embed declarative text (rules, tool *descriptions*, skills, memory,
policies) directly as files; *reference* executable code via a `ref` (`kind: mcp|package|adapter|
builtin`). Do not paste runtime code into the document.

## The operator vocabulary (closed — use exactly these)

Every edit is one of five operators. Pick the **most specific** one; `guard` checks that your
declared operator matches the change you actually made.

| Operator | Use when | Maps to a document change |
|---|---|---|
| `add` | introducing a new component | a new entry under a `layers.` list |
| `update` | changing an existing component's content/fields | edit a component or its embedded file |
| `remove` | deleting a component (adaptive simplification — a first-class move) | drop a `layers.` entry |
| `narrow` | restricting a component's surface (e.g. a tool schema, a permission) | a constraining `update` |
| `gate` | adding a verification/validation gate to an action or finalization path | add a verifier/middleware that blocks |

Prefer `remove`/`narrow`/`gate` when evidence says the agent has *too much* freedom — over-broad
tools and missing checks are common failure causes.

## The governance contract — read it before you edit

`hdp.yaml`'s `governance.evolution` declares what you may touch:

```yaml
governance:
  evolution:
    editable:  [context, tooling, lifecycle]   # you MAY edit these layers/ids
    read_only: [verification, governance, execution]   # you MUST NOT
    protected: [system-rules-core]             # you MUST NOT remove or gut these
```

Hard rules (`guard` will roll back any violation, wasting the iteration):
1. Only edit layers/components in `editable`.
2. Never edit anything in `read_only` (verification, governance, execution by default).
3. Never `remove` a `protected` component.
4. Never edit the `governance` block itself (you cannot widen your own permissions).
5. Model config (model, temperature, reasoning) is **not** in the document and is off-limits.

## Manifest-before-edit (mandatory)

Before changing any file, append your declaration to `evolution/manifests/<NNN>.json`. **An edit
without a matching, valid manifest entry is rejected.** One entry per change:

```json
{
  "hdp": "0.1",
  "iteration": 7,
  "changes": [{
    "change_id": "chg-7",
    "operator": "narrow",
    "component_id": "run-shell",
    "layer": "tooling",
    "failure_evidence": "trace t-042: agent deleted a verified output during cleanup",
    "root_cause": "the shell tool permits destructive ops on accepted deliverables",
    "repair_spec": {
      "editable_resources": ["tooling/run-shell.tool.yaml"],
      "forbidden_artifacts": ["verification/*", "governance/*"],
      "required_behaviors": ["non-destructive shell behavior unchanged"],
      "validation_criteria": ["t-042 flips to pass", "no regression on the suite"]
    },
    "prediction": {
      "expected_fixes": ["t-042"],
      "at_risk_regressions": ["t-017"],
      "rationale": "the guard only blocks deletion of verifier-accepted paths"
    }
  }]
}
```

- `prediction` is a **falsifiable contract**: name the exact task IDs you expect to flip to pass,
  and the ones you think might break. The next iteration grades you on it. Be honest about
  `at_risk_regressions` — unpredicted regressions are the loop's worst failure mode.
- `repair_spec.validation_criteria` must include "no regression on the suite": changes are judged
  as **system** changes, never per-component (harness layers couple).

## Evidence you are given each iteration

- **Per-task analysis** (`analysis/detail/<task>.md`) + **overview** (`analysis/overview.md`) — the
  distilled root causes of this round's failures.
- **Attestation** of your *previous* edits — each prior change now carries a verdict
  (`effective` / `partial` / `ineffective` / `harmful`) and the tasks it actually fixed or broke.
  If a change was `ineffective` or `harmful`, do not repeat the approach; consider `remove`.

## Workflow each iteration

1. Read the overview + per-task analyses; read the attestation of your last edits.
2. Pick the single highest-leverage failure pattern. Choose the **most specific operator**.
3. Write the manifest entry (operator + evidence + falsifiable prediction) **first**.
4. Make exactly that edit to the document (respecting governance).
5. Validate: `python hdp/validator/hdp_validate.py <doc.hdp>` — fix any error before finishing.
6. Stop. The engine gens, evals, and attests; you will see the verdict next round.

Make **one well-justified edit per iteration**. Smaller, attributable changes beat sweeping ones —
they are easier to grade, and the loop converges on what actually works.
