"""tests/test_smart_audio_and_transcript_caching.py

Proves Feature 1:
Intelligently stops re-runs from spending tokens on 100% identical inputs:
1. Identical transcript files skip execution (0 L1/L2/Judge calls, 0 tokens).
2. Identical audio files skip execution without invoking transcriber or LLMs.
3. Audio transcript caching in output/transcript_cache/ reuses cached transcripts across runs.
"""
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.transcript_pipeline_core.caching import (
    cache_audio_transcript,
    compute_source_file_hash,
    get_cached_audio_transcript,
)
from dual_pipeline.transcript_pipeline_core.pipeline import RawTranscriptPipeline

logger = logging.getLogger(__name__)


@pytest.fixture
def mock_pipeline_environment(tmp_path):
    """Provides a mocked offline environment for pipeline runs."""
    with patch("dual_pipeline.transcript_stages_core.l1_cognition.L1ForensicCognition.plan") as mock_l1, \
         patch("dual_pipeline.transcript_stages_core.l2_executors.L2ForensicNormalizerExecutor.execute") as mock_l2, \
         patch("dual_pipeline.transcript_stages_core.l2_executors.L2ExecutiveRecapExecutor.execute") as mock_l3, \
         patch("dual_pipeline.transcript_stages_core.l2_executors.L2GraphExtractorExecutor.execute") as mock_l4, \
         patch("dual_pipeline.transcript_pipeline_core.post_l2_review.TranscriptL1PostExecutionReviewer.review_execution") as mock_rev, \
         patch("dual_pipeline.transcript_pipeline_core.pipeline.evaluate_judge_and_release") as mock_judge, \
         patch("dual_pipeline.transcript_stages_core.x_gates.XExitGatesEvaluator.execute") as mock_xgates, \
         patch("tools.prepare_evidence_envelope_intake.prepare_package") as mock_prepare:

        mock_l1.return_value = MagicMock(plan_id="plan-1", source_type="mock", diarization_status="NATIVE")
        mock_l2.return_value = "Forensic Transcript Text"
        mock_l3.return_value = "Executive Recap Text"
        mock_l4.return_value = json.dumps({
            "schema_version": "5.7",
            "evidence_index": {},
            "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
        })
        mock_rev.return_value = MagicMock(to_dict=MagicMock(return_value={}))
        mock_xgates.return_value = True

        def mock_prepare_side_effect(*args, **kwargs):
            out = kwargs.get("output_path")
            if out:
                Path(out).write_text(json.dumps({"contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0"}))
            return {}

        mock_prepare.side_effect = mock_prepare_side_effect

        mock_judge.return_value = (
            {"status": "PASS", "passed": True},
            {"status": "PASS"},
            {
                "status": "PASS",
                "authoritative_write_authorized": True,
                "output_1_pass": True,
                "output_2_pass": True,
                "output_3_pass": True,
                "reasoning": "Passing idempotent release.",
            },
        )

        yield {
            "mock_l1": mock_l1,
            "mock_l2": mock_l2,
            "mock_l3": mock_l3,
            "mock_l4": mock_l4,
            "mock_judge": mock_judge,
        }


def test_identical_transcript_skips_rerun(tmp_path, mock_pipeline_environment):
    """Test Case 1: Running identical transcript does not spend tokens or call L1/Judge on rerun."""
    output_dir = tmp_path / "project_nexus" / "09-16-2026"
    input_file = tmp_path / "meeting_notes.md"
    input_file.write_text("Speaker 1: Project Atlas timeline confirmed for Q4.", encoding="utf-8")

    metadata = {
        "meeting_name": "Atlas Review",
        "date": "09-16-2026",
        "time": "10:00 AM",
        "project_name": "project_nexus",
        "roster": ["Alice", "Bob"],
    }

    pipeline = RawTranscriptPipeline()

    # --- FIRST RUN ---
    res1 = pipeline.run(
        input_path=str(input_file),
        metadata=metadata.copy(),
        output_dir=str(output_dir),
        max_judge_retries=0,
    )

    assert Path(res1["final_release_decision"]).exists()
    assert mock_pipeline_environment["mock_l1"].call_count == 1
    assert mock_pipeline_environment["mock_judge"].call_count == 1

    decision_data = json.loads(Path(res1["final_release_decision"]).read_text(encoding="utf-8"))
    assert decision_data["status"] == "PASS"
    assert "input_hash" in decision_data
    assert "source_file_hash" in decision_data
    assert decision_data["source_file_hash"] == compute_source_file_hash(input_file)

    # --- SECOND RUN (Identical Input) ---
    res2 = pipeline.run(
        input_path=str(input_file),
        metadata=metadata.copy(),
        output_dir=str(output_dir),
        max_judge_retries=0,
    )

    # Zero additional calls to L1 and Judge on second run
    assert mock_pipeline_environment["mock_l1"].call_count == 1
    assert mock_pipeline_environment["mock_judge"].call_count == 1

    # Exact same artifacts returned
    assert res1["01_transcript"] == res2["01_transcript"]
    assert res1["02_recap"] == res2["02_recap"]
    assert res1["03_graph"] == res2["03_graph"]
    assert res1["final_release_decision"] == res2["final_release_decision"]


def test_identical_audio_file_skips_transcription_and_rerun(tmp_path, mock_pipeline_environment):
    """Test Case 2: Running identical audio file skips transcription and LLM calls on rerun."""
    output_dir = tmp_path / "project_fpl" / "09-16-2026"
    audio_file = tmp_path / "recording.wav"
    audio_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00")

    metadata = {
        "meeting_name": "APEX Substation Sync",
        "date": "09-16-2026",
        "time": "11:00 AM",
        "project_name": "project_fpl",
        "roster": ["Alice", "Bob"],
        "transcript_cache_dir": str(tmp_path / "cache"),
    }

    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = "Speaker A: Substation feeder automated."

    pipeline = RawTranscriptPipeline()

    with patch("dual_pipeline.transcript_pipeline_core.pipeline.get_default_transcriber", return_value=mock_transcriber):
        # --- FIRST RUN ---
        res1 = pipeline.run(
            input_path=str(audio_file),
            metadata=metadata.copy(),
            output_dir=str(output_dir),
            max_judge_retries=0,
        )

        assert mock_transcriber.transcribe.call_count == 1
        assert mock_pipeline_environment["mock_l1"].call_count == 1
        assert Path(res1["final_release_decision"]).exists()

        decision = json.loads(Path(res1["final_release_decision"]).read_text(encoding="utf-8"))
        assert decision["source_file_hash"] == compute_source_file_hash(audio_file)

        # --- SECOND RUN (Identical Audio File) ---
        res2 = pipeline.run(
            input_path=str(audio_file),
            metadata=metadata.copy(),
            output_dir=str(output_dir),
            max_judge_retries=0,
        )

        # Transcriber was NOT called again (0 additional calls)
        assert mock_transcriber.transcribe.call_count == 1
        # L1 and Judge were NOT called again (0 additional calls)
        assert mock_pipeline_environment["mock_l1"].call_count == 1
        assert mock_pipeline_environment["mock_judge"].call_count == 1

        assert res1["01_transcript"] == res2["01_transcript"]


def test_audio_transcript_cache_hit_across_different_outputs(tmp_path):
    """Test Case 3: Audio transcription caching reuses cached transcript without re-transcribing."""
    audio_file = tmp_path / "sample.mp3"
    audio_file.write_bytes(b"FAKE_MP3_AUDIO_BYTES_FOR_HASHING_TEST_12345")
    cache_dir = tmp_path / "cache"

    sha = compute_source_file_hash(audio_file)
    assert sha is not None

    # Before caching
    cached_pre = get_cached_audio_transcript(audio_file, cache_dir=cache_dir)
    assert cached_pre is None

    # Write cache
    expected_transcript = "Speaker 1: Reviewing transformer telemetry."
    saved_path = cache_audio_transcript(audio_file, expected_transcript, cache_dir=cache_dir)
    assert saved_path is not None
    assert saved_path.exists()

    # After caching: retrieval returns cached content
    cached_post = get_cached_audio_transcript(audio_file, cache_dir=cache_dir)
    assert cached_post == expected_transcript
