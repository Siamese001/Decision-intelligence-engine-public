"""Wave 4 shadow receipts for the governed query L1 controller.

The builders in this module consume already-sealed query artifacts. They do
not retrieve, embed, call a provider, mutate a graph, or rescore evidence. A
caller may persist the returned artifacts in a query run directory; ``off``
returns an empty result so the historical runtime path is unchanged.
"""
from __future__ import annotations

from .query_l1_shadow_core import (
    ShadowReceiptError,
    ShadowReceiptSet,
    build_attempt_zero,
    build_observation_zero,
    build_off_shadow_comparison,
    build_shadow_action,
    build_shadow_receipts,
    build_shadow_terminal,
    shadow_telemetry_payload,
    validate_shadow_telemetry,
    write_shadow_receipts,
)

__all__ = [
    "ShadowReceiptError",
    "ShadowReceiptSet",
    "build_attempt_zero",
    "build_observation_zero",
    "build_off_shadow_comparison",
    "build_shadow_action",
    "build_shadow_receipts",
    "build_shadow_terminal",
    "shadow_telemetry_payload",
    "validate_shadow_telemetry",
    "write_shadow_receipts",
]
