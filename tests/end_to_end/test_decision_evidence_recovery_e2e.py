"""End-to-end lifecycle verification for obligation-driven query and decision reasoning.

Verifies:
1. Identical question with different options/criteria/constraints/time horizons
   produces distinct sealed evidence requirements and cache keys.
2. Missing authorized record outside initial candidates can be retrieved once
   (rank-1 action within shared budget).
3. Comparison mode preserves baseline output while evaluating candidate retrieval.
4. Combined pre/post-L2 loops respect budget ownership and monotonic limits.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from dual_pipeline.core import digest
from dual_pipeline.cost_tracker import ActionRole, BudgetExhaustionError, RequestContext, get_reservation_manager
from dual_pipeline.l0.decision_support_contracts import (
    compile_frame_evidence_requirements,
)
from dual_pipeline.query_core.cache import _query_cache_context
from dual_pipeline.query_core.coverage import (
    build_retrieval_evidence_report,
    coverage_supplemental_retrieval,
)
from dual_pipeline.query_core.decision_coverage import (
    extract_frame_coverage_requirements,
    rank_missing_gap_requirements,
)
from dual_pipeline.query_core.planning import build_query_plan


def _create_mock_destination_artifact(question: str) -> dict[str, Any]:
    return {
        "artifact_type": "decision_brief",
        "canonical_digest": {"value": digest({"question": question})},
        "destination": "NEE_DECISION_INTELLIGENCE",
        "query_type": "opportunity_nba",
        "materiality": "high",
        "delivery_profile": "internal_technical",
    }


def _create_mock_active(graph_path: Path) -> dict[str, Any]:
    return {
        "graph_snapshot": "snap_wave5_e2e",
        "graph_sha256": digest({"graph": "mock_wave5"}),
        "generation_id": "gen_wave5_001",
        "graph_path": str(graph_path),
    }


def _create_mock_l2_config() -> MagicMock:
    cfg = MagicMock()
    cfg.config_digest = digest({"l2_config": "mock_digest"})
    cfg.standard.maximum_primary_calls_per_run = 1
    cfg.standard.maximum_repair_calls_per_run = 0
    cfg.standard.maximum_challenger_calls_per_run = 0
    cfg.standard.maximum_judge_calls_per_run = 0
    return cfg


def test_distinct_decision_frames_produce_distinct_evidence_requirements_and_cache_keys(tmp_path: Path):
    question = "Which architecture should we adopt for pipeline intake?"

    frame_a = {
        "frame_digest": digest({"frame": "A"}),
        "decision_id": "dec_arch_001",
        "question": question,
        "options": [
            {"option_id": "opt_stream", "label": "Stream Processing"},
            {"option_id": "opt_microbatch", "label": "Micro-Batch"},
        ],
        "criteria": [
            {"criterion_id": "crit_cost", "name": "Operational Cost", "label": "Operational Cost", "weight": 0.5},
            {"criterion_id": "crit_tput", "name": "Peak Throughput", "label": "Peak Throughput", "weight": 0.5},
        ],
        "constraints": ["monthly_cloud_budget < 5000", "p99_latency < 200ms"],
        "time_horizon": "2026-Q4",
    }

    frame_b = {
        "frame_digest": digest({"frame": "B"}),
        "decision_id": "dec_arch_002",
        "question": question,
        "options": [
            {"option_id": "opt_stream", "label": "Stream Processing"},
            {"option_id": "opt_sqlite", "label": "Direct SQLite Ingestion"},
        ],
        "criteria": [
            {"criterion_id": "crit_storage", "name": "Storage Footprint", "label": "Storage Footprint", "weight": 0.6},
            {"criterion_id": "crit_risk", "name": "Migration Risk", "label": "Migration Risk", "weight": 0.4},
        ],
        "constraints": ["zero_external_cloud_deps", "apple_silicon_metal"],
        "time_horizon": "2027-Q2",
    }

    # 1. Obligations compiled from frames
    reqs_a = compile_frame_evidence_requirements(frame_a)
    reqs_b = compile_frame_evidence_requirements(frame_b)

    assert len(reqs_a) > 0
    assert len(reqs_b) > 0
    assert reqs_a != reqs_b

    digests_a = {r["requirement_id"] for r in reqs_a}
    digests_b = {r["requirement_id"] for r in reqs_b}
    assert digests_a.isdisjoint(digests_b)

    # 2. Query planning with frames
    l2_config = _create_mock_l2_config()
    dest_artifact = _create_mock_destination_artifact(question)

    plan_a = build_query_plan(
        question=question,
        query_type="opportunity_nba",
        request_id="req_test_001",
        run_id="run_test_001",
        current_trace_id="a1b2c3d4e5f60718293a4b5c6d7e8f90",
        l2_config=l2_config,
        provider_egress_allowed=False,
        destination_decision=dest_artifact,
        assurance_profile="standard",
        requested_materiality="high",
        decision_frame=frame_a,
    )

    plan_b = build_query_plan(
        question=question,
        query_type="opportunity_nba",
        request_id="req_test_002",
        run_id="run_test_002",
        current_trace_id="b1c2d3e4f5a60718293b4c5d6e7f8a90",
        l2_config=l2_config,
        provider_egress_allowed=False,
        destination_decision=dest_artifact,
        assurance_profile="standard",
        requested_materiality="high",
        decision_frame=frame_b,
    )

    assert plan_a["evidence_requirements"] != plan_b["evidence_requirements"]
    assert (
        plan_a["extensions"]["com.partner_advisory.nee.decision-frame"]["frame_digest"]
        != plan_b["extensions"]["com.partner_advisory.nee.decision-frame"]["frame_digest"]
    )

    # 3. Cache context includes decision frame digest
    active = _create_mock_active(tmp_path / "mock.db")
    route = {"query_route": "decision_direct"}

    cache_ctx_a = _query_cache_context(
        active=active,
        route=route,
        plan=plan_a,
        classification="internal",
        audiences=["nee-engineering"],
        user_id="operator",
        authorization_context=None,
        l2_config=l2_config,
        destination_policy_digest=digest({"dest": "digest"}),
        x1_policy_digest=digest({"x1": "digest"}),
        provider_egress_allowed=False,
        decision_frame_digest=plan_a["extensions"]["com.partner_advisory.nee.decision-frame"]["frame_digest"],
        coverage_requirements_digest=digest({"req": "a"}),
    )

    cache_ctx_b = _query_cache_context(
        active=active,
        route=route,
        plan=plan_b,
        classification="internal",
        audiences=["nee-engineering"],
        user_id="operator",
        authorization_context=None,
        l2_config=l2_config,
        destination_policy_digest=digest({"dest": "digest"}),
        x1_policy_digest=digest({"x1": "digest"}),
        provider_egress_allowed=False,
        decision_frame_digest=plan_b["extensions"]["com.partner_advisory.nee.decision-frame"]["frame_digest"],
        coverage_requirements_digest=digest({"req": "b"}),
    )

    assert cache_ctx_a["decision_frame_digest"] != cache_ctx_b["decision_frame_digest"]
    digest_a = digest(cache_ctx_a)
    digest_b = digest(cache_ctx_b)
    assert digest_a != digest_b


def test_missing_authorized_record_retrieved_once_rank1_action_within_budget(tmp_path: Path, monkeypatch):
    question = "Evaluate architecture options"
    frame = {
        "frame_digest": digest({"frame": "gap_01"}),
        "decision_id": "dec_arch_gap_01",
        "question": question,
        "options": [{"option_id": "opt_a", "label": "Stream Processing"}],
        "criteria": [{"criterion_id": "crit_cost", "name": "Operational Cost", "label": "Operational Cost", "weight": 1.0}],
        "constraints": ["budget_monthly < 5000"],
        "time_horizon": "2026-Q4",
    }

    l2_config = _create_mock_l2_config()
    plan = build_query_plan(
        question=question,
        query_type="opportunity_nba",
        request_id="req_gap_001",
        run_id="run_gap_001",
        current_trace_id="c1d2e3f4a5b60718293c4d5e6f7a8b90",
        l2_config=l2_config,
        provider_egress_allowed=False,
        destination_decision=_create_mock_destination_artifact(question),
        assurance_profile="standard",
        requested_materiality="high",
        decision_frame=frame,
    )

    frame_reqs = extract_frame_coverage_requirements(plan, default_lanes=("approved", "candidate"))
    assert len(frame_reqs) > 0
    target_req = frame_reqs[0]

    v_req_digest = digest({"request": "v_req"})
    q_plan_digest = digest({"plan": "q_plan"})
    manifest_digest = digest({"manifest": "man"})

    coverage_manifest = {
        "run_id": "run_gap_001",
        "validated_request_digest": v_req_digest,
        "query_plan_digest": q_plan_digest,
        "canonical_digest": {"value": manifest_digest},
        "requirements": [target_req],
    }

    # Initial evidence packet has no records for this obligation
    packet = {
        "graph_snapshot": "snap_gap_001",
        "packet_digest": digest({"packet": "init"}),
        "approved": [],
        "candidate": [],
        "external_public": [],
        "graph_paths": [],
        "counterevidence": [],
    }

    report = build_retrieval_evidence_report(
        coverage_manifest=coverage_manifest,
        packet=packet,
        authorization_scope_digest=digest({"auth": "scope"}),
        graph_sha256=digest({"graph": "sha"}),
        view_authorized=True,
    )

    # Initial report indicates explicit gap
    outcomes = {r["requirement_id"]: r for r in report["requirement_outcomes"]}
    assert outcomes[target_req["requirement_id"]]["outcome"] == "explicit_gap"

    # Test ranking: decision support obligation ranks at Rank 1
    req_map = {r["requirement_id"]: r for r in coverage_manifest["requirements"]}
    ranked = rank_missing_gap_requirements(
        [outcomes[target_req["requirement_id"]]],
        req_map,
        prior_attempts=set(),
    )
    assert len(ranked) == 1
    assert ranked[0]["requirement_id"] == target_req["requirement_id"]

    # Mock candidate record outside initial packet that fulfills obligation
    mock_candidate_record = {
        "record_id": "rec_cand_cost_001",
        "text": "Stream Processing operational cost was measured at $3,200 monthly, meeting the budget.",
        "entity_ids": ["Stream Processing"],
        "polarity": "positive",
        "currentness_state": "current",
        "lane": "candidate",
    }

    # Mock retriever that yields this candidate record on targeted query
    mock_retriever = MagicMock()
    mock_retriever.retrieve.return_value = {
        "candidate": [{"record": mock_candidate_record, "score": 0.95}],
        "approved": [],
        "external_public": [],
        "graph_paths": [],
        "counterevidence": [],
    }

    retrieval_input = {
        "approved": [],
        "candidate": [],
        "external_public": [],
        "graph_paths": [],
        "counterevidence": [],
        "allowed_lanes": ["approved", "candidate"],
        "retrieval_mode": "hybrid",
    }
    exact_input = {"bm25_executed": False, "bm25_ranks_by_lane": {}}

    # Monkeypatch expansion, hydration, and packet construction
    import dual_pipeline.query_core.coverage as cov_mod
    monkeypatch.setattr(cov_mod, "expand_connected_retrieval", lambda **kw: ({}, {}))
    monkeypatch.setattr(cov_mod, "hydrate_results", lambda *args, **kw: {})
    monkeypatch.setattr(
        cov_mod,
        "build_evidence_packet",
        lambda **kw: {
            "graph_snapshot": "snap_gap_001",
            "packet_digest": digest({"packet": "augmented"}),
            "approved": [],
            "candidate": [{"record": mock_candidate_record}],
            "external_public": [],
            "graph_paths": [],
            "counterevidence": [],
        },
    )

    # Supplemental retrieval with max_supplemental_queries=1
    aug_report, aug_packet, _, _, _, query_count = coverage_supplemental_retrieval(
        retrieval_evidence_report=report,
        coverage_manifest=coverage_manifest,
        retriever=mock_retriever,
        retrieval=retrieval_input,
        question=question,
        graph_path=tmp_path / "mock.db",
        sidecar_path=tmp_path / "sidecar.json",
        run_id="run_gap_001",
        graph_snapshot="snap_gap_001",
        query_plan_digest=q_plan_digest,
        requested_hops=1,
        relationships_to_inspect=[],
        classification="internal",
        audiences=["nee-engineering"],
        retrieval_mode="hybrid",
        allowed_assertion_ids=None,
        exact=exact_input,
        card_scope=None,
        active=_create_mock_active(tmp_path / "mock.db"),
        authorization_scope_digest=digest({"auth": "scope"}),
        max_supplemental_queries=1,
        comparison_mode=False,
    )

    assert query_count == 1
    assert aug_report is not None
    aug_outcomes = {r["requirement_id"]: r for r in aug_report["requirement_outcomes"]}
    assert aug_outcomes[target_req["requirement_id"]]["outcome"] == "covered"
    assert "rec_cand_cost_001" in aug_outcomes[target_req["requirement_id"]]["record_ids"]


def test_comparison_mode_preserves_baseline_evidence_and_output(tmp_path: Path, monkeypatch):
    question = "Compare baseline and candidate models"
    target_req = {
        "requirement_id": "req_compare_01",
        "requirement_label": "Comparison Support",
        "requirement_kind": "decision_support_obligation",
        "allowed_lanes": ["approved", "candidate"],
        "frame_obligation": {
            "option_label": "Option A",
            "criterion_name": "Accuracy",
            "required_direction": "positive",
        },
    }

    v_digest = digest({"req": "v_comp"})
    q_digest = digest({"plan": "q_comp"})
    m_digest = digest({"manifest": "m_comp"})

    coverage_manifest = {
        "run_id": "run_compare_001",
        "validated_request_digest": v_digest,
        "query_plan_digest": q_digest,
        "canonical_digest": {"value": m_digest},
        "requirements": [target_req],
    }

    baseline_packet_digest = digest({"packet": "baseline"})
    baseline_packet = {
        "graph_snapshot": "snap_compare_001",
        "packet_digest": baseline_packet_digest,
        "approved": [],
        "candidate": [],
        "external_public": [],
        "graph_paths": [],
        "counterevidence": [],
    }

    baseline_report = build_retrieval_evidence_report(
        coverage_manifest=coverage_manifest,
        packet=baseline_packet,
        authorization_scope_digest=digest({"auth": "scope"}),
        graph_sha256=digest({"graph": "comp"}),
        view_authorized=True,
    )

    mock_retriever = MagicMock()
    mock_candidate_record = {
        "record_id": "rec_cand_002",
        "text": "Option A Accuracy reached 99.2% in prospective trials.",
        "entity_ids": ["Option A"],
        "polarity": "positive",
        "currentness_state": "current",
    }
    mock_retriever.retrieve.return_value = {
        "candidate": [{"record": mock_candidate_record, "score": 0.98}],
        "approved": [],
        "external_public": [],
        "graph_paths": [],
        "counterevidence": [],
    }

    retrieval_input = {
        "approved": [],
        "candidate": [],
        "external_public": [],
        "graph_paths": [],
        "counterevidence": [],
        "allowed_lanes": ["approved", "candidate"],
        "retrieval_mode": "hybrid",
    }
    exact_input = {"bm25_executed": False, "bm25_ranks_by_lane": {}}

    import dual_pipeline.query_core.coverage as cov_mod
    monkeypatch.setattr(cov_mod, "expand_connected_retrieval", lambda **kw: ({}, {}))
    monkeypatch.setattr(cov_mod, "hydrate_results", lambda *args, **kw: {})
    monkeypatch.setattr(
        cov_mod,
        "build_evidence_packet",
        lambda **kw: {
            "graph_snapshot": "snap_compare_001",
            "packet_digest": digest({"packet": "augmented"}),
            "approved": [],
            "candidate": [{"record": mock_candidate_record}],
            "external_public": [],
            "graph_paths": [],
            "counterevidence": [],
        },
    )

    # Run in comparison mode
    aug_report, aug_packet, _, _, _, query_count = coverage_supplemental_retrieval(
        retrieval_evidence_report=baseline_report,
        coverage_manifest=coverage_manifest,
        retriever=mock_retriever,
        retrieval=retrieval_input,
        question=question,
        graph_path=tmp_path / "mock.db",
        sidecar_path=tmp_path / "sidecar.json",
        run_id="run_compare_001",
        graph_snapshot="snap_compare_001",
        query_plan_digest=q_digest,
        requested_hops=1,
        relationships_to_inspect=[],
        classification="internal",
        audiences=["nee-engineering"],
        retrieval_mode="hybrid",
        allowed_assertion_ids=None,
        exact=exact_input,
        card_scope=None,
        active=_create_mock_active(tmp_path / "mock.db"),
        authorization_scope_digest=digest({"auth": "scope"}),
        max_supplemental_queries=1,
        comparison_mode=True,
    )

    # In comparison mode, baseline output is preserved (aug_report is None, aug_packet is empty)
    assert aug_report is None
    assert aug_packet == {}
    assert query_count == 1
    # Verify baseline report was untouched
    assert baseline_report["evidence_packet_digest"] == baseline_packet_digest


def test_combined_pre_post_l2_loops_respect_single_budget_context():
    mgr = get_reservation_manager()
    ctx = RequestContext.create(
        run_id="run_wave5_shared_budget",
        request_id="req_wave5_shared",
        timeout_seconds=20.0,
        max_cost_usd=1.0,
        max_tokens=2000,
        tool_calls_max=2,
    )

    # Stage 1: Pre-L2 reserves and reconciles
    lease1 = mgr.reserve(
        context=ctx,
        estimated_cost_usd=0.2,
        estimated_tokens=500,
        action_role="primary",
    )
    mgr.reconcile(
        context=ctx,
        reservation_id=lease1.reservation_id,
        actual_cost_usd=0.2,
        actual_tokens=450,
    )
    assert ctx.consumed_cost_usd == pytest.approx(0.2)

    # Stage 2: Post-L2 attempts reservation exceeding remaining budget ($0.9 > $0.8 remaining)
    with pytest.raises(BudgetExhaustionError):
        mgr.reserve(
            context=ctx,
            estimated_cost_usd=0.9,
            estimated_tokens=500,
            action_role="judge",
        )
