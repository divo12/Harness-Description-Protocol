"""hdp.engine.port.coverage — the honest "what does not survive a port" report.

A leaf module: it imports only stdlib dataclasses so that adapters (and the port package)
can depend on it without an import cycle. An adapter's ``capability_issues(doc)`` returns a
list of :class:`CapabilityIssue`; :func:`hdp.engine.port.audit` wraps that in a
:class:`CoverageReport`.

Severities:
  * ``blocking``    — ``generate()``/``port()`` cannot faithfully proceed: a ``policy`` component,
                      a non-``external`` ``verifier``, or an adapter-bound ``tool`` whose binding
                      the destination adapter cannot resolve. ``port()`` refuses on any of these.
  * ``silent_drop`` — no raise, but the component's content lands nowhere in the output (e.g. a
                      ``system_rules`` id mini-SWE-agent has no manifest slot for).
  * ``collision``   — no raise, but two or more components overwrite one destination artifact and
                      only the last survives (e.g. two ``system_rules`` on NexAU, both →
                      ``systemprompt.md``).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CapabilityIssue:
    component_id: str
    layer: str
    type: str
    severity: str   # "blocking" | "silent_drop" | "collision"
    reason: str


@dataclass
class CoverageReport:
    source_target: str | None
    dest_target: str
    total_components: int
    issues: list[CapabilityIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def blocking(self) -> list[CapabilityIssue]:
        return [i for i in self.issues if i.severity == "blocking"]
