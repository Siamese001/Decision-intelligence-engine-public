"""
E2E tests for Wave 7: Hybrid Retrieval, Temporal Query Routing & Judge Evaluation Gate Integrity (CAT-6).
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import types

from dual_pipeline.a2a import A2ATask
from dual_pipeline.query_core.hybrid_reranker import BM25Reranker
from dual_pipeline.query_router_core.temporal_gate import (
    extract_meeting_topic_tokens,
    extract_temporal_target_date,
    verify_temporal_source_existence,
)
from dual_pipeline.retrieval.agents.hybrid_fusion_ranker import HybridFusionRanker
from dual_pipeline.transcript_qa_core.eval_cache import (
    compute_c0_receipt_digest,
    get_judge_prompt_hash,
    resolve_pins_and_cache,
)
from storage.connection import create_sqlite_conn


def test_temporal_gate_unpadded_and_natural_month_dates_fail_closed(tmp_path: Path) -> None:
    assert extract_temporal_target_date("Summarize the 10/8/2026 PartnerAdvisory SOW AI meeting") == "2026-10-08"
    assert extract_temporal_target_date("What happened on Oct 8, 2026 with the BU VP?") == "2026-10-08"
    assert extract_temporal_target_date("Review deliverables from October 26th, 2026") == "2026-10-26"

    tokens = extract_meeting_topic_tokens("Summarize the 10/8/2026 PartnerAdvisory SOW AI BU VP meeting")
    assert "partner_advisory" in tokens
    assert "sow" in tokens
    assert "ai" in tokens
    assert "bu" in tokens
    assert "vp" in tokens

    db_path = tmp_path / "temporal_test.sqlite"
    conn = create_sqlite_conn(db_path, readonly=False)
    try:
        conn.execute(
            "CREATE TABLE source (source_id TEXT PRIMARY KEY, source_name TEXT, source_date TEXT, cold_archive_ref TEXT)"
        )
        conn.execute(
            "INSERT INTO source VALUES ('src_other', 'General Weekly Sync', '2026-10-08', 'general_sync.md')"
        )
        conn.commit()

        # Query asks for PartnerAdvisory SOW AI meeting on 10/8/2026; General Weekly Sync does not match multi-token topic
        res = verify_temporal_source_existence(
            "Summarize the 10/8/2026 PartnerAdvisory SOW AI meeting",
            conn=conn,
            repo_root=tmp_path,
            runtime_root=tmp_path / ".local",
        )
        assert res["status"] == "NOT_IN_KNOWLEDGE_GRAPH"
        assert res["requested_date"] == "2026-10-08"
    finally:
        conn.close()


def test_tampered_frozen_output3_invalidates_judge_pin() -> None:
    f1_text = "[00:00:01] Alice: Confirmed architecture."
    f2_text = "# Executive Recap\nSummary."
    original_f3 = '{"assertions": [{"id": "a1", "claim": "Original verified claim"}]}'
    tampered_f3 = '{"assertions": [{"id": "a1", "claim": "TAMPERED unverified claim"}]}'

    orig_h3 = hashlib.sha256(original_f3.encode("utf-8")).hexdigest()
    p3_hash = get_judge_prompt_hash(3)
    meta = {
        "state_machine": types.SimpleNamespace(is_frozen=lambda k: k == "output_3"),
    }
    c0_dig = compute_c0_receipt_digest(meta)

    pinned = {
        "output_3": {
            "status": "PASS",
            "verdict": "PASS",
            "composite_score": 96,
            "artifact_sha256": orig_h3,
            "c0_receipt_digest": c0_dig,
            "prompt_hash": p3_hash,
            "rubric_version": "2.0",
            "topology": "decomposed_concurrent",
            "is_valid_scorecard": True,
            "dimension_1_strategic_edge": 96.0,
            "dimension_2_knowledge_layer_density": 96.0,
            "dimension_3_semantic_atomicity": 96.0,
            "dimension_4_predicate_specificity": 96.0,
            "dimension_5_exclusion_of_action_items": 96.0,
            "dimension_6_evidence_grounding_and_schema": 96.0,
        }
    }

    _, _, out3_eval, _, _, _ = resolve_pins_and_cache(
        f1_text, f2_text, tampered_f3, "gpt-test", "high", pinned, metadata=meta
    )
    # Because tampered_f3 hash != orig_h3 and eval_cache no longer overwrites artifact_sha256 in-place, pin is rejected
    assert out3_eval is None


def test_hybrid_fusion_ranker_preserves_distinct_unkeyed_stream_items_and_bm25_strips_punctuation() -> None:
    ranker = HybridFusionRanker(rrf_k=60)
    task = types.SimpleNamespace(
        task_id="t_rrf",
        input_payload={
            "ranked_lists": [
                [{"text": "Stream 0 rank 0 item without node_id"}],
                [{"text": "Stream 1 rank 0 distinct item without node_id"}],
            ],
            "limit": 10,
        },
    )
    res = asyncio.run(ranker.handle_task(task))
    assert res["fused_count"] == 2
    texts = {f["text"] for f in res["fused_facts"]}
    assert len(texts) == 2

    reranker = BM25Reranker(
        [
            "Architecture review for Apex Global (NEE), focusing on BGE-M3 retrieval.",
            "Unrelated facilities maintenance log.",
            "Quarterly finance budget discussion.",
            "Cloud infrastructure provisioning plan.",
        ]
    )
    scores = reranker.score_query("NEE BGE-M3 retrieval")
    assert scores[0] > scores[1]
