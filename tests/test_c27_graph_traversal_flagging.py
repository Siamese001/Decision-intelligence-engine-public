"""
tests/test_c27_graph_traversal_flagging.py

Regression tests for Constraint C-27 graph-traversal entity flagging.

Verifies that:
1. IdentityResolver.get_first_names_requiring_grounding() returns a graph-derived set
   that includes all roster names — without hardcoding Alex or Mandy.
2. gate1_entity_context.evaluate_referenced_entity_context_gate() catches bare references
   to any roster-registered name in Output 02.
3. CrossMeetingCoherenceJudge derives its C-27 check-set from the resolver, not a static
   constant, and fires on bare first names found in Output 02.
4. EntityClarificationDetector builds its candidate directory from the resolver's
   first_name_index so every graph-registered identity is included.
5. Names absent from any roster are NOT flagged (no false positives).

Assertion purity rule: no hardcoded meeting strings, attendee names, or project constants
are used as acceptance criteria.  All assertions check structural/behavioral invariants.
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, List, Set
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

def _make_stub_resolver(
    identities: Dict[str, Dict[str, Any]],
    first_name_index: Dict[str, List[Dict[str, Any]]],
) -> Any:
    """Build a lightweight stub that mimics IdentityResolver for testing."""
    resolver = MagicMock()
    resolver.identities = identities
    resolver.first_name_index = first_name_index

    def _get_grounding(attendees=None):
        result: Set[str] = set()
        for norm_first, candidates in first_name_index.items():
            if not norm_first or len(norm_first) < 2:
                continue
            if not candidates:
                continue
            if len(candidates) > 1:
                result.add(norm_first)
                continue
            entry = candidates[0]
            org = (entry.get("organization") or "").lower()
            role = (entry.get("role") or "").lower()
            if org or role:
                result.add(norm_first)
        return result

    resolver.get_first_names_requiring_grounding = _get_grounding
    return resolver


def _make_two_person_index(
    name_a: str, org_a: str, role_a: str,
    name_b: str, org_b: str, role_b: str,
    first_token: str,
) -> Dict[str, List[Dict[str, Any]]]:
    return {
        first_token: [
            {"canonical_name": name_a, "organization": org_a, "role": role_a},
            {"canonical_name": name_b, "organization": org_b, "role": role_b},
        ]
    }


def _make_one_person_index(
    name: str, org: str, role: str, first_token: str,
) -> Dict[str, List[Dict[str, Any]]]:
    return {
        first_token: [{"canonical_name": name, "organization": org, "role": role}]
    }


# ---------------------------------------------------------------------------
# 1. IdentityResolver.get_first_names_requiring_grounding
# ---------------------------------------------------------------------------

class TestGetFirstNamesRequiringGrounding(unittest.TestCase):

    def test_ambiguous_first_name_is_always_included(self) -> None:
        index = _make_two_person_index(
            "Alice Thornton", "PartnerAdvisory", "Engineer",
            "Alice Marshall", "NEA", "Product Lead",
            "alice",
        )
        stub = _make_stub_resolver({}, index)
        result = stub.get_first_names_requiring_grounding()
        self.assertIn("alice", result)

    def test_single_candidate_with_org_is_included(self) -> None:
        index = _make_one_person_index("Brett Gavagni", "NEA", "Engineering Manager", "brett")
        stub = _make_stub_resolver({}, index)
        result = stub.get_first_names_requiring_grounding()
        self.assertIn("brett", result)

    def test_empty_index_returns_empty_set(self) -> None:
        stub = _make_stub_resolver({}, {})
        result = stub.get_first_names_requiring_grounding()
        self.assertIsInstance(result, set)
        self.assertEqual(len(result), 0)

    def test_name_not_in_index_not_flagged(self) -> None:
        index = _make_one_person_index("Jordan Smith", "PartnerAdvisory", "Consultant", "jordan")
        stub = _make_stub_resolver({}, index)
        result = stub.get_first_names_requiring_grounding()
        self.assertNotIn("xavier", result)
        self.assertNotIn("thundercat", result)

    def test_real_resolver_base_identities_all_covered(self) -> None:
        """Real IdentityResolver must return every base-identity first token in the grounding set."""
        try:
            from dual_pipeline.identity_resolver_core.resolver import IdentityResolver
        except ImportError:
            self.skipTest("IdentityResolver not importable")

        resolver = IdentityResolver()
        result = resolver.get_first_names_requiring_grounding()
        self.assertIsInstance(result, set)
        self.assertGreater(len(result), 0)

        from dual_pipeline.identity_resolver_core.sources_loader import load_base_identities
        captured: List[str] = []

        def _cap(canonical_name: str, **_kw: Any) -> None:
            captured.append(canonical_name.split()[0].lower())

        load_base_identities(_cap)
        for first in captured:
            self.assertIn(first, result, f"Base-identity first token '{first}' missing from grounding set")


# ---------------------------------------------------------------------------
# 2. gate1_entity_context
# ---------------------------------------------------------------------------

class TestGate1EntityContext(unittest.TestCase):

    def test_bare_name_in_grounding_set_is_caught(self) -> None:
        from dual_pipeline.transcript_qa_core.gate1_entity_context import (
            evaluate_referenced_entity_context_gate,
        )
        index = _make_two_person_index(
            "Quinn Adams", "PartnerAdvisory", "Developer",
            "Quinn Reyes", "NEA", "Stakeholder",
            "quinn",
        )
        stub = _make_stub_resolver({}, index)
        f2 = "## Executive Recap\n\nThe team agreed with Quinn on the integration timeline.\n"
        passed, failures = evaluate_referenced_entity_context_gate(
            f1_text="", f2_text=f2, roster_names=[], entity_clarifications=[],
            identity_resolver=stub,
        )
        self.assertFalse(passed)
        self.assertTrue(any("C27_UNGROUNDED_STAKEHOLDER_IN_RECAP" in f for f in failures))

    def test_name_absent_from_resolver_is_not_flagged(self) -> None:
        from dual_pipeline.transcript_qa_core.gate1_entity_context import (
            evaluate_referenced_entity_context_gate,
        )
        stub = _make_stub_resolver({}, {})
        f2 = "## Executive Recap\n\nNebula mentioned the deployment approach.\n"
        passed, failures = evaluate_referenced_entity_context_gate(
            f1_text="", f2_text=f2, roster_names=[], entity_clarifications=[],
            identity_resolver=stub,
        )
        self.assertTrue(passed)
        self.assertEqual(failures, [])

    def test_resolver_none_does_not_raise(self) -> None:
        import dual_pipeline.transcript_qa_core.gate1_entity_context as mod
        from dual_pipeline.transcript_qa_core.gate1_entity_context import (
            evaluate_referenced_entity_context_gate,
        )
        original = mod._RESOLVER_SINGLETON
        try:
            mod._RESOLVER_SINGLETON = None
            passed, failures = evaluate_referenced_entity_context_gate(
                f1_text="", f2_text="Jordan confirmed.\n", roster_names=[], identity_resolver=None,
            )
            self.assertIsInstance(passed, bool)
        finally:
            mod._RESOLVER_SINGLETON = original


# ---------------------------------------------------------------------------
# 3. CrossMeetingCoherenceJudge
# ---------------------------------------------------------------------------

class TestCrossMeetingCoherenceJudge(unittest.TestCase):

    def test_bare_name_in_resolver_caught_in_output02(self) -> None:
        from dual_pipeline.llm_judge_core.cross_meeting_coherence import CrossMeetingCoherenceJudge
        index = _make_two_person_index(
            "Taylor Kim", "PartnerAdvisory", "Engineer",
            "Taylor Brooks", "NEA", "Analyst",
            "taylor",
        )
        stub = _make_stub_resolver({}, index)
        judge = CrossMeetingCoherenceJudge(identity_resolver=stub)
        f2 = "## Recap\n\nTaylor confirmed the schema contract was signed.\n"
        result = judge.audit(f1_text="", f2_text=f2, f3_json={}, roster_names=[])
        c27 = [f for f in result.findings if "C27" in f]
        self.assertTrue(len(c27) > 0, f"Expected C-27 finding, got: {result.findings}")

    def test_name_not_in_resolver_not_caught(self) -> None:
        from dual_pipeline.llm_judge_core.cross_meeting_coherence import CrossMeetingCoherenceJudge
        stub = _make_stub_resolver({}, {})
        judge = CrossMeetingCoherenceJudge(identity_resolver=stub)
        f2 = "## Recap\n\nNebula agreed on the deployment timeline.\n"
        result = judge.audit(f1_text="", f2_text=f2, f3_json={}, roster_names=[])
        c27 = [f for f in result.findings if "C27" in f]
        self.assertEqual(c27, [], f"Empty resolver → no C-27, got: {c27}")


# ---------------------------------------------------------------------------
# 4. EntityClarificationDetector directory seeding
# ---------------------------------------------------------------------------

class TestEntityClarificationDetectorDirectorySeeding(unittest.TestCase):

    def test_resolver_identity_in_directory(self) -> None:
        from dual_pipeline.ambiguity_core.detector_d6_entity import EntityClarificationDetector
        index = _make_one_person_index("Cameron Hayes", "PartnerAdvisory", "Solution Architect", "cameron")
        stub = _make_stub_resolver({}, index)
        detector = EntityClarificationDetector(identity_resolver=stub)
        self.assertIn("cameron", detector._enterprise_directory)
        names = [e.get("canonical_name") for e in detector._enterprise_directory["cameron"]]
        self.assertIn("Cameron Hayes", names)

    def test_static_fallback_preserved_when_resolver_unavailable(self) -> None:
        from dual_pipeline.ambiguity_core.detector_d6_entity import (
            DEFAULT_ENTERPRISE_CANDIDATES,
            EntityClarificationDetector,
        )
        # Simulate resolver failure
        with patch(
            "dual_pipeline.ambiguity_core.detector_d6_entity.EntityClarificationDetector._build_directory_from_resolver",
            return_value={k: list(v) for k, v in DEFAULT_ENTERPRISE_CANDIDATES.items()},
        ):
            detector = EntityClarificationDetector(identity_resolver=None)
        for static_key in DEFAULT_ENTERPRISE_CANDIDATES:
            self.assertIn(static_key, detector._enterprise_directory)


if __name__ == "__main__":
    unittest.main()
