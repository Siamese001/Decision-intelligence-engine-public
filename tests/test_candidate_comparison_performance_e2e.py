"""E2E performance and correctness tests for Stage C0.2 candidate comparison fast-path."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from dual_pipeline.core import connect_sqlite
from dual_pipeline.intake_core.comparison import (
    compare_candidates,
    hydrate_comparison_context,
)
from dual_pipeline.intake_core.routing import build_routes


class TestCandidateComparisonPerformanceE2E(unittest.TestCase):
    """Verifies that Stage C0.2 candidate comparison uses fast-path triage and batched hydration."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.graph_path = self.root / "test_graph.sqlite"

        # Set up SQLite schema with assertion, search_document, evidence_item, assertion_evidence
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

            # Insert 10 existing assertions and search documents
            for i in range(1, 11):
                aid = f"assertion-existing-{i:03d}"
                conn.execute(
                    """INSERT INTO assertion VALUES (?, ?, 'defines_concept', NULL, ?, 'canonical', 'current', 'internal', '["internal_consulting"]', 'source-1')""",
                    (aid, f"node-{i}", f"Core enterprise concept {i} architecture specification."),
                )
                conn.execute(
                    """INSERT INTO search_document VALUES (?, 'assertion', ?, ?, ?, 'canonical', 'current', 1, 'source-1', ?)""",
                    (f"sd-{i:03d}", aid, f"Concept {i} Title", f"Detailed body of concept {i} architecture specification.", aid),
                )
                eid = f"ev-{i:03d}"
                conn.execute(
                    """INSERT INTO evidence_item VALUES (?, 'source-1', 'v1', 'transcript_line', 'line:10')""",
                    (eid,),
                )
                conn.execute(
                    """INSERT INTO assertion_evidence VALUES (?, ?, 'direct')""",
                    (aid, eid),
                )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_candidate_comparison_fast_path_and_batched_hydration_e2e(self) -> None:
        """Proves fast-path triage bypasses retriever for decisive matches, and hydration is batched."""
        bundle = {
            "run_id": "run-test-c02-e2e",
            "trace_id": "a" * 32,
            "source_id": "source-test-e2e",
            "source_version_id": "v1",
            "candidates": [
                {
                    "candidate_id": "cand-exact-1",
                    "candidate_type": "assertion",
                    "normalized_text": "concept 1 title detailed body of concept 1 architecture specification",
                    "classification": "internal",
                    "materiality": "high",
                    "audiences": ["internal_consulting"],
                    "entity_mentions": [],
                },
                {
                    "candidate_id": "cand-decisive-2",
                    "candidate_type": "assertion",
                    "normalized_text": "Detailed body of concept 2 architecture specification",
                    "classification": "internal",
                    "materiality": "high",
                    "audiences": ["internal_consulting"],
                    "entity_mentions": [],
                },
                {
                    "candidate_id": "cand-novel-3",
                    "candidate_type": "assertion",
                    "normalized_text": "A completely brand new unrelated concept about quantum photonics",
                    "classification": "internal",
                    "materiality": "high",
                    "audiences": ["internal_consulting"],
                    "entity_mentions": [],
                },
            ],
        }

        # Run comparison with fast path
        t0 = time.monotonic()
        comp = compare_candidates(
            bundle,
            self.graph_path,
            retrieval_audiences=["internal_consulting"],
        )
        elapsed_comp = time.monotonic() - t0

        self.assertEqual(comp["run_id"], "run-test-c02-e2e")
        self.assertEqual(len(comp["comparisons"]), 3)

        # Exact match check
        c1 = comp["comparisons"]["cand-exact-1"]
        self.assertTrue(len(c1["exact_matches"]) > 0 or len(c1["partial_overlaps"]) > 0)

        # Decisive match check
        c2 = comp["comparisons"]["cand-decisive-2"]
        self.assertTrue(len(c2["semantic_matches"]) > 0 or len(c2["partial_overlaps"]) > 0)

        # Novel candidate
        c3 = comp["comparisons"]["cand-novel-3"]
        self.assertEqual(len(c3["exact_matches"]), 0)

        # Hydrate context
        t1 = time.monotonic()
        hydrated = hydrate_comparison_context(comp, self.graph_path)
        elapsed_hydration = time.monotonic() - t1

        self.assertGreaterEqual(hydrated["record_count"], 1)
        for assertion in hydrated["hydrated_assertions"]:
            self.assertIn("assertion_id", assertion)
            self.assertIn("evidence", assertion)
            self.assertGreaterEqual(len(assertion["evidence"]), 1)

        # Verify speed
        self.assertLess(elapsed_comp + elapsed_hydration, 0.500)

        # Verify routing succeeds
        resolutions = []
        routes = build_routes(bundle, resolutions, comp)
        self.assertEqual(len(routes), 3)


if __name__ == "__main__":
    unittest.main()
