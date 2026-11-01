"""Adversarial and edge-case test suite for publication journal locking and directive re-binding.

Covers:
1. Concurrency & Race: Re-entrant file locking nested acquisition across depths within the same thread.
2. Rollback & Recovery: Publication failure rollback (mark_publication_rolled_back inside guarded_publication)
   completes without same-process flock self-deadlock.
3. Rollback & Recovery: reconcile_publication_checkpoint safely delegates to mark_publication_rolled_back
   without deadlock.
4. Boundary Payload & Recovery: Uncommitted (OPEN) directive re-binds new package paths/hashes across
   retry/resume staging directories while resetting pre-commit approval and updating package_set_digest.
5. Invariant Bypass Attempt: Terminal (COMPLETE / BLOCKED) directive strictly fails closed against re-binding.
6. Invariant Bypass Attempt: Mismatched scope on existing directive ID strictly fails closed.
7. Concurrency & Race: Cross-thread mutual exclusion ensures different threads correctly block and serialize.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dual_pipeline.canonical_pipeline.publication_journal import (
    PublicationReconciliationRequired,
    get_publication_checkpoint,
    guarded_publication,
    mark_publication_rolled_back,
    reconcile_publication_checkpoint,
)
from dual_pipeline.completion_core.guard import IngestionCompletionGuard
from dual_pipeline.completion_core.verification import _verify_directive
from dual_pipeline.core import ROOT, file_lock, read_json, sha256_file, utc_now, write_json


def _create_minimal_sidecar(
    sidecar_path: Path,
    generation_id: str,
    graph_snapshot: str,
    graph_sha256: str,
) -> None:
    conn = sqlite3.connect(sidecar_path)
    conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY, lane TEXT, artifact_json TEXT)")
    conn.execute("CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY, dimensions INTEGER, vector BLOB)")
    conn.execute("CREATE TABLE sparse_weight (record_id TEXT, token TEXT, weight REAL, PRIMARY KEY (record_id, token))")
    conn.execute("CREATE TABLE colbert_token (record_id TEXT, position INTEGER, token TEXT, dimensions INTEGER, vector BLOB, PRIMARY KEY (record_id, position))")
    conn.execute("INSERT INTO metadata VALUES ('generation_id', ?)", (generation_id,))
    conn.execute("INSERT INTO metadata VALUES ('graph_snapshot', ?)", (graph_snapshot,))
    conn.execute("INSERT INTO metadata VALUES ('graph_sha256', ?)", (graph_sha256,))
    conn.commit()
    conn.close()


class TestPublicationJournalDirectiveRebindingAdversarial(unittest.TestCase):
    """Adversarial tests for lock re-entrancy, publication rollback, and directive re-binding."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-rca-adversarial-")
        self.scratch = Path(self.temp.name)
        self.runtime = self.scratch / "runtime"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.graph = ROOT / "graph" / "nee_graph.sqlite"

        # Initialize hermetic active_pair so IngestionCompletionGuard is fully operational
        gen_dir = self.runtime / "generations" / "gen-test"
        gen_dir.mkdir(parents=True, exist_ok=True)
        graph_file = gen_dir / "graph.sqlite"
        sidecar_file = gen_dir / "sidecar.sqlite"
        shutil.copyfile(self.graph, graph_file)
        graph_sha = sha256_file(graph_file)
        generation_id = "generation-bootstrap-test-01"
        graph_snapshot = "graph-snapshot-test-01"
        _create_minimal_sidecar(sidecar_file, generation_id, graph_snapshot, graph_sha)
        sidecar_sha = sha256_file(sidecar_file)

        active_pair_payload = {
            "schema_version": "1.0.0",
            "generation_id": generation_id,
            "graph_snapshot": graph_snapshot,
            "graph_sha256": graph_sha,
            "sidecar_sha256": sidecar_sha,
            "graph_path": "generations/gen-test/graph.sqlite",
            "sidecar_path": "generations/gen-test/sidecar.sqlite",
            "embedding_backend": "flagembedding-bge-m3-dev",
            "model_id": "BAAI/bge-m3",
            "model_revision": "c" * 40,
            "model_manifest_digest": "d" * 64,
            "production_qualified": True,
            "assurance_tier": "development",
            "previous_generation_id": None,
            "activated_at": utc_now(),
        }
        write_json(self.runtime / "active_pair.json", active_pair_payload)
        self.guard = IngestionCompletionGuard(self.runtime, self.graph)
        cache_key = (
            str(self.guard.manager._resolve(active_pair_payload["graph_path"])),
            str(active_pair_payload["graph_sha256"]),
            str(self.guard.manager._resolve(active_pair_payload["sidecar_path"])),
            str(active_pair_payload["sidecar_sha256"]),
            str(active_pair_payload["generation_id"]),
        )
        self.guard.manager._validated_pairs = {
            cache_key: {"status": "VALID", "sidecar_sha256": active_pair_payload["sidecar_sha256"]}
        }

        self.fixture_pkg1 = (
            ROOT / "contracts" / "dual_pipeline" / "fixtures" / "valid" / "copilot-meeting.json"
        )
        self.fixture_pkg2 = (
            ROOT / "contracts" / "dual_pipeline" / "fixtures" / "valid" / "architecture-diagram.json"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    # -------------------------------------------------------------------------
    # Category 1: Concurrency & Race - Re-Entrant Lock Depth & Teardown
    # -------------------------------------------------------------------------

    def test_file_lock_reentrant_nested_acquisition(self) -> None:
        """Advisory file_lock must be re-entrant within the same thread across nested scopes."""
        lock_file = self.scratch / "reentrant_test.lock"

        start_time = time.monotonic()
        with file_lock(lock_file):
            # Depth 2
            with file_lock(lock_file):
                # Depth 3
                with file_lock(lock_file):
                    self.assertTrue(lock_file.is_file())
                # Exited depth 3, still at depth 2
                self.assertTrue(lock_file.is_file())
            # Exited depth 2, still at depth 1
            self.assertTrue(lock_file.is_file())

        elapsed = time.monotonic() - start_time
        # Must complete instantaneously (< 0.2s), proving no self-deadlock
        self.assertLess(elapsed, 0.2, "Re-entrant file_lock took unexpectedly long")

    def test_file_lock_cross_thread_mutual_exclusion(self) -> None:
        """file_lock must enforce strict mutual exclusion across distinct threads."""
        lock_file = self.scratch / "thread_exclusion.lock"
        thread1_holding = threading.Event()
        thread2_acquired = threading.Event()
        can_thread1_exit = threading.Event()

        def worker_thread1():
            with file_lock(lock_file):
                thread1_holding.set()
                can_thread1_exit.wait(timeout=2.0)

        def worker_thread2():
            thread1_holding.wait(timeout=2.0)
            # Thread 2 should block here until Thread 1 releases the lock
            with file_lock(lock_file):
                thread2_acquired.set()

        with ThreadPoolExecutor(max_workers=2) as executor:
            fut1 = executor.submit(worker_thread1)
            self.assertTrue(thread1_holding.wait(timeout=1.0))

            fut2 = executor.submit(worker_thread2)
            # Thread 2 must NOT acquire while thread 1 holds it
            time.sleep(0.05)
            self.assertFalse(thread2_acquired.is_set(), "Thread 2 acquired lock while Thread 1 held it")

            # Release Thread 1
            can_thread1_exit.set()
            fut1.result(timeout=1.0)
            fut2.result(timeout=1.0)
            self.assertTrue(thread2_acquired.is_set(), "Thread 2 failed to acquire lock after Thread 1 released")

    # -------------------------------------------------------------------------
    # Category 2: Rollback & Recovery - No Deadlock During Publication Rollback
    # -------------------------------------------------------------------------

    def test_guarded_publication_rollback_same_process_no_deadlock(self) -> None:
        """Publication failure inside guarded_publication must roll back cleanly in <0.5s."""
        directive_id = "directive-rollback-test-01"

        def failing_publish():
            # Nested call to mark_publication_rolled_back inside the guarded_publication lock scope
            mark_publication_rolled_back(
                runtime_root=self.runtime,
                directive_id=directive_id,
                reason="Simulated publication failure in test",
                zero_delta=True,
            )
            raise RuntimeError("Deliberate publish failure for rollback test")

        start = time.monotonic()
        with self.assertRaises(PublicationReconciliationRequired):
            guarded_publication(
                runtime_root=self.runtime,
                directive_id=directive_id,
                bindings_factory=lambda: {"test": "bindings_payload"},
                publish=failing_publish,
            )
        duration = time.monotonic() - start

        self.assertLess(duration, 0.5, f"Rollback publication took {duration:.2f}s, indicating potential deadlock")

        # Verify publication checkpoint state is ROLLED_BACK and retryable
        chk = get_publication_checkpoint(self.runtime, directive_id)
        self.assertEqual(chk["intent_state"], "ROLLED_BACK")
        self.assertTrue(chk["retryable"])

    def test_reconcile_publication_checkpoint_nested_mark_rolled_back(self) -> None:
        """reconcile_publication_checkpoint with mark_rolled_back must execute without deadlock."""
        directive_id = "directive-reconcile-rollback-01"

        # Initialize an interrupted intent
        journal = self.runtime / "publication_journal"
        journal.mkdir(parents=True, exist_ok=True)
        intent_file = journal / f"{directive_id}.intent.json"
        write_json(
            intent_file,
            {
                "schema_version": 1,
                "state": "PENDING",
                "directive_id": directive_id,
                "bindings": {"mock": "data"},
                "bindings_sha256": "0" * 64,
            },
        )

        start = time.monotonic()
        res = reconcile_publication_checkpoint(
            self.runtime,
            directive_id,
            action="mark_rolled_back",
        )
        duration = time.monotonic() - start

        self.assertLess(duration, 0.5, "Reconciliation mark_rolled_back deadlocked")
        self.assertEqual(res["status"], "ROLLED_BACK")
        self.assertTrue(res["retryable"])

    # -------------------------------------------------------------------------
    # Category 3: Boundary Payload & Recovery - Stale Open Directive Re-Binding
    # -------------------------------------------------------------------------

    def test_open_directive_rebinding_on_resume_with_new_staging_package(self) -> None:
        """Uncommitted OPEN directives must allow package re-binding and reset approval."""
        directive_id = "directive-apex_trans-resume-rebind-v1"
        scope = "APEX_TRANS Session 2 Remediation"

        # Attempt 1: Ingest with package 1 in staging dir A
        staging_dir_a = self.scratch / ".staging_neet_attempt_1"
        staging_dir_a.mkdir(parents=True, exist_ok=True)
        pkg_a = staging_dir_a / "intake_package.json"
        pkg_a.write_bytes(self.fixture_pkg1.read_bytes())

        directive_att1 = self.guard.open(
            directive_id=directive_id,
            scope=scope,
            packages=[pkg_a],
        )
        self.assertEqual(directive_att1["status"], "OPEN")
        self.assertFalse(directive_att1["terminal"])
        initial_pkg_digest = directive_att1["package_set_digest"]

        # Attempt 2: Pipeline halted before completion; resume occurs in staging dir B with different bytes
        staging_dir_b = self.scratch / ".staging_neet_attempt_2"
        staging_dir_b.mkdir(parents=True, exist_ok=True)
        pkg_b = staging_dir_b / "intake_package.json"
        pkg_b.write_bytes(self.fixture_pkg2.read_bytes())

        # Calling guard.open on the same directive_id with new staging package MUST succeed
        directive_att2 = self.guard.open(
            directive_id=directive_id,
            scope=scope,
            packages=[pkg_b],
        )

        self.assertEqual(directive_att2["status"], "OPEN")
        self.assertFalse(directive_att2["terminal"])
        self.assertNotEqual(directive_att2["package_set_digest"], initial_pkg_digest)
        self.assertEqual(len(directive_att2["packages"]), 1)
        self.assertEqual(directive_att2["packages"][0]["path"], str(pkg_b.resolve()))
        self.assertIsNone(directive_att2["approval"])

        # Check recorded state event
        events = directive_att2["events"]
        rebound_events = [e for e in events if e.get("event_type") == "DIRECTIVE_PACKAGES_REBOUND"]
        self.assertEqual(len(rebound_events), 1)
        self.assertEqual(rebound_events[0]["package_set_digest"], directive_att2["package_set_digest"])

        # Validate stored directive schema & integrity verification
        stored_directive = self.guard.status(directive_id)
        _verify_directive(stored_directive)

    # -------------------------------------------------------------------------
    # Category 4: Invariant Bypass Attempt - Terminal & Mismatched Directives Fail Closed
    # -------------------------------------------------------------------------

    def test_terminal_directive_immutable_fails_closed(self) -> None:
        """Terminal directives must strictly fail closed if re-opened with different bindings."""
        directive_id = "directive-terminal-immutable-v1"
        scope = "APEX_TRANS Completed Session"

        staging_dir = self.scratch / ".staging_terminal_pkg"
        staging_dir.mkdir(parents=True, exist_ok=True)
        pkg1 = staging_dir / "pkg1.json"
        pkg1.write_bytes(self.fixture_pkg1.read_bytes())

        self.guard.open(
            directive_id=directive_id,
            scope=scope,
            packages=[pkg1],
        )

        # Transition directive to terminal state via authentic block
        self.guard.block(
            directive_id=directive_id,
            reason_code="RESOURCE_EXHAUSTED",
            reason="Deliberate block for terminal immutability test",
        )

        pkg2 = staging_dir / "pkg2.json"
        pkg2.write_bytes(self.fixture_pkg2.read_bytes())

        # Attempt to re-bind with different packages on a terminal directive must fail closed
        with self.assertRaises(RuntimeError) as ctx:
            self.guard.open(
                directive_id=directive_id,
                scope=scope,
                packages=[pkg2],
            )
        self.assertIn("already exists with different bindings", str(ctx.exception))

    def test_scope_mismatch_fails_closed(self) -> None:
        """Re-opening an uncommitted directive with a conflicting scope must fail closed."""
        directive_id = "directive-scope-conflict-v1"

        staging_dir = self.scratch / ".staging_scope_test"
        staging_dir.mkdir(parents=True, exist_ok=True)
        pkg = staging_dir / "pkg.json"
        pkg.write_bytes(self.fixture_pkg1.read_bytes())

        self.guard.open(
            directive_id=directive_id,
            scope="Scope Alpha",
            packages=[pkg],
        )

        pkg_diff = staging_dir / "pkg_diff.json"
        pkg_diff.write_bytes(self.fixture_pkg2.read_bytes())

        with self.assertRaises(RuntimeError) as ctx:
            self.guard.open(
                directive_id=directive_id,
                scope="Scope Beta",
                packages=[pkg_diff],
            )
        self.assertIn("already exists with different bindings", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
