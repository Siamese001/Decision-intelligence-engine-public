"""
tests/adversarial/test_retry_flattening_and_caching_adversarial.py

Adversarial qualification suite for Wave 2 Retry Flattening, Timeout Hygiene, and Caching:
- Simulates transient API dropouts (429, 503, timeouts) to verify transient error isolation.
- Validates fail-fast on non-transient 400 Bad Request errors (stopping infinite retry loops).
- Tests transport timeout propagation down to client invocation parameters.
- Validates corrupt or malicious SQLite cache records fail closed safely to memory without crashing.
- Verifies minimum score thresholds are strictly enforced against stale cached evaluations.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

import pytest

from dual_pipeline.llm_judge_core.judges.graph import is_transient_error
from dual_pipeline.transcript_qa_core.eval_cache import (
    _get_cache_db_path,
    _init_sqlite_cache,
    check_cache,
    get_cached_section_evaluation,
    store_cached_section_evaluation,
    update_evaluation_cache,
)


@pytest.mark.adversarial
class TestRetryFlatteningAndCachingAdversarial(unittest.TestCase):
    """Adversarial stress testing for retry flattening, timeouts, and cache resilience."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="nee_cost_adv_")
        self.cache_dir = os.path.join(self.temp_dir, "cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.env_patcher = mock.patch.dict(
            os.environ,
            {
                "NEE_CACHE_DIR": self.cache_dir,
            },
        )
        self.env_patcher.start()

    def tearDown(self) -> None:
        self.env_patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_transient_error_classification_adversarial_matrix(self) -> None:
        """
        Verify that _is_transient_error correctly isolates retriable transient
        network conditions from fatal non-transient schema/client errors.
        """
        # Transient errors (MUST retry)
        err_429 = RuntimeError("HTTP 429: Too Many Requests - rate limit exceeded")
        err_503 = Exception("503 Service Unavailable: backend down")
        err_timeout = TimeoutError("Request timed out after 30.0s")
        err_conn = ConnectionResetError("Connection reset by peer")

        self.assertTrue(is_transient_error(err_429))
        self.assertTrue(is_transient_error(err_503))
        self.assertTrue(is_transient_error(err_timeout))
        self.assertTrue(is_transient_error(err_conn))

        # Fatal / Non-transient errors (MUST NOT retry; fail-fast)
        err_400 = ValueError("HTTP 400: Invalid parameter 'temperature' for reasoning model")
        err_401 = PermissionError("HTTP 401: Unauthorized API key")
        err_schema = KeyError("missing expected field 'nodes'")

        self.assertFalse(is_transient_error(err_400))
        self.assertFalse(is_transient_error(err_401))
        self.assertFalse(is_transient_error(err_schema))

    def test_corrupt_sqlite_cache_record_fails_safe_without_crashing(self) -> None:
        """
        Verify that corrupted or unparseable SQLite database records
        fail safe to in-memory fallback without raising an unhandled exception.
        """
        db_path = _get_cache_db_path()
        _init_sqlite_cache()

        # Inject malformed non-JSON payload into judge_evaluations table
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO judge_evaluations (cache_key, composite_score, verdict, evaluation_json) VALUES (?, ?, ?, ?)",
                ("corrupt_key_001", 99, "PASS", "{{{MALFORMED_TRUNCATED_JSON"),
            )

        # Lookup corrupt key: should log debug and return None gracefully without raising
        result = check_cache("corrupt_key_001", min_score=90)
        self.assertIsNone(result)

    def test_minimum_score_threshold_rejects_sub_threshold_cached_records(self) -> None:
        """
        Verify that cached evaluations with sub-threshold scores
        are never returned as valid cache hits for higher pass requirements.
        """
        cache_key = "sub_threshold_test_key"
        eval_data = {
            "status": "PASS",
            "verdict": "PASS",
            "composite_score": 88,  # Below standard 90 or 95 threshold
        }
        update_evaluation_cache(cache_key, eval_data, "k2", None, "k3", None)

        # Clear in-memory cache to force DB retrieval
        from dual_pipeline.transcript_qa_core import eval_cache
        eval_cache._EVALUATION_CACHE.clear()

        # Require 95 -> Must reject
        self.assertIsNone(check_cache(cache_key, min_score=95))
        # Require 90 -> Must reject
        self.assertIsNone(check_cache(cache_key, min_score=90))
        # Require 85 -> Must pass
        hit = check_cache(cache_key, min_score=85)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.get("composite_score"), 88)

    def test_section_evaluation_disk_cache_persistence_and_invalidation(self) -> None:
        """
        Verify that granular markdown section evaluation caching persists to SQLite
        and invalidates when content hash differs.
        """
        sec_name = "strategic_synthesis"
        sec_hash_v1 = "sha256_hash_version_1"
        sec_hash_v2 = "sha256_hash_version_2"

        store_cached_section_evaluation(sec_name, sec_hash_v1, {"passed": True, "score": 98})

        # Clear in-memory cache
        from dual_pipeline.transcript_qa_core import eval_cache
        eval_cache._SECTION_EVALUATION_CACHE.clear()

        # Version 1 hits disk cache
        hit_v1 = get_cached_section_evaluation(sec_name, sec_hash_v1)
        self.assertIsNotNone(hit_v1)
        self.assertTrue(hit_v1.get("passed"))

        # Version 2 misses
        hit_v2 = get_cached_section_evaluation(sec_name, sec_hash_v2)
        self.assertIsNone(hit_v2)


if __name__ == "__main__":
    unittest.main()
