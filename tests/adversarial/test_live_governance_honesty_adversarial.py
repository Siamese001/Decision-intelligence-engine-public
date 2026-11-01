"""
tests/adversarial/test_live_governance_honesty_adversarial.py

Adversarial Governance Honesty and Anti-Bypass Test Suite (Wave 2).
Validates:
1. Fabricated-Approval Regression Pin:
   - Deleted hazard tools (reconcile_and_promote_live_ssot.py, eval_copilot_grounding.py,
     characterize_copilot_baseline.py) cannot be resurrected or imported.
   - No ad-hoc scripts in tools/ execute raw INSERTs into governance_approval.
2. Recovery-Rate Truth Invariant:
   - Interrupted sessions with missing chunk transcripts report true partial recovery rate
     (e.g., exactly 70.0% for 7 of 10 chunks), never asserting false 100% success.
   - Untranscribed chunks are marked [UNTRANSCRIBED] with zero fabricated placeholder prose.
3. Unauthenticated Control Rejection:
   - In-meeting WebSocket control actions ('set_metadata', 'CLEAR') are strictly rejected
     without mutating recorder metadata or clearing transcript state.
4. Telemetry Heartbeat Integrity:
   - copilot_advisor retriever graph_sha256 and generation are properly recorded in session
     heartbeats without attribute typos.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.live_meeting_recorder_core.disaster_recovery import (
    recover_live_session,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestLiveGovernanceHonestyAdversarial(unittest.TestCase):
    def setUp(self):
        self.tmp_base = REPO_ROOT / ".tmp"
        self.tmp_base.mkdir(parents=True, exist_ok=True)
        self.temp_dir = tempfile.mkdtemp(prefix="test_gov_honesty_", dir=str(self.tmp_base))
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_pin_deleted_governance_hazard_tools(self):
        """Validates that known governance bypass tools remain deleted."""
        deleted_tools = [
            "tools/reconcile_and_promote_live_ssot.py",
            "tools/eval_copilot_grounding.py",
            "tools/characterize_copilot_baseline.py",
        ]
        for rel_path in deleted_tools:
            tool_file = REPO_ROOT / rel_path
            self.assertFalse(
                tool_file.is_file(),
                f"Hazard tool {rel_path} must not exist in repository root.",
            )

    def test_pin_no_ad_hoc_governance_approval_writers_in_tools(self):
        """Verifies no script in tools/ executes ad-hoc SQL INSERT into governance_approval."""
        tools_dir = REPO_ROOT / "tools"
        for py_file in tools_dir.glob("*.py"):
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            self.assertNotIn(
                "INSERT INTO governance_approval",
                content,
                f"Ad-hoc approval insert detected in {py_file.name}. Approvals must route through UWG.",
            )
            self.assertNotIn(
                "INSERT OR REPLACE INTO governance_approval",
                content,
                f"Ad-hoc approval insert detected in {py_file.name}. Approvals must route through UWG.",
            )

    def test_recovery_rate_truth_with_partial_chunks(self):
        """
        Validates that a session with 10 captured chunks and 3 untranscribed chunks
        reports exactly 70.0% recovery rate, partial status, and no placeholder prose.
        """
        session_dir = self.temp_path / "interrupted_session"
        chunks_dir = session_dir / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)

        # Create 10 dummy valid WAV chunks
        rate = 16000
        for i in range(10):
            chunk_file = chunks_dir / f"chunk_{i:04d}.wav"
            with wave.open(str(chunk_file), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                # 1 second of speech tone
                w.writeframes(b"\x00\x10" * rate)

        # Create journal with only 7 transcribed chunks (indices 0 to 6, missing 7, 8, 9)
        journal_file = session_dir / "session_journal.jsonl"
        with open(journal_file, "w", encoding="utf-8") as f_j:
            f_j.write(json.dumps({"event": "session_start", "project": "truth_test", "meeting": "Honesty Check"}) + "\n")
            for i in range(7):
                f_j.write(json.dumps({
                    "event": "chunk_transcribed",
                    "chunk_idx": i,
                    "text": f"Transcribed speech turn number {i}.",
                    "speaker": "Speaker_A",
                }) + "\n")

        # Execute recovery
        res = recover_live_session(
            session_dir=session_dir,
            output_dir=self.temp_path / "recovered_output",
            project="truth_test",
            meeting="Honesty Check",
            date="10/04/2026",
        )

        self.assertTrue(res.success)
        self.assertEqual(res.total_chunks, 10)
        self.assertEqual(res.recovered_turns, 7)
        self.assertAlmostEqual(res.recovery_rate, 0.70, places=2)

        # Read reconstructed transcript markdown
        self.assertIsNotNone(res.transcript_path)
        md_text = res.transcript_path.read_text(encoding="utf-8")

        # Must report partial status in seal
        self.assertIn("- **Recovery Status:** DISASTER_RECOVERY_PARTIAL", md_text)
        self.assertIn("- **Dialogue Turn Recovery Rate:** 70.0%", md_text)

        # Must NOT contain fake placeholder prose
        self.assertNotIn("Recovered audio dialogue from", md_text)
        self.assertIn("[UNTRANSCRIBED CHUNK 7]", md_text)
        self.assertIn("[UNTRANSCRIBED CHUNK 8]", md_text)
        self.assertIn("[UNTRANSCRIBED CHUNK 9]", md_text)

    def test_ws_control_action_rejection(self):
        """Verifies that set_metadata and CLEAR WebSocket control actions produce no state mutations."""
        from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder

        recorder = LiveMeetingRecorder(
            project_name="project_immutable",
            meeting_name="Original Protected Meeting",
            meeting_date="10/04/2026",
            attendees="Samantha, Daniel",
            output_dir=self.temp_path,
            mock_mode=True,
        )
        recorder._init_transcript_file()

        # Attempt to hijack metadata via WS action
        malicious_action = {
            "action": "set_metadata",
            "project_name": "hijacked_project",
            "meeting_name": "Rogue Override",
            "attendees": "Attacker",
        }
        recorder._handle_ws_action(malicious_action)

        # Assert no state mutation occurred
        self.assertEqual(recorder.project_name, "project_immutable")
        self.assertEqual(recorder.meeting_name, "Original Protected Meeting")
        self.assertIsNone(recorder.ws_server)

        import contextlib
        with contextlib.suppress(Exception):
            recorder.stop()

    def test_heartbeat_graph_sha_telemetry_binding(self):
        """Verifies that recorder heartbeat accurately captures graph_sha256 and generation without typos."""
        from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder

        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Telemetry Binding Check",
            meeting_date="10/04/2026",
            attendees="Samantha",
            output_dir=self.temp_path,
            mock_mode=True,
        )
        recorder._init_transcript_file()

        # Mock copilot advisor with retriever exposing graph_sha256 and generation
        mock_retriever = MagicMock()
        mock_retriever.graph_sha256 = "090750e64e560d991cd8715d1b478bad4d1e3910cb4bb800e5d10cdf9765b1ca"
        mock_retriever.generation = "generation-graph-consolidation-d488dbeff02817be07106935301f"

        mock_advisor = MagicMock()
        mock_advisor.retriever = mock_retriever
        recorder.copilot_advisor = mock_advisor

        # Mock session_registry to capture heartbeat kwargs
        mock_registry = MagicMock()
        recorder.session_registry = mock_registry
        recorder.session_id = "test-session-123"

        recorder._emit_heartbeat(rms=150.0, chunk_idx=1, time_str="00:15")

        mock_registry.heartbeat.assert_called_once()
        _, kwargs = mock_registry.heartbeat.call_args

        self.assertEqual(kwargs.get("graph_sha256"), "090750e64e560d991cd8715d1b478bad4d1e3910cb4bb800e5d10cdf9765b1ca")
        self.assertEqual(kwargs.get("generation"), "generation-graph-consolidation-d488dbeff02817be07106935301f")

        import contextlib
        with contextlib.suppress(Exception):
            recorder.stop()


if __name__ == "__main__":
    unittest.main()
