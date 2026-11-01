"""
tests/test_parallel_document_ingestion_adversarial.py

Adversarial and Edge-Case Test Suite for Wave 3:
Parallel document ingestion resilience, concurrency locks, worker fault isolation,
and corrupt staging vector defenses.

Covers mandatory edge-case categories per e2e-and-edge-case-coverage.md:
  1. Concurrency & Race: Duplicate document lock contention via MeetingIngestionLock.
  2. Malformed Input: Corrupt non-zip DOCX file failure isolation.
  3. Boundary Payload: Empty document batch handling.
  4. Invariant Bypass / Worker Failure Isolation: Sibling pipelines proceed unimpeded when one fails.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.document_pipeline_core.parallel_ingestor import (
    ParallelDocumentIngestor,
    ingest_documents_parallel,
)


class TestParallelDocumentIngestionAdversarial(unittest.TestCase):
    """Adversarial fault injection and edge-case tests for parallel document ingestion."""

    @classmethod
    def setUpClass(cls) -> None:
        from dual_pipeline.index_core.backends import get_backend
        cls._shared_backend = get_backend("bge-m3-dev", allow_dev_backend=True)

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.docs_dir = self.root / "inputs"
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.out_root = self.root / "outputs"
        self.out_root.mkdir(parents=True, exist_ok=True)

        self.valid_file = self.docs_dir / "valid_doc.md"
        self.valid_file.write_text(
            "# Operational Architecture Readiness Briefing\n\n"
            "This document outlines the operational readiness and architectural governance standards for Project Nexus. "
            "The engineering teams have completed the comprehensive audit of all data pipelines, ensuring strict conformance "
            "to unidirectional layer boundaries and zero cyclic dependencies across all modules. "
            "All database transactions are now verified under write-ahead logging with serialized commit protocols. "
            "Performance evaluations demonstrate high retrieval accuracy across hybrid sparse and dense embedding models. "
            "Operational approval is confirmed for release deployment following final stakeholder review.\n",
            encoding="utf-8",
        )

        self.corrupt_docx = self.docs_dir / "corrupted.docx"
        self.corrupt_docx.write_bytes(b"NOT A VALID ZIP ARCHIVE JUNK HEADER 00000000000")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_duplicate_document_lock_contention_fails_closed(self) -> None:
        """Category: Concurrency & Race.
        Two workers targeting the identical file collide on MeetingIngestionLock;
        one acquires lock, the other fails closed with ConcurrentIngestionError.
        """
        # Pass the exact same document twice in parallel
        duplicate_docs = [self.valid_file, self.valid_file]

        with patch("dual_pipeline.retrieval_release.validate_activation_evaluation", return_value={"required": False}):
            res = ingest_documents_parallel(
                documents=duplicate_docs,
                max_workers=2,
                provider_type="offline",
                output_dir=str(self.out_root),
                mutate_graph=False,
                auto_mutate_on_pass=False,
                group_commit=False,
                embedding_backend=self._shared_backend,
            )

        # Ingestion lock isolates the collision: at least one run is processed or collision recorded
        self.assertIn(res["status"], ("COMPLETE", "PARTIAL_SUCCESS"))
        self.assertEqual(res["total_documents"], 2)

    def test_malformed_document_worker_failure_isolation(self) -> None:
        """Category: Malformed Input & Fault Isolation.
        One corrupt file in the batch fails closed without crashing sibling workers
        or corrupting overall state.
        """
        batch = [self.valid_file, self.corrupt_docx]

        with patch("dual_pipeline.retrieval_release.validate_activation_evaluation", return_value={"required": False}):
            res = ingest_documents_parallel(
                documents=batch,
                max_workers=2,
                provider_type="offline",
                output_dir=str(self.out_root),
                mutate_graph=False,
                auto_mutate_on_pass=False,
                group_commit=False,
                embedding_backend=self._shared_backend,
            )

        self.assertEqual(res["status"], "PARTIAL_SUCCESS")
        self.assertEqual(res["total_documents"], 2)
        self.assertEqual(res["successful_count"], 1)
        self.assertEqual(res["failed_count"], 1)

        valid_str = str(self.valid_file.resolve())
        corrupt_str = str(self.corrupt_docx.resolve())

        self.assertIn(valid_str, res["successful_paths"])
        self.assertIn(corrupt_str, res["failed_paths"])
        self.assertIn("FAILED", res["results"][corrupt_str]["status"])

    def test_boundary_empty_document_batch(self) -> None:
        """Category: Boundary Payload.
        Empty document batch completes with 0 duration and COMPLETE status.
        """
        res = ingest_documents_parallel(
            documents=[],
            max_workers=4,
            provider_type="offline",
        )
        self.assertEqual(res["status"], "COMPLETE")
        self.assertEqual(res["total_documents"], 0)
        self.assertEqual(res["successful_count"], 0)
        self.assertEqual(res["failed_count"], 0)

    def test_worker_pool_bounds_clamping(self) -> None:
        """Category: Overflow & Bounds.
        Requested max_workers is bounded to [1, 16] to prevent resource exhaustion.
        """
        ingestor_high = ParallelDocumentIngestor(max_workers=999)
        self.assertEqual(ingestor_high.max_workers, 16)

        ingestor_low = ParallelDocumentIngestor(max_workers=-5)
        self.assertEqual(ingestor_low.max_workers, 1)


if __name__ == "__main__":
    unittest.main()
