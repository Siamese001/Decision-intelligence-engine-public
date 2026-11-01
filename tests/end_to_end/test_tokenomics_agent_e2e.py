"""End-to-end test suite for the 11th Sidecar Tokenomics Cost Optimization Agent across all 8 pillars."""

from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.batch_core.adapter import (
    calculate_batch_cost_savings,
    create_anthropic_batch_request,
    create_gemini_batch_request,
)
from dual_pipeline.cost_benchmark_runner import benchmark_pinned_models_pareto
from dual_pipeline.l2_healer_service import UnifiedL2HealerService, compact_json
from dual_pipeline.l2_healing_contracts import HealingScope, L2HealingRequest
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.llm_judge_core.judges.forensic import _heal_scorecard_arithmetic
from dual_pipeline.llm_judge_core.orchestrator import DecomposedMultiJudge
from dual_pipeline.model_registry import ModelTier, resolve_tier_model
from dual_pipeline.prompt_cache import build_anthropic_multi_breakpoint_payload
from dual_pipeline.query_router_core.complexity_classifier import resolve_frugal_cascade_plan
from dual_pipeline.semantic_cache_core.cache import SUPPORTED_SEMANTIC_NAMESPACES
from dual_pipeline.token_optimization.multi_tier_cache import (
    ExactResponseDigestCache,
    PersistentNegativeFailureCache,
    SubgraphReadMemoCache,
    TurnWindowIncrementalCache,
)
from tools.audit_tokenomics_gaps import run_tokenomics_audit


def test_tokenomics_8_pillar_audit_passes_strict() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    report = run_tokenomics_audit(repo_root)
    assert report["total_pillars"] == 8
    assert report["pillars_passing"] == 8, f"Failing pillars: {report['pillars']}"
    assert report["total_gaps"] == 0
    assert report["terminal_outcome"] == "PASS"


def test_e2e_model_pinning_benchmark_and_frugal_cascades() -> None:
    model_id, effort = resolve_tier_model(ModelTier.SURGICAL_HEALER)
    assert model_id == "gpt-6-luna"
    assert effort == "medium"

    bench = benchmark_pinned_models_pareto()
    assert len(bench) == 8
    assert all("prefix_cached_cost_usd" in row for row in bench)

    plan = resolve_frugal_cascade_plan(
        "Who owns the BGE-M3 sidecar?",
        metadata={"preferred_provider": "google", "confidence_margin": 0.92},
    )
    assert plan.requires_immediate_frontier is False
    assert plan.initial_model == "gemini-3.5-flash-lite"

    esc_plan = resolve_frugal_cascade_plan(
        "Dialectical multi-hop tradeoff analysis",
        metadata={"confidence_margin": 0.55},
    )
    assert esc_plan.requires_immediate_frontier is True

    anth_req = create_anthropic_batch_request(
        "req-1", {"model": "claude-3-7-sonnet", "max_tokens": 1024, "messages": []}
    )
    gem_req = create_gemini_batch_request("req-2", {"contents": []}, model="gemini-3.8-flash")
    savings = calculate_batch_cost_savings(
        "gpt-6.1-sol", uncached_input_tokens=100_000, output_tokens=10_000
    )
    assert anth_req["custom_id"] == "req-1"
    assert gem_req["model"].startswith("models/")
    assert savings["discount_pct"] == 50.0


def test_e2e_frozen_deliverable_judge_bypass_and_scorecard_healing() -> None:
    drifted = {
        "dimension_1_forensic_grounding_and_fidelity": 24,
        "dimension_2_attribution_and_room_mic_hygiene": 24,
        "dimension_3_chronological_and_timestamp_integrity": 25,
        "dimension_4_asr_cleanup_and_domain_preservation": 24,
        "composite_score": 95,  # Drifted from 97
    }
    healed = _heal_scorecard_arithmetic(drifted)
    assert healed["composite_score"] == 97
    assert healed["arithmetic_healed"] is True

    judge = DecomposedMultiJudge(mock=True)
    frozen_o1 = {
        "status": "PASS",
        "verdict": "PASS",
        "composite_score": 98,
        "dimension_1_forensic_grounding_and_fidelity": 25,
        "dimension_2_attribution_and_room_mic_hygiene": 24,
        "dimension_3_chronological_and_timestamp_integrity": 25,
        "dimension_4_asr_cleanup_and_domain_preservation": 24,
        "defect_codes": [],
        "forensic_rationale": "Frozen pass",
        "actionable_remediation_diff": "None",
        "judge_model": "gpt-6-luna",
        "prompt_digest": "abcd1234abcd1234",
        "rubric_version": "2.0",
        "sample_count": 1,
    }
    res = judge.evaluate_parallel(
        output_1_text="**Alex Sanchez** (00:01): Valid transcript.",
        output_2_text="# Executive Recap\n\nSummary.",
        output_3_text=json.dumps({"entities": [], "assertions": []}),
        metadata={"frozen_deliverables": {"output_1": frozen_o1}},
        mock=True,
    )
    assert res["output_1_evaluation"]["frozen_bypass"] is True
    assert res["output_1_evaluation"]["tokens_saved_by_freeze"] is True


def test_e2e_closed_loop_tokenomics_learning_and_multi_tier_caches(tmp_path: Path) -> None:
    engine = ClosedLoopLearningEngine(
        runtime_root=tmp_path / "runtime",
        proposals_dir=tmp_path / "proposals",
    )
    signals = engine.ingest_tokenomics_signals(
        judge_reports=[
            {"run_id": "run-001", "defect_codes": ["ERR_SPEAKER_GENERIC"]},
            {"run_id": "run-002", "defect_codes": ["ERR_SPEAKER_GENERIC"]},
        ],
        healer_results=[
            {"run_id": "run-001", "repaired_fields": ["speakers"]},
            {"run_id": "run-002", "repaired_fields": ["speakers"]},
        ],
        cache_metrics=[
            {"run_id": "run-001", "stage": "D02", "cache_hit_rate": 0.20},
            {"run_id": "run-002", "stage": "D02", "cache_hit_rate": 0.25},
        ],
    )
    cohorts = engine.analyze_failure_cohorts(signals, min_occurrences=2)
    assert len(cohorts) >= 3
    proposal = engine.generate_proposal_for_cohort(cohorts[0])
    assert proposal["auto_apply"] is False
    assert proposal["approval_required"] is True

    # Verify compact_json strips indentation bloat
    raw_obj = {"a": [1, 2, 3], "b": {"nested": "value"}}
    assert "\n" not in compact_json(raw_obj)
    assert len(compact_json(raw_obj)) < len(json.dumps(raw_obj, indent=2))

    # Verify all 4 deterministic caches and semantic cache namespaces
    assert "l2_healer" in SUPPORTED_SEMANTIC_NAMESPACES
    db = tmp_path / "cache.sqlite"
    assert ExactResponseDigestCache(db) is not None
    assert TurnWindowIncrementalCache(db) is not None
    assert PersistentNegativeFailureCache(db) is not None
    assert SubgraphReadMemoCache(db) is not None
    assert build_anthropic_multi_breakpoint_payload(system_blocks=["sys"])["breakpoints_count"] >= 1
    healer = UnifiedL2HealerService(mock_mode=True)
    req = L2HealingRequest(
        scope=HealingScope.PIPELINE_A_TRANSCRIPT,
        candidate_artifact="2026-10-26 Speaker 1: Hello",
        failure_reasons=["ISO date"],
        allowed_entity_ids=["person:alex-sanchez"],
        allowed_evidence_ids=[],
    )
    prompt = healer._build_user_prompt(req)
    assert "CANDIDATE ARTIFACT TO HEAL:" in prompt
