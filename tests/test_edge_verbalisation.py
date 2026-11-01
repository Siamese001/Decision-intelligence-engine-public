"""Unit tests for registry-predicate verbalisation and predicate typing (Wave 4)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from dual_pipeline.index_core.edge_verbalisation import (
    PREDICATE_VERBALISATIONS,
    REGISTRY_PREDICATES,
    REVERSE_PREDICATE_PHRASES,
    canonicalize_predicate,
    compute_near_duplicate_share,
    render_edge_lexical_fields,
    render_edge_text_v0,
    render_edge_text_v1,
    render_edge_text_v2,
    render_edge_text_v3,
    verify_no_classification_widening,
)
from dual_pipeline.index_core.predicate_typer import (
    evaluate_typer_accuracy,
    type_query_predicate,
)


class TestEdgeVerbalisationUnit(unittest.TestCase):
    """Unit tests for edge verbalisation variants and lexical representations."""

    def test_registry_predicates_count(self) -> None:
        """Verify all 18 canonical predicates are registered."""
        self.assertEqual(len(REGISTRY_PREDICATES), 18)
        self.assertEqual(len(PREDICATE_VERBALISATIONS), 18)
        self.assertEqual(len(REVERSE_PREDICATE_PHRASES), 18)
        for pred in REGISTRY_PREDICATES:
            self.assertIn(pred, PREDICATE_VERBALISATIONS)
            self.assertIn(pred, REVERSE_PREDICATE_PHRASES)

    def test_canonicalize_predicate(self) -> None:
        """Verify canonicalization of registry, legacy, and unknown predicates."""
        self.assertEqual(canonicalize_predicate("REPORTS_TO"), "REPORTS_TO")
        self.assertEqual(canonicalize_predicate("reports_to"), "REPORTS_TO")
        self.assertEqual(canonicalize_predicate("SOURCED_FROM"), "SUPPORTED_BY")
        self.assertEqual(canonicalize_predicate("LEAD_ON"), "OWNS_OR_LEADS")
        self.assertEqual(canonicalize_predicate("CARD_TRACKING"), "ROUTES_TO")
        self.assertEqual(canonicalize_predicate("UNKNOWN_FOO_BAR"), "RELATED_TO")
        self.assertEqual(canonicalize_predicate(None), "RELATED_TO")

    def test_render_edge_text_v0(self) -> None:
        """Verify V0 legacy baseline rendering."""
        res = render_edge_text_v0("Alice", "REPORTS_TO", "Bob", "HR dept")
        self.assertEqual(res, "Alice REPORTS_TO Bob. HR dept.")
        res_no_scope = render_edge_text_v0("Alice", "REPORTS_TO", "Bob")
        self.assertEqual(res_no_scope, "Alice REPORTS_TO Bob.")

    def test_render_edge_text_v1(self) -> None:
        """Verify V1 active-voice verbalisation."""
        res = render_edge_text_v1("Alice Smith", "REPORTS_TO", "Bob Jones")
        self.assertEqual(res, "Alice Smith reports to Bob Jones.")
        res_legacy = render_edge_text_v1("Alice Smith", "SOURCED_FROM", "HR Document")
        self.assertEqual(res_legacy, "Alice Smith is supported by source HR Document.")

    def test_render_edge_text_v2(self) -> None:
        """Verify V2 rendering with entity types and quote."""
        res = render_edge_text_v2(
            "Alice Smith",
            "person",
            "OWNS_OR_LEADS",
            "Project Nexus",
            "project",
            "Alice leads Project Nexus delivery.",
        )
        expected = (
            "Alice Smith owns or leads Project Nexus. "
            "Subject type: person. Object type: project. "
            "Evidence: Alice leads Project Nexus delivery."
        )
        self.assertEqual(res, expected)

    def test_render_edge_text_v3(self) -> None:
        """Verify V3 distinguishing quote rendering."""
        long_q = "Alice Smith was confirmed as the delivery lead in the project charter."
        res_dist = render_edge_text_v3(
            "Alice Smith", "OWNS_OR_LEADS", "Project Nexus", long_q, is_distinguishing=True
        )
        self.assertIn("Evidence:", res_dist)

        res_not_dist = render_edge_text_v3(
            "Alice Smith", "OWNS_OR_LEADS", "Project Nexus", long_q, is_distinguishing=False
        )
        self.assertNotIn("Evidence:", res_not_dist)
        self.assertEqual(res_not_dist, "Alice Smith owns or leads Project Nexus.")

        short_q = "Short quote"
        res_short = render_edge_text_v3(
            "Alice Smith", "OWNS_OR_LEADS", "Project Nexus", short_q, is_distinguishing=True
        )
        self.assertNotIn("Evidence:", res_short)

    def test_render_edge_lexical_fields(self) -> None:
        """Verify lexical fields generation for hybrid BM25 index."""
        fields = render_edge_lexical_fields(
            "Alice Smith", "REPORTS_TO", "Bob Jones", "Quote text", "Org scope"
        )
        self.assertEqual(fields["subject"], "Alice Smith")
        self.assertEqual(fields["predicate"], "REPORTS_TO")
        self.assertEqual(fields["predicate_phrase"], "reports to")
        self.assertEqual(fields["reverse_predicate_phrase"], "reporting manager of")
        self.assertEqual(fields["object"], "Bob Jones")
        self.assertEqual(fields["verbalised_forward"], "Alice Smith reports to Bob Jones.")
        self.assertEqual(fields["verbalised_reverse"], "Bob Jones reporting manager of Alice Smith.")
        self.assertIn("reports to", fields["lexical_body"])
        self.assertIn("reporting manager of", fields["lexical_body"])

    def test_compute_near_duplicate_share(self) -> None:
        """Verify near-duplicate share calculation."""
        texts = [
            "Alice Smith reports to Bob Jones.",
            "Alice Smith reports to Bob Jones.",
            "Charlie Brown owns or leads Project Apollo.",
            "David Miller depends on Platform Core.",
        ]
        share, dups, total = compute_near_duplicate_share(texts, similarity_threshold=0.95)
        self.assertEqual(total, 4)
        self.assertEqual(dups, 2)
        self.assertEqual(share, 0.5)

    def test_verify_no_classification_widening(self) -> None:
        """Verify classification widening invariant check."""
        self.assertTrue(verify_no_classification_widening("internal", "internal"))
        self.assertTrue(verify_no_classification_widening("confidential", "internal"))
        self.assertTrue(verify_no_classification_widening("restricted", "public"))
        self.assertTrue(verify_no_classification_widening("internal", "public"))
        # Edge cannot be public if source is internal or confidential
        self.assertFalse(verify_no_classification_widening("public", "internal"))
        self.assertFalse(verify_no_classification_widening("public", "confidential"))
        self.assertFalse(verify_no_classification_widening("internal", "restricted"))


class TestPredicateTyperUnit(unittest.TestCase):
    """Unit tests for closed-registry query predicate typer."""

    def test_exact_phrase_matching(self) -> None:
        """Verify exact canonical phrases type with 1.0 confidence."""
        res = type_query_predicate("Who reports to Bob Jones?")
        self.assertEqual(res.predicate, "REPORTS_TO")
        self.assertEqual(res.confidence, 1.0)
        self.assertTrue(res.is_registry_predicate)

    def test_reverse_phrase_matching(self) -> None:
        """Verify reverse phrases type with high confidence."""
        res = type_query_predicate("Who is the reporting manager of Alice Smith?")
        self.assertEqual(res.predicate, "REPORTS_TO")
        self.assertGreaterEqual(res.confidence, 0.98)

    def test_pattern_matching(self) -> None:
        """Verify domain patterns and paraphrases."""
        res_lead = type_query_predicate("Who is the delivery leader for Nexus?")
        self.assertEqual(res_lead.predicate, "OWNS_OR_LEADS")

        res_dep = type_query_predicate("What infrastructure dependency is required before launch?")
        self.assertEqual(res_dep.predicate, "DEPENDS_ON")

        res_route = type_query_predicate("What tracking milestone is linked to this deliverable?")
        self.assertEqual(res_route.predicate, "ROUTES_TO")

    def test_qrels_relationship_benchmark(self) -> None:
        """Verify typer achieves >=90% accuracy on Wave 2 QRELS relationship slice."""
        qrels_path = Path("reports/edge_qrels_dataset.json")
        if not qrels_path.exists():
            self.skipTest("reports/edge_qrels_dataset.json not found")

        with open(qrels_path, encoding="utf-8") as f:
            data = json.load(f)

        rel_cases = [
            c for c in data.get("edge_cases", [])
            if c.get("query_form") in ("forward", "reverse")
        ] + data.get("name_free_cases", [])
        self.assertGreater(len(rel_cases), 100)
        eval_res = evaluate_typer_accuracy(rel_cases)
        self.assertGreaterEqual(eval_res["accuracy"], 0.90)
        self.assertTrue(eval_res["passed_floor_90"])


if __name__ == "__main__":
    unittest.main()
