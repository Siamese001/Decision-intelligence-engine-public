"""Unit tests for anchor expansion traversal integrity fixes (Wave 3 C4 fixes)."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.index_core.anchor_expansion import (
    expand_retrieval_anchors,
)


def _init_fixture_graph(db_path: Path) -> None:
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
            retrieval_eligible INTEGER NOT NULL DEFAULT 1,
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
    # Nodes
    conn.execute("INSERT INTO node VALUES ('node_alice', 'Alice Smith', 'person', '2026-07-09')")
    conn.execute("INSERT INTO node VALUES ('node_bob', 'Bob Jones', 'person', '2026-07-09')")
    conn.execute("INSERT INTO node VALUES ('node_charlie', 'Charlie Brown', 'person', '2026-07-09')")
    conn.execute("INSERT INTO node VALUES ('node_hub', 'PartnerAdvisory Consulting', 'org', '2026-07-09')")

    # Assertions
    # Alice reports to Bob (internal)
    conn.execute(
        """INSERT INTO assertion VALUES (
            'asrt_ab', 'node_alice', 'REPORTS_TO', 'node_bob', NULL,
            'Org structure', 'HR', '2026-07-09', 'internal', '["internal_consulting"]',
            'canonical', 'approved', 'current', 1, 'src_hr'
        )"""
    )
    conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_ab', 'ev_real_ab_101', 'supports')")

    # Bob reports to Charlie (internal) - Hop 2 from Alice
    conn.execute(
        """INSERT INTO assertion VALUES (
            'asrt_bc', 'node_bob', 'REPORTS_TO', 'node_charlie', NULL,
            'Executive leadership', 'HR', '2026-07-09', 'internal', '["internal_consulting"]',
            'canonical', 'approved', 'current', 1, 'src_hr'
        )"""
    )
    conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_bc', 'ev_real_bc_202', 'supports')")

    # Public external_approved fact
    conn.execute(
        """INSERT INTO assertion VALUES (
            'asrt_pub', 'node_alice', 'ALIGNED_TO', 'node_hub', NULL,
            'Public affiliation', 'PR', '2026-07-09', 'external_approved', '["external_public"]',
            'canonical', 'approved', 'current', 1, 'src_pr'
        )"""
    )
    conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_pub', 'ev_real_pub_303', 'supports')")

    # Hub node with 15 facts to test query-aware ordering and no arbitrary cap
    for i in range(15):
        pred = "DELIVERS_WORKSTREAM" if i == 12 else f"HUB_FACT_{i:02d}"
        claim = "Delivers Cloud Modernization" if i == 12 else f"Standard hub claim {i:02d}"
        conn.execute(
            f"""INSERT INTO assertion VALUES (
                'asrt_hub_{i:02d}', 'node_hub', '{pred}', NULL, '{claim}',
                'Hub context', 'Ops', '2026-07-09', 'internal', '["internal_consulting"]',
                'canonical', 'approved', 'current', 1, 'src_hub'
            )"""
        )
        conn.execute(
            f"INSERT INTO assertion_evidence VALUES ('asrt_hub_{i:02d}', 'ev_real_hub_{i:02d}', 'supports')"
        )

    conn.commit()
    conn.close()


class TestAnchorExpansionIntegrity(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.graph_path = Path(self.temp_dir.name) / "integrity_graph.sqlite"
        _init_fixture_graph(self.graph_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_external_approved_classification_handling(self) -> None:
        """external_approved classification must not raise KeyError and must be retrievable."""
        anchor_item = {
            "record_id": "profile_alice",
            "lane": "approved",
            "fused_score": 0.90,
            "record": {
                "record_type": "entity_facet",
                "retrieval_kind": "entity_facet",
                "classification": "public",
                "audiences": ["external_public"],
                "extensions": {"com.partner_advisory.nee.graph-entity-ids": ["node_alice"]},
            },
        }
        # Request with classification='external_approved'
        expanded = expand_retrieval_anchors(
            [anchor_item],
            self.graph_path,
            lane="approved",
            classification="external_approved",
            audiences=["external_public"],
        )
        expanded_facts = [it for it in expanded if it.get("is_anchor_expanded")]
        self.assertGreaterEqual(len(expanded_facts), 1)
        asrt_ids = [
            it["record"]["extensions"]["com.partner_advisory.nee.graph-assertion-ids"][0]
            for it in expanded_facts
        ]
        self.assertIn("asrt_pub", asrt_ids)

    def test_real_evidence_id_provenance(self) -> None:
        """All expanded assertions must carry real evidence IDs with zero synthetic strings."""
        anchor_item = {
            "record_id": "profile_alice",
            "lane": "approved",
            "fused_score": 0.90,
            "record": {
                "record_type": "entity_facet",
                "retrieval_kind": "entity_facet",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "extensions": {"com.partner_advisory.nee.graph-entity-ids": ["node_alice"]},
            },
        }
        expanded = expand_retrieval_anchors(
            [anchor_item],
            self.graph_path,
            lane="approved",
            classification="internal",
            audiences=["internal_consulting"],
        )
        expanded_facts = [it for it in expanded if it.get("is_anchor_expanded")]
        self.assertTrue(len(expanded_facts) > 0)
        for it in expanded_facts:
            rec = it["record"]
            ev_ids = rec.get("evidence_ids", [])
            self.assertTrue(len(ev_ids) > 0, "Expanded assertion missing evidence IDs!")
            for eid in ev_ids:
                self.assertFalse(
                    str(eid).startswith("evidence-ref-"),
                    f"Found synthetic evidence ID: {eid}",
                )
                self.assertTrue(
                    str(eid).startswith("ev_real_"),
                    f"Evidence ID does not match real graph provenance: {eid}",
                )
            self.assertEqual(
                rec["extensions"]["com.partner_advisory.nee.graph-evidence-ids"],
                ev_ids,
            )

    def test_both_endpoints_expanded(self) -> None:
        """Expansion from edge anchor expands incident assertions for both subject and object."""
        edge_anchor = {
            "record_id": "edge_alice_bob",
            "lane": "approved",
            "fused_score": 0.92,
            "record": {
                "record_type": "edge",
                "retrieval_kind": "edge",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "canonical_entities": ["node_alice", "node_bob"],
                "extensions": {
                    "com.partner_advisory.nee.graph-entity-ids": ["node_alice", "node_bob"],
                    "com.partner_advisory.nee.graph-object-id": "asrt_ab",
                },
            },
        }
        expanded = expand_retrieval_anchors(
            [edge_anchor],
            self.graph_path,
            lane="approved",
            classification="internal",
            audiences=["internal_consulting"],
            hop_budget=1,
        )
        expanded_facts = [it for it in expanded if it.get("is_anchor_expanded")]
        expanded_aids = [
            it["record"]["extensions"]["com.partner_advisory.nee.graph-assertion-ids"][0]
            for it in expanded_facts
        ]
        # asrt_ab was already in input as graph-object-id, so it should be deduped!
        self.assertNotIn("asrt_ab", expanded_aids, "Anchor edge was duplicated in expansion!")
        # Bob's reporting assertion (asrt_bc) must be expanded from Bob endpoint!
        self.assertIn("asrt_bc", expanded_aids, "Bob endpoint was not expanded!")

    def test_indexed_record_deduplication(self) -> None:
        """Indexed assertions already in results must not be duplicated by expansion."""
        indexed_assertion = {
            "record_id": "retrieval-record-ab",
            "lane": "approved",
            "fused_score": 0.95,
            "record": {
                "record_type": "assertion",
                "retrieval_kind": "assertion",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "canonical_entities": ["node_alice", "node_bob"],
                "extensions": {
                    "com.partner_advisory.nee.graph-assertion-ids": ["asrt_ab"],
                    "com.partner_advisory.nee.graph-object-id": "asrt_ab",
                },
            },
        }
        profile_anchor = {
            "record_id": "profile_alice",
            "lane": "approved",
            "fused_score": 0.88,
            "record": {
                "record_type": "entity_facet",
                "retrieval_kind": "entity_facet",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "extensions": {"com.partner_advisory.nee.graph-entity-ids": ["node_alice"]},
            },
        }
        expanded = expand_retrieval_anchors(
            [indexed_assertion, profile_anchor],
            self.graph_path,
            lane="approved",
            classification="internal",
            audiences=["internal_consulting"],
        )
        expanded_facts = [it for it in expanded if it.get("is_anchor_expanded")]
        expanded_aids = [
            it["record"]["extensions"]["com.partner_advisory.nee.graph-assertion-ids"][0]
            for it in expanded_facts
        ]
        self.assertNotIn("asrt_ab", expanded_aids, "Deduplication failed: indexed assertion re-emitted!")

    def test_query_aware_ordering_on_hubs(self) -> None:
        """Hub nodes order candidate assertions by query relevance, avoiding arbitrary truncation."""
        hub_anchor = {
            "record_id": "profile_hub",
            "lane": "approved",
            "fused_score": 0.90,
            "record": {
                "record_type": "entity_facet",
                "retrieval_kind": "entity_facet",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "extensions": {"com.partner_advisory.nee.graph-entity-ids": ["node_hub"]},
            },
        }
        # Query targeting Cloud Modernization (which is fact 12, beyond the default 10 cap)
        expanded = expand_retrieval_anchors(
            [hub_anchor],
            self.graph_path,
            lane="approved",
            classification="internal",
            audiences=["internal_consulting"],
            max_facts_per_anchor=5,
            query="Who delivers Cloud Modernization?",
        )
        expanded_facts = [it for it in expanded if it.get("is_anchor_expanded")]
        self.assertEqual(len(expanded_facts), 5)
        # Top ranked expanded fact must be asrt_hub_12 because it matches query tokens
        first_fact_aid = expanded_facts[0]["record"]["extensions"]["com.partner_advisory.nee.graph-assertion-ids"][0]
        self.assertEqual(
            first_fact_aid, "asrt_hub_12",
            f"Query-aware ordering failed: expected asrt_hub_12, got {first_fact_aid}",
        )

    def test_hop_budget_two_depth_expansion(self) -> None:
        """hop_budget=2 expands 2-hop connected assertions with traversal_hops=2."""
        anchor_item = {
            "record_id": "profile_alice",
            "lane": "approved",
            "fused_score": 0.90,
            "record": {
                "record_type": "entity_facet",
                "retrieval_kind": "entity_facet",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "extensions": {"com.partner_advisory.nee.graph-entity-ids": ["node_alice"]},
            },
        }
        # With hop_budget=1, only asrt_ab is reached
        exp1 = expand_retrieval_anchors(
            [anchor_item],
            self.graph_path,
            lane="approved",
            classification="internal",
            audiences=["internal_consulting"],
            hop_budget=1,
        )
        aids_hop1 = [
            it["record"]["extensions"]["com.partner_advisory.nee.graph-assertion-ids"][0]
            for it in exp1 if it.get("is_anchor_expanded")
        ]
        self.assertIn("asrt_ab", aids_hop1)
        self.assertNotIn("asrt_bc", aids_hop1, "Hop 2 reached when hop_budget=1!")

        # With hop_budget=2, asrt_bc is reached via Bob frontier
        exp2 = expand_retrieval_anchors(
            [anchor_item],
            self.graph_path,
            lane="approved",
            classification="internal",
            audiences=["internal_consulting"],
            hop_budget=2,
        )
        hop2_items = [
            it for it in exp2 if it.get("is_anchor_expanded") and it.get("traversal_hops") == 2
        ]
        self.assertTrue(len(hop2_items) > 0, "No hop 2 items found with hop_budget=2!")
        hop2_aids = [
            it["record"]["extensions"]["com.partner_advisory.nee.graph-assertion-ids"][0]
            for it in hop2_items
        ]
        self.assertIn("asrt_bc", hop2_aids)
        self.assertEqual(hop2_items[0]["record"]["extensions"]["com.partner_advisory.nee.traversal-hops"], 2)


if __name__ == "__main__":
    unittest.main()
