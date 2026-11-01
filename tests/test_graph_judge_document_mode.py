"""
Wave 2 unit and integration tests for DOCUMENT_MODE GraphExtractionJudge calibration
and elimination of default 4-call LLM arbiter sprawl in document ingestion.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from dual_pipeline.document_pipeline_core.evaluator import (
    evaluate_document_judge_and_release,
)
from dual_pipeline.document_pipeline_core.executor import (
    DocumentGraphExtractorExecutor,
)
from dual_pipeline.llm_judge_core.judges.graph import (
    GraphExtractionJudge,
    build_graph_judge_user_content,
)
from dual_pipeline.prompts import CANONICAL_PROMPT_REGISTRY


def _build_valid_lens_prd_projection(source_text: str) -> dict:
    exact_quote = (
        "LENS is an internal AI-powered knowledge platform designed for PartnerAdvisory consultants "
        "that transforms delivery artifacts into structured, reusable intelligence."
    )
    start_idx = source_text.index(exact_quote)
    end_idx = start_idx + len(exact_quote)
    return {
        "schema_version": "5.7",
        "context": {
            "project": "partner_advisory_internal",
            "classification": "internal",
            "audiences": ["internal"],
            "source_tags": ["document", "prd"],
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "system:lens_platform",
                    "type": "system",
                    "name": "LENS Platform",
                    "description": "Internal AI knowledge platform for PartnerAdvisory consultants.",
                    "mutation": "CREATE",
                    "evidence_ids": ["ev-0001"],
                },
                {
                    "id": "org:partner_advisory",
                    "type": "org",
                    "name": "PartnerAdvisory",
                    "description": "Consulting organization operating the LENS knowledge platform.",
                    "mutation": "CREATE",
                    "evidence_ids": ["ev-0001"],
                },
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-0001",
                    "from_id": "org:partner_advisory",
                    "predicate": "OWNS",
                    "to_id": "system:lens_platform",
                    "description": "PartnerAdvisory consultants use LENS to transform delivery artifacts into reusable intelligence.",
                    "confidence": 0.95,
                    "epistemic_status": "OBSERVED",
                    "evidence_ids": ["ev-0001"],
                }
            ],
            "decision_updates": [],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [],
            "strategic_intent_signals": [],
        },
        "evidence_index": {
            "ev-0001": {
                "evidence_id": "ev-0001",
                "kind": "technical",
                "text": exact_quote,
                "exact_text": exact_quote,
                "start_offset": start_idx,
                "end_offset": end_idx,
                "source_locator": "L1",
                "speaker": "Product Strategy",
                "source_classification": "FACT",
                "grounding_status": "GROUNDED",
            }
        },
    }


def test_build_graph_judge_user_content_document_mode_uses_document_envelope():
    """DOCUMENT_MODE wraps source text in <reference_source_document> and omits spoken transcript framing."""
    gov_ctx = "<governance_context>\n  <meeting_archetype>DOCUMENT_MODE</meeting_archetype>\n</governance_context>"
    doc_text = "# LENS PRD\nLENS is an internal AI-powered knowledge platform."
    cand_json = '{"schema_version": "5.7"}'

    doc_content = build_graph_judge_user_content(
        gov_ctx, doc_text, cand_json, metadata={"modality": "DOCUMENT"}, archetype="DOCUMENT_MODE"
    )
    assert "<reference_source_document>" in doc_content
    assert "<document_mode_evaluation_directive>" in doc_content
    assert "<reference_forensic_transcript>" not in doc_content
    assert "Do NOT deduct points for missing C0 spoken-roster receipts" in doc_content

    transcript_content = build_graph_judge_user_content(
        gov_ctx, doc_text, cand_json, metadata={"modality": "TRANSCRIPT"}, archetype="ARCHITECTURE_DEEP_DIVE"
    )
    assert "<reference_forensic_transcript>" in transcript_content
    assert "<reference_source_document>" not in transcript_content


def test_graph_judge_prompt_registers_document_mode_rules():
    """GRAPH_EXTRACTION_JUDGE_PROMPT.md registers DOCUMENT_MODE without spoken-transcript penalties."""
    prompt_text = CANONICAL_PROMPT_REGISTRY.get_prompt("JUDGE_GRAPH")
    assert "DOCUMENT_MODE" in prompt_text
    assert "do NOT reclassify or normalize into `STRATEGIC_DISCOVERY`" in prompt_text
    assert "do NOT deduct points for missing C0 spoken-roster receipts" in prompt_text
    assert "In `DOCUMENT_MODE`, Dimension 2 has no mandatory floor" in prompt_text


def test_graph_extraction_judge_document_mode_single_call_and_dim2_exemption():
    """GraphExtractionJudge in DOCUMENT_MODE makes 1 LLM call (0 AgenticLeverageJudge calls) and preserves DOCUMENT_MODE."""
    source_text = (
        "# LENS Product Requirements Document\n\n"
        "LENS is an internal AI-powered knowledge platform designed for PartnerAdvisory consultants "
        "that transforms delivery artifacts into structured, reusable intelligence."
    )
    projection = _build_valid_lens_prd_projection(source_text)
    bipartite_payload = {
        "client_safe": projection,
        "agentic_leverage": [{"node_id": "adv-1", "claim": "Internal advisory note"}],
    }

    fake_llm_scorecard = {
        "meeting_archetype": "STRATEGIC_DISCOVERY",  # Even if LLM attempts to drift, DOCUMENT_MODE lock preserves DOCUMENT_MODE
        "dimension_1_strategic_edge": 15,
        "dimension_2_knowledge_layer_density": 8,  # Concise high-signal extraction below 11.0 transcript floor
        "dimension_3_semantic_atomicity": 20,
        "dimension_4_predicate_specificity": 15,
        "dimension_5_exclusion_of_action_items": 15,
        "dimension_6_evidence_grounding_and_schema": 19,
        "composite_score": 92,
        "verdict": "PASS",
        "clarification_candidates": [],
        "forensic_rationale": "Written PRD core claims and system ownership accurately extracted.",
        "defect_codes": [],
        "actionable_remediation_diff": "",
        "grounding_citations": ["char:38-198"],
    }

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        model="gpt-5.4",
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(fake_llm_scorecard)))],
        usage=SimpleNamespace(prompt_tokens=400, completion_tokens=120, total_tokens=520),
    )

    judge = GraphExtractionJudge(model="gpt-5.4", reasoning_effort="low", mock=False)
    with patch.object(judge, "evaluate_agentic_nodes") as mock_leverage:
        res = judge.evaluate(
            f3_text=json.dumps(bipartite_payload),
            f1_text=source_text,
            client=mock_client,
            mock=False,
            archetype="DOCUMENT_MODE",
            metadata={"archetype": "DOCUMENT_MODE", "modality": "DOCUMENT", "is_document": True},
        )

    assert mock_leverage.call_count == 0
    assert mock_client.chat.completions.create.call_count == 1
    sent_messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
    assert "<reference_source_document>" in sent_messages[1]["content"]
    assert "<meeting_archetype>DOCUMENT_MODE</meeting_archetype>" in sent_messages[1]["content"]
    assert res["meeting_archetype"] == "DOCUMENT_MODE"
    assert res["dimension_2_knowledge_layer_density"] == 8
    assert res["composite_score"] == 92
    assert res["verdict"] == "PASS"


def test_document_extractor_executor_skips_5_agent_sprawl_by_default():
    """DocumentGraphExtractorExecutor.execute makes 0 multi-agent arbiter calls by default."""
    source_text = (
        "# LENS Product Requirements Document\n\n"
        "LENS is an internal AI-powered knowledge platform designed for PartnerAdvisory consultants "
        "that transforms delivery artifacts into structured, reusable intelligence.\n"
        + ("Architectural details and system specifications follow. " * 6)
    )
    projection = _build_valid_lens_prd_projection(source_text)
    provider = MagicMock()
    provider.synthesize_graph.return_value = json.dumps(projection)

    executor = DocumentGraphExtractorExecutor(provider=provider)
    with patch(
        "dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence.run_5_agent_document_intelligence"
    ) as mock_5_agent:
        output_str = executor.execute(
            document_text=source_text,
            pa_context={
                "metadata": {
                    "project": "partner_advisory_internal",
                    "title": "LENS PRD",
                    "classification": "internal",
                    "audiences": ["internal"],
                }
            },
        )

    assert mock_5_agent.call_count == 0
    assert provider.synthesize_graph.call_count == 1
    parsed = json.loads(output_str)
    assert any(e["id"] == "system:lens_platform" for e in parsed["graph_mutations"]["entity_upserts"])


def test_document_evaluator_short_circuits_judge_on_ungrounded_signal():
    """Ungrounded relationship signals fail deterministic Gate 3 and invoke 0 LLM judge calls."""
    source_text = (
        "# LENS Product Requirements Document\n\n"
        "LENS is an internal AI-powered knowledge platform designed for PartnerAdvisory consultants "
        "that transforms delivery artifacts into structured, reusable intelligence."
    )
    projection = _build_valid_lens_prd_projection(source_text)
    projection["graph_mutations"]["relationship_signals"].append(
        {
            "signal_id": "sig-ungrounded",
            "from_id": "system:lens_platform",
            "predicate": "INTEGRATES_WITH",
            "to_id": "org:partner_advisory",
            "description": "Quantum blockchain orbital telemetry fabric synchronization.",
            "confidence": 0.91,
            "epistemic_status": "OBSERVED",
            "evidence_ids": ["ev-missing-9999"],
        }
    )

    with patch.object(GraphExtractionJudge, "evaluate") as mock_judge_eval:
        qa_report, judge_report, final_decision = evaluate_document_judge_and_release(
            f3_text=projection,
            source_text=source_text,
            metadata={"project": "partner_advisory_internal", "title": "LENS PRD"},
            mock=False,
        )

    assert qa_report["passed"] is False
    assert mock_judge_eval.call_count == 0
    assert judge_report["output_3_evaluation"]["verdict"] == "FAIL"
    assert final_decision["final_pass"] is False
