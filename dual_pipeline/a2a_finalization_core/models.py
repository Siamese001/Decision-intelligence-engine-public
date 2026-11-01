"""Data classes and exception types for A2A finalization."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from dual_pipeline.exceptions import DualPipelineValueError

from .constants import _FINALIZATION_BINDING_FIELDS, _VERIFIER_CAPABILITY


class A2AFinalizationError(DualPipelineValueError):
    """Raised when a finalized A2A packet cannot be replayed from trusted bytes."""


@dataclass(frozen=True)
class VerifiedA2AFinalization:
    """Opaque, verifier-issued capability for one replayed A2A package.

    Its serialized projection is intentionally only audit evidence.  Intake
    code must use this capability while constructing a fact-eligible bundle;
    a shape-valid dictionary must never substitute for archive replay.
    """

    _binding: Mapping[str, str]
    _capability: object

    def audit_binding(self) -> dict[str, str]:
        """Return a copy safe to persist in sealed artifacts and graph metadata."""
        return dict(self._binding)


def verified_finalization_binding(
    value: object, *, package: dict[str, Any] | None = None
) -> dict[str, str]:
    """Return an audit binding only for a capability issued by this verifier."""
    if not isinstance(value, VerifiedA2AFinalization) or value._capability is not _VERIFIER_CAPABILITY:
        raise A2AFinalizationError(
            "A2A fact eligibility requires a verifier-issued finalization capability"
        )
    binding = value.audit_binding()
    if set(binding) != _FINALIZATION_BINDING_FIELDS:
        raise A2AFinalizationError("A2A verifier-issued finalization binding is invalid")
    if package is not None and binding["package_canonical_digest"] != package.get(
        "canonical_digest", {}
    ).get("value"):
        raise A2AFinalizationError(
            "A2A verifier-issued finalization capability is bound to another package"
        )
    return binding
