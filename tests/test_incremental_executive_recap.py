"""tests/test_incremental_executive_recap.py

Proves Feature 2 (Part A):
When re-running with new/updated inputs, Output 02 (Executive Recap) is synchronized:
- Retains previously established context, decisions, and action items.
- Appends newly introduced discussion points, decisions, and next steps as needed.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.transcript_pipeline_core.pipeline import RawTranscriptPipeline


@pytest.fixture
def mock_pipeline_components():
    with patch("dual_pipeline.transcript_stages_core.l1_cognition.L1ForensicCognition.plan") as mock_l1, \
         patch("dual_pipeline.transcript_stages_core.l2_executors.L2ForensicNormalizerExecutor.execute") as mock_l2, \
         patch("dual_pipeline.transcript_stages_core.x_gates.XExitGatesEvaluator.execute") as mock_xgates, \
         patch("dual_pipeline.transcript_pipeline_core.pipeline.evaluate_judge_and_release") as mock_judge, \
         patch("dual_pipeline.transcript_pipeline_core.post_l2_review.TranscriptL1PostExecutionReviewer.review_execution") as mock_rev, \
         patch("tools.prepare_evidence_envelope_intake.prepare_package") as mock_prepare:

        mock_l1.return_value = MagicMock(plan_id="plan-p2", source_type="mock", diarization_status="NATIVE")
        mock_l2.return_value = "# Meeting -- HITL-Resolved Transcript\n\nSpeaker 1: Text."
        mock_rev.return_value = MagicMock(to_dict=lambda: {"status": "PASS"})
        mock_xgates.return_value = True

        def mock_prepare_side_effect(*args, **kwargs):
            out = kwargs.get("output_path")
            if out:
                Path(out).write_text(json.dumps({"contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0"}))
            return {}

        mock_prepare.side_effect = mock_prepare_side_effect

        def mock_judge_side_effect(*args, **kwargs):
            return (
                {"status": "PASS", "passed": True},
                {"status": "PASS"},
                {
                    "status": "PASS",
                    "authoritative_write_authorized": True,
                    "output_1_pass": True,
                    "output_2_pass": True,
                    "output_3_pass": True,
                    "reasoning": "Passing release.",
                },
            )

        mock_judge.side_effect = mock_judge_side_effect

        yield {
            "mock_l1": mock_l1,
            "mock_l2": mock_l2,
            "mock_judge": mock_judge,
        }


def test_incremental_input_updates_output_2_recap(tmp_path, mock_pipeline_components):
    """Verifies that an updated transcript seamlessly updates Output 02 next steps and decisions."""
    output_dir = tmp_path / "project_nexus" / "09-16-2026"

    metadata = {
        "meeting_name": "Incremental Nexus Review",
        "date": "09-16-2026",
        "time": "02:00 PM",
        "project_name": "project_nexus",
    }

    pipeline = RawTranscriptPipeline()

    # --- RUN 1: Initial Transcript ---
    transcript_v1 = tmp_path / "transcript_v1.md"
    transcript_v1.write_text("Speaker 1: Phase 1 charter finalized.", encoding="utf-8")

    res1 = pipeline.run(
        input_path=str(transcript_v1),
        metadata=metadata.copy(),
        output_dir=str(output_dir),
        max_judge_retries=0,
    )

    recap_v1_path = Path(res1["02_recap"])
    assert recap_v1_path.exists()
    recap_v1_text = recap_v1_path.read_text(encoding="utf-8")
    assert "Finalize delivery charter" in recap_v1_text
    assert "Implement incremental operational additions" not in recap_v1_text

    # --- RUN 2: Updated Transcript with Incremental Content ---
    transcript_v2 = tmp_path / "transcript_v2.md"
    transcript_v2.write_text(
        "Speaker 1: Phase 1 charter finalized.\n"
        "Speaker 2: We need to also add incremental operational additions for October 15th.",
        encoding="utf-8",
    )

    res2 = pipeline.run(
        input_path=str(transcript_v2),
        metadata=metadata.copy(),
        output_dir=str(output_dir),
        max_judge_retries=0,
    )

    recap_v2_path = Path(res2["02_recap"])
    assert recap_v2_path.exists()
    recap_v2_text = recap_v2_path.read_text(encoding="utf-8")

    # Output 02 must preserve prior item AND include the new incremental action item!
    assert "Finalize delivery charter" in recap_v2_text
    assert "Implement incremental operational additions" in recap_v2_text
