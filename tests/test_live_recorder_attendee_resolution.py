"""
tests/test_live_recorder_attendee_resolution.py

Wave 2 acceptance test suite for GraphDB-backed attendee resolution across:
1. LiveMeetingRecorder initialization and manifest emission.
2. Canonical ingestion command construction.
3. Speaker hint speaker name resolution and durable queue persistence.
4. Disaster recovery session reconstruction and manifest attendee preservation.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import unittest
from pathlib import Path
import wave

from dual_pipeline.cli.speaker_hint_handlers import _resolve_speaker_name, handle_speaker_hint
from dual_pipeline.core import ROOT
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.disaster_recovery import SessionRecoveryManager


class TestLiveRecorderAttendeeResolution(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_recorder_roster_"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_record_meeting_attendees_resolution(self):
        """Verify LiveMeetingRecorder resolves shorthands into GraphDB full canonical names."""
        recorder = LiveMeetingRecorder(
            project_name="project_lens",
            meeting_name="LENS Sprint 4 PM EST",
            meeting_date="10/15/2026",
            attendees="Ed, Alex, PartnerAdvisory team",
            output_dir=self.temp_dir,
            mock_mode=True,
        )

        names = [a["name"] for a in recorder.attendees]
        # Must resolve to full canonical names, never raw shorthands
        self.assertIn("Eduardo More", names)
        self.assertIn("Alex Sanchez", names)
        self.assertIn("Jennifer Pan", names)
        self.assertIn("Amit Ayer", names)
        self.assertIn("Jake Stark", names)
        self.assertIn("Morgan Vance Krutilla", names)

        # Raw shorthand must not remain unexpanded
        self.assertNotIn("Ed", names)
        self.assertNotIn("PartnerAdvisory team", names)

        # Check organizations
        ed_entry = next(a for a in recorder.attendees if a["name"] == "Eduardo More")
        self.assertIn("Apex Global", ed_entry["org"])
        self.assertTrue(bool(ed_entry["role"]) and ed_entry["role"] != "Participant")

        amit_entry = next(a for a in recorder.attendees if a["name"] == "Amit Ayer")
        self.assertEqual(amit_entry["org"], "PartnerAdvisory")
        self.assertIn("Enterprise Architect", amit_entry["role"])

        # Verify manifest persistence on disk
        recorder._init_transcript_file()
        recorder._write_session_manifest(recorder.session_start_time)
        self.assertTrue(recorder.session_manifest.exists())

        manifest_data = json.loads(recorder.session_manifest.read_text(encoding="utf-8"))
        self.assertIn("attendees", manifest_data)
        manifest_names = [a["name"] for a in manifest_data["attendees"]]
        self.assertIn("Eduardo More", manifest_names)
        self.assertIn("Jennifer Pan", manifest_names)

        # Verify canonical ingest command formatting
        from dual_pipeline.cli.canonical_invocation import build_canonical_ingest_command
        cmd = build_canonical_ingest_command(
            input_path=recorder.master_wav,
            project=recorder.project_name,
            meeting=recorder.meeting_name,
            date=recorder.meeting_date_display,
            attendees=recorder.attendees,
        )
        self.assertIn("Eduardo More", cmd)
        self.assertIn("Jennifer Pan", cmd)
        self.assertNotIn("PartnerAdvisory team", cmd)

    def test_record_meeting_partner_advisory_team_individual_roster(self):
        """Verify full PartnerAdvisory team individual roster resolves all 5 contributors with non-placeholder roles."""
        recorder = LiveMeetingRecorder(
            project_name="project_lens",
            meeting_name="LENS Architecture Deep Dive",
            meeting_date="10/15/2026",
            attendees="full PartnerAdvisory Team (Jen, Amit, Alex, Jake, Morgan Vance)",
            output_dir=self.temp_dir,
            mock_mode=True,
        )

        names = [a["name"] for a in recorder.attendees]
        self.assertTrue(any("Jen" in n for n in names))
        self.assertIn("Amit Ayer", names)
        self.assertIn("Alex Sanchez", names)
        self.assertIn("Jake Stark", names)
        self.assertIn("Morgan Vance Krutilla", names)

        for a in recorder.attendees:
            self.assertEqual(a["org"], "PartnerAdvisory")
            self.assertNotEqual(a["role"], "Participant")
            self.assertTrue(len(a["role"]) > 3)

    def test_speaker_hint_speaker_name_resolution(self):
        """Verify speaker-hint resolves partial and initialed speaker tokens."""
        self.assertEqual(_resolve_speaker_name("ALex S"), "Alex Sanchez")
        self.assertEqual(_resolve_speaker_name("Ed"), "Eduardo More")
        self.assertTrue("Jen" in _resolve_speaker_name("Jen"))

        # Test queueing with resolved speaker name
        cache_dir = self.temp_dir / ".cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = cache_dir / "session_manifest.json"
        manifest_path.write_text(json.dumps({
            "project": "project_lens",
            "meeting": "LENS Demo",
            "attendees": [{"name": "Alex Sanchez", "org": "PartnerAdvisory", "role": "Tech Lead"}],
        }), encoding="utf-8")

        args = argparse.Namespace(
            quote="We have aligned on field level confidence scores",
            speaker="ALex S",
            meeting_dir=str(self.temp_dir),
            runtime_root=ROOT,
            force=False,
        )
        res = handle_speaker_hint(args)
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["speaker_name"], "Alex Sanchez")

        # Verify queued payload on disk
        queue_file = cache_dir / "speaker_hints_queue.json"
        self.assertTrue(queue_file.exists())
        queue_data = json.loads(queue_file.read_text(encoding="utf-8"))
        self.assertTrue(any(h.get("speaker_name") == "Alex Sanchez" for h in queue_data))

    def test_disaster_recovery_manifest_attendee_resolution(self):
        """Verify disaster recovery reconstructs manifest with GraphDB-resolved attendees."""
        session_dir = self.temp_dir / "interrupted_session"
        chunks_dir = session_dir / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)

        # Create a valid minimal WAV chunk
        wav_path = chunks_dir / "chunk_0001.wav"
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * 16000)

        # Write journal with session_start event containing shorthand attendees
        journal_path = session_dir / "session_journal.jsonl"
        start_event = {
            "event": "session_start",
            "project": "project_lens",
            "meeting": "LENS Recovery Check",
            "date": "10/15/2026",
            "attendees": "Ed, Alex, PartnerAdvisory team",
        }
        journal_path.write_text(json.dumps(start_event) + "\n", encoding="utf-8")

        rec_result = SessionRecoveryManager.recover_session(
            session_dir=session_dir,
            output_dir=self.temp_dir / "recovered_output",
            project="project_lens",
        )

        self.assertTrue(rec_result.success)
        self.assertTrue(rec_result.manifest_path.exists())

        manifest_data = json.loads(rec_result.manifest_path.read_text(encoding="utf-8"))
        self.assertIn("attendees", manifest_data)
        att_names = [a["name"] for a in manifest_data["attendees"]]
        self.assertIn("Eduardo More", att_names)
        self.assertIn("Jennifer Pan", att_names)
        self.assertIn("Alex Sanchez", att_names)
        self.assertNotIn("PartnerAdvisory team", att_names)


if __name__ == "__main__":
    unittest.main()
