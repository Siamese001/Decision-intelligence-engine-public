"""Adversarial & Construct-Validity Counterfactual Tests for Repository Evaluation Surfaces.

Covers all 5 construct-validity gaps remediated by the Evals Agent:
1. evals/copilot_quality_gate.py: Unclamped per-case relevance paired bootstrap & HOLDOUT_ASSERTIONS grounding binding.
2. evals/run_provider_fault_injection.py: Bounded ThreadPoolExecutor timeout cancellation & fallback extraction in FAULT-TIMEOUT.
3. dual_pipeline/evals/pipelines/pipeline_c_evaluator.py: Production query/FTS API invocation for Law 6/Law 2 & mtime cache invalidation.
4. dual_pipeline/evals/strategic/deliverable_evaluator.py: Output 03 JSON Rule C-19 firewall scanning & MM/DD/YYYY temporal validation.
5. evals/evaluate_metal_advisory_extraction.py & dual_pipeline/evals/answer_eval_core/metrics.py: Fork isolation enforcement & fabricated inline citation rejection.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dual_pipeline.evals.answer_eval_core.metrics import compute_groundedness
from dual_pipeline.evals.pipelines.pipeline_c_evaluator import PipelineCEvaluator
from dual_pipeline.evals.strategic.deliverable_evaluator import (
    DeliverableStrategicEvaluator,
)
import evals.copilot_quality_gate as cqg
from evals.copilot_quality_gate import (
    HOLDOUT_ASSERTIONS,
    _compute_paired_bootstrap,
    _evaluate_case_frames,
)
from evals.copilot_scorecard import compute_copilot_scorecard
from evals.evaluate_metal_advisory_extraction import MetalAdvisoryExtractionEvaluator
from evals.run_provider_fault_injection import simulate_fault_timeout
from storage.database import AdvisoryCategory, CanonicalGraphNode, InternalAdvisoryNode


def test_gap1a_copilot_paired_bootstrap_detects_negative_relevance_regression() -> None:
    """Paired bootstrap diffs must not clamp negative regressions to 0.0 and must reflect per-case variance."""
    cases = [
        {"id": "c1", "expected_action": "speak", "expected_assertion_ids": ["ast_slo_conflict"]},
        {"id": "c2", "expected_action": "speak", "expected_assertion_ids": ["ast_wal_mode_switch"]},
        {"id": "c3", "expected_action": "speak", "expected_assertion_ids": ["ast_q4_deadline_conflict"]},
    ]
    frames_results = [
        {"id": "c1", "status": "PASS", "response": "API endpoints maintain 99.95% availability SLO.", "cited_assertion_ids": ["ast_slo_conflict"]},
        {"id": "c2", "status": "PASS", "response": "SQLite operates under mandatory WAL journal mode.", "cited_assertion_ids": ["ast_wal_mode_switch"]},
        {"id": "c3", "status": "PASS", "response": "Gateway migration cutover is scheduled for Q4.", "cited_assertion_ids": ["ast_q4_deadline_conflict"]},
    ]
    # Degraded composer misses c1 and c2
    degraded_composer_results = [
        {"id": "c1", "status": "FAIL", "response": "", "cited_assertion_ids": []},
        {"id": "c2", "status": "PASS", "response": "Wrong citation.", "cited_assertion_ids": ["ast_disk_saturation"]},
        {"id": "c3", "status": "PASS", "response": "Gateway migration cutover is scheduled for Q4.", "cited_assertion_ids": ["ast_q4_deadline_conflict"]},
    ]

    assert hasattr(cqg, "_compute_case_relevance_diffs")
    diffs = cqg._compute_case_relevance_diffs(cases, frames_results, degraded_composer_results)
    assert diffs == [-1.0, -1.0, 0.0]
    mean_diff, ci_low, ci_high = _compute_paired_bootstrap(diffs)
    assert mean_diff < 0.0
    assert ci_high <= 0.0


def test_gap1b_copilot_case_evaluators_bind_assertion_texts_for_lexical_grounding() -> None:
    """_evaluate_case_frames and _evaluate_case_composer must bind assertion_texts so ungrounded claims fail grounding."""
    case = {
        "id": "case_grounding_check",
        "speaker": "Engineer",
        "dialogue": "Production API endpoints maintain an availability SLO of 99.95%.",
        "target": "availability SLO",
        "expected_action": "speak",
        "expected_assertion_ids": ["ast_slo_conflict"],
    }
    all_asrts = list(HOLDOUT_ASSERTIONS.values())
    frame_res = _evaluate_case_frames(case, all_asrts)
    assert "assertion_texts" in frame_res
    assert frame_res["assertion_texts"].get("ast_slo_conflict") == HOLDOUT_ASSERTIONS["ast_slo_conflict"]["claim_text"]

    # Mutate response to completely ungrounded text while keeping cited_assertion_ids
    ungrounded_res = dict(frame_res)
    ungrounded_res["response"] = "Substation transformer oil dissolved gas analysis completed yesterday."
    ungrounded_res["cited_assertion_ids"] = ["ast_slo_conflict"]
    scorecard = compute_copilot_scorecard([case], [ungrounded_res])
    assert scorecard.groundedness == 0.0


def test_gap2_fault_timeout_executes_bounded_threadpool_cancellation_and_fallback() -> None:
    """simulate_fault_timeout must trigger real ThreadPoolExecutor TimeoutError and engage fallback extraction."""
    res = simulate_fault_timeout(timeout_sim_seconds=0.03)
    assert res.passed is True
    assert res.fallback_engaged is True
    assert res.details.get("timeout_error_caught") is True
    assert res.details.get("future_cancelled") is True
    assert res.details.get("recovered_nodes", 0) > 0


def test_gap3_pipeline_c_evaluator_mtime_and_query_api_enforcement() -> None:
    """PipelineCEvaluator must verify mtime cache invalidation and invoke production query/FTS APIs for Law 6 & Law 2."""
    evaluator = PipelineCEvaluator()

    inv_res = evaluator.evaluate_vector_cache_invalidation()
    assert inv_res["passed"] is True
    assert inv_res.get("generation_bump_invalidated") is True
    assert inv_res.get("mtime_bump_invalidated") is True

    law6_res = evaluator.evaluate_law6_quarantine_enforcement()
    assert law6_res["passed"] is True
    assert law6_res.get("fts_quarantine_enforced") is True

    law2_res = evaluator.evaluate_law2_namespace_isolation()
    assert law2_res["passed"] is True
    assert law2_res.get("query_api_namespace_isolated") is True


def test_gap4_deliverable_strategic_evaluator_detects_output03_firewall_leak_and_mmddyyyy_dates(
    tmp_path: Path,
) -> None:
    """DeliverableStrategicEvaluator must fail Dim 4 when Output 03 leaks career firewall terms and pass Dim 7 on MM/DD/YYYY."""
    evaluator = DeliverableStrategicEvaluator()

    p1 = tmp_path / "01_recap.md"
    p2 = tmp_path / "02_notes.md"
    p3 = tmp_path / "03_decisions.json"

    p1.write_text("Clean client transcript summary.", encoding="utf-8")
    p2.write_text("Meeting Objective: Align on $250,000 cost avoidance tradeoff and next steps with finance sponsor.", encoding="utf-8")
    leaky_03 = {
        "schema_version": "5.8",
        "meeting_id": "mtg_grid_ops",
        "observed_at": "10/26/2026",
        "evidence_index": {
            "ev1": {"kind": "quote", "locator": {"value": "L10-L12"}},
            "ev2": {"kind": "metric", "locator": {"value": "L15-L18"}},
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "person:exec_target",
                    "type": "person",
                    "description": "Use this architecture for our executive compensation band and political power map.",
                }
            ],
            "decision_updates": [{"decision": "Consolidate telemetry"}],
            "relationship_signals": [],
        },
    }
    p3.write_text(json.dumps(leaky_03), encoding="utf-8")

    scorecard = evaluator.evaluate_deliverables(p1, p2, p3)
    dim4 = scorecard.dimension_scores["dim_4_dual_audience"]
    assert dim4.passed is False
    assert dim4.score == 0.0
    assert len(dim4.details.get("output_03_leaks", [])) > 0

    dim7 = scorecard.dimension_scores["dim_7_bitemporal"]
    assert dim7.passed is True
    assert dim7.score == 100.0


def test_gap5a_metal_advisory_gate2_fails_when_advisory_isolation_breached() -> None:
    """evaluate_gate_2_compute_fork_separation must fail when advisory nodes leak into CLIENT_SAFE namespace."""
    evaluator = MetalAdvisoryExtractionEvaluator(mock_mode=True)
    passed_clean, details_clean = evaluator.evaluate_gate_2_compute_fork_separation()
    assert passed_clean is True
    assert details_clean.get("has_sanitization") is True
    assert details_clean.get("all_advisory_isolated") is True

    # Inject a broken fork result where an advisory node has namespace="CLIENT_SAFE" and low stealth_score
    leaked_fork = SimpleNamespace(
        evidence_id="ev-fork-001",
        sanitized_text="scrubbed",
        canonical_nodes=[
            CanonicalGraphNode(
                node_id="node:canon_1",
                evidence_id="ev-fork-001",
                type="system",
                name="AWS S3",
                namespace="CLIENT_SAFE",
            )
        ],
        advisory_nodes=[
            InternalAdvisoryNode(
                node_id="node:adv_leak",
                evidence_id="ev-fork-001",
                type="BLOCKER",
                name="Leaked Advisory",
                category=AdvisoryCategory.BLOCKER,
                namespace="CLIENT_SAFE",
                stealth_score=0.2,
            )
        ],
    )

    async def _fake_fork(_ev):
        return leaked_fork

    with patch.object(evaluator.coordinator, "execute_fork", side_effect=_fake_fork):
        passed_breach, details_breach = evaluator.evaluate_gate_2_compute_fork_separation()
        assert details_breach["all_advisory_isolated"] is False
        assert passed_breach is False


def test_gap5b_compute_groundedness_rejects_fabricated_inline_citations() -> None:
    """compute_groundedness must reject inline ast-... citations that are absent from cited_assertion_ids."""
    # Case 1: Empty cited_assertion_ids with hallucinated inline citation
    rec_empty_cited = {
        "response_text": "The substation upgrade is complete [ast-hallucinated-999].",
        "cited_assertion_ids": [],
    }
    assert compute_groundedness(rec_empty_cited) == 0.0

    # Case 2: Mismatched inline citation not in cited_assertion_ids
    rec_mismatch = {
        "response_text": "The substation upgrade is complete [ast-hallucinated-999].",
        "cited_assertion_ids": ["ast-verified-001"],
    }
    assert compute_groundedness(rec_mismatch) == 0.0

    # Case 3: Matching inline citation in cited_assertion_ids
    rec_valid = {
        "response_text": "The substation upgrade is complete [ast-verified-001].",
        "cited_assertion_ids": ["ast-verified-001"],
    }
    assert compute_groundedness(rec_valid) == 1.0


def test_gap6_compute_copilot_scorecard_fails_on_ungrounded_or_false_positive_cases() -> None:
    """compute_copilot_scorecard must set passed=False when groundedness < 0.95 or silence_precision < 0.80."""
    from evals.copilot_scorecard import compute_copilot_scorecard

    cases = [
        {"id": "c1", "expected_action": "speak", "strata": "factual"},
        {"id": "c2", "expected_action": "silent", "strata": "chitchat"},
    ]
    failing_results = [
        {
            "case_id": "c1",
            "response": "• Unverified claim with zero citations",
            "cited_assertion_ids": [],
            "stage_latencies_ms": {"total_ms": 100.0},
        },
        {
            "case_id": "c2",
            "response": "• Spoke when silence was required [ast-1]",
            "cited_assertion_ids": ["ast-1"],
            "stage_latencies_ms": {"total_ms": 100.0},
        },
    ]
    sc_fail = compute_copilot_scorecard(cases, failing_results)
    assert sc_fail.groundedness < 0.95
    assert sc_fail.silence_precision < 0.80
    assert sc_fail.passed is False

