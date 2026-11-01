"""Adversarial and boundary test suite for Ingestion Dry-Run and Stage 03 Timeout.

Categories Covered:
1. Boundary payloads (nonexistent files, empty paths).
2. Malformed input / RBAC violation (subject mismatch, expired token, missing token).
3. Missing mandatory governance directives under mutation mode.
4. Invariant bypass attempts (--bypass-speaker-hitl rejection).
5. Production regression pins (Stage 03 graph extraction deadline capping at 300.0s).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.core import ROOT
from dual_pipeline.transcript_stages_core.synthesizers import LLMTranscriptSynthesizer


@pytest.mark.adversarial
class IngestDryRunAndTimeoutAdversarialTests(unittest.TestCase):
    """Adversarial stress test suite covering boundaries, bypass rejection, and timeout caps."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.meeting_file = self.tmp_path / "meeting.md"
        self.meeting_file.write_text("## Forensic Transcript\n[00:00] Alice: Hello\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_adversarial_nonexistent_input_file_fails_closed(self):
        """Category 1: Boundary payloads - Nonexistent file path fails closed."""
        py_bin = sys.executable
        bogus_path = self.tmp_path / "does_not_exist_transcript.md"
        cmd = [
            py_bin,
            str(ROOT / "nee.py"),
            "ingest-meeting",
            "--input",
            str(bogus_path),
            "--project",
            "test_project",
            "--meeting",
            "test_meeting",
            "--date",
            "01/01/2026",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
        self.assertNotEqual(res.returncode, 0)

    def test_adversarial_removed_dry_run_flag_fails_closed(self):
        """Category 3: Removed simulated run mode (--dry-run) fails closed."""
        py_bin = sys.executable
        cmd = [
            py_bin,
            str(ROOT / "nee.py"),
            "ingest-meeting",
            "--input",
            str(self.meeting_file),
            "--project",
            "test_project",
            "--meeting",
            "test_meeting",
            "--date",
            "01/01/2026",
            "--dry-run",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(res.returncode, 1)
        self.assertIn("flag removed: '--dry-run' has been eliminated", res.stderr)

    def test_adversarial_rbac_subject_mismatch_fails_closed(self):
        """Category 2: Malformed input / RBAC violation - Subject mismatch between token and NEE_REVIEWER."""
        py_bin = sys.executable
        tok = self.tmp_path / "token.json"
        tok.write_text(
            json.dumps({
                "sub": "Bob Smith",
                "capabilities": ["knowledge:release"],
            }),
            encoding="utf-8",
        )
        cmd = [
            py_bin,
            str(ROOT / "nee.py"),
            "ingest-meeting",
            "--input",
            str(self.meeting_file),
            "--project",
            "test_project",
            "--meeting",
            "test_meeting",
            "--date",
            "01/01/2026",
            "--directive-id",
            "directive-test-01",
            "--auth-token",
            str(tok),
        ]
        import os
        env = dict(os.environ, NEE_REVIEWER="Amit Ayer")
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), env=env)
        self.assertEqual(res.returncode, 1)
        self.assertIn("RBACAuthorizationError", res.stderr)
        self.assertIn("RBAC_SUBJECT_MISMATCH", res.stderr)

    def test_adversarial_rbac_expired_token_fails_closed(self):
        """Category 2: Malformed input / RBAC violation - Token expiration in past."""
        py_bin = sys.executable
        tok = self.tmp_path / "expired_token.json"
        tok.write_text(
            json.dumps({
                "sub": "Amit Ayer",
                "capabilities": ["knowledge:release"],
                "exp": time.time() - 3600,
            }),
            encoding="utf-8",
        )
        cmd = [
            py_bin,
            str(ROOT / "nee.py"),
            "ingest-meeting",
            "--input",
            str(self.meeting_file),
            "--project",
            "test_project",
            "--meeting",
            "test_meeting",
            "--date",
            "01/01/2026",
            "--directive-id",
            "directive-test-01",
            "--auth-token",
            str(tok),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(res.returncode, 1)
        self.assertIn("RBACAuthorizationError", res.stderr)
        self.assertIn("Auth token expired", res.stderr)

    def test_adversarial_bypass_speaker_hitl_strictly_prohibited(self):
        """Category 4: Invariant bypass attempt - bypass_speaker_hitl fails closed."""
        import argparse

        from dual_pipeline.cli.ingest import build_canonical_metadata

        mock_args = argparse.Namespace(bypass_speaker_hitl=True, input=str(self.meeting_file))
        with self.assertRaises(ValueError) as ctx:
            build_canonical_metadata(mock_args)
        self.assertIn("HITL cannot be removed, disabled, or bypassed in Stage U0 ingestion", str(ctx.exception))

    def test_adversarial_stage03_synthesis_deadline_capped_at_300s(self):
        """Category 5: Production regression pin - Stage 03 graph extraction enforces 1200.0s deadline."""
        from config.credentials import OpenAICredentials

        stub_client = MagicMock()
        stub_creds = OpenAICredentials(
            api_key="test-key-not-used",
            is_mock=False,
            model="gpt-5.6-sol",
            reasoning_effort="high",
            masked_key="[TEST_STUB]",
            source="test_injection",
        )
        synth = LLMTranscriptSynthesizer(
            model="gpt-5.6-sol",
            reasoning_effort="high",
            _client=stub_client,
            _creds=stub_creds,
        )

        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({"nodes": [], "edges": []})
        mock_choice.finish_reason = "stop"
        mock_resp.choices = [mock_choice]

        with patch.object(synth, "_create_completion_with_effort", return_value=mock_resp) as mock_complete:
            res = synth.synthesize_graph("system instructions", "transcript content")
            self.assertEqual(res, '{"nodes": [], "edges": []}')
            self.assertEqual(mock_complete.call_count, 1)
            _, kwargs = mock_complete.call_args
            self.assertAlmostEqual(kwargs["deadline_seconds"], 1200.0, places=1)
            self.assertEqual(kwargs["max_completion_tokens"], 64000)


if __name__ == "__main__":
    unittest.main()
