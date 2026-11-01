"""Adversarial and edge-case verification for AgentRuntimeLoop, A2A contracts, and RoundtableOrchestrator.

Covers mandatory edge-case categories:
1. Boundary & malformed input (invalid ClaimCheckPointer SHA-256 digests, empty roundtable topic, invalid config budgets).
2. Overflow & exhaustion (tool call budget exhaustion, token budget exhaustion, stagnation circuit breaker).
3. Invariant bypass attempts (unauthorized tool scope rejection, Tier-0 irreversible HITL escalation override, narrow margin <= 0.20 escalation).
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict

import pytest

from dual_pipeline.a2a.protocol import ClaimCheckPointer
from dual_pipeline.agent_architecture_core.runtime_loop import (
    AgentRuntimeLoop,
    LoopTerminalOutcome,
    RuntimeLoopConfig,
    evaluate_autonomy_margin,
    verify_claim_check_pointers,
)
from dual_pipeline.roundtable_orchestrator import RoundtableOrchestrator


def test_boundary_and_malformed_inputs_fail_closed() -> None:
    """Category 1: Boundary & malformed input rejection."""
    with pytest.raises(ValueError, match="must be positive"):
        RuntimeLoopConfig(agent_id="bad_agent", goal="test", max_steps=0)

    with pytest.raises(ValueError, match="delegation_depth"):
        RuntimeLoopConfig(agent_id="bad_agent", goal="test", delegation_depth=5)

    bad_ptr = ClaimCheckPointer(
        source_id="src:1",
        evidence_id="ev:1",
        content_digest="not-a-valid-sha256-digest",
        locator={"line": 1},
    )
    ok, errors = verify_claim_check_pointers([bad_ptr])
    assert ok is False
    assert any("Invalid SHA-256" in e for e in errors)

    with pytest.raises(ValueError, match="non-empty"):
        RoundtableOrchestrator(topic="   ").start_debate()

    with pytest.raises(ValueError, match="Invalid ClaimCheckPointer digest"):
        RoundtableOrchestrator(topic="Valid topic", claim_checks=[bad_ptr]).start_debate()


def test_overflow_and_budget_exhaustion_halts_deterministically() -> None:
    """Category 2: Tool call and token budget exhaustion halts loop with BUDGET_EXHAUSTED."""
    cfg = RuntimeLoopConfig(
        agent_id="budget_test_agent",
        goal="Test tool call cap",
        max_steps=4,
        max_tool_calls=3,
        max_tokens=500,
        timeout_seconds=10.0,
    )
    loop = AgentRuntimeLoop(cfg)

    result = loop.execute(
        initial_state={"count": 0},
        observe_fn=lambda s, i: {"count": s["count"]},
        decide_fn=lambda o, i: {"action": "heavy_tool_step"},
        act_fn=lambda s, d, i: {"tool_calls_used": 2, "tokens_used": 100, "state_updates": {"count": i}},
        evaluate_fn=lambda s, a, i: {"done": False, "quality_score": 0.2 * i},
    )
    assert result.terminal_outcome == LoopTerminalOutcome.BUDGET_EXHAUSTED
    assert result.total_tool_calls == 4
    assert "Budget exhausted" in result.termination_reason


def test_stagnation_detection_halts_non_progressing_loop() -> None:
    """Category 2b: Repeated state signature or non-increasing quality score triggers STAGNATED."""
    cfg = RuntimeLoopConfig(
        agent_id="stagnation_agent",
        goal="Detect zero-progress cycling",
        max_steps=5,
        max_tool_calls=15,
    )
    loop = AgentRuntimeLoop(cfg)

    result = loop.execute(
        initial_state={"frontier": ["node_a"]},
        observe_fn=lambda s, i: {"frontier": s["frontier"]},
        decide_fn=lambda o, i: {"action": "repeat_query"},
        act_fn=lambda s, d, i: {"tool_calls_used": 1, "tokens_used": 50, "state_updates": {"frontier": ["node_a"]}},
        evaluate_fn=lambda s, a, i: {"done": False, "quality_score": 0.40},
    )
    assert result.terminal_outcome == LoopTerminalOutcome.STAGNATED
    assert result.steps_executed == 2
    assert "Stagnation detected" in result.termination_reason


def test_invariant_bypass_unauthorized_tool_and_hitl_margin_gate() -> None:
    """Category 3: Unauthorized tool scope raises PermissionError; narrow margin or Tier-0 forces HITL."""
    cfg = RuntimeLoopConfig(
        agent_id="guarded_supervisor",
        goal="Enforce strict tool allowlist and ambiguity margin",
        max_steps=3,
        allowed_tools=("query_knowledge_graph",),
    )
    loop = AgentRuntimeLoop(cfg)
    valid_digest = hashlib.sha256(b"payload").hexdigest()
    valid_ptr = ClaimCheckPointer("src:1", "ev:1", valid_digest, {"id": 1})

    with pytest.raises(PermissionError, match="UNAUTHORIZED_TOOL_SCOPE"):
        loop.dispatch_subagent_task(
            recipient_agent_id="rogue_subagent",
            objective="Attempt unapproved write tool",
            scope_in=["nodes"],
            scope_out=["shadow_graph_nodes"],
            allowed_tools=["direct_sql_mutation_tool"],
            deliverable="mutation",
            definition_of_done="never",
            claim_checks=[valid_ptr],
        )

    # Tier-0 irreversible operation must escalate even when confidence margin > 0.20
    tier0_dec = evaluate_autonomy_margin(
        [("promote_release", 0.96), ("abort_release", 0.04)],
        is_tier0_irreversible=True,
    )
    assert tier0_dec.outcome_type == "ESCALATE_HITL"

    # Narrow margin (delta = 0.12 <= 0.20) must escalate to HITL inside execute()
    def decide_narrow(_obs: Dict[str, Any], _step: int) -> Dict[str, Any]:
        return {
            "action": "ambiguous_route",
            "candidates": [("route_alpha", 0.72), ("route_beta", 0.60)],
            "halt_on_ambiguity": True,
        }

    esc_result = loop.execute(
        initial_state={},
        observe_fn=lambda s, i: {},
        decide_fn=decide_narrow,
        act_fn=lambda s, d, i: {"tool_calls_used": 1},
        evaluate_fn=lambda s, a, i: {"done": True, "quality_score": 1.0},
    )
    assert esc_result.terminal_outcome == LoopTerminalOutcome.ESCALATED_HITL
    assert esc_result.final_output["escalated_decision"]["outcome_type"] == "ESCALATE_HITL"
