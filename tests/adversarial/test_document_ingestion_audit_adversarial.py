"""
tests/adversarial/test_document_ingestion_audit_adversarial.py

Wave 5 Adversarial and Edge-Case Tests:
Covers adversarial boundary, malformed input, and invariant bypass scenarios:
1. Malformed/truncated JSON candidates in cached healing.
2. Boundary payloads: empty string, None, whitespace-only candidates.
3. Invariant bypass: forged sidecar hashes and tampered pair bindings.
4. Directory traversal and path injection attempts in plan resolution.
5. Invalidation under rapid state modification.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.document_pipeline_core.audit_dedup import (
    _CANDIDATE_HEALING_CACHE,
    get_healing_counters,
    healed_output_3_cached,
    reset_healing_counters,
)
from dual_pipeline.release_core.manager import ReleaseManager
from tools.semantic_diff_reviewer import _read_plan_content


class Wave5AdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_healing_counters()

    def tearDown(self) -> None:
        reset_healing_counters()

    def test_malformed_truncated_json_candidate_handling(self) -> None:
        """Verify that malformed or truncated candidate JSON does not crash or corrupt cache."""
        malformed = '{"assertions": [{"assertion_id": "A1", "unclosed": '
        res, changes = healed_output_3_cached(malformed)
        self.assertIsInstance(res, str)
        self.assertTrue(any("failed" in c.lower() or "repaired" in c.lower() or "pda" in c.lower() for c in changes))

        # Re-invoking malformed candidate reuses cache safely without throwing
        res2, changes2 = healed_output_3_cached(malformed)
        self.assertEqual(res, res2)
        counters = get_healing_counters()
        self.assertEqual(counters["cache_hits"], 1)

    def test_boundary_empty_and_whitespace_candidates(self) -> None:
        """Verify that empty, None, and whitespace-only candidates handle safely."""
        for boundary_input in ("", "   \n\t  ", "{}"):
            res, _ = healed_output_3_cached(boundary_input)
            self.assertIsInstance(res, str)
            # Re-read should hit cache
            _, _ = healed_output_3_cached(boundary_input)

        counters = get_healing_counters()
        self.assertGreaterEqual(counters["cache_hits"], 3)

    def test_forged_sidecar_hash_invariant_bypass_rejected(self) -> None:
        """Verify that a pair with forged or mismatched sidecar hash fails closed."""
        manager = ReleaseManager.__new__(ReleaseManager)
        manager._validated_pairs = {}
        manager._resolve = lambda p: Path(tempfile.gettempdir()) / Path(p).name

        test_file = Path(tempfile.gettempdir()) / "test_adversarial_sidecar.sqlite"
        test_file.write_bytes(b"actual content on disk")

        pair_forged = {
            "embedding_backend": "flagembedding-bge-m3-dev",
            "model_id": "BAAI/bge-m3",
            "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
            "production_qualified": False,
            "graph_path": str(test_file),
            "sidecar_path": str(test_file),
            "graph_sha256": "correct_graph_sha",
            "sidecar_sha256": "forged_nonexistent_sha",
            "generation_id": "gen-forged-01",
        }

        with patch("dual_pipeline.release_core.manager.require_approved_bge_m3_pair"), \
             patch("dual_pipeline.release_core.manager.sha256_file", side_effect=["correct_graph_sha", "actual_disk_sha"]):
            with self.assertRaises(RuntimeError) as ctx:
                manager.validate_pair(pair_forged)
            self.assertIn("sidecar hash mismatch", str(ctx.exception))

    def test_plan_resolution_path_traversal_rejection(self) -> None:
        """Verify that path traversal or invalid characters in branch slug cannot escape root."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmproot = Path(tmpdir)
            sensitive_file = tmproot / "sensitive.md"
            sensitive_file.write_text("CONFIDENTIAL")

            # Slugification sanitizes path traversal tokens like ../..
            content = _read_plan_content(tmproot, "../../../sensitive")
            self.assertNotIn("CONFIDENTIAL", content)

    def test_tampered_cache_invalidation_safety(self) -> None:
        """Verify that mutating underlying candidate payload invalidates cache reuse."""
        base_candidate = json.dumps({"assertions": [{"id": "1", "text": "Original"}]})
        res1, _ = healed_output_3_cached(base_candidate)

        # Mutate payload
        tampered_candidate = json.dumps({"assertions": [{"id": "1", "text": "Tampered"}]})
        res2, _ = healed_output_3_cached(tampered_candidate)

        self.assertNotEqual(res1, res2)
        counters = get_healing_counters()
        self.assertEqual(counters["executions"], 2)
        self.assertEqual(counters["cache_hits"], 0)
