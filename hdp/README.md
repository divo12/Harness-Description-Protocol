# HDP — Harness Definition Protocol (v0.1 draft)

A vendor-neutral, machine-readable format that **defines** an LLM-agent harness (all seven
ETCLOVG layers), compiles to concrete backends, and is **evolved** through a standardized,
governed edit interface (typed operators + falsifiable change manifests).

| Artifact | Path |
|---|---|
| Specification (normative) | [SPEC.md](SPEC.md) |
| Manifest JSON Schema | [schema/hdp.schema.json](schema/hdp.schema.json) |
| Change-manifest JSON Schema | [schema/change-manifest.schema.json](schema/change-manifest.schema.json) |
| Reference validator | [validator/hdp_validate.py](validator/hdp_validate.py) |
| Worked example (AHE seed harness) | [examples/code-agent-simple.hdp/](examples/code-agent-simple.hdp/) |
| Design rationale + decisions | [../docs/superpowers/specs/2026-06-12-hdp-v0.1-design.md](../docs/superpowers/specs/2026-06-12-hdp-v0.1-design.md) |
| Literature review (35+ sources) | [../docs/literature-review-hdp.md](../docs/literature-review-hdp.md) |

## Validate a document

```bash
uv run python hdp/validator/hdp_validate.py hdp/examples/code-agent-simple.hdp
# code-agent-simple.hdp: conformant with HDP 0.1 (0 warning(s))
```

## Status

v0.1 ships the format, schemas, validator, and one worked example (the NexAU seed harness whose
gpt-5.2 Terminal-Bench 2 baseline we measured at 62.9–65.9%; see
[../docs/baseline-gpt52.md](../docs/baseline-gpt52.md)). Next pieces, in order: the NexAU
generator (HDP → runnable harness), then generalizing the AHE evolution loop to operate on any
HDP document.
