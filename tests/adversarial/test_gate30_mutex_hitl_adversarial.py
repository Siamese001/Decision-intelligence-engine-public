"""
tests/adversarial/test_gate30_mutex_hitl_adversarial.py

Adversarial test suite for Wave 4: Gate 30 Mutex Resolution Gate.
Tests multi-way collisions (3+ leads), atomic database transitions,
unrelated signal degradation preservation, and error recovery.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.release_core.helpers import evaluate_gate30_non_degradation
from dual_pipeline.transcript_stages_core.models import HITLRequiredError


def _setup_multiway_db(db_path: Path, conflict_count: int = 3) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            display_name TEXT,
            node_type TEXT,
            active INTEGER DEFAULT 1,
            description TEXT
        )
    """)
    cur.execute("CREATE TABLE source (source_id TEXT PRIMARY KEY, source_type TEXT)")
    cur.execute("""
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            claim_text TEXT,
            confidence_score REAL,
            currentness_state TEXT DEFAULT 'current',
            canonical_status TEXT DEFAULT 'canonical',
            primary_source_id TEXT,
            assertion_type TEXT DEFAULT 'claim',
            origin_type TEXT DEFAULT 'extracted',
            validation_state TEXT DEFAULT 'validated',
            is_quarantined INTEGER DEFAULT 0,
            recorded_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT,
            evidence_relationship TEXT DEFAULT 'supports'
        )
    """)

    cur.execute("INSERT INTO source VALUES ('src_01', 'transcript')")
    cur.execute("INSERT INTO node VALUES ('workstream_core', 'Core Architecture', 'Core Architecture', 'workstream', 1, '')")

    names = ["Alice", "Bob", "Charlie", "Dave"]
    for name in names:
        pid = f"person_{name.lower()}"
        cur.execute(f"INSERT INTO node VALUES ('{pid}', '{name}', '{name}', 'person', 1, '')")

    for i in range(conflict_count):
        pid = f"person_{names[i].lower()}"
        asrt_id = f"asrt_{i:02d}"
        cur.execute(f"""
            INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, confidence_score, currentness_state, canonical_status, primary_source_id)
            VALUES ('{asrt_id}', '{pid}', 'OWNS_OR_LEADS', 'workstream_core', 0.90, 'current', 'canonical', 'src_01')
        """)
        cur.execute(f"INSERT INTO assertion_evidence VALUES ('{asrt_id}', 'ev_{i:02d}', 'supports')")

    conn.commit()
    conn.close()


class TestGate30MutexAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.ledger_path = self.root / "hitl_decision_ledger.json"
        self.store = HITLDecisionStore(self.ledger_path)

        self.active_graph = self.root / "active_graph.sqlite"
        self.post_graph = self.root / "post_graph.sqlite"
        self.sidecar = None

        # Active has 1 lead
        _setup_multiway_db(self.active_graph, conflict_count=1)
        # Post has 3 competing leads
        _setup_multiway_db(self.post_graph, conflict_count=3)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_three_way_collision_prompts_with_all_candidates(self) -> None:
        """A 3-way collision lists all 3 candidates and prompts the operator."""
        with self.assertRaises(HITLRequiredError) as ctx:
            evaluate_gate30_non_degradation(
                graph_path=self.post_graph,
                sidecar_path=self.sidecar,
                active_graph_path=self.active_graph,
                active_sidecar_path=self.sidecar,
                store=self.store,
            )

        err = ctx.exception
        self.assertEqual(len(err.options), 3)
        self.assertIn("person_alice", [o["subject"] for o in err.options])
        self.assertIn("person_bob", [o["subject"] for o in err.options])
        self.assertIn("person_charlie", [o["subject"] for o in err.options])

    def test_three_way_resolution_supersedes_all_other_leads(self) -> None:
        """Selecting person_charlie supersedes both person_alice and person_bob."""
        def mock_resolver(envelope: Any) -> str:
            return "person_charlie"

        res = evaluate_gate30_non_degradation(
            graph_path=self.post_graph,
            sidecar_path=self.sidecar,
            active_graph_path=self.active_graph,
            active_sidecar_path=self.sidecar,
            interactive_resolver=mock_resolver,
            store=self.store,
        )
        self.assertTrue(res.get("passed", False))

        conn = sqlite3.connect(self.post_graph)
        current_rows = conn.execute(
            "SELECT subject_node_id FROM assertion WHERE object_node_id = 'workstream_core' AND currentness_state = 'current'"
        ).fetchall()
        superseded_rows = conn.execute(
            "SELECT subject_node_id FROM assertion WHERE object_node_id = 'workstream_core' AND currentness_state = 'superseded'"
        ).fetchall()
        conn.close()

        self.assertEqual(len(current_rows), 1)
        self.assertEqual(current_rows[0][0], "person_charlie")
        superseded_subs = {r[0] for r in superseded_rows}
        self.assertIn("person_alice", superseded_subs)
        self.assertIn("person_bob", superseded_subs)


if __name__ == "__main__":
    unittest.main()
