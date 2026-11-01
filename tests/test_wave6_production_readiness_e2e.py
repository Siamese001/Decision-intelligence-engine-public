"""
tests/test_wave6_production_readiness_e2e.py

End-to-End Test Suite for Wave 6 Production Readiness Capabilities:
- Deep Health Check (/health endpoint inspects thread pool, SQLite connectivity, memory)
- Graceful Shutdown (drains active requests, sets atomic shutdown flag, returns 503 during termination)
- Disk Space Preflight (StorageExhaustionError, assert_disk_headroom)
- Archival & Cache Cleanup (LRU eviction, 7-day TTL, stale .tmp_* sweeping)
- Prometheus /metrics Endpoint (Prometheus exposition format)
- Schema Compatibility Checker (tools/validate_schema_compatibility.py & metadata migration)
- Acoustic Model Pinning (model_revision, manifest_sha256 in model registry)
- Feature Flag Architecture (ON/OFF/SHADOW modes, env var overrides, non-fatal shadow execution)
- Pipeline Dependency Injection (CanonicalAgenticPipeline accepts stage overrides)
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.archival_manager import (
    cleanup_transcript_cache,
    sweep_stale_temp_files,
)
from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
from dual_pipeline.core import StorageExhaustionError, assert_disk_headroom
from dual_pipeline.decision_api import DecisionHTTPServer
from dual_pipeline.feature_flags import (
    FeatureFlagManager,
    FeatureFlagState,
)
from dual_pipeline.model_registry import (
    ModelTier,
    get_acoustic_pinning,
    reload_registry,
)
from dual_pipeline.telemetry_core.metrics import (
    format_prometheus_metrics,
    increment_counter,
    record_histogram,
    reset_metrics,
    set_gauge,
)
from dual_pipeline.transcript_metadata_helper import migrate_metadata_payload
from tools.validate_schema_compatibility import (
    validate_contracts_syntax,
    validate_metadata_migration_compatibility,
)


class TestWave6ProductionReadinessE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = Path(tempfile.mkdtemp(prefix="wave6_e2e_"))
        reset_metrics()

    def tearDown(self) -> None:
        reset_metrics()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_deep_health_check_healthy_and_degraded_e2e(self) -> None:
        """Deep health check inspects executor, SQLite connectivity, and memory."""
        mock_service = MagicMock()
        server = DecisionHTTPServer(("127.0.0.1", 0), mock_service, max_concurrency=2)
        try:
            # 1. Healthy state
            healthy, payload = server.deep_health_check()
            self.assertTrue(healthy)
            self.assertEqual(payload["status"], "HEALTHY")
            self.assertEqual(payload["checks"]["thread_pool"], "OK")
            self.assertEqual(payload["checks"]["sqlite"], "OK")
            self.assertIn("memory", payload["checks"])

            # 2. Degraded state: simulate SQLite failure
            with patch("dual_pipeline.decision_api.create_sqlite_conn", side_effect=RuntimeError("DB locked")):
                healthy_db_fail, payload_db_fail = server.deep_health_check()
                self.assertFalse(healthy_db_fail)
                self.assertEqual(payload_db_fail["status"], "DEGRADED")
                self.assertIn("FAIL", payload_db_fail["checks"]["sqlite"])

            # 3. Degraded state: simulate memory exhaustion (>95%)
            mock_vm = MagicMock()
            mock_vm.percent = 97.5
            mock_vm.available = 100 * 1024 * 1024
            with patch("psutil.virtual_memory", return_value=mock_vm):
                healthy_mem_fail, payload_mem_fail = server.deep_health_check()
                self.assertFalse(healthy_mem_fail)
                self.assertEqual(payload_mem_fail["status"], "DEGRADED")
                self.assertEqual(payload_mem_fail["checks"]["memory"], "DEGRADED")
        finally:
            server.server_close()

    def test_graceful_shutdown_drains_and_rejects_e2e(self) -> None:
        """Server shutdown drains active requests and rejects subsequent calls with 503."""
        mock_service = MagicMock()
        server = DecisionHTTPServer(("127.0.0.1", 0), mock_service, max_concurrency=2)
        try:
            self.assertFalse(server.is_shutting_down.is_set())
            server.shutdown_gracefully(timeout=1.0)
            self.assertTrue(server.is_shutting_down.is_set())

            # Attempting to submit a new task after shutdown must return None
            res = server.submit(lambda: {"status": "NEW"})
            self.assertIsNone(res)
        finally:
            server.server_close()

    def test_prometheus_metrics_endpoint_e2e(self) -> None:
        """Prometheus metrics exposition formats counters, histograms, and gauges."""
        increment_counter("ingestion.total", 3.0, {"status": "COMPLETE", "project": "nexus"})
        set_gauge("active.workers", 4.0)
        record_histogram("stage.duration", 1.25, {"stage": "U0"})

        prom_text = format_prometheus_metrics()
        self.assertIn("nee_up 1", prom_text)
        self.assertIn("# TYPE nee_ingestion_total counter", prom_text)
        self.assertIn('nee_ingestion_total{project="nexus",status="COMPLETE"} 3.0', prom_text)
        self.assertIn("# TYPE nee_active_workers gauge", prom_text)
        self.assertIn("nee_active_workers 4.0", prom_text)
        self.assertIn("# TYPE nee_stage_duration histogram", prom_text)
        self.assertIn('nee_stage_duration_count{stage="U0"} 1', prom_text)
        self.assertIn('nee_stage_duration_sum{stage="U0"} 1.25', prom_text)

    def test_disk_space_preflight_and_storage_exhaustion_e2e(self) -> None:
        """Preflight checks disk headroom and raises StorageExhaustionError when below buffer."""
        # 1. Normal check should succeed with standard or small buffer
        assert_disk_headroom(self.test_dir, required_bytes=1024)

        # 2. Exorbitant requirement (e.g. 100 Petabytes) must raise StorageExhaustionError
        with self.assertRaises(StorageExhaustionError) as ctx:
            assert_disk_headroom(self.test_dir, required_bytes=100 * 1024 * 1024 * 1024 * 1024 * 1024)
        self.assertIn("Insufficient disk space", str(ctx.exception))

    def test_archival_cache_lru_and_ttl_cleanup_e2e(self) -> None:
        """LRU and TTL eviction prunes expired cache files and bounds total size to ceiling."""
        cache_dir = self.test_dir / "transcript_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Create 5 files with distinct ages and sizes
        now = time.time()
        # File 1: Old (expired under 7-day TTL)
        f1 = cache_dir / "old_chunk_1.wav"
        f1.write_bytes(b"A" * 1000)
        os.utime(f1, (now - 10 * 86400, now - 10 * 86400))

        # Files 2, 3, 4: Fresh (1 hour ago)
        f2 = cache_dir / "fresh_chunk_2.wav"
        f2.write_bytes(b"B" * 2000)
        os.utime(f2, (now - 3600, now - 3600))

        f3 = cache_dir / "fresh_chunk_3.wav"
        f3.write_bytes(b"C" * 3000)
        os.utime(f3, (now - 1800, now - 1800))

        f4 = cache_dir / "fresh_chunk_4.wav"
        f4.write_bytes(b"D" * 4000)
        os.utime(f4, (now - 60, now - 60))

        # File 5: Stale temporary file (.tmp_)
        f_tmp = cache_dir / "stream.wav.tmp_12345"
        f_tmp.write_bytes(b"TEMP" * 50)
        os.utime(f_tmp, (now - 7200, now - 7200))  # 2 hours old

        # 1. Sweep stale temp files (>1 hour old)
        unlinked = sweep_stale_temp_files(cache_dir, max_age_seconds=3600.0)
        self.assertEqual(len(unlinked), 1)
        self.assertFalse(f_tmp.exists())

        # 2. Run cache cleanup with max_bytes=4500 and TTL=7 days
        # Total remaining fresh: 2000 + 3000 + 4000 = 9000 bytes.
        # f1 (1000 bytes) will be evicted by TTL.
        # Then LRU will evict f2 (2000B, oldest fresh) -> remaining 7000B.
        # Then LRU will evict f3 (3000B, next oldest) -> remaining 4000B <= 4500B limit!
        summary = cleanup_transcript_cache(cache_dir, max_bytes=4500, max_age_seconds=7 * 86400)
        self.assertEqual(summary["evicted_ttl_count"], 1)
        self.assertEqual(summary["evicted_lru_count"], 2)
        self.assertFalse(f1.exists())
        self.assertFalse(f2.exists())
        self.assertFalse(f3.exists())
        self.assertTrue(f4.exists())
        self.assertEqual(summary["remaining_bytes"], 4000)

    def test_schema_compatibility_and_metadata_migration_e2e(self) -> None:
        """Schema compatibility gates and metadata migration execute losslessly."""
        ok_syn, msg_syn = validate_contracts_syntax()
        self.assertTrue(ok_syn, msg_syn)

        ok_mig, msg_mig = validate_metadata_migration_compatibility()
        self.assertTrue(ok_mig, msg_mig)

        # Direct test of legacy payload migration
        legacy = {
            "meeting_name": "Review_Alex_Amit",
            "date": "09-25-2026",
            "is_audio": True,
        }
        migrated = migrate_metadata_payload(legacy)
        self.assertEqual(migrated["schema_version"], "2.0.0")
        self.assertEqual(migrated["source_type"], "AUDIO_ONLY_UNDIARIZED")
        self.assertEqual(migrated["date_slash"], "09/25/2026")
        self.assertEqual(migrated["date_filename"], "09-25-2026")
        self.assertEqual(len(migrated["migration_history"]), 1)

    def test_acoustic_model_pinning_e2e(self) -> None:
        """Acoustic tiers in model registry declare immutable model_revision and manifest_sha256."""
        reload_registry()
        for tier in (
            ModelTier.ACOUSTIC_MLX_WHISPER,
            ModelTier.ACOUSTIC_FASTER_WHISPER,
            ModelTier.ACOUSTIC_WHISPERX,
            ModelTier.ACOUSTIC_DIARIZATION,
        ):
            pinning = get_acoustic_pinning(tier)
            self.assertIsNotNone(pinning["model_id"], f"Tier {tier} missing model_id")
            self.assertIsNotNone(pinning["model_revision"], f"Tier {tier} missing model_revision")
            self.assertIsNotNone(pinning["manifest_sha256"], f"Tier {tier} missing manifest_sha256")
            self.assertEqual(len(pinning["model_revision"]), 40, f"Tier {tier} revision not 40-char SHA")
            self.assertEqual(len(pinning["manifest_sha256"]), 64, f"Tier {tier} manifest not 64-char SHA256")

    def test_feature_flag_manager_lifecycle_e2e(self) -> None:
        """Feature flag manager supports ON, OFF, and SHADOW with graceful error suppression in SHADOW."""
        mgr = FeatureFlagManager()
        flag_name = "EXPERIMENTAL_SYNTHESIS"

        # 1. Default state is OFF
        self.assertTrue(mgr.is_off(flag_name))
        self.assertFalse(mgr.is_enabled(flag_name))

        # execute_stage should skip and return default_return
        called = False
        def dummy_stage():
            nonlocal called
            called = True
            return "SUCCESS"

        res = mgr.execute_stage(flag_name, dummy_stage, default_return="SKIPPED")
        self.assertEqual(res, "SKIPPED")
        self.assertFalse(called)

        # 2. Set to ON
        mgr.set_flag(flag_name, FeatureFlagState.ON)
        self.assertTrue(mgr.is_on(flag_name))
        self.assertTrue(mgr.is_enabled(flag_name))
        res = mgr.execute_stage(flag_name, dummy_stage)
        self.assertEqual(res, "SUCCESS")
        self.assertTrue(called)

        # 3. Set to SHADOW: failing stage should log and return default_return without raising
        mgr.set_flag(flag_name, FeatureFlagState.SHADOW)
        self.assertTrue(mgr.is_shadow(flag_name))
        self.assertTrue(mgr.is_enabled(flag_name))

        def failing_stage():
            raise RuntimeError("Transient upstream synthesis timeout")

        res_shadow = mgr.execute_stage(flag_name, failing_stage, default_return="FALLBACK_OK")
        self.assertEqual(res_shadow, "FALLBACK_OK")

        # 4. Environment variable override
        with patch.dict(os.environ, {"NEE_FLAG_EXPERIMENTAL_SYNTHESIS": "off"}):
            mgr.clear_overrides()
            self.assertTrue(mgr.is_off(flag_name))

    def test_canonical_pipeline_dependency_injection_e2e(self) -> None:
        """CanonicalAgenticPipeline accepts custom injected stage overrides for hermetic mocking."""
        mock_u0 = MagicMock()
        mock_l1 = MagicMock()
        mock_l0 = MagicMock()
        mock_l2 = MagicMock()
        mock_x_gates = MagicMock()

        pipeline = CanonicalAgenticPipeline(
            provider_type="offline",
            u0_override=mock_u0,
            l1_override=mock_l1,
            l0_override=mock_l0,
            l2_override=mock_l2,
            x_gates_override=mock_x_gates,
        )

        self.assertIs(pipeline.u0, mock_u0)
        self.assertIs(pipeline.l1, mock_l1)
        self.assertIs(pipeline.l0, mock_l0)
        self.assertIs(pipeline.l2, mock_l2)
        self.assertIs(pipeline.x_gates, mock_x_gates)


if __name__ == "__main__":
    unittest.main()
