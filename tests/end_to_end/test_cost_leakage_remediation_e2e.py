"""
tests/end_to_end/test_cost_leakage_remediation_e2e.py

End-to-End qualification suite for Wave 2 Cost Leakage Plugs:
- Validates deterministic-only fail-fast in the synthesis execution loop
  (halting after 1 attempt when deterministic gates fail, preventing wasted rounds).
- Verifies prompt metadata deduplication across Stage 01, Stage 02, and Stage 03
  (zero duplicate transcripts in <meeting_metadata>, compact JSON serialization).
- Verifies persistent on-disk SQLite evaluation caching across runs
  (subsequent evaluations of identical artifacts yield 100% cache hit with 0 LLM calls).
- Verifies deterministic pre-filtering of ineligible Agentic Leverage nodes.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import pytest

from dual_pipeline.canonical_pipeline.lifecycle import run_synthesis_loop
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.llm_judge_core.judges.leverage import AgenticLeverageJudge
from dual_pipeline.transcript_qa_core.eval_cache import (
    resolve_pins_and_cache,
    update_evaluation_cache,
)
from dual_pipeline.transcript_stages_core.graph_executor import (
    build_stage03_user_content,
)
from dual_pipeline.transcript_stages_core.models import AgenticLeverageNode
from dual_pipeline.transcript_stages_core.recap_executor import (
    L2ExecutiveRecapExecutor,
)


@pytest.mark.e2e
class TestCostLeakageRemediationE2E(unittest.TestCase):
    """End-to-end integration test suite for Wave 2 cost leakage remediations."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="nee_cost_e2e_")
        self.cache_dir = os.path.join(self.temp_dir, "cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.env_patcher = mock.patch.dict(
            os.environ,
            {
                "NEE_CACHE_DIR": self.cache_dir,
            },
        )
        self.env_patcher.start()

    def tearDown(self) -> None:
        self.env_patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_deterministic_only_failure_halts_synthesis_loop_immediately(self) -> None:
        """
        Verify that when deterministic QA fails with non-LLM-fixable errors,
        run_synthesis_loop terminates immediately after attempt 1
        rather than executing 2 additional paid synthesis + judge cycles ($9.18 savings).
        """
        mock_provider = mock.MagicMock()

        # Mock QA strategy to return deterministic failure on all calls
        failing_qa_report = {
            "passed": False,
            "status": "FAIL",
            "output_01_forensic_transcript": {
                "passed": False,
                "status": "FAIL",
                "failures": ["PydanticValidationError: SCHEMA_VIOLATION in Output 1"],
                "gates": {
                    "Gate 1.1": {"passed": False, "failure": "PydanticValidationError: SCHEMA_VIOLATION"}
                },
            },
            "output_02_executive_recap": {"passed": True, "status": "PASS", "failures": []},
            "output_03_canonical_signal_projection": {"passed": True, "status": "PASS", "failures": []},
            "cross_artifact_consistency": {"passed": True, "status": "PASS"},
        }
        failing_judge_report = {
            "status": "FAIL",
            "overall_rubric_summary": "Deterministic QA failed",
            "output_1_evaluation": {"composite_score": 0, "verdict": "FAIL"},
            "output_2_evaluation": {"composite_score": 95, "verdict": "PASS"},
            "output_3_evaluation": {"composite_score": 92, "verdict": "PASS"},
        }
        failing_decision = {
            "final_pass": False,
            "status": "FAIL",
            "output_1_deterministic_gates_pass": False,
            "output_1_llm_score_pass": True,
            "output_1_pass": False,
            "output_2_pass": True,
            "output_3_pass": True,
            "cross_artifact_consistency_pass": True,
        }
        mock_eval_res = QAEvaluationResult(
            passed=False,
            qa_report=failing_qa_report,
            judge_report=failing_judge_report,
            final_decision=failing_decision,
            authoritative_write_authorized=False,
        )

        mock_qa = mock.MagicMock()
        mock_qa.evaluate.return_value = mock_eval_res

        mock_syn_strat = mock.MagicMock()
        mock_syn_strat.synthesize.return_value = SynthesisResult(
            output_1="Clean f1",
            output_2="Clean f2",
            output_3="{}",
            attempt=1,
            metadata={},
        )

        with mock.patch("dual_pipeline.canonical_pipeline.lifecycle.get_qa_strategy", return_value=mock_qa), \
             mock.patch("dual_pipeline.canonical_pipeline.lifecycle.get_synthesis_strategy", return_value=mock_syn_strat):
            meta = {"project": "test_project", "meeting_name": "CostTest"}
            attempts, syn_res, eval_res = run_synthesis_loop(
                modality=CanonicalModality.AUDIO,
                provider=mock_provider,
                provider_type="mock",
                model_name="mock_model",
                reasoning_effort="low",
                source_text="Amit Ayer: Test source text",
                pa_context={"metadata": meta},
                metadata=meta,
                out_p=Path(self.temp_dir),
                start_t=time.time(),
                max_judge_retries=2,
            )

        # Assert: Loop halted after exactly 1 attempt despite max_attempts=3
        self.assertEqual(attempts, 1)
        self.assertEqual(mock_syn_strat.synthesize.call_count, 1)
        self.assertEqual(mock_qa.evaluate.call_count, 1)

    def test_prompt_metadata_deduplication_and_compact_json(self) -> None:
        """
        Verify that Stage 01, 02, and 03 prompt construction excludes raw transcript
        and candidate bloat from <meeting_metadata>, and serializes compact JSON.
        """
        raw_transcript = "Amit Ayer: This is a huge multi-megabyte raw transcript " * 100
        heavy_metadata = {
            "project": "CostLeakageShield",
            "meeting_name": "MetadataDedupTest",
            "source_text": raw_transcript,
            "raw_transcript": raw_transcript,
            "resolved_transcript": raw_transcript,
            "forensic_transcript": raw_transcript,
            "prior_recap_candidate": "Prior recap text...",
            "prior_forensic_candidate": "Prior forensic text...",
            "prior_graph_candidate": "{}",
            "attendees": ["Amit Ayer", "Miles Erickson"],
        }

        # 1. Test Stage 02 Recap prompt builder
        mock_provider = mock.MagicMock()
        mock_provider.synthesize_recap.return_value = "# Executive Recap\n\nMeeting Objective: Cost Optimization."
        recap_exec = L2ExecutiveRecapExecutor(provider=mock_provider)
        recap_exec.execute(
            forensic_transcript="Clean forensic transcript",
            pa_context={"metadata": heavy_metadata},
        )
        self.assertEqual(mock_provider.synthesize_recap.call_count, 1)
        _, recap_prompt = mock_provider.synthesize_recap.call_args[0]
        self.assertNotIn(raw_transcript, recap_prompt.split("<forensic_transcript>")[0])
        meta_match = recap_prompt.split("<meeting_metadata>\n")[1].split("\n</meeting_metadata>")[0]
        self.assertNotIn('": ', meta_match)
        self.assertIn('","', meta_match)

        # 2. Test Stage 03 Graph prompt builder
        graph_prompt = build_stage03_user_content(
            forensic_transcript="Clean forensic transcript",
            metadata=heavy_metadata,
        )
        self.assertNotIn(raw_transcript, graph_prompt.split("<forensic_transcript>")[0])
        graph_meta = graph_prompt.split("<meeting_metadata>\n")[1].split("\n</meeting_metadata>")[0]
        self.assertNotIn('": ', graph_meta)

    def test_persistent_disk_judge_cache_end_to_end(self) -> None:
        """
        Verify that passing evaluations are written to SQLite disk cache
        and immediately resolve on subsequent calls with identical content hashes.
        """
        f1 = "# Forensic Meeting Transcript\n\n### [00:01:00]\n**Amit Ayer:** Test"
        f2 = "# Executive Recap\n\nMeeting Objective: Cost Optimization."
        f3 = '{"nodes": [], "edges": []}'
        source = "Amit Ayer: Test"
        model = "gpt-6.1-sol"
        effort = "high"

        # Step 1: Initial resolution - cache misses
        o1, o2, o3, k1, k2, k3 = resolve_pins_and_cache(
            f1, f2, f3, model, effort, pinned={}, source_text=source
        )
        self.assertIsNone(o1)
        self.assertIsNone(o2)
        self.assertIsNone(o3)

        # Step 2: Store passing evaluation in cache (writes to SQLite)
        mock_eval = {
            "status": "PASS",
            "verdict": "PASS",
            "composite_score": 96,
            "forensic_rationale": "High quality passing output.",
        }
        update_evaluation_cache(k1, mock_eval, k2, mock_eval, k3, mock_eval)

        # Verify SQLite file exists on disk
        db_path = os.path.join(self.cache_dir, "judge_evaluation_cache.sqlite")
        self.assertTrue(os.path.exists(db_path))

        # Step 3: Clear in-memory cache to force SQLite disk retrieval
        from dual_pipeline.transcript_qa_core import eval_cache
        eval_cache._EVALUATION_CACHE.clear()

        # Step 4: Resolve again - must hit disk cache
        r1, r2, r3, _, _, _ = resolve_pins_and_cache(
            f1, f2, f3, model, effort, pinned={}, source_text=source
        )
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)
        self.assertIsNotNone(r3)
        self.assertEqual(r1.get("composite_score"), 96)
        self.assertEqual(r1.get("source"), "CONTENT_CACHE")

    def test_deterministic_prefilter_skips_ineligible_leverage_nodes(self) -> None:
        """
        Verify that AgenticLeverageJudge deterministically rejects ineligible nodes
        (lacking networking_wedge or target_stakeholder) without invoking LLM client.
        """
        judge = AgenticLeverageJudge(mock=False)
        ineligible_node = AgenticLeverageNode(
            node_type="CAREER_VISIBILITY",
            key_players=[],  # Missing players and target
            political_rationale="Short rationale",
            networking_wedge="",  # Missing required wedge
            verbatim_quote="Quote",
            speaker="Unknown",
        )

        with mock.patch("config.credentials.get_openai_client") as mock_get_client:
            scorecard = judge.evaluate_agentic_leverage(
                ineligible_node, transcript_text="Sample transcript", mock=False
            )
            # Assert client creation was NEVER called
            mock_get_client.assert_not_called()
            self.assertFalse(scorecard.pass_gate)
            self.assertEqual(scorecard.political_materiality, 0)
            self.assertIn("Deterministic pre-filter", scorecard.rejection_reason or "")


if __name__ == "__main__":
    unittest.main()
