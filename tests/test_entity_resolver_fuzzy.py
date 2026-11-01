"""tests/test_entity_resolver_fuzzy.py

Unit test suite validating:
1. Levenshtein distance <= 1 fuzzy matching fallback in entity_resolver.py.
2. Punctuation and typo alias coverage (Jarrod Bryant, Alexandra Corrow, Casey Okeefe, etc.).
3. Directory deduplication (Cameron Mercer -> Cameron Mercer).
4. Newly enrolled executive stakeholders (Anuj Chokshi, Lancey Lexima, John Andersen).
5. Corporate affiliation parenthetical isolation (preventing '(PartnerAdvisory)' tags from poisoning Apex Global attendees).
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
    _levenshtein_distance,
    resolve_company_for_person,
)


class TestEntityResolverFuzzy(unittest.TestCase):
    def setUp(self):
        self.resolver = PreSynthesisEntityResolver(repo_root=ROOT, enable_graph_query=False)

    def test_levenshtein_distance_helper(self):
        self.assertEqual(_levenshtein_distance("jarod", "jarrod"), 1)
        self.assertEqual(_levenshtein_distance("coro", "corrow"), 2)
        self.assertEqual(_levenshtein_distance("stefan", "stephan"), 2)
        self.assertEqual(_levenshtein_distance("same", "same"), 0)

    def test_exact_alias_resolution(self):
        """Verify explicit aliases resolve to canonical entities."""
        cases = [
            ("Jarrod Bryant", "Jarod Bryant", "Apex Global Enterprises (PGD)"),
            ("Alexandra Corrow", "Alexandra Coro", "Apex Global Enterprises (NEA)"),
            ("Alex Corrow", "Alexandra Coro", "Apex Global Enterprises (NEA)"),
            ("Casey Okeefe", "Casey O'Keefe", "Apex Global Enterprises (NEA)"),
            ("John Anderson", "John Andersen", "Apex Global Analytics"),
            ("Lancy Lexima", "Lancey Lexima", "Apex Global Enterprises (PGD)"),
            ("Damian Gessler", "Damyn Gessler", "PartnerAdvisory"),
        ]
        for raw, exp_canon, exp_org in cases:
            ent = self.resolver.resolve(raw)
            self.assertIsNotNone(ent, f"Failed resolving {raw}")
            self.assertEqual(ent.canonical_name, exp_canon)
            self.assertEqual(ent.org, exp_org)

    def test_unaliased_fuzzy_distance_1_fallback(self):
        """Verify un-aliased typos resolve correctly via Levenshtein-1 fuzzy matching."""
        fuzzy_cases = [
            ("Jarodd Bryant", "Jarod Bryant", "Apex Global Enterprises (PGD)"),
            ("Alexsandra Coro", "Alexandra Coro", "Apex Global Enterprises (NEA)"),
            ("Mandy Hopman", "harper Hoffman", "Apex Global Enterprises (NEA)"),
            ("Morgan Vance Krutila", "Morgan Vance Krutilla", "PartnerAdvisory"),
            ("Shreyak Guptaa", "Shreyak Gupta", "PartnerAdvisory"),
        ]
        for raw, exp_canon, exp_org in fuzzy_cases:
            ent = self.resolver.resolve(raw)
            self.assertIsNotNone(ent, f"Failed fuzzy resolving {raw}")
            self.assertEqual(ent.canonical_name, exp_canon)
            self.assertEqual(ent.org, exp_org)
            self.assertEqual(ent.source, "directory_fuzzy_levenshtein_1")

    def test_directory_deduplication(self):
        """Verify Cameron Mercer and Cameron Mercer unify to single canonical identity."""
        ent1 = self.resolver.resolve("Cameron Mercer")
        ent2 = self.resolver.resolve("Cameron Mercer")
        self.assertIsNotNone(ent1)
        self.assertIsNotNone(ent2)
        self.assertEqual(ent1.canonical_name, "Cameron Mercer")
        self.assertEqual(ent2.canonical_name, "Cameron Mercer")
        self.assertEqual(ent1.org, "PartnerAdvisory")

    def test_enrolled_stakeholders(self):
        """Verify newly enrolled high-profile leaders resolve correctly."""
        anuj = self.resolver.resolve("Anuj Chokshi")
        self.assertIsNotNone(anuj)
        self.assertEqual(anuj.canonical_name, "Anuj Chokshi")
        self.assertEqual(anuj.org, "Apex Global Enterprises")

        lancey = self.resolver.resolve("Lancey Lexima")
        self.assertIsNotNone(lancey)
        self.assertEqual(lancey.canonical_name, "Lancey Lexima")

        andersen = self.resolver.resolve("John Andersen")
        self.assertIsNotNone(andersen)
        self.assertEqual(andersen.canonical_name, "John Andersen")

    def test_parenthetical_company_isolation(self):
        """Verify parenthetical annotations do not poison corporate attribution."""
        # Jack O'Shea is Apex Global Analytics, even if raw input has '(PartnerAdvisory)'
        ent = self.resolver.resolve("Jack O'Shea (PartnerAdvisory)")
        self.assertIsNotNone(ent)
        self.assertEqual(ent.canonical_name, "Jack O'Shea")
        self.assertEqual(ent.org, "Apex Global Analytics")

        org = resolve_company_for_person("Jack O'Shea (PartnerAdvisory)")
        self.assertEqual(org, "Apex Global Analytics")

    def test_surname_coverage(self):
        """Verify high-frequency surnames resolve without full first names."""
        surnames = [
            ("ayer", "Amit Ayer"),
            ("morton", "Bailey Morton"),
            ("sanchez", "Alex Sanchez"),
            ("hopmann", "harper Hoffman"),
            ("krutilla", "Morgan Vance Krutilla"),
            ("gupta", "Shreyak Gupta"),
            ("wellington", "Jake Wellington"),
        ]
        for sname, exp_canon in surnames:
            ent = self.resolver.resolve(sname)
            self.assertIsNotNone(ent, f"Failed resolving surname {sname}")
            self.assertEqual(ent.canonical_name, exp_canon)


if __name__ == "__main__":
    unittest.main()
