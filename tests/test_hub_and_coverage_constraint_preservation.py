"""Wave 4 tests for hub fanout exemption, bridge scoring, and coverage gate constraint path preservation."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from dual_pipeline.core import digest
from dual_pipeline.graph_retrieval_core.scoring import _edge_increment
from dual_pipeline.graph_retrieval_core.traversal import expand_connected_retrieval
from dual_pipeline.query_core.coverage import (
    build_retrieval_evidence_report,
    compile_query_coverage_manifest,
    filter_packet_for_l2,
)


def _populate_fanout_db(db_path: Path) -> None:
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
        nodes = [
            ("entity_accelerate_power", "Accelerate Power", "entity"),
            ("person_chakra", "Chakra Sankaraiah", "person"),
        ]
        for idx in range(15):
            nodes.append((f"leaf_{idx:02d}", f"Leaf {idx:02d}", "entity"))
        conn.executemany(
            "INSERT INTO node (node_id, canonical_name, node_type, active) VALUES (?, ?, ?, 1)",
            nodes,
        )
        # 15 ordinary degree-1 edges on entity_accelerate_power that would otherwise exhaust maximum_node_fanout=12
        for idx in range(15):
            conn.execute(
                """
                INSERT INTO assertion (
                    assertion_id, subject_node_id, predicate, legacy_predicate,
                    object_node_id, object_literal, materiality, canonical_status,
                    validation_state, currentness_state, retrieval_eligible,
                    primary_source_id, recorded_at, created_at, confidence_score
                ) VALUES (?, 'entity_accelerate_power', 'MENTIONS', NULL, ?, NULL, 'medium', 'canonical', 'approved', 'current', 1, 'src-1', '2026-10-29', '2026-10-29', 0.85)
                """,
                (f"asrt-leaf-{idx:02d}", f"leaf_{idx:02d}"),
            )
        # Seed claim and bridged constraint claim
        conn.executemany(
            """
            INSERT INTO assertion (
                assertion_id, subject_node_id, predicate, legacy_predicate,
                object_node_id, object_literal, materiality, canonical_status,
                validation_state, currentness_state, retrieval_eligible,
                primary_source_id, recorded_at, created_at, confidence_score
            ) VALUES (?, ?, 'HAS_CLAIM_TEXT', NULL, NULL, ?, 'high', 'canonical', 'approved', 'current', 1, ?, '2026-10-29', '2026-10-29', 0.92)
            """,
            [
                (
                    "asrt-seed-pod",
                    "entity_accelerate_power",
                    "Accelerate Power proposes forward deployed teams billing 42 hours a week.",
                    "src-brad",
                ),
                (
                    "asrt-constraint-chakra",
                    "person_chakra",
                    "NEA standardizes on 2 to 3 full-stack engineers per workstream, phasing out segregated pods.",
                    "src-chakra",
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()


class HubAndCoverageConstraintPreservationTests(unittest.TestCase):
    def test_capability_domain_edge_exempt_from_hub_penalty(self) -> None:
        cap_edge = {
            "relationship_family": "SHARES_CAPABILITY_DOMAIN",
            "evidence_ids": ["ev-1"],
            "materiality": "high",
            "confidence_score": 0.9,
        }
        inc_low_deg, _ = _edge_increment(
            cap_edge,
            requested_relationships=set(),
            destination_degree=2,
            hop=1,
        )
        inc_high_deg, _ = _edge_increment(
            cap_edge,
            requested_relationships=set(),
            destination_degree=250,
            hop=1,
        )
        self.assertAlmostEqual(inc_low_deg, inc_high_deg, places=6)

    def test_capability_bridge_survives_node_fanout_cap_of_12(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "graph.sqlite"
            sidecar_path = Path(tmp) / "sidecar.sqlite"
            _populate_fanout_db(db_path)
            seed_rec = {
                "record_id": "assertion:asrt-seed-pod",
                "assertion_id": "asrt-seed-pod",
                "lane": "approved",
                "entity_ids": ["entity_accelerate_power"],
                "retrieval_text": "Accelerate Power proposes forward deployed teams billing 42 hours a week.",
                "extensions": {
                    "com.partner_advisory.nee.graph-object-id": "asrt-seed-pod",
                    "com.partner_advisory.nee.graph-entity-ids": ["entity_accelerate_power"],
                },
            }
            chakra_rec = {
                "record_id": "assertion:asrt-constraint-chakra",
                "assertion_id": "asrt-constraint-chakra",
                "lane": "approved",
                "entity_ids": ["person_chakra"],
                "retrieval_text": "NEA standardizes on 2 to 3 full-stack engineers per workstream, phasing out segregated pods.",
                "extensions": {
                    "com.partner_advisory.nee.graph-object-id": "asrt-constraint-chakra",
                    "com.partner_advisory.nee.graph-entity-ids": ["person_chakra"],
                },
            }
            records_by_id = {
                "assertion:asrt-seed-pod": seed_rec,
                "assertion:asrt-constraint-chakra": chakra_rec,
            }
            records_by_assertion = {
                "asrt-seed-pod": ("assertion:asrt-seed-pod", seed_rec),
                "asrt-constraint-chakra": ("assertion:asrt-constraint-chakra", chakra_rec),
            }
            initial_retrieval = {
                "retrieval_mode": "approved_only",
                "allowed_lanes": ["approved"],
                "lanes": {
                    "approved": {
                        "results": [
                            {
                                "record_id": "assertion:asrt-seed-pod",
                                "lane": "approved",
                                "fused_rank": 1,
                                "score": 0.95,
                                "rank": 1,
                                "record": seed_rec,
                            }
                        ]
                    }
                },
            }
            with (
                patch(
                    "dual_pipeline.graph_retrieval_core.traversal._authorized_records",
                    return_value=(records_by_id, records_by_assertion),
                ),
                patch(
                    "dual_pipeline.graph_retrieval_core.clearance_cache.record_tokens_cached",
                    return_value={},
                ),
            ):
                expanded, connected_map = expand_connected_retrieval(
                    retrieval=initial_retrieval,
                    question="Provide honest analysis of Accelerate Power local pods.",
                    graph_path=db_path,
                    sidecar_path=sidecar_path,
                    run_id="run-wave4-test",
                    graph_snapshot="snap-wave4",
                    query_plan_digest="a" * 64,
                    requested_hops=2,
                    relationships_to_inspect=["DEPENDS_ON"],
                    classification="internal",
                    audiences=["executive"],
                    retrieval_mode="approved_only",
                    allowed_assertion_ids=None,
                )
            expanded_ids = {
                row["record_id"] for row in expanded["lanes"]["approved"]["results"]
            }
            self.assertIn("assertion:asrt-constraint-chakra", expanded_ids)
            self.assertTrue(len(connected_map) > 0)

    def test_coverage_gate_and_filter_packet_preserve_constraint_and_graph_paths(self) -> None:
        plan = {
            "run_id": "run-wave4-cov",
            "query_type": "general_query",
            "allowed_lanes": ["approved"],
            "materiality": "high",
            "evidence_requirements": ["Approved evidence for Accelerate Power"],
            "canonical_digest": {"value": "b" * 64},
        }
        active = {
            "graph_snapshot": "snap-wave4",
            "graph_sha256": "c" * 64,
        }
        manifest = compile_query_coverage_manifest(
            question="Assess Accelerate Power local pods",
            plan=plan,
            active=active,
            validated_request_digest="d" * 64,
            authorization_scope_digest="e" * 64,
        )
        seed_row = {
            "record": {
                "record_id": "assertion:asrt-seed-pod",
                "title": "Accelerate Power pod proposal",
                "retrieval_text": "Accelerate Power proposes local West Palm Beach pods.",
                "record_type": "canonical_assertion",
                "answer_use": "current_state_fact",
                "strategic_profile": {"knowledge_role": "fact"},
            },
            "evidence": [{"source_id": "src-brad"}],
        }
        constraint_row = {
            "record": {
                "record_id": "assertion:asrt-constraint-chakra",
                "title": "NEA workstream staffing ratio",
                "retrieval_text": "NEA standardizes on 2 to 3 full-stack engineers per workstream, phasing out segregated pods.",
                "record_type": "canonical_assertion",
                "answer_use": "decision_constraint",
                "strategic_profile": {"knowledge_role": "constraint"},
            },
            "evidence": [{"source_id": "src-chakra"}],
        }
        packet = {
            "graph_snapshot": "snap-wave4",
            "allowed_lanes": ["approved"],
            "approved": [seed_row, constraint_row],
            "candidate": [],
            "external_public": [],
            "counterevidence": [],
            "graph_paths": [
                {
                    "record_id": "assertion:asrt-constraint-chakra",
                    "seed_record_id": "assertion:asrt-seed-pod",
                    "hop_count": 1,
                    "steps": [
                        {
                            "from_node_id": "entity_accelerate_power",
                            "to_node_id": "person_chakra",
                            "relationship_family": "SHARES_CAPABILITY_DOMAIN",
                            "traversal_direction": "reverse",
                        }
                    ],
                }
            ],
        }
        packet["packet_digest"] = digest(
            {k: v for k, v in packet.items() if k != "packet_digest"}
        )
        report = build_retrieval_evidence_report(
            coverage_manifest=manifest,
            packet=packet,
            authorization_scope_digest="e" * 64,
            graph_sha256="c" * 64,
            view_authorized=True,
        )
        filtered = filter_packet_for_l2(packet, report)
        retained_ids = {
            row["record"]["record_id"] for row in filtered["approved"]
        }
        self.assertIn("assertion:asrt-seed-pod", retained_ids)
        self.assertIn("assertion:asrt-constraint-chakra", retained_ids)
        self.assertEqual(len(filtered["graph_paths"]), 1)
        self.assertEqual(
            filtered["graph_paths"][0]["record_id"],
            "assertion:asrt-constraint-chakra",
        )


if __name__ == "__main__":
    unittest.main()
