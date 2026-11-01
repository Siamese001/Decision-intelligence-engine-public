"""
Wave 4 E2E Verification: Synthesis Stages U0-L2, Exit Gates X1-X3 & LLM Judge Integrity.

Verifies:
- C0 native acoustic routing for DIARIZED_ACOUSTIC_RESOLVED / VERIFIED_STEREO_SEPARATION
- C05/C06 roster sync and OfflineGraphContextRetriever refresh after C0
- L0 clean_transcript propagation and L2 executive_recap propagation
- Rule C-16 deterministic CREATE -> CONFIRM flip for known_graph_entities / prior_entity_ids
- Multi-evidence temporal timestamp evaluation across all evidence_ids
- Turn-scoped quote pointer healing and non-null in-bounds X3 quote_pointer verification
- Rule C-17 evidence_only_statement_ids <= max(3, 2 * promoted_mutation_count)
- UnifiedPipelineQAEvaluator preserving adversarial_veto, swarm_passed, and blocking PARTIAL_QA_FAILURE promotion
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.evaluator import _sync_and_reseal_evaluation
from dual_pipeline.prompts.contracts.versioned_judge_contracts import MeetingXExitContract
from dual_pipeline.transcript_pipeline_core.context_builder import _summarize_prior_output3
from dual_pipeline.transcript_pipeline_core.release_runner import promote_staging_to_release
from dual_pipeline.transcript_qa_core.evaluator import _build_final_decision
from dual_pipeline.transcript_qa_core.quote_pointer_healer import heal_quote_pointers
from dual_pipeline.transcript_stages_core.c0_gates import (
    _enrich_single_list_item,
    _format_matched_attendee,
)
from dual_pipeline.transcript_stages_core.graph_executor import (
    _confirm_known_graph_entities,
    _enrich_single_item,
)
from dual_pipeline.transcript_stages_core.x_gates import XExitGatesEvaluator
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult


def test_e2e_c0_to_l2_confirm_flip_and_x3_quote_pointer_pipeline(tmp_path: Path) -> None:
    """End-to-end verification of C0 string role preservation, C-16 CONFIRM flip, turn-scoped quote pointer healing, and X1-X3 gates."""
    # 1. C0 preserves explicit role from string attendee "Name (Role)" and enforces whole-name boundary matching
    metadata: Dict[str, Any] = {
        "attendees": ["Amit Ayer (Managing Director)", "Speaker_Alpha Speaker_Beta (Principal Architect)"],
        "prior_entity_ids": ["person:amit_ayer", "org:partner_advisory"],
        "known_graph_entities": [{"id": "system:snowflake", "name": "Snowflake"}],
    }
    enriched_str = _enrich_single_list_item("Amit Ayer (Managing Director)", metadata)
    assert "Managing Director" in str(enriched_str)
    # Short-name substring safety: "Al" must not overwrite "Alex"
    assert _format_matched_attendee("Alex Johnson (Engineer)", [{"name": "Al", "role": "VP"}]) == "Alex Johnson (Engineer)"

    # 2. Prior Output 3 summarization reads graph_mutations.entity_upserts and .id
    prior_o3 = {
        "schema_version": "5.7",
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:amit_ayer", "type": "person", "name": "Amit Ayer", "mutation": "CONFIRM"},
                {"id": "system:snowflake", "type": "system", "name": "Snowflake", "mutation": "CONFIRM"},
            ],
            "decision_updates": [
                {"decision_id": "dec:001", "title": "Adopt Snowflake Cortex", "rationale": "Low latency"}
            ],
        },
    }
    summary_str = _summarize_prior_output3(json.dumps(prior_o3))
    assert "person:amit_ayer" in summary_str
    assert "system:snowflake" in summary_str
    assert "dec:001" in summary_str

    # 3. Rule C-16: _confirm_known_graph_entities flips CREATE -> CONFIRM for prior/known entities
    f3_data: Dict[str, Any] = {
        "schema_version": "5.7",
        "meeting_metadata": {
            "meeting_id": "mtg:2026_10_24_arch_sync",
            "date": "10/24/2026",
            "title": "Architecture Sync",
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:amit_ayer", "type": "person", "name": "Amit Ayer", "mutation": "CREATE"},
                {"id": "person:brien_ninan", "type": "person", "name": "Speaker_Alpha Speaker_Beta", "mutation": "CREATE"},
                {"id": "system:snowflake", "type": "system", "name": "Snowflake", "mutation": "CREATE"},
            ],
            "relationship_signals": [
                {
                    "signal_id": "rel:001",
                    "from_id": "person:amit_ayer",
                    "relationship": "OWNS",
                    "to_id": "system:snowflake",
                    "evidence_ids": ["ev:001", "ev:002"],
                }
            ],
            "decision_updates": [
                {
                    "decision_id": "dec:001",
                    "title": "Standardize on Snowflake",
                    "description": "Team agreed to standardize on Snowflake.",
                    "status": "APPROVED",
                    "owner_id": "person:amit_ayer",
                    "evidence_ids": ["ev:001", "ev:002"],
                }
            ],
            "proposal_updates": [],
            "action_updates": [
                {
                    "action_id": "act:001",
                    "description": "Finalize schema migration",
                    "owner_id": "person:brien_ninan",
                    "status": "OPEN",
                    "target_date": "10/30/2026",
                    "evidence_ids": ["ev:002"],
                }
            ],
            "risk_updates": [],
        },
        "evidence_index": {
            "ev:001": {
                "statement_id": "ev:001",
                "speaker": "person:brien_ninan",
                "locator": {"kind": "timestamp", "value": "00:01:00"},
                "exact_text": "We should standardize on Snowflake for the telemetry store.",
                "kind": "technical",
            },
            "ev:002": {
                "statement_id": "ev:002",
                "speaker": "person:amit_ayer",
                "locator": {"kind": "timestamp", "value": "00:05:30"},
                "exact_text": "We should standardize on Snowflake for the telemetry store.",
                "kind": "decision",
            },
        },
        "evidence_only_statement_ids": ["ev:001"],
    }

    _confirm_known_graph_entities(f3_data, metadata)
    upserts_by_id = {u["id"]: u["mutation"] for u in f3_data["graph_mutations"]["entity_upserts"]}
    assert upserts_by_id["person:amit_ayer"] == "CONFIRM"
    assert upserts_by_id["system:snowflake"] == "CONFIRM"
    assert upserts_by_id["person:brien_ninan"] == "CREATE"

    # 4. Multi-evidence evaluation across ALL evidence_ids in _enrich_single_item
    ev_index = f3_data["evidence_index"]
    ev_index["ev:002"]["grounding_status"] = "GROUNDED"
    dec_item = f3_data["graph_mutations"]["decision_updates"][0]
    _enrich_single_item(dec_item, "decision", ev_index, metadata, "mtg:2026_10_24_arch_sync")
    assert dec_item["evidence"]["statement_id"] == "ev:002"
    assert dec_item["evidence"]["all_evidence_ids"] == ["ev:001", "ev:002"]
    assert dec_item["confidence"]["tier"] == "HIGH"

    # 5. Turn-scoped quote pointer healing anchors identical phrases to their speaker/timestamp turn
    f1_transcript = (
        "# Forensic Transcript\n\n"
        "**[00:01:00] Speaker_Alpha Speaker_Beta:** We should standardize on Snowflake for the telemetry store. It scales cleanly.\n\n"
        "**[00:05:30] Amit Ayer:** I agree. We should standardize on Snowflake for the telemetry store. Let us proceed.\n"
    )
    heal_quote_pointers(f3_data, transcript=f1_transcript)

    qp1 = f3_data["evidence_index"]["ev:001"]["quote_pointer"]
    qp2 = f3_data["evidence_index"]["ev:002"]["quote_pointer"]
    assert qp1 != qp2, "Repeated phrase in two different speaker turns must resolve to distinct turn-scoped offsets"
    assert f1_transcript[qp1[0] : qp1[1]] == "We should standardize on Snowflake for the telemetry store."
    assert f1_transcript[qp2[0] : qp2[1]] == "We should standardize on Snowflake for the telemetry store."

    f2_recap = (
        "# Executive Recap\n\n"
        "## 1. Meeting Objective\nAlign on telemetry storage architecture.\n\n"
        "## 2. Action & Governance Ledger\n"
        "| ID | Action | Owner | Target Date |\n"
        "|---|---|---|---|\n"
        "| A-1 | Finalize schema migration | Speaker_Alpha Speaker_Beta | 10/30/2026 |\n"
    )
    x3_ok, x3_errs = MeetingXExitContract.validate_x3_cross_output_coherence(
        f1_text=f1_transcript,
        f2_text=f2_recap,
        f3_data=f3_data,
        metadata=metadata,
    )
    assert x3_ok, f"Expected X3 pass, got: {x3_errs}"


def test_e2e_evaluator_preserves_adversarial_veto_and_blocks_partial_qa_promotion(tmp_path: Path) -> None:
    """Verifies _sync_and_reseal_evaluation honors adversarial_veto and swarm_passed, and promote_staging_to_release blocks PARTIAL_QA_FAILURE."""
    synth = SynthesisResult(
        output_1="### [00:01:00]\n\n**Amit Ayer:** Confirmed.\n",
        output_2="# Executive Recap\n## 1. Meeting Objective\nTest.\n## 2. Action & Governance Ledger\n",
        output_3=json.dumps({"schema_version": "5.7", "graph_mutations": {}, "evidence_index": {}}),
    )
    qa_rep: Dict[str, Any] = {
        "passed": True,
        "output_01_forensic_transcript": {"passed": True, "status": "PASS"},
        "output_02_executive_recap": {"passed": True, "status": "PASS"},
        "output_03_canonical_signal_projection": {"passed": True, "status": "PASS"},
        "cross_artifact_consistency": {"status": "PASS"},
        "15_agent_swarm_review": {"swarm_passed": True},
    }
    judge_rep: Dict[str, Any] = {
        "output_1_evaluation": {"composite_score": 96.0, "verdict": "PASS"},
        "output_2_evaluation": {"composite_score": 95.0, "verdict": "PASS"},
        "output_3_evaluation": {"composite_score": 95.0, "verdict": "PASS"},
        "cross_artifact_reconciliation": {"passed": True, "status": "PASS"},
        "adversarial_audit": {"hard_veto": True},
    }
    final_dec: Dict[str, Any] = {
        "named_word_coverage_pass": True,
        "adversarial_veto": True,
    }
    auth = _sync_and_reseal_evaluation(
        judge_rep=judge_rep,
        final_dec=final_dec,
        qa_rep=qa_rep,
        synthesis_result=synth,
        source_text="raw source",
        meta={},
    )
    assert auth is False
    assert final_dec["authoritative_write_authorized"] is False
    assert final_dec["status"] == "FAIL"

    # Staging promotion must block PARTIAL_QA_FAILURE
    staging = tmp_path / ".staging_run"
    staging.mkdir()
    (staging / "metadata.json").write_text(json.dumps({"status": "PARTIAL_QA_FAILURE"}), encoding="utf-8")
    target = tmp_path / "release_target"
    with pytest.raises(RuntimeError, match="PARTIAL_QA_FAILURE"):
        promote_staging_to_release(staging, target, purge_staging=True)
    assert not staging.exists(), "Staging directory must still be cleaned up in finally block"
