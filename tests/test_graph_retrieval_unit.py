"""Graph retrieval unit tests — BGE-free isolated coverage.

These tests exercise the deterministic, provider-free internals of
graph_retrieval.py using pure-Python assertions.  They require no
embedding backend and no real sidecar database.
"""
from __future__ import annotations

import unittest

from dual_pipeline.core import digest
from dual_pipeline.graph_retrieval import (
    _EXPANSION_LIMITS,
    _MATERIALITY_BOOST,
    GRAPH_RETRIEVAL_POLICY,
    GRAPH_RETRIEVAL_POLICY_BODY,
    _assertion_is_navigation_only,
    _base_seed_score,
    _better_path,
    _edge_increment,
    _question_tokens,
    bm25_query_tokens,
)


class BM25TokenProjectionTests(unittest.TestCase):
    """Deterministic BM25 token extraction without an embedding backend."""

    def test_stopwords_are_removed(self) -> None:
        tokens = bm25_query_tokens("how are the risks and opportunities")
        self.assertNotIn("how", tokens)
        self.assertNotIn("are", tokens)
        self.assertNotIn("the", tokens)
        self.assertNotIn("and", tokens)

    def test_short_tokens_are_excluded(self) -> None:
        tokens = bm25_query_tokens("a x AI is an important ML tool")
        # "a", "x" are 1 char, excluded
        self.assertNotIn("a", tokens)
        self.assertNotIn("x", tokens)
        # "is", "an" are 2-char stopwords, excluded
        self.assertNotIn("is", tokens)
        self.assertNotIn("an", tokens)
        # "ai", "ml" are 2-char domain acronyms, admitted
        self.assertIn("ai", tokens)
        self.assertIn("ml", tokens)
        self.assertIn("important", tokens)
        self.assertIn("tool", tokens)

    def test_deduplication_preserves_first_occurrence_order(self) -> None:
        tokens = bm25_query_tokens("sherpa sherpa evaluation sherpa")
        self.assertEqual(tokens.count("sherpa"), 1)
        self.assertEqual(tokens.count("evaluation"), 1)
        self.assertEqual(tokens.index("sherpa"), 0)

    def test_caps_at_32_tokens(self) -> None:
        long_query = " ".join(f"word{i}" for i in range(50))
        tokens = bm25_query_tokens(long_query)
        self.assertEqual(len(tokens), 32)

    def test_empty_input_returns_empty_list(self) -> None:
        self.assertEqual([], bm25_query_tokens(""))

    def test_only_stopwords_returns_empty_list(self) -> None:
        self.assertEqual([], bm25_query_tokens("is a an the and"))

    def test_question_tokens_uses_separate_stopword_list(self) -> None:
        question = _question_tokens("what are the Project Beacon risks")
        self.assertNotIn("what", question)
        self.assertNotIn("are", question)
        self.assertNotIn("the", question)
        self.assertIn("digital", question)
        self.assertIn("sherpa", question)
        self.assertIn("risks", question)


class NavigationOnlyFilteringTests(unittest.TestCase):
    """Test _assertion_is_navigation_only without a live graph."""

    def _nodes(self, *types: tuple[str, str]) -> dict[str, dict]:
        return {nid: {"node_type": ntype} for nid, ntype in types}

    def test_routes_to_is_navigation_only(self) -> None:
        nodes = self._nodes(("n1", "workstream"), ("n2", "workstream"))
        assertion = {"predicate": "ROUTES_TO", "legacy_predicate": None,
                      "subject_node_id": "n1", "object_node_id": "n2"}
        self.assertTrue(_assertion_is_navigation_only(assertion, nodes))

    def test_routes_to_card_predicate_alone_is_not_excluded(self) -> None:
        """ROUTES_TO_CARD is a different predicate from ROUTES_TO."""
        nodes = self._nodes(("n1", "workstream"), ("n2", "workstream"))
        assertion = {"predicate": "ROUTES_TO_CARD", "legacy_predicate": None,
                      "subject_node_id": "n1", "object_node_id": "n2"}
        # Neither endpoint is a card, and ROUTES_TO_CARD != ROUTES_TO
        self.assertFalse(_assertion_is_navigation_only(assertion, nodes))

    def test_card_node_type_is_navigation_only(self) -> None:
        nodes = self._nodes(("n1", "workstream"), ("c1", "card"))
        assertion = {"predicate": "BELONGS_TO", "legacy_predicate": None,
                      "subject_node_id": "n1", "object_node_id": "c1"}
        self.assertTrue(_assertion_is_navigation_only(assertion, nodes))

    def test_normal_relationship_is_not_navigation_only(self) -> None:
        nodes = self._nodes(("n1", "workstream"), ("n2", "deliverable"))
        assertion = {"predicate": "DEPENDS_ON", "legacy_predicate": None,
                      "subject_node_id": "n1", "object_node_id": "n2"}
        self.assertFalse(_assertion_is_navigation_only(assertion, nodes))

    def test_legacy_predicate_routes_to_is_navigation_only(self) -> None:
        nodes = self._nodes(("n1", "workstream"), ("n2", "workstream"))
        assertion = {"predicate": "SOMETHING", "legacy_predicate": "ROUTES_TO",
                      "subject_node_id": "n1", "object_node_id": "n2"}
        self.assertTrue(_assertion_is_navigation_only(assertion, nodes))


class PathScoringTests(unittest.TestCase):
    """Verify deterministic path and topology scoring arithmetic."""

    def _edge(self, **overrides: object) -> dict:
        base = {
            "relationship_family": "DEPENDS_ON",
            "edge_type": "DEPENDS_ON",
            "evidence_ids": [],
            "materiality": "medium",
        }
        base.update(overrides)
        return base

    def test_requested_relationship_boost(self) -> None:
        edge = self._edge(relationship_family="DEPENDS_ON")
        boosted, _ = _edge_increment(
            edge, requested_relationships={"DEPENDS_ON"}, destination_degree=5, hop=1,
        )
        normal, _ = _edge_increment(
            edge, requested_relationships={"UNRELATED"}, destination_degree=5, hop=1,
        )
        self.assertGreater(boosted, normal)

    def test_hub_penalty_increases_above_threshold(self) -> None:
        edge = self._edge()
        _, topo_low = _edge_increment(
            edge, requested_relationships=set(), destination_degree=5, hop=1,
        )
        _, topo_high = _edge_increment(
            edge, requested_relationships=set(), destination_degree=50, hop=1,
        )
        self.assertGreater(topo_low, topo_high)

    def test_length_penalty_increases_per_hop(self) -> None:
        edge = self._edge()
        score_hop1, _ = _edge_increment(
            edge, requested_relationships=set(), destination_degree=5, hop=1,
        )
        score_hop3, _ = _edge_increment(
            edge, requested_relationships=set(), destination_degree=5, hop=3,
        )
        self.assertGreater(score_hop1, score_hop3)

    def test_evidence_boost_is_bounded(self) -> None:
        edge_no_evidence = self._edge(evidence_ids=[])
        edge_many = self._edge(evidence_ids=[f"e{i}" for i in range(20)])
        score_none, _ = _edge_increment(
            edge_no_evidence, requested_relationships=set(), destination_degree=5, hop=1,
        )
        score_many, _ = _edge_increment(
            edge_many, requested_relationships=set(), destination_degree=5, hop=1,
        )
        self.assertGreater(score_many, score_none)
        # Evidence boost is capped at 0.06
        self.assertLessEqual(score_many - score_none, 0.061)

    def test_materiality_boost_is_monotonically_ordered(self) -> None:
        levels = ["critical", "high", "medium-high", "medium", "low"]
        boosts = [_MATERIALITY_BOOST[level] for level in levels]
        self.assertEqual(boosts, sorted(boosts, reverse=True))

    def test_base_seed_score_uses_reciprocal_rank(self) -> None:
        result_rank_1 = {"fused_rank": 1}
        result_rank_10 = {"fused_rank": 10}
        result_no_rank = {}
        self.assertGreater(
            _base_seed_score(result_rank_1), _base_seed_score(result_rank_10)
        )
        self.assertEqual(0.1, _base_seed_score(result_no_rank))


class BetterPathTests(unittest.TestCase):
    """Verify path comparison is deterministic and score-first."""

    def _path(self, score: float, hops: int, path_id: str) -> dict:
        return {"path_score": score, "hop_count": hops, "path_id": path_id}

    def test_higher_score_wins(self) -> None:
        self.assertTrue(_better_path(self._path(0.9, 2, "b"), self._path(0.5, 2, "a")))

    def test_fewer_hops_wins_on_equal_score(self) -> None:
        self.assertTrue(_better_path(self._path(0.5, 1, "b"), self._path(0.5, 3, "a")))

    def test_none_current_always_replaced(self) -> None:
        self.assertTrue(_better_path(self._path(0.1, 5, "x"), None))

    def test_deterministic_tiebreak_on_path_id(self) -> None:
        self.assertTrue(_better_path(self._path(0.5, 2, "aaa"), self._path(0.5, 2, "zzz")))


class ExpansionLimitTests(unittest.TestCase):
    """Verify _EXPANSION_LIMITS structure and invariants."""

    def test_all_query_types_have_expansion_limits(self) -> None:
        expected_types = {
            "factual", "guidance", "strategic",
            "candidate_review", "candidate_evaluation", "candidate_guidance",
        }
        self.assertEqual(expected_types, set(_EXPANSION_LIMITS.keys()))

    def test_factual_queries_block_candidate_and_external(self) -> None:
        limits = _EXPANSION_LIMITS["factual"]
        self.assertEqual(0, limits["candidate"])
        self.assertEqual(0, limits["external_public"])
        self.assertGreater(limits["approved"], 0)

    def test_candidate_evaluation_blocks_approved(self) -> None:
        limits = _EXPANSION_LIMITS["candidate_evaluation"]
        self.assertEqual(0, limits["approved"])
        self.assertGreater(limits["candidate"], 0)

    def test_strategic_allows_all_lanes(self) -> None:
        limits = _EXPANSION_LIMITS["strategic"]
        self.assertGreater(limits["approved"], 0)
        self.assertGreater(limits["candidate"], 0)
        self.assertGreater(limits["external_public"], 0)


class PolicyConstantTests(unittest.TestCase):
    """Verify graph retrieval policy constants are well-formed."""

    def test_policy_digest_is_deterministic(self) -> None:
        expected = digest(GRAPH_RETRIEVAL_POLICY_BODY)
        self.assertEqual(expected, GRAPH_RETRIEVAL_POLICY["policy_digest"])

    def test_maximum_hops_is_three(self) -> None:
        self.assertEqual(3, GRAPH_RETRIEVAL_POLICY_BODY["maximum_hops"])

    def test_excluded_relationship_families_contains_routes_to(self) -> None:
        self.assertIn(
            "ROUTES_TO",
            GRAPH_RETRIEVAL_POLICY_BODY["excluded_relationship_families"],
        )

    def test_excluded_node_types_contains_card(self) -> None:
        self.assertIn("card", GRAPH_RETRIEVAL_POLICY_BODY["excluded_node_types"])

    def test_terminal_node_types_contains_source(self) -> None:
        self.assertIn("source", GRAPH_RETRIEVAL_POLICY_BODY["terminal_node_types"])

    def test_seed_record_limit_is_bounded(self) -> None:
        self.assertGreater(GRAPH_RETRIEVAL_POLICY_BODY["maximum_seed_records"], 0)
        self.assertLessEqual(GRAPH_RETRIEVAL_POLICY_BODY["maximum_seed_records"], 50)

    def test_hub_degree_threshold_is_positive(self) -> None:
        self.assertGreater(GRAPH_RETRIEVAL_POLICY_BODY["hub_degree_threshold"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
