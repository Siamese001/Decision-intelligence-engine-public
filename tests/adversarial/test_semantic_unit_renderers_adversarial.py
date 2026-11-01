"""Adversarial and boundary tests for semantic unit renderers."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.index_core.unit_renderers import (
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


class TestSemanticUnitRenderersAdversarial(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.graph_path = Path(self.temp_dir.name) / "graph.sqlite"
        _init_test_graph_db(self.graph_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_01_boundary_empty_or_whitespace_quotes(self):
        conn = sqlite3.connect(self.graph_path)
        conn.execute("INSERT INTO node VALUES ('node-1', 'Alpha', 'system')")
        conn.execute(
            """INSERT INTO evidence_item VALUES
               ('ev-empty', 'src-1', 'sv-1', 'flow', 'f1', NULL, NULL, '10:00:00', ''),
               ('ev-spaces', 'src-1', 'sv-1', 'flow', 'f2', NULL, NULL, '10:00:05', '   \t\n   '),
               ('ev-valid', 'src-1', 'sv-1', 'flow', 'f3', NULL, NULL, '10:00:10',
                'Valid evidence quote that carries actual operational signal for retrieval.')"""
        )
        conn.execute(
            """INSERT INTO assertion VALUES
               ('asrt-1', 'node-1', 'declares', NULL, 'Valid claim text',
                'decision', 'canonical', 'approved', 'current', 'internal',
                '["internal_consulting"]', 1, NULL, NULL, '2026-10-01', 'src-1')"""
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-1', 'ev-valid', 'supports')")
        conn.commit()
        conn.close()

        passages = render_evidence_passage_records(self.graph_path, "snap-adv")
        self.assertEqual(len(passages), 1)
        self.assertIn("Valid evidence quote", passages[0]["retrieval_text"])
        self.assertNotIn("   ", passages[0]["retrieval_text"])

    def test_02_malformed_audience_json_fails_closed(self):
        conn = sqlite3.connect(self.graph_path)
        conn.execute("INSERT INTO node VALUES ('node-1', 'Alpha', 'system')")
        conn.execute(
            """INSERT INTO evidence_item VALUES
               ('ev-1', 'src-1', 'sv-1', 'flow', 'f1', NULL, NULL, '10:00:00',
                'Quote backed by malformed assertion audience metadata.')"""
        )
        # Corrupt JSON in allowed_audiences_json
        conn.execute(
            """INSERT INTO assertion VALUES
               ('asrt-1', 'node-1', 'declares', NULL, 'Valid claim',
                'decision', 'canonical', 'approved', 'current', 'internal',
                '{"corrupt": true, not valid json', 1, NULL, NULL, '2026-10-01', 'src-1')"""
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-1', 'ev-1', 'supports')")
        conn.commit()
        conn.close()

        from dual_pipeline.label_quality import LabelQualityError
        with self.assertRaises(LabelQualityError):
            render_evidence_passage_records(self.graph_path, "snap-adv")

    def test_03_invariant_bypass_unapproved_canonical_cannot_promote(self):
        conn = sqlite3.connect(self.graph_path)
        conn.execute("INSERT INTO node VALUES ('node-1', 'Alpha', 'system')")
        conn.execute(
            """INSERT INTO evidence_item VALUES
               ('ev-1', 'src-1', 'sv-1', 'flow', 'f1', NULL, NULL, '10:00:00',
                'Candidate passage backing unreviewed and needs_review canonical facts.')"""
        )
        # Needs review canonical assertion
        conn.execute(
            """INSERT INTO assertion VALUES
               ('asrt-nr', 'node-1', 'declares', NULL, 'Unreviewed claim',
                'decision', 'canonical', 'needs_review', 'current', 'internal',
                '["internal_consulting"]', 1, NULL, NULL, '2026-10-01', 'src-1')"""
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-nr', 'ev-1', 'supports')")
        conn.commit()
        conn.close()

        passages = render_evidence_passage_records(self.graph_path, "snap-adv")
        self.assertEqual(len(passages), 1)
        p = passages[0]
        # Must be in candidate lane with candidate_hypothesis answer use
        self.assertEqual(p["lane"], "candidate")
        self.assertEqual(p["answer_use"], "candidate_hypothesis")
        self.assertEqual(p["epistemic_status"], "candidate")

    def test_04_conflicting_classifications_inherits_strictest(self):
        conn = sqlite3.connect(self.graph_path)
        conn.execute("INSERT INTO node VALUES ('node-1', 'Alpha', 'system')")
        conn.execute(
            """INSERT INTO evidence_item VALUES
               ('ev-multi', 'src-1', 'sv-1', 'flow', 'f1', NULL, NULL, '10:00:00',
                'Multi-supported evidence item connecting assertions with differing security tiers.')"""
        )
        # 3 assertions supporting the same evidence with public, internal, restricted
        conn.execute(
            """INSERT INTO assertion VALUES
               ('asrt-pub', 'node-1', 'has_pub', NULL, 'Public claim',
                'decision', 'canonical', 'approved', 'current', 'public',
                '["internal_consulting"]', 1, NULL, NULL, '2026-10-01', 'src-1'),
               ('asrt-int', 'node-1', 'has_int', NULL, 'Internal claim',
                'decision', 'canonical', 'approved', 'current', 'internal',
                '["internal_consulting"]', 1, NULL, NULL, '2026-10-01', 'src-1'),
               ('asrt-res', 'node-1', 'has_res', NULL, 'Restricted claim',
                'decision', 'canonical', 'approved', 'current', 'restricted',
                '["internal_consulting"]', 1, NULL, NULL, '2026-10-01', 'src-1')"""
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-pub', 'ev-multi', 'supports')")
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-int', 'ev-multi', 'supports')")
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-res', 'ev-multi', 'supports')")
        conn.commit()
        conn.close()

        passages = render_evidence_passage_records(self.graph_path, "snap-adv")
        self.assertEqual(len(passages), 1)
        # Strictest rank must win: restricted > confidential > internal > public
        self.assertEqual(passages[0]["classification"], "restricted")

    def test_05_overflow_max_chars_window_boundary(self):
        conn = sqlite3.connect(self.graph_path)
        conn.execute("INSERT INTO node VALUES ('node-1', 'Alpha', 'system')")
        # Insert 10 quotes each 200 chars long (total 2,000 chars)
        quotes = [(f"ev-chunk-{i}", f"Segment {i}: " + ("x" * 180) + ".") for i in range(10)]
        for evid, q in quotes:
            conn.execute(
                f"""INSERT INTO evidence_item VALUES
                   ('{evid}', 'src-1', 'sv-1', 'flow', '{evid}', NULL, NULL, '10:00:00', '{q}')"""
            )
            conn.execute(
                f"""INSERT INTO assertion VALUES
                   ('asrt-{evid}', 'node-1', 'p', NULL, 'claim',
                    'decision', 'canonical', 'approved', 'current', 'internal',
                    '["internal_consulting"]', 1, NULL, NULL, '2026-10-01', 'src-1')"""
            )
            conn.execute(f"INSERT INTO assertion_evidence VALUES ('asrt-{evid}', '{evid}', 'supports')")
        conn.commit()
        conn.close()

        passages = render_evidence_passage_records(self.graph_path, "snap-adv")
        # Must partition into multiple passages without exceeding max_chars
        self.assertGreater(len(passages), 1)
        for p in passages:
            validate_artifact(p)

    def test_06_proposition_graceful_null_handling(self):
        conn = sqlite3.connect(self.graph_path)
        conn.execute("INSERT INTO node VALUES ('node-1', 'System Omega', 'system')")
        # Assertion with None scope, None speaker, None date, None object_node_id
        conn.execute(
            """INSERT INTO assertion VALUES
               ('asrt-sparse', 'node-1', 'maintains', NULL, 'System Omega maintains 99.99% availability.',
                'decision', 'canonical', 'approved', 'current', 'internal',
                '["internal_consulting"]', 1, NULL, NULL, NULL, 'src-1')"""
        )
        conn.execute(
            """INSERT INTO evidence_item VALUES
               ('ev-sparse', 'src-1', 'sv-1', 'flow', 'f1', NULL, NULL, '10:00:00',
                'Availability quote.')"""
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-sparse', 'ev-sparse', 'supports')")
        conn.commit()
        conn.close()

        propositions = render_proposition_records(self.graph_path, "snap-adv")
        self.assertEqual(len(propositions), 1)
        prop = propositions[0]
        validate_artifact(prop)
        self.assertEqual(prop["retrieval_text"], "System Omega maintains 99.99% availability.")
        self.assertNotIn("None", prop["retrieval_text"])
