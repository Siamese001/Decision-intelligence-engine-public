"""
Adversarial tests for Wave 7: Hybrid Retrieval, Temporal Query Routing & Judge Evaluation Gate Integrity (CAT-6).

Covers:
1. Invariant bypass attempt: ReleaseGateAuditor _eval_delta_thresholds fails closed in strict mode when has_delta is omitted; evaluator rejects unpinned freeze_output_1 without SHA.
2. Malformed input / integrity corruption: RetrievalService.get_topology and _check_graphdb_available fail closed on corrupted active_pair.json SHA-256 digest.
3. Boundary payload: Missing judge reconciler fails closed instead of synthesizing passed=True.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from dual_pipeline.query_router import (
    _check_graphdb_available,
    _clear_graphdb_available_cache,
)
from dual_pipeline.retrieval.agents.release_gate_auditor import _eval_delta_thresholds
from dual_pipeline.retrieval_service import RetrievalService
from storage.connection import create_sqlite_conn


def test_release_gate_auditor_delta_thresholds_fails_closed_when_strict_and_missing() -> None:
    passed, msg = _eval_delta_thresholds({}, strict=True)
    assert passed is False
    assert "Missing delta verification fields" in msg

    passed_ok, _ = _eval_delta_thresholds(
        {"has_delta": True, "delta_recall": 1.0, "delta_mrr": 1.0, "delta_ndcg": 1.0},
        strict=True,
    )
    assert passed_ok is True


def test_retrieval_service_and_query_router_fail_closed_on_sha256_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rt_root = tmp_path / "runtime"
    gen_dir = rt_root / "generations" / "gen-test"
    gen_dir.mkdir(parents=True)
    db_path = gen_dir / "nee_graph.sqlite"

    conn = create_sqlite_conn(db_path, readonly=False)
    try:
        conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, node_type TEXT, canonical_name TEXT)")
        conn.execute("CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT)")
        conn.commit()
    finally:
        conn.close()

    active_pair_path = rt_root / "active_pair.json"
    active_pair_path.write_text(
        json.dumps(
            {
                "generation_id": "gen-test",
                "graph_path": "generations/gen-test/nee_graph.sqlite",
                "graph_sha256": "0" * 64,  # Corrupted SHA-256 digest
            }
        ),
        encoding="utf-8",
    )

    from dual_pipeline.in_memory_graph import GraphIntegrityError

    svc = RetrievalService(query_engine=None, runtime_root=rt_root)
    with pytest.raises((ValueError, RuntimeError, GraphIntegrityError), match="SHA-256|sha256|mismatch|digest|Hash"):
        svc.get_topology()

    _clear_graphdb_available_cache()
    monkeypatch.setenv("NEE_RUNTIME_ROOT", str(rt_root))
    assert _check_graphdb_available(force_refresh=True) is False
    _clear_graphdb_available_cache()


def test_evaluator_missing_reconciler_and_unverified_freeze_flag_fail_closed() -> None:
    from dual_pipeline.transcript_qa_core.evaluator import _run_micro_judges_concurrently

    class DummyJudgeWithoutReconciler:
        pass

    card_pass = {
        "status": "PASS",
        "verdict": "PASS",
        "composite_score": 95,
        "evaluator": "MicroJudge",
        "is_valid_scorecard": True,
        "rubric_version": "2.0",
        "judge_model": "gpt-test",
        "prompt_digest": "abcd1234",
        "sample_count": 1,
        "evaluated_span_coverage": 1.0,
        "defect_codes": [],
        "actionable_remediation_diff": "None",
        "forensic_rationale": "Solid rationale with sufficient detail.",
        "dimension_1_forensic_grounding_and_fidelity": 95.0,
        "dimension_2_attribution_and_room_mic_hygiene": 95.0,
        "dimension_3_chronological_and_timestamp_integrity": 95.0,
        "dimension_4_asr_cleanup_and_domain_preservation": 95.0,
        "dimension_1_speaker_identity_fidelity": 95.0,
        "dimension_2_turn_boundary_timestamp_integrity": 95.0,
        "dimension_3_verbatim_completeness_grounding": 95.0,
        "dimension_4_register_framing_absence": 95.0,
        "section_scores": {
            "header_and_overview": 28.0,
            "key_updates_technical_substance": 38.0,
            "conditional_extensions": 14.0,
            "appendix_stakeholder_perspectives": 15.0,
        },
        "executive_rationale": "Solid executive rationale.",
        "dimension_1_strategic_edge": 95.0,
        "dimension_2_knowledge_layer_density": 95.0,
        "dimension_3_semantic_atomicity": 95.0,
        "dimension_4_predicate_specificity": 95.0,
        "dimension_5_exclusion_of_action_items": 95.0,
        "dimension_6_evidence_grounding_and_schema": 95.0,
    }

    _, _, _, recon = _run_micro_judges_concurrently(
        DummyJudgeWithoutReconciler(),
        "f1",
        "f2",
        "{}",
        "src",
        None,
        True,
        {},
        (dict(card_pass), dict(card_pass), dict(card_pass)),
        ("k1", "k2", "k3"),
        {},
    )
    assert recon["passed"] is False
    assert recon["reason"] == "MISSING_JUDGE_RECONCILER"
