"""Internal-operational qualification, status, recovery, and revocation.

Thin backward-compatibility facade delegating to dual_pipeline.internal_operational_core.
"""
from __future__ import annotations

from dual_pipeline.internal_operational_core import (
    InternalOperationalManager,
    _directive_binding,
    _evaluate_target_suite,
    _recovery_evidence,
    _source_evaluation_binding,
    _verify_directive_package_bytes,
)

__all__ = [
    "InternalOperationalManager",
    "_directive_binding",
    "_evaluate_target_suite",
    "_recovery_evidence",
    "_source_evaluation_binding",
    "_verify_directive_package_bytes",
]
