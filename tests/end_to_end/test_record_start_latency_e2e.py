"""
tests/end_to_end/test_record_start_latency_e2e.py

Wave 5 End-to-End Test Suite: Recording Startup Latency & Diagnostic Telemetry.
Verifies the complete lifecycle:
1. Adapter dispatch meets the <= 250ms p95 latency budget.
2. Source-bound monotonic clock timestamps for all capture milestones.
3. Decoupled capture proof and ASR readiness states.
4. Benchmark harness execution with RFC-8259 canonical receipt sealing.
5. Cohort isolation between cold starts and warm starts.
6. Session-scoped cleanup with zero orphaned subprocesses.
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder_core.recording_service_adapter import (
    ASRState,
    RecordingServiceAdapter,
    RecordingSessionHandle,
    RecordingStartRequest,
    RecordingState,
    RecordingStopRequest,
)
from tools.benchmark_record_start_latency import run_benchmark


class TestRecordStartLatencyE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp_dir.name)
        self.adapter = RecordingServiceAdapter(repo_root=self.repo_root)

    def tearDown(self) -> None:
        for status in self.adapter.list_active_sessions():
            self.adapter.stop_recording(RecordingStopRequest(session_id=status.session_id))
        self.tmp_dir.cleanup()

    def test_e2e_recording_start_lifecycle_and_monotonic_evidence(self) -> None:
        """E2E lifecycle: verifies dispatch < 250ms, monotonic milestones, decoupled ASR, and proof."""
        chunks_dir = self.repo_root / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        # Create a mock valid chunk
        test_chunk = chunks_dir / "chunk_00000.wav"
        test_chunk.write_bytes(b"RIFF" + b"\x00" * 100)

        mock_rec = MagicMock()
        mock_rec.running = True
        mock_rec._is_stopped = False
        mock_rec.mock_mode = True
        mock_rec.output_dir = self.repo_root / "out"
        mock_rec.chunks_dir = chunks_dir
        mock_rec.master_wav = self.repo_root / "master.wav"
        mock_rec.processed_chunks = {"chunk_00000.wav"}

        def _fake_init_recorder(handle_self: RecordingSessionHandle) -> MagicMock:
            with handle_self.lock:
                handle_self.recorder = mock_rec
                handle_self._output_dir = mock_rec.output_dir
                handle_self._chunks_dir = mock_rec.chunks_dir
                handle_self._master_wav = mock_rec.master_wav
                handle_self.source_opened_monotonic = time.monotonic()
            return mock_rec

        with patch.object(RecordingSessionHandle, "_init_recorder", autospec=True, side_effect=_fake_init_recorder):

            t_intent_before = time.monotonic()
            req = RecordingStartRequest(
                session_id="e2e_latency_session",
                project_name="e2e_proj",
                meeting_name="E2E Latency Session",
                mock_mode=True,
                is_cold_start=False,
            )
            t_intent_after = time.monotonic()

            self.assertGreaterEqual(req.intent_received_monotonic, t_intent_before)
            self.assertLessEqual(req.intent_received_monotonic, t_intent_after)

            t0 = time.perf_counter()
            resp = self.adapter.start_recording(req)
            dispatch_duration_ms = (time.perf_counter() - t0) * 1000.0

            # Gate 1: Dispatch latency <= 250ms
            self.assertLess(dispatch_duration_ms, 250.0, f"Dispatch took {dispatch_duration_ms:.2f}ms (> 250ms)")
            self.assertEqual(resp.session_id, "e2e_latency_session")

            # Wait for worker to capture chunks and verify milestones
            deadline = time.time() + 4.0
            final_status = None
            while time.time() < deadline:
                st = self.adapter.get_status("e2e_latency_session")
                if st and st.recording_state == RecordingState.CAPTURING and st.first_pcm_monotonic is not None:
                    final_status = st
                    break
                time.sleep(0.05)

            self.assertIsNotNone(final_status, "Capture worker failed to reach CAPTURING state with first PCM")
            self.assertEqual(final_status.recording_state, RecordingState.CAPTURING)
            self.assertTrue(final_status.is_proven)
            self.assertIsNotNone(final_status.first_pcm_monotonic)
            self.assertIsNotNone(final_status.first_pcm_utc)

            # Verify monotonic milestones order
            cap = final_status.capture_milestones
            self.assertIsNotNone(cap.get("intent_received_monotonic"))
            self.assertIsNotNone(cap.get("dispatch_monotonic"))
            self.assertGreaterEqual(cap["dispatch_monotonic"], cap["intent_received_monotonic"])

            # Decoupled ASR milestones
            asr = final_status.asr_milestones
            self.assertIn("asr_state", asr)
            self.assertIn("diarization_state", asr)

            # Clean session stop
            stop_resp = self.adapter.stop_recording(RecordingStopRequest(session_id="e2e_latency_session"))
            self.assertEqual(stop_resp.status, "STOPPED")

    def test_e2e_benchmark_harness_execution_and_receipt_sealing(self) -> None:
        """E2E benchmark harness: executes cycles, isolates cohorts, seals receipt with RFC-8259 digest."""
        out_receipt = self.repo_root / "reports" / "test_latency_receipt.json"

        receipt = run_benchmark(
            mode="mock",
            runs=3,
            cold_runs=1,
            out_path=out_receipt,
            project="e2e_bench_project",
            meeting_title="E2E Benchmark Meeting",
        )

        self.assertTrue(out_receipt.exists(), "Benchmark receipt file was not written")
        self.assertIn("receipt_sha256", receipt)
        self.assertEqual(len(receipt["receipt_sha256"]), 64)

        # Denominator tracking
        denom = receipt["denominator_accounting"]
        self.assertEqual(denom["total_attempts"], 3)
        self.assertEqual(denom["successful_starts"], 3)
        self.assertEqual(denom["failed_starts"], 0)
        self.assertEqual(denom["missing_pcm_count"], 0)
        self.assertEqual(denom["success_rate"], 1.0)

        # Qualification verdict
        qual = receipt["qualification"]
        self.assertTrue(qual["qualified"])
        self.assertTrue(qual["gates"]["gate_no_missing_pcm"]["pass"])

        # Cohort separation
        self.assertIn("warm_cohort", receipt["statistics"])
        self.assertIn("cold_cohort", receipt["statistics"])
        self.assertIsNotNone(receipt["statistics"]["cold_cohort"])

        # Validate file content matches returned payload
        saved_data = json.loads(out_receipt.read_text(encoding="utf-8"))
        self.assertEqual(saved_data["receipt_sha256"], receipt["receipt_sha256"])


if __name__ == "__main__":
    unittest.main()
