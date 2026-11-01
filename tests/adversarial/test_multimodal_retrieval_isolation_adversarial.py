"""
Adversarial Edge-Case Test Suite for Wave 5: Multimodal Evidence BGE-M3 Facet Isolation.

Adversarial Scenarios Covered:
1. Corrupted zero-byte image upload handled gracefully without pipeline crash.
2. Duplicate image attachments with identical SHA-256 digests deduplicated.
3. Completely missing collateral_drop_log.json and empty metadata attachments.
4. Hostile binary extensions (.exe, .sh, .tar.gz) rejected from collateral staging.
5. Retrieval 11-gate adversarial boundary check (missing gates, latency breach, gate failure).
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.attachment_core.detector import (
    classify_attachment_type,
    discover_attachments,
    stage_attachments_into_output,
)
from dual_pipeline.attachment_core.types import AttachmentType
from dual_pipeline.release_policy import (
    RETRIEVAL_RELEASE_11_GATES,
    evaluate_retrieval_11_gates,
)
from dual_pipeline.transcript_qa_core.graph_ontology_healer import (
    heal_ontology_and_predicates,
)


class TestMultimodalRetrievalIsolationAdversarial(unittest.TestCase):
    """Adversarial stress and edge-case tests for multimodal retrieval isolation."""

    def setUp(self) -> None:
        self.test_root = Path(tempfile.mkdtemp(prefix="adv_multimodal_isolation_"))
        self.output_dir = self.test_root / "output" / "gemini_enablement" / "09-30-2026" / "Test_Session"
        self.ref_dir = self.output_dir / "referenced_artifacts"
        self.ref_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.test_root, ignore_errors=True)

    def test_adv_zero_byte_corrupted_image_handling(self) -> None:
        """Adversarial Scenario 1: Truncated zero-byte image paste does not crash discovery or staging."""
        corrupt_img = self.ref_dir / "corrupted_zero_byte_slide.png"
        corrupt_img.write_bytes(b"")

        candidates = discover_attachments(meeting_output_dir=self.output_dir)
        self.assertIn(corrupt_img.resolve(), [c.resolve() for c in candidates])

        records = stage_attachments_into_output(candidates, self.output_dir)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].file_size_bytes, 0)
        self.assertEqual(records[0].attribution_mode, "COLLATERAL_REVIEWED")

    def test_adv_duplicate_image_hashes_deduplication(self) -> None:
        """Adversarial Scenario 2: Identical file contents with different names deduplicate cleanly."""
        file1 = self.ref_dir / "slide_alpha.png"
        file2 = self.ref_dir / "slide_beta.png"
        content = b"\x89PNG\r\n\x1a\n" + b"IDENTICAL_CONTENT" * 10
        file1.write_bytes(content)
        file2.write_bytes(content)

        records = stage_attachments_into_output([file1, file2], self.output_dir)
        # Should deduplicate to a single record by SHA-256
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].attribution_mode, "COLLATERAL_REVIEWED")

    def test_adv_missing_drop_log_and_empty_metadata_fallback(self) -> None:
        """Adversarial Scenario 3: Missing drop log gracefully recovers slide topic facets."""
        data = {
            "schema_version": "5.7",
            "evidence_index": {},
            "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
        }
        changes: list[str] = []
        # No attachments in metadata, no drop log on disk
        metadata = {
            "project": "gemini_enablement",
            "source_text": "Rose Teame covered Three Parts Of Every Notebook in detail.",
            "output_dir": str(self.output_dir),
        }

        heal_ontology_and_predicates(data, data["graph_mutations"], changes, metadata=metadata)

        # Must have extracted the governed entity facet
        self.assertIn("ev-slide-three-parts", data["evidence_index"])
        self.assertEqual(data["evidence_index"]["ev-slide-three-parts"]["attribution_mode"], "COLLATERAL_REVIEWED")
        ent_ids = [e["id"] for e in data["graph_mutations"]["entity_upserts"]]
        self.assertIn("system:three-parts-of-every-notebook", ent_ids)

    def test_adv_hostile_binary_extension_rejection(self) -> None:
        """Adversarial Scenario 4: Non-collateral file extensions are rejected."""
        malicious_files = [
            self.ref_dir / "payload.exe",
            self.ref_dir / "exploit.sh",
            self.ref_dir / "archive.tar.gz",
            self.ref_dir / ".hidden_backdoor.png",
        ]
        for f in malicious_files:
            f.write_bytes(b"DATA")

        for f in malicious_files[:3]:
            self.assertEqual(classify_attachment_type(f), AttachmentType.UNKNOWN)

        from dual_pipeline.attachment_core.detector import _is_valid_attachment_file
        self.assertFalse(_is_valid_attachment_file(malicious_files[3]))

        candidates = discover_attachments(meeting_output_dir=self.output_dir)
        # None of the malicious files should be discovered
        cand_names = [c.name for c in candidates]
        for mf in malicious_files:
            self.assertNotIn(mf.name, cand_names)

    def test_adv_retrieval_11_gates_failure_and_latency_breach(self) -> None:
        """Adversarial Scenario 5: Adversarial gate failures fail closed."""
        valid_gates = {gate: True for gate in RETRIEVAL_RELEASE_11_GATES}

        # Gate failure
        for gate_to_fail in ("latency", "rank_stability", "real_bge_m3"):
            bad_gates = valid_gates.copy()
            bad_gates[gate_to_fail] = False
            passed, failures = evaluate_retrieval_11_gates(bad_gates)
            self.assertFalse(passed)
            self.assertIn(f"{gate_to_fail} (FAILED)", failures)

        # Missing gate
        incomplete_gates = valid_gates.copy()
        del incomplete_gates["bm25_execution"]
        passed, failures = evaluate_retrieval_11_gates(incomplete_gates)
        self.assertFalse(passed)
        self.assertIn("bm25_execution (MISSING)", failures)


if __name__ == "__main__":
    unittest.main()
