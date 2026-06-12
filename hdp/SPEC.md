# Harness Definition Protocol (HDP) — Specification v0.1

**Status:** Draft. **Date:** 2026-06-12.

HDP is a vendor-neutral, machine-readable format that **defines** an LLM-agent harness — the
model-external layer that turns model calls into bounded, stateful, tool-mediated task execution.
An HDP document is the *source of truth*: generators compile it into a concrete harness for a
target backend, and evolution loops modify the HDP document itself through a standardized,
governed edit interface.

The key words MUST, MUST NOT, SHOULD, and MAY are to be interpreted as in RFC 2119.

---

## 1. Motivation and positioning

Harness quality, not model capability, is increasingly the binding constraint on agent
reliability (Bölük 2026; *Agent Harness Engineering: A Survey*, TMLR-track). Closed-loop systems
now evolve harnesses automatically — AHE (arXiv:2604.25850), Meta-Harness (arXiv:2603.28052),
HarnessFix (arXiv:2606.06324), Life-Harness (arXiv:2605.22166) — but each invents a bespoke,
framework-locked substrate. Meanwhile declarative agent specifications exist (Oracle Agent Spec,
arXiv:2510.04173; Agent Format; Microsoft Agent Control Specification) but none covers the full
harness: Agent Spec explicitly excludes memory, middleware, permissions/sandboxing,
self-evolution, and change manifests.

HDP fills the intersection: **harness-complete** (all seven ETCLOVG layers), **evolution-ready**
(typed edit operators + falsifiable change manifests as protocol objects), **safety-as-schema**
(blast radius, policy immunity, editability governance). It is a normative instantiation of the
ETCLOVG taxonomy — the survey's own stated next step.

## 2. Document model

An **HDP document** is a directory anchored by a manifest file `hdp.yaml`:

```
<name>.hdp/
├── hdp.yaml                  # manifest (REQUIRED)
├── context/                  # embedded declarative content (conventional layout)
│   ├── system-rules.md
│   ├── memory/*.md
│   └── skills/<skill>/SKILL.md
├── tooling/*.tool.yaml        # embedded tool descriptions
├── verification/*.verifier.yaml
├── governance/*.policy.yaml   # optional split-out policy files
└── evolution/manifests/*.json # change manifests (sidecar, append-only)
```

Rules:

- `hdp.yaml` MUST validate against `schema/hdp.schema.json`.
- Embedded content lives in its own file per component (file-level diffs are the unit of
  observability and rollback — AHE component observability; Meta-Harness filesystem feedback).
- The directory layout above is RECOMMENDED, not mandatory; file paths in the manifest are
  authoritative.
- An HDP directory SHOULD be a git repository or live inside one; one logical change = one commit.

### 2.1 Embed vs. reference

- **Embedded (declarative)**: system rules, tool *descriptions*, skills, memory seeds, verifier
  contracts, policies. This is the layer empirically shown to transfer across models (AHE
  ablation; Life-Harness cross-backbone transfer).
- **Referenced (executable)**: tool *implementations*, middleware code, tracer wiring, sandbox
  backends. References use the `ref` object (§4.2); generators resolve them per target.

## 3. Manifest top level

```yaml
hdp: "0.1"                  # REQUIRED protocol version
meta:                        # REQUIRED
  id: my-agent               # REQUIRED stable slug [a-z0-9-]
  version: 1.0.0             # REQUIRED semver
  name: My Agent             # optional
  description: One sentence. # optional
  base_model: gpt-5.2        # optional; model the harness is tuned for
  targets: [nexau]           # optional; backends with known generators
layers: { ... }              # REQUIRED, see §4
governance: { ... }          # OPTIONAL but RECOMMENDED, see §5
evaluation: { ... }          # OPTIONAL, see §6
evolution: { ... }           # OPTIONAL, see §7
extensions: { ... }          # OPTIONAL free-form, namespaced by tool
```

Only `hdp`, `meta.id`, `meta.version`, and `layers` are required. Simple harnesses stay simple.

## 4. Layers and typed components

`layers` has seven optional keys — the ETCLOVG taxonomy:

| Key | ETCLOVG layer | Component types |
|---|---|---|
| `execution` | Execution environment & sandbox | `sandbox` |
| `tooling` | Tool interface & protocol | `tool` |
| `context` | Context & memory management | `system_rules`, `skill`, `memory` |
| `lifecycle` | Lifecycle & orchestration | `loop`, `middleware`, `sub_agent` |
| `observability` | Observability & operations | `tracer` |
| `verification` | Verification & evaluation | `verifier` |
| `governance` | Governance & security | `policy` |

Each layer holds a list of **components**.

### 4.1 Common component fields

```yaml
- id: search            # REQUIRED, unique across the whole document
  type: tool            # REQUIRED, one of the types valid for its layer
  name: Web search      # optional
  description: ...      # optional
  file: ./tooling/search.tool.yaml   # embedded content (path relative to hdp.yaml)
  ref: { ... }          # reference to executable artifact (§4.2)
```

A component MUST have `file` (embedded), `ref` (referenced), inline fields, or a combination
appropriate to its type. `id` values MUST be unique document-wide: they are the join key for
change manifests (§7) and trace spans (§8).

### 4.2 The `ref` object

```yaml
ref:
  kind: mcp | package | path | adapter | builtin
  # kind-specific:
  server: exa            # mcp
  package: "pkg>=1.0"    # package (pip/npm spec)
  path: ./impl/run.py    # path (relative, must exist)
  binding: tools.shell_tools:run_shell_command   # adapter/builtin entry point
```

### 4.3 Type-specific fields (normative highlights)

- **`tool`**: `schema` (JSON Schema for inputs, embedded or in `file`), `blast_radius`
  (§5.1), `implementation` (a `ref`). Tool *description* and *implementation* are deliberately
  separate concerns (AHE found description-flaws and implementation-flaws are distinct failure
  classes).
- **`middleware`**: `hook` — one of `contract_injection`, `skill_retrieval`,
  `action_validation`, `trajectory_regulation` (the four runtime intervention classes of
  Life-Harness), or `before_model`, `after_model`, `before_tool`, `after_tool` (positional
  hooks); plus `impl` (a `ref`). Runtime adaptation is *declared component behavior*; the HDP
  document itself never mutates at runtime.
- **`memory`**: `scope` (`session` | `persistent` | `shared`), `file` (seed content).
- **`verifier`**: `trigger` (`before_exit` | `on_demand` | `periodic` | `external`),
  `on_failure` (`block_exit` | `warn` | `retry`), `contract` (expected results).
- **`policy`**: `rule` (text), `enforcement` (`hard_block` | `soft_warn` | `log_only`),
  `applies_to` (component-id globs).
- **`sandbox`**: `isolation` (`container` | `microvm` | `os_permission` | `wasm` | `none`),
  `backend` (a `ref`), resource limits.
- **`loop`**: `max_iterations`, `tool_call_mode`, stop conditions.
- **`sub_agent`**: `definition` (a `ref` to another HDP document or backend-native agent),
  `max_depth` (REQUIRED; unbounded recursion is not a valid configuration).

## 5. Governance

```yaml
governance:
  blast_radius: system          # ceiling, §5.1
  evolution:
    editable:  [context, tooling, lifecycle]   # layers or component ids
    read_only: [verification, governance]      # default for these layers
    protected: [system-rules-core]             # ids that MUST NOT be removed or have
                                               # protected sections modified
  audit:
    log_all_tool_calls: true    # REQUIRED true when blast_radius is system|external
```

### 5.1 Blast radius

Monotone ordering: `read_only < local < session < system < external`. The harness-level
`governance.blast_radius` is a **ceiling**: no component may declare a wider blast radius.
(Containment-first design; cf. survey §11.2 capability–control tradeoff.)

### 5.2 Safety rules (normative)

1. **Blast-radius ordering** — no component exceeds the harness ceiling.
2. **Policy immunity** — `verification` and `governance` layers default to `read_only` for
   evolution agents; making them editable requires an explicit `governance.evolution.editable`
   entry (deliberate, auditable opt-in). This generalizes AHE's controllability constraints
   (read-only verifier/model config, non-deletable seed prompt).
3. **Immutable model config** — model identity/reasoning budget are not HDP components and MUST
   NOT be editable through the evolution interface.
4. **Protected components** — ids listed under `protected` MUST NOT be targets of `remove`.
5. **No embedded secrets** — secrets MUST be referenced as `${env.VAR}`; validators MUST scan
   embedded content for literal key patterns.
6. **Audit minimum** — `system`/`external` blast radius requires `log_all_tool_calls: true`.
7. **Recursion cap** — every `sub_agent` MUST declare `max_depth`.
8. **Manifest-before-edit** — an evolution agent MUST write a change-manifest entry (with
   predictions) before modifying any file (§7).

## 6. Evaluation

```yaml
evaluation:
  benchmark: "terminal-bench@2.0"
  primary_metric: pass_at_1
  baseline: 0.629            # score before evolution; all gains are relative to this
```

Grounds the evolution loop. Optional but REQUIRED if an `evolution` block is present.

## 7. Evolution interface

This section is HDP's distinctive half: the protocol standardizes not only what a harness *is*
but what an evolver may *do* to it.

### 7.1 Operator vocabulary (closed set)

| Operator | Meaning | Notes |
|---|---|---|
| `add` | introduce a new component | |
| `update` | modify an existing component's content/fields | |
| `remove` | delete a component | first-class: adaptive simplification (survey §12.5) |
| `narrow` | restrict a component's surface (e.g. tool-schema narrowing) | HarnessFix operator |
| `gate` | add a verification gate to an action/finalization path | HarnessFix operator |

Constrained, operator-scoped edits empirically outperform unconstrained edits
(HarnessFix ablation), which motivates a closed set. Tools MAY define additional operators under
`extensions`, but conformant validators only attribute the five above.

### 7.2 Change manifest

Each evolution round appends one JSON file under `evolution/manifests/`, validating against
`schema/change-manifest.schema.json`. Per change entry:

```json
{
  "change_id": "chg-7",
  "operator": "narrow",
  "component_id": "run-shell",
  "layer": "tooling",
  "failure_evidence": "trace t-042: agent deleted verified output during cleanup",
  "root_cause": "...",
  "repair_spec": {
    "editable_resources": ["tooling/run-shell.tool.yaml"],
    "forbidden_artifacts": ["verification/*", "governance/*"],
    "required_behaviors": ["existing passing tasks unaffected"],
    "validation_criteria": ["task t-042 flips to pass", "no regression on suite"]
  },
  "prediction": {
    "expected_fixes": ["t-042"],
    "at_risk_regressions": ["t-017"],
    "rationale": "..."
  },
  "verdict": "pending"
}
```

- `repair_spec` follows HarnessFix's repair-specification structure (editable resources,
  forbidden artifacts, required behaviors, validation criteria).
- `prediction` follows AHE's decision observability: every edit is a falsifiable contract,
  settled by the next evaluation round (`verdict`: `pending → effective | partial | ineffective |
  harmful`, with `regressions_observed`).
- **System-level validation**: predictions MUST be verified against full-suite results, not
  per-component checks — harness layers couple, and changes must be tested as system changes
  (survey §11.3; AHE's non-additive composition finding).

### 7.3 Versioning

Applying a manifest bumps `meta.version` (MINOR for `add`/`remove`, PATCH for
`update`/`narrow`/`gate` unless behavior-breaking). Each applied manifest SHOULD correspond to one
git commit tagged with the new version. HDP documents are frozen snapshots; there is no runtime
mutation channel.

## 8. Trace-reference convention (SHOULD-level)

HDP does not define a trace format. Conformant runtimes SHOULD tag execution-trace spans with the
`component_id` responsible for the behavior observed in that span:

- tool call spans → the `tool` id;
- injected context (rules/skills/memory) → the originating `context` component id;
- blocked/transformed actions → the `middleware` or `policy` id;
- verification events → the `verifier` id.

This makes trace-to-component failure attribution (HarnessFix HTIR "responsibility facets";
survey §12.3 trace-native evaluation) possible on any HDP harness without HDP owning a trace
schema; tags ride on OpenTelemetry/Langfuse/native attributes.

## 9. ETCLOVG crosswalk

HDP layer keys are the ETCLOVG layers; the crosswalk is the identity mapping plus component
placement:

| ETCLOVG | HDP layer key | HDP components | Notes |
|---|---|---|---|
| Execution | `execution` | `sandbox` | declarative sandbox profile; backends referenced (cf. K8s Agent Sandbox CRD precedent) |
| Tooling | `tooling` | `tool` | description embedded, implementation referenced |
| Context | `context` | `system_rules`, `skill`, `memory` | the transferable layer |
| Lifecycle | `lifecycle` | `loop`, `middleware`, `sub_agent` | state management lives here, per the survey |
| Observability | `observability` | `tracer` + §8 convention | |
| Verification | `verification` | `verifier` | first-class, separate from O |
| Governance | `governance` layer + `governance` block | `policy` + blast radius/editability/audit | the "declarative constitution," made portable |

## 10. Conformance

- **Document conformance**: validates against the schema AND passes the structural rules of §5.2
  (the reference validator implements both).
- **Generator conformance**: a generator MUST honor embedded content byte-for-byte, MUST enforce
  `policy`/`verifier` components in the produced harness, and MUST fail (not skip) on `ref`s it
  cannot resolve.
- **Evolver conformance**: only the §7.1 operators; manifest-before-edit; respects
  `governance.evolution`; never edits model config.

## 11. Limitations (v0.1)

- Typed decomposition forgoes free-form whole-program harness search (Meta-Harness); the `ref`
  escape hatch admits arbitrary code at the implementation level only.
- Generators are per-backend engineering; v0.1 ships the format and validator, with NexAU as the
  first generation target (worked example included).
- Out of scope, planned: sub-agent handoff contracts (survey §12.4), component conflict/coupling
  declarations, a full trace intermediate representation.

## References

AHE (arXiv:2604.25850) · Meta-Harness (arXiv:2603.28052) · HarnessFix (arXiv:2606.06324) ·
Life-Harness (arXiv:2605.22166) · HarnessForge (arXiv:2606.01779) · *Agent Harness Engineering: A
Survey* (TMLR-track, ETCLOVG) · *Externalization in LLM Agents* (arXiv:2604.08224) · ACE
(arXiv:2510.04618) · Training-Free GRPO (arXiv:2510.08191) · GEPA (arXiv:2507.19457) · Open Agent
Spec (arXiv:2510.04173) · MCP/A2A/ACP/ANP survey (arXiv:2505.02279). Full annotated review:
[docs/literature-review-hdp.md](../docs/literature-review-hdp.md).
