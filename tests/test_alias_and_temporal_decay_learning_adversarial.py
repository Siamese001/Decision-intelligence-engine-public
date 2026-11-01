"""Adversarial and edge-case tests for Wave 3 Owner Alias Learning and Bi-Temporal Decay Calibration.

Covers mandatory edge-case categories:
1. Unrelated single-occurrence owner drifts do not collide into a false cohort.
2. Placeholder ('system:unmapped' -> 'system:canonical'), self-mapping, and Output 01 mutation violations fail closed in _simulate_alias.
3. Concurrent contradictions (delta_days == 0) are excluded from decay step scaling, and decay_tau_seconds clamps strictly to [30.0, 1800.0].
"""
from __future__ import annotations

from pathlib import Path

from dual_pipeline.core import utc_now, write_json
from dual_pipeline.learning_adapters.live_pipeline_integrator import LivePipelineIntegrator
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.learning_loop_core import scan_answer_eval_receipts
from dual_pipeline.staleness_core.temporal_drift_agent import (
    SupersessionProposal,
    TemporalDriftScanResult,
    compute_empirical_decay_step_seconds,
)
from dual_pipeline.transcript_qa_core.reconciliation_agent import CrossArtifactReconciliationAgent


def test_unrelated_single_owner_drifts_do_not_form_false_cohort(tmp_path: Path) -> None:
    """Category 1: Two distinct 1-occurrence owner drift pairs remain in separate buckets below min_occurrences=2."""
    integrator = LivePipelineIntegrator(runtime_root=tmp_path)
    reconciler = CrossArtifactReconciliationAgent(integrator=integrator)

    o1_a = "Alice Chen: I will review the schema.\n"
    o2_a = "- Review schema (**Owner:** Alice Chen)\n"
    o3_a = "- Review schema (**Owner:** Alicia Chen)\n"

    o1_b = "David Kim: I will deploy the gateway.\n"
    o2_b = "- Deploy gateway (**Owner:** David Kim)\n"
    o3_b = "- Deploy gateway (**Owner:** Dave Kim)\n"

    reconciler.reconcile(output_01_text=o1_a, output_02_text=o2_a, output_03_text=o3_a)
    reconciler.reconcile(output_01_text=o1_b, output_02_text=o2_b, output_03_text=o3_b)

    items = integrator.drain_to_cohort_items()
    engine = ClosedLoopLearningEngine(runtime_root=tmp_path, proposals_dir=tmp_path / "proposals")
    cohorts = engine.analyze_failure_cohorts(items, min_occurrences=2)
    alias_cohorts = [c for c in cohorts if "adjudication_error" in c["cohort_key"]]
    assert alias_cohorts == []


def test_placeholder_self_mapping_and_unverified_output01_rejected_by_simulate_alias(tmp_path: Path) -> None:
    """Category 2: _simulate_alias rejects dummy 'system:unmapped', self-aliases, and output_01_immutable_verified=False."""
    engine = ClosedLoopLearningEngine(runtime_root=tmp_path, proposals_dir=tmp_path / "proposals")
    cohort = {
        "cohort_key": "qa:entity_resolution:alias_mismatch",
        "occurrence_count": 2,
        "run_ids": ["run-01", "run-02"],
        "items": [{"event_id": "ev-01"}, {"event_id": "ev-02"}],
    }

    # Default fallback without from_id/to_id produces system:unmapped -> rejected
    dummy_prop = engine.generate_proposal_for_cohort(cohort, proposal_type="alias_addition")
    sim_dummy = engine.simulate_proposal(dummy_prop)
    assert sim_dummy.success is False
    assert sim_dummy.status == "SIMULATED_FAIL"
    assert "Placeholder alias" in sim_dummy.details.get("error", "")

    # Self-mapping rejected
    self_prop = engine.generate_proposal_for_cohort(
        cohort, proposal_type="alias_addition", target_override={"from_id": "Marcus Vance", "to_id": "marcus vance"}
    )
    sim_self = engine.simulate_proposal(self_prop)
    assert sim_self.success is False
    assert "Self-mapping" in sim_self.details.get("error", "")

    # Output 01 immutability violation rejected
    unverified_prop = engine.generate_proposal_for_cohort(
        cohort,
        proposal_type="alias_addition",
        target_override={"from_id": "Mark Vance", "to_id": "Marcus Vance", "output_01_immutable_verified": False},
    )
    sim_unv = engine.simulate_proposal(unverified_prop)
    assert sim_unv.success is False
    assert "Output 01 immutability" in sim_unv.details.get("error", "")


def test_concurrent_contradictions_excluded_and_decay_tau_clamped_to_bounds(tmp_path: Path) -> None:
    """Category 3: delta_days=0 contradictions do not skew decay step, and extreme values clamp to [30.0, 1800.0]."""
    contradiction_only_scan = TemporalDriftScanResult(
        proposals=[
            SupersessionProposal(
                proposal_id="p-c1",
                subject_id="s1",
                predicate="P",
                action="escalate_concurrent_contradiction",
                newer_assertion_id="a2",
                older_assertion_id="a1",
                newer_timestamp="2026-03-10",
                older_timestamp="2026-03-10",
                delta_days=0,
                rationale="Concurrent conflict",
            )
        ],
        supersession_count=0,
        concurrent_contradiction_count=1,
        db_sha256_unchanged=True,
        steps_executed=2,
        tool_calls_used=3,
        terminal_outcome="COMPLETED",
    )
    # Contradictions only -> returns exact base_step unchanged
    assert compute_empirical_decay_step_seconds(contradiction_only_scan, base_step=150.0) == 150.0

    # Extreme 3650-day supersession + current tau 1700.0 clamps strictly to 1800.0
    extreme_scan = TemporalDriftScanResult(
        proposals=[
            SupersessionProposal(
                proposal_id="p-s1",
                subject_id="s1",
                predicate="P",
                action="supersede_assertion",
                newer_assertion_id="a2",
                older_assertion_id="a1",
                newer_timestamp="2026-03-10",
                older_timestamp="2016-03-10",
                delta_days=3650,
                rationale="10-year gap",
            )
        ],
        supersession_count=1,
        concurrent_contradiction_count=0,
        db_sha256_unchanged=True,
        steps_executed=2,
        tool_calls_used=3,
        terminal_outcome="COMPLETED",
    )
    eval_dir = tmp_path / "evals"
    eval_dir.mkdir(parents=True, exist_ok=True)
    for i in (1, 2):
        write_json(
            eval_dir / f"eval_{i}.json",
            {"receipt_id": f"r-{i}", "pipeline": "copilot", "evaluated_at": utc_now(), "metrics": {"context_recall": 0.40}},
        )
    cfg = tmp_path / "parameters.json"
    write_json(
        cfg,
        {"schema_version": "1.0.0", "updated_at": utc_now(), "parameters": {"copilot": {"context": {"decay_tau_seconds": 1700.0}}}},
    )
    cohorts = scan_answer_eval_receipts(eval_dir, min_occurrences=2, config_path=cfg, temporal_drift_scan=extreme_scan)
    assert cohorts[0].proposed_value == 1800.0
