"""
tests/end_to_end/test_graphdb_attendee_resolution_e2e.py

End-to-End qualification for GraphDB-backed attendee name, title, and team resolution
across the complete live recording, speaker-hint, session recovery, and canonical ingestion lifecycle.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import unittest
import wave
from pathlib import Path

from dual_pipeline.cli.canonical_invocation import build_canonical_ingest_command
from dual_pipeline.cli.speaker_hint_handlers import handle_speaker_hint
from dual_pipeline.core import ROOT
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.disaster_recovery import recover_live_session


class TestGraphdbAttendeeResolutionE2E(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_e2e_roster_"))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_complete_attendee_resolution_lifecycle_e2e(self):
        """E2E lifecycle: record-meeting -> manifest -> hints -> recovery -> canonical ingest."""
        output_dir = self.test_dir / "session_out"
        output_dir.mkdir(parents=True, exist_ok=True)

        # 1. Initialize LiveMeetingRecorder with partial names and PartnerAdvisory team shorthand
        recorder = LiveMeetingRecorder(
            project_name="project_lens",
            meeting_name="LENS Sprint Review and SOW Alignment",
            meeting_date="10/15/2026",
            attendees="Ed, Alex, PartnerAdvisory team",
            output_dir=output_dir,
            mock_mode=True,
        )

        # 2. Verify all attendees resolved to verified canonical identities from GraphDB
        roster_names = {a["name"] for a in recorder.attendees}
        expected_names = {
            "Eduardo More",
            "Alex Sanchez",
            "Jennifer Pan",
            "Amit Ayer",
            "Jake Stark",
            "Morgan Vance Krutilla",
        }
        self.assertTrue(expected_names.issubset(roster_names), f"Missing names: {expected_names - roster_names}")
        self.assertNotIn("Ed", roster_names)
        self.assertNotIn("PartnerAdvisory team", roster_names)

        # Verify organizations and titles
        ed_entry = next(a for a in recorder.attendees if a["name"] == "Eduardo More")
        self.assertIn("Apex Global", ed_entry["org"])
        self.assertTrue(len(ed_entry["role"]) > 3)

        amit_entry = next(a for a in recorder.attendees if a["name"] == "Amit Ayer")
        self.assertEqual(amit_entry["org"], "PartnerAdvisory")
        self.assertIn("Enterprise Architect", amit_entry["role"])

        # 3. Initialize files and verify disk manifest
        recorder._init_transcript_file()
        recorder._write_session_manifest()
        self.assertTrue(recorder.session_manifest.exists())

        disk_manifest = json.loads(recorder.session_manifest.read_text(encoding="utf-8"))
        self.assertIn("attendees", disk_manifest)
        disk_names = {a["name"] for a in disk_manifest["attendees"]}
        self.assertTrue(expected_names.issubset(disk_names))

        # 4. Handle speaker-hint with initialed token
        args = argparse.Namespace(
            quote="We agreed on the 90 percent confidence threshold for Atlas labels",
            speaker="ALex S",
            meeting_dir=str(output_dir),
            runtime_root=ROOT,
            force=False,
        )
        hint_res = handle_speaker_hint(args)
        self.assertEqual(hint_res["status"], "PASS")
        self.assertEqual(hint_res["speaker_name"], "Alex Sanchez")

        # 5. Verify canonical ingest command contains full names
        cmd = build_canonical_ingest_command(
            input_path=recorder.master_wav,
            project=recorder.project_name,
            meeting=recorder.meeting_name,
            date=recorder.meeting_date_display,
            attendees=recorder.attendees,
        )
        self.assertIn("Eduardo More", cmd)
        self.assertIn("Jennifer Pan", cmd)
        self.assertIn("Alex Sanchez", cmd)
        self.assertNotIn("PartnerAdvisory team", cmd)

        # 6. Simulate disaster recovery on the session folder
        chunks_dir = output_dir / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        chunk_file = chunks_dir / "chunk_0001.wav"
        with wave.open(str(chunk_file), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * 8000)

        rec_result = recover_live_session(
            session_dir=output_dir,
            output_dir=self.test_dir / "recovered",
            project="project_lens",
        )
        self.assertTrue(rec_result.success)
        self.assertTrue(rec_result.manifest_path.exists())

        rec_manifest = json.loads(rec_result.manifest_path.read_text(encoding="utf-8"))
        self.assertIn("attendees", rec_manifest)
        rec_names = {a["name"] for a in rec_manifest["attendees"]}
        self.assertIn("Eduardo More", rec_names)
        self.assertIn("Jennifer Pan", rec_names)


if __name__ == "__main__":
    unittest.main()
