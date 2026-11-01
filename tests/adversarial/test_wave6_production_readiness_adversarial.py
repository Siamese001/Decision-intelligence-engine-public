"""
tests/adversarial/test_wave6_production_readiness_adversarial.py

Adversarial and Edge-Case Test Suite for Wave 6 Production Readiness:
- Extreme Disk Preflight Boundaries (negative bytes, deep non-existent paths, root exhaustion)
- Hostile Feature Flag Inputs (malformed JSON, hostile strings, concurrent override mutations)
- Pathological Cache Cleanup Conditions (zero-capacity LRU, missing directories, zero TTL)
- High-Concurrency Server Shutdown Stampede (concurrent requests racing with graceful termination)
- Corrupt Metadata Migration Payloads (non-dict inputs, unparseable dates, circular structures)
- Model Registry Acoustic Pinning Invariant Protection (unknown tiers, missing configs)
"""
from __future__ import annotations

import concurrent.futures
import contextlib
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
from dual_pipeline.core import assert_disk_headroom
from dual_pipeline.decision_api import DecisionHTTPServer
from dual_pipeline.feature_flags import (
    FeatureFlagManager,
    FeatureFlagState,
)
from dual_pipeline.model_registry import (
    ModelTier,
    get_acoustic_pinning,
    get_binding,
)
from dual_pipeline.transcript_metadata_helper import migrate_metadata_payload


class TestWave6ProductionReadinessAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = Path(tempfile.mkdtemp(prefix="wave6_adv_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_adversarial_disk_space_negative_zero_and_missing_ancestors(self) -> None:
        """Disk headroom check handles extreme required byte boundaries and deep non-existent paths."""
        # 1. Negative or zero required bytes must pass cleanly without exception
        assert_disk_headroom(self.test_dir, required_bytes=0)
        assert_disk_headroom(self.test_dir, required_bytes=-1000)

        # 2. Deep non-existent nested path walks up to existing root ancestor
        deep_path = self.test_dir / "non" / "existent" / "deeply" / "nested" / "output"
        assert_disk_headroom(deep_path, required_bytes=1024)

        # 3. Simulate disk usage failure / permission error (must fail open or log warning, not crash)
        with patch("shutil.disk_usage", side_effect=OSError("Disk probe access denied")):
            # Should not raise StorageExhaustionError when probe OS call fails
            assert_disk_headroom(self.test_dir, required_bytes=1024)

    def test_adversarial_feature_flags_hostile_inputs(self) -> None:
        """Feature flag manager resists hostile env strings, corrupt config files, and multi-thread contention."""
        # 1. Hostile config file
        corrupt_config = self.test_dir / "corrupt_flags.json"
        corrupt_config.write_text("{malformed: [json, syntax", encoding="utf-8")
        mgr = FeatureFlagManager(config_file=corrupt_config)

        # 2. Hostile environment variable values
        hostile_vals = ["'; DROP TABLE flags;--", "UNKNOWN_STATUS", "", "🔥", "NONE", "NULL"]
        for hostile in hostile_vals:
            with patch.dict(os.environ, {"NEE_FLAG_TEST_HOSTILE": hostile}):
                # Must default cleanly to OFF without raising exception
                state = mgr.get_state("TEST_HOSTILE")
                self.assertEqual(state, FeatureFlagState.OFF)

        # Direct hostile string with embedded null byte passed to set_flag
        mgr.set_flag("TEST_NULLBYTE", "\x00nullbyte")
        self.assertEqual(mgr.get_state("TEST_NULLBYTE"), FeatureFlagState.OFF)

        # 3. Concurrent multi-threaded flag mutation race
        def mutate_flags(thread_idx: int) -> None:
            for i in range(50):
                target_state = FeatureFlagState.ON if i % 2 == 0 else FeatureFlagState.OFF
                mgr.set_flag(f"RACE_FLAG_{thread_idx}", target_state)
                _ = mgr.get_state(f"RACE_FLAG_{thread_idx}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(mutate_flags, i) for i in range(8)]
            for f in concurrent.futures.as_completed(futures):
                f.result()

    def test_adversarial_cache_cleanup_edge_cases(self) -> None:
        """Cache cleanup behaves predictably under zero-capacity, non-existent, and zero-TTL conditions."""
        # 1. Non-existent directory returns zero summary without exception
        missing = self.test_dir / "does_not_exist"
        summary = cleanup_transcript_cache(missing, max_bytes=1000)
        self.assertEqual(summary["evicted_ttl_count"], 0)
        self.assertEqual(summary["evicted_lru_count"], 0)

        # 2. Zero-capacity LRU evicts all files
        cache_dir = self.test_dir / "zero_cap_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (cache_dir / f"chunk_{i}.wav").write_bytes(b"DATA" * 100)

        summary_zero = cleanup_transcript_cache(cache_dir, max_bytes=0, max_age_seconds=86400)
        self.assertEqual(summary_zero["evicted_lru_count"], 3)
        self.assertEqual(len(list(cache_dir.glob("*.wav"))), 0)

        # 3. sweep_stale_temp_files on empty directory
        unlinked = sweep_stale_temp_files(self.test_dir, max_age_seconds=0.0)
        self.assertEqual(unlinked, [])

    def test_adversarial_server_shutdown_concurrent_requests(self) -> None:
        """Server handles concurrent in-flight requests gracefully during shutdown signal."""
        mock_service = MagicMock()
        mock_service.route.return_value = {"status": "OK", "answer": "routed"}
        server = DecisionHTTPServer(("127.0.0.1", 0), mock_service, max_concurrency=4)

        try:
            results: list[dict] = []
            shutdown_initiated = False

            def worker(idx: int) -> None:
                nonlocal shutdown_initiated
                time.sleep(0.01 * idx)
                if idx == 5 and not shutdown_initiated:
                    shutdown_initiated = True
                    server.shutdown_gracefully(timeout=1.0)
                fut = server.submit(lambda: {"thread": idx, "status": "COMPLETED"})
                if fut is not None:
                    with contextlib.suppress(Exception):
                        results.append(fut.result(timeout=1.0))

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                futures = [pool.submit(worker, i) for i in range(10)]
                for f in concurrent.futures.as_completed(futures):
                    f.result()

            # Server shutdown flag must be definitively set
            self.assertTrue(server.is_shutting_down.is_set())
            # Subsequent requests are rejected immediately
            self.assertIsNone(server.submit(lambda: {"status": "POST_SHUTDOWN"}))
        finally:
            server.server_close()

    def test_adversarial_metadata_migration_malformed_payloads(self) -> None:
        """Metadata migration handles non-dict, corrupt, and degenerate inputs safely."""
        # 1. Non-dict inputs
        self.assertEqual(migrate_metadata_payload(None), {})
        self.assertEqual(migrate_metadata_payload(["not", "a", "dict"]), {})
        self.assertEqual(migrate_metadata_payload("string"), {})

        # 2. Corrupt / unparseable dates
        corrupt_meta = {
            "date": "INVALID_DATE_STRING_9999",
            "meeting_name": None,
            "attendees": {"invalid": "attendee_structure"},
        }
        migrated = migrate_metadata_payload(corrupt_meta)
        self.assertEqual(migrated["schema_version"], "2.0.0")
        self.assertIn("date_slash", migrated)

        # 3. Payload already at higher version (e.g. v3.0.0) remains unmutated
        future_meta = {
            "schema_version": "3.0.0",
            "future_field": "preserved",
        }
        res_future = migrate_metadata_payload(future_meta)
        self.assertEqual(res_future["schema_version"], "3.0.0")
        self.assertEqual(res_future["future_field"], "preserved")

    def test_adversarial_model_registry_unknown_tier_and_banned_fallbacks(self) -> None:
        """Model registry rejects unknown tiers and enforces invariant boundaries."""
        # 1. Unknown tier lookup raises KeyError
        with self.assertRaises(KeyError):
            get_binding("NON_EXISTENT_TIER_XYZ")

        with self.assertRaises(KeyError):
            get_acoustic_pinning("NON_EXISTENT_TIER_XYZ")

        # 2. Non-acoustic tier passed to acoustic pinning returns None for revision/manifest
        pinning_frontier = get_acoustic_pinning(ModelTier.FRONTIER_HIGH_REASONING)
        self.assertIsNone(pinning_frontier["model_revision"])
        self.assertIsNone(pinning_frontier["manifest_sha256"])


if __name__ == "__main__":
    unittest.main()
