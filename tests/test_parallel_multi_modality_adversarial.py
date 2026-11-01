"""
tests/test_parallel_multi_modality_adversarial.py

Adversarial and Edge-Case Test Suite for Parallel Multi-Modality Ingestion.
Covers mandatory categories under e2e-and-edge-case-coverage.md:
  1. Boundary Payload: Empty document lists, 0-byte files.
  2. Malformed Input: Invalid JSON metadata sidecars, corrupted headers.
  3. Concurrency & Race: High-thread concurrent worktree provision lock acquisition.
  4. Rollback & Recovery: Isolated task failure in batch without aborting sibling tasks.
  5. Exact Production Regression: Audio .wav file routed to AUDIO modality without
     "Audio file cannot be coerced to DOCUMENT" failure.
"""
from __future__ import annotations

import concurrent.futures
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.document_pipeline_core.parallel_ingestor import (
    DocumentTask,
    ParallelDocumentIngestor,
    ingest_documents_parallel,
)
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    git_worktree_provision_lock,
)


class TestParallelMultiModalityAdversarial(unittest.TestCase):
    """Adversarial stress and edge-case testing for parallel multi-modality ingest."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.inputs_dir = self.root / "inputs"
        self.inputs_dir.mkdir(parents=True, exist_ok=True)
        self.out_root = self.root / "outputs"
        self.out_root.mkdir(parents=True, exist_ok=True)

        # Valid document fixture (> 50 words to satisfy minimum semantic yield)
        self.valid_doc = self.inputs_dir / "valid_memo.md"
        self.valid_doc.write_text(
            "# Architecture Audit and Verification Summary Specification\n\n"
            "This report summarizes the operational review of the multi-tier pipeline architecture. "
            "All components strictly adhere to unidirectional layer boundaries and zero circular dependencies. "
            "Database connections use write-ahead logging with serialized transaction safety protocols across all processes. "
            "Automated qualification benchmarks verify continuous alignment against operational performance targets. "
            "The engineering team has confirmed that all governance linters and release criteria are fully satisfied "
            "for production deployment and canonical knowledge graph publication.\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_adversarial_boundary_empty_batch(self) -> None:
        """Category: Boundary payload - empty input batch."""
        res = ingest_documents_parallel(
            documents=[],
            max_workers=2,
            output_dir=str(self.out_root),
        )
        self.assertEqual(res["status"], "COMPLETE")
        self.assertEqual(res["total_documents"], 0)
        self.assertEqual(res["successful_count"], 0)
        self.assertEqual(res["failed_count"], 0)
        self.assertEqual(res["results"], {})

    def test_adversarial_boundary_zero_byte_document(self) -> None:
        """Category: Boundary payload - 0-byte document alongside valid document."""
        zero_byte_doc = self.inputs_dir / "zero_byte.md"
        zero_byte_doc.write_bytes(b"")

        items = [
            {"path": zero_byte_doc, "output_dir": str(self.out_root / "zero_out"), "metadata": {"no_subfolder": True}},
            {"path": self.valid_doc, "output_dir": str(self.out_root / "valid_out"), "metadata": {"no_subfolder": True}},
        ]

        res = ingest_documents_parallel(
            documents=items,
            max_workers=2,
            provider_type="offline",
            output_dir=str(self.out_root),
            mutate_graph=False,
            auto_mutate_on_pass=False,
        )

        # The valid document must succeed regardless of the 0-byte file result
        valid_res = res["results"].get(str(self.valid_doc.resolve()))
        self.assertIsNotNone(valid_res)
        valid_out = Path(valid_res["output_dir"])
        self.assertGreaterEqual(len(list(valid_out.glob("03_*"))), 1)

    def test_adversarial_malformed_input_corrupt_sidecar_metadata(self) -> None:
        """Category: Malformed input - corrupt JSON in metadata sidecar."""
        doc_with_corrupt_meta = self.inputs_dir / "memo_with_bad_meta.md"
        doc_with_corrupt_meta.write_text(
            "# System Memo\n\nOperational guidelines for multi-tenant data pipelines.\n",
            encoding="utf-8",
        )
        # Create malformed sidecar metadata file
        bad_meta_file = self.inputs_dir / "memo_with_bad_meta.metadata.json"
        bad_meta_file.write_text("{CORRUPTED_JSON: UNCLOSED_BRACKET", encoding="utf-8")

        items = [
            {"path": doc_with_corrupt_meta, "output_dir": str(self.out_root / "bad_meta_out"), "metadata": {"no_subfolder": True}},
            {"path": self.valid_doc, "output_dir": str(self.out_root / "valid_meta_out"), "metadata": {"no_subfolder": True}},
        ]

        res = ingest_documents_parallel(
            documents=items,
            max_workers=2,
            provider_type="offline",
            output_dir=str(self.out_root),
            mutate_graph=False,
            auto_mutate_on_pass=False,
        )

        # Corrupt sidecar on one file must not crash execution of the batch or the other document
        self.assertIn(res["status"], ("COMPLETE", "PARTIAL_SUCCESS"))
        self.assertIn(str(self.valid_doc.resolve()), res["results"])

    def test_adversarial_concurrency_race_worktree_provision_lock(self) -> None:
        """Category: Concurrency & race - high-thread concurrent lock acquisition."""
        repo_root = self.root / "fake_repo"
        repo_root.mkdir(parents=True, exist_ok=True)
        (repo_root / ".git").mkdir(parents=True, exist_ok=True)

        counter = 0
        counter_lock = threading.Lock()
        violations = []

        def worker_task(thread_id: int) -> None:
            nonlocal counter
            for _ in range(5):
                with git_worktree_provision_lock(repo_root, timeout_seconds=15.0) as lock_file:
                    with counter_lock:
                        counter += 1
                        if counter > 1:
                            violations.append(f"Overlap detected by thread {thread_id} (counter={counter})")
                    # Verify lock file has a valid PID written
                    lock_content = Path(lock_file).read_text(encoding="utf-8").strip()
                    if not lock_content.isdigit():
                        violations.append(f"Lock file missing numeric PID: {lock_content}")
                    with counter_lock:
                        counter -= 1

        threads = [threading.Thread(target=worker_task, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        self.assertEqual(len(violations), 0, f"Lock concurrency violations detected: {violations}")

    def test_adversarial_rollback_and_recovery_partial_failure(self) -> None:
        """Category: Rollback & recovery - isolated failure leaves successful items intact."""
        nonexistent_doc = self.inputs_dir / "nonexistent_memo.md"

        items = [
            {"path": nonexistent_doc, "output_dir": str(self.out_root / "none_out"), "metadata": {"no_subfolder": True}},
            {"path": self.valid_doc, "output_dir": str(self.out_root / "valid_out2"), "metadata": {"no_subfolder": True}},
        ]

        res = ingest_documents_parallel(
            documents=items,
            max_workers=2,
            provider_type="offline",
            output_dir=str(self.out_root),
            mutate_graph=False,
            auto_mutate_on_pass=False,
        )

        self.assertEqual(res["status"], "PARTIAL_SUCCESS")
        self.assertEqual(res["successful_count"], 1)
        self.assertEqual(res["failed_count"], 1)
        self.assertIn(str(nonexistent_doc.resolve()), res["failed_paths"])
        self.assertIn(str(self.valid_doc.resolve()), res["successful_paths"])

        # Valid document outputs must still be generated cleanly
        valid_out = Path(res["results"][str(self.valid_doc.resolve())]["output_dir"])
        self.assertGreaterEqual(len(list(valid_out.glob("03_*"))), 1)

    def test_adversarial_exact_production_regression_audio_wav_routing(self) -> None:
        """Category: Exact production regression - .wav file routes to AUDIO modality."""
        audio_file = self.inputs_dir / "sample_call.wav"
        audio_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00")

        ingestor = ParallelDocumentIngestor()
        tasks = ingestor._normalize_tasks(
            documents=[{"path": audio_file, "metadata": {"meeting_name": "Call_Alpha"}}],
        )
        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        meta = copy.deepcopy(task.metadata)
        modality = ingestor._resolve_task_modality(task, meta)

        # Must resolve to AUDIO modality without throwing "Audio file cannot be coerced to DOCUMENT"
        self.assertEqual(modality, CanonicalModality.AUDIO)
        self.assertFalse(meta.get("is_document", False))
        self.assertFalse(meta.get("output3_only", False))


if __name__ == "__main__":
    unittest.main()
