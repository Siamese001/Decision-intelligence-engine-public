"""Wave 3 End-to-End Tests: Connected Graph Topology, Split-Identity Bridging & Multi-Hop Predicate Alignment."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_retrieval_core.graph_cache import read_graph_from_disk
from dual_pipeline.graph_retrieval_core.recursive_traversal import (
    traverse_k_hop_neighborhood,
)
from dual_pipeline.graph_retrieval_core.scoring import unified_result_sort_key
from dual_pipeline.query_core.constants import MAX_L2_INPUT_RECORDS_PER_LANE
from dual_pipeline.query_core.planning import _resolve_query_type_blueprint


def _create_wave3_test_graph(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                node_type TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE canonical_alias (
                alias_id TEXT PRIMARY KEY,
                node_id TEXT NOT NULL,
                alias TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE entity_redirect (
                old_node_id TEXT NOT NULL,
                new_node_id TEXT NOT NULL
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                legacy_predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                scope_text TEXT,
                materiality TEXT,
                canonical_status TEXT,
                canonical_eligible INTEGER,
                validation_state TEXT,
                currentness_state TEXT,
                retrieval_eligible INTEGER,
                primary_source_id TEXT,
                recorded_at TEXT,
                created_at TEXT,
                confidence_score REAL,
                confidence_label TEXT,
                superseded_by_assertion_id TEXT
            );
            CREATE TABLE assertion_evidence (
                assertion_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                evidence_relationship TEXT NOT NULL
            );
            CREATE VIEW v_canonical_edge AS
            SELECT
                assertion_id,
                subject_node_id AS from_node_id,
                predicate AS relationship_family,
                COALESCE(legacy_predicate, predicate) AS edge_type,
                object_node_id AS to_node_id,
                materiality,
                primary_source_id AS source_id
            FROM assertion
            WHERE object_node_id IS NOT NULL
              AND canonical_status = 'canonical'
              AND currentness_state = 'current'
              AND retrieval_eligible = 1;
            CREATE VIEW v_conflicting_assertions AS
            SELECT '' AS assertion_id_1, '' AS assertion_id_2, 'resolved' AS resolution_state
            WHERE 0;
            """
        )
        # Split-identity twins for Rich Argentieri and DocIQ
        conn.executemany(
            "INSERT INTO node VALUES (?, ?, ?, 1)",
            [
                ("person:richard-argentieri", "Rich Argentieri", "person"),
                ("person_rich_argentieri", "Rich Argentieri", "stakeholder"),
                ("workstream_dociq", "DocIQ", "workstream"),
                ("system:dociq", "DocIQ", "system"),
                ("workstream_sherpa", "Project Beacon", "workstream"),
            ],
        )
        conn.execute("INSERT INTO canonical_alias VALUES ('al-1', 'workstream_dociq', 'DocIQ Platform', 1)")
        conn.execute("INSERT INTO entity_redirect VALUES ('person_rich_argentieri', 'person:richard-argentieri')")

        # 1. Edge on twin 1: person:richard-argentieri OWNS_OR_LEADS workstream_dociq
        conn.execute(
            """INSERT INTO assertion VALUES
               ('ass-rich-owns', 'person:richard-argentieri', 'OWNS_OR_LEADS', 'OWNS_OR_LEADS',
                'workstream_dociq', NULL, 'Leads DocIQ', 'high', 'canonical', 1, 'approved', 'current', 1,
                'src-1', '2026-10-01', '2026-10-01', 0.95, 'high', NULL)"""
        )
        # 2. Edge on twin 2: system:dociq DELIVERS workstream_sherpa
        conn.execute(
            """INSERT INTO assertion VALUES
               ('ass-dociq-delivers', 'system:dociq', 'DELIVERS', 'DELIVERS',
                'workstream_sherpa', NULL, 'Delivers capabilities', 'high', 'canonical', 1, 'approved', 'current', 1,
                'src-1', '2026-10-01', '2026-10-01', 0.92, 'high', NULL)"""
        )
        # 3. Literal-only HAS_CLAIM_TEXT assertion on workstream_sherpa mentioning DocIQ
        conn.execute(
            """INSERT INTO assertion VALUES
               ('ass-sherpa-literal', 'workstream_sherpa', 'HAS_CLAIM_TEXT', 'HAS_CLAIM_TEXT',
                NULL, 'Project Beacon integrates with DocIQ for document intelligence.', 'Integration claim',
                'high', 'canonical', 1, 'approved', 'current', 1,
                'src-1', '2026-10-01', '2026-10-01', 0.90, 'high', NULL)"""
        )
        # 4. Candidate assertion for candidate-lane traversal
        conn.execute(
            """INSERT INTO assertion VALUES
               ('ass-cand-bridge', 'person_rich_argentieri', 'GOVERNS', 'GOVERNS',
                'workstream_sherpa', NULL, 'Candidate governance', 'medium', 'candidate', 0, 'proposed', 'current', 1,
                'src-2', '2026-10-02', '2026-10-02', 0.80, 'medium', NULL)"""
        )
        for aid in ("ass-rich-owns", "ass-dociq-delivers", "ass-sherpa-literal", "ass-cand-bridge"):
            conn.execute("INSERT INTO assertion_evidence VALUES (?, ?, 'supports')", (aid, f"ev-{aid}"))
        conn.commit()
    finally:
        conn.close()


class Wave3ConnectedTraversalE2ETests(unittest.TestCase):
    def test_blueprint_predicates_include_all_canonical_relationships(self) -> None:
        """All query blueprints include the 20 active canonical graph predicates and current_state."""
        required_predicates = {
            "AFFILIATED_WITH",
            "SUPPORTED_BY",
            "ROUTES_TO",
            "CONTRIBUTES_TO",
            "HAS_ROLE_IN",
            "CONTEXT_FOR",
            "HAS_CLAIM_TEXT",
            "USES_PATTERN",
            "OWNS_OR_LEADS",
            "REPORTS_TO",
            "CONTAINS",
            "DEPENDS_ON",
            "HAS_RISK",
            "CANDIDATE_FOR",
            "GOVERNS",
            "DELIVERS",
            "TRANSITIONS_TO",
            "CONSTRAINS",
            "VALIDATES",
            "RELATED_TO",
        }
        for qtype in (
            "opportunity_nba",
            "gap_risk_inconsistency",
            "relationship_explanation",
            "cross_workstream_synthesis",
            "current_state",
            "current_state_corroboration",
            "project_status_current",
            "project_status_history",
            "candidate_promotion_review",
        ):
            _, rels, _, _ = _resolve_query_type_blueprint(qtype)
            self.assertTrue(
                required_predicates.issubset(set(rels)),
                f"Blueprint {qtype} missing predicates: {required_predicates - set(rels)}",
            )

    def test_split_identity_and_literal_assertion_bridging_in_graph_cache(self) -> None:
        """read_graph_from_disk connects split-identity twins and literal-only HAS_CLAIM_TEXT entity mentions."""
        with tempfile.TemporaryDirectory(prefix="wave3-cache-e2e-") as tmpdir:
            graph_path = Path(tmpdir) / "graph.sqlite"
            _create_wave3_test_graph(graph_path)
            nodes, assertions, supporting, adjacency, _, _, _ = read_graph_from_disk(graph_path)

            # Verify split-identity twin edges between person_rich_argentieri and person:richard-argentieri
            rich_neighbors = {edge["to_node_id"] for edge in adjacency["person_rich_argentieri"]}
            self.assertIn("person:richard-argentieri", rich_neighbors)

            # Verify split-identity twin edges between workstream_dociq and system:dociq
            dociq_neighbors = {edge["to_node_id"] for edge in adjacency["workstream_dociq"]}
            self.assertIn("system:dociq", dociq_neighbors)

            # Verify literal-only HAS_CLAIM_TEXT assertion on workstream_sherpa mentioning DocIQ creates a virtual edge
            sherpa_edges = adjacency["workstream_sherpa"]
            self.assertTrue(
                any(
                    edge["assertion_id"] == "ass-sherpa-literal"
                    and edge["to_node_id"] in {"workstream_dociq", "system:dociq"}
                    for edge in sherpa_edges
                )
            )

            # Verify recursive CTE traversal with split-twin and candidate expansion
            cte_res = traverse_k_hop_neighborhood(
                ["person_rich_argentieri"],
                max_hops=3,
                graph_path=graph_path,
                include_candidates=True,
                expand_split_twins=True,
            )
            self.assertIn("workstream_dociq", cte_res["nodes"])
            self.assertIn("workstream_sherpa", cte_res["nodes"])

    def test_interleaved_seed_and_expansion_ranking_survives_lane_slicing(self) -> None:
        """High-scoring multi-hop graph_expansion records survive MAX_L2_INPUT_RECORDS_PER_LANE slicing when >= 16 seeds exist."""
        seed_paths = {
            f"rec-seed-{i}": {"path_score": round(1.0 / (1.0 + i), 6)}
            for i in range(1, 21)
        }
        results = [
            {
                "record_id": f"rec-seed-{i}",
                "fused_rank": i,
                "graph_rank": None,
                "graph_score": None,
                "retrieval_origin": "hybrid_seed",
            }
            for i in range(1, 21)
        ]
        # Add 3 high-scoring multi-hop expansion records (score 0.55 > tail seeds 9..20 which have score <= 0.10)
        for j in range(1, 4):
            results.append(
                {
                    "record_id": f"rec-exp-{j}",
                    "fused_rank": None,
                    "graph_rank": j,
                    "graph_score": 0.60 - 0.02 * j,
                    "retrieval_origin": "graph_expansion",
                }
            )

        results.sort(key=lambda r: unified_result_sort_key(r, seed_paths))
        top_16_ids = [r["record_id"] for r in results[:MAX_L2_INPUT_RECORDS_PER_LANE]]
        self.assertIn("rec-exp-1", top_16_ids)
        self.assertIn("rec-exp-2", top_16_ids)
        self.assertIn("rec-exp-3", top_16_ids)
        # Top-8 seeds are also preserved
        for i in range(1, 9):
            self.assertIn(f"rec-seed-{i}", top_16_ids)


if __name__ == "__main__":
    unittest.main()
