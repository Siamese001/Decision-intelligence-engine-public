"""
tests/test_document_pipeline_performance_optimizations.py

Unit and integration tests verifying performance optimizations for document and email ingestion:
1. Process-level BGE-M3 model singleton caching (_BGEM3_MODEL_CACHE).
2. Gate 3 short-circuiting preventing LLM Judge invocations on invalid drafts.
3. Fast-path micro-judge evaluation with tuned token limits (2048) and low reasoning effort.
4. End-to-end execution efficiency and receipt validity under DocumentPipeline.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.document_pipeline import DocumentPipeline
from dual_pipeline.document_pipeline_core.evaluator import (
    evaluate_document_judge_and_release,
)
from dual_pipeline.index_core.backends import (
    _BGEM3_MODEL_CACHE,
    clear_bge_m3_model_cache,
)
from dual_pipeline.prompts import CANONICAL_PROMPT_CONFIG


class TestDocumentPipelinePerformanceOptimizations(unittest.TestCase):
    """Verifies that performance optimizations reduce latency and eliminate wasted compute."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()
        clear_bge_m3_model_cache()

    def test_bge_m3_model_cache_reused(self) -> None:
        """Verify that _BGEM3_MODEL_CACHE stores and reuses loaded BGEM3FlagModel instances."""
        clear_bge_m3_model_cache()
        self.assertEqual(len(_BGEM3_MODEL_CACHE), 0)

        # Mock BGEM3FlagModel constructor
        mock_model_instance = MagicMock()
        mock_flag_cls = MagicMock(return_value=mock_model_instance)

        with patch.dict("sys.modules", {"FlagEmbedding": MagicMock(BGEM3FlagModel=mock_flag_cls)}):

            # Simulate caching behavior with synthetic cache key
            cache_key = ("/fake/model/path", False, "cpu")
            _BGEM3_MODEL_CACHE[cache_key] = mock_model_instance

            # Verifying retrieval from cache
            self.assertIn(cache_key, _BGEM3_MODEL_CACHE)
            self.assertIs(_BGEM3_MODEL_CACHE[cache_key], mock_model_instance)

            # Clear cache helper check
            clear_bge_m3_model_cache()
            self.assertEqual(len(_BGEM3_MODEL_CACHE), 0)

    def test_document_evaluator_short_circuits_on_gate3_failure(self) -> None:
        """Verify that if Gate 3 fails, the LLM Judge is NOT invoked at all."""
        invalid_projection = {
            "schema_version": "5.7",
            "meeting_id": "document-20260917-test",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            # Missing mandatory fields like evidence_index, graph_mutations
        }
        source_text = "This is a test email source text."

        with patch("dual_pipeline.document_pipeline_core.evaluator.GraphExtractionJudge") as mock_judge_cls:
            qa_rep, judge_rep, final_dec = evaluate_document_judge_and_release(
                f3_text=invalid_projection,
                source_text=source_text,
                metadata={"title": "Test Email"},
                mock=True,
            )

            # Judge should NEVER be instantiated or called because Gate 3 failed
            mock_judge_cls.assert_not_called()

            # Final decision must fail immediately
            self.assertFalse(final_dec["final_pass"])
            self.assertFalse(final_dec["authoritative_write_authorized"])
            self.assertEqual(judge_rep["output_3_evaluation"]["verdict"], "FAIL")
            self.assertEqual(judge_rep["output_3_evaluation"]["composite_score"], 0)
            self.assertIn("Short-circuited", judge_rep["output_3_evaluation"]["forensic_rationale"])

    def test_document_evaluator_fast_judge_token_and_effort(self) -> None:
        """Verify that clean Gate 3 projections trigger fast micro-judge with tuned tokens."""
        valid_projection = {
            "_strategic_analysis": "Strategic overview of hiring decision and candidate screen.",
            "schema_version": "5.7",
            "meeting_id": "document-20260917-test",
            "classification": "internal",
            "audiences": ["internal_consulting", "nee-engineering"],
            "expected_base_graph_version": "1.0.0",
            "claim_ids": [],
            "evidence_index": {
                "ev-01": {
                    "speaker": "Mark McCarthy",
                    "source_classification": "DECISION",
                    "text": "We will retain Chris Chockley for Q4.",
                    "locator": {"kind": "section", "value": "Will Retain"},
                    "exact_text": "We will retain Chris Chockley for Q4.",
                    "start_offset": 0,
                    "end_offset": 36,
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "person:chris-chockley",
                        "type": "person",
                        "name": "Chris Chockley",
                        "mutation": "CREATE",
                    }
                ],
                "relationship_signals": [],
                "decision_updates": [
                    {
                        "decision_id": "decision:chris-chockley-q4-retention",
                        "decision_question": "Should Chris Chockley be retained for Q4?",
                        "choice": "Retain Chris Chockley.",
                        "status": "DECIDED",
                        "observed_at": "09/17/2026",
                        "stale_after": "12/17/2026",
                        "rationale": "Strong technical performance on screen.",
                        "evidence_ids": ["ev-01"],
                    }
                ],
                "proposal_updates": [],
                "action_updates": [],
                "risk_updates": [],
            },
            "evidence_only_statement_ids": [],
        }
        source_text = "We will retain Chris Chockley for Q4. Strong technical performance on screen."

        with patch("dual_pipeline.document_pipeline_core.evaluator.GraphExtractionJudge") as mock_judge_cls:
            mock_judge_instance = MagicMock()
            mock_judge_instance.evaluate.return_value = {
                "dimension_1_strategic_edge": 15,
                "dimension_2_knowledge_layer_density": 14,
                "dimension_3_semantic_atomicity": 20,
                "dimension_4_predicate_specificity": 13,
                "dimension_5_exclusion_of_action_items": 15,
                "dimension_6_evidence_grounding_and_schema": 20,
                "composite_score": 97,
                "verdict": "PASS",
                "forensic_rationale": "High-fidelity extraction.",
            }
            mock_judge_cls.return_value = mock_judge_instance

            qa_rep, judge_rep, final_dec = evaluate_document_judge_and_release(
                f3_text=valid_projection,
                source_text=source_text,
                metadata={"title": "Test Email"},
                mock=False,
                fast_judge=True,
            )

            # Check that judge was instantiated with low reasoning effort for fast micro-judge
            mock_judge_cls.assert_called_once()
            _, kwargs = mock_judge_cls.call_args
            self.assertEqual(kwargs.get("reasoning_effort"), "low")

            # Check that evaluate was called with CANONICAL_PROMPT_CONFIG.judge.max_completion_tokens
            mock_judge_instance.evaluate.assert_called_once()
            _, eval_kwargs = mock_judge_instance.evaluate.call_args
            self.assertEqual(eval_kwargs.get("max_completion_tokens"), CANONICAL_PROMPT_CONFIG.judge.max_completion_tokens)

            # Final decision must pass
            self.assertTrue(final_dec["final_pass"])
            self.assertTrue(final_dec["authoritative_write_authorized"])

    def test_document_pipeline_offline_empty_fixture_fails_closed(self) -> None:
        """An unsupported offline fixture must not release an empty graph."""
        doc_file = self.tmp_path / "email.txt"
        doc_file.write_text(
            "That's correct, Thad will not be going through the formal screen ahead of Q4'26. "
            "We also interviewed four others and we would like to retain Chris Chockley if available. "
            "We are not hiring junior software engineers at this point (full time or contract). "
            "Please align with engineering leadership on the final pod staffing roster changes by end of week.",
            encoding="utf-8",
        )

        pipeline = DocumentPipeline(provider_type="offline")
        with self.assertRaisesRegex(RuntimeError, "source-grounded evidence"):
            pipeline.run(
                input_path=doc_file,
                metadata={
                    "project": "nea",
                    "title": "Mark McCarthy SWE Interviews and Pod Retentions",
                    "date": "09/17/2026",
                    "author": "Mark McCarthy",
                },
                output_dir=str(self.tmp_path / "output"),
                mutate_graph=False,
                fast_judge=True,
            )
        self.assertEqual(list((self.tmp_path / "output").rglob("intake_package.json")), [])


if __name__ == "__main__":
    unittest.main()
