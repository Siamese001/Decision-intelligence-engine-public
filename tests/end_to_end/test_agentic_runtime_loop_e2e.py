"""End-to-end verification of the Bounded Agent Runtime Loop, Roundtable Deliberation, and Specialist Hierarchy."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.a2a.protocol import A2ATaskStatus, ClaimCheckPointer
from dual_pipeline.agent_architecture_core.runtime_loop import (
    AgentRuntimeLoop,
    LoopTerminalOutcome,
    RuntimeLoopConfig,
    evaluate_autonomy_margin,
    verify_claim_check_pointers,
)
from dual_pipeline.agent_sdk.orchestration import NeeAgentHierarchy
from dual_pipeline.roundtable_orchestrator import ROSTER, RoundtableOrchestrator


def test_agent_runtime_loop_converges_with_a2a_delegation_e2e() -> None:
    """Verifies multi-step Observe -> Decide -> Act -> Evaluate convergence with A2ATask delegation."""
    cfg = RuntimeLoopConfig(
        agent_id="supervisor_agent",
        goal="Resolve multi-hop entity relationships with bounded tool calls",
        max_steps=3,
        max_tool_calls=10,
        max_tokens=4000,
        timeout_seconds=30.0,
        allowed_tools=("query_knowledge_graph", "inspect_graph_snapshot"),
    )
    loop = AgentRuntimeLoop(cfg)

    digest_hex = hashlib.sha256(b"canonical_evidence_payload").hexdigest()
    claim_check = ClaimCheckPointer(
        source_id="src:graph:node_1",
        evidence_id="ev:assertion_101",
        content_digest=digest_hex,
        locator={"table": "assertions", "row_id": "101"},
    )
    cc_valid, cc_errors = verify_claim_check_pointers([claim_check])
    assert cc_valid is True
    assert cc_errors == []

    def observe_fn(state: Dict[str, Any], step_idx: int) -> Dict[str, Any]:
        return {"step": step_idx, "resolved_hops": state.get("resolved_hops", 0)}

    def decide_fn(obs: Dict[str, Any], step_idx: int) -> Dict[str, Any]:
        return {
            "action": f"expand_hop_{step_idx}",
            "delegated": step_idx == 1,
            "candidates": [("expand_bridge_edges", 0.88), ("stop_early", 0.42)],
        }

    def act_fn(state: Dict[str, Any], decision: Dict[str, Any], step_idx: int) -> Dict[str, Any]:
        task, ret = loop.dispatch_subagent_task(
            recipient_agent_id=NeeAgentHierarchy.NAVIGATOR_AGENT,
            objective=f"Expand hop {step_idx} bridge predicates",
            scope_in=["assertions", "nodes"],
            scope_out=["shadow_graph_nodes"],
            allowed_tools=["query_knowledge_graph"],
            deliverable={"hop": step_idx},
            definition_of_done="Hop frontier expanded with verified claim checks",
            claim_checks=[claim_check],
        )
        assert task.status == A2ATaskStatus.COMPLETED
        assert ret["status"] == "COMPLETE"
        return {
            "tool_calls_used": 2,
            "tokens_used": 250,
            "a2a_task_id": task.task_id,
            "state_updates": {"resolved_hops": step_idx, f"hop_{step_idx}_task": task.task_id},
        }

    def evaluate_fn(state: Dict[str, Any], act_res: Dict[str, Any], step_idx: int) -> Dict[str, Any]:
        hops = act_res["state_updates"]["resolved_hops"]
        done = hops >= 2
        return {
            "done": done,
            "quality_score": 0.50 * hops,
            "reason": "Two-hop chain verified." if done else "Need second hop.",
        }

    result = loop.execute(
        initial_state={"resolved_hops": 0},
        observe_fn=observe_fn,
        decide_fn=decide_fn,
        act_fn=act_fn,
        evaluate_fn=evaluate_fn,
    )

    assert result.terminal_outcome == LoopTerminalOutcome.COMPLETED
    assert result.steps_executed == 2
    assert result.total_tool_calls == 4
    assert result.total_tokens == 500
    assert result.lifecycle_valid is True
    assert len(result.a2a_tasks) == 2
    assert result.final_output["resolved_hops"] == 2
    serialized = result.to_dict()
    assert serialized["terminal_outcome"] == "COMPLETED"
    assert serialized["step_records"][0]["margin_decision"]["outcome_type"] == "AUTONOMOUS"


def test_roundtable_orchestrator_three_phase_deliberation_and_dissent_e2e() -> None:
    """Verifies 12-agent PROPOSE / CHALLENGE / REBUT deliberation, ClaimCheck verification, and dissent ledger."""
    orchestrator = RoundtableOrchestrator(
        topic="Runtime Agentic Evolution and Bounded Multi-Hop Autonomy",
        custom_votes={"Red_Team_Exploit_Guard": False},
    )
    plan_md = orchestrator.start_debate()

    assert orchestrator.consensus_reached is True
    assert len(orchestrator.active_agents) == len(ROSTER) == 12
    phases = [r.phase for r in orchestrator.deliberation_rounds]
    assert "PROPOSE" in phases
    assert "CHALLENGE" in phases
    assert "REBUT" in phases
    assert len(orchestrator.deliberation_rounds) == 12
    assert len(orchestrator.minority_dissents) == 1
    assert orchestrator.minority_dissents[0]["agent_id"] == "Red_Team_Exploit_Guard"
    assert "Minority Dissent Ledger" in plan_md


def test_nee_agent_hierarchy_registers_all_nine_specialists_and_evaluates_completion() -> None:
    """Verifies that NeeAgentHierarchy binds all 9 specialists and upgrades on retry failure."""
    specs = NeeAgentHierarchy.get_specialist_specs()
    assert len(specs) == 9
    expected_agents = {
        NeeAgentHierarchy.INGESTION_AGENT,
        NeeAgentHierarchy.RETRIEVAL_AGENT,
        NeeAgentHierarchy.SPARK_AGENT,
        NeeAgentHierarchy.NAVIGATOR_AGENT,
        NeeAgentHierarchy.DEVIL_ADVOCATE_AGENT,
        NeeAgentHierarchy.RECONCILIATION_AGENT,
        NeeAgentHierarchy.INFLECTION_AGENT,
        NeeAgentHierarchy.TEMPORAL_DRIFT_AGENT,
        NeeAgentHierarchy.CALIBRATOR_AGENT,
    }
    assert set(specs.keys()) == expected_agents

    subagent_configs = NeeAgentHierarchy.build_subagent_configs()
    assert len(subagent_configs) == 9

    ok, status_msg, upgraded = NeeAgentHierarchy.evaluate_specialist_completion(
        NeeAgentHierarchy.NAVIGATOR_AGENT,
        {
            "task_id": "t-1",
            "status": "COMPLETE",
            "deliverable": {"nodes": 4},
            "evidence_refs": ["ev-1"],
            "metrics": {"tool_calls_used": 2},
        },
    )
    assert ok is True
    assert status_msg == "COMPLETED"
    assert upgraded is None

    ok_fail, fail_msg, upgraded_spec = NeeAgentHierarchy.evaluate_specialist_completion(
        NeeAgentHierarchy.NAVIGATOR_AGENT,
        {
            "task_id": "t-2",
            "status": "FAILED",
            "error_code": "FRONTIER_STALLED",
            "error_message": "Bridge expansion stalled on hop 2.",
            "deliverable": {},
            "evidence_refs": ["ev-1"],
            "metrics": {"tool_calls_used": 3},
        },
        failure_count=1,
    )
    assert ok_fail is False
    assert "RETRY_WITH_UPGRADED_SPECIALIST" in fail_msg
    assert upgraded_spec is not None
    assert "RECOVERY MODE ACTIVE" in upgraded_spec.system_instructions


def test_runtime_design_governance_artifact_is_valid() -> None:
    """Verifies that the canonical runtime design contract exists and declares bounded budgets."""
    design_path = Path("config/governance/runtime_designs/agentic_runtime_evolution.json")
    assert design_path.is_file()
    payload = json.loads(design_path.read_text(encoding="utf-8"))
    assert payload["termination"]["max_steps"] == 3
    assert payload["budgets"]["tool_calls"] == 20
    assert payload["budgets"]["delegation_depth"] == 2
    assert len(payload["authority"]["delegation_targets"]) == 9
    assert len(payload["human_control"]["approvals"]) >= 3
    margin_dec = evaluate_autonomy_margin([("route_a", 0.85), ("route_b", 0.55)])
    assert margin_dec.outcome_type == "AUTONOMOUS"
