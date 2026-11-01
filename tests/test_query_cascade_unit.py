"""Unit tests for the 5-tier Query Cascade, GraphFirstQueryEngine, and EvidenceLocatorService."""

from __future__ import annotations

import unittest
from pathlib import Path

from dual_pipeline.query_core.evidence_locator import EvidenceLocatorService
from dual_pipeline.query_core.firewall import (
    GraphDBRequiredError,
    ProhibitedCommandGuard,
    check_command_firewall,
)
from dual_pipeline.query_core.graph_first_engine import GraphFirstQueryEngine


class TestQueryCascadeUnit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parents[1]
        target_gen = "generation-6b972fcf462da2799e2af1807c0f"
        gen_dir = cls.repo_root / ".local" / "dual_pipeline" / "generations" / target_gen
        if not (gen_dir / "graph.sqlite").exists():
            main_repo_gen = cls.repo_root.parent.parent / ".local" / "dual_pipeline" / "generations" / target_gen
            if (main_repo_gen / "graph.sqlite").exists():
                gen_dir = main_repo_gen
        cls.graph_path = gen_dir / "graph.sqlite"
        cls.sidecar_path = gen_dir / "sidecar.sqlite"
        cls.cold_archive = (
            "output/project_nexus/09-22-2026/Google_Internal_Anya_Shreyak/01_Google_Internal_Anya_Shreyak_09-22-2026_1000AM_1030AM_ET_hitl_resolved_transcript.md"
        )

    def setUp(self):
        self.locator = EvidenceLocatorService(self.repo_root)
        self.firewall = ProhibitedCommandGuard()
        self.engine = GraphFirstQueryEngine(
            self.repo_root, self.graph_path, self.sidecar_path
        )

    # -------------------------------------------------------------------------
    # EvidenceLocatorService Tests
    # -------------------------------------------------------------------------
    def test_evidence_locator_path_resolution(self):
        resolved = self.locator.resolve_archive_path(self.cold_archive)
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.is_file())
        self.assertTrue(resolved.is_relative_to(self.repo_root))

    def test_evidence_locator_path_traversal_rejected(self):
        bad_path = "../../etc/passwd"
        resolved = self.locator.resolve_archive_path(bad_path)
        self.assertIsNone(resolved)

    def test_evidence_locator_seek_exact_timestamp(self):
        result = self.locator.locate_exchange(
            cold_archive_ref=self.cold_archive,
            locator_kind="timestamp",
            locator_value="00:11:45",
            source_id="source-5c0d92c8fc4cb8966f19523d",
        )
        self.assertTrue(result.success)
        self.assertIsNotNone(result.target_turn)
        self.assertEqual(result.target_turn.speaker, "Shreyak Gupta")
        self.assertIn("shared Teams channel", result.target_turn.utterance)
        self.assertLess(result.latency_ms, 50.0)

    def test_evidence_locator_seek_with_context(self):
        result = self.locator.locate_exchange(
            cold_archive_ref=self.cold_archive,
            locator_kind="timestamp",
            locator_value="00:04:30",
            context_window_turns=1,
            source_id="source-5c0d92c8fc4cb8966f19523d",
        )
        self.assertTrue(result.success)
        self.assertIsNotNone(result.target_turn)
        self.assertEqual(result.target_turn.speaker, "Amit Ayer")
        self.assertIn("Why are we competing?", result.target_turn.utterance)
        self.assertGreater(len(result.context_turns), 0)

    def test_evidence_locator_missing_timestamp(self):
        result = self.locator.locate_exchange(
            cold_archive_ref=self.cold_archive,
            locator_kind="timestamp",
            locator_value="99:99:99",
        )
        self.assertFalse(result.success)
        self.assertIn("not found in archive", result.error_message)

    # -------------------------------------------------------------------------
    # GraphFirstQueryEngine Cascade Tests
    # -------------------------------------------------------------------------
    def test_tier1_relational_traversal_success(self):
        query = "What was Shreyak responsible for in the Google meeting?"
        result = self.engine.execute_query(query, source_id="source-5c0d92c8fc4cb8966f19523d")
        self.assertTrue(result.success)
        self.assertEqual(result.resolved_tier, 1)
        self.assertGreater(len(result.assertions), 0)
        self.assertLess(result.total_latency_ms, 150.0)

        # Check Shreyak assertions exist
        shreyak_claims = [
            a["object_literal"] for a in result.assertions if a.get("subject_node_id") == "stakeholder_shreyak_gupta" and a.get("object_literal")
        ]
        self.assertTrue(any("Teams channel" in c or "Terraform" in c for c in shreyak_claims))

    def test_tier1_comparative_query_resolution(self):
        query = "google meeting with Anya and Shreyak what was SHreyak responsible for vs. what am I respomsible for?"
        result = self.engine.execute_query(query, source_id="source-5c0d92c8fc4cb8966f19523d")
        self.assertTrue(result.success)
        self.assertEqual(result.resolved_tier, 1)

        subjects = {a["subject_node_id"] for a in result.assertions}
        self.assertIn("stakeholder_shreyak_gupta", subjects)
        self.assertIn("person_amit", subjects)
        self.assertGreaterEqual(len(result.assertions), 10)

    def test_tier1_with_tier3_forensic_hydration(self):
        query = "What did Shreyak say about Teams in the meeting?"
        result = self.engine.execute_query(
            query, source_id="source-5c0d92c8fc4cb8966f19523d", hydrate_forensic_quotes=True
        )
        self.assertTrue(result.success)
        self.assertEqual(result.resolved_tier, 1)
        self.assertGreater(len(result.forensic_quotes), 0)
        self.assertTrue(any("Teams" in q.target_turn.utterance for q in result.forensic_quotes if q.target_turn))

    def test_tier4_hitl_escalation_on_unknown_domain(self):
        query = "completely nonexistent alien artifact discovery 12345XYZ"
        result = self.engine.execute_query(query)
        self.assertFalse(result.success)
        self.assertEqual(result.resolved_tier, 4)
        self.assertTrue(result.hitl_required)
        self.assertIsNotNone(result.hitl_clarification)
        self.assertIn("options", result.hitl_clarification)

    # -------------------------------------------------------------------------
    # Prohibited Search Firewall Tests
    # -------------------------------------------------------------------------
    def test_firewall_blocks_shell_grep(self):
        cmd = 'grep -ri "Shreyak" output/'
        with self.assertRaises(GraphDBRequiredError):
            check_command_firewall(cmd)

    def test_firewall_permits_safe_commands(self):
        cmd = 'python -m pytest tests/test_governance_rules.py'
        # Must not raise
        check_command_firewall(cmd)


if __name__ == "__main__":
    unittest.main()
