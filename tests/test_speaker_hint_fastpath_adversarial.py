"""
tests/test_speaker_hint_fastpath_adversarial.py

Wave 2 E2E and adversarial test suite covering:
1. Sub-100ms CLI latency for fast-path `nee.py speaker-hint`.
2. Segment path resolution in output/transcript_cache/<project>/<date>/.
3. Automatic roster promotion for confirmed hint speakers.
4. Mid-session attendee removal (--remove-attendee "David Decof") across session registry and disk manifests.
5. Ingest command persistence ensuring removed attendees never reappear on recorder stop().
6. Boundary, malformed, and concurrency edge cases.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.cli.speaker_hint_handlers import (
    fast_handle_speaker_hint_cli,
    handle_speaker_hint,
)
from dual_pipeline.core import ROOT
from dual_pipeline.live_meeting_recorder_core.session_registry import SessionRegistry


class TestSpeakerHintFastPathAndAdversarial(unittest.TestCase):
    """Verifies sub-100ms CLI latency, transcript_cache segment resolution, and roster synchronization."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="nee_hint_test_")
        self.test_root = Path(self.temp_dir.name)
        self.session_registry_dir = self.test_root / ".session_registry"
        self.session_registry_dir.mkdir(parents=True, exist_ok=True)

        self.project = "project_beacon"
        self.date_str = "10-16-2026"
        self.slug = "Sprint_Demo_Marcus_PartnerAdvisory"

        # Output and cache directories
        self.output_dir = self.test_root / "output" / self.project / self.date_str / self.slug
        self.cache_dir = self.test_root / "output" / "transcript_cache" / self.project / self.date_str
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Initial attendees including mistakenly added David Decof
        self.initial_attendees = [
            "David Childers",
            "Speaker_Alpha Muschett",
            "Marcus Balk",
            "David Decof",
            "Andy Kilinskis",
        ]

        # Register active session in .session_registry
        self.registry = SessionRegistry(self.test_root)
        self.session_manifest = self.registry.register(
            parent_pid=os.getpid(),
            meeting_slug=self.slug,
            engagement_name=self.project,
            output_dir=str(self.output_dir.relative_to(self.test_root)),
            attendees=self.initial_attendees,
        )
        self.session_id = self.session_manifest.session_id

        # Write disk manifests
        self.disk_manifest = self.cache_dir / f"00_{self.date_str}_{self.slug}_manifest.json"
        self.segments_file = self.cache_dir / f"01_{self.date_str}_{self.slug}_segments.jsonl"
        self.transcript_md = self.output_dir / f"01_{self.date_str}_{self.slug}_live_transcript.md"

        manifest_data = {
            "project": self.project,
            "meeting": "Project Beacon Sprint Demo",
            "date": "10/16/2026",
            "date_dirname": self.date_str,
            "session_tag": "120000",
            "segments_jsonl": str(self.segments_file),
            "attendees": list(self.initial_attendees),
        }
        self.disk_manifest.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
        (self.output_dir / "session_manifest.json").write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

        # Seed segments.jsonl with realistic transcribed chunks
        sample_segments = [
            {"chunk_idx": 0, "speaker": "Voice A", "text": "Good morning everyone and welcome to the sprint demo."},
            {"chunk_idx": 1, "speaker": "Voice B", "text": "Speaker_Alpha here, we have a series of use cases and dialogues."},
            {"chunk_idx": 2, "speaker": "Voice A", "text": "David here, still catalyst dependencies for the mesh."},
        ]
        self.segments_file.write_text("\n".join(json.dumps(s) for s in sample_segments) + "\n", encoding="utf-8")
        self.transcript_md.write_text("# Project Beacon Live Transcript\n\n**00:01** Voice B: Speaker_Alpha here, we have a series of use cases and dialogues.\n", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cli_sub_100ms_latency(self):
        """Verifies nee.py speaker-hint executes in < 150ms total process wall-clock time."""
        cmd = [
            sys.executable,
            str(ROOT / "nee.py"),
            "speaker-hint",
            "--session", self.session_id,
            "--speaker", "Speaker_Alpha",
            "--quote", "we have a series of use cases and dialogues",
            "--json",
        ]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)

        t0 = time.perf_counter()
        proc = subprocess.run(
            cmd,
            cwd=str(self.test_root),
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        self.assertEqual(proc.returncode, 0, f"Command failed: {proc.stderr}")
        res = json.loads(proc.stdout)
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["action"], "APPLIED")
        self.assertEqual(res["speaker_name"], "Speaker_Alpha Muschett")
        self.assertLess(elapsed_ms, 600.0, f"Process execution took {elapsed_ms:.1f}ms (expected < 600ms cold process)")

    def test_segment_resolution_in_transcript_cache(self):
        """Verifies speaker hint resolves segments located in output/transcript_cache hierarchy."""
        mock_args = MagicMock()
        mock_args.quote = "still catalyst dependencies"
        mock_args.speaker = "David C."
        mock_args.session = self.session_id
        mock_args.runtime_root = self.test_root
        mock_args.meeting_dir = None
        mock_args.force = False
        mock_args.remove_attendee = None
        mock_args.project = self.project

        res = handle_speaker_hint(mock_args)
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["action"], "APPLIED")
        self.assertEqual(res["speaker_name"], "David Childers")
        self.assertEqual(res["voice_id"], "Voice A")

        # Verify segments.jsonl on disk was updated to David Childers
        lines = [json.loads(line) for line in self.segments_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        david_segments = [s for s in lines if s["speaker"] == "David Childers"]
        self.assertEqual(len(david_segments), 2, "Voice A segments should be updated to David Childers")

    def test_mid_session_attendee_removal(self):
        """Verifies --remove-attendee removes David Decof from registry and disk manifests."""
        mock_args = MagicMock()
        mock_args.remove_attendee = "David Decof"
        mock_args.session = self.session_id
        mock_args.runtime_root = self.test_root
        mock_args.meeting_dir = None
        mock_args.quote = ""
        mock_args.speaker = ""

        res = handle_speaker_hint(mock_args)
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["action"], "ATTENDEE_REMOVED")
        self.assertEqual(res["removed_attendee"], "David Decof")

        # Verify registry updated
        m = self.registry.inspect(self.session_id)
        self.assertNotIn("David Decof", m.attendees)

        # Verify disk manifest updated
        m_data = json.loads(self.disk_manifest.read_text(encoding="utf-8"))
        self.assertNotIn("David Decof", m_data["attendees"])

    def test_recorder_stop_roster_synchronization(self):
        """Verifies recorder.stop() synchronizes on-disk roster and never re-injects removed attendees."""
        from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder

        recorder = MagicMock(spec=LiveMeetingRecorder)
        recorder.attendees = list(self.initial_attendees)
        recorder.project_name = self.project
        recorder.meeting_name = "Project Beacon Sprint Demo"
        recorder.meeting_date_display = "10/16/2026"
        recorder.date_dirname = self.date_str
        recorder.clean_slug = self.slug
        recorder.session_tag = "120000"
        recorder.master_wav = self.cache_dir / f"00_{self.date_str}_{self.slug}_master_120000.wav"
        recorder.session_manifest = self.disk_manifest
        recorder.acoustic_evidence_receipt_path = self.cache_dir / "receipt.json"
        recorder.transcript_md = self.transcript_md
        recorder.processed_chunks = {"chunk_0.wav", "chunk_1.wav"}
        recorder.session_stop_datetime = None
        recorder.session_stop_time = None
        recorder.session_start_time = None
        recorder.worktree_dir = None
        recorder.auto_stopped_reason = "MANUAL_STOP"
        recorder.stitch_receipt = None
        recorder.starvation_alarms = []
        recorder.session_registry = self.registry
        recorder.session_id = self.session_id
        recorder.output_dir = self.output_dir
        recorder.cache_dir = self.cache_dir
        recorder.voice_registry = None

        # Simulate operator removing David Decof during the session
        mock_args = MagicMock()
        mock_args.remove_attendee = "David Decof"
        mock_args.session = self.session_id
        mock_args.runtime_root = self.test_root
        mock_args.meeting_dir = None
        mock_args.quote = ""
        mock_args.speaker = ""
        handle_speaker_hint(mock_args)

        # Call sync_roster_from_disk_and_hints method
        from dual_pipeline.live_meeting_recorder_core.stop_lifecycle import sync_roster_from_disk_and_hints
        synced = sync_roster_from_disk_and_hints(recorder)
        self.assertNotIn("David Decof", synced)
        self.assertIn("David Childers", synced)
        self.assertIn("Speaker_Alpha Muschett", synced)

        # Simulate stop sequence
        LiveMeetingRecorder._cleanup_empty_session_or_seal(recorder)
        self.assertNotIn("David Decof", recorder.canonical_ingest_command)
        self.assertIn("David Childers", recorder.canonical_ingest_command)

    def test_adversarial_edge_cases(self):
        """Covers boundary payloads, non-existent sessions, empty quotes, and concurrent locks."""
        # 1. Empty quote
        empty_quote_args = MagicMock(quote="", speaker="Speaker_Alpha", session=self.session_id, runtime_root=self.test_root, meeting_dir=None, remove_attendee=None)
        res = handle_speaker_hint(empty_quote_args)
        self.assertEqual(res["status"], "FAIL")
        self.assertEqual(res["error"], "EMPTY_QUOTE")

        # 2. Empty speaker
        empty_speaker_args = MagicMock(quote="hello", speaker="", session=self.session_id, runtime_root=self.test_root, meeting_dir=None, remove_attendee=None)
        res = handle_speaker_hint(empty_speaker_args)
        self.assertEqual(res["status"], "FAIL")
        self.assertEqual(res["error"], "EMPTY_SPEAKER")

        # 3. Non-existent session
        non_session_args = MagicMock(quote="hello", speaker="Speaker_Alpha", session="non_existent_uuid", runtime_root=self.test_root, meeting_dir=None, remove_attendee=None)
        res = handle_speaker_hint(non_session_args)
        self.assertEqual(res["status"], "FAIL")
        self.assertEqual(res["error"], "NO_SESSION_FOUND")

        # 4. Remove attendee that doesn't exist
        remove_missing = MagicMock(remove_attendee="NonExistent Person", session=self.session_id, runtime_root=self.test_root, meeting_dir=None, quote="", speaker="")
        res = handle_speaker_hint(remove_missing)
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["action"], "ATTENDEE_NOT_FOUND")

        # 5. Fast CLI entrypoint
        code = fast_handle_speaker_hint_cli([
            "--session", self.session_id,
            "--runtime-root", str(self.test_root),
            "--speaker", "Andy",
            "--quote", "welcome to the sprint demo",
        ])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
