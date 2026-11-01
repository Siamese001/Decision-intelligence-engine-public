"""
Typed models for agentic trajectory and step-level evaluation.

Adheres to 2026+ Anthropic and OpenAI agentic evaluation specifications:
- Fine-grained trajectory tracing across tools, subagents, and environmental actions.
- Step-level credit assignment, schema validation, and redundancy scoring.
- Immutable scorecard representation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class TrajectoryStep:
    """A single discrete step or action within an agentic execution trajectory."""

    step_index: int
    actor_id: str
    action_type: str  # "tool_call", "subagent_delegation", "state_read", "state_write", "final_response"
    tool_name: Optional[str] = None
    tool_arguments: Dict[str, Any] = field(default_factory=dict)
    tool_output: Optional[Any] = None
    status: str = "SUCCESS"  # "SUCCESS", "ERROR", "TIMEOUT", "BLOCKED"
    duration_ms: float = 0.0
    token_usage: Dict[str, int] = field(default_factory=dict)
    timestamp_iso: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TrajectoryTrace:
    """An end-to-end execution trajectory comprising sequential steps."""

    trace_id: str
    goal: str
    pipeline_id: str
    steps: List[TrajectoryStep] = field(default_factory=list)
    optimal_step_count: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StepEvaluationVerdict:
    """Individual verdict and diagnostics for a single trajectory step."""

    step_index: int
    is_valid: bool
    is_redundant: bool
    is_loop_strike: bool
    schema_errors: List[str] = field(default_factory=list)
    penalty_score: float = 0.0
    reasoning: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TrajectoryScorecard:
    """Aggregated evaluation scorecard for an entire agentic execution trajectory."""

    trace_id: str
    pipeline_id: str
    total_steps: int
    optimal_steps: int
    efficiency_ratio: float
    schema_compliance_rate: float
    loop_count: int
    redundant_step_count: int
    error_recovery_rate: float
    composite_score: float
    passed: bool
    terminal_task_success: bool = True
    adaptation_attempts: int = 0
    successful_recoveries: int = 0
    optimal_steps_available: bool = True
    unresolved_error_count: int = 0
    step_verdicts: List[StepEvaluationVerdict] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)
    evaluated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
