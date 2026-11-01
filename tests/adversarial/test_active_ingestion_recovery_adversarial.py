"""
tests/adversarial/test_active_ingestion_recovery_adversarial.py

Adversarial and Edge-Case Test Suite for Active Ingestion Publication and Recovery (Wave 3).
Covers mandatory edge-case categories:
  1. Rollback & recovery: pre-activation crash, post-result idempotency, and allow_resume handling.
  2. Concurrency & race: concurrent publication attempts on the same directive hold locks and prevent races.
  3. Malformed input & integrity bypass: corrupt journal records, cross-runtime mismatches, and mock deliverables rejection.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

from dual_pipeline.canonical_pipeline.publication_journal import (
    PublicationReconciliationRequired,
    _record_state,
    _store_record,
    guarded_publication,
)
from dual_pipeline.canonical_pipeline.publish_helper import (
    _assert_authoritative_mutation_preconditions,
    publish_graph_if_requested,
)


class ActiveIngestionRecoveryAdversarialTests(unittest.TestCase):
    """Adversarial suite probing publication crash recovery, journal corruption, and concurrency."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.runtime_root = self.root / "runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        (self.runtime_root / "active_pair.json").write_text(
            json.dumps({"generation_id": "gen-adv-001"}),
            encoding="utf-8",
        )
        self.out_p = self.root / "stage"
        self.out_p.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # Category 1: Rollback & Recovery
    def test_pre_activation_crash_locks_directive_until_reconciled(self) -> None:
        """Crash during publication leaves PENDING intent; subsequent calls fail closed without allow_resume."""
        directive_id = "dir-crash-001"
        writer_calls = []

        def failing_writer():
            writer_calls.append("called")
            raise RuntimeError("Database connection crashed before commit")

        # 1. Initial attempt fails
        with self.assertRaises(PublicationReconciliationRequired) as raised:
            guarded_publication(
                runtime_root=self.runtime_root,
                directive_id=directive_id,
                bindings_factory=lambda: {"source": "doc1", "digest": "sha-1"},
                publish=failing_writer,
            )
        self.assertEqual(raised.exception.details["prior_state"], "PENDING")
        self.assertEqual(len(writer_calls), 1)

        # 2. Subsequent call without allow_resume still fails closed
        with self.assertRaises(PublicationReconciliationRequired):
            guarded_publication(
                runtime_root=self.runtime_root,
                directive_id=directive_id,
                bindings_factory=lambda: {"source": "doc1", "digest": "sha-1"},
                publish=lambda: {"status": "COMPLETE"},
            )
        self.assertEqual(len(writer_calls), 1)

        # 3. Controlled resumption with allow_resume=True succeeds
        resumed_res = guarded_publication(
            runtime_root=self.runtime_root,
            directive_id=directive_id,
            bindings_factory=lambda: {"source": "doc1", "digest": "sha-1"},
            publish=lambda: {"status": "COMPLETE", "recovered": True},
            allow_resume=True,
        )
        self.assertEqual(resumed_res["status"], "COMPLETE")
        self.assertTrue(resumed_res["recovered"])

    def test_post_publication_idempotent_result_reuse(self) -> None:
        """Already completed publication with allow_resume returns prior result without re-executing writer."""
        directive_id = "dir-idemp-002"
        writer_count = 0

        def counted_writer():
            nonlocal writer_count
            writer_count += 1
            return {"status": "COMPLETE", "run_id": "first-run-001"}

        res1 = guarded_publication(
            runtime_root=self.runtime_root,
            directive_id=directive_id,
            bindings_factory=lambda: {"source": "doc2"},
            publish=counted_writer,
        )
        self.assertEqual(res1["run_id"], "first-run-001")
        self.assertEqual(writer_count, 1)

        # Re-running with allow_resume=True must reuse the recorded result
        res2 = guarded_publication(
            runtime_root=self.runtime_root,
            directive_id=directive_id,
            bindings_factory=lambda: {"source": "doc2"},
            publish=counted_writer,
            allow_resume=True,
        )
        self.assertEqual(res2["run_id"], "first-run-001")
        self.assertEqual(writer_count, 1)  # Writer was NOT called a second time

    # Category 2: Concurrency & Race
    def test_concurrent_publications_prevent_parallel_writers(self) -> None:
        """Concurrent calls on the same directive execute writer exactly once."""
        directive_id = "dir-conc-003"
        writer_entered = threading.Event()
        can_proceed = threading.Event()
        writer_invocations = 0

        def blocking_writer():
            nonlocal writer_invocations
            writer_invocations += 1
            writer_entered.set()
            if not can_proceed.wait(timeout=5):
                raise TimeoutError("Test timeout")
            return {"status": "COMPLETE"}

        results = []
        errors = []

        def execute():
            try:
                r = guarded_publication(
                    runtime_root=self.runtime_root,
                    directive_id=directive_id,
                    bindings_factory=lambda: {"source": "conc-doc"},
                    publish=blocking_writer,
                )
                results.append(r)
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=2) as executor:
            fut1 = executor.submit(execute)
            self.assertTrue(writer_entered.wait(timeout=3))
            fut2 = executor.submit(execute)
            can_proceed.set()
            fut1.result(timeout=5)
            fut2.result(timeout=5)

        self.assertEqual(writer_invocations, 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], PublicationReconciliationRequired)

    # Category 3: Malformed Input & Integrity Bypass
    def test_corrupt_journal_fails_closed(self) -> None:
        """Corrupted JSON in intent or result file halts publication fail-closed."""
        directive_id = "dir-corrupt-004"
        journal_dir = self.runtime_root / "publication_journal"
        journal_dir.mkdir(parents=True, exist_ok=True)
        (journal_dir / f"{directive_id}.intent.json").write_text("TRUNCATED_JSON_DATA{", encoding="utf-8")

        with self.assertRaises(PublicationReconciliationRequired) as raised:
            guarded_publication(
                runtime_root=self.runtime_root,
                directive_id=directive_id,
                bindings_factory=lambda: {},
                publish=lambda: {},
            )
        self.assertEqual(raised.exception.details["prior_state"], "CORRUPT_JOURNAL")

    def test_mock_deliverables_rejected_before_mutation(self) -> None:
        """Deliverables from mock or offline providers are strictly barred from graph mutation."""
        meta_mock = {"is_mock": True, "provider": "mock"}
        with self.assertRaisesRegex(RuntimeError, "MOCK_SYNTHESIS_NOT_AUTHORITATIVE"):
            _assert_authoritative_mutation_preconditions(self.out_p, meta_mock)

        meta_offline = {"provider": "offline"}
        with self.assertRaisesRegex(RuntimeError, "MOCK_SYNTHESIS_NOT_AUTHORITATIVE"):
            _assert_authoritative_mutation_preconditions(self.out_p, meta_offline)


if __name__ == "__main__":
    unittest.main()
