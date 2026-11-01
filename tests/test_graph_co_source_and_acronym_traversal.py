"""Wave 2 tests: Short-acronym literal bridging ('lens'), co-source workstream inheritance, phonetic twin resolution ('Michael Reiss' / 'Michael Rice'), and recency-ordered Tier-1 traversal."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_retrieval_core.constants import (
    LITERAL_MENTION_RELATIONSHIP,
    SPLIT_IDENTITY_RELATIONSHIP,
)
from dual_pipeline.graph_retrieval_core.graph_cache import (
    GenerationalGraphCache,
    read_graph_from_disk,
)
from dual_pipeline.query_core.constants import MAX_L2_INPUT_RECORDS_PER_LANE
from dual_pipeline.query_core.graph_first_engine import GraphFirstQueryEngine


class TestGraphCoSourceAndAcronymTraversal(unittest.TestCase):
    """Test Wave 2 graph traversal, short-acronym bridging, phonetic twin bridging, and recency ranking across all 8 adversarial categories."""

    def _build_hermetic_lens_graph(self, graph_path: Path) -> None:
        conn = sqlite3.connect(graph_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(
                """
                CREATE TABLE source (
                    source_id TEXT PRIMARY KEY,
                    source_name TEXT,
                    source_date TEXT,
                    source_posture TEXT,
                    cold_archive_ref TEXT
                );
                CREATE TABLE node (
                    node_id TEXT PRIMARY KEY,
                    canonical_name TEXT,
                    display_name TEXT,
                    node_type TEXT,
                    active INTEGER
                );
                CREATE TABLE canonical_alias (
                    alias_id TEXT PRIMARY KEY,
                    entity_id TEXT,
                    display_alias TEXT,
                    active INTEGER
                );
                CREATE TABLE entity_redirect (
                    old_node_id TEXT,
                    new_node_id TEXT
                );
                CREATE TABLE assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT,
                    predicate TEXT,
                    legacy_predicate TEXT,
                    object_node_id TEXT,
                    object_literal TEXT,
                    materiality TEXT,
                    canonical_status TEXT,
                    validation_state TEXT,
                    currentness_state TEXT,
                    retrieval_eligible INTEGER,
                    canonical_eligible INTEGER,
                    primary_source_id TEXT,
                    recorded_at TEXT,
                    created_at TEXT,
                    confidence_score REAL,
                    confidence_label TEXT
                );
                CREATE TABLE assertion_evidence (
                    assertion_id TEXT,
                    evidence_id TEXT,
                    evidence_relationship TEXT
                );
                CREATE TABLE evidence_item (
                    evidence_id TEXT PRIMARY KEY,
                    transcript_timestamp TEXT
                );
                CREATE VIEW v_canonical_edge AS
                SELECT assertion_id,
                       subject_node_id AS from_node_id,
                       predicate AS relationship_family,
                       predicate AS edge_type,
                       object_node_id AS to_node_id,
                       materiality,
                       primary_source_id AS source_id
                  FROM assertion
                 WHERE object_node_id IS NOT NULL
                   AND currentness_state = 'current'
                   AND retrieval_eligible = 1;
                CREATE VIEW v_conflicting_assertions AS
                SELECT NULL AS assertion_id_1,
                       NULL AS assertion_id_2,
                       'resolved' AS resolution_state
                 WHERE 0;
                """
            )
            conn.executemany(
                "INSERT INTO source VALUES (?, ?, ?, ?, NULL)",
                [
                    ("source-sow-old", "20260901 LENS SOW Baseline", "2026-09-01", "document-governed"),
                    ("source-e17a00483f549a6495c38d44", "20261027 Discuss Prd", "2026-10-27", "transcript-governed"),
                    ("source-d4d6a747fa66307138d4f96a", "20261027 Lens Debrief", "2026-10-27", "transcript-governed"),
                    ("source-ed01b486836314055f69c725", "20261028 Project Lens Sync", "2026-10-28", "transcript-governed"),
                ],
            )
            conn.executemany(
                "INSERT INTO node VALUES (?, ?, ?, ?, 1)",
                [
                    ("workstream_lens", "Project LENS", "Project LENS", "workstream"),
                    ("stakeholder_mandy_hopmann", "harper Hoffman", "harper Hoffman", "stakeholder"),
                    ("stakeholder_eduardo_more", "Eduardo Juan More", "Eduardo Juan More", "stakeholder"),
                    ("person_amit", "Amit Ayer", "Amit Ayer", "stakeholder"),
                    ("person_skyler", "Skyler Carlson", "Skyler Carlson", "stakeholder"),
                    ("person_chaffee", "Chaffee Burke", "Chaffee Burke", "stakeholder"),
                    ("person_rob_koch", "Rob Koch", "Rob Koch", "stakeholder"),
                    ("entity-4194e45b88e674f0b5829355b67e", "Michael Reiss", "Michael Reiss", "person"),
                    ("entity-74e59d0b1422500bfe868d4361a7", "Michael Rice", "Michael Rice", "person"),
                    ("entity_dashboard_cards", "Dashboard Cards", "Dashboard Cards", "technical_anchor"),
                    ("entity_messaging_queue", "Messaging Queue", "Messaging Queue", "technical_anchor"),
                    ("entity_sow_metrics", "SOW Metric Assessment", "SOW Metric Assessment", "workstream"),
                ],
            )
            # 8 adversarial review items across Oct 27-28 + 1 older Sept 1 claim
            assertions = [
                # Older SOW claim (to test recency ordering vs alphabetical)
                (
                    "ass-00-old-sow",
                    "workstream_lens",
                    "HAS_CLAIM_TEXT",
                    None,
                    None,
                    "Initial SOW 1 kickoff milestone baseline established in September",
                    "medium",
                    "canonical",
                    "accepted_legacy",
                    "current",
                    1,
                    1,
                    "source-sow-old",
                    "2026-09-01T10:00:00Z",
                    "2026-09-01T10:00:00Z",
                    0.90,
                    "high",
                ),
                # Cat 1 & 2: Eduardo's 5 operational requirements (Oct 28)
                (
                    "ass-cat1-eduardo-turbine",
                    "stakeholder_eduardo_more",
                    "HAS_CLAIM_TEXT",
                    None,
                    None,
                    "Skyler Carlson to confirm with engineering and display turbine number on the LENS review screen",
                    "high",
                    "canonical",
                    "accepted_legacy",
                    "current",
                    1,
                    1,
                    "source-ed01b486836314055f69c725",
                    "2026-10-28T15:00:00Z",
                    "2026-10-28T15:00:00Z",
                    0.88,
                    "high",
                ),
                # Cat 3: harper Hoffman & Michael Reiss / Rice third-party assessment (Oct 27 Discuss Prd & Debrief)
                (
                    "ass-cat3-mandy-reiss",
                    "entity-4194e45b88e674f0b5829355b67e",
                    "HAS_CLAIM_TEXT",
                    None,
                    None,
                    "harper Hoffman requires Data Scientist Michael Reiss to conduct an independent third-party assessment of the 90% golden dataset threshold",
                    "high",
                    "canonical",
                    "accepted_legacy",
                    "current",
                    1,
                    1,
                    "source-e17a00483f549a6495c38d44",
                    "2026-10-27T14:00:00Z",
                    "2026-10-27T14:00:00Z",
                    0.90,
                    "high",
                ),
                (
                    "ass-cat3-chaffee-rice",
                    "person_chaffee",
                    "HAS_CLAIM_TEXT",
                    None,
                    "entity-74e59d0b1422500bfe868d4361a7",
                    "Chaffee Burke will schedule a meeting with Michael Rice to review the LENS data",
                    "high",
                    "canonical",
                    "accepted_legacy",
                    "current",
                    1,
                    1,
                    "source-d4d6a747fa66307138d4f96a",
                    "2026-10-27T16:00:00Z",
                    "2026-10-27T16:00:00Z",
                    0.90,
                    "high",
                ),
                # Cat 4: Production Deployment & 2,500 images flowing + Amit Ayer wiring + Skyler reprocessing + Mandy data center transfer
                (
                    "ass-cat4-prod-2500",
                    "workstream_lens",
                    "HAS_CLAIM_TEXT",
                    None,
                    None,
                    "2,500 production images are flowing into production UI while Amit Ayer coordinates end-to-end pipeline wiring, Skyler Carlson verifies reprocessing, and harper Hoffman transfers materials to data centers",
                    "high",
                    "canonical",
                    "accepted_legacy",
                    "current",
                    1,
                    1,
                    "source-ed01b486836314055f69c725",
                    "2026-10-28T15:10:00Z",
                    "2026-10-28T15:10:00Z",
                    0.92,
                    "high",
                ),
                # Cat 5: UI 200-record dashboard card skew in Discuss Prd (attached to isolated node entity_dashboard_cards)
                (
                    "ass-cat5-dashboard-200-skew",
                    "entity_dashboard_cards",
                    "HAS_CLAIM_TEXT",
                    None,
                    None,
                    "Dashboard cards based on the last 200 filtered records may skew performance interpretation and must be removed from production UI",
                    "high",
                    "canonical",
                    "accepted_legacy",
                    "current",
                    1,
                    1,
                    "source-e17a00483f549a6495c38d44",
                    "2026-10-27T14:15:00Z",
                    "2026-10-27T14:15:00Z",
                    0.85,
                    "high",
                ),
                # Cat 6: Notification & Queue Routing (South messaging queue vs technology/equipment routing)
                (
                    "ass-cat6-messaging-queue",
                    "entity_messaging_queue",
                    "HAS_CLAIM_TEXT",
                    None,
                    None,
                    "Immediate storage-trigger email notifications were deferred in favor of a documented messaging queue for South while configurable routing by technology and equipment type remains open",
                    "high",
                    "canonical",
                    "accepted_legacy",
                    "current",
                    1,
                    1,
                    "source-e17a00483f549a6495c38d44",
                    "2026-10-27T14:20:00Z",
                    "2026-10-27T14:20:00Z",
                    0.88,
                    "high",
                ),
                # Cat 7: Data Retention & Rule Boundary (non-Maximo & rejected GE tags prior to Odyssey)
                (
                    "ass-cat7-data-retention",
                    "workstream_lens",
                    "HAS_CLAIM_TEXT",
                    None,
                    None,
                    "Retain all extracted nameplate data in a database beyond fields mapped to IBM Maximo including rejected General Electric tags prior to Odyssey write-back",
                    "high",
                    "canonical",
                    "accepted_legacy",
                    "current",
                    1,
                    1,
                    "source-ed01b486836314055f69c725",
                    "2026-10-28T15:20:00Z",
                    "2026-10-28T15:20:00Z",
                    0.88,
                    "high",
                ),
                # Cat 8: Executive Synthesis & Action Owner Cadence Lock (Rob Koch, Skyler Carlson, Amit Ayer twice-weekly LENS syncs)
                (
                    "ass-cat8-cadence-lock",
                    "person_rob_koch",
                    "HAS_CLAIM_TEXT",
                    None,
                    None,
                    "Rob Koch, Skyler Carlson, and Amit Ayer will establish twice-weekly recurring LENS working sessions with Eduardo Juan More",
                    "high",
                    "canonical",
                    "accepted_legacy",
                    "current",
                    1,
                    1,
                    "source-ed01b486836314055f69c725",
                    "2026-10-28T15:30:00Z",
                    "2026-10-28T15:30:00Z",
                    0.90,
                    "high",
                ),
                # Co-source anchor in Discuss Prd linking to workstream_lens
                (
                    "ass-prd-lens-anchor",
                    "workstream_lens",
                    "HAS_CLAIM_TEXT",
                    None,
                    None,
                    "Review of LENS PRD and SOW 1 accuracy metrics",
                    "medium",
                    "canonical",
                    "accepted_legacy",
                    "current",
                    1,
                    1,
                    "source-e17a00483f549a6495c38d44",
                    "2026-10-27T14:05:00Z",
                    "2026-10-27T14:05:00Z",
                    0.90,
                    "high",
                ),
            ]
            conn.executemany(
                "INSERT INTO assertion VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                assertions,
            )
            for a in assertions:
                aid = a[0]
                conn.execute("INSERT INTO evidence_item VALUES (?, '00:10:00')", (f"ev-{aid}",))
                conn.execute("INSERT INTO assertion_evidence VALUES (?, ?, 'supports')", (aid, f"ev-{aid}"))
            conn.commit()
        finally:
            conn.close()

    def test_max_l2_input_records_per_lane_raised_to_32(self) -> None:
        self.assertEqual(32, MAX_L2_INPUT_RECORDS_PER_LANE)

    def test_short_acronym_co_source_and_phonetic_twins_in_graph_cache(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wave2-graph-cache-") as tmpdir:
            graph_path = Path(tmpdir) / "graph.sqlite"
            self._build_hermetic_lens_graph(graph_path)
            cache = GenerationalGraphCache()
            nodes, assertions, supporting, adjacency, _contra, _conflicts, _excl = cache.get(graph_path)

            # 1. Verify 4-character acronym 'lens' builds virtual LITERAL_MENTION_RELATIONSHIP edges to workstream_lens
            lens_edges = adjacency["workstream_lens"]
            lens_neighbors = {e["to_node_id"] for e in lens_edges}
            self.assertIn("person_rob_koch", lens_neighbors)
            self.assertIn("stakeholder_eduardo_more", lens_neighbors)

            # 2. Verify co-source workstream inheritance connects isolated 20261027 Discuss Prd nodes within 1-2 hops of workstream_lens
            self.assertIn("entity_dashboard_cards", lens_neighbors)
            self.assertIn("entity_messaging_queue", lens_neighbors)
            self.assertIn("entity-4194e45b88e674f0b5829355b67e", lens_neighbors)

            # 3. Verify phonetic twin bridging connects Michael Reiss and Michael Rice via SPLIT_IDENTITY_RELATIONSHIP
            reiss_edges = adjacency["entity-4194e45b88e674f0b5829355b67e"]
            rice_twin_edges = [
                e
                for e in reiss_edges
                if e["to_node_id"] == "entity-74e59d0b1422500bfe868d4361a7"
                and e["relationship_family"] == SPLIT_IDENTITY_RELATIONSHIP
            ]
            self.assertTrue(rice_twin_edges)

    def test_graph_first_engine_recency_ordering_and_8_adversarial_items_recall(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wave2-gfe-") as tmpdir:
            root = Path(tmpdir)
            graph_path = root / "graph.sqlite"
            self._build_hermetic_lens_graph(graph_path)
            engine = GraphFirstQueryEngine(root, graph_path)
            res = engine.execute_query("What are the open steps and latest meeting gates to complete LENS SOW 1?")
            self.assertTrue(res.success)
            self.assertEqual(1, res.resolved_tier)

            retrieved_ids = [a["assertion_id"] for a in res.assertions]
            # Verify all 8 adversarial review categories are retrieved
            for expected_aid in (
                "ass-cat1-eduardo-turbine",
                "ass-cat3-mandy-reiss",
                "ass-cat4-prod-2500",
                "ass-cat5-dashboard-200-skew",
                "ass-cat6-messaging-queue",
                "ass-cat7-data-retention",
                "ass-cat8-cadence-lock",
            ):
                self.assertIn(expected_aid, retrieved_ids)

            # Verify recency ordering: October 28 assertions appear before September 1 baseline assertion
            idx_oct28 = retrieved_ids.index("ass-cat8-cadence-lock")
            idx_sep01 = retrieved_ids.index("ass-00-old-sow")
            self.assertLess(idx_oct28, idx_sep01)


if __name__ == "__main__":
    unittest.main()
