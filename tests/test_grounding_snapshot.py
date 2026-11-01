"""tests/test_grounding_snapshot.py

Qualification suite for ReadOnlyGraphSnapshot and AssertionEligibilityAuditor.
Asserts that:
1. Read-only SQLite connection semantics (mode=ro, query_only=ON, busy_timeout=5000) reject writes.
2. Active-pair resolution adheres strictly to scoped runtime roots and pointer targets.
3. Graph hash mismatch, missing files, or generation drift trigger immediate rejection.
4. Entity existence alone does not certify assertions (REJECTED_ENTITY_ONLY).
5. Unqualified statuses (SUPERSEDED, RETRACTED, PROPOSED) fail closed (REJECTED_STATUS).
6. Expired assertions and restricted namespaces fail closed.
7. Contradicting assertions prevent certification (REJECTED_CONTRADICTION).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from dual_pipeline.grounding.contracts import ClaimRecord, SourceLocator
from dual_pipeline.grounding.eligibility import (
    AssertionEligibilityAuditor,
    EligibilityDecision,
)
from dual_pipeline.grounding.snapshot import (
    ReadOnlyGraphSnapshot,
    _compute_sha256,
    resolve_scoped_active_pair,
    verify_snapshot_generation,
)


class TestGroundingSnapshot(unittest.TestCase):
    """Test suite for ReadOnlyGraphSnapshot and AssertionEligibilityAuditor."""

    def setUp(self) -> None:
        self.temp_dir_obj = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.temp_dir_obj.name)

        # 1. Initialize SQLite database with canonical schema tables
        self.db_path = self.runtime_root / "canonical_graph.db"
        conn = sqlite3.connect(self.db_path)
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
                namespace TEXT DEFAULT 'CLIENT_SAFE',
                FOREIGN KEY (subject_node_id) REFERENCES canonical_graph_nodes(node_id),
                FOREIGN KEY (object_node_id) REFERENCES canonical_graph_nodes(node_id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE assertion_evidence (
                evidence_id TEXT PRIMARY KEY,
                assertion_id TEXT NOT NULL,
                source_uri TEXT NOT NULL,
                locator_value TEXT NOT NULL,
                snippet TEXT,
                FOREIGN KEY (assertion_id) REFERENCES assertion(assertion_id)
            );
            """
        )

        # Seed sample entities
        cur.execute(
            "INSERT INTO canonical_graph_nodes VALUES ('N-PRF', 'PRF Automation', 'Project', 'CLIENT_SAFE')"
        )
        cur.execute(
            "INSERT INTO canonical_graph_nodes VALUES ('N-SF', 'SAP SuccessFactors', 'System', 'CLIENT_SAFE')"
        )
        cur.execute(
            "INSERT INTO canonical_graph_nodes VALUES ('N-WD', 'Workday', 'System', 'CLIENT_SAFE')"
        )

        # Seed valid confirmed assertion
        cur.execute(
            """
            INSERT INTO assertion VALUES (
                'ASN-001', 'N-PRF', 'touches_system', 'N-SF', NULL,
                'CONFIRMED', '2026-10-06T00:00:00Z', NULL, 'CLIENT_SAFE'
            )
            """
        )

        # Seed evidence
        cur.execute(
            """
            INSERT INTO assertion_evidence VALUES (
                'EVD-001', 'ASN-001', 'file:///transcripts/meeting_2026_10_06.md', 'para_42',
                'Confirmed touches_system SAP SuccessFactors'
            )
            """
        )

        # Seed superseded assertion
        cur.execute(
            """
            INSERT INTO assertion VALUES (
                'ASN-002-SUPERSEDED', 'N-PRF', 'touches_system', 'N-WD', NULL,
                'SUPERSEDED', '2026-09-01T00:00:00Z', NULL, 'CLIENT_SAFE'
            )
            """
        )

        # Seed expired assertion
        cur.execute(
            """
            INSERT INTO assertion VALUES (
                'ASN-003-EXPIRED', 'N-PRF', 'lead_workstream', NULL, 'Consulting Team',
                'CONFIRMED', '2026-01-01T00:00:00Z', '2026-06-01T00:00:00Z', 'CLIENT_SAFE'
            )
            """
        )

        # Seed restricted internal assertion
        cur.execute(
            """
            INSERT INTO assertion VALUES (
                'ASN-004-INTERNAL', 'N-PRF', 'billing_rate', NULL, '$350/hr',
                'CONFIRMED', '2026-10-01T00:00:00Z', NULL, 'INTERNAL_ADVISORY'
            )
            """
        )

        conn.commit()
        conn.close()

        # Compute graph SHA-256
        self.graph_sha256 = _compute_sha256(self.db_path)

        # 2. Initialize active_pair.json
        self.active_pair_data = {
            "generation_id": "GEN-TEST-001",
            "graph_path": "canonical_graph.db",
            "graph_sha256": self.graph_sha256,
        }
        self.pair_file = self.runtime_root / "active_pair.json"
        self.pair_file.write_text(json.dumps(self.active_pair_data), encoding="utf-8")

        self.valid_locator = SourceLocator(
            locator_type="transcript_paragraph",
            source_uri="file:///transcripts/meeting_2026_10_06.md",
            locator_value="para_42",
        )

    def tearDown(self) -> None:
        self.temp_dir_obj.cleanup()

    def test_readonly_connection_pragmas_and_write_rejection(self) -> None:
        """Verify connection opens in mode=ro and strictly rejects writes."""
        snapshot = ReadOnlyGraphSnapshot(self.runtime_root)
        with snapshot as snap:
            conn = snap.connect()
            cur = conn.cursor()

            # Read should succeed
            cur.execute("SELECT COUNT(*) FROM canonical_graph_nodes")
            self.assertEqual(cur.fetchone()[0], 3)

            # Write should fail with readonly OperationalError
            with self.assertRaises(sqlite3.OperationalError) as ctx:
                cur.execute("INSERT INTO canonical_graph_nodes VALUES ('N-TEST', 'Test', 'System', 'CLIENT_SAFE')")
            self.assertIn("readonly", str(ctx.exception).lower())

    def test_active_pair_pointer_resolution(self) -> None:
        """Verify active_pair pointer is followed across directories."""
        pointer_dir = self.runtime_root / "pointer_dir"
        pointer_dir.mkdir()
        pointer_file = pointer_dir / "active_pair.json"
        pointer_file.write_text(
            json.dumps({"$schema": "runtime-pointer:1.0.0", "target": "../active_pair.json"}),
            encoding="utf-8",
        )

        resolved_data, target_file = resolve_scoped_active_pair(pointer_dir)
        self.assertEqual(resolved_data["generation_id"], "GEN-TEST-001")
        self.assertEqual(target_file, self.pair_file.resolve())

    def test_active_pair_rejection_on_missing_file_or_mismatch(self) -> None:
        """Verify missing pair or graph hash mismatch raises fail-closed."""
        empty_dir = self.runtime_root / "empty_dir"
        empty_dir.mkdir()
        with self.assertRaises(FileNotFoundError):
            resolve_scoped_active_pair(empty_dir)

        # Hash mismatch test
        bad_pair_data = dict(self.active_pair_data)
        bad_pair_data["graph_sha256"] = "0000000000000000000000000000000000000000000000000000000000000000"
        self.pair_file.write_text(json.dumps(bad_pair_data), encoding="utf-8")

        with self.assertRaises(ValueError) as ctx:
            ReadOnlyGraphSnapshot(self.runtime_root, enforce_pair_integrity=True)
        self.assertIn("Graph integrity mismatch", str(ctx.exception))

    def test_generation_drift_detection(self) -> None:
        """Verify generation change between snapshot capture and re-verification triggers drift."""
        snapshot = ReadOnlyGraphSnapshot(self.runtime_root)
        self.assertTrue(snapshot.is_generation_current())

        # Simulate concurrent generation bump
        mutated_pair_data = dict(self.active_pair_data)
        mutated_pair_data["generation_id"] = "GEN-TEST-002"
        self.pair_file.write_text(json.dumps(mutated_pair_data), encoding="utf-8")

        self.assertFalse(snapshot.is_generation_current())
        self.assertFalse(
            verify_snapshot_generation(
                self.runtime_root,
                "GEN-TEST-001",
                self.graph_sha256,
            )
        )

    def test_assertion_eligibility_auditor_eligible(self) -> None:
        """Verify confirmed assertion with valid locator passes eligibility."""
        snapshot = ReadOnlyGraphSnapshot(self.runtime_root)
        auditor = AssertionEligibilityAuditor()

        claim = ClaimRecord(
            claim_id="CLM-001",
            subject="PRF Automation",
            predicate="touches_system",
            object="SAP SuccessFactors",
            scope="PROJECT_LEVEL",
            negation=False,
            assertion_ids=["ASN-001"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[self.valid_locator],
        )

        res = auditor.audit_claim(claim, snapshot, audience="client_safe")
        self.assertTrue(res.is_eligible)
        self.assertEqual(res.decision, EligibilityDecision.ELIGIBLE)
        self.assertIn("ASN-001", res.matched_assertion_ids)

    def test_assertion_eligibility_auditor_entity_only_rejection(self) -> None:
        """Verify entity existence alone does NOT certify an assertion."""
        snapshot = ReadOnlyGraphSnapshot(self.runtime_root)
        auditor = AssertionEligibilityAuditor()

        # Subject 'PRF Automation' exists in canonical_graph_nodes, but predicate 'uses_database' is missing
        claim = ClaimRecord(
            claim_id="CLM-002",
            subject="PRF Automation",
            predicate="uses_database",
            object="PostgreSQL",
            scope="PROJECT_LEVEL",
            negation=False,
            assertion_ids=["ASN-FAKE"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[self.valid_locator],
        )

        res = auditor.audit_claim(claim, snapshot, audience="client_safe")
        self.assertFalse(res.is_eligible)
        self.assertEqual(res.decision, EligibilityDecision.REJECTED_ENTITY_ONLY)
        self.assertTrue(any("exists, but asserted relationship" in r for r in res.rejection_reasons))

    def test_assertion_eligibility_auditor_status_rejection(self) -> None:
        """Verify SUPERSEDED or RETRACTED assertions are rejected."""
        snapshot = ReadOnlyGraphSnapshot(self.runtime_root)
        auditor = AssertionEligibilityAuditor()

        claim = ClaimRecord(
            claim_id="CLM-003",
            subject="PRF Automation",
            predicate="touches_system",
            object="Workday",
            scope="PROJECT_LEVEL",
            negation=False,
            assertion_ids=["ASN-002-SUPERSEDED"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[self.valid_locator],
        )

        res = auditor.audit_claim(claim, snapshot, audience="client_safe")
        self.assertFalse(res.is_eligible)
        self.assertEqual(res.decision, EligibilityDecision.REJECTED_STATUS)
        self.assertTrue(any("unqualified status 'SUPERSEDED'" in r for r in res.rejection_reasons))

    def test_assertion_eligibility_auditor_staleness_rejection(self) -> None:
        """Verify expired assertion is rejected."""
        snapshot = ReadOnlyGraphSnapshot(self.runtime_root)
        auditor = AssertionEligibilityAuditor()

        claim = ClaimRecord(
            claim_id="CLM-004",
            subject="PRF Automation",
            predicate="lead_workstream",
            object="Consulting Team",
            scope="PROJECT_LEVEL",
            negation=False,
            assertion_ids=["ASN-003-EXPIRED"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[self.valid_locator],
        )

        # Audit as of today (after stale_after 2026-06-01)
        as_of = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
        res = auditor.audit_claim(claim, snapshot, audience="client_safe", as_of_time=as_of)
        self.assertFalse(res.is_eligible)
        self.assertEqual(res.decision, EligibilityDecision.REJECTED_STATUS)
        self.assertTrue(any("expired at" in r for r in res.rejection_reasons))

    def test_assertion_eligibility_auditor_audience_rejection(self) -> None:
        """Verify restricted internal assertions are blocked from client_safe audience."""
        snapshot = ReadOnlyGraphSnapshot(self.runtime_root)
        auditor = AssertionEligibilityAuditor()

        claim = ClaimRecord(
            claim_id="CLM-005",
            subject="PRF Automation",
            predicate="billing_rate",
            object="$350/hr",
            scope="PROJECT_LEVEL",
            negation=False,
            assertion_ids=["ASN-004-INTERNAL"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[self.valid_locator],
        )

        res = auditor.audit_claim(claim, snapshot, audience="client_safe")
        self.assertFalse(res.is_eligible)
        self.assertEqual(res.decision, EligibilityDecision.REJECTED_AUDIENCE)
        self.assertTrue(any("inaccessible to audience" in r for r in res.rejection_reasons))

    def test_assertion_eligibility_auditor_contradiction_rejection(self) -> None:
        """Verify claims with active contradictions fail closed."""
        snapshot = ReadOnlyGraphSnapshot(self.runtime_root)
        auditor = AssertionEligibilityAuditor()

        claim = ClaimRecord(
            claim_id="CLM-006",
            subject="PRF Automation",
            predicate="touches_system",
            object="SAP SuccessFactors",
            scope="PROJECT_LEVEL",
            negation=False,
            assertion_ids=["ASN-001"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[self.valid_locator],
            contradicting_assertion_ids=["ASN-COMPETING-001"],
        )

        res = auditor.audit_claim(claim, snapshot, audience="client_safe")
        self.assertFalse(res.is_eligible)
        self.assertEqual(res.decision, EligibilityDecision.REJECTED_CONTRADICTION)
        self.assertEqual(res.contradicting_assertion_ids, ["ASN-COMPETING-001"])

    def test_assertion_eligibility_auditor_missing_locator_rejection(self) -> None:
        """Verify claims missing source locators are rejected."""
        snapshot = ReadOnlyGraphSnapshot(self.runtime_root)
        auditor = AssertionEligibilityAuditor()

        claim = ClaimRecord(
            claim_id="CLM-007",
            subject="PRF Automation",
            predicate="touches_system",
            object="SAP SuccessFactors",
            scope="PROJECT_LEVEL",
            negation=False,
            assertion_ids=["ASN-001"],
            evidence_lane="approved",
            epistemic_status="approved",
            source_locators=[],  # Missing locator
        )

        res = auditor.audit_claim(claim, snapshot, audience="client_safe")
        self.assertFalse(res.is_eligible)
        self.assertEqual(res.decision, EligibilityDecision.REJECTED_LOCATOR)


if __name__ == "__main__":
    unittest.main()
