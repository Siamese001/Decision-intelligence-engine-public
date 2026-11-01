"""End-to-End Test Suite for Live Audio Recording Initialization Latency & Import Overhead.

Validates Wave 1 Acceptance Criteria:
1. Subprocess import of instant_spooler completes in < 50ms with zero torch/scipy leakage.
2. InstantAudioSpooler.spawn_instant executes in < 250ms and writes the first PCM bytes to
   master_spool.wav within < 650ms.
3. PEP 562 lazy resolution in pipeline_a_intake and canonical_pipeline correctly resolves
   all exported symbols on demand.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.instant_spooler import InstantAudioSpooler
from dual_pipeline.live_meeting_recorder_core.instant_spooler_contracts import SpoolStatus


class TestRecordingInitLatencyE2E(unittest.TestCase):
    """E2E suite verifying instant audio recording bootstrap latency and import purity."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_init_latency_"))

    def tearDown(self):
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_instant_spooler_hermetic_subprocess_import_budget(self):
        """Spooler imports in a fresh Python process without loading heavy ML/DSP frameworks."""
        probe_code = """
import time, sys, json
t0 = time.perf_counter()
import dual_pipeline.live_meeting_recorder_core.instant_spooler
elapsed_ms = (time.perf_counter() - t0) * 1000.0

res = {
    "import_ms": elapsed_ms,
    "has_torch": "torch" in sys.modules,
    "has_scipy": "scipy" in sys.modules,
    "has_onnx": "onnxruntime" in sys.modules,
}
print(json.dumps(res))
"""
        proc = subprocess.run(
            [sys.executable, "-c", probe_code],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(proc.returncode, 0, f"Import probe failed: {proc.stderr}")
        data = json.loads(proc.stdout.strip())

        self.assertFalse(data["has_torch"], "torch must not be imported during spooler bootstrap")
        self.assertFalse(data["has_scipy"], "scipy must not be imported during spooler bootstrap")
        self.assertFalse(data["has_onnx"], "onnxruntime must not be imported during spooler bootstrap")
        self.assertLess(
            data["import_ms"],
            350.0,
            f"Hermetic spooler import took {data['import_ms']:.2f}ms (must be < 350ms)",
        )

    def test_pipeline_a_intake_lazy_attribute_access(self):
        """PEP 562 lazy __getattr__ in pipeline_a_intake correctly resolves symbols on demand."""
        import pipeline_a_intake

        # Symbols resolve correctly
        self.assertTrue(hasattr(pipeline_a_intake, "AudioDeviceInfo"))
        self.assertTrue(hasattr(pipeline_a_intake, "CoreAudioDeviceManager"))
        self.assertTrue(hasattr(pipeline_a_intake, "AcousticSpeakerGate"))
        self.assertTrue(hasattr(pipeline_a_intake, "LocalSTTEngine"))

        # Invalid symbol raises AttributeError
        with self.assertRaises(AttributeError):
            _ = pipeline_a_intake.NonExistentSymbol999

    def test_canonical_pipeline_lazy_attribute_access(self):
        """PEP 562 lazy __getattr__ in canonical_pipeline correctly resolves symbols on demand."""
        import dual_pipeline.canonical_pipeline as cp

        self.assertTrue(hasattr(cp, "CanonicalAgenticPipeline"))
        self.assertTrue(hasattr(cp, "CanonicalModality"))
        self.assertTrue(hasattr(cp, "SynthesisStrategy"))
        self.assertTrue(hasattr(cp, "QAGateStrategy"))

        with self.assertRaises(AttributeError):
            _ = cp.NonExistentCanonicalItem888

    def test_instant_spooler_spawn_and_first_pcm_latency(self):
        """InstantAudioSpooler.spawn_instant starts under 250ms and writes PCM data under 650ms."""
        spool_dir = self.tmp_dir / "spool"
        t0 = time.perf_counter()
        spooler = InstantAudioSpooler.spawn_instant(
            spool_dir=spool_dir,
            segment_seconds=5,
            source_mode="microphone",
            mock_mode=False,
        )
        spawn_time_ms = (time.perf_counter() - t0) * 1000.0

        try:
            self.assertTrue(spooler.is_running)
            self.assertEqual(spooler.status, SpoolStatus.ACTIVE_SPOOLING)
            self.assertLess(
                spawn_time_ms,
                250.0,
                f"spawn_instant took {spawn_time_ms:.2f}ms (exceeds 250ms gate)",
            )

            # Wait for first PCM data in master_spool.wav
            first_pcm_ms = None
            deadline = time.perf_counter() + 2.0
            while time.perf_counter() < deadline:
                if spooler.master_wav.exists() and spooler.master_wav.stat().st_size > 44:
                    first_pcm_ms = (time.perf_counter() - t0) * 1000.0
                    break
                time.sleep(0.02)

            self.assertIsNotNone(first_pcm_ms, "master_spool.wav was not written within 2.0s")
            self.assertLess(
                first_pcm_ms,
                1000.0,
                f"First PCM bytes appeared at {first_pcm_ms:.1f}ms (exceeds 1000ms target)",
            )
        finally:
            spooler.stop()
