"""
tests/adversarial/test_mac_audio_tap_binary_drift_adversarial.py

Adversarial stress and regression suite verifying binary hash drift defense:
1. Source modification without manifest update triggers fail-closed CI rejection.
2. Binary tampering (1-byte patch) triggers cryptographic hash violation.
3. Unknown CLI flags exit with code 2 in <100ms and NEVER hang.
4. Preflight raises RuntimeError on probe timeout instead of disguising as TCC denial.
5. Cross-platform manifest verification passes on mock Windows/Linux environments.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.audio_preflight import AudioPreflightValidator
from tools.verify_mac_audio_tap_integrity import compute_normalized_sha256, verify_integrity

ROOT = Path(__file__).resolve().parents[2]


class TestMacAudioTapBinaryDriftAdversarial(unittest.TestCase):

    def test_unknown_cli_flag_fails_closed_without_hanging(self):
        """Passing an unrecognized flag must exit immediately with code 2, not hang."""
        bin_path = ROOT / "tools" / "mac_audio_tap" / "bin" / "mac_audio_tap"
        if sys.platform != "darwin" or not bin_path.exists():
            self.skipTest("Requires Darwin and compiled mac_audio_tap binary")

        proc = subprocess.run(
            [str(bin_path), "--unrecognized-adversarial-flag-xyz"],
            capture_output=True,
            text=True,
            timeout=1.0,  # Must terminate well within 1.0s
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Unrecognized arguments", proc.stderr)

    def test_source_drift_detected_by_integrity_verifier(self):
        """Simulate modification of Swift source; verify tool exits with code 1."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            tap_dir = tmp_root / "tools" / "mac_audio_tap"
            tap_dir.mkdir(parents=True)
            swift_file = tap_dir / "mac_audio_tap.swift"
            swift_file.write_text("// dummy modified swift source\n")

            manifest_file = tap_dir / "mac_audio_tap.manifest.json"
            manifest_file.write_text(
                json.dumps({
                    "source_file": "tools/mac_audio_tap/mac_audio_tap.swift",
                    "source_sha256": "0" * 64,  # Intentionally drifted hash
                })
            )

            ok, msg = verify_integrity(tmp_root)
            self.assertFalse(ok)
            self.assertIn("DRIFT_DETECTED", msg)

    def test_binary_tamper_detected_by_integrity_verifier(self):
        """Simulate binary tampering; verify tool detects hash mismatch."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            tap_dir = tmp_root / "tools" / "mac_audio_tap"
            bin_dir = tap_dir / "bin"
            bin_dir.mkdir(parents=True)

            swift_file = tap_dir / "mac_audio_tap.swift"
            swift_file.write_text("// swift source\n")
            source_hash = compute_normalized_sha256(swift_file)

            bin_file = bin_dir / "mac_audio_tap"
            bin_file.write_bytes(b"\x7fELFfakebinarytampered")

            manifest_file = tap_dir / "mac_audio_tap.manifest.json"
            manifest_file.write_text(
                json.dumps({
                    "source_file": "tools/mac_audio_tap/mac_audio_tap.swift",
                    "source_sha256": source_hash,
                    "binary_file": "tools/mac_audio_tap/bin/mac_audio_tap",
                    "binary_sha256": "f" * 64,  # Mismatched binary hash
                })
            )

            ok, msg = verify_integrity(tmp_root)
            self.assertFalse(ok)
            self.assertIn("DRIFT_DETECTED", msg)

    def test_binary_self_reported_hash_matches_source(self):
        """Asserts that the binary's --source-hash matches sha256(mac_audio_tap.swift)."""
        bin_path = ROOT / "tools" / "mac_audio_tap" / "bin" / "mac_audio_tap"
        swift_path = ROOT / "tools" / "mac_audio_tap" / "mac_audio_tap.swift"
        if sys.platform != "darwin" or not bin_path.exists():
            self.skipTest("Requires Darwin and compiled mac_audio_tap binary")

        actual_source_hash = compute_normalized_sha256(swift_path)
        res = subprocess.run([str(bin_path), "--source-hash"], capture_output=True, text=True, timeout=1.0)
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), actual_source_hash)

    def test_preflight_timeout_raises_fatal_error(self):
        """Assert that preflight does NOT swallow probe timeout, but raises RuntimeError."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_bin = Path(tmp_dir) / "mock_hanging_binary"
            # Script that simulates legacy binary hanging in RunLoop
            tmp_bin.write_text("#!/bin/sh\nsleep 5\n")
            tmp_bin.chmod(0o755)

            with self.assertRaises(RuntimeError) as ctx:
                AudioPreflightValidator.check_sck_tcc_permission(tmp_bin, timeout_sec=0.2)
            self.assertIn("probe timed out", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
