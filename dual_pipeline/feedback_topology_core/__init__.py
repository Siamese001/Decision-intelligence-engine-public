"""Feedback Topology Core: Contracts, state descriptors, and event types for agentic loops."""
from __future__ import annotations

from .contracts import (
    FeedbackAuditEvent,
    FeedbackTopologyClass,
    LoopDescriptor,
    LoopState,
    LoopTerminationReason,
)

__all__ = [
    "FeedbackAuditEvent",
    "FeedbackTopologyClass",
    "LoopDescriptor",
    "LoopState",
    "LoopTerminationReason",
]
