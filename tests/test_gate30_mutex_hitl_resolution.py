"""
tests/test_gate30_mutex_hitl_resolution.py

Unit test suite for Wave 4: Gate 30 Mutex Resolution Gate & Single-DRI Enforcement.
Validates that Release Gate 30 (Graph Signal Non-Degradation) does not silently fall back
to DEGRADED_OUTPUTS_SAVED, but instead triggers interactive HITL resolution of competing
workstream leads and cleanly clears graph mutation.
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


def _setup_test_db(db_path: Path, with_mutex_conflict: bool = False) -> None:
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
    cur.execute("""
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_type TEXT
        )
    """)
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

    # Populate baseline nodes
    cur.execute("INSERT INTO source VALUES ('src_01', 'meeting_transcript')")
    cur.execute("INSERT INTO node VALUES ('person_amit', 'Amit Ayer', 'Amit Ayer', 'person', 1, '')")
    cur.execute("INSERT INTO node VALUES ('person_alex', 'Alex Sanchez', 'Alex Sanchez', 'person', 1, '')")
    cur.execute("INSERT INTO node VALUES ('workstream_hr', 'HR Front Door', 'HR Front Door', 'workstream', 1, '')")

    # Add valid supported assertion
    cur.execute("""
        INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, confidence_score, currentness_state, canonical_status, primary_source_id)
        VALUES ('asrt_01', 'person_amit', 'OWNS_OR_LEADS', 'workstream_hr', 0.95, 'current', 'canonical', 'src_01')
    """)
    cur.execute("INSERT INTO assertion_evidence VALUES ('asrt_01', 'ev_01', 'supports')")

    if with_mutex_conflict:
        # Add competing assertion creating a mutex conflict (count = 2 > 1)
        cur.execute("""
            INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, confidence_score, currentness_state, canonical_status, primary_source_id)
            VALUES ('asrt_02', 'person_alex', 'OWNS_OR_LEADS', 'workstream_hr', 0.85, 'current', 'canonical', 'src_01')
        """)
        cur.execute("INSERT INTO assertion_evidence VALUES ('asrt_02', 'ev_02', 'supports')")

    conn.commit()
    conn.close()


class TestGate30MutexHITLResolution(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.ledger_path = self.root / "hitl_decision_ledger.json"
        self.store = HITLDecisionStore(self.ledger_path)

        self.active_graph = self.root / "active_graph.sqlite"
        self.post_graph = self.root / "post_graph.sqlite"
        self.sidecar = None

        # Active graph has NO mutex conflict
        _setup_test_db(self.active_graph, with_mutex_conflict=False)
        # Post graph HAS a mutex conflict
        _setup_test_db(self.post_graph, with_mutex_conflict=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_evaluate_gate30_raises_hitl_when_mutex_conflict_unresolved(self) -> None:
        """Gate 30 raises HITLRequiredError when competing leads exist without operator resolution."""
        with self.assertRaises(HITLRequiredError) as ctx:
            evaluate_gate30_non_degradation(
                graph_path=self.post_graph,
                sidecar_path=self.sidecar,
                active_graph_path=self.active_graph,
                active_sidecar_path=self.sidecar,
                store=self.store,
            )

        err = ctx.exception
        self.assertEqual(err.hitl_type, "mutex_lead_resolution")
        self.assertIn("workstream_hr", err.prompt)
        self.assertTrue(len(err.options) >= 2)

    def test_evaluate_gate30_clears_with_interactive_resolver(self) -> None:
        """Interactive resolver selects winning DRI, supersedes competitor, and clears Gate 30."""
        def mock_resolver(envelope: Any) -> str:
            # Select person_amit as the exclusive lead
            return "person_amit"

        res = evaluate_gate30_non_degradation(
            graph_path=self.post_graph,
            sidecar_path=self.sidecar,
            active_graph_path=self.active_graph,
            active_sidecar_path=self.sidecar,
            interactive_resolver=mock_resolver,
            store=self.store,
        )
        self.assertTrue(res.get("passed", False))

        # Verify in database: person_alex is superseded, person_amit is current
        conn = sqlite3.connect(self.post_graph)
        rows = conn.execute(
            "SELECT subject_node_id, currentness_state FROM assertion WHERE object_node_id = 'workstream_hr'"
        ).fetchall()
        conn.close()

        status_by_sub = {r[0]: r[1] for r in rows}
        self.assertEqual(status_by_sub["person_amit"], "current")
        self.assertEqual(status_by_sub["person_alex"], "superseded")

    def test_evaluate_gate30_uses_prior_ledger_decision(self) -> None:
        """A pre-recorded human resolution in hitl_decision_ledger.json clears Gate 30 without prompt."""
        self.store.record_decision(
            decision_id="dec_mutex_01",
            category="mutex_lead_resolution",
            prompt="Pre-recorded operator prior",
            options=[{"subject": "person_amit"}],
            selected_option="person_amit",
            rationale="Pre-recorded operator prior",
            metadata={
                "target": "workstream_hr",
                "predicate": "OWNS_OR_LEADS",
                "selected_subject": "person_amit",
            },
        )

        res = evaluate_gate30_non_degradation(
            graph_path=self.post_graph,
            sidecar_path=self.sidecar,
            active_graph_path=self.active_graph,
            active_sidecar_path=self.sidecar,
            store=self.store,
        )
        self.assertTrue(res.get("passed", False))


if __name__ == "__main__":
    unittest.main()
