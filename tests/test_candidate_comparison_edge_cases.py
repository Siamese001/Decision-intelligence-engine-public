"""Adversarial and edge-case unit tests for candidate comparison and context hydration."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import connect_sqlite
from dual_pipeline.intake_core.comparison import (
    compare_candidates,
    hydrate_comparison_context,
)


class TestCandidateComparisonEdgeCases(unittest.TestCase):
    """Adversarial edge-case tests for Stage C0.2 candidate comparison."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.graph_path = self.root / "edge_graph.sqlite"

        conn = connect_sqlite(self.graph_path)
        try:
            conn.executescript(
                """
                CREATE TABLE assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object_node_id TEXT,
                    object_literal TEXT,
                    canonical_status TEXT NOT NULL,
                    currentness_state TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    allowed_audiences_json TEXT NOT NULL,
                    primary_source_id TEXT
                );
                CREATE TABLE search_document (
                    search_document_id TEXT PRIMARY KEY,
                    object_type TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    canonical_status TEXT NOT NULL,
                    currentness_state TEXT NOT NULL,
                    retrieval_eligible INTEGER NOT NULL,
                    source_id TEXT,
                    assertion_id TEXT NOT NULL REFERENCES assertion(assertion_id)
                );
                CREATE TABLE evidence_item (
                    evidence_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    source_version_id TEXT NOT NULL,
                    locator_kind TEXT NOT NULL,
                    locator_value TEXT NOT NULL
                );
                CREATE TABLE assertion_evidence (
                    assertion_id TEXT NOT NULL REFERENCES assertion(assertion_id),
                    evidence_id TEXT NOT NULL REFERENCES evidence_item(evidence_id),
                    evidence_relationship TEXT NOT NULL,
                    PRIMARY KEY (assertion_id, evidence_id)
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_case_1_empty_candidate_list(self) -> None:
        """Proves empty candidate bundle returns empty comparisons without failure."""
        bundle = {
            "run_id": "run-empty",
            "source_id": "source-empty",
            "source_version_id": "v1",
            "candidates": [],
        }
        comp = compare_candidates(bundle, self.graph_path)
        self.assertEqual(comp["run_id"], "run-empty")
        self.assertEqual(len(comp["comparisons"]), 0)

        hydrated = hydrate_comparison_context(comp, self.graph_path)
        self.assertEqual(hydrated["record_count"], 0)
        self.assertEqual(hydrated["hydrated_assertions"], [])

    def test_case_2_empty_search_documents_in_graph(self) -> None:
        """Proves comparison against an empty knowledge graph handles candidate gracefully."""
        bundle = {
            "run_id": "run-empty-graph",
            "source_id": "source-1",
            "source_version_id": "v1",
            "candidates": [
                {
                    "candidate_id": "cand-1",
                    "candidate_type": "assertion",
                    "normalized_text": "Sample assertion text",
                    "classification": "internal",
                    "audiences": ["internal_consulting"],
                    "entity_mentions": [],
                }
            ],
        }
        comp = compare_candidates(bundle, self.graph_path)
        c1 = comp["comparisons"]["cand-1"]
        self.assertEqual(len(c1["exact_matches"]), 0)
        self.assertEqual(len(c1["partial_overlaps"]), 0)
        self.assertEqual(len(c1["semantic_matches"]), 0)

    def test_case_3_unranked_or_malformed_classification(self) -> None:
        """Proves candidates with unknown classification produce empty admitted list safely."""
        bundle = {
            "run_id": "run-bad-class",
            "source_id": "source-1",
            "source_version_id": "v1",
            "candidates": [
                {
                    "candidate_id": "cand-invalid-class",
                    "candidate_type": "assertion",
                    "normalized_text": "Classified text",
                    "classification": "TOP_SECRET_UNKNOWN",
                    "audiences": ["internal_consulting"],
                    "entity_mentions": [],
                }
            ],
        }
        comp = compare_candidates(bundle, self.graph_path)
        c1 = comp["comparisons"]["cand-invalid-class"]
        self.assertEqual(len(c1["exact_matches"]), 0)
        self.assertEqual(len(c1["partial_overlaps"]), 0)

    def test_case_4_hydrate_context_with_empty_or_nonexistent_ids(self) -> None:
        """Proves hydrate_comparison_context handles missing assertion references gracefully."""
        comparison_stub = {
            "run_id": "run-stub",
            "graph_sha256": "fake_sha",
            "comparisons": {
                "cand-1": {
                    "exact_matches": [{"record_id": "nonexistent-doc-1"}],
                    "semantic_matches": [{"record_id": "nonexistent-doc-2"}],
                }
            },
        }
        hydrated = hydrate_comparison_context(comparison_stub, self.graph_path)
        self.assertEqual(hydrated["record_count"], 0)
        self.assertEqual(hydrated["hydrated_assertions"], [])


if __name__ == "__main__":
    unittest.main()
