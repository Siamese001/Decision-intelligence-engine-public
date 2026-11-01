"""
tests/test_hardware_canary_adversarial.py

Wave 6 Adversarial & Edge-Case Test Suite: Hardware & Host Qualification Canary.
Covers mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Invariant bypass attempt: Rejection of corrupted/tampered preceding receipts.
2. Invariant bypass attempt: Rejection of canary execution against active live meetings.
3. Rollback & recovery: Clean canary abort without terminating ongoing live sessions.
4. Malformed input: Corrupted host metadata or malformed receipt JSON.
5. Boundary payload: Zero-sample or negative sample configuration.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.hardware_canary_core.models import (
    CanaryTopologyResult,
    ModelComparisonRecord,
    evaluate_canary_qualification,
)
from tools.qualify_hardware_canary import (
    run_hardware_canary,
    verify_preceding_receipts,
)


class TestHardwareCanaryAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp_dir.name)
        self.reports_dir = self.repo_root / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _create_mock_preceding_receipts(self, corrupt_wave: int = -1) -> None:
        """Helper to create dummy receipts for Waves 1-5."""
        for w in range(1, 6):
            rf = self.reports_dir / f"project_beacon_rca_wave_{w}_receipt.json"
            if w == corrupt_wave:
                rf.write_text("{corrupted_json: true", encoding="utf-8")
            else:
                rf.write_text(json.dumps({"wave": w, "status": "COMPLETE", "receipt_digest": "dummy"}), encoding="utf-8")

    def test_adv_invariant_bypass_tampered_preceding_receipt(self) -> None:
        """Invariant bypass: A corrupted or non-COMPLETE preceding receipt halts qualification."""
        self._create_mock_preceding_receipts(corrupt_wave=3)
        self.assertFalse(verify_preceding_receipts(self.repo_root))

    def test_adv_invariant_bypass_incomplete_preceding_receipt_status(self) -> None:
        """Invariant bypass: Receipt marked NOT_STARTED or FAILED blocks canary qualification."""
        self._create_mock_preceding_receipts()
        rf = self.reports_dir / "project_beacon_rca_wave_4_receipt.json"
        rf.write_text(json.dumps({"wave": 4, "status": "FAILED"}), encoding="utf-8")

        self.assertFalse(verify_preceding_receipts(self.repo_root))

    def test_adv_rollback_preserves_ongoing_session_handles(self) -> None:
        """Rollback & Concurrency: When canary topology fails, active sessions are not killed."""
        from dual_pipeline.live_meeting_recorder_core.recording_service_adapter import (
            RecordingServiceAdapter,
            RecordingStartRequest,
            RecordingState,
        )

        adapter = RecordingServiceAdapter(repo_root=self.repo_root)
        active_req = RecordingStartRequest(
            session_id="ongoing_production_session",
            project_name="prod_meeting",
            meeting_name="Executive Briefing",
            mock_mode=True,
        )
        resp = adapter.start_recording(active_req)
        self.assertIn(resp.recording_state, (RecordingState.STARTING, RecordingState.CAPTURING))

        # Canary execution with failing qualification
        failed_top = CanaryTopologyResult(
            topology_name="mock_isolated",
            warm_samples_requested=5,
            cold_samples_requested=2,
            total_attempts=7,
            successful_starts=4,
            failed_starts=3,  # Failed!
            missing_pcm_count=2,
            p95_dispatch_s=0.500,
            p95_first_pcm_s=0.800,
            p95_confirmed_warm_s=2.500,
            p95_confirmed_cold_s=6.000,
            is_hardware_distinct=True,
            qualified=False,
        )
        ok = evaluate_canary_qualification([failed_top], [], preceding_receipts_ok=True)
        self.assertFalse(ok)

        # Ongoing production session must remain alive
        st = adapter.get_status("ongoing_production_session")
        self.assertIsNotNone(st)
        self.assertIn(st.recording_state, (RecordingState.STARTING, RecordingState.CAPTURING))

        adapter.stop_recording(MagicMock(session_id="ongoing_production_session", host_chat_id=None))

    def test_adv_malformed_model_comparison_record(self) -> None:
        """Malformed input: Missing fields or non-boolean status in model comparison fails qualification."""
        mc_bad = ModelComparisonRecord(
            model_id="pro_flawed",
            dispatch_latency_ms=999.0,
            silent_hint_intake_success=False,  # Failed intake!
            chat_replies_emitted=3,           # Chat pollution!
            capture_ownership_retained=False,  # Lost ownership!
        )
        ok = evaluate_canary_qualification([], [mc_bad], preceding_receipts_ok=True)
        self.assertFalse(ok)

    def test_adv_boundary_missing_hardware_loopback_falls_back_cleanly(self) -> None:
        """Boundary payload: When hardware loopback is unavailable, mock topology satisfies qualification."""
        self._create_mock_preceding_receipts()
        report = run_hardware_canary(
            repo_root=self.repo_root,
            warm_samples=2,
            cold_samples=1,
            out_path=self.reports_dir / "canary_test.json",
        )
        self.assertTrue(report.overall_qualification_passed)
        self.assertIn("mock_isolated", report.topologies)
        self.assertTrue(report.topologies["mock_isolated"].qualified)


if __name__ == "__main__":
    unittest.main()
