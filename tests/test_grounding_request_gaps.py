"""tests/test_grounding_request_gaps.py

Qualification suite for request-gap matrix and client data request audit.
Asserts that:
1. Questions already answered by graph assertions are identified as ANSWERED_IN_GRAPH.
2. Questions with partial coverage are classified as PARTIALLY_ANSWERED.
3. Questions with premises conflicting with graph facts are flagged as CONTRADICTED.
4. Genuinely unresolved questions are classified as UNRESOLVED_NEED with explicit scoped search evidence.
5. RequestGapMatrix aggregates counts and serializes deterministically.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.grounding.request_gaps import (
    GapClassification,
    RequestGapAuditor,
    RequestGapItem,
    RequestGapMatrix,
)
from dual_pipeline.grounding.snapshot import (
    ReadOnlyGraphSnapshot,
    _compute_sha256,
)
from storage.connection import create_sqlite_conn


class TestGroundingRequestGaps(unittest.TestCase):
    """Test suite for request gap classification and evidence matrix generation."""

    def setUp(self) -> None:
        self.temp_dir_obj = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.temp_dir_obj.name)

        # 1. Initialize SQLite database with canonical schema
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

        # Seed sample entities
        cur.execute("INSERT INTO canonical_graph_nodes VALUES ('N-PRF', 'PRF Automation', 'Process', 'CLIENT_SAFE')")
        cur.execute("INSERT INTO canonical_graph_nodes VALUES ('N-SF', 'SAP SuccessFactors', 'System', 'CLIENT_SAFE')")
        cur.execute("INSERT INTO canonical_graph_nodes VALUES ('N-SNOW', 'ServiceNow', 'System', 'CLIENT_SAFE')")

        # Seed assertions answering Q4 and parts of Q9
        cur.execute(
            """
            INSERT INTO assertion VALUES (
                'ASN-PRF-SF-01', 'N-PRF', 'touches_system', 'N-SF', NULL,
                'CONFIRMED', '2026-10-06T00:00:00Z', NULL, 'CLIENT_SAFE'
            )
            """
        )
        cur.execute(
            """
            INSERT INTO assertion VALUES (
                'ASN-PRF-SF-02', 'N-PRF', 'authoritative_system_of_record', 'N-SF', NULL,
                'CONFIRMED', '2026-10-06T00:00:00Z', NULL, 'CLIENT_SAFE'
            )
            """
        )
        cur.execute(
            """
            INSERT INTO assertion VALUES (
                'ASN-PRF-SNOW-01', 'N-PRF', 'intake_portal', 'N-SNOW', NULL,
                'CONFIRMED', '2026-10-06T00:00:00Z', NULL, 'CLIENT_SAFE'
            )
            """
        )

        conn.commit()
        conn.close()

        graph_sha = _compute_sha256(self.db_path)
        pair_data = {
            "generation_id": "GEN-GAP-TEST",
            "graph_path": "canonical_graph.db",
            "graph_sha256": graph_sha,
        }
        (self.runtime_root / "active_pair.json").write_text(json.dumps(pair_data), encoding="utf-8")
        self.snapshot = ReadOnlyGraphSnapshot(self.runtime_root)
        self.auditor = RequestGapAuditor(target_engagement="HR IT / ENTERPRISE4U")

    def tearDown(self) -> None:
        self.snapshot.close()
        self.temp_dir_obj.cleanup()

    def test_answered_question_classification(self) -> None:
        """Verify question that can be answered from graph is flagged as ANSWERED_IN_GRAPH."""
        item = self.auditor.audit_single_question(
            q_id="Q-04",
            text="Can you confirm if create position, update position and create requisition only touch SAP SuccessFactors?",
            snapshot=self.snapshot,
            key_tokens=["SAP SuccessFactors", "authoritative_system_of_record"],
        )
        self.assertEqual(item.classification, GapClassification.ANSWERED_IN_GRAPH)
        self.assertTrue(len(item.evidence_assertion_ids) >= 2)
        self.assertIn("ASN-PRF-SF-01", item.evidence_assertion_ids)
        self.assertTrue(len(item.scope_searched) > 0)

    def test_partially_answered_question_classification(self) -> None:
        """Verify question with partial coverage is classified as PARTIALLY_ANSWERED."""
        item = self.auditor.audit_single_question(
            q_id="Q-09",
            text="Does ServiceNow need to create or update an HR case or approval record?",
            snapshot=self.snapshot,
            key_tokens=["ServiceNow"],
        )
        self.assertEqual(item.classification, GapClassification.PARTIALLY_ANSWERED)
        self.assertEqual(len(item.evidence_assertion_ids), 1)
        self.assertIn("ASN-PRF-SNOW-01", item.evidence_assertion_ids)

    def test_contradicted_question_premise_classification(self) -> None:
        """Verify question assuming a contradictory premise is flagged as CONTRADICTED."""
        item = self.auditor.audit_single_question(
            q_id="Q-CONTRA",
            text="Should the position creation requisition touch Workday rather than SuccessFactors?",
            snapshot=self.snapshot,
            key_tokens=["Workday"],
            contradiction_tokens=["SAP SuccessFactors"],
        )
        self.assertEqual(item.classification, GapClassification.CONTRADICTED)
        self.assertIsNotNone(item.contradiction_details)
        self.assertTrue(len(item.evidence_assertion_ids) > 0)

    def test_unresolved_need_scoped_outcome(self) -> None:
        """Verify genuine gap question is classified as UNRESOLVED_NEED with explicit scope."""
        item = self.auditor.audit_single_question(
            q_id="Q-12",
            text="What should happen with timeouts, validation errors, and duplicate submissions?",
            snapshot=self.snapshot,
            key_tokens=["timeout", "duplicate_submission", "validation_error"],
        )
        self.assertEqual(item.classification, GapClassification.UNRESOLVED_NEED)
        self.assertEqual(len(item.evidence_assertion_ids), 0)
        self.assertIn("runtime:", item.scope_searched)
        self.assertIn("Not found in explicitly searched", item.analysis_notes)

    def test_request_gap_matrix_batch_aggregation(self) -> None:
        """Verify batch auditing aggregates counts and produces valid matrix."""
        questions = [
            {
                "question_id": "Q-04",
                "question_text": "Confirm SAP SuccessFactors touches.",
                "key_tokens": ["SAP SuccessFactors", "authoritative_system_of_record"],
            },
            {
                "question_id": "Q-09",
                "question_text": "ServiceNow integration roles.",
                "key_tokens": ["ServiceNow"],
            },
            {
                "question_id": "Q-12",
                "question_text": "Error and timeout handling policy.",
                "key_tokens": ["timeout_policy"],
            },
            {
                "question_id": "Q-CONTRA",
                "question_text": "Workday PRF integration.",
                "key_tokens": ["Workday"],
                "contradiction_tokens": ["SAP SuccessFactors"],
            },
        ]

        matrix = self.auditor.audit_question_batch(questions, self.snapshot)
        self.assertEqual(matrix.total_questions, 4)
        self.assertEqual(matrix.answered_count, 1)
        self.assertEqual(matrix.partially_answered_count, 1)
        self.assertEqual(matrix.contradicted_count, 1)
        self.assertEqual(matrix.unresolved_needs_count, 1)

        matrix_dict = matrix.to_dict()
        self.assertEqual(len(matrix_dict["items"]), 4)
        self.assertEqual(matrix_dict["target_engagement"], "HR IT / ENTERPRISE4U")


if __name__ == "__main__":
    unittest.main()
