"""Wave 3 tests for operational, screening, commercial, and badging constraint bridges and CSR parity."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_csr_sidecar import CSRGraphReader, compile_csr_from_graph
from dual_pipeline.graph_retrieval_core.graph_cache import read_graph_from_disk


def _populate_constraint_bridge_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT,
                node_type TEXT,
                active INTEGER DEFAULT 1
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
                primary_source_id TEXT,
                recorded_at TEXT,
                created_at TEXT,
                confidence_score REAL,
                superseded_by_assertion_id TEXT
            );
            CREATE TABLE assertion_evidence (
                assertion_id TEXT,
                evidence_id TEXT,
                evidence_relationship TEXT
            );
            CREATE VIEW v_canonical_edge AS
            SELECT
                assertion_id,
                subject_node_id AS from_node_id,
                predicate AS relationship_family,
                predicate AS edge_type,
                object_node_id AS to_node_id,
                materiality,
                primary_source_id AS source_id
            FROM assertion
            WHERE object_node_id IS NOT NULL AND currentness_state = 'current';
            CREATE VIEW v_conflicting_assertions AS
            SELECT NULL AS assertion_id_1, NULL AS assertion_id_2, 'resolved' AS resolution_state
            WHERE 0;
            """
        )
        conn.executemany(
            "INSERT INTO node (node_id, canonical_name, node_type, active) VALUES (?, ?, ?, 1)",
            [
                ("entity_accelerate_power", "Accelerate Power", "entity"),
                ("person_chakra", "Chakra Sankaraiah", "person"),
                ("person_amit", "Amit Ayer", "person"),
                ("person_andrew_hanson", "Andrew Hanson", "person"),
                ("stakeholder_rianne_donst", "Rianne Donst", "stakeholder"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO assertion (
                assertion_id, subject_node_id, predicate, legacy_predicate,
                object_node_id, object_literal, materiality, canonical_status,
                validation_state, currentness_state, retrieval_eligible,
                primary_source_id, recorded_at, created_at, confidence_score
            ) VALUES (?, ?, 'HAS_CLAIM_TEXT', NULL, NULL, ?, 'high', 'canonical', 'approved', 'current', 1, ?, '2026-10-29', '2026-10-29', 0.90)
            """,
            [
                (
                    "asrt-brad-pod-pitch",
                    "entity_accelerate_power",
                    "Accelerate Power proposes forward deployed teams billing 42 hours a week at six hundred thousand per quarter.",
                    "source-brad-beau",
                ),
                (
                    "asrt-chakra-op-model",
                    "person_chakra",
                    "NEA standardizes on 2 to 3 full-stack engineers per workstream, phasing out segregated pods.",
                    "source-chakra",
                ),
                (
                    "asrt-amit-screening",
                    "person_amit",
                    "Calibrate candidate screening against NEA 5%-10% qualification bar and planted anti-patterns code review.",
                    "source-screening",
                ),
                (
                    "asrt-hanson-necap",
                    "person_andrew_hanson",
                    "NECAP vs NESOP spend: PartnerAdvisory bid onshore at 45% higher rate than offshore SoftServe.",
                    "source-sow",
                ),
                (
                    "asrt-rianne-badging",
                    "stakeholder_rianne_donst",
                    "Fieldglass badging requires 3 days per week onsite and IT begins deprovisioning two weeks prior to contract end.",
                    "source-badging",
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()


class ConstraintDomainBridgeTests(unittest.TestCase):
    def test_accelerate_power_bridges_all_four_constraint_domains_and_csr_parity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "graph.sqlite"
            csr_path = Path(tmp) / "graph.csr.bin"
            _populate_constraint_bridge_db(db_path)

            _nodes, _assertions, _sup, adjacency, _contra, _conf, _excl = read_graph_from_disk(
                db_path
            )
            seed_neighbors = {
                edge["to_node_id"] for edge in adjacency.get("entity_accelerate_power", [])
            }
            self.assertIn("person_chakra", seed_neighbors)
            self.assertIn("person_amit", seed_neighbors)
            self.assertIn("person_andrew_hanson", seed_neighbors)
            self.assertIn("stakeholder_rianne_donst", seed_neighbors)

            res = compile_csr_from_graph(db_path, csr_path)
            self.assertGreater(res.out_edge_count, 0)
            with CSRGraphReader(csr_path) as reader:
                csr_neighbors = set(reader.get_neighbors("entity_accelerate_power"))
                self.assertEqual(seed_neighbors, csr_neighbors)


if __name__ == "__main__":
    unittest.main()
