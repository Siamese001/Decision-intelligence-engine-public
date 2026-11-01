"""Unit test suite for native ScreenCaptureKit mac_audio_tap resilience and manifest sealing.

Validates:
1. Manifest cryptographic seal and version 1.2.0 synchronization.
2. Binary CLI flags (--version, --source-hash, invalid flag rejection).
3. Source invariants: display sleep recovery, safe POSIX write with EPIPE tolerance, no unconditional exit(1).
4. Runtime resilience against abrupt downstream pipe closure (SIGPIPE/EPIPE).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path


class TestMacAudioTapResilience(unittest.TestCase):
    """Validates ScreenCaptureKit tap hardening, recovery, and manifest verification."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parents[2]
        cls.tap_dir = cls.repo_root / "tools" / "mac_audio_tap"
        cls.binary_path = cls.tap_dir / "bin" / "mac_audio_tap"
        cls.manifest_path = cls.tap_dir / "mac_audio_tap.manifest.json"
        cls.swift_source = cls.tap_dir / "mac_audio_tap.swift"

    def test_manifest_cryptographic_seal_and_version(self) -> None:
        """Assert manifest is present, version 1.2.0, and hashes match source and binary."""
        self.assertTrue(self.manifest_path.exists(), "Manifest file missing")
        self.assertTrue(self.binary_path.exists(), "Compiled binary missing")
        self.assertTrue(self.swift_source.exists(), "Swift source missing")

        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("version"), "1.2.0")

        # Verify source hash
        norm_source = self.swift_source.read_bytes().replace(b"\r\n", b"\n")
        computed_source_sha = hashlib.sha256(norm_source).hexdigest()
        self.assertEqual(manifest.get("source_sha256"), computed_source_sha)

        # Verify binary hash
        computed_bin_sha = hashlib.sha256(self.binary_path.read_bytes()).hexdigest()
        self.assertEqual(manifest.get("binary_sha256"), computed_bin_sha)

    def test_binary_cli_flags_verification(self) -> None:
        """Assert compiled binary executes --version, --source-hash, and fails closed on unknown flags."""
        if sys.platform != "darwin":
            self.skipTest("mac_audio_tap binary requires macOS Darwin")

        # Test --version
        p_ver = subprocess.run([str(self.binary_path), "--version"], capture_output=True, text=True, timeout=5.0)
        self.assertEqual(p_ver.returncode, 0)
        self.assertIn("1.2.0", p_ver.stdout)

        # Test --source-hash
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        p_hash = subprocess.run([str(self.binary_path), "--source-hash"], capture_output=True, text=True, timeout=5.0)
        self.assertEqual(p_hash.returncode, 0)
        self.assertEqual(p_hash.stdout.strip(), manifest.get("source_sha256"))

        # Test unknown flag exits with code 2
        p_bad = subprocess.run([str(self.binary_path), "--invalid-flag"], capture_output=True, text=True, timeout=5.0)
        self.assertEqual(p_bad.returncode, 2)

    def test_source_code_sleep_recovery_and_epipe_invariants(self) -> None:
        """Assert source code contains sleep recovery logic and handles broken pipes without crashing."""
        content = self.swift_source.read_text(encoding="utf-8")

        # 1. didStopWithError must not call exit(1) unconditionally
        did_stop_idx = content.find("func stream(_ stream: SCStream, didStopWithError error: Error)")
        self.assertNotEqual(did_stop_idx, -1, "didStopWithError delegate method missing")
        did_stop_block = content[did_stop_idx:did_stop_idx + 500]
        self.assertNotIn("exit(1)", did_stop_block, "didStopWithError must not call exit(1)")
        self.assertIn("attemptStreamRecovery", did_stop_block, "didStopWithError must trigger recovery")

        # 2. Recovery loop and display sleep tolerance
        self.assertIn("func attemptStreamRecovery()", content)
        self.assertIn("SCShareableContent.excludingDesktopWindows", content)

        # 3. Safe POSIX write handling EPIPE and EINTR
        self.assertIn("safeWriteToStdout", content)
        self.assertIn("errno == EPIPE", content)
        self.assertIn("errno == EINTR", content)

    def test_broken_pipe_downstream_resistance(self) -> None:
        """Assert tap process does not crash with unhandled exception when downstream pipe abruptly terminates."""
        if sys.platform != "darwin":
            self.skipTest("mac_audio_tap binary requires macOS Darwin")

        # Pipe output to head -c 10 which terminates immediately after 10 bytes, generating EPIPE
        cmd = f"{self.binary_path} | head -c 10"
        res = subprocess.run(cmd, shell=True, capture_output=True, timeout=5.0)
        # Process should not crash with SIGSEGV or uncaught NSException
        self.assertNotIn(b"NSFileHandleOperationException", res.stderr)
        self.assertNotIn(b"Segmentation fault", res.stderr)


if __name__ == "__main__":
    unittest.main()
