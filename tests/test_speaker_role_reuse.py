#!/usr/bin/env python3
"""
tests/test_speaker_role_reuse.py

Unit test suite for Wave 4: Reusing the current verified role in the next run.
Verifies authoritative assertion precedence over node descriptions, temporal filtering,
conflict detection, cache invalidation, and gate integration.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
    resolve_person_entity,
)
from dual_pipeline.transcript_stages_core.c05_title_gate import C05TitleCompletenessGate
from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import (
    C06GraphDBRoleVerificationGate,
)


def _init_test_graph(db_path: Path, table_type: str = "node") -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    if table_type == "node":
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
            """
        )
    else:
        conn.executescript(
            """
            CREATE TABLE canonical_graph_nodes (
                node_id TEXT PRIMARY KEY,
                name TEXT,
                type TEXT,
                description TEXT,
                properties TEXT,
                is_quarantined INTEGER DEFAULT 0
            );
            """
        )

    conn.executescript(
        """
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


def _setup_runtime_root(base_dir: Path, gen_id: str = "gen-001", table_type: str = "node") -> Path:
    rt_root = base_dir / "runtime"
    rt_root.mkdir(parents=True, exist_ok=True)
    db_path = rt_root / "CANONICAL_GRAPH.db"
    _init_test_graph(db_path, table_type=table_type)
    ap = {
        "generation_id": gen_id,
        "graph_path": "CANONICAL_GRAPH.db",
        "index_path": "CANONICAL_GRAPH.idx",
    }
    (rt_root / "active_pair.json").write_text(json.dumps(ap), encoding="utf-8")
    return rt_root


class TestSpeakerRoleReuseUnit(unittest.TestCase):
    """Unit tests for next-run speaker role reuse and temporal precedence."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.rt_root = _setup_runtime_root(self.work_dir)
        self.db_path = self.rt_root / "CANONICAL_GRAPH.db"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_authoritative_assertion_overrides_older_node_description(self) -> None:
        """Active assertion takes strict precedence over older non-placeholder node description."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, description, node_type, active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            ("p_sarah", "Sarah Jenkins", "Sarah Jenkins", "Role: Senior Analyst\nAffiliation label(s): NEA", "person"),
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence, status) "
            "VALUES (?, ?, 'HAS_ROLE', ?, 0.95, 'CONFIRMED')",
            ("ass_sarah_01", "p_sarah", "VP, Renewable Power"),
        )
        conn.commit()
        conn.close()

        resolver = PreSynthesisEntityResolver(runtime_root=self.rt_root, enable_graph_query=True)
        ent = resolver.resolve("Sarah Jenkins")
        self.assertIsNotNone(ent)
        self.assertEqual(ent.role, "VP, Renewable Power")
        self.assertEqual(ent.source, "knowledge_graph_assertion")
        self.assertEqual(ent.confidence, 0.95)
        self.assertFalse(ent.requires_operator_confirmation)

    def test_canonical_graph_nodes_table_support(self) -> None:
        """Resolver seamlessly loads from canonical_graph_nodes table when node is absent."""
        alt_root = _setup_runtime_root(self.work_dir / "alt", gen_id="gen-alt", table_type="canonical_graph_nodes")
        alt_db = alt_root / "CANONICAL_GRAPH.db"
        conn = sqlite3.connect(str(alt_db))
        conn.execute(
            "INSERT INTO canonical_graph_nodes (node_id, name, type, description, properties, is_quarantined) "
            "VALUES (?, ?, 'person', 'Old Description', ?, 0)",
            ("p_elena", "Elena Rostova", json.dumps({"org": "PartnerAdvisory", "role": "Consultant"})),
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence, status) "
            "VALUES (?, ?, 'HAS_ROLE', ?, 1.0, 'CONFIRMED')",
            ("ass_elena_01", "p_elena", "Managing Director"),
        )
        conn.commit()
        conn.close()

        resolver = PreSynthesisEntityResolver(runtime_root=alt_root, enable_graph_query=True)
        ent = resolver.resolve("Elena Rostova")
        self.assertIsNotNone(ent)
        self.assertEqual(ent.role, "Managing Director")
        self.assertEqual(ent.org, "PartnerAdvisory")
        self.assertEqual(ent.source, "knowledge_graph_assertion")

    def test_future_effective_assertion_is_skipped(self) -> None:
        """An assertion effective in the future relative to as_of_timestamp is not current truth."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, description, node_type, active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            ("p_david", "David Vance", "David Vance", "Role: Senior Manager", "person"),
        )
        # Promotion observed in October 2026
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, observed_at, status) "
            "VALUES (?, ?, 'HAS_ROLE', ?, '2026-10-01T00:00:00Z', 'CONFIRMED')",
            ("ass_vance_01", "p_david", "Vice President"),
        )
        conn.commit()
        conn.close()

        # Querying for a historical meeting from May 2026
        resolver = PreSynthesisEntityResolver(
            runtime_root=self.rt_root,
            as_of_timestamp="2026-05-01T12:00:00Z",
            enable_graph_query=True,
        )
        ent = resolver.resolve("David Vance")
        self.assertIsNotNone(ent)
        # Future promotion is skipped; falls back to node description role
        self.assertEqual(ent.role, "Senior Manager")
        self.assertEqual(ent.source, "knowledge_graph")

    def test_expired_assertion_is_skipped(self) -> None:
        """An assertion with stale_after in the past relative to as_of_timestamp is skipped."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, description, node_type, active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            ("p_marcus", "Marcus Cole", "Marcus Cole", "Role: Strategy Director", "person"),
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, stale_after, status) "
            "VALUES (?, ?, 'HAS_ROLE', ?, '2025-12-31T23:59:59Z', 'CONFIRMED')",
            ("ass_cole_expired", "p_marcus", "Interim Chief Strategy Officer"),
        )
        conn.commit()
        conn.close()

        # In 2026, the interim role is stale
        resolver = PreSynthesisEntityResolver(
            runtime_root=self.rt_root,
            as_of_timestamp="2026-06-01T00:00:00Z",
            enable_graph_query=True,
        )
        ent = resolver.resolve("Marcus Cole")
        self.assertIsNotNone(ent)
        self.assertEqual(ent.role, "Strategy Director")

    def test_historical_meeting_resolves_appropriate_timeline_role(self) -> None:
        """Historical meetings resolve historical role, while subsequent dates resolve new role."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, description, node_type, active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            ("p_rachel", "Rachel Adams", "Rachel Adams", "Role: Senior Analyst", "person"),
        )
        # Period 1: Jan 2024 to May 2025
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, observed_at, stale_after, status) "
            "VALUES (?, ?, 'HAS_ROLE', ?, '2024-01-01', '2025-05-31', 'CONFIRMED')",
            ("ass_rachel_p1", "p_rachel", "Lead Architect"),
        )
        # Period 2: June 2025 to Dec 2027
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, observed_at, stale_after, status) "
            "VALUES (?, ?, 'HAS_ROLE', ?, '2025-06-01', '2027-12-31', 'CONFIRMED')",
            ("ass_rachel_p2", "p_rachel", "Principal Architect"),
        )
        conn.commit()
        conn.close()

        res_2024 = PreSynthesisEntityResolver(
            runtime_root=self.rt_root,
            as_of_timestamp="2024-08-15",
            enable_graph_query=True,
        )
        ent_2024 = res_2024.resolve("Rachel Adams")
        self.assertIsNotNone(ent_2024)
        self.assertEqual(ent_2024.role, "Lead Architect")

        res_2026 = PreSynthesisEntityResolver(
            runtime_root=self.rt_root,
            as_of_timestamp="2026-03-10",
            enable_graph_query=True,
        )
        ent_2026 = res_2026.resolve("Rachel Adams")
        self.assertIsNotNone(ent_2026)
        self.assertEqual(ent_2026.role, "Principal Architect")

    def test_retired_quarantined_superseded_assertions_ignored(self) -> None:
        """Quarantined, superseded, and retired assertions never become current truth."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, description, node_type, active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            ("p_tom", "Thomas Gray", "Thomas Gray", "Role: Senior Analyst", "person"),
        )
        # 4 invalid assertions
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, status) "
            "VALUES ('ass_tom_01', 'p_tom', 'HAS_ROLE', 'Retired Title', 'RETIRED')"
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, is_quarantined) "
            "VALUES ('ass_tom_02', 'p_tom', 'HAS_ROLE', 'Quarantined Title', 1)"
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, currentness_state) "
            "VALUES ('ass_tom_03', 'p_tom', 'HAS_ROLE', 'Expired State Title', 'EXPIRED')"
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, superseded_by_assertion_id) "
            "VALUES ('ass_tom_04', 'p_tom', 'HAS_ROLE', 'Superseded Title', 'ass_tom_05')"
        )
        conn.commit()
        conn.close()

        resolver = PreSynthesisEntityResolver(runtime_root=self.rt_root, enable_graph_query=True)
        ent = resolver.resolve("Thomas Gray")
        self.assertIsNotNone(ent)
        self.assertEqual(ent.role, "Senior Analyst")
        self.assertEqual(ent.source, "knowledge_graph")

    def test_conflicting_concurrent_roles_trigger_ambiguous_conflict(self) -> None:
        """Two concurrent active assertions with close confidence produce AMBIGUOUS_CONFLICT."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, description, node_type, active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            ("p_alex", "Alex Mercer", "Alex Mercer", "Role: Participant", "person"),
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence, status) "
            "VALUES (?, ?, 'HAS_ROLE', ?, 0.90, 'CONFIRMED')",
            ("ass_alex_01", "p_alex", "VP of Engineering"),
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence, status) "
            "VALUES (?, ?, 'HAS_ROLE', ?, 0.92, 'CONFIRMED')",
            ("ass_alex_02", "p_alex", "Chief Systems Architect"),
        )
        conn.commit()
        conn.close()

        resolver = PreSynthesisEntityResolver(runtime_root=self.rt_root, enable_graph_query=True)
        ent = resolver.resolve("Alex Mercer")
        self.assertIsNotNone(ent)
        self.assertEqual(ent.status, "AMBIGUOUS_CONFLICT")
        self.assertTrue(ent.requires_operator_confirmation)
        self.assertIn("VP of Engineering", ent.role)
        self.assertIn("Chief Systems Architect", ent.role)

    def test_decisive_confidence_margin_resolves_without_conflict(self) -> None:
        """When confidence margin > 0.30, the decisively higher-confidence assertion wins."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, description, node_type, active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            ("p_clara", "Clara Oswald", "Clara Oswald", "Role: Participant", "person"),
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence, status) "
            "VALUES (?, ?, 'HAS_ROLE', ?, 0.50, 'CONFIRMED')",
            ("ass_clara_low", "p_clara", "Junior Consultant"),
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, confidence, status) "
            "VALUES (?, ?, 'HAS_ROLE', ?, 0.95, 'CONFIRMED')",
            ("ass_clara_high", "p_clara", "Engagement Lead"),
        )
        conn.commit()
        conn.close()

        resolver = PreSynthesisEntityResolver(runtime_root=self.rt_root, enable_graph_query=True)
        ent = resolver.resolve("Clara Oswald")
        self.assertIsNotNone(ent)
        self.assertEqual(ent.role, "Engagement Lead")
        self.assertEqual(ent.status, "RESOLVED")
        self.assertFalse(ent.requires_operator_confirmation)

    def test_cache_invalidation_on_generation_change(self) -> None:
        """Resolver instance invalidates cached entities when generation_id changes."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, description, node_type, active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            ("p_bob", "Bob Taylor", "Bob Taylor", "Role: Senior Analyst", "person"),
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, status) "
            "VALUES (?, ?, 'HAS_ROLE', ?, 'CONFIRMED')",
            ("ass_bob_01", "p_bob", "Engineering Manager"),
        )
        conn.commit()
        conn.close()

        resolver = PreSynthesisEntityResolver(
            runtime_root=self.rt_root,
            generation_id="gen-001",
            enable_graph_query=True,
        )
        ent1 = resolver.resolve("Bob Taylor")
        self.assertEqual(ent1.role, "Engineering Manager")

        # Mutate database with new role and change generation
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("UPDATE assertion SET object_literal = ? WHERE assertion_id = ?", ("Director of Engineering", "ass_bob_01"))
        conn.commit()
        conn.close()

        # Update resolver's generation_id to trigger cache invalidation
        resolver.generation_id = "gen-002"
        ent2 = resolver.resolve("Bob Taylor")
        self.assertEqual(ent2.role, "Director of Engineering")

    def test_runtime_root_isolation_prevents_cross_leak(self) -> None:
        """Entities and roles in Runtime Root A cannot leak to Runtime Root B."""
        root_a = _setup_runtime_root(self.work_dir / "tenant_a", gen_id="gen-a")
        root_b = _setup_runtime_root(self.work_dir / "tenant_b", gen_id="gen-b")

        conn_a = sqlite3.connect(str(root_a / "CANONICAL_GRAPH.db"))
        conn_a.execute("INSERT INTO node VALUES ('p_shared', 'Shared Person', 'Shared Person', 'Desc', '{}', 'person', 1)")
        conn_a.execute("INSERT INTO assertion VALUES ('ass_a', 'p_shared', 'HAS_ROLE', 'Tenant A Lead', 'claim', 1.0, 'CONFIRMED', NULL, NULL, NULL, 0, NULL)")
        conn_a.commit()
        conn_a.close()

        conn_b = sqlite3.connect(str(root_b / "CANONICAL_GRAPH.db"))
        conn_b.execute("INSERT INTO node VALUES ('p_shared', 'Shared Person', 'Shared Person', 'Desc', '{}', 'person', 1)")
        conn_b.execute("INSERT INTO assertion VALUES ('ass_b', 'p_shared', 'HAS_ROLE', 'Tenant B Architect', 'claim', 1.0, 'CONFIRMED', NULL, NULL, NULL, 0, NULL)")
        conn_b.commit()
        conn_b.close()

        ent_a = resolve_person_entity("Shared Person", runtime_root=root_a)
        ent_b = resolve_person_entity("Shared Person", runtime_root=root_b)

        self.assertIsNotNone(ent_a)
        self.assertIsNotNone(ent_b)
        self.assertEqual(ent_a.role, "Tenant A Lead")
        self.assertEqual(ent_b.role, "Tenant B Architect")

    def test_c05_and_c06_gates_reuse_active_role(self) -> None:
        """C0.5 and C0.6 gates automatically resolve placeholder roles using active assertions."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, description, node_type, active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            ("p_zoe", "Zoe Barnes", "Zoe Barnes", "Role: Participant", "person"),
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, status) "
            "VALUES (?, ?, 'HAS_ROLE', ?, 'CONFIRMED')",
            ("ass_zoe_01", "p_zoe", "Principal Consultant"),
        )
        conn.commit()
        conn.close()

        l0_context = {
            "runtime_root": self.rt_root,
            "metadata": {
                "roster": [{"name": "Zoe Barnes", "role": "Participant"}],
                "pre_enrichment_roles": {"Zoe Barnes": "Participant"},
            },
        }

        c05 = C05TitleCompletenessGate(fail_closed=True)
        res_c05 = c05.execute(l0_context)
        self.assertIn("Zoe Barnes", res_c05["metadata"]["auto_role_resolutions"])
        self.assertEqual(res_c05["metadata"]["auto_role_resolutions"]["Zoe Barnes"], "Principal Consultant")

        c06 = C06GraphDBRoleVerificationGate(fail_closed=True)
        res_c06 = c06.execute(res_c05)
        self.assertTrue(res_c06.get("c06_role_gate_cleared"))


if __name__ == "__main__":
    unittest.main()
