"""
tests/adversarial/test_wave1_safety_and_integrity_adversarial.py

Adversarial edge-case tests covering:
1. Malformed audio containers (fuzzed headers, truncated RIFF, sample rate overflow, invalid channels)
2. Concurrency races and lock collision attempts
3. Source file integrity bypass attempts (zero-byte files, digest tampering)
4. Cryptographic mutation signature forgery in GovernedGraphWriter
5. Bipartite namespace breach tamper logging
"""
from __future__ import annotations

import json
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.audio_ingestion_core.container_validator import (
    validate_audio_container_headers,
)
from dual_pipeline.canonical_pipeline.concurrency import MeetingIngestionLock
from dual_pipeline.canonical_pipeline.content_loader import load_canonical_source_content
from dual_pipeline.exceptions import (
    AudioContainerValidationError,
    ConcurrentIngestionError,
    GraphMutationSecurityError,
    SourceIntegrityError,
)
from dual_pipeline.graph_writer_core.writer import GovernedGraphWriter
from dual_pipeline.telemetry_core.events import record_bipartite_leak_event


class TestWave1AdversarialSecurityAndIntegrity(unittest.TestCase):
    """Adversarial security and integrity tests."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_malformed_wav_truncated_header_rejected(self):
        """Adversarial input: WAV file with truncated header (< 44 bytes)."""
        bad_wav = self.tmp_path / "truncated.wav"
        bad_wav.write_bytes(b"RIFF\x10\x00\x00\x00WAVEfmt ")  # Only 16 bytes

        with self.assertRaises(AudioContainerValidationError) as cm:
            validate_audio_container_headers(bad_wav)
        self.assertIn("too small", str(cm.exception))

    def test_malformed_wav_invalid_magic_bytes_rejected(self):
        """Adversarial input: Non-RIFF file with .wav extension."""
        bad_wav = self.tmp_path / "fake.wav"
        bad_wav.write_bytes(b"NOT_A_RIFF_HEADER" + b"\x00" * 100)

        with self.assertRaises(AudioContainerValidationError) as cm:
            validate_audio_container_headers(bad_wav)
        self.assertIn("Invalid magic bytes", str(cm.exception))

    def test_malformed_wav_sample_rate_overflow_rejected(self):
        """Adversarial input: WAV with illegal sample rate (e.g. 500,000 Hz overflow attempt)."""
        bad_wav = self.tmp_path / "rate_overflow.wav"
        fmt_chunk = struct.pack("<HHIIHH", 1, 2, 500000, 500000 * 4, 4, 16)
        header = b"RIFF" + struct.pack("<I", 36 + len(fmt_chunk)) + b"WAVE" + b"fmt " + struct.pack("<I", len(fmt_chunk)) + fmt_chunk
        bad_wav.write_bytes(header + b"\x00" * 100)

        with self.assertRaises(AudioContainerValidationError) as cm:
            validate_audio_container_headers(bad_wav)
        self.assertIn("outside safe bounds", str(cm.exception))

    def test_malformed_wav_excessive_channels_rejected(self):
        """Adversarial input: WAV with 128 channels (exceeding channel count <= 8)."""
        bad_wav = self.tmp_path / "excess_channels.wav"
        fmt_chunk = struct.pack("<HHIIHH", 1, 128, 16000, 16000 * 256, 256, 16)
        header = b"RIFF" + struct.pack("<I", 36 + len(fmt_chunk)) + b"WAVE" + b"fmt " + struct.pack("<I", len(fmt_chunk)) + fmt_chunk
        bad_wav.write_bytes(header + b"\x00" * 100)

        with self.assertRaises(AudioContainerValidationError) as cm:
            validate_audio_container_headers(bad_wav)
        self.assertIn("channel count", str(cm.exception).lower())

    def test_concurrency_race_condition_adversarial(self):
        """Adversarial concurrency: 10 threads racing to acquire the same meeting lock."""
        lock_dir = self.tmp_path / "locks"
        meeting_key = "contested_meeting_lock"
        success_count = 0
        failure_count = 0
        lock = threading.Lock()

        def worker():
            nonlocal success_count, failure_count
            ingest_lock = MeetingIngestionLock(meeting_key, lock_dir=lock_dir)
            try:
                ingest_lock.acquire()
                with lock:
                    success_count += 1
                import time
                time.sleep(0.05)
                ingest_lock.release()
            except ConcurrentIngestionError:
                with lock:
                    failure_count += 1

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertGreaterEqual(success_count, 1)
        self.assertGreaterEqual(failure_count, 1)

    def test_zero_byte_source_file_rejected_adversarial(self):
        """Adversarial input: Zero-byte file supplied as meeting input."""
        empty_file = self.tmp_path / "empty.txt"
        empty_file.write_text("", encoding="utf-8")

        with self.assertRaises(SourceIntegrityError) as cm:
            load_canonical_source_content(empty_file, {})
        self.assertIn("empty (0 bytes)", str(cm.exception))

    def test_tampered_source_file_sha256_rejected_adversarial(self):
        """Adversarial input: Source file whose content does not match expected_sha256."""
        f = self.tmp_path / "tampered.txt"
        f.write_text("Legitimate transcript content", encoding="utf-8")

        meta = {"expected_sha256": "0000000000000000000000000000000000000000000000000000000000000000"}
        with self.assertRaises(SourceIntegrityError) as cm:
            load_canonical_source_content(f, meta)
        self.assertIn("verification failed", str(cm.exception))

    def test_graph_mutation_forged_signature_rejected_adversarial(self):
        """Adversarial input: Forged or invalid cryptographic signature on graph mutation."""
        db_path = self.tmp_path / "fake_graph.sqlite"
        db_path.write_bytes(b"")  # placeholder
        writer = GovernedGraphWriter(db_path)

        change_set = {
            "status": "approved",
            "approval_id": "app_999",
            "base_graph_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "operations": [{"operation_id": "op_1", "operation": "add_candidate_assertion", "target_id": "as_1"}],
            "extensions": {
                "com.partner_advisory.nee.source": {"source_id": "s1"},
                "com.partner_advisory.nee.operation-payloads": {"op_1": {}},
                "com.partner_advisory.nee.operation-contexts": {"op_1": {}},
            },
            "signature": {
                "signer_key_id": "key_evil",
                "signature": "forged_base64_sig",
                "digest": "deadbeef" * 8,  # Does not match computed digest
            },
        }

        from unittest.mock import patch
        with patch("dual_pipeline.graph_writer_core.writer.validate_artifact"):
            with self.assertRaises(GraphMutationSecurityError) as cm:
                writer.apply(change_set)
            self.assertIn("Signature digest mismatch", str(cm.exception))

    def test_bipartite_namespace_leak_security_audit_logging(self):
        """Verifies that bipartite leak events create an immutable audit log entry."""
        mock_span = MagicMock()
        record_bipartite_leak_event(
            mock_span,
            requested_namespace="CLIENT_SAFE",
            leaked_node_id="internal_stakeholder_salary_tier",
            node_tags=["AGENTIC_LEVERAGE", "CONFIDENTIAL"],
        )

        mock_span.add_event.assert_called_once()
        mock_span.set_status.assert_called_once()

        from dual_pipeline.core import ROOT
        audit_file = ROOT / ".local" / "dual_pipeline" / "security_audit.jsonl"
        self.assertTrue(audit_file.is_file())
        last_line = audit_file.read_text(encoding="utf-8").strip().splitlines()[-1]
        audit_record = json.loads(last_line)
        self.assertEqual(audit_record["event"], "BIPARTITE_NAMESPACE_LEAK")
        self.assertEqual(audit_record["leaked_node_id"], "internal_stakeholder_salary_tier")
        self.assertEqual(audit_record["severity"], "CRITICAL")
        self.assertIn("entry_digest", audit_record)


if __name__ == "__main__":
    unittest.main()
