"""Unit tests for additive semantic unit renderers."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.index_core.unit_renderers import (
    render_entity_profile_records,
    render_evidence_passage_records,
    render_proposition_records,
)


def _init_test_graph_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            node_type TEXT NOT NULL
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY,
            source_id TEXT,
            source_version_id TEXT,
            locator_kind TEXT,
            locator_value TEXT,
            section_name TEXT,
            slide_number INTEGER,
            transcript_timestamp TEXT,
            direct_quote TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            assertion_type TEXT NOT NULL,
            canonical_status TEXT NOT NULL,
            validation_state TEXT NOT NULL,
            currentness_state TEXT NOT NULL,
            classification TEXT NOT NULL,
            allowed_audiences_json TEXT NOT NULL,
            retrieval_eligible INTEGER NOT NULL DEFAULT 1,
            scope_text TEXT,
            speaker_or_author TEXT,
            valid_from TEXT,
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
    conn.commit()
    conn.close()


class TestSemanticUnitRenderers(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.graph_path = Path(self.temp_dir.name) / "graph.sqlite"
        _init_test_graph_db(self.graph_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_01_passage_windowing_and_deduplication(self):
        conn = sqlite3.connect(self.graph_path)
        conn.execute("INSERT INTO node VALUES ('node-1', 'Alpha System', 'system')")
        # Add quotes across two sections
        conn.execute(
            """INSERT INTO evidence_item VALUES
               ('ev-1', 'src-1', 'sv-1', 'section', 'sec-1', 'Architecture', NULL, '10:00:00',
                'Alpha system coordinates distributed agents for real-time decision support across hybrid nodes.'),
               ('ev-2', 'src-1', 'sv-1', 'section', 'sec-1', 'Architecture', NULL, '10:00:15',
                'Every subsystem enforces cryptographic audit trails and RFC 8785 canonical digest validation.'),
               ('ev-3', 'src-1', 'sv-1', 'section', 'sec-1', 'Architecture', NULL, '10:00:30',
                'Alpha system coordinates distributed agents for real-time decision support across hybrid nodes.'),
               ('ev-4', 'src-1', 'sv-1', 'slide', 'slide-2', NULL, 2, '10:05:00',
                'Slide 2 details the fail-closed security boundary separating provider calls from deterministic indexing.')"""
        )
        conn.execute(
            """INSERT INTO assertion VALUES
               ('asrt-1', 'node-1', 'implements', NULL, 'Distributed agent architecture',
                'decision', 'canonical', 'approved', 'current', 'confidential',
                '["internal_consulting"]', 1, 'Core Architecture', 'Chief Architect', '2026-10-01', 'src-1')"""
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-1', 'ev-1', 'supports')")
        conn.commit()
        conn.close()

        passages = render_evidence_passage_records(self.graph_path, "snap-01")
        self.assertGreaterEqual(len(passages), 2)

        # Validate all artifacts against contract
        for p in passages:
            validate_artifact(p)
            self.assertEqual(p["record_type"], "source_chunk")
            self.assertEqual(p["retrieval_kind"], "evidence_span")
            self.assertIn("com.partner_advisory.nee.source-version-id", p["extensions"])

        # Check section vs slide boundary separation
        titles = [p["title"] for p in passages]
        self.assertTrue(any("Architecture" in t for t in titles))
        self.assertTrue(any("slide" in t for t in titles))

    def test_02_passage_security_inheritance(self):
        conn = sqlite3.connect(self.graph_path)
        conn.execute("INSERT INTO node VALUES ('node-sec', 'Secure Gateway', 'system')")
        conn.execute(
            """INSERT INTO evidence_item VALUES
               ('ev-sec', 'src-1', 'sv-sec', 'flow', 'flow-1', 'SecSection', NULL, '11:00:00',
                'The restricted gateway strictly prohibits unauthenticated tenant data leakage across pipelines.')"""
        )
        # Assertion has restricted classification
        conn.execute(
            """INSERT INTO assertion VALUES
               ('asrt-sec', 'node-sec', 'secures', NULL, 'Tenant boundary isolation',
                'decision', 'canonical', 'approved', 'current', 'restricted',
                '["internal_consulting", "client_confidential"]', 1, 'Security Scope', 'CISO', '2026-10-02', 'src-1')"""
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-sec', 'ev-sec', 'supports')")
        conn.commit()
        conn.close()

        passages = render_evidence_passage_records(self.graph_path, "snap-sec")
        sec_pass = [p for p in passages if "sv-sec" in p["extensions"].get("com.partner_advisory.nee.source-version-id", "")]
        self.assertEqual(len(sec_pass), 1)
        p = sec_pass[0]
        self.assertEqual(p["classification"], "restricted")
        self.assertEqual(p["lane"], "approved")
        self.assertEqual(p["answer_use"], "citation_only")

    def test_03_proposition_decontextualization_and_filtering(self):
        conn = sqlite3.connect(self.graph_path)
        conn.execute("INSERT INTO node VALUES ('node-di', 'Decision Engine', 'system')")
        # 1. Eligible proposition: decision with literal claim
        conn.execute(
            """INSERT INTO assertion VALUES
               ('asrt-prop-1', 'node-di', 'adopts', NULL, 'Zero-trust runtime verification standard',
                'decision', 'canonical', 'approved', 'current', 'internal',
                '["internal_consulting"]', 1, 'Security Governance', 'Security Lead', '2026-10-01', 'src-1')"""
        )
        conn.execute(
            """INSERT INTO evidence_item VALUES
               ('ev-prop-1', 'src-1', 'sv-1', 'section', 'sec-1', 'Sec', NULL, '12:00:00',
                'Zero-trust verification standard quote.')"""
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-prop-1', 'ev-prop-1', 'supports')")

        # 2. Ineligible structural edge: no literal claim, relationship assertion
        conn.execute(
            """INSERT INTO assertion VALUES
               ('asrt-prop-2', 'node-di', 'connects_to', 'node-other', NULL,
                'relationship_assertion', 'canonical', 'approved', 'current', 'internal',
                '["internal_consulting"]', 1, NULL, NULL, '2026-10-01', 'src-1')"""
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-prop-2', 'ev-prop-1', 'supports')")
        conn.commit()
        conn.close()

        propositions = render_proposition_records(self.graph_path, "snap-prop")
        self.assertEqual(len(propositions), 1)
        prop = propositions[0]
        validate_artifact(prop)
        self.assertEqual(prop["record_type"], "assertion")
        self.assertEqual(prop["retrieval_kind"], "assertion")
        self.assertEqual(prop["lane"], "approved")
        self.assertEqual(prop["answer_use"], "factual_answer")

        # Verify decontextualized template content
        self.assertIn("Decision Engine", prop["retrieval_text"])
        self.assertIn("Zero-trust runtime verification standard", prop["retrieval_text"])
        self.assertIn("Scope: Security Governance", prop["retrieval_text"])
        self.assertIn("Speaker: Security Lead", prop["retrieval_text"])
        self.assertIn("Date: 2026-10-01", prop["retrieval_text"])

    def test_04_reworked_entity_profile_no_prefix(self):
        # Create sample assertion records
        asrt_records = [
            {
                "record_id": "assertion-001",
                "record_type": "assertion",
                "lane": "approved",
                "retrieval_text": "Decision Engine adopts zero-trust verification standard.",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "evidence_ids": ["evidence-ref-001"],
                "extensions": {
                    "com.partner_advisory.nee.graph-object-id": "asrt-001",
                    "com.partner_advisory.nee.graph-entity-ids": ["node-di"],
                    "com.partner_advisory.nee.graph-evidence-ids": ["ev-001"],
                },
            },
            {
                "record_id": "assertion-002",
                "record_type": "assertion",
                "lane": "approved",
                "retrieval_text": "Decision Engine processes real-time telemetry streaming events.",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "evidence_ids": ["evidence-ref-002"],
                "extensions": {
                    "com.partner_advisory.nee.graph-object-id": "asrt-002",
                    "com.partner_advisory.nee.graph-entity-ids": ["node-di"],
                    "com.partner_advisory.nee.graph-evidence-ids": ["ev-002"],
                },
            },
        ]
        entity_names = {"node-di": "Decision Intelligence Engine"}
        profiles = render_entity_profile_records(asrt_records, entity_names=entity_names, graph_snapshot="snap-prof")

        self.assertEqual(len(profiles), 1)
        prof = profiles[0]
        validate_artifact(prof)
        self.assertEqual(prof["record_type"], "entity_facet")
        self.assertEqual(prof["retrieval_kind"], "entity_facet")
        self.assertEqual(prof["answer_use"], "navigation_only")

        # Crucial invariant: boilerplate prefix removed
        self.assertFalse(prof["retrieval_text"].startswith("Entity facet for"))
        self.assertTrue(prof["retrieval_text"].startswith("Entity: Decision Intelligence Engine. Facts:"))
        self.assertIn("zero-trust verification standard", prof["retrieval_text"])
        self.assertIn("real-time telemetry streaming events", prof["retrieval_text"])

        # Check required schema extensions
        ext = prof["extensions"]
        self.assertIn("com.partner_advisory.nee.entity-facet-source-record-ids", ext)
        self.assertIn("com.partner_advisory.nee.entity-facet-profile-record-ids", ext)
        self.assertEqual(len(ext["com.partner_advisory.nee.entity-facet-source-record-ids"]), 2)
        self.assertEqual(len(ext["com.partner_advisory.nee.entity-facet-profile-record-ids"]), 2)
