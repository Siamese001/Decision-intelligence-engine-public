"""
tests/test_pipeline_context_and_checkpointing.py

Tests for strongly-typed PipelineInvocationContext, stage payloads,
and deterministic stage checkpointing in UnifiedAgenticPipeline.
"""

import pytest
import tempfile
from pathlib import Path

from dual_pipeline.runtime.pipeline_context import (
    ContentAddressedDependencyManifest,
    EvalsPayload,
    L1CognitionPayload,
    L2SynthesisPayload,
    PipelineInvocationContext,
    U0IngressPayload,
    XExitGatesPayload,
    compute_stage_manifest,
    invalidate_stage_checkpoint,
    is_checkpoint_valid,
    save_stage_checkpoint,
)
from dual_pipeline.transcript_stages_core.recap_executor import validate_recap_preflight
from dual_pipeline.unified_agentic_pipeline import PipelineStage


def test_pipeline_invocation_context_immutability_and_hash():
    ctx = PipelineInvocationContext(
        pipeline_name="RawTranscriptPipeline",
        input_path="/path/to/meeting.txt",
        project="project_lens",
        meeting="LENS_Status",
        meeting_date="09-18-2026",
        provider_type="offline",
    )
    assert ctx.pipeline_name == "RawTranscriptPipeline"
    assert ctx.meeting_date == "09-18-2026"
    h1 = ctx.context_hash()
    assert len(h1) == 64

    # Identical configuration must produce identical fingerprint
    ctx2 = PipelineInvocationContext(
        pipeline_name="RawTranscriptPipeline",
        input_path="/path/to/meeting.txt",
        project="project_lens",
        meeting="LENS_Status",
        meeting_date="09-18-2026",
        provider_type="offline",
    )
    assert ctx.context_hash() == ctx2.context_hash()


def test_stage_payload_instantiation():
    u0 = U0IngressPayload(
        raw_input="raw audio content",
        transcript_text="transcript text",
        input_hash="abc123hash",
        is_file=False,
    )
    assert u0.input_hash == "abc123hash"
    assert not u0.is_file

    l1 = L1CognitionPayload(
        forensic_transcript="# 01 Forensic Transcript",
        dialogue_text="Amit: Hello world",
        speaker_map={"SPEAKER_00": "Amit Ayer"},
    )
    assert l1.speaker_map["SPEAKER_00"] == "Amit Ayer"

    l2 = L2SynthesisPayload(
        forensic_transcript="# 01 Forensic Transcript",
        executive_recap="# 02 Executive Recap",
        graph_projection_json='{"nodes": []}',
    )
    assert "# 02 Executive Recap" in l2.executive_recap

    x = XExitGatesPayload(
        passed=True,
        qa_report={"qa_passed": True},
        judge_report={"status": "PASS"},
        final_decision={"decision": "ACCEPT"},
    )
    assert x.passed

    ev = EvalsPayload(
        terminal_outcome="COMPLETE",
        overall_status="PASS",
        receipt_digest="sha256:abc",
    )
    assert ev.terminal_outcome == "COMPLETE"


def test_stage_checkpoint_save_and_resume():
    from dual_pipeline.unified_agentic_pipeline import UnifiedAgenticPipelineTracker
    with tempfile.TemporaryDirectory() as tmp_dir:
        checkpoint_dir = Path(tmp_dir) / "checkpoints"
        tracker = UnifiedAgenticPipelineTracker(
            pipeline_name="TestPipeline",
            input_path="dummy.txt",
        )

        test_data = {"test_key": "test_val", "score": 98}
        tracker.save_checkpoint(PipelineStage.U0_INGRESS, test_data, checkpoint_dir)

        assert tracker.can_resume_from_stage(PipelineStage.L1_IDENTITY, checkpoint_dir)
        assert not tracker.can_resume_from_stage(PipelineStage.X_EXIT_GATES, checkpoint_dir)

        loaded = tracker.load_checkpoint(PipelineStage.U0_INGRESS, checkpoint_dir)
        assert loaded is not None
        assert loaded["score"] == 98
        assert loaded["test_key"] == "test_val"

        # Resume to stage
        tracker.resume_to_stage(PipelineStage.L1_IDENTITY, checkpoint_dir)
        assert PipelineStage.U0_INGRESS in tracker.completed_stages


def test_content_addressed_dependency_manifest_fingerprinting():
    meta = {
        "project": "project_lens",
        "meeting": "Scope_Morgan Vance_PartnerAdvisory",
        "meeting_date": "10-05-2026",
        "speaker_map": {"Voice A": "Amit Ayer", "Voice B": "Morgan Vance"},
        "model": "gemini-3.8-flash",
    }
    src_text = "Amit Ayer: Hello everyone, let us begin.\nMorgan Vance: Thanks Amit."

    m1 = compute_stage_manifest(
        stage_name="output_01_forensic_transcript",
        source_text=src_text,
        metadata=meta,
        model_id="gemini-3.8-flash",
    )
    digest1 = m1.manifest_digest()
    assert len(digest1) == 64

    # Identical inputs must yield identical digest
    m2 = compute_stage_manifest(
        stage_name="output_01_forensic_transcript",
        source_text=src_text,
        metadata=meta,
        model_id="gemini-3.8-flash",
    )
    assert m2.manifest_digest() == digest1

    # Mutated speaker map must alter digest
    meta_mutated = dict(meta)
    meta_mutated["speaker_map"] = {"Voice A": "Jake", "Voice B": "Morgan Vance"}
    m_diff_spk = compute_stage_manifest(
        stage_name="output_01_forensic_transcript",
        source_text=src_text,
        metadata=meta_mutated,
        model_id="gemini-3.8-flash",
    )
    assert m_diff_spk.manifest_digest() != digest1

    # Mutated source text must alter digest
    m_diff_src = compute_stage_manifest(
        stage_name="output_01_forensic_transcript",
        source_text=src_text + "\nJake: Added turn.",
        metadata=meta,
        model_id="gemini-3.8-flash",
    )
    assert m_diff_src.manifest_digest() != digest1

    # Mutated model ID must alter digest
    m_diff_model = compute_stage_manifest(
        stage_name="output_01_forensic_transcript",
        source_text=src_text,
        metadata=meta,
        model_id="gemini-3.5-flash-lite",
    )
    assert m_diff_model.manifest_digest() != digest1


def test_checkpoint_manifest_validation_and_invalidation():
    with tempfile.TemporaryDirectory() as tmp_dir:
        ckpt_path = Path(tmp_dir) / "output_1.ckpt.md"
        meta = {
            "project": "project_lens",
            "meeting": "Scope_Morgan Vance_PartnerAdvisory",
            "meeting_date": "10-05-2026",
            "speaker_map": {"Voice A": "Amit Ayer"},
        }
        manifest = compute_stage_manifest(
            stage_name="output_01",
            source_text="Amit Ayer: Dialogue content here.",
            metadata=meta,
            model_id="mock-model",
        )

        # Checkpoint does not exist yet
        assert not is_checkpoint_valid(ckpt_path, manifest)

        # Checkpoint exists but is <= 500 bytes
        ckpt_path.write_text("Short text", encoding="utf-8")
        assert not is_checkpoint_valid(ckpt_path, manifest)

        # Save valid checkpoint with manifest (> 500 bytes)
        content = (
            "# 01 Forensic Transcript\n\n**Amit Ayer**: Valid substantial content.\n"
            + ("padding line\n" * 40)
        )
        assert len(content) > 500
        save_stage_checkpoint(ckpt_path, content, manifest)

        assert ckpt_path.exists()
        manifest_file = ckpt_path.with_suffix(ckpt_path.suffix + ".manifest.json")
        assert manifest_file.exists()

        # Should validate successfully
        assert is_checkpoint_valid(ckpt_path, manifest, strict_manifest=True)

        # Invalidate when expected manifest changes (e.g. source changed)
        different_manifest = compute_stage_manifest(
            stage_name="output_01",
            source_text="Amit Ayer: Different dialogue text.",
            metadata=meta,
            model_id="mock-model",
        )
        assert not is_checkpoint_valid(ckpt_path, different_manifest, strict_manifest=True)

        # Invalidate and check deletion
        invalidate_stage_checkpoint(ckpt_path)
        assert not ckpt_path.exists()
        assert not manifest_file.exists()


def test_validate_recap_preflight():
    valid_transcript = (
        "# 01 Forensic Transcript\n\n"
        "**[00:00:10] Amit Ayer**: We have agreed on the three milestones.\n"
        "**[00:00:25] Morgan Vance**: Confirmed from PartnerAdvisory side.\n"
    )

    # Empty transcript must raise ValueError
    with pytest.raises(ValueError, match="empty"):
        validate_recap_preflight("", {})

    with pytest.raises(ValueError, match="empty"):
        validate_recap_preflight("   \n\t  ", {})

    # Transcript without turn locators must raise ValueError
    with pytest.raises(ValueError, match="no valid dialogue turn locators"):
        validate_recap_preflight("Just some descriptive prose without dialogue turns.", {})

    # Valid transcript must pass silently
    validate_recap_preflight(valid_transcript, {})


def test_concurrent_sibling_recap_and_graph_synthesis():
    from unittest.mock import MagicMock, patch
    from dual_pipeline.canonical_pipeline.strategies.synthesis import (
        FullMeetingSynthesisStrategy,
        NotesMeetingSynthesisStrategy,
    )

    mock_provider = MagicMock()
    mock_l2_forensic = MagicMock()
    mock_l2_recap = MagicMock()
    mock_l2_graph = MagicMock()

    mock_l2_forensic.execute.return_value = "# 01 Forensic\n**Amit**: Hello"
    mock_l2_recap.execute.return_value = "# 02 Recap\nKey takeaways"
    mock_l2_graph.execute.return_value = '{"meeting_id": "test", "graph_mutations": {}}'

    strategy = FullMeetingSynthesisStrategy(
        provider=mock_provider,
        l2_forensic=mock_l2_forensic,
        l2_recap=mock_l2_recap,
        l2_graph=mock_l2_graph,
    )

    metadata = {
        "project": "project_lens",
        "meeting": "Scope_Morgan Vance_PartnerAdvisory",
        "meeting_date": "10-05-2026",
        "audio_authorized": True,
        "circuit_breaker_disabled": True,
        "attendees": ["Amit Ayer", "Morgan Vance"],
    }
    pa_context = {"metadata": metadata}

    with patch("dual_pipeline.audio.circuit_breaker.AudioCompletenessCircuitBreaker.assert_outputs_authorized"):
        with patch.object(strategy, "_resolve_output_1", return_value="[00:00:10] Amit: Hello\n[00:00:15] Morgan Vance: Hi"):
            res = strategy.synthesize(
                source_text="raw text",
                pa_context=pa_context,
                metadata=metadata,
            )

    assert res.output_1 is not None
    assert res.output_2 is not None
    assert res.output_3 is not None
    # Verify that l2_graph was called without requiring executive recap
    assert mock_l2_graph.execute.called
    graph_kwargs = mock_l2_graph.execute.call_args.kwargs
    assert graph_kwargs.get("executive_recap") is None

    # Test NotesMeetingSynthesisStrategy sibling concurrency
    mock_recap_notes = MagicMock()
    mock_graph_notes = MagicMock()
    mock_recap_notes.execute.return_value = "# 02 Notes Recap"
    mock_graph_notes.execute.return_value = '{"meeting_id": "notes", "graph_mutations": {}}'

    notes_strategy = NotesMeetingSynthesisStrategy(provider=mock_provider)
    notes_strategy.l2_recap = mock_recap_notes
    notes_strategy.l2_graph = mock_graph_notes

    res_notes = notes_strategy.synthesize(
        source_text="# Meeting Notes\nSome notes content here.",
        pa_context=pa_context,
        metadata=metadata,
    )
    assert res_notes.output_1 is None
    assert res_notes.output_2 is not None
    assert res_notes.output_3 is not None
    assert mock_graph_notes.execute.called
    notes_graph_kwargs = mock_graph_notes.execute.call_args.kwargs
    assert notes_graph_kwargs.get("executive_recap") is None
