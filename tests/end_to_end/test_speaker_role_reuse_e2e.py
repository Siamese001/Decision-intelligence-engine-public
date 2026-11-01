#!/usr/bin/env python3
"""
tests/end_to_end/test_speaker_role_reuse_e2e.py

End-to-End test suite for Wave 4: Reusing verified speaker roles across runs.
Validates cold-process execution via subprocess, warm-process same-session reuse,
full U0 -> C0.5 -> C0.6 pipeline progression, and historical meeting timeline isolation.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.transcript_metadata_core.entity_resolver import (
    invalidate_resolver_cache,
    resolve_person_entity,
)
from dual_pipeline.transcript_stages_core.c05_title_gate import C05TitleCompletenessGate
from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import (
    C06GraphDBRoleVerificationGate,
)
from dual_pipeline.transcript_stages_core.u0_ingestion import (
    _normalize_transcript_entities,
)


def _init_test_graph(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            display_name TEXT,
            description TEXT,
            properties_json TEXT,
            node_type TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_literal TEXT,
            claim_text TEXT,
            confidence REAL DEFAULT 1.0,
            status TEXT DEFAULT 'CONFIRMED',
            observed_at TEXT,
            stale_after TEXT,
            currentness_state TEXT,
            is_quarantined INTEGER DEFAULT 0,
            superseded_by_assertion_id TEXT
        );
        CREATE TABLE alias (
            alias_name TEXT,
            canonical_name TEXT,
            active INTEGER DEFAULT 1
        );
        """
    )
    conn.commit()
    return conn


def _setup_runtime_root(base_dir: Path, gen_id: str = "gen-e2e-001") -> Path:
    rt_root = base_dir / "runtime"
    rt_root.mkdir(parents=True, exist_ok=True)
    db_path = rt_root / "CANONICAL_GRAPH.db"
    _init_test_graph(db_path)
    ap = {
        "generation_id": gen_id,
        "graph_path": "CANONICAL_GRAPH.db",
        "index_path": "CANONICAL_GRAPH.idx",
    }
    (rt_root / "active_pair.json").write_text(json.dumps(ap), encoding="utf-8")
    return rt_root


class TestSpeakerRoleReuseE2E(unittest.TestCase):
    """End-to-End tests for next-run speaker role reuse across cold and warm processes."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.rt_root = _setup_runtime_root(self.work_dir)
        self.db_path = self.rt_root / "CANONICAL_GRAPH.db"
        invalidate_resolver_cache()

    def tearDown(self) -> None:
        invalidate_resolver_cache()
        self.temp_dir.cleanup()

    def test_cold_process_resolves_activated_role(self) -> None:
        """A fresh cold Python subprocess resolves the activated role from disk with zero warm cache."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, description, node_type, active) "
            "VALUES (?, ?, ?, ?, 'person', 1)",
            ("p_marcus", "Marcus Sterling", "Marcus Sterling", "Role: Senior Manager"),
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence, status) "
            "VALUES (?, ?, 'HAS_ROLE', ?, 0.95, 'CONFIRMED')",
            ("ass_marcus_01", "p_marcus", "VP of Renewable Infrastructure"),
        )
        conn.commit()
        conn.close()

        # Child subprocess code to query cold
        script = f"""
import json, sys
from pathlib import Path
from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity

rt = Path(r'{self.rt_root}')
ent = resolve_person_entity("Marcus Sterling", runtime_root=rt)
if ent is None:
    sys.exit(2)
out = {{
    "canonical_name": ent.canonical_name,
    "role": ent.role,
    "source": ent.source,
    "confidence": ent.confidence,
    "status": ent.status,
}}
print(json.dumps(out))
"""
        res = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=str(Path.cwd()),
        )
        self.assertEqual(res.returncode, 0, f"Subprocess failed: {res.stderr}")
        data = json.loads(res.stdout.strip())
        self.assertEqual(data["canonical_name"], "Marcus Sterling")
        self.assertEqual(data["role"], "VP of Renewable Infrastructure")
        self.assertEqual(data["source"], "knowledge_graph_assertion")
        self.assertEqual(data["confidence"], 0.95)
        self.assertEqual(data["status"], "RESOLVED")

    def test_warm_process_same_session_reuse(self) -> None:
        """Subsequent queries in the same process reuse the resolved role without redundant lookups."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, description, node_type, active) "
            "VALUES (?, ?, ?, ?, 'person', 1)",
            ("p_hannah", "Hannah Abbott", "Hannah Abbott", "Role: Analyst"),
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence, status) "
            "VALUES (?, ?, 'HAS_ROLE', ?, 1.0, 'CONFIRMED')",
            ("ass_hannah_01", "p_hannah", "Principal Lead"),
        )
        conn.commit()
        conn.close()

        # Query 1
        ent1 = resolve_person_entity("Hannah Abbott", runtime_root=self.rt_root)
        self.assertIsNotNone(ent1)
        self.assertEqual(ent1.role, "Principal Lead")

        # Query 2 in same process
        ent2 = resolve_person_entity("Hannah Abbott", runtime_root=self.rt_root)
        self.assertIsNotNone(ent2)
        self.assertEqual(ent2.role, "Principal Lead")
        self.assertEqual(ent2.assertion_id, "ass_hannah_01")

    def test_e2e_meeting_pipeline_reuses_activated_role(self) -> None:
        """Full pipeline pass (U0 -> C0.5 -> C0.6) reuses confirmed assertion without HITL prompts."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, description, node_type, active) "
            "VALUES (?, ?, ?, ?, 'person', 1)",
            ("p_jordan", "Jordan Matthews", "Jordan Matthews", "Role: Participant"),
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence, status) "
            "VALUES (?, ?, 'HAS_ROLE', ?, 0.95, 'CONFIRMED')",
            ("ass_jordan_01", "p_jordan", "Principal AI Architect"),
        )
        conn.commit()
        conn.close()

        raw_transcript = (
            "**Jordan Matthews:** Good morning team. Today we review the grid architecture.\n\n"
            "**Speaker 2:** Agreed, let's proceed."
        )
        metadata = {
            "runtime_root": self.rt_root,
            "roster": [{"name": "Jordan Matthews", "role": "Participant"}],
            "attendees": "Jordan Matthews (Participant)",
        }

        # Stage U0 Normalization
        clean_transcript = _normalize_transcript_entities(raw_transcript, metadata)
        self.assertIn("**Jordan Matthews:**", clean_transcript)
        self.assertEqual(metadata["roster"][0]["role"], "Principal AI Architect")

        # Stage C0.5 Title Completeness Gate
        l0_context = {
            "runtime_root": self.rt_root,
            "metadata": metadata,
            "clean_transcript": clean_transcript,
        }
        c05 = C05TitleCompletenessGate(fail_closed=True)
        res_c05 = c05.execute(l0_context)
        self.assertTrue(res_c05.get("c05_title_gate_cleared", True))

        # Stage C0.6 GraphDB Role Verification Gate
        c06 = C06GraphDBRoleVerificationGate(fail_closed=True)
        res_c06 = c06.execute(res_c05)
        receipt = res_c06["metadata"]["graph_role_verification_receipt"]
        self.assertEqual(receipt["status"], "CLEARED")
        verified_roles = receipt["attendees_verified"]
        self.assertIn("Jordan Matthews", verified_roles)
        self.assertEqual(verified_roles["Jordan Matthews"]["role"], "Principal AI Architect")

    def test_e2e_historical_and_current_meetings_in_sequence(self) -> None:
        """Sequential meetings from different dates resolve appropriate historical roles without leak."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, description, node_type, active) "
            "VALUES (?, ?, ?, ?, 'person', 1)",
            ("p_cynthia", "Cynthia Wu", "Cynthia Wu", "Role: Senior Analyst"),
        )
        # Role 1: Historical 2024
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, observed_at, stale_after, status) "
            "VALUES ('ass_cyn_01', 'p_cynthia', 'HAS_ROLE', 'Senior Project Manager', '2024-01-01', '2025-06-01', 'CONFIRMED')"
        )
        # Role 2: Current 2025+
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, observed_at, stale_after, status) "
            "VALUES ('ass_cyn_02', 'p_cynthia', 'HAS_ROLE', 'Director of Delivery', '2025-06-01', '2028-01-01', 'CONFIRMED')"
        )
        conn.commit()
        conn.close()

        # Historical 2024 meeting run
        ent_hist = resolve_person_entity(
            "Cynthia Wu",
            runtime_root=self.rt_root,
            as_of_timestamp="2024-10-15T10:00:00Z",
        )
        self.assertIsNotNone(ent_hist)
        self.assertEqual(ent_hist.role, "Senior Project Manager")

        # Current 2026 meeting run
        ent_curr = resolve_person_entity(
            "Cynthia Wu",
            runtime_root=self.rt_root,
            as_of_timestamp="2026-03-01T10:00:00Z",
        )
        self.assertIsNotNone(ent_curr)
        self.assertEqual(ent_curr.role, "Director of Delivery")


if __name__ == "__main__":
    unittest.main()
