"""tests/end_to_end/test_system_learning_agent_e2e.py

End-to-end verification for the System Learning Agent (`system_learning_assurance`)
and its 6-surface closed-loop system learning assurance:
1. Canonical System Learning Agent specification & SSOT pointer registration.
2. Surface 1 (`AdaptiveRetrievalCalibratorAgent`): provenance, 11-gate replay, and damped step bounds.
3. Surface 2 (`outcome_streaming`, `stream_navigator_outcome`): Layer 0 runtime outcome emission.
4. Surface 3 & 4 (`ClosedLoopLearningEngine.evaluate_post_apply_canary`, `seal_learning_stage_chain`):
   6-stage `LP0`–`LP5` Merkle sealing and auto-rollback on canary regression.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, List

from dual_pipeline.agent_architecture_core.outcome_streaming import stream_navigator_outcome
from dual_pipeline.learning_loop_core import (
    CANARY_STAGE,
    LEARNING_STAGES_WITH_CANARY,
    record_learning_event,
    seal_learning_stage_chain,
)
from dual_pipeline.learning_loop_core.retrieval_calibrator_agent import AdaptiveRetrievalCalibratorAgent
from tools.lint_prompt_ssot import EXPECTED_CANONICAL_PROMPTS, run_prompt_ssot_lint
from tools.scheduled_task_core.contracts import TWELVE_GOVERNED_TASK_IDS, get_task_definition
from tools.scheduled_task_core.launch_adapters import AntigravityHostLaunchAdapter
from tools.task_restoration_core.ssot_pointers import (
    TASK_SIDECAR_MAP,
    generate_ssot_pointer_prompt,
    is_valid_pointer_prompt,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class _E2EOutcomeIntegrator:
    def __init__(self) -> None:
        self.misses: List[dict[str, Any]] = []

    def record_retrieval_miss(self, **kwargs: Any) -> None:
        self.misses.append(kwargs)


def test_system_learning_agent_canonical_prompt_and_ssot_registration_e2e(tmp_path: Path) -> None:
    """Verify System Learning Agent prompt specification, SSOT pointer registration, and launch adapter execution."""
    assert "system_learning_assurance" in TASK_SIDECAR_MAP
    assert "system_learning_assurance" in EXPECTED_CANONICAL_PROMPTS
    assert "system_learning_assurance" in TWELVE_GOVERNED_TASK_IDS

    info = TASK_SIDECAR_MAP["system_learning_assurance"]
    assert info["sidecar_dir"] == "system-learning-assurance-agent"
    assert info["canonical_prompt"] == "automations/prompts/system_learning_agent.md"
    assert info["title"] == "System Learning Agent"

    tdef = get_task_definition("system_learning_assurance")
    assert tdef["prompt_path"] == "automations/prompts/system_learning_agent.md"
    assert tdef["registration_status"]["status"] == "RECONCILED_ACTIVE"

    pointer = generate_ssot_pointer_prompt("system_learning_assurance", REPO_ROOT)
    assert is_valid_pointer_prompt(pointer)

    prompt_path = REPO_ROOT / info["canonical_prompt"]
    assert prompt_path.is_file()
    content = prompt_path.read_text(encoding="utf-8")
    assert "Canonical Task: system_learning_assurance" in content
    assert "Exhaustive 8-Surface Closed-Loop System Learning" in content
    assert "STAGE 1: EXHAUSTIVE 8-SURFACE SYSTEM LEARNING FUNCTIONALITY GAP AUDIT" in content
    assert "INV-LEARNING-GAPS-FIRST-GATE" in content
    assert "INV-LEARNING-ROI-ENHANCEMENT-GATE" in content
    assert "UNCONDITIONAL_WAVE_PLAN_MANDATE" in content
    assert "INV-RUNTIME-PROOF-5POINT" in content
    assert "exhaustive_eight_surface_system_learning_audit" in tdef["capabilities"]
    assert "unified_eight_surface_learning_cycle" in tdef["capabilities"]
    assert "8 repository system learning surfaces" in tdef["domain_routing"]["d03_query"]

    ok, errors = run_prompt_ssot_lint(REPO_ROOT)
    assert ok, f"Prompt SSOT lint failed: {errors}"

    adapter = AntigravityHostLaunchAdapter(repo_root=REPO_ROOT)
    res_clean = adapter.launch_task("system_learning_assurance", "scheduled", tmp_path, reject_empty_callback=False)
    assert res_clean.terminal_outcome == "COMPLETE"
    assert res_clean.metadata["surfaces_evaluated"] == 8
    assert res_clean.metadata["unified_cycle_summary"]["surfaces_scanned"] == 8

    (tmp_path / "system_learning_gap.json").write_text("{}", encoding="utf-8")
    res_gap = adapter.launch_task("system_learning_assurance", "scheduled", tmp_path, reject_empty_callback=False)
    assert res_gap.terminal_outcome == "HITL_REQUIRED"
    assert res_gap.implementation_plan_path is not None
    assert Path(res_gap.implementation_plan_path).is_file()


def test_end_to_end_six_surface_system_learning_pipeline_and_canary_seal(tmp_path: Path) -> None:
    """Verify end-to-end execution across calibrator provenance, L0 outcome streaming, and 6-stage LP0-LP5 seal."""
    assert LEARNING_STAGES_WITH_CANARY == ("LP0", "LP1", "LP2", "LP3", "LP4", "LP5")
    assert CANARY_STAGE == "LP5"

    # 1. Surface 2: Layer 0 runtime outcome emission
    integrator = _E2EOutcomeIntegrator()
    stream_navigator_outcome(
        integrator,
        stream_outcomes=True,
        query="LENS commercial blocker analysis",
        unresolved_subquestions=["commercial blocker owner"],
        coverage_ratio=0.50,
        trace_id="run-sys-learn-e2e-1",
    )
    assert len(integrator.misses) == 1
    assert integrator.misses[0]["source_agent"] == "multihop_query_navigator_agent"

    # 2. Surface 1: AdaptiveRetrievalCalibratorAgent proposal generation with damped step bounds
    calibrator = AdaptiveRetrievalCalibratorAgent()
    cal_res = calibrator.calibrate(
        failure_cohort=[
            {"event_id": "ev-1", "dimension": "relational", "gradient": 0.85},
            {"event_id": "ev-2", "dimension": "relational", "gradient": 0.80},
            {"event_id": "ev-3", "dimension": "relational", "gradient": 0.90},
        ],
        current_multipliers={"relational": 0.45, "temporal": 0.35},
        baseline_metrics={"ndcg_at_10": 0.82, "recall_at_50": 0.88, "p95_latency_ms": 180.0},
        candidate_metrics={"ndcg_at_10": 0.86, "recall_at_50": 0.91, "p95_latency_ms": 175.0},
        proposals_dir=tmp_path / "proposals",
    )
    assert cal_res.accepted is True
    assert cal_res.proposal_artifact is not None
    assert cal_res.proposal_artifact["auto_apply"] is False

    # 3. Surface 4: 6-stage LP0-LP5 Merkle lifecycle sealing
    db_path = tmp_path / "ledger" / "learning.sqlite"
    run_id = "run-sys-learn-e2e-seal"
    p_id = cal_res.proposal_artifact["proposal_id"]
    stage_events = []
    parent_span = None
    for idx, stg in enumerate(LEARNING_STAGES_WITH_CANARY):
        ev = record_learning_event(
            stage=stg,
            event_type=f"stage_{stg.lower()}_verified",
            status="PASS",
            run_id=run_id,
            proposal_id=p_id,
            payload_reference=f"ref-{idx}",
            artifact_data={"stage": stg, "verified": True},
            db_path=db_path,
            parent_span_id=parent_span,
        )
        parent_span = ev["span_id"]
        stage_events.append(ev)

    seal_res = seal_learning_stage_chain(
        proposal_id=p_id,
        run_id=run_id,
        stage_events=stage_events,
        output_dir=tmp_path / "runs" / run_id,
        require_canary=True,
    )
    assert seal_res["stage_count"] == 6
    assert seal_res["stages_sealed"] == list(LEARNING_STAGES_WITH_CANARY)


def test_wave1_four_system_learning_gaps_counterfactual_e2e(tmp_path: Path) -> None:
    """Counterfactual verification for GAP-01 through GAP-04 across all 6 learning surfaces."""
    import subprocess
    import sys

    from dual_pipeline.agent_architecture_core.outcome_streaming import (
        append_runtime_outcome_event,
        stream_devil_advocate_outcome,
        stream_loop_terminal_outcome,
        stream_navigator_outcome,
    )
    from dual_pipeline.decision_engine_core.devil_advocate_agent import DevilAdvocateAgent
    from dual_pipeline.learning_engine import ClosedLoopLearningEngine, verify_post_apply_canary_and_seal
    from dual_pipeline.learning_engine_core.rollback import rollback_applied_proposal
    from dual_pipeline.learning_loop import (
        LearningChangeProposal,
        cluster_failure_cohorts,
        scan_feedback_for_proposals,
        simulate_proposal,
    )
    from dual_pipeline.learning_loop_core.retrieval_calibrator_agent import (
        AdaptiveRetrievalCalibratorAgent,
        normalize_hybrid_weights,
    )
    from dual_pipeline.learning_loop_core.seal import (
        REQUIRED_STAGES,
        evaluate_post_apply_canary,
        seal_learning_lifecycle,
    )
    from dual_pipeline.query_core.navigator_agent import QueryReasoningNavigatorAgent
    from dual_pipeline.staleness_core.temporal_drift_agent import TemporalStalenessDriftAgent
    from dual_pipeline.token_optimization.agent import TokenCostOptimizationAgent
    from dual_pipeline.transcript_qa_core.alias_registry import persist_learned_alias
    from dual_pipeline.transcript_qa_core.reconciliation_agent import (
        CrossTranscriptEntityReconciliationAgent,
        _detect_owner_drift_directives,
    )

    assert QueryReasoningNavigatorAgent is not None
    assert DevilAdvocateAgent is not None
    assert TokenCostOptimizationAgent is not None
    assert CrossTranscriptEntityReconciliationAgent is not None
    assert TemporalStalenessDriftAgent is not None
    assert LearningChangeProposal is not None
    assert callable(simulate_proposal)
    assert REQUIRED_STAGES == ("LP0", "LP1", "LP2", "LP3", "LP4")

    # GAP-01: 3-way simplex renormalization + dimension/gradient scaling
    norm3 = normalize_hybrid_weights(0.50, 0.30, 0.40)
    assert "colbert_weight" in norm3
    assert abs(norm3["dense_weight"] + norm3["sparse_weight"] + norm3["colbert_weight"] - 1.0) < 1e-6

    cal = AdaptiveRetrievalCalibratorAgent()
    cand_mults, cand_weights, _ = cal._compute_candidate_parameters(
        failure_cohort=[
            {"event_id": "ev-1", "dimension": "relational", "gradient": 0.90},
            {"event_id": "ev-2", "dimension": "relational", "gradient": 0.90},
            {"event_id": "ev-3", "dimension": "relational", "gradient": 0.90},
        ],
        current_multipliers={"relational": 0.45},
        current_weights={"dense_weight": 0.5, "sparse_weight": 0.3, "colbert_weight": 0.2},
    )
    assert cand_mults["relational"] > 0.55
    assert "colbert_weight" in cand_weights

    # GAP-02: suppress_loop_success=True still streams STAGNATED/BUDGET_EXHAUSTED, plus append_runtime_outcome_event
    events_log = tmp_path / "runtime_outcomes.jsonl"
    ev_written = append_runtime_outcome_event(
        {"event_type": "LOOP_STAGNATED", "source_agent": "test_agent"},
        log_path=events_log,
    )
    assert ev_written["event_type"] == "LOOP_STAGNATED"
    assert events_log.is_file()

    class _Recorder:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict[str, Any]]] = []

        def record_grounding_rejection(self, **kw: Any) -> None:
            self.events.append(("grounding", kw))

        def record_judge_retry_exhaustion(self, **kw: Any) -> None:
            self.events.append(("exhaustion", kw))

        def record_retrieval_miss(self, **kw: Any) -> None:
            self.events.append(("miss", kw))

    rec = _Recorder()
    stream_loop_terminal_outcome(
        rec,
        stream_outcomes=True,
        suppress_loop_success=True,
        agent_id="nav_agent",
        terminal_outcome="STAGNATED",
        iterations=3,
        max_iterations=5,
        accumulated_cost_usd=0.02,
        trace_id="tr-stagnated",
    )
    assert len(rec.events) == 1
    assert rec.events[0][0] == "grounding"

    stream_navigator_outcome(
        rec,
        stream_outcomes=True,
        query="test query",
        unresolved_subquestions=["sub-q1"],
        coverage_ratio=0.4,
        trace_id="tr-nav",
    )
    assert rec.events[-1][1]["details"].get("depth_escalation") is True

    stream_devil_advocate_outcome(
        rec,
        stream_outcomes=True,
        option_id="opt-1",
        original_confidence=0.80,
        penalized_confidence=0.40,
        Triggered_rules=["R1", "R2"],
        trace_id="tr-da",
    )
    assert "adversarial_survival_score" in rec.events[-1][1]["details"]

    # GAP-03: OWNER_DRIFT & TEMPORAL_STALENESS cohort clustering + learned alias suppression
    alias_path = tmp_path / "aliases.json"
    persist_learned_alias("Jonathon", "Jonathan", "prop-alias-1", registry_path=alias_path)
    directives: list[dict[str, Any]] = []
    _detect_owner_drift_directives(
        {"ACT-1": "Jonathon"},
        {"ACT-1": "Jonathan"},
        directives,
        rec,
        True,
        "tr-alias",
        alias_registry_path=alias_path,
    )
    assert len(directives) == 0

    cohorts = cluster_failure_cohorts(
        [
            {"failure_type": "OWNER_DRIFT", "from_alias": "Mitch", "to_canonical": "Mitchell"},
            {"failure_type": "OWNER_DRIFT", "from_alias": "Mitch", "to_canonical": "Mitchell"},
            {"failure_type": "OWNER_DRIFT", "from_alias": "Mitch", "to_canonical": "Mitchell"},
            {"failure_type": "TEMPORAL_STALENESS", "domain": "lens", "observed_decay_tau": 120.0},
            {"failure_type": "TEMPORAL_STALENESS", "domain": "lens", "observed_decay_tau": 120.0},
            {"failure_type": "TEMPORAL_STALENESS", "domain": "lens", "observed_decay_tau": 120.0},
        ],
        min_cohort_size=3,
    )
    assert "OWNER_DRIFT" in cohorts
    assert "TEMPORAL_STALENESS" in cohorts

    proposals = scan_feedback_for_proposals(
        feedback_items=[
            {"failure_type": "TEMPORAL_STALENESS", "event_id": "ts-1", "suggested_decay_tau": 120.0},
            {"failure_type": "TEMPORAL_STALENESS", "event_id": "ts-2", "suggested_decay_tau": 120.0},
            {"failure_type": "TEMPORAL_STALENESS", "event_id": "ts-3", "suggested_decay_tau": 120.0},
            {"failure_type": "OWNER_DRIFT", "event_id": "od-1", "from_alias": "Mitch", "to_canonical": "Mitchell"},
            {"failure_type": "OWNER_DRIFT", "event_id": "od-2", "from_alias": "Mitch", "to_canonical": "Mitchell"},
            {"failure_type": "OWNER_DRIFT", "event_id": "od-3", "from_alias": "Mitch", "to_canonical": "Mitchell"},
        ],
        min_cohort_size=3,
    )
    assert len(proposals) == 2

    # GAP-04: evaluate_post_apply_canary, seal_learning_lifecycle, rollback_applied_proposal, CLI verify-canary
    canary_check = evaluate_post_apply_canary(
        baseline_metrics={"mrr": 0.80, "ndcg": 0.82},
        canary_metrics={"mrr": 0.81, "ndcg": 0.83},
    )
    assert canary_check["passed"] is True

    engine = ClosedLoopLearningEngine(storage_dir=tmp_path / "engine_store")
    canary_seal = verify_post_apply_canary_and_seal(
        engine,
        proposal_id="prop-canary-1",
        baseline_metrics={"mrr": 0.80, "ndcg": 0.82},
        canary_metrics={"mrr": 0.75, "ndcg": 0.82},
        previous_parameters={"retrieval.bm25_weight": 0.35},
    )
    assert canary_seal["status"] == "ROLLED_BACK"

    rb_res = rollback_applied_proposal(
        proposal_id="prop-rb-direct",
        previous_parameters={"retrieval.bm25_weight": 0.35},
        reason="canary_mrr_regression",
        active_params_path=tmp_path / "engine_store" / "active_parameters.json",
        rollback_ledger_path=tmp_path / "engine_store" / "rollback_ledger.jsonl",
    )
    assert rb_res["status"] == "ROLLED_BACK"

    cli_res = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "govern_learning_proposals.py"),
            "verify-canary",
            "--proposal-id",
            "prop-cli-canary-1",
            "--baseline-mrr",
            "0.82",
            "--canary-mrr",
            "0.83",
            "--storage-dir",
            str(tmp_path / "cli_canary_store"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert cli_res.returncode == 0

