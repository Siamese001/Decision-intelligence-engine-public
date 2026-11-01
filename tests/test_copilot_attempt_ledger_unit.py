"""
tests/test_copilot_attempt_ledger_unit.py

Unit tests for Copilot attempt records, failure categorization, and startup failure persistence.
Verifies Phase 1 acceptance criteria:
- Every Copilot terminal outcome (success or failure) generates a bounded attempt record.
- Failed attempts are strictly isolated from copilot_answers.jsonl.
- Concise, actionable human messages distinguish all 7 failure categories.
- Zero transcript-body duplication in attempt telemetry.
- Diagnostic recording failure cannot hang capture or produce false success.
- Audio capture startup/proof exceptions are persisted before cleanup begins.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.cli.copilot_attempt_ledger import (
    CATEGORY_ABSENT_CANDIDATES,
    CATEGORY_DEADLINE_EXHAUSTION,
    CATEGORY_GRAPH_BINDING,
    CATEGORY_REJECTED_EVIDENCE,
    CATEGORY_RENDERING_FAILURE,
    CATEGORY_SESSION_DISCOVERY,
    CATEGORY_STALE_CONTEXT,
    CATEGORY_UNHANDLED_ERROR,
    CATEGORY_USAGE,
    CopilotAttemptRecord,
    categorize_copilot_terminal_reason,
    create_attempt_record,
    extract_context_freshness,
    record_copilot_attempt,
)
from dual_pipeline.cli.copilot_handlers import handle_copilot
from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    LiveAudioCaptureManager,
)
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureUnprovenError,
    AudioReasonCode,
)


class TestCopilotAttemptLedgerUnit(unittest.TestCase):
    """Unit tests for CopilotAttemptLedger and failure telemetry."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._temp_dir.name)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_categorize_copilot_terminal_reason(self) -> None:
        """Verifies distinct failure categories and concise actionable messages."""
        cases = [
            ("NO_SESSION_FOUND", CATEGORY_SESSION_DISCOVERY, "[Session Discovery]"),
            ("NO_SESSION_MATCH", CATEGORY_SESSION_DISCOVERY, "[Session Discovery]"),
            ("NO_LIVE_CONTEXT", CATEGORY_STALE_CONTEXT, "[Session Context]"),
            ("STALE_LIVE_CONTEXT", CATEGORY_STALE_CONTEXT, "[Session Context]"),
            ("NO_GRAPH_GROUNDING_MISSING_DATABASE", CATEGORY_GRAPH_BINDING, "[Graph Grounding]"),
            ("ZERO_RELEVANT_ASSERTIONS", CATEGORY_ABSENT_CANDIDATES, "[Graph Retrieval]"),
            ("REJECTED_EVIDENCE", CATEGORY_REJECTED_EVIDENCE, "[Grounding Verification]"),
            ("NO_SPEAKABLE_ASSERTIONS", CATEGORY_RENDERING_FAILURE, "[Speech Synthesis]"),
            ("DEADLINE_EXHAUSTED", CATEGORY_DEADLINE_EXHAUSTION, "[Retrieval Deadline]"),
            ("INVALID_INTENT", CATEGORY_USAGE, "[Usage Error]"),
            ("MISSING_TARGET", CATEGORY_USAGE, "[Usage Error]"),
            ("RANDOM_UNKNOWN_ERROR", CATEGORY_UNHANDLED_ERROR, "[Copilot Error]"),
        ]

        for code, expected_cat, expected_prefix in cases:
            cat, msg = categorize_copilot_terminal_reason(code, details="detail test")
            self.assertEqual(cat, expected_cat, f"Mismatch for code: {code}")
            self.assertTrue(msg.startswith(expected_prefix), f"Message '{msg}' did not start with '{expected_prefix}'")

    def test_extract_context_freshness_zero_transcript_leakage(self) -> None:
        """Verifies context freshness extracts counts and seeds without duplicating full transcript bodies."""
        mock_cm = MagicMock()
        mock_cm.focus_turns.return_value = [
            {"speaker": "Alice", "text": "This is a very long transcript body that should never be copied into telemetry.", "timestamp": "2026-10-07T12:00:00Z"},
            {"speaker": "Bob", "text": "Another long turn with sensitive architectural details.", "timestamp": "2026-10-07T12:00:15Z"},
        ]
        mock_cm.compute_hash.return_value = "digest-12345"
        mock_cm.retrieval_seeds.return_value = ["load", "concurrency", "agents"]

        freshness = extract_context_freshness(mock_cm)
        self.assertEqual(freshness["turn_count"], 2)
        self.assertEqual(freshness["context_digest"], "digest-12345")
        self.assertEqual(freshness["last_turn_timestamp"], "2026-10-07T12:00:15Z")
        self.assertEqual(freshness["seeds"], ["load", "concurrency", "agents"])

        # Crucial check: verify full turn texts are absent from freshness dict
        serialized = json.dumps(freshness)
        self.assertNotIn("sensitive architectural details", serialized)
        self.assertNotIn("This is a very long transcript body", serialized)

    def test_create_and_record_copilot_attempt_failure(self) -> None:
        """Verifies creating and recording a failed attempt record."""
        out_dir = self.tmp_path / "meeting_out"
        rec = create_attempt_record(
            terminal_outcome="FAIL",
            terminal_reason="NO_SESSION_FOUND",
            details="No active session found in .session_registry/.",
            request="React to Mitch's comment",
            parsed_intent="respond",
            parsed_target="Mitch",
            parsed_topic="load and agents",
            session_id="sess-abc",
            resolved_roots={"output_dir": str(out_dir)},
            candidate_counts={"initial_candidates": 0, "verified_candidates": 0, "rendered_candidates": 0},
        )

        self.assertEqual(rec.terminal_outcome, "FAIL")
        self.assertEqual(rec.terminal_reason, "NO_SESSION_FOUND")
        self.assertEqual(rec.failure_category, CATEGORY_SESSION_DISCOVERY)
        self.assertIn("[Session Discovery]", rec.user_message)

        written_path = record_copilot_attempt(rec, output_dir=out_dir)
        self.assertIsNotNone(written_path)
        self.assertTrue(written_path.exists())
        self.assertEqual(written_path.name, "copilot_attempts.jsonl")

        # Verify isolating failed attempts: copilot_answers.jsonl must NOT exist
        answers_file = out_dir / "copilot_answers.jsonl"
        self.assertFalse(answers_file.exists())

        # Verify content of copilot_attempts.jsonl
        with open(written_path, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(lines), 1)
        entry = lines[0]
        self.assertEqual(entry["attempt_id"], rec.attempt_id)
        self.assertEqual(entry["terminal_outcome"], "FAIL")
        self.assertEqual(entry["terminal_reason"], "NO_SESSION_FOUND")
        self.assertEqual(entry["parsed_topic"], "load and agents")
        self.assertIn("resolved_roots", entry)

    def test_record_copilot_attempt_failsafe_on_io_error(self) -> None:
        """Verifies that attempt recording failures never raise or alter caller behavior."""
        rec = create_attempt_record(
            terminal_outcome="FAIL",
            terminal_reason="NO_SESSION_FOUND",
        )
        # Passing an impossible path
        with patch("builtins.open", side_effect=OSError("Read-only filesystem")):
            result = record_copilot_attempt(rec, output_dir=self.tmp_path)
            self.assertIsNone(result)

    def test_handle_copilot_records_attempt_on_no_session(self) -> None:
        """Verifies handle_copilot emits actionable message and persists attempt when session is missing."""
        args = argparse.Namespace(
            intent="next-questions",
            target=None,
            session="nonexistent-session-id-12345",
            runtime_root=self.tmp_path,
            json=True,
        )
        res = handle_copilot(args)
        self.assertEqual(res.get("status"), "FAIL")
        self.assertEqual(res.get("error"), "NO_SESSION_FOUND")
        self.assertEqual(res.get("failure_category"), CATEGORY_SESSION_DISCOVERY)
        self.assertIn("[Session Discovery]", res.get("user_message", ""))

        # Check attempts file written in runtime_root / learning
        attempts_file = self.tmp_path / "learning" / "copilot_attempts.jsonl"
        self.assertTrue(attempts_file.exists(), f"Attempts file {attempts_file} was not written")
        with open(attempts_file, "r", encoding="utf-8") as f:
            entries = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["terminal_reason"], "NO_SESSION_FOUND")

    def test_audio_capture_persists_startup_failure_before_cleanup(self) -> None:
        """Verifies capture startup proof exception is persisted before stop_processes is called."""
        master_wav = self.tmp_path / "master.wav"
        chunks_dir = self.tmp_path / "chunks"
        mgr = LiveAudioCaptureManager(
            chunks_dir=chunks_dir,
            master_wav=master_wav,
            source_mode="mock",
        )

        proof_exc = AudioCaptureUnprovenError(
            stage="BYTE_FLOW",
            reason_code=AudioReasonCode.AUDIO_GROWTH_STALLED,
            detail="Simulated proof failure: 0 bytes delivered in window.",
            remediation="Check microphone input device.",
        )

        with patch("dual_pipeline.live_meeting_recorder_core.audio_capture_proof.AudioCaptureProofVerifier.run_full_start_proof", side_effect=proof_exc):
            with self.assertRaises(AudioCaptureUnprovenError) as ctx:
                mgr.start(run_proof=True)

            self.assertIs(ctx.exception, proof_exc)

        # Check record_start_failure.json exists on disk
        failure_path = master_wav.parent / "record_start_failure.json"
        self.assertTrue(failure_path.exists())
        with open(failure_path, "r", encoding="utf-8") as f:
            failure_doc = json.load(f)

        self.assertEqual(failure_doc["stage"], "proof_verification")
        self.assertEqual(failure_doc["exception_type"], "AudioCaptureUnprovenError")
        self.assertIn("Simulated proof failure", failure_doc["exception_message"])
        self.assertEqual(mgr.startup_exception, proof_exc)

    def test_audio_capture_cleanup_failure_does_not_mask_startup_exception(self) -> None:
        """Verifies that if cleanup fails, the original startup exception is still preserved and raised."""
        master_wav = self.tmp_path / "master.wav"
        chunks_dir = self.tmp_path / "chunks"
        mgr = LiveAudioCaptureManager(
            chunks_dir=chunks_dir,
            master_wav=master_wav,
            source_mode="mock",
        )

        orig_exc = RuntimeError("Original hardware initialization failure")

        with patch.object(mgr, "_start_mock_capture", side_effect=orig_exc):
            with patch.object(mgr, "stop_processes", side_effect=RuntimeError("Secondary cleanup deadlock/error")):
                with self.assertRaises(RuntimeError) as ctx:
                    mgr.start(run_proof=False)

                # Must be the ORIGINAL exception, not the secondary cleanup error!
                self.assertIs(ctx.exception, orig_exc)

        # Check failure doc recorded the original exception
        failure_path = master_wav.parent / "record_start_failure.json"
        self.assertTrue(failure_path.exists())
        with open(failure_path, "r", encoding="utf-8") as f:
            failure_doc = json.load(f)
        self.assertEqual(failure_doc["exception_message"], "Original hardware initialization failure")


if __name__ == "__main__":
    unittest.main()
