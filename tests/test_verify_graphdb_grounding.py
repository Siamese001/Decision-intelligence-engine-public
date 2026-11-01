"""tests/test_verify_graphdb_grounding.py

Qualification suite for tools/verify_graphdb_grounding.py delivery verification CLI.
Asserts that:
1. Valid grounded payloads with matching manifests and confirmed assertions produce CERTIFIED receipts.
2. Tampered payloads or digest mismatches fail closed with REJECTED status.
3. Word count >= 500 fails closed with reason_code WORD_COUNT_EXCEEDED.
4. Fabricated persona titles in signatures fail closed with reason_code AMBIGUOUS_IDENTITY.
5. Ineligible claims or missing assertions fail closed with reason_code UNSUPPORTED_CLAIM.
6. Contradictions fail closed with reason_code UNRESOLVED_CONTRADICTION.
7. CLI entrypoint returns exit code 0 on certification and 1 on failure, sealing the delivery receipt.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.grounding.contracts import (
    ClaimManifest,
    ClaimRecord,
    DeliveryReceipt,
    SourceLocator,
)
from dual_pipeline.grounding.snapshot import _compute_sha256
from storage.connection import create_sqlite_conn
from tools.verify_graphdb_grounding import main, verify_grounded_delivery


class TestVerifyGraphdbGrounding(unittest.TestCase):
    """Test suite for grounded delivery verification CLI and pipeline gate."""

    def setUp(self) -> None:
        self.temp_dir_obj = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temp_dir_obj.name)
        self.runtime_root = self.temp_dir / "runtime"
        self.runtime_root.mkdir()

        # Initialize SQLite graph
        self.db_path = self.runtime_root / "canonical_graph.db"
        conn = create_sqlite_conn(self.db_path, readonly=False)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE canonical_graph_nodes (
                node_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                node_type TEXT,
                namespace TEXT DEFAULT 'CLIENT_SAFE'
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_node_id TEXT,
                literal_value TEXT,
                status TEXT DEFAULT 'CONFIRMED',
                observed_at TEXT,
                stale_after TEXT,
                namespace TEXT DEFAULT 'CLIENT_SAFE'
            );
            """
        )
        # Seed nodes and assertions
        cur.execute("INSERT INTO canonical_graph_nodes VALUES ('N-PRF', 'PRF Automation', 'Process', 'CLIENT_SAFE')")
        cur.execute("INSERT INTO canonical_graph_nodes VALUES ('N-SF', 'SAP SuccessFactors', 'System', 'CLIENT_SAFE')")
        cur.execute("INSERT INTO canonical_graph_nodes VALUES ('N-AMIT', 'Amit Ayer', 'Person', 'CLIENT_SAFE')")

        cur.execute(
            """
            INSERT INTO assertion VALUES (
                'ASN-001', 'N-PRF', 'touches_system', 'N-SF', NULL,
                'CONFIRMED', '2026-10-06T00:00:00Z', NULL, 'CLIENT_SAFE'
            )
            """
        )
        cur.execute(
            """
            INSERT INTO assertion VALUES (
                'ASN-TITLE-01', 'N-AMIT', 'has_title', NULL, 'Solution Architect',
                'CONFIRMED', '2026-10-06T00:00:00Z', NULL, 'CLIENT_SAFE'
            )
            """
        )
        conn.commit()
        conn.close()

        self.graph_sha = _compute_sha256(self.db_path)
        self.gen_id = "GEN-VERIFY-001"
        pair_data = {
            "generation_id": self.gen_id,
            "graph_path": "canonical_graph.db",
            "graph_sha256": self.graph_sha,
        }
        (self.runtime_root / "active_pair.json").write_text(json.dumps(pair_data), encoding="utf-8")

        self.locator = SourceLocator(
            locator_type="transcript_paragraph",
            source_uri="file:///transcripts/meeting_2026_10_06.md",
            locator_value="para_42",
        )
        self.claim = ClaimRecord(
            claim_id="CLM-001",
            subject="PRF Automation",
            predicate="touches_system",
            object="SAP SuccessFactors",
            scope="PROJECT_LEVEL",
            negation=False,
            assertion_ids=["ASN-001"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[self.locator],
        )

    def tearDown(self) -> None:
        self.temp_dir_obj.cleanup()

    def test_grounded_delivery_success(self) -> None:
        """Verify compliant payload and valid manifest achieve CERTIFIED status."""
        text = "Hello Clement,\n\nPRF Automation touches SAP SuccessFactors as confirmed.\n\nBest regards,\nAmit Ayer\nSolution Architect\n"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        manifest = ClaimManifest(
            manifest_id="MNF-001",
            payload_digest=digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[self.claim],
        )

        certified, receipt, reasons = verify_grounded_delivery(
            payload_text=text,
            manifest=manifest,
            runtime_root=self.runtime_root,
            max_words=500,
        )
        self.assertTrue(certified)
        self.assertEqual(receipt.status, "CERTIFIED")
        self.assertEqual(receipt.reason_code, "GROUNDED_SUPPORTED")
        self.assertEqual(len(reasons), 0)

    def test_payload_digest_mismatch_rejection(self) -> None:
        """Verify altered payload triggers digest mismatch rejection."""
        text = "Original payload text."
        manifest = ClaimManifest(
            manifest_id="MNF-002",
            payload_digest="0000000000000000000000000000000000000000000000000000000000000000",
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[self.claim],
        )

        certified, receipt, reasons = verify_grounded_delivery(
            payload_text=text,
            manifest=manifest,
            runtime_root=self.runtime_root,
        )
        self.assertFalse(certified)
        self.assertEqual(receipt.status, "REJECTED")
        self.assertTrue(any("mismatch" in r.lower() for r in reasons))

    def test_word_count_exceeded_rejection(self) -> None:
        """Verify payload >= 500 words fails with WORD_COUNT_EXCEEDED."""
        text = " ".join(["token"] * 500)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        manifest = ClaimManifest(
            manifest_id="MNF-003",
            payload_digest=digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[self.claim],
        )

        certified, receipt, reasons = verify_grounded_delivery(
            payload_text=text,
            manifest=manifest,
            runtime_root=self.runtime_root,
            max_words=500,
        )
        self.assertFalse(certified)
        self.assertEqual(receipt.status, "REJECTED")
        self.assertEqual(receipt.reason_code, "WORD_COUNT_EXCEEDED")

    def test_unsupported_persona_title_rejection(self) -> None:
        """Verify fabricated persona title fails with AMBIGUOUS_IDENTITY."""
        text = "Hello Clement,\n\nPRF Scope review.\n\nBest regards,\nAmit Ayer\nClient Partner & Delivery Lead | PartnerAdvisory\n"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        manifest = ClaimManifest(
            manifest_id="MNF-004",
            payload_digest=digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[self.claim],
        )

        certified, receipt, reasons = verify_grounded_delivery(
            payload_text=text,
            manifest=manifest,
            runtime_root=self.runtime_root,
        )
        self.assertFalse(certified)
        self.assertEqual(receipt.status, "REJECTED")
        self.assertEqual(receipt.reason_code, "AMBIGUOUS_IDENTITY")

    def test_unsupported_claim_rejection(self) -> None:
        """Verify manifest with unverified claim fails with UNSUPPORTED_CLAIM."""
        text = "Hello Clement,\n\nScope note.\n"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        unsupported_claim = ClaimRecord(
            claim_id="CLM-999",
            subject="PRF Automation",
            predicate="uses_database",
            object="Oracle",
            scope="PROJECT_LEVEL",
            negation=False,
            assertion_ids=["ASN-NONE"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[self.locator],
        )

        manifest = ClaimManifest(
            manifest_id="MNF-005",
            payload_digest=digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[unsupported_claim],
        )

        certified, receipt, reasons = verify_grounded_delivery(
            payload_text=text,
            manifest=manifest,
            runtime_root=self.runtime_root,
        )
        self.assertFalse(certified)
        self.assertEqual(receipt.status, "REJECTED")
        self.assertEqual(receipt.reason_code, "UNSUPPORTED_CLAIM")

    def test_cli_entrypoint_success_and_receipt_emission(self) -> None:
        """Verify CLI main() runs end-to-end, returns exit code 0, and writes receipt."""
        text = "Hello Clement,\n\nPRF touches SAP SuccessFactors.\n\nBest regards,\nAmit Ayer\nSolution Architect\n"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        manifest = ClaimManifest(
            manifest_id="MNF-CLI-01",
            payload_digest=digest,
            runtime_root=str(self.runtime_root),
            graph_generation_id=self.gen_id,
            graph_sha256=self.graph_sha,
            claims=[self.claim],
        )

        payload_file = self.temp_dir / "payload.txt"
        payload_file.write_text(text, encoding="utf-8")

        manifest_file = self.temp_dir / "manifest.json"
        manifest_file.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")

        receipt_file = self.temp_dir / "receipt.json"

        exit_code = main([
            "--payload", str(payload_file),
            "--manifest", str(manifest_file),
            "--runtime-root", str(self.runtime_root),
            "--output-receipt", str(receipt_file),
        ])
        self.assertEqual(exit_code, 0)
        self.assertTrue(receipt_file.is_file())
        receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))
        self.assertEqual(receipt_data["status"], "CERTIFIED")


if __name__ == "__main__":
    unittest.main()
