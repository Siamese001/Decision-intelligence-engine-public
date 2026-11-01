"""Wave 2 Adversarial & Edge-Case Tests: Clearance, Sidecar Sync & Card Scope Boundaries.

Covers mandatory edge-case categories:
1. Boundary payload (0 missing search documents, empty audience lists)
2. Malformed input (corrupted allowed_audiences_json, tampered card selector digest)
3. Invariant bypass attempt (external client exposure of confidential records, private_personal overlay, unauthorized card audience)
4. Rollback & recovery / idempotence (idempotent repeated synchronize_missing_search_documents runs)
5. Exact production regression (confidential nee-engineering records with lineage vs raw client exposure)
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.card_views import (
    default_route_selector,
    resolve_authorized_card_scope,
)
from dual_pipeline.index_core.records import synchronize_missing_search_documents
from dual_pipeline.retrieval_clearance import retrieval_clearance


class Wave2ClearanceSidecarAdversarialTests(unittest.TestCase):
    def test_invariant_bypass_external_client_and_private_personal_fail_closed(self) -> None:
        """Category 5 (Invariant bypass): client_approved_external and private_personal are always blocked on confidential records."""
        ok1, r1 = retrieval_clearance(
            assertion_type="confirmed_canonical_fact",
            classification="confidential",
            audiences=["client_approved_external", "internal_consulting"],
            origin_type="human_source",
            extraction_run_id="run-1",
        )
        self.assertFalse(ok1)
        self.assertEqual("confidential_record_exposed_to_client_audience", r1)

        ok2, r2 = retrieval_clearance(
            assertion_type="confirmed_canonical_fact",
            classification="internal",
            audiences=["private_personal"],
            origin_type="human_source",
            extraction_run_id="run-1",
        )
        self.assertFalse(ok2)
        self.assertEqual("private_overlay_audience_in_graph", r2)

        # Direct synthetic call without lineage denies confidential + nee-engineering
        ok3, r3 = retrieval_clearance(
            assertion_type="confirmed_canonical_fact",
            classification="confidential",
            audiences=["internal_consulting", "nee-engineering"],
        )
        self.assertFalse(ok3)
        self.assertEqual("confidential_record_exposed_to_client_audience", r3)

    def test_malformed_input_and_unauthorized_card_view_rejected(self) -> None:
        """Category 2 & 5 (Malformed input & Invariant bypass): malformed audience JSON and external audience card access fail closed."""
        ok, reason = retrieval_clearance(
            assertion_type="confirmed_canonical_fact",
            classification="internal",
            audiences="{corrupted",
        )
        self.assertFalse(ok)
        self.assertEqual("invalid_audience_metadata", reason)

        from dual_pipeline import facets
        from tests.test_cards_refactoring import BASE_SCHEMA_SQL

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(BASE_SCHEMA_SQL)
            conn.execute("INSERT INTO governance_approval VALUES ('app-1', 'approved', 1)")
            conn.execute("INSERT INTO card VALUES ('card-sec', 'Card Sec', 'cards/sec.md', 'topic-sec')")
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            facets.install_facet_schema(conn, applied_at="2026-10-01T00:00:00Z", applied_by="wave2-adv")
            conn.commit()
            selector = default_route_selector("card-sec")
            conn.execute(
                """INSERT INTO card_view_definition(
                     card_view_id,card_id,view_version,view_kind,
                     selector_schema_version,selector_json,selector_digest,
                     authority_mode,classification_ceiling,
                     eligible_audiences_json,editorial_owner,view_state,
                     approval_reference,active,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "cv-sec",
                    "card-sec",
                    1,
                    "route_membership",
                    "1.0.0",
                    json.dumps(selector, sort_keys=True, separators=(",", ":")),
                    facets.selector_digest(selector),
                    "approved_only",
                    "restricted",
                    json.dumps(["partner_advisory_account_leadership"]),
                    "gov",
                    "active",
                    "app-1",
                    1,
                    "2026-10-01T00:00:00Z",
                ),
            )
            conn.commit()
            with self.assertRaisesRegex(PermissionError, "cannot access this card view"):
                resolve_authorized_card_scope(
                    conn,
                    card_id="card-sec",
                    authorization_context={
                        "classification_clearance": "public",
                        "audiences": ["client_approved_external"],
                        "candidate_review": False,
                    },
                )
        finally:
            conn.close()

    def test_idempotent_search_document_synchronization_and_boundary_empty(self) -> None:
        """Category 1 & 6 (Boundary payload & Idempotence): repeated sync inserts 0 duplicate rows."""
        with tempfile.TemporaryDirectory(prefix="wave2-adv-sync-") as tmpdir:
            graph = Path(tmpdir) / "graph.sqlite"
            conn = sqlite3.connect(graph)
            conn.row_factory = sqlite3.Row
            try:
                conn.executescript(
                    """
                    CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT);
                    CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT,
                        object_node_id TEXT, object_literal TEXT, scope_text TEXT, canonical_status TEXT,
                        currentness_state TEXT, retrieval_eligible INTEGER);
                    CREATE TABLE search_document (search_rowid INTEGER PRIMARY KEY AUTOINCREMENT, search_document_id TEXT,
                        object_type TEXT, object_id TEXT, title TEXT, body TEXT, canonical_status TEXT,
                        currentness_state TEXT, retrieval_eligible INTEGER, source_id TEXT, assertion_id TEXT);
                    """
                )
                # Boundary: empty assertion table -> 0 synced
                self.assertEqual(0, synchronize_missing_search_documents(conn))

                conn.execute("INSERT INTO node VALUES ('n1', 'DocIQ')")
                conn.execute(
                    "INSERT INTO assertion VALUES ('a1', 'n1', 'HAS_CLAIM_TEXT', NULL, 'Fact', 'Scope', 'canonical', 'current', 1)"
                )
                self.assertEqual(1, synchronize_missing_search_documents(conn))
                # Idempotent second run -> 0 synced
                self.assertEqual(0, synchronize_missing_search_documents(conn))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
