"""
End-to-End Lifecycle Verification for Phase 4:
1. Closed-Loop System Learning Gap Remediation & Largest-Delta Route Calibration (Order 8/16)
2. High-Churn U0 Preflight Ingress Inline Time Hydration Hardening (Order 11/16)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import pytest

from dual_pipeline.canonical_pipeline.u0_ingress import (
    batch_validate_preflight_checks,
    raise_if_preflight_failed,
)
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.learning_loop_core.retrieval_calibrator_agent import (
    ALL_ELEVEN_BGE_GATES,
    AdaptiveRetrievalCalibratorAgent,
    evaluate_eleven_gate_holdout,
)
from dual_pipeline.learning_loop_core.seal import seal_learning_stage_chain


def _baseline_11_metrics() -> Dict[str, float]:
    return {
        "ndcg_at_10": 0.81,
        "recall_at_50": 0.86,
        "mrr_at_10": 0.79,
        "contradiction_pair_recall": 0.90,
        "provenance_preservation_rate": 1.0,
        "temporal_status_accuracy": 0.95,
        "entity_anchor_hit_rate": 0.92,
        "multi_hop_bridge_coverage": 0.84,
        "p95_latency_ms": 140.0,
        "stale_leakage_rate": 0.0,
        "candidate_fact_leakage": 0.0,
    }


def _candidate_11_metrics() -> Dict[str, float]:
    m = dict(_baseline_11_metrics())
    m["ndcg_at_10"] = 0.86
    m["recall_at_50"] = 0.91
    m["p95_latency_ms"] = 135.0
    return m


def test_calibrator_agent_e2e_provenance_and_largest_delta_selection(tmp_path: Path) -> None:
    """Verifies Surface 1 origin_agent provenance and deterministic largest-delta dimension selection."""
    proposals_dir = tmp_path / "proposals"
    agent = AdaptiveRetrievalCalibratorAgent(proposals_dir=proposals_dir)

    # Cohort has a small delta on 'temporal' (+0.02) and a large delta on 'contradiction' (+0.18).
    # Even when 'temporal' appears first in current_multipliers, 'contradiction' must be selected as primary_dim.
    cohort = [
        {"event_id": "ev_temp_01", "missed_dimension": "temporal", "suggested_multiplier": 0.42, "severity": 0.6},
        {"event_id": "ev_contra_01", "missed_dimension": "contradiction", "suggested_multiplier": 0.62, "severity": 0.9},
        {"event_id": "ev_contra_02", "missed_dimension": "contradiction", "suggested_multiplier": 0.64, "severity": 0.95},
    ]
    current_multipliers = {
        "temporal": 0.40,
        "relational": 0.45,
        "contradiction": 0.40,
    }

    outcome = agent.calibrate(
        failure_cohort=cohort,
        current_multipliers=current_multipliers,
        baseline_metrics=_baseline_11_metrics(),
        candidate_metrics=_candidate_11_metrics(),
    )

    assert outcome.accepted is True
    assert outcome.steps_executed == 3
    assert outcome.proposal_artifact is not None

    ext_learning = outcome.proposal_artifact["extensions"]["com.partner_advisory.nee.learning"]
    ext_calibrator = outcome.proposal_artifact["extensions"]["com.partner_advisory.nee.calibrator"]

    assert ext_learning["origin_agent"] == "AdaptiveRetrievalCalibratorAgent"
    assert ext_calibrator["origin_agent"] == "AdaptiveRetrievalCalibratorAgent"
    assert ext_learning["parameter_name"] == "retrieval.route_multiplier.contradiction"
    assert ext_learning["cohort_key"] == "calibrator:retrieval_weight:contradiction"
    assert outcome.proposal_artifact["approval_required"] is True
    assert outcome.proposal_artifact["auto_apply"] is False

    # Verify closed-loop simulation and LP5 canary evaluation in an isolated runtime root
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    engine = ClosedLoopLearningEngine(runtime_root=runtime_root, proposals_dir=proposals_dir)

    sim_res = engine.simulate_proposal(outcome.proposal_artifact)
    assert sim_res.success is True
    assert sim_res.status == "SIMULATED_PASS"
    assert sim_res.details.get("zero_mutation_guaranteed") is True

    # Simulate an applied snapshot and trigger LP5 post-apply canary regression -> automatic rollback
    from dual_pipeline.learning_engine_core.rollback import record_rollback_snapshot

    param_file = tmp_path / "parameters.json"
    param_file.write_text(json.dumps({"retrieval.route_multiplier.contradiction": 0.55}), encoding="utf-8")
    record_rollback_snapshot(
        proposals_dir,
        outcome.proposal_artifact,
        {"parameter_name": "retrieval.route_multiplier.contradiction", "previous_value": 0.40},
    )

    canary_res = engine.evaluate_post_apply_canary(
        outcome.proposal_artifact,
        {
            "target_before": 0.86,
            "target_after": 0.78,  # Regression triggers automatic LP5 rollback
            "guard_groundedness_before": 0.95,
            "guard_groundedness_after": 0.95,
        },
        parameter_path=param_file,
        ledger_db_path=tmp_path / "learning.sqlite",
    )
    assert canary_res["canary_passed"] is False
    assert canary_res["status"] == "ROLLED_BACK"
    assert canary_res["rolled_back"] is True
    assert json.loads(param_file.read_text(encoding="utf-8"))["retrieval.route_multiplier.contradiction"] == 0.40


def test_lp0_to_lp5_stage_chain_cryptographic_seal(tmp_path: Path) -> None:
    """Verifies LP0..LP5 Merkle stage chain sealing across all 6 stages."""
    seal = seal_learning_stage_chain(
        proposal_id="prop-phase4-e2e",
        run_id="run-phase4-e2e",
        stage_events=[
            {"stage": "LP0", "event_type": "cohort_mined", "status": "PASS", "payload_digest": "d0" * 32},
            {"stage": "LP1", "event_type": "proposal_synthesized", "status": "PASS", "payload_digest": "d1" * 32},
            {"stage": "LP2", "event_type": "holdout_simulated", "status": "PASS", "payload_digest": "d2" * 32},
            {"stage": "LP3", "event_type": "hitl_approved", "status": "PASS", "payload_digest": "d3" * 32},
            {"stage": "LP4", "event_type": "uwg_committed", "status": "PASS", "payload_digest": "d4" * 32},
            {"stage": "LP5", "event_type": "canary_verified", "status": "PASS", "payload_digest": "d5" * 32},
        ],
        output_dir=tmp_path,
        require_canary=True,
    )
    assert seal["terminal_outcome"] == "PASS"
    assert seal["stage_count"] == 6
    assert seal["stages_sealed"] == ["LP0", "LP1", "LP2", "LP3", "LP4", "LP5"]
    assert len(seal["merkle_root"]) == 64


def test_u0_ingress_inline_markdown_time_hydration_e2e(tmp_path: Path) -> None:
    """Verifies U0 preflight ingress auto-hydrates metadata['time'] from inline Markdown transcript headers."""
    transcript_md = (
        "# Executive Architecture Sync\n"
        "**Date:** 10/28/2026\n"
        "**Time:** 02:00 PM - 02:45 PM ET\n\n"
        "## Verbatim Transcript\n"
        "**Amit Ayer:** Welcome everyone to the architecture review.\n"
    )
    metadata = {
        "title": "Executive Architecture Sync",
        "date": "10/28/2026",
        "notes_mode": True,
    }
    report = batch_validate_preflight_checks(metadata, raw_transcript=transcript_md, input_path=tmp_path / "01_transcript.md")
    raise_if_preflight_failed(report)
    assert report.is_valid is True
    assert metadata["time"] == "02:00 PM - 02:45 PM ET"
    assert "time" in report.hydrated_fields
    assert "time" not in report.missing_fields
