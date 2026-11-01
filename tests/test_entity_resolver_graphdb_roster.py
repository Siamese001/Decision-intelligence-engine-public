"""
tests/test_entity_resolver_graphdb_roster.py

Wave 1 Acceptance Test Suite:
Validates GraphDB-backed attendee name, nickname, initial, and team roster resolution:
1. Partial first names ('Ed', 'Jen', 'Amit', 'Jake', 'Morgan Vance')
2. Last-initial disambiguation ('Alex S', 'Alex C')
3. Group shorthand expansion ('PartnerAdvisory team', 'Apex Global team')
4. Same-first-name project cohort disambiguation ('Alex' in PartnerAdvisory vs Apex Global context)
5. Non-placeholder titles and verified organizations from GraphDB.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
    resolve_attendee_list,
    resolve_person_entity,
)


class TestEntityResolverGraphdbRoster(unittest.TestCase):
    """Wave 1 Acceptance Suite for GraphDB roster and attendee resolution."""

    def setUp(self):
        self.resolver = PreSynthesisEntityResolver(repo_root=ROOT)
        self.resolver._ensure_loaded()

    def test_ed_alex_partner_advisory_team_resolution(self):
        """Verify 'Ed, Alex, PartnerAdvisory team' expands and resolves all members with full titles."""
        resolved = self.resolver.resolve_attendee_list(
            "Ed, Alex, PartnerAdvisory team", project_name="project_lens"
        )
        self.assertGreaterEqual(len(resolved), 6)

        names = {r.canonical_name for r in resolved}
        orgs = {r.canonical_name: r.org for r in resolved}
        roles = {r.canonical_name: r.role for r in resolved}

        # Verify Eduardo More (Apex Global)
        self.assertTrue(
            any("Eduardo More" in n or "Ed More" in n for n in names),
            f"Eduardo More not resolved in {names}",
        )
        ed_key = next(n for n in names if "Eduardo More" in n or "Ed More" in n)
        self.assertIn("Apex Global", orgs[ed_key])
        self.assertNotEqual(roles[ed_key], "Participant")

        # Verify Alex Corrow (Apex Global, because Alex Sanchez is in PartnerAdvisory team)
        self.assertTrue(
            any(k in names for k in ("Alex Corrow", "Alexandra Coro", "Alexandra Corrow")),
            f"Alex Corrow not resolved in {names}",
        )

        # Verify PartnerAdvisory Team members present
        self.assertTrue(any("Alex Sanchez" in n for n in names))
        self.assertTrue(any("Amit Ayer" in n for n in names))
        self.assertTrue(any("Jake Stark" in n for n in names))
        self.assertTrue(any("Morgan Vance Krutilla" in n for n in names))
        self.assertTrue(any("Pan" in n for n in names))

        # Check organizations
        for r in resolved:
            if "Sanchez" in r.canonical_name or "Ayer" in r.canonical_name or "Stark" in r.canonical_name or "Krutilla" in r.canonical_name or "Pan" in r.canonical_name:
                self.assertEqual(r.org, "PartnerAdvisory")
            self.assertNotEqual(r.role, "Participant", f"{r.canonical_name} has placeholder role")

    def test_full_partner_advisory_team_individual_roster(self):
        """Verify 'Jen, Amit, Alex, Jake, Morgan Vance' on project_lens resolves to PartnerAdvisory team."""
        resolved = self.resolver.resolve_attendee_list(
            "Jen, Amit, Alex, Jake, Morgan Vance", project_name="project_lens"
        )
        self.assertEqual(len(resolved), 5)

        names = [r.canonical_name for r in resolved]
        orgs = [r.org for r in resolved]
        roles = [r.role for r in resolved]

        # In a PartnerAdvisory-only roster, Alex must resolve to Alex Sanchez
        self.assertTrue(any("Alex Sanchez" in n for n in names), f"Alex Sanchez not in {names}")
        self.assertTrue(any("Pan" in n for n in names), f"Jen/Jennifer Pan not in {names}")
        self.assertTrue(any("Amit Ayer" in n for n in names), f"Amit Ayer not in {names}")
        self.assertTrue(any("Jake Stark" in n for n in names), f"Jake Stark not in {names}")
        self.assertTrue(any("Morgan Vance Krutilla" in n for n in names), f"Morgan Vance Krutilla not in {names}")

        # All must be PartnerAdvisory
        for org in orgs:
            self.assertEqual(org, "PartnerAdvisory")

        # All must have non-placeholder titles
        for r in resolved:
            self.assertNotEqual(r.role, "Participant")
            self.assertNotEqual(r.role, "Unknown")

    def test_single_token_resolution_and_titles(self):
        """Verify 2-char tokens, first names, and initials resolve accurately."""
        # 2-character token 'Ed'
        ed = self.resolver.resolve("Ed")
        self.assertIsNotNone(ed)
        self.assertIn("More", ed.canonical_name)
        self.assertIn("Apex Global", ed.org)
        self.assertIn("Atlas Lead", ed.role)

        # Initialed names 'Alex S' vs 'Alex C'
        alex_s = self.resolver.resolve("Alex S")
        self.assertIsNotNone(alex_s)
        self.assertEqual(alex_s.canonical_name, "Alex Sanchez")
        self.assertEqual(alex_s.org, "PartnerAdvisory")
        self.assertIn("Tech Lead", alex_s.role)

        alex_c = self.resolver.resolve("Alex C")
        self.assertIsNotNone(alex_c)
        self.assertTrue("Alex Corrow" in alex_c.canonical_name or "Alexandra Coro" in alex_c.canonical_name)
        self.assertIn("Apex Global", alex_c.org)

        # Single first names
        jake = self.resolver.resolve("Jake")
        self.assertIsNotNone(jake)
        self.assertEqual(jake.canonical_name, "Jake Stark")
        self.assertEqual(jake.org, "PartnerAdvisory")

        karyn = self.resolver.resolve("Morgan Vance")
        self.assertIsNotNone(karyn)
        self.assertEqual(karyn.canonical_name, "Morgan Vance Krutilla")
        self.assertEqual(karyn.org, "PartnerAdvisory")

        amit = self.resolver.resolve("Amit")
        self.assertIsNotNone(amit)
        self.assertEqual(amit.canonical_name, "Amit Ayer")
        self.assertEqual(amit.org, "PartnerAdvisory")

        jen = self.resolver.resolve("Jen")
        self.assertIsNotNone(jen)
        self.assertTrue("Pan" in jen.canonical_name)
        self.assertEqual(jen.org, "PartnerAdvisory")

    def test_team_group_expansion_lens(self):
        """Verify 'PartnerAdvisory team' and 'Apex Global team' expand correctly on project_lens."""
        partner_advisory_team = self.resolver.resolve_attendee_list("PartnerAdvisory team", project_name="project_lens")
        self.assertEqual(len(partner_advisory_team), 5)
        for mem in partner_advisory_team:
            self.assertEqual(mem.org, "PartnerAdvisory")
            self.assertNotEqual(mem.role, "Participant")

        apex_global_team = self.resolver.resolve_attendee_list("Apex Global team", project_name="project_lens")
        self.assertGreaterEqual(len(apex_global_team), 2)
        for mem in apex_global_team:
            self.assertIn("Apex Global", mem.org)
            self.assertNotEqual(mem.role, "Participant")

    def test_parenthetical_team_roster_parsing(self):
        """Verify 'Full PartnerAdvisory Team (Jen, Amit, Alex, Jake, Morgan Vance)' parses all members."""
        raw = "Full PartnerAdvisory Team (Jen, Amit, Alex, Jake, Morgan Vance)"
        resolved = resolve_attendee_list(raw, project_name="project_lens")
        self.assertEqual(len(resolved), 5)
        names = {r.canonical_name for r in resolved}
        self.assertTrue(any("Sanchez" in n for n in names))
        self.assertTrue(any("Ayer" in n for n in names))
        self.assertTrue(any("Stark" in n for n in names))
        self.assertTrue(any("Krutilla" in n for n in names))
        self.assertTrue(any("Pan" in n for n in names))


if __name__ == "__main__":
    unittest.main()
