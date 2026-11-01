import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.transcript_pipeline_core.pipeline import RawTranscriptPipeline

# Setup basic logging for tests
logging.basicConfig(level=logging.DEBUG)


def mock_l1_plan(context):
    return MagicMock()

def mock_l2_execute(plan):
    # This shouldn't be called on the second run
    return MagicMock()

def mock_x_evaluate(artifacts):
    return MagicMock()


@pytest.fixture
def run_idempotent_pipeline(tmp_path):
    output_dir = tmp_path / "project_nexus" / "09-15-2026"

    # We will use the standard RawTranscriptPipeline but mock the LLM calls to prevent actual API calls
    with patch("dual_pipeline.transcript_stages_core.l1_cognition.L1ForensicCognition.plan") as mock_l1, \
         patch("dual_pipeline.transcript_stages_core.l2_executors.L2ForensicNormalizerExecutor.execute") as mock_l2, \
         patch("dual_pipeline.transcript_stages_core.l2_executors.L2ExecutiveRecapExecutor.execute") as mock_l3, \
         patch("dual_pipeline.transcript_stages_core.l2_executors.L2GraphExtractorExecutor.execute") as mock_l4, \
         patch("dual_pipeline.transcript_pipeline_core.post_l2_review.TranscriptL1PostExecutionReviewer.review_execution") as mock_reviewer, \
        patch("dual_pipeline.transcript_pipeline_core.qa_runner.evaluate_judge_and_release") as mock_judge, \
        patch("dual_pipeline.canonical_pipeline.attempt_binding.verify_attempt_binding") as mock_binding, \
        patch("dual_pipeline.transcript_stages_core.x_gates.XExitGatesEvaluator.execute") as mock_xgates, \
        patch("dual_pipeline.transcript_pipeline_core.release_runner.prepare_package") as mock_prepare:

        # Configure mocks for first run
        mock_l1.return_value = MagicMock(plan_id="mock_id", source_type="mock", diarization_status="NATIVE")
        mock_l2.return_value = "t"
        mock_l3.return_value = "r"
        mock_l4.return_value = "{}"
        mock_reviewer.return_value = MagicMock()
        mock_reviewer.return_value.to_dict.return_value = {}
        mock_xgates.return_value = True

        def mock_prepare_side_effect(*args, **kwargs):
            out = kwargs.get("output_path")
            payload = {"status": "READY", "package_id": "pkg_intake_001", "stage": "INTAKE"}
            if out:
                Path(out).write_text(json.dumps(payload), encoding="utf-8")
            return payload

        mock_prepare.side_effect = mock_prepare_side_effect

        import hashlib
        input_text = "[10:15] Amit Ayer: Hello, this is a test transcript for idempotency.\n[10:16] Reid Schenk: Confirmed, proceeding with test."
        expected_hash = hashlib.sha256(input_text.encode("utf-8")).hexdigest()

        # Mock judge to pass and provide a valid decision payload
        mock_judge.return_value = (
            {"status": "PASS", "passed": True},  # qa_report
            {"status": "PASS"},  # judge_report
            {                    # final_decision
                "status": "PASS",
                "authoritative_write_authorized": True,
                "output_1_pass": True,
                "output_2_pass": True,
                "output_3_pass": True,
                "reasoning": "All looks good.",
                "input_hash": expected_hash,
            }
        )

        pipeline = RawTranscriptPipeline(provider_type="offline")

        input_file = tmp_path / "test_input.md"
        input_file.write_text(input_text, encoding="utf-8")
        metadata = {
            "meeting_name": "Idempotency Test",
            "date": "2026-09-15",
            "time": "14:00:00",
            "project_name": "project_nexus",
            "attendees": "Amit Ayer, Reid Schenk",
            "output_dir_override": str(output_dir),
            "reviewer": "Amit Ayer",
            "package_only": True,
        }

        # --- FIRST RUN ---
        result_1 = pipeline.run(
            input_path=str(input_file),
            metadata=metadata.copy(),
            output_dir=str(output_dir),
            max_judge_retries=0,
            reviewer="Amit Ayer",
        )

        assert Path(result_1["final_release_decision"]).exists()
        decision_data = json.loads(Path(result_1["final_release_decision"]).read_text())
        assert decision_data["status"] == "PASS"
        assert "input_hash" in decision_data

        first_run_l1_calls = mock_l1.call_count
        first_run_judge_calls = mock_judge.call_count
        assert first_run_l1_calls == 1
        assert first_run_judge_calls == 1

        # --- SECOND RUN ---
        # The second run should return early without calling L1 or judge
        result_2 = pipeline.run(
            input_path=str(input_file),
            metadata=metadata.copy(),
            output_dir=str(output_dir),
            max_judge_retries=0,
            reviewer="Amit Ayer",
        )

        assert mock_l1.call_count == first_run_l1_calls, "L1 was called on the second run!"
        assert mock_judge.call_count == first_run_judge_calls, "Judge was called on the second run!"

        return result_1, result_2


def test_idempotency_gate(run_idempotent_pipeline):
    result_1, result_2 = run_idempotent_pipeline
    assert result_1["01_transcript"] == result_2["01_transcript"]
    assert result_1["02_recap"] == result_2["02_recap"]
    assert result_1["03_graph"] == result_2["03_graph"]
    assert result_1["final_release_decision"] == result_2["final_release_decision"]
