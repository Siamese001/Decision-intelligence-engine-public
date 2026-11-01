"""
Unit and acceptance test suite for Wave 1: Agentic Trajectory and Step-Level Evaluator.

Verifies:
- Model instantiation and serialization.
- ToolSchemaValidator type, enum, required, and additionalProperties enforcement.
- TrajectoryEvaluator path efficiency calculation.
- Redundancy and 3-step / oscillating loop detection.
- Error recovery adaptability.
- Non-compensating release gating.
"""

from __future__ import annotations

import pytest

from dual_pipeline.evals.trajectory import (
    ToolSchemaValidator,
    TrajectoryEvaluator,
    TrajectoryStep,
    TrajectoryTrace,
)


@pytest.fixture
def sample_tool_schema() -> dict:
    return {
        "read_node": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "minLength": 3},
                "include_evidence": {"type": "boolean"},
            },
            "required": ["node_id"],
            "additionalProperties": False,
        },
        "query_graph": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
                "mode": {"type": "string", "enum": ["exact", "hybrid", "semantic"]},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    }


def test_empty_trajectory_fails_closed():
    """Verify empty trajectory results in failure scorecard."""
    evaluator = TrajectoryEvaluator()
    trace = TrajectoryTrace(
        trace_id="trace-empty",
        goal="Test empty execution",
        pipeline_id="pipeline_c",
        steps=[],
    )
    scorecard = evaluator.evaluate_trace(trace)
    assert not scorecard.passed
    assert scorecard.total_steps == 0
    assert scorecard.composite_score == 0.0


def test_schema_validator_enforces_rules(sample_tool_schema):
    """Verify schema validator detects missing fields, bad types, and unexpected props."""
    validator = ToolSchemaValidator(sample_tool_schema)

    # Valid call
    valid, errors = validator.validate_call("read_node", {"node_id": "node:test-1"})
    assert valid
    assert len(errors) == 0

    # Missing required field
    valid, errors = validator.validate_call("read_node", {"include_evidence": True})
    assert not valid
    assert any("Missing required parameter 'node_id'" in e for e in errors)

    # Type mismatch: bool instead of str
    valid, errors = validator.validate_call("read_node", {"node_id": True})
    assert not valid
    assert any("expected string, got bool" in e for e in errors)

    # Type mismatch: bool instead of int
    valid, errors = validator.validate_call(
        "query_graph", {"query": "test", "limit": True}
    )
    assert not valid
    assert any("expected integer, got boolean" in e for e in errors)

    # Enum violation
    valid, errors = validator.validate_call(
        "query_graph", {"query": "test", "mode": "unsupported_mode"}
    )
    assert not valid
    assert any("not in allowed enum" in e for e in errors)

    # Unexpected property with additionalProperties=False
    valid, errors = validator.validate_call(
        "read_node", {"node_id": "node:test-1", "rogue_field": 123}
    )
    assert not valid
    assert any("Unexpected property 'rogue_field'" in e for e in errors)


def test_nominal_optimal_trajectory(sample_tool_schema):
    """Verify clean, optimal trajectory achieves high score and passes gates."""
    validator = ToolSchemaValidator(sample_tool_schema)
    evaluator = TrajectoryEvaluator(schema_validator=validator)

    steps = [
        TrajectoryStep(
            step_index=1,
            actor_id="agent:retrieval",
            action_type="tool_call",
            tool_name="query_graph",
            tool_arguments={"query": "find substation telemetry", "limit": 10},
            status="SUCCESS",
        ),
        TrajectoryStep(
            step_index=2,
            actor_id="agent:retrieval",
            action_type="tool_call",
            tool_name="read_node",
            tool_arguments={"node_id": "system:substation-1"},
            status="SUCCESS",
        ),
        TrajectoryStep(
            step_index=3,
            actor_id="agent:retrieval",
            action_type="final_response",
            tool_name=None,
            tool_arguments={},
            status="SUCCESS",
        ),
    ]

    trace = TrajectoryTrace(
        trace_id="trace-optimal-1",
        goal="Retrieve substation telemetry",
        pipeline_id="pipeline_c",
        steps=steps,
        optimal_step_count=3,
    )

    scorecard = evaluator.evaluate_trace(trace)
    assert scorecard.passed
    assert scorecard.efficiency_ratio == 1.0
    assert scorecard.schema_compliance_rate == 1.0
    assert scorecard.loop_count == 0
    assert scorecard.redundant_step_count == 0
    assert scorecard.composite_score >= 95.0


def test_consecutive_redundant_calls_penalized(sample_tool_schema):
    """Verify consecutive duplicate tool calls are flagged as redundant."""
    validator = ToolSchemaValidator(sample_tool_schema)
    evaluator = TrajectoryEvaluator(schema_validator=validator)

    steps = [
        TrajectoryStep(
            step_index=1,
            actor_id="agent:retrieval",
            action_type="tool_call",
            tool_name="read_node",
            tool_arguments={"node_id": "system:substation-1"},
            status="SUCCESS",
        ),
        # Immediate duplicate
        TrajectoryStep(
            step_index=2,
            actor_id="agent:retrieval",
            action_type="tool_call",
            tool_name="read_node",
            tool_arguments={"node_id": "system:substation-1"},
            status="SUCCESS",
        ),
    ]

    trace = TrajectoryTrace(
        trace_id="trace-redundant-1",
        goal="Read node",
        pipeline_id="pipeline_c",
        steps=steps,
        optimal_step_count=1,
    )

    scorecard = evaluator.evaluate_trace(trace)
    assert scorecard.redundant_step_count == 1
    assert scorecard.step_verdicts[1].is_redundant


def test_repetitive_loop_strike_and_gate_failure(sample_tool_schema):
    """Verify 3 consecutive identical calls trigger loop strike and fail the gate."""
    validator = ToolSchemaValidator(sample_tool_schema)
    evaluator = TrajectoryEvaluator(schema_validator=validator, max_allowed_loops=0)

    steps = [
        TrajectoryStep(
            step_index=i,
            actor_id="agent:retrieval",
            action_type="tool_call",
            tool_name="read_node",
            tool_arguments={"node_id": "system:substation-1"},
            status="SUCCESS",
        )
        for i in range(1, 4)
    ]

    trace = TrajectoryTrace(
        trace_id="trace-loop-1",
        goal="Loop test",
        pipeline_id="pipeline_c",
        steps=steps,
        optimal_step_count=1,
    )

    scorecard = evaluator.evaluate_trace(trace)
    assert scorecard.loop_count >= 1
    assert not scorecard.passed
    assert any("Exceeded max loops" in f for f in scorecard.details["gate_failures"])


def test_oscillating_loop_detection(sample_tool_schema):
    """Verify alternating A -> B -> A -> B pattern is recognized as an oscillating loop."""
    validator = ToolSchemaValidator(sample_tool_schema)
    evaluator = TrajectoryEvaluator(schema_validator=validator, max_allowed_loops=0)

    steps = [
        TrajectoryStep(
            step_index=1,
            actor_id="agent:retrieval",
            action_type="tool_call",
            tool_name="read_node",
            tool_arguments={"node_id": "node:A"},
            status="SUCCESS",
        ),
        TrajectoryStep(
            step_index=2,
            actor_id="agent:retrieval",
            action_type="tool_call",
            tool_name="query_graph",
            tool_arguments={"query": "query:B"},
            status="SUCCESS",
        ),
        TrajectoryStep(
            step_index=3,
            actor_id="agent:retrieval",
            action_type="tool_call",
            tool_name="read_node",
            tool_arguments={"node_id": "node:A"},
            status="SUCCESS",
        ),
        TrajectoryStep(
            step_index=4,
            actor_id="agent:retrieval",
            action_type="tool_call",
            tool_name="query_graph",
            tool_arguments={"query": "query:B"},
            status="SUCCESS",
        ),
    ]

    trace = TrajectoryTrace(
        trace_id="trace-osc-1",
        goal="Oscillation test",
        pipeline_id="pipeline_c",
        steps=steps,
        optimal_step_count=2,
    )

    scorecard = evaluator.evaluate_trace(trace)
    assert scorecard.loop_count >= 1
    assert not scorecard.passed


def test_error_recovery_rate(sample_tool_schema):
    """Verify that adapting after an error produces a high error recovery rate."""
    validator = ToolSchemaValidator(sample_tool_schema)
    evaluator = TrajectoryEvaluator(schema_validator=validator)

    steps = [
        # Step 1 errors
        TrajectoryStep(
            step_index=1,
            actor_id="agent:retrieval",
            action_type="tool_call",
            tool_name="query_graph",
            tool_arguments={"query": "invalid_syntax["},
            status="ERROR",
        ),
        # Step 2 adapts by modifying arguments and succeeds
        TrajectoryStep(
            step_index=2,
            actor_id="agent:retrieval",
            action_type="tool_call",
            tool_name="query_graph",
            tool_arguments={"query": "valid syntax"},
            status="SUCCESS",
        ),
    ]

    trace = TrajectoryTrace(
        trace_id="trace-recovery-1",
        goal="Error recovery test",
        pipeline_id="pipeline_c",
        steps=steps,
        optimal_step_count=2,
    )

    scorecard = evaluator.evaluate_trace(trace)
    assert scorecard.error_recovery_rate == 1.0
    assert scorecard.details["error_count"] == 1
    assert scorecard.details["recovered_count"] == 1
