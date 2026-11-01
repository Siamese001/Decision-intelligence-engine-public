"""
Unit tests for the UnifiedAgenticPipeline kernel, stage sequencing enforcement,
and execution receipt recording.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dual_pipeline.transcript_pipeline_core.pipeline import RawTranscriptPipeline
from dual_pipeline.unified_agentic_pipeline import (
    PIPELINE_STAGE_SEQUENCE,
    PipelineSequencingViolationError,
    PipelineStage,
    UnifiedAgenticPipeline,
    UnifiedAgenticPipelineTracker,
)


def test_stage_sequence_completeness_and_order():
    """Verify that PIPELINE_STAGE_SEQUENCE contains all 7 canonical stages in strict order."""
    expected = [
        PipelineStage.U0_INGRESS,
        PipelineStage.L1_IDENTITY,
        PipelineStage.L0_ROUTING,
        PipelineStage.C0_PA_CONTEXT,
        PipelineStage.L2_SYNTHESIS,
        PipelineStage.X_EXIT_GATES,
        PipelineStage.EVALS_RELEASE,
    ]
    assert expected == PIPELINE_STAGE_SEQUENCE
    assert len(PIPELINE_STAGE_SEQUENCE) == 7


def test_monotonic_progression_succeeds(tmp_path: Path):
    """Verify that executing all 7 stages in order succeeds and builds a complete receipt."""
    tracker = UnifiedAgenticPipelineTracker(
        pipeline_name="TestPipeline",
        input_path="dummy.txt",
        metadata={"project": "test_project"},
    )

    for stage in PIPELINE_STAGE_SEQUENCE:
        tracker.start_stage(stage)
        tracker.complete_stage(stage, status="PASS", details={"note": f"completed {stage.value}"})

    receipt = tracker.build_receipt(tmp_path, terminal_outcome="COMPLETE")
    assert receipt.terminal_outcome == "COMPLETE"
    assert len(receipt.stages) == 7
    for stage in PIPELINE_STAGE_SEQUENCE:
        assert stage.value in receipt.stages
        assert receipt.stages[stage.value]["status"] == "PASS"

    receipt_file = receipt.write_to_dir(tmp_path)
    assert receipt_file.exists()
    saved = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert saved["pipeline_name"] == "TestPipeline"
    assert saved["terminal_outcome"] == "COMPLETE"
    assert len(saved["stages"]) == 7


def test_skipping_stage_raises_sequencing_violation():
    """Verify that attempting to jump over a stage raises PipelineSequencingViolationError."""
    tracker = UnifiedAgenticPipelineTracker(
        pipeline_name="TestPipeline",
        input_path="dummy.txt",
    )

    tracker.start_stage(PipelineStage.U0_INGRESS)
    tracker.complete_stage(PipelineStage.U0_INGRESS)

    # Attempt to skip L1 and jump straight to L0
    with pytest.raises(PipelineSequencingViolationError) as exc_info:
        tracker.start_stage(PipelineStage.L0_ROUTING)
    assert "Sequencing violation" in str(exc_info.value)
    assert "Expected next stage: 'L1'" in str(exc_info.value)


def test_completing_unstarted_stage_raises_error():
    """Verify that completing a stage that was not started raises an error."""
    tracker = UnifiedAgenticPipelineTracker(
        pipeline_name="TestPipeline",
        input_path="dummy.txt",
    )

    with pytest.raises(PipelineSequencingViolationError):
        tracker.complete_stage(PipelineStage.U0_INGRESS)


def test_raw_transcript_pipeline_conforms_to_unified_kernel():
    """Verify that RawTranscriptPipeline is a subclass of UnifiedAgenticPipeline."""
    assert issubclass(RawTranscriptPipeline, UnifiedAgenticPipeline)
    pipeline = RawTranscriptPipeline(provider_type="offline")
    assert pipeline.pipeline_name == "RawTranscriptPipeline"
    tracker = pipeline.create_tracker("sample.md")
    assert tracker.pipeline_name == "RawTranscriptPipeline"


def test_remediation_cycle_rolls_back_cleanly(tmp_path: Path):
    """Verify that an exit gate failure triggers remediation cycle rollback to L2."""
    tracker = UnifiedAgenticPipelineTracker(
        pipeline_name="TestPipeline",
        input_path="sample.md",
    )

    # Progress through U0 -> L1 -> L0 -> C0-PA -> L2
    stages_to_l2 = [
        PipelineStage.U0_INGRESS,
        PipelineStage.L1_IDENTITY,
        PipelineStage.L0_ROUTING,
        PipelineStage.C0_PA_CONTEXT,
        PipelineStage.L2_SYNTHESIS,
    ]
    for s in stages_to_l2:
        tracker.start_stage(s)
        tracker.complete_stage(s)

    # Start X1-X3 exit gates
    tracker.start_stage(PipelineStage.X_EXIT_GATES)

    # Simulate adversarial QA finding requiring remediation in L2
    tracker.request_remediation(
        from_stage=PipelineStage.X_EXIT_GATES,
        to_stage=PipelineStage.L2_SYNTHESIS,
        reason="QA Gate 2 word count deficiency",
        details={"gate": 2, "deficit": 40},
    )

    # Now tracker should allow starting L2_SYNTHESIS again
    tracker.start_stage(PipelineStage.L2_SYNTHESIS)
    tracker.complete_stage(PipelineStage.L2_SYNTHESIS, details={"attempt": 2})

    # Now proceed to X1-X3 again
    tracker.start_stage(PipelineStage.X_EXIT_GATES)
    tracker.complete_stage(PipelineStage.X_EXIT_GATES, status="PASS")

    # Proceed to EVALS
    tracker.start_stage(PipelineStage.EVALS_RELEASE)
    tracker.complete_stage(PipelineStage.EVALS_RELEASE, status="PASS")

    receipt = tracker.build_receipt(tmp_path)
    assert receipt.terminal_outcome == "COMPLETE"
    assert len(receipt.remediation_cycles) == 1
    assert receipt.remediation_cycles[0]["details"]["remediation_target"] == "L2"
    assert receipt.remediation_cycles[0]["started_at"] != ""
    assert receipt.remediation_cycles[0]["ended_at"] != ""


def test_stage_timing_and_session_ids_captured(tmp_path: Path):
    """Verify that stage true wall-clock start/end, monotonic duration, and session IDs are recorded."""
    tracker = UnifiedAgenticPipelineTracker(
        pipeline_name="Morgan VancePipeline",
        input_path="Scope_Morgan Vance_PartnerAdvisory.md",
        session_id="session-karyn-1005",
        attempt_id="attempt-01",
        metadata={"project": "project_lens"},
    )
    assert tracker.session_id == "session-karyn-1005"
    assert tracker.attempt_id == "attempt-01"

    for stage in PIPELINE_STAGE_SEQUENCE:
        tracker.start_stage(stage)
        tracker.complete_stage(stage, status="PASS")

    receipt = tracker.build_receipt(tmp_path, terminal_outcome="PASS")
    assert receipt.session_id == "session-karyn-1005"
    assert receipt.attempt_id == "attempt-01"
    assert receipt.started_at != ""
    assert receipt.ended_at != ""
    assert receipt.monotonic_duration_s >= 0.0

    for stg in PIPELINE_STAGE_SEQUENCE:
        record = receipt.stages[stg.value]
        assert record["session_id"] == "session-karyn-1005"
        assert record["attempt_id"] == "attempt-01"
        assert record["started_at"] != ""
        assert record["ended_at"] != ""
        assert record["monotonic_duration_s"] >= 0.0

    # Test file round-trip serialization
    receipt_path = receipt.write_to_dir(tmp_path)
    loaded = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert loaded["session_id"] == "session-karyn-1005"
    assert loaded["attempt_id"] == "attempt-01"
    assert "timing_breakdown" in loaded


def test_timing_breakdown_categories_isolated(tmp_path: Path):
    """Verify that provider, queue, operator wait, retries, and graph stages are measured separately."""
    tracker = UnifiedAgenticPipelineTracker(
        pipeline_name="MeasurementPipeline",
        input_path="sample.md",
    )

    tracker.record_timing_span("provider_wait_s", 1.25)
    tracker.record_timing_span("queue_wait_s", 0.5)
    tracker.record_timing_span("operator_wait_s", 15.0)
    tracker.record_timing_span("retry_overhead_s", 2.5)
    tracker.record_timing_span("graph_operations_s", 0.75)

    for stage in PIPELINE_STAGE_SEQUENCE:
        tracker.start_stage(stage)
        tracker.complete_stage(stage, status="PASS")

    receipt = tracker.build_receipt(tmp_path, terminal_outcome="PASS")
    breakdown = receipt.timing_breakdown
    assert breakdown["provider_wait_s"] == 1.25
    assert breakdown["queue_wait_s"] == 0.5
    assert breakdown["operator_wait_s"] == 15.0
    assert breakdown["retry_overhead_s"] == 2.5
    assert breakdown["graph_operations_s"] == 0.75
    assert "active_compute_s" in breakdown
