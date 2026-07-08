"""hdp.engine.adapters.base — the framework-adapter seam (vendor neutrality).

An adapter knows how to compile an HDP document into one concrete backend's harness
(``generate``) and how to recover an HDP document from such a harness (``lift``). HDP is
the source of truth; adapters are the per-backend engineering. NexAU is the first target.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from hdp.engine.core.loader import HDPDoc

if TYPE_CHECKING:
    from hdp.engine.port.coverage import CapabilityIssue


class FrameworkAdapter(ABC):
    #: backend identifier, matched against meta.targets (e.g. "nexau")
    target: str

    def capability_issues(self, doc: HDPDoc) -> list[CapabilityIssue]:
        """Enumerate every component this adapter cannot faithfully represent in *doc* —
        unsupported types, unrecognized id-keyed slots, destination collisions, unresolvable
        bindings — without raising. Default: no adapter-specific knowledge; each concrete adapter
        overrides. Consumed by :func:`hdp.engine.port.audit` (never raises here)."""
        return []

    @abstractmethod
    def generate(self, doc: HDPDoc, out_dir: Path) -> Path:
        """Compile *doc* into a runnable harness under *out_dir*; return *out_dir*.

        MUST honor embedded content byte-for-byte, MUST enforce policy/verifier components,
        and MUST fail (not skip) on a ``ref`` it cannot resolve (SPEC §10 generator conformance).
        """

    @abstractmethod
    def lift(self, harness_dir: Path, out_dir: Path) -> HDPDoc:
        """Recover an HDP document from an existing backend harness, writing it to *out_dir*.

        Deterministic structural mapping first; an optional LLM pass fills only what structure
        can't give. The result MUST validate against the typed model before being written
        (symmetric inverse of :meth:`generate`)."""
