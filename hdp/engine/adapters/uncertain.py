"""hdp.engine.adapters.uncertain — the lift-time uncertainty sentinel (a shared *type*).

A heuristic mapper emits :class:`Uncertain` in place of a field it cannot confidently
determine (e.g. a ``blast_radius`` it can't infer from a tool's name/binding). This makes the
uncertainty explicit and backend-agnostic:

  * heuristic strategy collapses every ``Uncertain`` back to its ``default`` before validation
    (:func:`finalize`), so the deterministic output is byte-for-byte unchanged;
  * llm_assisted strategy asks the LLM to resolve only the fields still carrying the sentinel.

This module is a shared data *type* plus the traversal mechanics intrinsic to that type (like
``dataclasses.asdict`` for a dataclass) — deliberately backend-agnostic so a future adapter gets
a correct llm_assisted scope for free, as long as its own mapper is honest via :class:`Uncertain`.
Adapter *behavior* stays uncoupled: each adapter keeps its own ``_llm_refine``/``agentic_lift``
methods and its own field-emission logic; only these type mechanics are shared.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Uncertain:
    """A field value a heuristic mapper couldn't confidently determine."""
    default: str                          # conservative value heuristic mode collapses to
    reason: str                           # why the mapper couldn't decide (fed to the LLM)
    candidates: tuple[str, ...] = field(default_factory=tuple)  # legal values, if a closed set


def resolved(value):
    """Unwrap an ``Uncertain`` to its conservative ``default`` (for arithmetic like a ceiling);
    pass any other value through unchanged."""
    return value.default if isinstance(value, Uncertain) else value


def iter_uncertain(obj) -> Iterator[tuple]:
    """Yield ``(container, key_or_index, Uncertain)`` for every sentinel nested in *obj*,
    so a caller can resolve it in place via ``container[key] = <value>``."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, Uncertain):
                yield obj, k, v
            else:
                yield from iter_uncertain(v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, Uncertain):
                yield obj, i, v
            else:
                yield from iter_uncertain(v)


def finalize(obj) -> None:
    """Recursively replace every ``Uncertain`` in *obj* with its ``default``, in place."""
    for container, key, unc in list(iter_uncertain(obj)):
        container[key] = unc.default


def refine_prompt(field_name, unc: Uncertain) -> str:
    """Build the focused single-shot prompt asking the LLM to resolve one sentinel."""
    choices = f" Choose exactly one of: {', '.join(unc.candidates)}." if unc.candidates else ""
    return (
        f"You are refining one uncertain field of a lifted HDP agent-harness document.\n"
        f"Field: {field_name}\n"
        f"Why the deterministic mapper was unsure: {unc.reason}\n"
        f"Conservative default if unsure: {unc.default}\n"
        f"{choices}\n"
        f"Reply with ONLY the single chosen value, no punctuation or explanation."
    )
