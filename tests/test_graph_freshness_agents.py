"""Unit tests for Graph Freshness Agents (Wave 3 & 4)."""
import sqlite3
import unittest

from dual_pipeline.graph_freshness_core.agents.detection import (
    AccuracyReviewer,
    EvidenceReaper,
    FreshnessWarden,
    LineageInspector,
    TopologyCartographer,
)
from dual_pipeline.graph_freshness_core.agents.supersession import SupersessionResolver
from dual_pipeline.graph_freshness_core.policy import GraphFreshnessPolicy


class TestGraphFreshnessAgents(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        cursor = self.conn.cursor()

        cursor.execute("CREATE TABLE assertion (assertion_id TEXT, currentness_state TEXT, retrieval_eligible INTEGER, canonical_status TEXT, extraction_run_id TEXT, qualifiers_json TEXT, subject_node_id TEXT, predicate TEXT, object_node_id TEXT, object_literal TEXT, primary_source_id TEXT, superseded_by_assertion_id TEXT, recorded_at TEXT)")
        cursor.execute("CREATE TABLE evidence_item (evidence_id TEXT, location_status TEXT, direct_quote TEXT)")
        cursor.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT)")
        cursor.execute("CREATE TABLE source (id TEXT, authority_rank INTEGER, published_at TEXT)")

        # Populate for EvidenceReaper (e2 is orphaned)
        cursor.execute("INSERT INTO evidence_item VALUES ('e1', 'exact', 'quote1')")
        cursor.execute("INSERT INTO evidence_item VALUES ('e2', 'exact', 'quote2')")
        cursor.execute("INSERT INTO assertion_evidence VALUES ('a1', 'e1')")

        # Populate for LineageInspector (a2 missing extraction_run_id)
        cursor.execute("INSERT INTO assertion VALUES ('a1', 'current', 1, 'candidate', 'run1', '{}', 's1', 'p1', 'o1', NULL, 'src1', NULL, date('now'))")
        cursor.execute("INSERT INTO assertion VALUES ('a2', 'current', 1, 'candidate', NULL, '{}', 's2', 'p2', 'o2', NULL, 'src1', NULL, date('now'))")

        # Populate for FreshnessWarden (a3 is very old and current, a4 has stale_after past)
        # Using -100 days for a3
        cursor.execute("INSERT INTO assertion VALUES ('a3', 'current', 1, 'candidate', 'run2', '{}', 's3', 'p3', 'o3', NULL, 'src2', NULL, date('now', '-100 days'))")
        cursor.execute("INSERT INTO assertion VALUES ('a4', 'current', 1, 'candidate', 'run2', '{\"stale_after\": \"2020-01-01\"}', 's4', 'p4', 'o4', NULL, 'src2', NULL, '2026-01-01')")

        # Populate for AccuracyReviewer (e3 has bad location, e4 has no quote)
        cursor.execute("INSERT INTO evidence_item VALUES ('e3', 'fuzzy', 'quote3')")
        cursor.execute("INSERT INTO evidence_item VALUES ('e4', 'exact', '')")

        # Populate for SupersessionResolver (s_conf, HAS_ROLE has 2 active assertions from different sources)
        cursor.execute("INSERT INTO source VALUES ('src_low', 1, '2026-01-01')")
        cursor.execute("INSERT INTO source VALUES ('src_high', 10, '2025-01-01')")
        # a_conflict_1 is newer but lower authority. a_conflict_2 is older but higher authority.
        cursor.execute("INSERT INTO assertion VALUES ('a_conflict_1', 'current', 1, 'candidate', 'run1', '{}', 's_conf', 'HAS_ROLE', 'o_new', NULL, 'src_low', NULL, '2026-02-01')")
        cursor.execute("INSERT INTO assertion VALUES ('a_conflict_2', 'current', 1, 'candidate', 'run1', '{}', 's_conf', 'HAS_ROLE', 'o_old', NULL, 'src_high', NULL, '2025-01-01')")

        self.conn.commit()
        self.policy = GraphFreshnessPolicy()

    def tearDown(self):
        self.conn.close()

    def test_evidence_reaper(self):
        agent = EvidenceReaper()
        findings = agent.scan(self.conn, self.policy)
        self.assertIn('e2', findings.evidence_ids_to_purge)
        self.assertIn('e3', findings.evidence_ids_to_purge)
        self.assertIn('e4', findings.evidence_ids_to_purge)
        self.assertNotIn('e1', findings.evidence_ids_to_purge)

    def test_freshness_warden(self):
        agent = FreshnessWarden()
        findings = agent.scan(self.conn, self.policy)
        # a3 is > 90 days old and ungrounded. a4 has stale_after = 2020-01-01.
        self.assertIn('a3', findings.stale_assertion_ids)
        self.assertIn('a4', findings.stale_assertion_ids)
        self.assertNotIn('a1', findings.stale_assertion_ids)
        self.assertNotIn('a2', findings.stale_assertion_ids)

        # Grounded old assertion without superseding assertion should go to needs_revalidation_ids, NOT stale_assertion_ids
        cur = self.conn.cursor()
        cur.execute("INSERT INTO assertion VALUES ('a5_grounded_old', 'current', 1, 'canonical', 'run1', '{}', 's5', 'p5', 'o5', NULL, 'src1', NULL, date('now', '-120 days'))")
        cur.execute("INSERT INTO assertion_evidence VALUES ('a5_grounded_old', 'e1')")
        self.conn.commit()
        findings2 = agent.scan(self.conn, self.policy)
        self.assertNotIn('a5_grounded_old', findings2.stale_assertion_ids)
        self.assertIn('a5_grounded_old', findings2.needs_revalidation_ids)

    def test_lineage_inspector(self):
        agent = LineageInspector()
        findings = agent.scan(self.conn, self.policy)
        self.assertIn('a2', findings.assertions_to_quarantine) # missing extraction run
        self.assertIn('a3', findings.assertions_to_quarantine) # no evidence linked

    def test_accuracy_reviewer(self):
        agent = AccuracyReviewer()
        findings = agent.scan(self.conn)
        self.assertIn('e3', findings.evidence_needing_review)
        self.assertIn('e4', findings.evidence_needing_review)
        self.assertNotIn('e1', findings.evidence_needing_review)

    def test_supersession_resolver(self):
        agent = SupersessionResolver()
        findings = agent.scan(self.conn, self.policy)
        # Policy is "newest_source_unless_authority_differs"
        # src_high (auth=10) vs src_low (auth=1).
        # a_conflict_2 is from src_high, so it should win over a_conflict_1.
        self.assertIn('a_conflict_1', findings.superseded_mapping)
        self.assertEqual(findings.superseded_mapping['a_conflict_1'], 'a_conflict_2')

    def test_topology_cartographer(self):
        agent = TopologyCartographer()
        findings = agent.scan(self.conn)
        # 6 assertions are current
        self.assertEqual(findings.total_edges, 6)
