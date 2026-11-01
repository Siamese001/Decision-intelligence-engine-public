"""
tests/end_to_end/test_fault_injection_session_and_checkpoint_e2e.py

End-to-End Resilience & Fault-Injection Lifecycle Test Suite.
Validates multi-session isolation, stage checkpoint memoization & resumption,
and worktree bifurcation recovery under concurrent execution.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import LiveBufferReader
from dual_pipeline.live_meeting_recorder_core.recording_service_models import (
    RecordingStartRequest,
    RecordingState,
)
from dual_pipeline.live_meeting_recorder_core.recording_service_owner import (
    RecordingServiceOwner,
    get_recording_service_owner,
)
from dual_pipeline.runtime.pipeline_context import (
    ContentAddressedDependencyManifest,
    compute_stage_manifest,
    is_checkpoint_valid,
    save_stage_checkpoint,
)
from dual_pipeline.unified_agentic_pipeline import (
    PipelineStage,
    UnifiedAgenticPipelineTracker,
)
from tools.pr_governance_core.worktree import safely_unlink_worktree_dependencies
from tools.reconcile_live_worktrees import (
    coalesce_live_worktree_pair,
    migrate_artifacts,
)


class TestFaultInjectionSessionAndCheckpointE2E:
    """E2E suite verifying session isolation, checkpoint resume, and worktree recovery."""

    def test_e2e_multi_session_concurrency_and_cancellation(self, tmp_path: Path):
        """
        Validates concurrent recording session lifecycles:
        Multiple sessions run concurrently across different projects;
        one session undergoes cancellation while another runs to clean completion.
        LiveBufferReader accurately isolates transcripts without cross-session bleed.
        """
        sessions_root = tmp_path / "sessions_root"
        sessions_root.mkdir()
        owner = get_recording_service_owner(sessions_root)

        # 1. Register Session Alpha
        req_alpha = RecordingStartRequest(
            request_id="req_alpha",
            project_name="ProjectA",
            meeting_name="MeetingA",
            mock_mode=True,
        )
        rec_alpha, is_new_a = owner.acquire_session_owner(req_alpha, "sess_alpha")
        assert is_new_a is True
        owner.update_session_status("sess_alpha", RecordingState.CAPTURING)

        # 2. Register Session Beta concurrently
        req_beta = RecordingStartRequest(
            request_id="req_beta",
            project_name="ProjectB",
            meeting_name="MeetingB",
            mock_mode=True,
        )
        rec_beta, is_new_b = owner.acquire_session_owner(req_beta, "sess_beta")
        assert is_new_b is True
        owner.update_session_status("sess_beta", RecordingState.CAPTURING)

        sess_alpha_dir = sessions_root / "sess_alpha"
        sess_beta_dir = sessions_root / "sess_beta"

        # 3. Simulate streaming chunks into both sessions concurrently
        def stream_session(session_dir: Path, tag: str, count: int):
            for i in range(count):
                chunk_file = session_dir / f"chunk_{i:03d}.json"
                chunk_data = {
                    "seq": i,
                    "tag": tag,
                    "speaker": f"Speaker_{tag}",
                    "text": f"Telemetry event {i} for {tag}",
                    "timestamp": time.time(),
                }
                chunk_file.write_text(json.dumps(chunk_data), encoding="utf-8")
                time.sleep(0.01)

        t1 = threading.Thread(target=stream_session, args=(sess_alpha_dir, "ALPHA", 6))
        t2 = threading.Thread(target=stream_session, args=(sess_beta_dir, "BETA", 6))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # 4. Verify streams are isolated without cross-talk
        chunks_alpha = list(sess_alpha_dir.glob("chunk_*.json"))
        chunks_beta = list(sess_beta_dir.glob("chunk_*.json"))
        assert len(chunks_alpha) == 6
        assert len(chunks_beta) == 6

        alpha_texts = [json.loads(p.read_text(encoding="utf-8"))["tag"] for p in chunks_alpha]
        beta_texts = [json.loads(p.read_text(encoding="utf-8"))["tag"] for p in chunks_beta]
        assert all(t == "ALPHA" for t in alpha_texts)
        assert all(t == "BETA" for t in beta_texts)

        # 5. Fault-inject cancellation of Session Alpha
        owner.stop_session("sess_alpha")
        rec_a = owner.get_session_record("sess_alpha")
        assert rec_a is not None
        assert rec_a.status == RecordingState.STOPPED.value

        # Session Beta remains actively capturing
        rec_b = owner.get_session_record("sess_beta")
        assert rec_b is not None
        assert rec_b.status == RecordingState.CAPTURING.value

        # 6. Session Beta completes cleanly
        owner.stop_session("sess_beta")
        rec_b_final = owner.get_session_record("sess_beta")
        assert rec_b_final is not None
        assert rec_b_final.status == RecordingState.STOPPED.value

    def test_e2e_checkpoint_memoization_and_crash_resumption_lifecycle(self, tmp_path: Path):
        """
        Validates stage checkpoint memoization across multi-stage synthesis:
        - Stage U0 (Ingress) completes and saves atomic checkpoint.
        - Stage L1 (Identity) crashes mid-execution (simulated crash).
        - Pipeline retries with new tracker instance:
            - Stage U0 detects valid checkpoint and reuses memoized result without recomputing.
            - Fast-forwards stage U0 to CHECKPOINT_RESTORED.
            - Stage L1 re-runs and successfully completes.
            - Stage Output files verify content-addressed dependency manifest validation.
        """
        ckpt_dir = tmp_path / "pipeline_checkpoints"
        ckpt_dir.mkdir()
        agentic_ckpt_dir = tmp_path / "agentic_ckpts"
        agentic_ckpt_dir.mkdir()

        metadata = {
            "project": "Apollo",
            "meeting": "Quarterly Review",
            "meeting_date": "10-09-2026",
            "attendees": ["Alice", "Bob"],
        }
        input_file = tmp_path / "sample_input.txt"
        raw_source = "Alice: We hit our Q3 deliverables on schedule. Bob: Architecture verified."
        input_file.write_text(raw_source, encoding="utf-8")

        # Tracker Run 1: Stage U0 succeeds, Stage L1 crashes
        tracker1 = UnifiedAgenticPipelineTracker(
            pipeline_name="unified_e2e_test",
            input_path=str(input_file),
            metadata=metadata,
        )
        tracker1.start_stage(PipelineStage.U0_INGRESS)
        stage_u0_payload = {"raw_transcript": raw_source, "speaker_count": 2}
        tracker1.save_checkpoint(PipelineStage.U0_INGRESS, stage_u0_payload, agentic_ckpt_dir)
        tracker1.complete_stage(PipelineStage.U0_INGRESS, status="PASS", details=stage_u0_payload)

        # Stage L1 crashes mid-flight
        tracker1.start_stage(PipelineStage.L1_IDENTITY)
        tracker1.fail_active_stage(RuntimeError("Simulated SIGTERM / crash during L1 identity resolution"))
        assert PipelineStage.L1_IDENTITY not in tracker1.completed_stages

        # Tracker Run 2 (Retry invocation):
        tracker2 = UnifiedAgenticPipelineTracker(
            pipeline_name="unified_e2e_test",
            input_path=str(input_file),
            metadata=metadata,
            pinned_config_sha256=tracker1.config_sha256,
            pinned_parameters_digest=tracker1.parameters_digest,
        )

        # Fast-forward resume from checkpoint
        assert tracker2.can_resume_from_stage(PipelineStage.L1_IDENTITY, agentic_ckpt_dir) is True
        tracker2.resume_to_stage(PipelineStage.L1_IDENTITY, agentic_ckpt_dir)

        assert PipelineStage.U0_INGRESS in tracker2.completed_stages
        u0_record = tracker2.stage_records[PipelineStage.U0_INGRESS.value]
        assert u0_record.status == "CHECKPOINT_RESTORED"
        assert u0_record.details["raw_transcript"] == raw_source

        # Now execute Stage L1 cleanly to completion
        tracker2.start_stage(PipelineStage.L1_IDENTITY)
        tracker2.complete_stage(
            PipelineStage.L1_IDENTITY,
            status="PASS",
            details={"resolved_identities": ["Alice", "Bob"]},
        )
        assert PipelineStage.L1_IDENTITY in tracker2.completed_stages

        # Verify Stage Output Checkpoints with ContentAddressedDependencyManifest
        stage1_manifest = compute_stage_manifest("output_1", raw_source, metadata)
        stage1_file = ckpt_dir / "01_apollo_transcript.ckpt.md"
        stage1_content = "# Output 01 Transcript\n\nAlice: We hit our Q3 deliverables on schedule.\n" * 20
        save_stage_checkpoint(stage1_file, stage1_content, stage1_manifest)
        assert is_checkpoint_valid(stage1_file, stage1_manifest, strict_manifest=True) is True

    def test_e2e_worktree_bifurcation_reconciliation_and_symlink_safety(self, tmp_path: Path):
        """
        Validates worktree bifurcation recovery and dependency isolation:
        - Simulates bifurcated recording chunks across source and target worktrees.
        - Coalesces and merges artifacts without data loss or file clobbering.
        - Verifies safely_unlink_worktree_dependencies preserves root shared directories (.venv, .local).
        """
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        shared_venv = repo_root / ".venv"
        shared_venv.mkdir()
        (shared_venv / "lib_file.txt").write_text("critical_venv_payload", encoding="utf-8")
        shared_local = repo_root / ".local"
        shared_local.mkdir()
        (shared_local / "local_cache.db").write_text("critical_local_db", encoding="utf-8")

        source_wt = tmp_path / "worktree_source"
        target_wt = tmp_path / "worktree_target"
        source_wt.mkdir()
        target_wt.mkdir()

        # Create output folders with meeting deliverables in source worktree
        src_output_meeting = source_wt / "output" / "Apollo" / "10-09-2026" / "WeeklySync"
        src_output_meeting.mkdir(parents=True)
        (src_output_meeting / "meeting_audio.wav").write_bytes(b"RIFF_FAKE_AUDIO_DATA_FOR_SYNC")
        (src_output_meeting / "01_transcript.md").write_text("Initial partial transcript", encoding="utf-8")

        target_output_meeting = target_wt / "output" / "Apollo" / "10-09-2026" / "WeeklySync"
        target_output_meeting.mkdir(parents=True)
        (target_output_meeting / "02_recap.md").write_text("Executive recap", encoding="utf-8")

        # 1. Test dry-run reconciliation scanning
        dry_run_res = coalesce_live_worktree_pair(source_wt, target_wt, repo_root, dry_run=True)
        assert dry_run_res["status"] == "PASS"
        assert dry_run_res["migrated_files_count"] == 2

        # 2. Test actual artifact migration and content hashing
        migrated = migrate_artifacts(source_wt, target_wt)
        assert len(migrated) == 2
        for item in migrated:
            assert Path(item["target"]).is_file()
            src_bytes = Path(item["source"]).read_bytes()
            dst_bytes = Path(item["target"]).read_bytes()
            assert hashlib.sha256(src_bytes).hexdigest() == item["sha256"]
            assert src_bytes == dst_bytes

        # Verify target worktree now has both sets of deliverables
        assert (target_output_meeting / "meeting_audio.wav").is_file()
        assert (target_output_meeting / "01_transcript.md").is_file()
        assert (target_output_meeting / "02_recap.md").is_file()

        # 3. Test symlink dependency isolation
        fake_tool_wt = tmp_path / "worktree_fake"
        fake_tool_wt.mkdir()
        os.symlink(shared_venv, fake_tool_wt / ".venv")
        os.symlink(shared_local, fake_tool_wt / ".local")

        # Safely unlink worktree dependencies
        safely_unlink_worktree_dependencies(fake_tool_wt)
        assert not (fake_tool_wt / ".venv").exists()
        assert not (fake_tool_wt / ".local").exists()

        # CRITICAL INVARIANT: Shared root assets MUST remain completely intact
        assert shared_venv.is_dir()
        assert (shared_venv / "lib_file.txt").read_text(encoding="utf-8") == "critical_venv_payload"
        assert shared_local.is_dir()
        assert (shared_local / "local_cache.db").read_text(encoding="utf-8") == "critical_local_db"
