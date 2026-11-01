"""End-to-end and adversarial regression tests for E2E Critical Functionality Assurance repairs."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, strategies as st

from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.canonical_pipeline.transcript_resume import (
    _ACTIVE_TRANSCRIPT_CHECKPOINT,
    record_transcript_failure,
    refresh_validated_transcript_checkpoint,
)
from dual_pipeline.canonical_pipeline.worktree_inheritance import _normalize_worktree_path
from dual_pipeline.checkpoint import PipelineCheckpointManager
from dual_pipeline.cli.ingest import _extract_flag_attributes
from dual_pipeline.live_meeting_recorder_core.recording_service_models import RecordingStartRequest
from dual_pipeline.live_meeting_recorder_core.recording_service_owner import run_service_worker


def test_lazy_imports_resolve_without_error() -> None:
    """Verifies that all repaired lazy-import targets across production modules resolve cleanly."""
    from dual_pipeline.canonical_pipeline.worktree_inheritance import _normalize_worktree_path as norm_fn
    from dual_pipeline.l2_providers_core.google_provider import GoogleProvider, GoogleReasoningProvider
    from dual_pipeline.model_registry import ModelTier, resolve_tier_model
    from dual_pipeline.retrieval_release import RetrievalReleaseEvaluator
    from dual_pipeline.retrieval_suite import AutomaticRetrievalReleaseSuiteBuilder
    from tools.lint_layer_imports import get_layer_for_file

    assert callable(norm_fn)
    assert GoogleProvider is GoogleReasoningProvider
    assert callable(get_layer_for_file)
    assert RetrievalReleaseEvaluator is not None
    assert AutomaticRetrievalReleaseSuiteBuilder is not None
    assert ModelTier.DIALECTICAL_JUDGE is not None
    assert callable(resolve_tier_model)


def test_recording_service_worker_preserves_all_16_ipc_fields(tmp_path: Path) -> None:
    """Verifies run_service_worker hydrates all 16 RecordingStartRequest fields and passes them to LiveMeetingRecorder."""
    req = RecordingStartRequest(
        request_id="req_test1234",
        idempotency_key="idem_999",
        session_id="sess_01",
        host_chat_id="chat_abc",
        project_name="project_nexus",
        meeting_name="Architecture Sync",
        meeting_date="10-25-2026",
        attendees=["Alice", "Bob"],
        audio_source="mixed",
        mock_mode=False,
        device_index=":1",
        segment_seconds=10,
        whisper_model="mlx-community/whisper-large-v3-turbo",
        is_cold_start=True,
        output_dir=str(tmp_path / "custom_out"),
    )
    req_file = tmp_path / "request.json"
    req_file.write_text(json.dumps(req.to_dict()), encoding="utf-8")

    mock_owner = MagicMock()
    mock_owner.acquire_session_owner.return_value = (MagicMock(), True)

    with (
        patch(
            "dual_pipeline.live_meeting_recorder_core.recording_service_owner.get_recording_service_owner",
            return_value=mock_owner,
        ),
        patch("dual_pipeline.live_meeting_recorder.LiveMeetingRecorder", autospec=True) as mock_recorder_cls,
    ):
        run_service_worker("sess_01", str(req_file), str(tmp_path / "sessions"))

    hydrated_req: RecordingStartRequest = mock_owner.acquire_session_owner.call_args[0][0]
    for field in dataclasses.fields(RecordingStartRequest):
        if field.name == "output_dir":
            assert str(getattr(hydrated_req, field.name)) == str(getattr(req, field.name))
        else:
            assert getattr(hydrated_req, field.name) == getattr(req, field.name)

    mock_recorder_cls.assert_called_once()
    call_kwargs = mock_recorder_cls.call_args.kwargs
    assert call_kwargs["meeting_date"] == "10-25-2026"
    assert call_kwargs["whisper_model"] == "mlx-community/whisper-large-v3-turbo"
    assert call_kwargs["output_dir"] == tmp_path / "custom_out"
    assert call_kwargs["session_id"] == "sess_01"
    assert call_kwargs["host_chat_id"] == "chat_abc"
    assert call_kwargs["idempotency_key"] == "idem_999"
    assert call_kwargs["is_cold_start"] is True


def test_transcript_checkpoint_invalidated_on_x_exit_gate_failure_and_refreshed_on_pass(
    tmp_path: Path,
) -> None:
    """Verifies X_EXIT_GATES failure invalidates VALIDATED_SYNTHESIS while pass refreshes healed artifacts."""
    ckpt_path = tmp_path / "transcript_ckpt.json"
    ckpt = PipelineCheckpointManager(ckpt_path)
    syn = SynthesisResult(output_1="t1", output_2="pre-heal recap", output_3='{"nodes": []}')
    ev = QAEvaluationResult(
        passed=True,
        qa_report={},
        judge_report={},
        authoritative_write_authorized=True,
        final_decision={"status": "PASS"},
    )
    ckpt.save_stage(
        "VALIDATED_SYNTHESIS",
        {
            "attempt": 1,
            "synthesis": dataclasses.asdict(syn),
            "evaluation": dataclasses.asdict(ev),
            "run_id": "run_1",
            "binding_sha256": "abc123",
        },
    )
    _ACTIVE_TRANSCRIPT_CHECKPOINT.set(ckpt)

    # 1. Post-X-gate refresh updates healed synthesis
    healed_syn = SynthesisResult(output_1="t1", output_2="healed recap", output_3='{"nodes": ["n1"]}')
    refresh_validated_transcript_checkpoint(healed_syn, ev, 1, {"run_id": "run_1"})
    saved = ckpt.get_stage("VALIDATED_SYNTHESIS")
    assert saved is not None
    assert saved["synthesis"]["output_2"] == "healed recap"
    assert saved["exit_gates_verified"] is True

    # 2. X_EXIT_GATES failure invalidates VALIDATED_SYNTHESIS so --resume cannot replay broken state
    record_transcript_failure({"run_id": "run_1"}, "X_EXIT_GATES", RuntimeError("X3 quote mismatch"))
    reloaded = PipelineCheckpointManager(ckpt_path)
    assert not reloaded.has_stage("VALIDATED_SYNTHESIS")
    last_fail = reloaded.get_stage("LAST_FAILURE")
    assert last_fail is not None
    assert last_fail["reason"] == "X_EXIT_GATES_FAILED_SYNTHESIS_INVALIDATED"


@given(
    batch_queue=st.booleans(),
    subpath=st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Nd"), whitelist_characters="_-"),
        min_size=1,
        max_size=20,
    ),
)
@settings(max_examples=15, deadline=None)
def test_property_cli_batch_queue_and_worktree_path_normalization(
    batch_queue: bool, subpath: str
) -> None:
    """Property check for CLI --batch-queue flag wiring and worktree path normalization."""
    import argparse
    import tempfile

    ns = argparse.Namespace(batch_queue=batch_queue, project="project_nexus")
    meta: dict = {}
    _extract_flag_attributes(ns, meta)
    assert meta["batch_queue"] is batch_queue
    assert meta["project"] == "project_nexus"

    with tempfile.TemporaryDirectory() as td:
        tmp_root = Path(td)
        wt_dir = tmp_root / ".worktrees" / subpath
        wt_dir.mkdir(parents=True, exist_ok=True)
        resolved = _normalize_worktree_path(Path(".worktrees") / subpath, tmp_root)
        assert resolved == wt_dir.resolve()


@given(
    copilot_prefetch=st.booleans(),
    instant=st.booleans(),
    probation_ms=st.integers(min_value=100, max_value=5000),
)
@settings(max_examples=10, deadline=None)
def test_property_live_recorder_preserves_prefetch_instant_probation_and_lock_session_id(
    copilot_prefetch: bool, instant: bool, probation_ms: int
) -> None:
    """F-E2E-01 property check: LiveMeetingRecorder honors copilot_prefetch, instant, probation_ms, and non-empty lock session_id."""
    import tempfile
    from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        lock_calls: list[tuple] = []

        def _fake_lock(slug: str, proj: str | None, sess_id: str):
            lock_calls.append((slug, proj, sess_id))
            m = MagicMock()
            return m

        with (
            patch("dual_pipeline.live_meeting_recorder.acquire_global_mlx_lock", side_effect=_fake_lock),
            patch("dual_pipeline.live_meeting_recorder.ensure_ffmpeg"),
            patch("dual_pipeline.live_meeting_recorder.LiveAudioCaptureManager"),
        ):
            rec = LiveMeetingRecorder(
                project_name=None,
                meeting_name=None,
                output_dir=tmp_dir / "out",
                mock_mode=True,
                copilot_prefetch=copilot_prefetch,
                instant=instant,
                probation_ms=probation_ms,
                session_id="sess_prop_123",
                enforce_worktree=False,
                dry_run=True,
            )
            try:
                assert rec.enable_copilot_prefetch is copilot_prefetch
                assert rec.instant is instant
                assert rec.probation_ms == probation_ms
                assert rec.session_id == "sess_prop_123"
                assert len(lock_calls) == 1
                assert lock_calls[0][2] == "sess_prop_123"
            finally:
                if getattr(rec, "_slug_lock_fd", None):
                    rec._slug_lock_fd.close()
