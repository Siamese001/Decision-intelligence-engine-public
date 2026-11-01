"""Adversarial and boundary condition tests for anchor expansion and retrieval."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.index_core.anchor_expansion import (
    expand_retrieval_anchors,
)


def _init_test_graph(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            node_type TEXT NOT NULL,
            updated_at TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            speaker_or_author TEXT,
            valid_from TEXT,
            classification TEXT NOT NULL DEFAULT 'internal',
            allowed_audiences_json TEXT NOT NULL DEFAULT '["internal_consulting"]',
            canonical_status TEXT NOT NULL DEFAULT 'canonical',
            validation_state TEXT NOT NULL DEFAULT 'approved',
            currentness_state TEXT NOT NULL DEFAULT 'current',
            primary_source_id TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            evidence_relationship TEXT NOT NULL,
            PRIMARY KEY (assertion_id, evidence_id)
        );
        """
    )
    conn.execute("INSERT INTO node VALUES ('node_1', 'Scott Bores', 'person', '2026-07-09')")
    conn.execute("INSERT INTO node VALUES ('node_2', 'Apex Power & Grid', 'org', '2026-07-09')")
    conn.execute("INSERT INTO node VALUES ('node_secret', 'Restricted Project', 'project', '2026-07-09')")

    # Approved internal assertion
    conn.execute(
        """INSERT INTO assertion VALUES (
            'asrt_1', 'node_1', 'CONTEXT_FOR', 'node_2', 'Scott Bores is President & CEO',
            'APEX context', 'Operator', '2026-07-09', 'internal', '["internal_consulting"]',
            'canonical', 'approved', 'current', 'src_1'
        )"""
    )
    # Restricted secret assertion
    conn.execute(
        """INSERT INTO assertion VALUES (
            'asrt_secret', 'node_1', 'WORKS_ON', 'node_secret', 'Highly confidential initiative',
            'Secret', 'Operator', '2026-07-09', 'restricted', '["leadership_only"]',
            'canonical', 'approved', 'current', 'src_2'
        )"""
    )
    # Rejected assertion
    conn.execute(
        """INSERT INTO assertion VALUES (
            'asrt_rejected', 'node_1', 'ROLE', NULL, 'Disputed role assertion',
            'Dispute', 'Speaker', '2026-07-09', 'internal', '["internal_consulting"]',
            'canonical', 'rejected', 'current', 'src_3'
        )"""
    )
    # Evidence linkages
    conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_1', 'ev_1', 'supports')")
    conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_secret', 'ev_secret', 'supports')")
    conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_rejected', 'ev_rej', 'supports')")
    conn.commit()
    conn.close()


class TestAnchorExpansionAdversarial(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.graph_path = Path(self.temp_dir.name) / "test_graph.sqlite"
        _init_test_graph(self.graph_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_security_downgrade_prevention(self):
        """Passage anchor with internal clearance MUST NOT expand to restricted assertions."""
        passage_item = {
            "record_id": "passage_1",
            "lane": "approved",
            "fused_score": 0.85,
            "record": {
                "record_type": "source_chunk",
                "retrieval_kind": "evidence_span",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "extensions": {
                    "com.partner_advisory.nee.graph-assertion-ids": ["asrt_1", "asrt_secret"],
                },
            },
        }
        # Request with internal classification & internal_consulting audience
        expanded = expand_retrieval_anchors(
            [passage_item],
            self.graph_path,
            lane="approved",
            classification="internal",
            audiences=["internal_consulting"],
        )
        expanded_aids = [
            item["record"]["extensions"]["com.partner_advisory.nee.graph-assertion-ids"][0]
            for item in expanded if item.get("is_anchor_expanded")
        ]
        self.assertIn("asrt_1", expanded_aids)
        self.assertNotIn("asrt_secret", expanded_aids, "SECURITY VIOLATION: Restricted fact leaked to internal request!")

    def test_unauthorized_audience_isolation(self):
        """Anchor expansion refuses facts when audience intersection is empty."""
        passage_item = {
            "record_id": "passage_1",
            "lane": "approved",
            "fused_score": 0.85,
            "record": {
                "record_type": "source_chunk",
                "retrieval_kind": "evidence_span",
                "classification": "restricted",
                "audiences": ["leadership_only"],
                "extensions": {
                    "com.partner_advisory.nee.graph-assertion-ids": ["asrt_secret"],
                },
            },
        }
        # Request with restricted classification but wrong audience
        expanded = expand_retrieval_anchors(
            [passage_item],
            self.graph_path,
            lane="approved",
            classification="restricted",
            audiences=["external_public"],
        )
        expanded_facts = [item for item in expanded if item.get("is_anchor_expanded")]
        self.assertEqual(len(expanded_facts), 0, "AUDIENCE VIOLATION: Fact leaked without audience overlap!")

    def test_lane_isolation_rejected_facts_suppression(self):
        """Approved lane expansion MUST suppress rejected or candidate facts."""
        profile_item = {
            "record_id": "profile_node_1",
            "lane": "approved",
            "fused_score": 0.90,
            "record": {
                "record_type": "entity_facet",
                "retrieval_kind": "entity_facet",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "extensions": {
                    "com.partner_advisory.nee.graph-entity-ids": ["node_1"],
                },
            },
        }
        expanded = expand_retrieval_anchors(
            [profile_item],
            self.graph_path,
            lane="approved",
            classification="internal",
            audiences=["internal_consulting"],
        )
        expanded_aids = [
            item["record"]["extensions"]["com.partner_advisory.nee.graph-assertion-ids"][0]
            for item in expanded if item.get("is_anchor_expanded")
        ]
        self.assertIn("asrt_1", expanded_aids)
        self.assertNotIn("asrt_rejected", expanded_aids, "LANE VIOLATION: Rejected fact leaked into approved lane!")

    def test_max_facts_per_anchor_budget_bounding(self):
        """Anchor expansion caps facts per anchor to prevent denial-of-service / token explosion."""
        conn = sqlite3.connect(self.graph_path)
        for i in range(25):
            conn.execute(
                f"""INSERT INTO assertion VALUES (
                    'asrt_bulk_{i}', 'node_1', 'FACT_{i}', NULL, 'Bulk claim {i}',
                    'Bulk', 'Operator', '2026-07-09', 'internal', '["internal_consulting"]',
                    'canonical', 'approved', 'current', 'src_bulk'
                )"""
            )
            conn.execute(
                f"INSERT INTO assertion_evidence VALUES ('asrt_bulk_{i}', 'ev_bulk_{i}', 'supports')"
            )
        conn.commit()
        conn.close()

        profile_item = {
            "record_id": "profile_node_1",
            "lane": "approved",
            "fused_score": 0.90,
            "record": {
                "record_type": "entity_facet",
                "retrieval_kind": "entity_facet",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "extensions": {"com.partner_advisory.nee.graph-entity-ids": ["node_1"]},
            },
        }
        expanded = expand_retrieval_anchors(
            [profile_item],
            self.graph_path,
            lane="approved",
            classification="internal",
            audiences=["internal_consulting"],
            max_facts_per_anchor=5,
        )
        expanded_facts = [item for item in expanded if item.get("is_anchor_expanded")]
        self.assertEqual(len(expanded_facts), 5, "BOUNDING FAILURE: max_facts_per_anchor cap not enforced!")

    def test_empty_or_malformed_results_graceful_handling(self):
        """Empty results or malformed records handle gracefully without raising unhandled errors."""
        self.assertEqual(expand_retrieval_anchors([], self.graph_path, lane="approved", classification="internal", audiences=["internal_consulting"]), [])

        malformed_item = {"record_id": "bad_item", "record": {}}
        res = expand_retrieval_anchors([malformed_item], self.graph_path, lane="approved", classification="internal", audiences=["internal_consulting"])
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["record_id"], "bad_item")

    def test_invalid_classification_or_empty_audience_rejection(self):
        """Invalid security parameters fail closed with ValueError."""
        item = {"record_id": "item1", "record": {}}
        with self.assertRaises(ValueError):
            expand_retrieval_anchors([item], self.graph_path, lane="approved", classification="top_secret_invalid", audiences=["internal_consulting"])
        with self.assertRaises(ValueError):
            expand_retrieval_anchors([item], self.graph_path, lane="approved", classification="internal", audiences=[])

    def test_missing_graph_file_handling(self):
        """Missing graph file raises FileNotFoundError or OperationalError cleanly."""
        item = {"record_id": "item1", "record": {"record_type": "source_chunk", "extensions": {"com.partner_advisory.nee.graph-assertion-ids": ["asrt_1"]}}}
        with self.assertRaises((FileNotFoundError, sqlite3.OperationalError)):
            expand_retrieval_anchors([item], Path("/nonexistent/graph.sqlite"), lane="approved", classification="internal", audiences=["internal_consulting"])
