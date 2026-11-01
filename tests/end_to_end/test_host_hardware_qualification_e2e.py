"""
tests/end_to_end/test_host_hardware_qualification_e2e.py

Wave 6 End-to-End Test Suite: Host & Hardware Qualification Lifecycle.
Verifies:
1. Full canary execution across preceding receipts (Waves 1-5).
2. Host environment telemetry on Apple Silicon hardware.
3. Controlled tool replay across Gemini Pro and Flash with zero chat replies.
4. Cryptographic receipt sealing with canonical RFC-8259 digest.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.qualify_hardware_canary import run_hardware_canary


class TestHostHardwareQualificationE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.sandbox_root = Path(self.tmp_dir.name)
        self.reports_dir = self.sandbox_root / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_e2e_hardware_canary_qualification_lifecycle(self) -> None:
        """E2E lifecycle: verifies receipt chaining, host telemetry, model parity, and receipt sealing."""
        # Setup actual workspace receipts reference or link
        workspace_root = Path(__file__).resolve().parent.parent.parent
        out_receipt = self.reports_dir / "hardware_canary_qualification_receipt.json"

        report = run_hardware_canary(
            repo_root=workspace_root,
            warm_samples=5,
            cold_samples=2,
            out_path=out_receipt,
        )

        self.assertTrue(report.overall_qualification_passed)
        self.assertTrue(report.all_preceding_receipts_passed)
        self.assertTrue(report.rollback_policy_verified)
        self.assertIsNotNone(report.receipt_sha256)
        self.assertEqual(len(report.receipt_sha256), 64)

        # Host inspection assertions
        host = report.host
        self.assertEqual(host.platform_name, "darwin")
        self.assertTrue(host.is_apple_silicon)

        # Mock topology assertions
        self.assertIn("mock_isolated", report.topologies)
        mock_top = report.topologies["mock_isolated"]
        self.assertEqual(mock_top.total_attempts, 7)
        self.assertEqual(mock_top.successful_starts, 7)
        self.assertEqual(mock_top.failed_starts, 0)
        self.assertEqual(mock_top.missing_pcm_count, 0)
        self.assertTrue(mock_top.is_hardware_distinct)

        # Model comparison matrix assertions
        self.assertEqual(len(report.model_comparison), 2)
        for mc in report.model_comparison:
            self.assertLess(mc.dispatch_latency_ms, 250.0)
            self.assertTrue(mc.silent_hint_intake_success)
            self.assertEqual(mc.chat_replies_emitted, 0)
            self.assertTrue(mc.capture_ownership_retained)

        # Validate serialized receipt on disk
        self.assertTrue(out_receipt.exists())
        saved_data = json.loads(out_receipt.read_text(encoding="utf-8"))
        self.assertEqual(saved_data["receipt_sha256"], report.receipt_sha256)


if __name__ == "__main__":
    unittest.main()
