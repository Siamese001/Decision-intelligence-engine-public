"""End-to-end verification of Wave 2 runtime outcome streaming and durable reasoning effort hysteresis."""
from __future__ import annotations

from pathlib import Path

from dual_pipeline.agent_architecture_core.runtime_loop import AgentRuntimeLoop, RuntimeLoopConfig
from dual_pipeline.decision_engine_core.devil_advocate_agent import CounterfactualDevilAdvocateAgent
from dual_pipeline.learning_adapters.agent_outcome_adapter import OutcomeType
from dual_pipeline.learning_adapters.live_pipeline_integrator import LivePipelineIntegrator
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.query_core.navigator_agent import MultiHopQueryNavigatorAgent
from dual_pipeline.token_optimization.agent import TokenOptimizationAgent
from dual_pipeline.token_optimization.schemas import ReasoningEffortTier


def test_runtime_agents_stream_outcomes_to_live_pipeline_integrator_e2e(tmp_path: Path) -> None:
    """Verifies that AgentRuntimeLoop, MultiHopQueryNavigatorAgent, and CounterfactualDevilAdvocateAgent stream outcomes."""
    integrator = LivePipelineIntegrator(runtime_root=tmp_path)

    # 1. Standalone AgentRuntimeLoop stagnates -> streams RETRY_EXHAUSTION
    stagnated_loop = AgentRuntimeLoop(
        RuntimeLoopConfig(agent_id="stagnated_test_agent", goal="Test stagnation streaming", max_steps=3),
        integrator=integrator,
    )
    res_loop = stagnated_loop.execute(
        initial_state={"v": 1},
        observe_fn=lambda s, _: dict(s),
        decide_fn=lambda _o, _: {"action": "noop", "candidates": [("noop", 0.90), ("halt", 0.10)]},
        act_fn=lambda _s, _d, _: {"tool_calls_used": 1, "tokens_used": 10, "state_updates": {"v": 1}},
        evaluate_fn=lambda _s, _a, _: {"done": False, "quality_score": 0.2},
    )
    assert res_loop.terminal_outcome.value == "STAGNATED"

    # 2. MultiHopQueryNavigatorAgent partial coverage -> streams RETRIEVAL_MISS (and suppresses inner loop SUCCESS)
    nav = MultiHopQueryNavigatorAgent(max_steps=2, max_tool_calls=4, integrator=integrator)
    nav_res = nav.navigate(
        query="How does initiative_alpha connect to missing_workstream_omega?",
        seed_entities=["initiative_alpha"],
        subquestions=[
            "How does initiative_alpha connect to capability_beta?",
            "How does capability_beta address missing_workstream_omega?",
        ],
        assertions=[
            {
                "assertion_id": "a_1",
                "subject_id": "initiative_alpha",
                "predicate": "CONTRIBUTES_TO",
                "object_id": "capability_beta",
                "claim_text": "initiative_alpha contributes to capability_beta",
            }
        ],
    )
    assert nav_res.coverage_ratio == 0.5
    assert len(nav_res.unresolved_subquestions) == 1

    # 3. CounterfactualDevilAdvocateAgent with disconfirming risks -> streams ABSTAIN
    devil = CounterfactualDevilAdvocateAgent(integrator=integrator)
    devil_res = devil.challenge(
        options=[{"option_id": "opt_a", "label": "Option A", "confidence": 0.72}, {"option_id": "opt_b", "label": "Option B", "confidence": 0.65}],
        evidence_records=[
            {"evidence_id": "ev_r1", "option_id": "opt_a", "predicate": "HAS_RISK", "claim_text": "Option A has latency risk"},
            {"evidence_id": "ev_r2", "option_id": "opt_a", "predicate": "CONTRADICTS", "claim_text": "Option A contradicts SLA"},
        ],
    )
    assert devil_res.requires_hitl_escalation is True

    # Verify JSONL persistence and draining into ClosedLoopLearningEngine cohorts
    assert integrator.events_log_path.is_file()
    ex_events = integrator.get_recorded_events(outcome_type=OutcomeType.RETRY_EXHAUSTION)
    miss_events = integrator.get_recorded_events(outcome_type=OutcomeType.RETRIEVAL_MISS)
    assert len(ex_events) == 1
    assert len(miss_events) == 1

    # Trigger second retrieval miss so cohort reaches min_occurrences=2
    nav.navigate(
        query="How does initiative_alpha connect to missing_workstream_omega?",
        seed_entities=["initiative_alpha"],
        subquestions=["How does capability_beta address missing_workstream_omega?"],
        assertions=[],
    )
    drained = integrator.drain_to_cohort_items()
    assert len(drained) >= 2
    assert integrator.cursor_path.is_file()

    engine = ClosedLoopLearningEngine(runtime_root=tmp_path, proposals_dir=tmp_path / "proposals")
    cohorts = engine.analyze_failure_cohorts(drained, min_occurrences=2)
    assert any("retrieval_miss" in c["cohort_key"] for c in cohorts)


def test_token_optimization_agent_bidirectional_effort_hysteresis_e2e(tmp_path: Path) -> None:
    """Verifies immediate escalation to XHIGH and 3-clean-run step-down hysteresis (XHIGH -> HIGH -> MEDIUM -> LOW)."""
    integrator = LivePipelineIntegrator(runtime_root=tmp_path)
    agent = TokenOptimizationAgent(integrator=integrator)
    task = "router_classification"

    # Base tier for router_classification is LOW
    assert agent.calibrate_reasoning_effort(task) == ReasoningEffortTier.LOW

    # Budget exhaustion escalates immediately to XHIGH and persists RETRY_EXHAUSTION to integrator
    tier_ex = agent.calibrate_from_runtime_loop(
        task, {"terminal_outcome": "BUDGET_EXHAUSTED", "steps_executed": 3}
    )
    assert tier_ex == ReasoningEffortTier.XHIGH
    assert agent.calibrate_reasoning_effort(task) == ReasoningEffortTier.XHIGH
    assert len(integrator.get_recorded_events(outcome_type=OutcomeType.RETRY_EXHAUSTION)) == 1

    # 3 consecutive clean 1-step runs with margin > 0.20 step down XHIGH -> HIGH
    clean_run = {"terminal_outcome": "COMPLETED", "steps_executed": 1, "margin_delta": 0.35}
    assert agent.calibrate_from_runtime_loop(task, clean_run) == ReasoningEffortTier.XHIGH
    assert agent.calibrate_from_runtime_loop(task, clean_run) == ReasoningEffortTier.XHIGH
    assert agent.calibrate_from_runtime_loop(task, clean_run) == ReasoningEffortTier.HIGH
    assert agent.calibrate_reasoning_effort(task) == ReasoningEffortTier.HIGH

    # Next 3 clean runs step down HIGH -> MEDIUM
    assert agent.calibrate_from_runtime_loop(task, clean_run) == ReasoningEffortTier.HIGH
    assert agent.calibrate_from_runtime_loop(task, clean_run) == ReasoningEffortTier.HIGH
    assert agent.calibrate_from_runtime_loop(task, clean_run) == ReasoningEffortTier.MEDIUM

    # Next 3 clean runs step down MEDIUM -> LOW (base floor)
    assert agent.calibrate_from_runtime_loop(task, clean_run) == ReasoningEffortTier.MEDIUM
    assert agent.calibrate_from_runtime_loop(task, clean_run) == ReasoningEffortTier.MEDIUM
    assert agent.calibrate_from_runtime_loop(task, clean_run) == ReasoningEffortTier.LOW
    assert agent.calibrate_reasoning_effort(task) == ReasoningEffortTier.LOW
