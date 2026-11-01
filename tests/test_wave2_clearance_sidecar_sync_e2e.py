"""Wave 2 End-to-End tests: Sidecar Eligibility, Default Audience/Classification Alignment & Card Scope Unblocking."""
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
from dual_pipeline.facets import selector_digest
from dual_pipeline.grounding.contracts import ClaimRecord, SourceLocator
from dual_pipeline.grounding.eligibility import AssertionEligibilityAuditor
from dual_pipeline.grounding.snapshot import ReadOnlyGraphSnapshot
from dual_pipeline.index_core.constants import CLASSIFICATION_RANK
from dual_pipeline.index_core.eligibility import classify_retrieval_eligibility
from dual_pipeline.index_core.records import (
    render_retrieval_records,
    synchronize_missing_search_documents,
)
from dual_pipeline.query_core.hydration import exact_retrieval
from dual_pipeline.query_u0 import QueryU0Validator


class Wave2ClearanceSidecarSyncE2ETests(unittest.TestCase):
    def test_classification_rank_and_default_operator_audience_alignment_e2e(self) -> None:
        """external_approved is ranked, and default internal operator queries access internal_consulting & nee-engineering assertions."""
        self.assertIn("external_approved", CLASSIFICATION_RANK)
        self.assertEqual(0, CLASSIFICATION_RANK["external_approved"])

        validator = QueryU0Validator(production_qualified=False)
        validated = validator.validate(
            "What is the public architecture posture?",
            classification="external_approved",
            audience="nee-engineering",
        )
        self.assertEqual("public", validated.classification)

        with tempfile.TemporaryDirectory(prefix="wave2-e2e-") as tmpdir:
            graph = Path(tmpdir) / "graph.sqlite"
            conn = sqlite3.connect(graph)
            conn.row_factory = sqlite3.Row
            try:
                conn.executescript(
                    """
                    CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, display_name TEXT, node_type TEXT, active INTEGER);
                    CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT, object_node_id TEXT,
                        object_literal TEXT, scope_text TEXT, classification TEXT, allowed_audiences_json TEXT,
                        canonical_status TEXT, canonical_eligible INTEGER, validation_state TEXT, currentness_state TEXT,
                        retrieval_eligible INTEGER, confidence_score REAL, qualifiers_json TEXT, valid_from TEXT,
                        origin_type TEXT, extraction_run_id TEXT);
                    CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT, evidence_relationship TEXT);
                    CREATE TABLE search_document (search_rowid INTEGER PRIMARY KEY AUTOINCREMENT, search_document_id TEXT,
                        object_type TEXT, object_id TEXT, title TEXT, body TEXT, canonical_status TEXT,
                        currentness_state TEXT, retrieval_eligible INTEGER, source_id TEXT, assertion_id TEXT);
                    CREATE VIRTUAL TABLE search_fts USING fts5(title, body);
                    """
                )
                conn.execute("INSERT INTO node VALUES ('n-sherpa', 'Project Beacon', 'Project Beacon', 'workstream', 1)")
                conn.execute("INSERT INTO node VALUES ('n-dociq', 'DocIQ', 'DocIQ', 'workstream', 1)")

                # 1. internal_consulting workstream assertion without search_document initially
                conn.execute(
                    """INSERT INTO assertion VALUES
                       ('ass-partner_advisory-edge', 'n-sherpa', 'DEPENDS_ON', 'n-dociq', NULL, 'Cross workstream dependency',
                        'internal', '["internal_consulting"]', 'canonical', 1, 'approved', 'current', 1, 0.92,
                        '{"dual_pipeline_candidate_type": "architecture_relationship"}', '2026-10-01', 'human_source', 'run-1')"""
                )
                # 2. confidential assertion with ["internal_consulting", "nee-engineering"]
                conn.execute(
                    """INSERT INTO assertion VALUES
                       ('ass-conf-shared', 'n-dociq', 'HAS_CLAIM_TEXT', NULL, 'DocIQ confidential roadmap', 'Roadmap scope',
                        'confidential', '["internal_consulting", "nee-engineering"]', 'canonical', 1, 'approved', 'current', 1, 0.95,
                        '{}', '2026-10-01', 'human_source', 'run-2')"""
                )
                # 3. external_approved assertion
                conn.execute(
                    """INSERT INTO assertion VALUES
                       ('ass-ext-pub', 'n-sherpa', 'HAS_CLAIM_TEXT', NULL, 'Public external benchmark', 'Benchmark',
                        'external_approved', '["nee-engineering"]', 'canonical', 1, 'approved', 'current', 1, 0.90,
                        '{}', '2026-10-01', 'human_source', 'run-3')"""
                )
                for aid in ("ass-partner_advisory-edge", "ass-conf-shared", "ass-ext-pub"):
                    conn.execute("INSERT INTO assertion_evidence VALUES (?, ?, 'supports')", (aid, f"ev-{aid}"))

                synced = synchronize_missing_search_documents(conn)
                self.assertEqual(3, synced)
                conn.commit()
            finally:
                conn.close()

            records = render_retrieval_records(graph, "wave2-test-snapshot")
            assertion_records = [r for r in records if r["record_type"] != "entity_facet"]
            self.assertEqual(3, len(assertion_records))

            # Verify default internal operator query (audience=["nee-engineering"]) retrieves both nee-engineering and internal_consulting
            exact = exact_retrieval(
                "Project Beacon DocIQ dependency benchmark",
                graph,
                classification="internal",
                audiences=["nee-engineering"],
                retrieval_mode="factual",
            )
            matched_aids = {row["assertion_id"] for row in exact["exact_anchors"]}
            self.assertIn("ass-partner_advisory-edge", matched_aids)
            self.assertIn("ass-ext-pub", matched_aids)

    def test_card_view_scope_authorizes_default_operator_audience_e2e(self) -> None:
        """All card_view_definition scopes with internal_consulting authorize default nee-engineering operator context."""
        from dual_pipeline import facets
        from tests.test_cards_refactoring import BASE_SCHEMA_SQL

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(BASE_SCHEMA_SQL)
            conn.execute("INSERT INTO governance_approval VALUES ('app-1', 'approved', 1)")
            conn.execute("INSERT INTO card VALUES ('card-01', 'Card 01', 'cards/01.md', 'topic-01')")
            conn.execute("INSERT INTO node VALUES ('n-01', 'workstream', 1)")
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            facets.install_facet_schema(conn, applied_at="2026-10-01T00:00:00Z", applied_by="wave2-test")
            conn.commit()

            selector = default_route_selector("card-01")
            s_digest = selector_digest(selector)
            conn.execute(
                """INSERT INTO card_view_definition(
                     card_view_id,card_id,view_version,view_kind,
                     selector_schema_version,selector_json,selector_digest,
                     authority_mode,classification_ceiling,
                     eligible_audiences_json,editorial_owner,view_state,
                     approval_reference,active,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "cv-01",
                    "card-01",
                    1,
                    "route_membership",
                    "1.0.0",
                    json.dumps(selector, sort_keys=True, separators=(",", ":")),
                    s_digest,
                    "approved_only",
                    "internal",
                    json.dumps(["internal_consulting"]),
                    "gov",
                    "active",
                    "app-1",
                    1,
                    "2026-10-01T00:00:00Z",
                ),
            )
            conn.execute(
                "INSERT INTO assertion VALUES ('ass-card-1', 'n-01', 'HAS_CLAIM_TEXT', 'direct_statement', 'canonical', 'current', 'accepted_legacy', 1, 1, 'internal', '[\"internal_consulting\"]', 'app-1')"
            )
            conn.execute("INSERT INTO assertion_evidence VALUES ('ass-card-1', 'ev-1', 'supports')")
            conn.execute(
                "INSERT INTO canonical_route VALUES ('rt-1', 'assertion', 'ass-card-1', 'card-01', 'topic', 1, 'active', 'test', NULL, 1, '2026-10-01T00:00:00Z')"
            )
            conn.commit()

            scope = resolve_authorized_card_scope(
                conn,
                card_id="card-01",
                authorization_context={
                    "classification_clearance": "internal",
                    "audiences": ["nee-engineering"],
                    "candidate_review": False,
                },
            )
            self.assertEqual(["ass-card-1"], scope["assertion_ids"])
        finally:
            conn.close()

    def test_grounding_eligibility_auditor_respects_candidate_lane_and_operator_audience_e2e(self) -> None:
        """AssertionEligibilityAuditor permits candidate assertions when allow_candidate_lane=True."""
        with tempfile.TemporaryDirectory(prefix="wave2-ground-e2e-") as tmpdir:
            root = Path(tmpdir)
            graph = root / "graph.sqlite"
            conn = sqlite3.connect(graph)
            try:
                conn.executescript(
                    """
                    CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, display_name TEXT, node_type TEXT, active INTEGER);
                    CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT, object_node_id TEXT,
                        object_literal TEXT, canonical_status TEXT, validation_state TEXT, currentness_state TEXT,
                        retrieval_eligible INTEGER, confidence_score REAL, namespace TEXT);
                    INSERT INTO node VALUES ('n1', 'DocIQ', 'DocIQ', 'workstream', 1);
                    INSERT INTO assertion VALUES ('a-cand-1', 'n1', 'HAS_CLAIM_TEXT', NULL, 'Candidate roadmap target',
                        'candidate', 'proposed', 'current', 1, 0.80, 'INTERNAL_CONSULTING');
                    """
                )
                conn.commit()
            finally:
                conn.close()

            (root / "active_pair.json").write_text(
                json.dumps(
                    {
                        "generation_id": "gen-wave2-test",
                        "graph_path": str(graph),
                    }
                ),
                encoding="utf-8",
            )
            snapshot = ReadOnlyGraphSnapshot(root, enforce_pair_integrity=False)
            claim = ClaimRecord(
                claim_id="cl-1",
                subject="DocIQ",
                predicate="HAS_CLAIM_TEXT",
                object="Candidate roadmap target",
                scope="PROJECT_LEVEL",
                negation=False,
                assertion_ids=["a-cand-1"],
                evidence_lane="candidate",
                epistemic_status="candidate_hypothesis",
                source_locators=[SourceLocator(source_uri="doc://1", locator_type="line", locator_value="L10")],
            )
            auditor = AssertionEligibilityAuditor(allow_candidate_lane=True)
            res = auditor.audit_claim(claim, snapshot, audience="nee-engineering")
            self.assertTrue(res.is_eligible)
            self.assertEqual(["a-cand-1"], res.matched_assertion_ids)


if __name__ == "__main__":
    unittest.main()
