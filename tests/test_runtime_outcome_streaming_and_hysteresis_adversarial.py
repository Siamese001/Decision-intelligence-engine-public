"""Adversarial and edge-case tests for Wave 2 runtime outcome streaming and effort hysteresis.

Covers mandatory edge-case categories:
1. Oscillation prevention on marginal completions (steps > 1 or margin <= 0.20 reset clean streak).
2. Denominator deduplication (domain agents wrapping AgentRuntimeLoop emit exactly 1 SUCCESS event, not 2).
3. Hermetic zero-disk I/O when stream_outcomes=False and integrator=None.
"""
from __future__ import annotations

from pathlib import Path

from dual_pipeline.agent_architecture_core.runtime_loop import AgentRuntimeLoop, RuntimeLoopConfig
from dual_pipeline.decision_engine_core.devil_advocate_agent import CounterfactualDevilAdvocateAgent
from dual_pipeline.learning_adapters.live_pipeline_integrator import LivePipelineIntegrator
from dual_pipeline.query_core.navigator_agent import MultiHopQueryNavigatorAgent
from dual_pipeline.token_optimization.agent import TokenOptimizationAgent
from dual_pipeline.token_optimization.schemas import ReasoningEffortTier


def test_marginal_completions_reset_hysteresis_streak_and_prevent_oscillation(tmp_path: Path) -> None:
    """Category 1: Multi-step (steps > 1) or narrow-margin (margin <= 0.20) completions reset the 3-run counter."""
    integrator = LivePipelineIntegrator(runtime_root=tmp_path)
    agent = TokenOptimizationAgent(integrator=integrator)
    task = "router_classification"

    agent.calibrate_from_runtime_loop(task, {"terminal_outcome": "BUDGET_EXHAUSTED", "steps_executed": 3})
    assert agent.calibrate_reasoning_effort(task) == ReasoningEffortTier.XHIGH

    clean_run = {"terminal_outcome": "COMPLETED", "steps_executed": 1, "margin_delta": 0.35}
    marginal_steps = {"terminal_outcome": "COMPLETED", "steps_executed": 2, "margin_delta": 0.40}
    marginal_margin = {"terminal_outcome": "COMPLETED", "steps_executed": 1, "margin_delta": 0.15}

    # 2 clean runs followed by a 2-step run must reset streak and hold XHIGH
    assert agent.calibrate_from_runtime_loop(task, clean_run) == ReasoningEffortTier.XHIGH
    assert agent.calibrate_from_runtime_loop(task, clean_run) == ReasoningEffortTier.XHIGH
    assert agent.calibrate_from_runtime_loop(task, marginal_steps) == ReasoningEffortTier.XHIGH
    assert agent.calibrate_from_runtime_loop(task, clean_run) == ReasoningEffortTier.XHIGH

    # Another clean run + narrow margin run resets streak again
    assert agent.calibrate_from_runtime_loop(task, clean_run) == ReasoningEffortTier.XHIGH
    assert agent.calibrate_from_runtime_loop(task, marginal_margin) == ReasoningEffortTier.XHIGH
    assert agent.calibrate_from_runtime_loop(task, clean_run) == ReasoningEffortTier.XHIGH


def test_domain_agents_suppress_inner_loop_success_to_prevent_denominator_inflation(tmp_path: Path) -> None:
    """Category 2: Successful Navigator and Devil's Advocate runs emit 1 domain SUCCESS event without duplicate loop SUCCESS."""
    integrator = LivePipelineIntegrator(runtime_root=tmp_path)

    nav = MultiHopQueryNavigatorAgent(integrator=integrator)
    nav_res = nav.navigate(
        query="How does alpha_node connect to beta_node?",
        seed_entities=["alpha_node"],
        subquestions=["How does alpha_node connect to beta_node?"],
        assertions=[
            {
                "assertion_id": "a_full",
                "subject_id": "alpha_node",
                "predicate": "ADDRESSES",
                "object_id": "beta_node",
                "claim_text": "alpha_node addresses beta_node",
            }
        ],
    )
    assert nav_res.coverage_ratio == 1.0

    devil = CounterfactualDevilAdvocateAgent(integrator=integrator)
    devil_res = devil.challenge(
        options=[{"option_id": "opt_1", "label": "Option 1", "confidence": 0.90}, {"option_id": "opt_2", "label": "Option 2", "confidence": 0.50}],
        evidence_records=[],
    )
    assert devil_res.requires_hitl_escalation is False

    all_events = integrator.get_recorded_events()
    # Exactly 1 event from navigator and 1 event from devil advocate (no duplicate runtime_loop SUCCESS)
    assert len(all_events) == 2
    assert {e.source_agent for e in all_events} == {
        "multihop_query_navigator_agent",
        "counterfactual_devil_advocate_agent",
    }


def test_default_stream_outcomes_false_performs_zero_disk_writes(tmp_path: Path) -> None:
    """Category 3: When integrator=None and stream_outcomes=False, no JSONL files are created."""
    loop = AgentRuntimeLoop(
        RuntimeLoopConfig(agent_id="silent_agent", goal="Verify zero I/O", max_steps=1),
        integrator=None,
        stream_outcomes=False,
    )
    res = loop.execute(
        initial_state={"ok": True},
        observe_fn=lambda s, _: dict(s),
        decide_fn=lambda _o, _: {"action": "done", "candidates": [("done", 0.95), ("wait", 0.05)]},
        act_fn=lambda _s, _d, _: {"tool_calls_used": 1, "tokens_used": 5},
        evaluate_fn=lambda _s, _a, _: {"done": True, "quality_score": 1.0},
    )
    assert res.terminal_outcome.value == "COMPLETED"
    assert not list(tmp_path.rglob("*.jsonl"))
