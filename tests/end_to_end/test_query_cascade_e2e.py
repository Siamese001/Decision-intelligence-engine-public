#!/usr/bin/env python3
"""End-to-End integration test suite for the 5-tier Query Resolution Cascade.

Validates the complete query lifecycle:
1. Tier 0: Request intent analysis and pronoun disambiguation ('am I' -> person_amit).
2. Tier 1: Direct O(log N) SQLite relational traversal across node, assertion, evidence_item.
3. Tier 2: Sidecar hybrid fallback (BGE-M3 dense/sparse) on ungrounded entities.
4. Tier 3: Scoped forensic evidence envelope hydration via line/byte offset seeking.
5. Tier 4: Governed HITL ambiguity gating with structured interactive options.
6. Anti-Pattern Firewall: Prohibits unindexed directory searches fail-closed.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from typing import List

import pytest

from dual_pipeline.core import connect_sqlite
from dual_pipeline.query_core.firewall import (
    GraphDBRequiredError,
    ProhibitedCommandGuard,
)
from dual_pipeline.query_core.graph_first_engine import (
    GraphFirstQueryEngine,
)


@pytest.mark.e2e
class TestQueryCascadeE2E(unittest.TestCase):
    """End-to-end verification of the normative 5-tier query cascade."""

    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parents[2]
        cls.active_gen = (
            cls.repo_root
            / ".local"
            / "dual_pipeline"
            / "generations"
            / "generation-6b972fcf462da2799e2af1807c0f"
        )
        cls.graph_path = cls.active_gen / "graph.sqlite"
        cls.sidecar_path = cls.active_gen / "sidecar.sqlite"
        cls.cold_archive = (
            "output/project_nexus/09-22-2026/Google_Internal_Anya_Shreyak/"
            "01_Google_Internal_Anya_Shreyak_09-22-2026_1000AM_1030AM_ET_hitl_resolved_transcript.md"
        )

    def setUp(self):
        self.engine = GraphFirstQueryEngine(
            self.repo_root, self.graph_path, self.sidecar_path
        )
        self.firewall = ProhibitedCommandGuard()

    # -------------------------------------------------------------------------
    # E2E Test 1: Production Incident Natural Language Query Resolution
    # -------------------------------------------------------------------------
    def test_e2e_stakeholder_comparative_query_lifecycle(self):
        """End-to-end execution of the exact query that triggered the incident."""
        question = (
            "google meeting with Anya and Shreyak what was SHreyak responsible for "
            "vs. what am I respomsible for?"
        )
        result = self.engine.execute_query(
            question,
            source_id="source-5c0d92c8fc4cb8966f19523d",
            hydrate_forensic_quotes=True,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.resolved_tier, 1)
        self.assertFalse(result.hitl_required)
        self.assertLess(result.total_latency_ms, 50.0)

        # Verify matched nodes include both Shreyak and Amit (via pronoun resolution)
        matched_node_ids = {n["node_id"] for n in result.matched_nodes}
        self.assertIn("stakeholder_shreyak_gupta", matched_node_ids)
        self.assertIn("person_amit", matched_node_ids)

        # Verify retrieved assertions cover both participants
        asserted_subjects = {a["subject_node_id"] for a in result.assertions}
        self.assertIn("stakeholder_shreyak_gupta", asserted_subjects)
        self.assertIn("person_amit", asserted_subjects)

        # Verify Tier 3 forensic transcript hydration retrieved exact dialogue turns
        self.assertGreater(len(result.forensic_quotes), 0)
        found_target_speaker = any(
            q.target_turn and "Shreyak" in q.target_turn.speaker
            for q in result.forensic_quotes
        )
        self.assertTrue(found_target_speaker)

        # Verify rendered markdown contains structured tables and quotes
        self.assertIn("### Graph-Grounded Resolution", result.formatted_markdown)
        self.assertIn("Hydrated Forensic Quotes", result.formatted_markdown)
        self.assertIn("Shreyak", result.formatted_markdown)

    # -------------------------------------------------------------------------
    # E2E Test 2: Isolated Ingestion-to-Query Lifecycle
    # -------------------------------------------------------------------------
    def test_e2e_isolated_graph_intake_to_query_cascade(self):
        """Verifies query cascade against a newly provisioned, isolated generation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            graph_db = tmp_root / "graph.sqlite"
            sidecar_db = tmp_root / "sidecar.sqlite"

            # Create mock transcript file in output hierarchy
            rel_archive = "output/project_alpha/10-01-2026/Meeting_A/01_transcript.md"
            full_archive = tmp_root / rel_archive
            full_archive.parent.mkdir(parents=True, exist_ok=True)
            full_archive.write_text(
                "# Transcript\n\n"
                "### [00:02:15]\n"
                "**Dr. Sarah Chen:** We decided to adopt Kafka streaming.\n\n"
                "### [00:03:00]\n"
                "**Alex Rivera:** I will lead the deployment pipeline.\n",
                encoding="utf-8",
            )

            # Initialize isolated graph schema
            conn = connect_sqlite(graph_db)
            try:
                conn.executescript(
                    """
                    CREATE TABLE node (
                        node_id TEXT PRIMARY KEY,
                        node_type TEXT NOT NULL,
                        canonical_name TEXT NOT NULL,
                        display_name TEXT NOT NULL
                    );
                    CREATE TABLE source (
                        source_id TEXT PRIMARY KEY,
                        cold_archive_ref TEXT NOT NULL
                    );
                    CREATE TABLE assertion (
                        assertion_id TEXT PRIMARY KEY,
                        subject_node_id TEXT NOT NULL,
                        predicate TEXT NOT NULL,
                        object_node_id TEXT,
                        object_literal TEXT,
                        primary_source_id TEXT NOT NULL,
                        currentness_state TEXT NOT NULL,
                        retrieval_eligible INTEGER NOT NULL,
                        confidence_label TEXT NOT NULL
                    );
                    CREATE TABLE evidence_item (
                        evidence_id TEXT PRIMARY KEY,
                        source_id TEXT NOT NULL,
                        locator_kind TEXT NOT NULL,
                        locator_value TEXT NOT NULL,
                        transcript_timestamp TEXT,
                        direct_quote TEXT
                    );
                    CREATE TABLE assertion_evidence (
                        assertion_id TEXT NOT NULL,
                        evidence_id TEXT NOT NULL
                    );

                    INSERT INTO node VALUES
                        ('person_sarah', 'person', 'Dr. Sarah Chen', 'Sarah Chen'),
                        ('person_alex', 'person', 'Alex Rivera', 'Alex Rivera');

                    INSERT INTO source VALUES
                        ('src-alpha-001', 'output/project_alpha/10-01-2026/Meeting_A/01_transcript.md');

                    INSERT INTO assertion VALUES
                        ('asrt-001', 'person_sarah', 'decided_architecture', NULL, 'Kafka streaming', 'src-alpha-001', 'current', 1, 'authoritative'),
                        ('asrt-002', 'person_alex', 'responsible_for', NULL, 'deployment pipeline', 'src-alpha-001', 'current', 1, 'authoritative');

                    INSERT INTO evidence_item VALUES
                        ('ev-001', 'src-alpha-001', 'timestamp', '00:02:15', '00:02:15', NULL),
                        ('ev-002', 'src-alpha-001', 'timestamp', '00:03:00', '00:03:00', NULL);

                    INSERT INTO assertion_evidence VALUES
                        ('asrt-001', 'ev-001'),
                        ('asrt-002', 'ev-002');
                    """
                )
                conn.commit()
            finally:
                conn.close()

            # Execute query cascade against the isolated generation
            iso_engine = GraphFirstQueryEngine(tmp_root, graph_db, sidecar_db)
            res = iso_engine.execute_query(
                "What is Sarah Chen responsible for?",
                hydrate_forensic_quotes=True,
            )

            self.assertTrue(res.success)
            self.assertEqual(res.resolved_tier, 1)
            self.assertEqual(len(res.assertions), 1)
            self.assertEqual(res.assertions[0]["subject_node_id"], "person_sarah")
            self.assertEqual(len(res.forensic_quotes), 1)
            self.assertEqual(res.forensic_quotes[0].target_turn.speaker, "Dr. Sarah Chen")
            self.assertIn("Kafka streaming", res.forensic_quotes[0].target_turn.utterance)

    # -------------------------------------------------------------------------
    # E2E Test 3: Sidecar Hybrid Fallback on Ungrounded Tokens
    # -------------------------------------------------------------------------
    def test_e2e_sidecar_hybrid_fallback_on_ungrounded_entity(self):
        """Ungrounded tokens with no direct node matches fall back to Tier 2 sidecar."""
        res = self.engine.execute_query("quantum_encryption_protocol_spec")
        # When neither graph nor sidecar matches, escalates to Tier 4 HITL
        if res.resolved_tier == 2:
            self.assertTrue(res.success)
            self.assertGreater(len(res.matched_nodes), 0)
        else:
            self.assertEqual(res.resolved_tier, 4)
            self.assertTrue(res.hitl_required)

    # -------------------------------------------------------------------------
    # E2E Test 4: Governed HITL Ambiguity Escalation
    # -------------------------------------------------------------------------
    def test_e2e_governed_hitl_escalation_flow(self):
        """Ambiguous or ungrounded queries escalate cleanly to Tier 4 HITL Gate."""
        question = "completely nonexistent alien artifact discovery 12345XYZ"
        res = self.engine.execute_query(question)

        self.assertFalse(res.success)
        self.assertEqual(res.resolved_tier, 4)
        self.assertTrue(res.hitl_required)
        self.assertIsNotNone(res.hitl_clarification)
        self.assertIn("question", res.hitl_clarification)
        self.assertIn("options", res.hitl_clarification)
        self.assertGreaterEqual(len(res.hitl_clarification["options"]), 2)

    # -------------------------------------------------------------------------
    # E2E Test 5: Pre-Command Firewall Blocks Shell Anti-Pattern
    # -------------------------------------------------------------------------
    def test_e2e_prohibited_anti_pattern_interception(self):
        """Firewall prevents unindexed recursive directory searches fail-closed."""
        prohibited_commands = [
            "grep -ri 'Shreyak' output/",
            "find output/ -type f -name '*.md'",
            "rg 'Amit' dual_pipeline/",
            "ag 'responsibility' output/",
        ]
        for cmd in prohibited_commands:
            with self.assertRaises(GraphDBRequiredError) as ctx:
                self.firewall.inspect(cmd)
            self.assertIn("Prohibited shell search command blocked", str(ctx.exception))

    # -------------------------------------------------------------------------
    # E2E Test 6: SLA Latency & Performance Bounds
    # -------------------------------------------------------------------------
    def test_e2e_sla_performance_and_latency_bounds(self):
        """Validates that graph traversal achieves <15ms P95 latency bounds."""
        latencies: List[float] = []
        for _ in range(50):
            t0 = time.perf_counter()
            res = self.engine.execute_query(
                "Shreyak responsibilities",
                source_id="source-5c0d92c8fc4cb8966f19523d",
                hydrate_forensic_quotes=False,
            )
            lat = (time.perf_counter() - t0) * 1000.0
            latencies.append(lat)
            self.assertTrue(res.success)

        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]

        # Traversal target SLA is < 15ms P95
        self.assertLess(p50, 10.0, f"P50 latency {p50:.3f}ms exceeded 10ms SLA")
        self.assertLess(p95, 15.0, f"P95 latency {p95:.3f}ms exceeded 15ms SLA")


if __name__ == "__main__":
    unittest.main()
