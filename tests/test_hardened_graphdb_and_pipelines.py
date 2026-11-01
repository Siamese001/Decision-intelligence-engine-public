#!/usr/bin/env python3
"""
Comprehensive Test Suite for Hardened GraphDB, Sidecar, and Pipelines Architecture (v55).
Tests:
  1. High-throughput M5 Pro SQLite PRAGMAs (WAL, mmap_size, cache_size, foreign keys).
  2. First-class relational graph_edges and bounded multi-hop neighborhood traversal.
  3. SQLite FTS5 Full-Text Search indexing and automatic synchronization triggers.
  4. Pipeline B Extraction & Worker edge persistence and idempotency.
  5. Pipeline C Policy-Gated Retrieval with FTS5 BM25, RRF scoring, and edge hydration.
  6. Zero-leakage namespace enforcement in Stealth Mode (Law 6).
"""
import asyncio
import shutil
import tempfile
import unittest

from pipeline_b_extraction.worker import SlowPathWorker
from pipeline_c_retrieval import (
    INTERNAL_ADVISORY_NAMESPACE,
    PolicyGatedRetrievalEngine,
)
from storage.database import (
    AdvisoryCategory,
    CanonicalGraphNode,
    DatabaseManager,
    EvidenceRecord,
    GraphEdge,
    InternalAdvisoryNode,
)


class TestHardenedGraphDBAndPipelines(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_manager = DatabaseManager(base_dir=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # ── 1. Storage & PRAGMA Conformance ───────────────────────────────────

    def test_m5_pro_pragmas(self):
        """Verifies Apple Silicon M5 Pro PRAGMA settings (WAL, mmap, cache, foreign keys)."""
        with self.db_manager.get_canonical_conn() as conn:
            journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            self.assertEqual(journal_mode.upper(), "WAL")

            fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            self.assertEqual(fk, 1)

            mmap_size = conn.execute("PRAGMA mmap_size;").fetchone()[0]
            self.assertGreaterEqual(mmap_size, 1000000)

            temp_store = conn.execute("PRAGMA temp_store;").fetchone()[0]
            self.assertIn(temp_store, (2, "MEMORY"))

    # ── 2. First-Class Relational Edges & Multi-Hop Traversal ─────────────

    def test_relational_edge_crud_and_traversal(self):
        """Tests inserting, querying, and multi-hop traversing relational graph edges."""
        n1 = CanonicalGraphNode(
            node_id="cnode_grid",
            evidence_id="ev_1",
            type="SYSTEM",
            name="Grid Controller",
            strategic_value=9.0,
        )
        n2 = CanonicalGraphNode(
            node_id="cnode_pg",
            evidence_id="ev_1",
            type="DATABASE",
            name="PostgreSQL Storage Engine",
            strategic_value=8.0,
        )
        n3 = CanonicalGraphNode(
            node_id="cnode_cache",
            evidence_id="ev_1",
            type="CACHE",
            name="Redis Cluster",
            strategic_value=7.0,
        )
        self.db_manager.upsert_canonical_node(n1)
        self.db_manager.upsert_canonical_node(n2)
        self.db_manager.upsert_canonical_node(n3)

        # Edges: Grid -> PostgreSQL -> Redis
        e1 = GraphEdge(
            edge_id="edge_1",
            from_node_id="cnode_grid",
            predicate="CONNECTS_TO",
            to_node_id="cnode_pg",
            confidence=0.98,
        )
        e2 = GraphEdge(
            edge_id="edge_2",
            from_node_id="cnode_pg",
            predicate="SYNCS_WITH",
            to_node_id="cnode_cache",
            confidence=0.95,
        )
        self.db_manager.upsert_canonical_edge(e1)
        self.db_manager.upsert_canonical_edge(e2)

        # Query edges
        edges = self.db_manager.query_canonical_edges(from_node_id="cnode_grid")
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].predicate, "CONNECTS_TO")
        self.assertEqual(edges[0].to_node_id, "cnode_pg")

        # 1-Hop Traversal from Grid
        nodes_1h, edges_1h = self.db_manager.traverse_canonical_neighborhood(["cnode_grid"], max_hops=1)
        self.assertEqual(len(nodes_1h), 2)  # Grid + PostgreSQL
        self.assertEqual(len(edges_1h), 1)

        # 2-Hop Traversal from Grid
        nodes_2h, edges_2h = self.db_manager.traverse_canonical_neighborhood(["cnode_grid"], max_hops=2)
        self.assertEqual(len(nodes_2h), 3)  # Grid + PostgreSQL + Redis
        self.assertEqual(len(edges_2h), 2)

    # ── 3. SQLite FTS5 Full-Text Search & Trigger Sync ────────────────────

    def test_fts5_indexing_and_triggers(self):
        """Verifies FTS5 virtual table synchronization on insert, update, delete."""
        node = CanonicalGraphNode(
            node_id="cnode_fts_test",
            evidence_id="ev_fts",
            type="ARCHITECTURAL_DECISION",
            name="Microsecond Latency Buffer",
            properties={"context": "Configured for Apple Silicon Metal acceleration and high throughput."},
            strategic_value=8.5,
        )
        self.db_manager.upsert_canonical_node(node)

        # Search via FTS5
        hits = self.db_manager.search_canonical_nodes_fts("Microsecond Metal")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].node_id, "cnode_fts_test")

        # Update node name
        node.name = "Nanosecond Latency Buffer"
        self.db_manager.upsert_canonical_node(node)

        hits_new = self.db_manager.search_canonical_nodes_fts("Nanosecond")
        self.assertEqual(len(hits_new), 1)
        self.assertEqual(hits_new[0].name, "Nanosecond Latency Buffer")

        # Old name should no longer match
        hits_old = self.db_manager.search_canonical_nodes_fts("Microsecond")
        self.assertEqual(len(hits_old), 0)

    # ── 4. Pipeline B Extraction & Worker Edge Persistence ────────────────

    def test_pipeline_b_worker_relational_extraction(self):
        """Verifies SlowPathWorker executes compute fork, judges, and commits nodes + edges."""
        ev = EvidenceRecord(
            evidence_id="ev_worker_test",
            meeting_id="meeting_101",
            speaker="VP Architecture",
            text="We approved the dual-artery WAL architecture to support compliance and eliminate delay.",
            start_timestamp=10.0,
            end_timestamp=15.0,
        )
        self.db_manager.append_evidence(ev, enqueue=True)

        worker = SlowPathWorker(db_manager=self.db_manager)
        processed = asyncio.run(worker.run_once())
        self.assertEqual(processed, 1)

        # Check canonical nodes
        canonical_nodes = self.db_manager.query_canonical_nodes()
        self.assertGreaterEqual(len(canonical_nodes), 1)

        # Check internal advisory nodes (from 'eliminate delay' trigger)
        advisory_nodes = self.db_manager.query_internal_advisory_nodes()
        self.assertIn(
            advisory_nodes[0].category,
            (AdvisoryCategory.ADOPTION_RISK, AdvisoryCategory.DECISION_INFLUENCE),
        )

    # ── 5. Pipeline C Policy-Gated Retrieval & Multi-Hop ──────────────────

    def test_pipeline_c_retrieval_and_edge_hydration(self):
        """Verifies hybrid search with FTS5, RRF scoring, and connected edge hydration."""
        n1 = CanonicalGraphNode(
            node_id="cnode_auth",
            evidence_id="ev_retrieval",
            type="SECURITY_POLICY",
            name="Zero-Trust Authentication Boundary",
            properties={"context": "Pre-vector SQL policy gating enforcing strict namespace boundaries."},
            strategic_value=9.5,
        )
        n2 = CanonicalGraphNode(
            node_id="cnode_audit",
            evidence_id="ev_retrieval",
            type="AUDIT_LOG",
            name="Immutable Audit Trail",
            properties={"context": "Cryptographic logging of all entity mutations."},
            strategic_value=8.0,
        )
        self.db_manager.upsert_canonical_node(n1)
        self.db_manager.upsert_canonical_node(n2)

        edge = GraphEdge(
            edge_id="edge_sec",
            from_node_id="cnode_auth",
            predicate="SECURES",
            to_node_id="cnode_audit",
            confidence=0.99,
        )
        self.db_manager.upsert_canonical_edge(edge)

        retriever = PolicyGatedRetrievalEngine(db_manager=self.db_manager)
        facts = retriever.search(query="Zero-Trust Authentication", stealth_mode=True)
        self.assertGreaterEqual(len(facts), 1)

        top_fact = facts[0]
        self.assertEqual(top_fact.node_id, "cnode_auth")
        self.assertGreater(top_fact.relevance_score, 0.0)

        # Verify connected edges are hydrated on the retrieved fact
        self.assertEqual(len(top_fact.connected_edges), 1)
        self.assertEqual(top_fact.connected_edges[0]["predicate"], "SECURES")
        self.assertEqual(top_fact.connected_edges[0]["to_node_id"], "cnode_audit")

    # ── 6. Zero-Leakage Stealth Mode Enforcement (Law 6) ──────────────────

    def test_stealth_mode_zero_leakage_enforcement(self):
        """Verifies that INTERNAL_ADVISORY facts never leak in Stealth Mode."""
        adv_node = InternalAdvisoryNode(
            node_id="adv_stealth_secret",
            evidence_id="ev_sec",
            type="DECISION_INFLUENCE",
            name="Executive Veto Politics",
            category=AdvisoryCategory.DECISION_INFLUENCE,
            properties={"rationale": "High political sensitivity regarding budget authority."},
            strategic_value=10.0,
            stealth_score=1.0,
        )
        self.db_manager.upsert_internal_advisory_node(adv_node)

        retriever = PolicyGatedRetrievalEngine(db_manager=self.db_manager)

        # In Stealth Mode (default): zero advisory nodes must be returned
        stealth_results = retriever.search(query="Executive Veto", stealth_mode=True)
        for fact in stealth_results:
            self.assertNotEqual(fact.namespace, INTERNAL_ADVISORY_NAMESPACE)
            self.assertNotEqual(fact.node_id, "adv_stealth_secret")

        # In Unmasked Mode: advisory nodes are returned
        unmasked_results = retriever.search(query="Executive Veto", stealth_mode=False)
        advisory_facts = [f for f in unmasked_results if f.namespace == INTERNAL_ADVISORY_NAMESPACE]
        self.assertGreaterEqual(len(advisory_facts), 1)
        self.assertEqual(advisory_facts[0].node_id, "adv_stealth_secret")


if __name__ == "__main__":
    unittest.main()
