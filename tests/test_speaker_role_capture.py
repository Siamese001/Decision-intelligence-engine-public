#!/usr/bin/env python3
"""
tests/test_speaker_role_capture.py

Unit test suite for Wave 1 speaker role resolution capture and provenance authentication.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
    ResolutionProvenance,
    ResolutionStatus,
    SpeakerRoleResolution,
    create_speaker_role_resolution,
    is_conflated_organization,
    is_placeholder_title,
)
from dual_pipeline.transcript_stages_core.c05_title_gate import (
    apply_hitl_role_resolution,
)


class TestSpeakerRoleCaptureUnit(unittest.TestCase):
    """Unit tests for speaker role resolution contracts and capture gates."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.sqlite_path = self.work_dir / "hitl_decision_ledger.sqlite"
        self.json_path = self.work_dir / "hitl_decision_ledger.json"
        self.store = HITLDecisionStore(self.json_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_authentic_human_capture_and_ledger_persistence(self) -> None:
        """Authentic human resolution persists to ledger with hash digest and verified status."""
        resolution = create_speaker_role_resolution(
            person_name="Marcus Balk",
            resolved_role="Principal Director",
            organization="PartnerAdvisory",
            provenance=ResolutionProvenance.HUMAN_OPERATOR,
            is_human_confirmed=True,
            operator_origin={"source": "human_operator", "text": "Confirmed by operator"},
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )

        self.assertEqual(resolution.person_name, "Marcus Balk")
        self.assertEqual(resolution.resolved_role, "Principal Director")
        self.assertEqual(resolution.organization, "PartnerAdvisory")
        self.assertTrue(resolution.is_human_confirmed)
        self.assertEqual(resolution.status, ResolutionStatus.AUTHENTICATED_HUMAN.value)
        self.assertIsNotNone(resolution.ledger_entry_id)
        self.assertTrue(len(resolution.digest) == 64)

        # Verify SQLite ledger persistence
        self.assertTrue(self.sqlite_path.exists())
        self.assertTrue(self.json_path.exists())

    def test_directory_and_graph_lookups_are_not_human_confirmed(self) -> None:
        """Automated lookups never gain human verified status or write to operator ledger."""
        res_dir = create_speaker_role_resolution(
            person_name="Daniel Brankin",
            resolved_role="Technical Architect",
            organization="Apex Global Enterprises",
            provenance=ResolutionProvenance.ENTERPRISE_DIRECTORY,
            is_human_confirmed=False,
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )
        self.assertFalse(res_dir.is_human_confirmed)
        self.assertEqual(res_dir.status, ResolutionStatus.DIRECTORY_LOOKUP.value)
        self.assertIsNone(res_dir.ledger_entry_id)

        res_graph = create_speaker_role_resolution(
            person_name="Sharon Diaram",
            resolved_role="Scrum Master",
            provenance=ResolutionProvenance.GRAPH_LOOKUP,
            is_human_confirmed=False,
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )
        self.assertFalse(res_graph.is_human_confirmed)
        self.assertEqual(res_graph.status, ResolutionStatus.GRAPH_LOOKUP.value)
        self.assertIsNone(res_graph.ledger_entry_id)

    def test_synthetic_approval_marker_fails_closed(self) -> None:
        """Synthetic stop-hook markers are rejected as unauthorized origins."""
        with self.assertRaises(ValueError) as ctx:
            create_speaker_role_resolution(
                person_name="Marcus Balk",
                resolved_role="Workstream Lead",
                provenance=ResolutionProvenance.HUMAN_OPERATOR,
                is_human_confirmed=True,
                operator_origin={"source": "system", "text": "auto_approved by stop hook"},
                sqlite_path=self.sqlite_path,
                json_path=self.json_path,
            )
        self.assertIn("Synthetic approval rejected", str(ctx.exception))

    def test_conflated_organization_as_title_rejected(self) -> None:
        """Organization names masquerading as titles fail validation."""
        self.assertTrue(is_conflated_organization("PartnerAdvisory"))
        self.assertTrue(is_conflated_organization("APEX"))
        self.assertTrue(is_conflated_organization("Apex Global Enterprises"))
        self.assertFalse(is_conflated_organization("Solution Architect"))

        with self.assertRaises(ValueError) as ctx:
            create_speaker_role_resolution(
                person_name="Jane Doe",
                resolved_role="PartnerAdvisory",
                organization="PartnerAdvisory",
                sqlite_path=self.sqlite_path,
                json_path=self.json_path,
            )
        self.assertIn("Conflation error", str(ctx.exception))

    def test_placeholder_title_rejected(self) -> None:
        """Placeholder or empty titles are rejected."""
        self.assertTrue(is_placeholder_title("[Title Pending Operator Confirmation]"))
        self.assertTrue(is_placeholder_title("Unknown"))
        self.assertTrue(is_placeholder_title(""))
        self.assertFalse(is_placeholder_title("Project Lead"))

        with self.assertRaises(ValueError) as ctx:
            create_speaker_role_resolution(
                person_name="Jane Doe",
                resolved_role="[Title Pending]",
                sqlite_path=self.sqlite_path,
                json_path=self.json_path,
            )
        self.assertIn("placeholder", str(ctx.exception).lower())

    def test_roster_format_consistency(self) -> None:
        """String, list, and dict rosters are consistently updated by apply_hitl_role_resolution."""
        # 1. String roster
        meta_str = {"roster": "Alice, Bob (PartnerAdvisory)", "attendees": "Alice, Bob"}
        receipt_str = apply_hitl_role_resolution(
            meta_str,
            "Alice",
            "Technical Architect",
            store=self.store,
            is_human_confirmed=True,
        )
        self.assertEqual(meta_str["hitl_role_resolutions"]["Alice"], "Technical Architect")
        self.assertIn("Alice (Technical Architect)", meta_str["roster"])
        self.assertIn("Alice (Technical Architect)", meta_str["attendees"])
        self.assertTrue(receipt_str["hitl_receipt_bound"])

        # 2. Dict roster mapping names to roles
        meta_dict = {"roster": {"Bob": "Placeholder Role"}}
        receipt_dict = apply_hitl_role_resolution(
            meta_dict,
            "Bob",
            "Lead Delivery Consultant",
            store=self.store,
            is_human_confirmed=True,
        )
        self.assertEqual(meta_dict["roster"]["Bob"], "Lead Delivery Consultant")
        self.assertEqual(meta_dict["hitl_role_resolutions"]["Bob"], "Lead Delivery Consultant")

        # 3. List of dicts roster
        meta_list = {"roster": [{"name": "Charlie", "role": ""}]}
        receipt_list = apply_hitl_role_resolution(
            meta_list,
            "Charlie",
            "Scrum Master",
            store=self.store,
            is_human_confirmed=True,
        )
        self.assertEqual(meta_list["roster"][0]["role"], "Scrum Master")
        self.assertEqual(meta_list["hitl_role_resolutions"]["Charlie"], "Scrum Master")

    def test_no_graph_writes_during_capture(self) -> None:
        """Capture gate strictly operates on metadata and ledger without graph database writes."""
        metadata = {"roster": "David"}
        receipt = apply_hitl_role_resolution(
            metadata,
            "David",
            "Security Architect",
            store=self.store,
            is_human_confirmed=True,
        )
        self.assertEqual(receipt["status"], "RESOLVED_HITL")
        # Ensure no graph files or databases were provisioned
        graph_files = list(self.work_dir.glob("*.db"))
        self.assertEqual(len(graph_files), 0)

    def test_failed_ledger_persistence_fails_closed(self) -> None:
        """If ledger file cannot be written, capture fails closed rather than fabricating success."""
        # Supply invalid non-writable path
        bad_sqlite = Path("/nonexistent/readonly/dir/ledger.sqlite")
        bad_json = Path("/nonexistent/readonly/dir/ledger.json")

        with self.assertRaises(RuntimeError) as ctx:
            create_speaker_role_resolution(
                person_name="Marcus Balk",
                resolved_role="Project Lead",
                is_human_confirmed=True,
                sqlite_path=bad_sqlite,
                json_path=bad_json,
            )
        self.assertIn("Failed to persist", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
