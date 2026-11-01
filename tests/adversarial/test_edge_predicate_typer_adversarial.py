"""Adversarial and boundary test suite for edge verbalisation and predicate typing (Wave 4)."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from dual_pipeline.index_core.edge_verbalisation import (
    canonicalize_predicate,
    compute_near_duplicate_share,
    render_edge_lexical_fields,
    render_edge_text_v1,
    render_edge_text_v2,
    render_edge_text_v3,
    verify_no_classification_widening,
)
from dual_pipeline.index_core.predicate_typer import type_query_predicate


class TestEdgePredicateTyperAdversarial(unittest.TestCase):
    """Adversarial stress and security boundaries for predicate typing and verbalisation."""

    def test_malformed_and_boundary_queries(self) -> None:
        """Verify typer handles malformed, empty, None, and whitespace inputs gracefully."""
        cases = [
            "",
            "   ",
            "\t\n\r   \n",
            None,
            "?",
            "!!!",
            "...",
            "12345",
            "a",
            "   who   ",
        ]
        for q in cases:
            res = type_query_predicate(q)  # type: ignore[arg-type]
            self.assertIsNone(res.predicate)
            self.assertEqual(res.confidence, 0.0)
            self.assertFalse(res.is_registry_predicate)

    def test_overflow_and_giant_queries(self) -> None:
        """Verify typer handles massive strings (50,000 characters) without catastrophic backtracking."""
        giant_query = "Who reports to " + ("manager " * 5000) + "Bob Jones?"
        res = type_query_predicate(giant_query)
        self.assertEqual(res.predicate, "REPORTS_TO")
        self.assertGreaterEqual(res.confidence, 0.60)

        giant_noise = "irrelevant token " * 10000
        res_noise = type_query_predicate(giant_noise)
        self.assertIsNone(res_noise.predicate)
        self.assertEqual(res_noise.confidence, 0.0)

    def test_regex_injection_and_metacharacters(self) -> None:
        """Verify queries with regex metacharacters, unbalanced brackets, and wildcards do not crash."""
        pathological = [
            "Who reports to (.*)+ [a-z]* {1,100} \\d+ $ ^ | Bob?",
            "What is [affiliated with] (org) {dept}? *+?^$()[]{}",
            "((((((((nested unclosed parentheses who leads",
            "\\\\\\\\\\\\\\ regex escapes \\/\\/\\/",
            "SELECT * FROM assertion WHERE predicate = 'REPORTS_TO'; DROP TABLE node;--",
            "<script>alert('xss')</script> reports to admin",
        ]
        for q in pathological:
            res = type_query_predicate(q)
            self.assertIsInstance(res.confidence, float)
            self.assertIn(res.is_registry_predicate, (True, False))

    def test_unicode_and_emojis(self) -> None:
        """Verify multilingual unicode, zero-width spaces, and emojis do not cause crash."""
        cases = [
            "Who reports to \u200b\u200c\u200d Bob Jones?",
            "🏢 Who owns or leads Project Nexus? 🚀",
            "Hierarchical reporting: Alice -> Bob \u2192 Charlie",
            "Quién informa a Roberto?",
            "Who governs the \u0000 null byte stream?",
        ]
        for q in cases:
            res = type_query_predicate(q)
            self.assertIsInstance(res.confidence, float)

    def test_competing_predicate_tokens_deterministic(self) -> None:
        """Verify queries containing multiple competing predicate phrases resolve deterministically."""
        competing_query = "Who reports to Bob and also owns or leads Project Nexus?"
        res1 = type_query_predicate(competing_query)
        res2 = type_query_predicate(competing_query)
        self.assertEqual(res1.predicate, res2.predicate)
        self.assertEqual(res1.confidence, res2.confidence)
        self.assertTrue(res1.is_registry_predicate)

    def test_classification_widening_adversarial_tampering(self) -> None:
        """Verify classification widening invariant strictly fails on unauthorized exposure."""
        # Restricted source leaked into public or internal edge
        self.assertFalse(
            verify_no_classification_widening(edge_classification="public", source_classification="restricted")
        )
        self.assertFalse(
            verify_no_classification_widening(edge_classification="internal", source_classification="restricted")
        )
        self.assertFalse(
            verify_no_classification_widening(edge_classification="confidential", source_classification="restricted")
        )
        self.assertFalse(
            verify_no_classification_widening(edge_classification="public", source_classification="confidential")
        )

        # Legitimate equal or higher-classification bounds pass
        self.assertTrue(
            verify_no_classification_widening(edge_classification="restricted", source_classification="restricted")
        )
        self.assertTrue(
            verify_no_classification_widening(edge_classification="restricted", source_classification="public")
        )
        self.assertTrue(
            verify_no_classification_widening(edge_classification="internal", source_classification="public")
        )

    def test_provider_free_boundary(self) -> None:
        """Verify verbalisation and typer make ZERO socket or HTTP provider calls."""
        with patch("socket.socket") as mock_socket:
            mock_socket.side_effect = RuntimeError("Illegal provider or network call detected!")

            # Run verbalisations
            v1 = render_edge_text_v1("Alice", "REPORTS_TO", "Bob")
            self.assertIn("reports to", v1)
            v2 = render_edge_text_v2("Alice", "person", "OWNS_OR_LEADS", "Nexus", "project", "Quote")
            self.assertIn("Evidence:", v2)
            v3 = render_edge_text_v3("Alice", "DELIVERS", "Feature", "A" * 50, is_distinguishing=True)
            self.assertIn("Evidence:", v3)

            # Run lexical fields
            fields = render_edge_lexical_fields("Alice", "REPORTS_TO", "Bob")
            self.assertIn("reports to", fields["lexical_body"])

            # Run typer
            res = type_query_predicate("Who owns or leads Project Nexus?")
            self.assertEqual(res.predicate, "OWNS_OR_LEADS")

            # Canonicalize
            canon = canonicalize_predicate("SPONSOR_LEAD")
            self.assertEqual(canon, "OWNS_OR_LEADS")

    def test_near_duplicate_boundary_cases(self) -> None:
        """Verify near duplicate calculation on empty, single, and identical sets."""
        share, dups, total = compute_near_duplicate_share([])
        self.assertEqual((share, dups, total), (0.0, 0, 0))

        share, dups, total = compute_near_duplicate_share(["Only one sentence in corpus."])
        self.assertEqual((share, dups, total), (0.0, 0, 1))

        identical = ["Identical sentence across all records."] * 10
        share, dups, total = compute_near_duplicate_share(identical, similarity_threshold=0.95)
        self.assertEqual(share, 1.0)
        self.assertEqual(dups, 10)


if __name__ == "__main__":
    unittest.main()
