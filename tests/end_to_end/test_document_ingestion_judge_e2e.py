"""
tests/end_to_end/test_document_ingestion_judge_e2e.py

End-to-end verification of high-signal document ingestion and Output 03
GraphExtractionJudge calibration in an isolated sandbox.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from dual_pipeline.document_pipeline_core.evaluator import (
    evaluate_document_judge_and_release,
)
from dual_pipeline.document_pipeline_core.executor import (
    DocumentGraphExtractorExecutor,
)
from dual_pipeline.llm_judge_core.judges.graph import GraphExtractionJudge


def _build_lens_prd_source_and_candidate() -> tuple[str, dict]:
    source_text = (
        "# LENS Product Requirements Document (PRD)\n\n"
        "LENS is an internal AI-powered knowledge platform designed for PartnerAdvisory consultants "
        "that transforms delivery artifacts into structured, reusable intelligence.\n"
        "The platform indexes architecture specifications, statements of work, and technical memos "
        "into a governed enterprise knowledge graph with verbatim character-offset provenance."
    )
    quote_1 = (
        "LENS is an internal AI-powered knowledge platform designed for PartnerAdvisory consultants "
        "that transforms delivery artifacts into structured, reusable intelligence."
    )
    quote_2 = (
        "The platform indexes architecture specifications, statements of work, and technical memos "
        "into a governed enterprise knowledge graph with verbatim character-offset provenance."
    )
    idx_1 = source_text.index(quote_1)
    idx_2 = source_text.index(quote_2)
    candidate = {
        "schema_version": "5.7",
        "context": {
            "project": "partner_advisory_internal",
            "classification": "internal",
            "audiences": ["internal"],
            "source_tags": ["document", "prd", "architecture"],
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
                {
                    "id": "system:enterprise_knowledge_graph",
                    "type": "system",
                    "name": "Enterprise Knowledge Graph",
                    "description": "Governed knowledge graph storing indexed delivery artifacts with character-offset provenance.",
                    "mutation": "CREATE",
                    "evidence_ids": ["ev-0002"],
                },
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-0001",
                    "from_id": "org:partner_advisory",
                    "predicate": "OWNS",
                    "to_id": "system:lens_platform",
                    "description": "PartnerAdvisory operates the LENS knowledge platform for consultants.",
                    "confidence": 0.96,
                    "epistemic_status": "OBSERVED",
                    "evidence_ids": ["ev-0001"],
                },
                {
                    "signal_id": "sig-0002",
                    "from_id": "system:lens_platform",
                    "predicate": "INTEGRATES_WITH",
                    "to_id": "system:enterprise_knowledge_graph",
                    "description": "LENS indexes delivery artifacts into the governed enterprise knowledge graph.",
                    "confidence": 0.94,
                    "epistemic_status": "OBSERVED",
                    "evidence_ids": ["ev-0002"],
                },
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
                "text": quote_1,
                "exact_text": quote_1,
                "start_offset": idx_1,
                "end_offset": idx_1 + len(quote_1),
                "source_locator": "L2",
                "speaker": "Product Strategy",
                "source_classification": "FACT",
                "grounding_status": "GROUNDED",
            },
            "ev-0002": {
                "evidence_id": "ev-0002",
                "kind": "technical",
                "text": quote_2,
                "exact_text": quote_2,
                "start_offset": idx_2,
                "end_offset": idx_2 + len(quote_2),
                "source_locator": "L3",
                "speaker": "Product Strategy",
                "source_classification": "FACT",
                "grounding_status": "GROUNDED",
            },
        },
    }
    return source_text, candidate


class TestDocumentIngestionJudgeE2E(unittest.TestCase):
    """End-to-end document extraction and single-judge qualification in an isolated sandbox."""

    def test_document_ingestion_judge_e2e_lens_prd_lifecycle(self):
        source_text, candidate = _build_lens_prd_source_and_candidate()
        provider = MagicMock()
        provider.synthesize_graph.return_value = json.dumps(candidate)

        metadata = {
            "project": "partner_advisory_internal",
            "title": "LENS Product Requirements Document",
            "classification": "internal",
            "audiences": ["internal"],
            "date": "10/15/2026",
            "is_document": True,
            "modality": "DOCUMENT",
        }

        executor = DocumentGraphExtractorExecutor(provider=provider)
        with patch(
            "dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence.run_5_agent_document_intelligence"
        ) as mock_arbiter:
            extracted_f3 = executor.execute(
                document_text=source_text,
                pa_context={"metadata": metadata},
            )
        self.assertEqual(mock_arbiter.call_count, 0)
        self.assertEqual(provider.synthesize_graph.call_count, 1)

        fake_judge_scorecard = {
            "meeting_archetype": "DOCUMENT_MODE",
            "dimension_1_strategic_edge": 15,
            "dimension_2_knowledge_layer_density": 7,  # Concise high-signal selection (<11.0 transcript floor)
            "dimension_3_semantic_atomicity": 20,
            "dimension_4_predicate_specificity": 15,
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 19,
            "composite_score": 91,
            "verdict": "PASS",
            "clarification_candidates": [],
            "forensic_rationale": "High-signal PRD claims grounded via character offsets without spoken transcript artifacts.",
            "defect_codes": [],
            "actionable_remediation_diff": "",
            "grounding_citations": ["L2", "L3"],
        }
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = SimpleNamespace(
            model="gpt-5.4",
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(fake_judge_scorecard)))],
            usage=SimpleNamespace(prompt_tokens=450, completion_tokens=140, total_tokens=590),
        )

        with patch(
            "dual_pipeline.llm_judge_core.judges.graph._resolve_graph_client",
            return_value=mock_client,
        ), patch.object(GraphExtractionJudge, "evaluate_agentic_nodes") as mock_leverage:
            qa_report, judge_report, final_decision = evaluate_document_judge_and_release(
                f3_text=extracted_f3,
                source_text=source_text,
                metadata=metadata,
                mock=False,
                model="gpt-5.4",
                reasoning_effort="low",
            )

        self.assertEqual(mock_leverage.call_count, 0)
        self.assertEqual(mock_client.chat.completions.create.call_count, 1)
        self.assertTrue(qa_report["passed"])
        self.assertEqual(judge_report["status"], "PASS")
        self.assertEqual(judge_report["output_3_evaluation"]["meeting_archetype"], "DOCUMENT_MODE")
        self.assertEqual(judge_report["output_3_evaluation"]["dimension_2_knowledge_layer_density"], 7)
        self.assertEqual(judge_report["output_3_evaluation"]["composite_score"], 91)
        self.assertTrue(final_decision["final_pass"])
        self.assertTrue(final_decision["authoritative_write_authorized"])

        with tempfile.TemporaryDirectory(prefix="eval_sandbox_lens_prd_") as tmp_dir:
            sandbox_dir = Path(tmp_dir)
            judge_receipt_path = sandbox_dir / "llm_judge_evaluation.json"
            judge_receipt_path.write_text(json.dumps(judge_report, indent=2), encoding="utf-8")
            persisted_judge = json.loads(judge_receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted_judge["status"], "PASS")
            self.assertEqual(persisted_judge["output_1_evaluation"]["verdict"], "NOT_APPLICABLE")
            self.assertEqual(persisted_judge["output_2_evaluation"]["verdict"], "NOT_APPLICABLE")
            self.assertEqual(persisted_judge["output_3_evaluation"]["verdict"], "PASS")
            self.assertEqual(persisted_judge["output_3_evaluation"]["meeting_archetype"], "DOCUMENT_MODE")

    def test_document_ingestion_judge_e2e_short_circuits_on_gate3_failure(self):
        source_text, candidate = _build_lens_prd_source_and_candidate()
        candidate["graph_mutations"]["action_updates"] = [
            {"action_id": "act-1", "description": "Schedule follow-up sprint task"}
        ]

        with patch.object(GraphExtractionJudge, "evaluate") as mock_judge:
            qa_report, judge_report, final_decision = evaluate_document_judge_and_release(
                f3_text=candidate,
                source_text=source_text,
                metadata={"project": "partner_advisory_internal", "title": "LENS PRD"},
                mock=False,
            )

        self.assertEqual(mock_judge.call_count, 0)
        self.assertFalse(qa_report["passed"])
        self.assertEqual(judge_report["output_3_evaluation"]["status"], "SKIPPED_DETERMINISTIC_QA_FAILURE")
        self.assertEqual(judge_report["execution"]["status"], "SKIPPED_DETERMINISTIC_QA_FAILURE")
        self.assertFalse(final_decision["final_pass"])
        self.assertFalse(final_decision["authoritative_write_authorized"])
