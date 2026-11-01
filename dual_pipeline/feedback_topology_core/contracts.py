"""Standard loop state, termination descriptors, and audit contracts for agentic feedback loops."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Optional


class LoopState(StrEnum):
    """Discrete states of the agentic re-planning and evaluation lifecycle."""
    IDLE = "IDLE"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    REVIEWING = "REVIEWING"
    SUFFICIENT = "SUFFICIENT"
    REPLANNING = "REPLANNING"
    EXHAUSTED = "EXHAUSTED"
    BLOCKED = "BLOCKED"


class LoopTerminationReason(StrEnum):
    """Formal closed taxonomy of feedback loop terminal outcomes."""
    SUFFICIENT_COMPLETION = "SUFFICIENT_COMPLETION"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    FATAL_BLOCK = "FATAL_BLOCK"
    MANUAL_ABORT = "MANUAL_ABORT"


class FeedbackTopologyClass(StrEnum):
    """Structural classification of agentic feedback topology."""
    CLASS_A_BOUNDED_REPLAN = "Class A — True agentic feedback (L1 -> L2 -> L1 bounded re-planning)"
    CLASS_C_GATE_DEPENDENT = "Class C — Gate-dependent pipeline (Stages S01-S03 -> QA Gates 1-4 -> LLM Judge X1 -> UWG Release Gate)"


@dataclass(frozen=True)
class FeedbackAuditEvent:
    """Structured, tamper-evident audit record capturing discrete loop state transitions."""
    event_id: str
    loop_id: str
    event_type: str
    from_state: str
    to_state: str
    iteration: int
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def create(
        cls,
        loop_id: str,
        event_type: str,
        from_state: LoopState | str,
        to_state: LoopState | str,
        iteration: int,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> FeedbackAuditEvent:
        """Construct a new immutable FeedbackAuditEvent."""
        from_val = from_state.value if isinstance(from_state, LoopState) else str(from_state)
        to_val = to_state.value if isinstance(to_state, LoopState) else str(to_state)
        return cls(
            event_id=f"fbevt-{uuid.uuid4().hex[:12]}",
            loop_id=loop_id,
            event_type=event_type,
            from_state=from_val,
            to_state=to_val,
            iteration=iteration,
            payload=dict(payload or {}),
            timestamp=time.time(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert event to JSON-serializable dictionary."""
        return {
            "event_id": self.event_id,
            "loop_id": self.loop_id,
            "event_type": self.event_type,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "iteration": self.iteration,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }


@dataclass
class LoopDescriptor:
    """Comprehensive descriptor of an agentic feedback loop instance."""
    loop_id: str
    topology_class: FeedbackTopologyClass
    max_iterations: int
    current_iteration: int
    current_state: LoopState
    terminal: bool
    termination_reason: Optional[LoopTerminationReason] = None
    transitions_count: int = 0
    replan_directives_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize descriptor to dictionary."""
        return {
            "loop_id": self.loop_id,
            "topology_class": self.topology_class.value,
            "max_iterations": self.max_iterations,
            "current_iteration": self.current_iteration,
            "current_state": self.current_state.value,
            "terminal": self.terminal,
            "termination_reason": self.termination_reason.value if self.termination_reason else None,
            "transitions_count": self.transitions_count,
            "replan_directives_count": self.replan_directives_count,
        }
