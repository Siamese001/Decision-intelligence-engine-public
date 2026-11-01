"""Unit tests for C0 string roster parsing, APEX enterprise directory priors, and C0.5/C0.6 auto-resolution."""

from __future__ import annotations

import unittest

from dual_pipeline.transcript_metadata_core.entity_resolver import (
    resolve_person_entity,
)
from dual_pipeline.transcript_stages_core.c0_gates import (
    _enrich_roster,
    _extract_pre_enrichment_roles,
)
from dual_pipeline.transcript_stages_core.c05_title_gate import (
    C05TitleCompletenessGate,
    apply_hitl_role_resolution,
)
from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import (
    C06GraphDBRoleVerificationGate,
)


class TestC0StringRosterAndDirectoryTitles(unittest.TestCase):
    """Test suite for string roster parsing and directory prior auto-resolution."""

    def test_extract_pre_enrichment_roles_string_formats(self):
        """String rosters with various delimiters and parenthesized roles parse cleanly."""
        # Simple comma-separated
        roles = _extract_pre_enrichment_roles("Alice, Bob, Charlie")
        self.assertEqual(roles, {"Alice": "", "Bob": "", "Charlie": ""})

        # Semicolon-separated
        roles = _extract_pre_enrichment_roles("Alice; Bob; Charlie")
        self.assertEqual(roles, {"Alice": "", "Bob": "", "Charlie": ""})

        # Newline-separated
        roles = _extract_pre_enrichment_roles("Alice\nBob\nCharlie")
        self.assertEqual(roles, {"Alice": "", "Bob": "", "Charlie": ""})

        # Roles in parentheses
        roles = _extract_pre_enrichment_roles("Alice (Senior Architect), Bob (Product Owner)")
        self.assertEqual(roles, {"Alice": "Senior Architect", "Bob": "Product Owner"})

        # Delimiter inside parentheses must not be split
        roles = _extract_pre_enrichment_roles(
            "Alice (Senior Architect, Systems), Bob (Product Owner, AI); Charlie (Lead, Core)"
        )
        self.assertEqual(roles, {
            "Alice": "Senior Architect, Systems",
            "Bob": "Product Owner, AI",
            "Charlie": "Lead, Core",
        })

    def test_extract_pre_enrichment_roles_dict_with_string_values(self):
        """Dict rosters where values are strings parse cleanly."""
        roster = {
            "Apex Global Enterprises (NEA)": "Daniel Brankin, Jill Cizewski",
            "PartnerAdvisory": "Amit Ayer (CAO), Taylor Mercer (Delivery Lead)",
        }
        roles = _extract_pre_enrichment_roles(roster)
        self.assertIn("Daniel Brankin", roles)
        self.assertIn("Jill Cizewski", roles)
        self.assertEqual(roles["Amit Ayer"], "CAO")
        self.assertEqual(roles["Taylor Mercer"], "Delivery Lead")

    def test_enrich_roster_string(self):
        """String roster is enriched and stored in metadata."""
        metadata = {"roster": "Daniel Brankin, Jill Cizewski"}
        _enrich_roster("Daniel Brankin, Jill Cizewski", metadata)
        self.assertIn("roster", metadata)
        self.assertIsInstance(metadata["roster"], list)
        self.assertEqual(len(metadata["roster"]), 2)
        # Should have populated attendees string
        self.assertIn("attendees", metadata)
        self.assertIn("Daniel Brankin", metadata["attendees"])
        self.assertIn("Jill Cizewski", metadata["attendees"])

    def test_apply_hitl_role_resolution_string_roster_and_attendees(self):
        """Updating string roster and attendees replaces target name with name (role)."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp_dir:
            metadata = {
                "roster": "Alice, Bob (Dev)",
                "attendees": "Alice, Bob (Dev)",
            }
            apply_hitl_role_resolution(metadata, "Alice", "Principal Architect", repo_root=Path(tmp_dir))
            self.assertEqual(metadata["roster"], "Alice (Principal Architect), Bob (Dev)")
            self.assertEqual(metadata["attendees"], "Alice (Principal Architect), Bob (Dev)")

    def test_resolve_person_entity_fpl_directory_priors(self):
        """Daniel Brankin, Jill Cizewski, and Sharon Diaram resolve to canonical APEX priors."""
        # Full names
        daniel = resolve_person_entity("Daniel Brankin")
        self.assertIsNotNone(daniel)
        self.assertEqual(daniel.canonical_name, "Daniel Brankin")
        self.assertEqual(daniel.org, "Apex Global Enterprises (NEA / APEX)")
        self.assertEqual(daniel.role, "Project Development Lead / Jarvis Product Owner")
        self.assertIn("directory_prior", daniel.source)

        jill = resolve_person_entity("Jill Cizewski")
        self.assertIsNotNone(jill)
        self.assertEqual(jill.canonical_name, "Jill Cizewski")
        self.assertEqual(jill.org, "Apex Global Enterprises (NEA / APEX)")
        self.assertEqual(jill.role, "Business Lead / Project Development Specialist")
        self.assertIn("directory_prior", jill.source)

        sharon = resolve_person_entity("Sharon Diaram")
        self.assertIsNotNone(sharon)
        self.assertEqual(sharon.canonical_name, "Sharon Diaram")
        self.assertEqual(sharon.org, "Apex Global Enterprises (NEA / APEX)")
        self.assertEqual(sharon.role, "Data & Reporting Lead / Power BI Specialist")
        self.assertIn("directory_prior", sharon.source)

        # First name / alias keys
        self.assertEqual(resolve_person_entity("daniel").canonical_name, "Daniel Brankin")
        self.assertEqual(resolve_person_entity("jill").canonical_name, "Jill Cizewski")
        self.assertEqual(resolve_person_entity("sharon").canonical_name, "Sharon Diaram")

    def test_c05_gate_auto_resolves_fpl_directory_priors(self):
        """C0.5 gate auto-resolves directory priors without raising HITLRequiredError."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp_dir:
            metadata = {
                "attendees": "Jill Cizewski, Daniel Brankin, Taylor Mercer, Sharon",
            }
            ctx = {"metadata": metadata}
            gate = C05TitleCompletenessGate(repo_root=Path(tmp_dir))
            res = gate.execute(ctx)

            self.assertTrue(res.get("c05_title_gate_cleared"))
            attendees = res["metadata"]["attendees"]
            self.assertIn("Jill Cizewski (Business Lead / Project Development Specialist)", attendees)
            self.assertIn("Daniel Brankin (Project Development Lead / Jarvis Product Owner)", attendees)
            self.assertIn("Sharon (Data & Reporting Lead / Power BI Specialist)", attendees)

    def test_c06_gate_auto_resolves_fpl_directory_priors(self):
        """C0.6 gate auto-verifies directory priors and marks attendees verified."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp_dir:
            metadata = {
                "attendees": "Jill Cizewski, Daniel Brankin, Taylor Mercer, Sharon",
            }
            ctx = {"metadata": metadata}
            # Run C0.5 first to mirror pipeline sequence
            gate05 = C05TitleCompletenessGate(repo_root=Path(tmp_dir))
            ctx = gate05.execute(ctx)

            # Run C0.6
            gate06 = C06GraphDBRoleVerificationGate(repo_root=Path(tmp_dir))
            res = gate06.execute(ctx)

            self.assertTrue(res.get("c06_role_gate_cleared"))
            receipt = res["metadata"].get("graph_role_verification_receipt")
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt["status"], "CLEARED")
            self.assertEqual(receipt["unresolved_count"], 0)


if __name__ == "__main__":
    unittest.main()
