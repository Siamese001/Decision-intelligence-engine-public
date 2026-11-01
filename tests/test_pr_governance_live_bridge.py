"""
Unit and regression tests for Live Worktree Meeting Intake Bridge & Status Classifier (Wave 3).
Tests cover:
- Detection of live meeting containers (naming conventions and markers)
- Classification of all 5 lifecycle states:
  1. RECORDING_IN_PROGRESS
  2. SYNTHESIS_PENDING
  3. STAGE_U0_READY
  4. ORPHANED_AUDIO
  5. ABANDONED_STUB
- Action routing explanations in queue_actions.py
- Inventory breakdown and metrics calculation
- Decision D5 compliance: strictly non-destructive inspection (zero repo or file mutations)
"""
from __future__ import annotations

from pathlib import Path

from tools.pr_governance_core.live_meeting_bridge import (
    LiveMeetingStatus,
    classify_live_worktree,
    explain_live_meeting_action,
    is_live_meeting_container,
    scan_live_meeting_markers,
)
from tools.pr_governance_core.queue_actions import explain_worktree_action
from tools.pr_governance_core.worktree_inventory import (
    _classify_worktree_child,
    build_worktree_inventory,
)


class TestLiveMeetingContainerDetection:
    """Tests for is_live_meeting_container()."""

    def test_naming_conventions(self, tmp_path: Path):
        live_dir = tmp_path / "live_20261005_120000"
        live_dir.mkdir()
        assert is_live_meeting_container(live_dir) is True

        feat_live_dir = tmp_path / "feat_live_project_lens"
        feat_live_dir.mkdir()
        assert is_live_meeting_container(feat_live_dir) is True

        meeting_dir = tmp_path / "meeting_sync"
        meeting_dir.mkdir()
        assert is_live_meeting_container(meeting_dir) is True

        unrelated = tmp_path / "feat_core_storage"
        unrelated.mkdir()
        assert is_live_meeting_container(unrelated) is False

    def test_marker_detection_without_naming(self, tmp_path: Path):
        session = tmp_path / "session_abc"
        session.mkdir()
        assert is_live_meeting_container(session) is False

        (session / ".recording_active").touch()
        assert is_live_meeting_container(session) is True

    def test_live_transcript_detection(self, tmp_path: Path):
        session = tmp_path / "session_xyz"
        session.mkdir()
        (session / "meeting_live_transcript.md").write_text("# Live transcript\n")
        assert is_live_meeting_container(session) is True


class TestLiveMeetingLifecycleClassification:
    """Tests for classify_live_worktree across all 5 states."""

    def test_state_recording_in_progress_via_marker(self, tmp_path: Path):
        session = tmp_path / "live_session_1"
        session.mkdir()
        (session / ".recording_active").touch()
        (session / "chunk_001.wav").write_bytes(b"RIFF...")

        status, markers = classify_live_worktree(session, is_registered=True)
        assert status == LiveMeetingStatus.RECORDING_IN_PROGRESS
        assert markers["has_active_marker"] is True
        assert markers["audio_chunks_count"] == 1
        assert markers["has_deliverables"] is False

        action = explain_live_meeting_action(status, markers)
        assert "[WAIT_RECORDING]" in action
        assert "do not commit or prune" in action

    def test_state_recording_in_progress_via_transcript_stream(self, tmp_path: Path):
        session = tmp_path / "live_session_2"
        session.mkdir()
        lt = session / "09-20-2026_live_transcript.md"
        lt.write_text("# Meeting Log\nStatus: RECORDING_IN_PROGRESS\nSpeaker 1: Hello\n")

        status, markers = classify_live_worktree(session, is_registered=True)
        assert status == LiveMeetingStatus.RECORDING_IN_PROGRESS
        assert markers["has_active_marker"] is True

    def test_state_synthesis_pending(self, tmp_path: Path):
        session = tmp_path / "live_session_3"
        session.mkdir()
        (session / "chunk_001.wav").write_bytes(b"RIFF...")
        (session / "chunk_002.wav").write_bytes(b"RIFF...")
        lt = session / "live_transcript.md"
        lt.write_text("# Meeting Log\nStatus: Session completed\n")

        status, markers = classify_live_worktree(session, is_registered=True)
        assert status == LiveMeetingStatus.SYNTHESIS_PENDING
        assert markers["audio_chunks_count"] == 2
        assert markers["has_deliverables"] is False

        action = explain_live_meeting_action(status, markers)
        assert "[RUN_SYNTHESIS]" in action
        assert "Audio recorded (2 chunk(s))" in action

    def test_state_stage_u0_ready_triad(self, tmp_path: Path):
        session = tmp_path / "live_session_4"
        out_dir = session / "output" / "project_lens" / "10-05-2026" / "Review_Amit"
        out_dir.mkdir(parents=True)
        (out_dir / "01_Review_Amit_10-05-2026_hitl_resolved_transcript.md").write_text("# 01")
        (out_dir / "02_Review_Amit_10-05-2026_executive_recap.md").write_text("# 02")
        (out_dir / "03_Review_Amit_10-05-2026_canonical_signal_projection.json").write_text("{}")

        status, markers = classify_live_worktree(session, is_registered=True)
        assert status == LiveMeetingStatus.STAGE_U0_READY
        assert markers["has_deliverables"] is True
        assert markers["triad_complete"] is True

        action = explain_live_meeting_action(status, markers)
        assert "[COMMIT_DELIVERABLES]" in action
        assert "triad complete (01, 02, 03)" in action

    def test_state_orphaned_audio(self, tmp_path: Path):
        orphan_dir = tmp_path / "orphan_audio_dump"
        orphan_dir.mkdir()
        (orphan_dir / "chunk_alpha.wav").write_bytes(b"RIFF...")

        # Not registered, name doesn't start with live_
        status, markers = classify_live_worktree(orphan_dir, is_registered=False)
        assert status == LiveMeetingStatus.ORPHANED_AUDIO

        action = explain_live_meeting_action(status, markers)
        assert "[QUARANTINE_AUDIO]" in action

    def test_state_abandoned_stub(self, tmp_path: Path):
        stub_dir = tmp_path / "live_empty_stub"
        stub_dir.mkdir()

        status, markers = classify_live_worktree(stub_dir, is_registered=True)
        assert status == LiveMeetingStatus.ABANDONED_STUB

        action = explain_live_meeting_action(status, markers)
        assert "[QUARANTINE_STUB]" in action


class TestQueueActionsWorktreeIntegration:
    """Tests for explain_worktree_action with live meeting status."""

    def test_routing_recording_in_progress(self):
        item = {
            "classification": "RECORDING_IN_PROGRESS",
            "live_meeting_status": "RECORDING_IN_PROGRESS",
        }
        res = explain_worktree_action(item)
        assert "[WAIT_RECORDING]" in res

    def test_routing_synthesis_pending(self):
        item = {
            "classification": "SYNTHESIS_PENDING",
            "live_meeting_status": "SYNTHESIS_PENDING",
            "audio_chunks_count": 5,
        }
        res = explain_worktree_action(item)
        assert "[RUN_SYNTHESIS]" in res
        assert "5 chunk(s)" in res

    def test_routing_stage_u0_ready(self):
        item = {
            "classification": "STAGE_U0_READY",
            "live_meeting_status": "STAGE_U0_READY",
        }
        res = explain_worktree_action(item)
        assert "[COMMIT_DELIVERABLES]" in res


class TestInventoryCountsAndMetrics:
    """Tests for build_worktree_inventory with live meeting sessions."""

    def test_inventory_breakdown(self, tmp_path: Path):
        repo_root = tmp_path
        wt_dir = repo_root / ".worktrees"
        wt_dir.mkdir()

        # 1. Live recording in progress
        s1 = wt_dir / "live_session_active"
        s1.mkdir()
        (s1 / ".recording_active").touch()

        # 2. Live synthesis pending
        s2 = wt_dir / "live_session_recorded"
        s2.mkdir()
        (s2 / "chunk_1.wav").write_bytes(b"RIFF")

        # 3. Live deliverables ready
        s3 = wt_dir / "live_session_done"
        s3.mkdir()
        (s3 / "01_done.md").write_text("# 01")

        # 4. Plain orphan directory
        s4 = wt_dir / "plain_orphan"
        s4.mkdir()
        (s4 / "notes.txt").write_text("hello")

        inv = build_worktree_inventory(repo_root)
        counts = inv["counts"]

        assert counts["live_meeting_sessions"] == 3
        assert counts["live_recording_in_progress"] == 1
        assert counts["live_synthesis_pending"] == 1
        assert counts["live_stage_u0_ready"] == 1


class TestDecisionD5NonDestructiveInvariant:
    """Verifies that live meeting inspection never alters, unlinks, or deletes any files."""

    def test_non_destructive_scanning(self, tmp_path: Path):
        session = tmp_path / "live_immutable_session"
        session.mkdir()
        marker = session / ".recording_active"
        marker.write_text("ACTIVE\n")
        chunk = session / "chunk_1.wav"
        chunk.write_bytes(b"BINARY_PAYLOAD_IMMUTABLE")

        initial_files = {p: p.read_bytes() for p in session.iterdir()}

        # Run scanner multiple times
        scan_live_meeting_markers(session)
        classify_live_worktree(session, is_registered=True)
        classify_live_worktree(session, is_registered=False)
        _classify_worktree_child(session, {}, tmp_path)

        final_files = {p: p.read_bytes() for p in session.iterdir()}
        assert initial_files == final_files
        assert marker.exists()
        assert chunk.exists()
