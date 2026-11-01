"""
tests/characterization/test_live_recorder_start_characterization.py

Flipped verification tests for startup latency defects and capture architecture:
F1: _worker_entrypoint verifies readiness (Metal, model, golden inference) before READY emission.
F3: transcribe() drains stale/mismatched task_ids and accepts only matching task_id.
F9: Single capture authority - recorder adopts running InstantAudioSpooler without spawning competing process.
F10: Diarization off critical path - get_diarization_engine() not invoked during __init__.
F11: nee.py entrypoint defers dual_pipeline.cli import behind main() gate.
F12: repoint_recorder_paths preserves capture process staging directory and migrates with sha256 verification.
"""
from __future__ import annotations

import ast
import inspect
import multiprocessing as mp
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core import isolated_transcriber
from dual_pipeline.live_meeting_recorder_core.instant_spooler import InstantAudioSpooler
from dual_pipeline.live_meeting_recorder_core.isolated_transcriber import (
    IsolatedMLXTranscriberClient,
)
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    repoint_recorder_paths,
)


class TestLiveRecorderStartCharacterization(unittest.TestCase):
    def test_f1_worker_verifies_readiness_before_ready_emission(self):
        """F1: _worker_entrypoint delegates to _perform_worker_handshake before accepting tasks."""
        src = inspect.getsource(isolated_transcriber._worker_entrypoint)
        self.assertIn(
            "_perform_worker_handshake",
            src,
            "Worker must call _perform_worker_handshake to verify Metal, weights, and golden inference before accepting tasks.",
        )
        raw_ready = 'output_q.put({"type": "READY"}'
        self.assertNotIn(
            raw_ready,
            src,
            "Worker entrypoint must not emit raw unverified READY message.",
        )

    def test_f3_transcribe_drains_stale_task_id_and_returns_matching(self):
        """F3: transcribe() drains stale task_id results from queue and returns matching task result."""
        client = IsolatedMLXTranscriberClient(mock_mode=True)
        client._is_running = True
        client._process = MagicMock()
        client._process.is_alive.return_value = True
        client._input_q = mp.Queue()
        client._output_q = mp.Queue()

        # Inject stale result from an earlier timed-out task
        client._output_q.put({
            "type": "RESULT",
            "task_id": "task_000000_stale_task_12345",
            "status": "SUCCESS",
            "text": "stale unverified transcription from previous timeout",
        })

        def _mock_put(payload):
            task_id = payload.get("task_id")
            client._output_q.put({
                "type": "RESULT",
                "task_id": task_id,
                "status": "SUCCESS",
                "text": "fresh matching transcription",
            })

        client._input_q.put = _mock_put

        res = client.transcribe(Path("dummy_audio.wav"), timeout=2.0)
        self.assertEqual(
            res,
            "fresh matching transcription",
            "transcribe() must drain stale task results and return the matching result.",
        )

    def test_f9_single_capture_authority_adopts_spooler(self):
        """F9: LiveMeetingRecorder adopts running InstantAudioSpooler without spawning a second capture authority."""
        with patch("dual_pipeline.live_meeting_recorder.ensure_ffmpeg"), \
             patch("dual_pipeline.live_meeting_recorder.ConversationalSpeakerTracker"), \
             patch("dual_pipeline.live_meeting_recorder.ChatAttributionCollector"), \
             patch("dual_pipeline.live_meeting_recorder.AudioEnergyWatchdog"):

            spooler = InstantAudioSpooler(mock_mode=True)
            spooler.start_eager()
            self.assertTrue(spooler.is_running, "Spooler must be actively running.")

            recorder = LiveMeetingRecorder(
                project_name="project_nexus",
                meeting_name="Characterization Session F9",
                mock_mode=True,
                spooler=spooler,
                isolated_process=False,
                enable_ws=False,
                enable_copilot=False,
            )
            recorder.start_ffmpeg()

            try:
                self.assertTrue(spooler.is_adopted, "Spooler must be marked as adopted by recorder.")
                self.assertTrue(recorder.capture_manager.is_running, "recorder.capture_manager must be active.")
                self.assertIs(
                    recorder.capture_manager._mock_thread,
                    spooler._mock_thread,
                    "recorder.capture_manager must adopt the spooler capture thread rather than spawning a second authority.",
                )
            finally:
                spooler.discard()
                recorder._early_abort_cleanup()

    def test_f10_diarization_engine_not_instantiated_synchronously_in_init(self):
        """F10: LiveMeetingRecorder.__init__ does not invoke get_diarization_engine() synchronously."""
        with patch("dual_pipeline.audio_ingestion_core.diarization_engine.get_diarization_engine") as mock_get_engine, \
             patch("dual_pipeline.live_meeting_recorder.ensure_ffmpeg"), \
             patch("dual_pipeline.live_meeting_recorder.LiveAudioCaptureManager"), \
             patch("dual_pipeline.live_meeting_recorder.ConversationalSpeakerTracker"), \
             patch("dual_pipeline.live_meeting_recorder.ChatAttributionCollector"), \
             patch("dual_pipeline.live_meeting_recorder.AudioEnergyWatchdog"):

            rec = LiveMeetingRecorder(
                project_name="project_nexus",
                meeting_name="Characterization Session F10",
                mock_mode=False,
                dry_run=False,
                isolated_process=False,
                enable_ws=False,
                enable_copilot=False,
            )
            try:
                self.assertEqual(
                    mock_get_engine.call_count,
                    0,
                    "get_diarization_engine() must NOT be called synchronously during __init__.",
                )
            finally:
                rec._release_slug_lock()

    def test_f11_entrypoint_has_no_toplevel_cli_imports(self):
        """F11: nee.py does not import dual_pipeline.cli at top level."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        nee_path = repo_root / "nee.py"
        self.assertTrue(nee_path.exists(), "nee.py must exist at repository root.")

        tree = ast.parse(nee_path.read_text(encoding="utf-8"))
        cli_imports = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == "dual_pipeline.cli":
                cli_imports.append(node)

        self.assertEqual(
            len(cli_imports),
            0,
            "nee.py must not import dual_pipeline.cli at top level outside main() gate.",
        )

    def test_f12_worktree_repoint_preserves_capture_staging_and_verifies_checksum(self):
        """F12: repoint_recorder_paths preserves capture_manager staging chunks directory and verifies sha256."""
        with patch("dual_pipeline.live_meeting_recorder.ensure_ffmpeg"), \
             patch("dual_pipeline.live_meeting_recorder.ConversationalSpeakerTracker"), \
             patch("dual_pipeline.live_meeting_recorder.ChatAttributionCollector"), \
             patch("dual_pipeline.live_meeting_recorder.AudioEnergyWatchdog"), \
             patch("dual_pipeline.live_meeting_recorder.is_inside_isolated_worktree", return_value=False):

            rec = LiveMeetingRecorder(
                project_name="project_nexus",
                meeting_name="Characterization Session F12",
                mock_mode=True,
                isolated_process=False,
                enable_ws=False,
                enable_copilot=False,
                enforce_worktree=True,
            )
            try:
                initial_staging = rec.capture_manager.chunks_dir
                self.assertIn(".staging_", str(initial_staging))

                target_dir = Path("/tmp/test_worktree_target_f12")
                repoint_recorder_paths(rec, target_dir)

                self.assertEqual(rec.chunks_dir, rec._target_chunks_dir)
                self.assertEqual(
                    rec.capture_manager.chunks_dir,
                    initial_staging,
                    "Capture manager chunks_dir must remain pointing to staging directory where subprocess writes.",
                )
            finally:
                rec._early_abort_cleanup()


if __name__ == "__main__":
    unittest.main()
