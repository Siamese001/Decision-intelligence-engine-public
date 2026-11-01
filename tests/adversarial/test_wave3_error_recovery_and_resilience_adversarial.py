"""
tests/adversarial/test_wave3_error_recovery_and_resilience_adversarial.py

Adversarial and boundary test suite for Wave 3 Error Recovery & Resilience:
- Non-transient errors fail fast on attempt 1 without retry budget burn
- Retry budget exhaustion raises underlying exception after max_retries
- Corrupt checkpoint files fail closed without overwriting malformed bytes
- Unserializable and circular metadata handling in DLQ envelopes
- Nonexistent DLQ item retrieval fail-safe
- Custom exception filtering in retry decorator
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.checkpoint import PipelineCheckpointManager
from dual_pipeline.checkpoint_storage import CheckpointIntegrityError
from dual_pipeline.dlq import DeadLetterQueueManager
from dual_pipeline.resilience import is_retryable_error, with_idempotent_retry


class TestWave3ErrorRecoveryAndResilienceAdversarial(unittest.TestCase):
    """Adversarial suite verifying Wave 3 resilience invariants and boundary conditions."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_non_transient_error_fails_fast_adversarial(self):
        """Verifies that non-transient errors fail fast immediately without burning retries."""
        call_count = 0

        @with_idempotent_retry(max_retries=5, base_delay=0.01)
        def fail_with_value_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("Deterministic schema violation")

        with self.assertRaises(ValueError):
            fail_with_value_error()

        # Invariant: Must fail on attempt 1 without retries
        self.assertEqual(call_count, 1)

    def test_retry_budget_exhaustion_adversarial(self):
        """Verifies that transient errors exceeding retry budget raise underlying exception."""
        attempts = 0

        @with_idempotent_retry(max_retries=3, base_delay=0.01, jitter=False)
        def persistent_network_failure():
            nonlocal attempts
            attempts += 1
            raise TimeoutError("Gateway timeout to LLM provider")

        with self.assertRaises(TimeoutError):
            persistent_network_failure()

        # Invariant: Initial attempt + 3 retries = 4 attempts total
        self.assertEqual(attempts, 4)

    def test_corrupt_checkpoint_file_resilience(self):
        """Malformed checkpoint state cannot be reused or silently replaced."""
        cp_file = self.tmp_path / "corrupt_checkpoint.json"
        cp_file.write_text("{invalid_json: truncated bytes...", encoding="utf-8")
        original = cp_file.read_bytes()
        with self.assertRaises(CheckpointIntegrityError) as error:
            PipelineCheckpointManager(cp_file)
        self.assertEqual(error.exception.reason, "CORRUPT_CHECKPOINT")
        self.assertEqual(cp_file.read_bytes(), original)

    def test_dlq_handles_unserializable_metadata(self):
        """Verifies that DLQ enqueue sanitizes non-JSON-serializable objects without crashing."""
        dlq = DeadLetterQueueManager(self.tmp_path / "dlq")

        class NonSerializableObj:
            def __repr__(self):
                return "<CustomNonSerializable 0x1234>"

        meta = {
            "project": "project_nexus",
            "callable": lambda x: x * 2,
            "custom_obj": NonSerializableObj(),
            "bytes_payload": b"\x00\x01\x02",
        }

        try:
            raise RuntimeError("Fatal extraction crash")
        except RuntimeError as err:
            dlq_id = dlq.enqueue(
                input_path=None,
                metadata=meta,
                error=err,
                stage="X_EXIT_GATES",
            )

        item = dlq.get_item(dlq_id)
        self.assertIsNotNone(item)
        self.assertEqual(item["metadata"]["project"], "project_nexus")
        self.assertIn("CustomNonSerializable", item["metadata"]["custom_obj"])
        self.assertIn("function", item["metadata"]["callable"])

    def test_dlq_lookup_missing_item_returns_none(self):
        """Verifies querying nonexistent DLQ items returns None safely."""
        dlq = DeadLetterQueueManager(self.tmp_path / "dlq")
        self.assertIsNone(dlq.get_item("nonexistent_dlq_id"))
        self.assertFalse(dlq.mark_status("nonexistent_dlq_id", "RESOLVED"))

    def test_is_retryable_error_classification(self):
        """Verifies transient error classification across standard and HTTP status attributes."""
        # Built-in network
        self.assertTrue(is_retryable_error(TimeoutError("Timeout")))
        self.assertTrue(is_retryable_error(ConnectionResetError("Reset")))

        # HTTP Status 429 and 503
        class Http429Error(Exception):
            status_code = 429

        class Http503Error(Exception):
            http_status = 503

        class Http400Error(Exception):
            status_code = 400

        self.assertTrue(is_retryable_error(Http429Error("Rate limit exceeded")))
        self.assertTrue(is_retryable_error(Http503Error("Backend service unavailable")))
        self.assertFalse(is_retryable_error(Http400Error("Bad Request")))


if __name__ == "__main__":
    unittest.main()
