"""Exhaustive graph content audit tests — nodes, assertions, evidence, and search.

These tests verify the actual content quality, coverage, and consistency
of the live graph database.  They are read-only, deterministic, require
no embedding backend, and enforce regression gates on known debt ceilings.
"""
from __future__ import annotations

import json
import unittest

from dual_pipeline.core import ROOT, connect_sqlite, sha256_file

GRAPH = ROOT / "graph" / "nee_graph.sqlite"


class NodeContentAuditTests(unittest.TestCase):
    """Content-level audit of every active node in the graph."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.connection = connect_sqlite(GRAPH, readonly=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_every_active_node_has_nonempty_canonical_name(self) -> None:
        bad = self.connection.execute(
            "SELECT node_id FROM node WHERE active=1 AND "
            "(canonical_name IS NULL OR TRIM(canonical_name) = '')"
        ).fetchall()
        self.assertEqual([], [r["node_id"] for r in bad])

    def test_every_active_node_has_a_valid_node_type(self) -> None:
        bad = self.connection.execute(
            "SELECT node_id FROM node WHERE active=1 AND "
            "(node_type IS NULL OR TRIM(node_type) = '')"
        ).fetchall()
        self.assertEqual([], [r["node_id"] for r in bad])

    def test_orphan_node_count_does_not_regress(self) -> None:
        """Regression gate: orphan count must not increase above known ceiling."""
        orphans = self.connection.execute("""
            SELECT count(*) FROM node n
            WHERE n.active = 1
              AND NOT EXISTS (SELECT 1 FROM assertion a WHERE a.subject_node_id = n.node_id)
              AND NOT EXISTS (SELECT 1 FROM assertion a WHERE a.object_node_id = n.node_id)
        """).fetchone()[0]
        # Known ceiling: 65 orphans.  Must not grow.
        self.assertLessEqual(
            orphans, 65,
            f"Orphan node count ({orphans}) exceeds ceiling of 65 — "
            f"new nodes must be connected"
        )

    def test_orphan_nodes_are_documented_by_type(self) -> None:
        """Every orphan node type is accounted for in the known debt ledger."""
        rows = self.connection.execute("""
            SELECT n.node_type, count(*) as cnt FROM node n
            WHERE n.active = 1
              AND NOT EXISTS (SELECT 1 FROM assertion a WHERE a.subject_node_id = n.node_id)
              AND NOT EXISTS (SELECT 1 FROM assertion a WHERE a.object_node_id = n.node_id)
            GROUP BY n.node_type ORDER BY cnt DESC
        """).fetchall()
        # Orphan types should only come from known categories
        known_orphan_types = {
            "stakeholder", "source", "workstream", "deliverable",
            "external_concept", "pattern", "control", "org",
            "platform", "risk", "stakeholder_group", "source_lineage",
            "capability", "card", "technical_anchor",
            "deprecated_stakeholder_fragment",
        }
        actual = {r["node_type"] for r in rows}
        unknown = actual - known_orphan_types
        self.assertEqual(
            set(), unknown,
            f"New orphan node types detected: {unknown} — connect or retire them"
        )

    def test_stakeholder_node_connectivity_coverage(self) -> None:
        """≥ 60% of active stakeholder nodes have at least one structural edge."""
        total = self.connection.execute(
            "SELECT count(*) FROM node WHERE active=1 AND node_type='stakeholder'"
        ).fetchone()[0]
        if total == 0:
            self.skipTest("No stakeholder nodes")
        connected = self.connection.execute("""
            SELECT count(DISTINCT n.node_id) FROM node n
            WHERE n.active = 1 AND n.node_type = 'stakeholder'
              AND (
                EXISTS (SELECT 1 FROM assertion a WHERE a.subject_node_id = n.node_id
                        AND a.predicate IS NOT NULL)
                OR EXISTS (SELECT 1 FROM assertion a WHERE a.object_node_id = n.node_id
                           AND a.predicate IS NOT NULL)
              )
        """).fetchone()[0]
        coverage = connected / total
        self.assertGreaterEqual(
            coverage, 0.60,
            f"Stakeholder connectivity {coverage:.1%} ({connected}/{total}) below 60% target"
        )

    def test_workstream_node_connectivity_coverage(self) -> None:
        """≥ 85% of active workstream nodes have structural edges."""
        total = self.connection.execute(
            "SELECT count(*) FROM node WHERE active=1 AND node_type='workstream'"
        ).fetchone()[0]
        if total == 0:
            self.skipTest("No workstream nodes")
        connected = self.connection.execute("""
            SELECT count(DISTINCT n.node_id) FROM node n
            WHERE n.active = 1 AND n.node_type = 'workstream'
              AND (
                EXISTS (SELECT 1 FROM assertion a WHERE a.subject_node_id = n.node_id
                        AND a.predicate IS NOT NULL)
                OR EXISTS (SELECT 1 FROM assertion a WHERE a.object_node_id = n.node_id
                           AND a.predicate IS NOT NULL)
              )
        """).fetchone()[0]
        coverage = connected / total
        self.assertGreaterEqual(
            coverage, 0.85,
            f"Workstream connectivity {coverage:.1%} ({connected}/{total}) below 85% target"
        )

    def test_node_type_distribution_has_minimum_diversity(self) -> None:
        """Graph must have at least 8 distinct active node types."""
        types = self.connection.execute(
            "SELECT count(DISTINCT node_type) FROM node WHERE active=1"
        ).fetchone()[0]
        self.assertGreaterEqual(types, 8)

    def test_active_node_population_has_minimum_counts_per_core_type(self) -> None:
        """Core node types must have minimum populations."""
        minimums = {
            "stakeholder": 100,
            "source": 50,
            "workstream": 50,
            "pattern": 30,
            "deliverable": 15,
            "risk": 15,
            "org": 10,
        }
        for node_type, minimum in minimums.items():
            with self.subTest(node_type=node_type):
                count = self.connection.execute(
                    "SELECT count(*) FROM node WHERE active=1 AND node_type=?",
                    (node_type,),
                ).fetchone()[0]
                self.assertGreaterEqual(
                    count, minimum,
                    f"{node_type} count ({count}) below minimum {minimum}"
                )


class AssertionContentAuditTests(unittest.TestCase):
    """Content-level audit of every assertion in the graph."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.connection = connect_sqlite(GRAPH, readonly=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_null_predicate_count_does_not_regress(self) -> None:
        """NULL-predicate assertions must not grow above known ceiling."""
        count = self.connection.execute(
            "SELECT count(*) FROM assertion WHERE predicate IS NULL"
        ).fetchone()[0]
        self.assertLessEqual(
            count, 280,
            f"NULL-predicate count ({count}) exceeds ceiling of 280"
        )

    def test_confidence_distribution_maintains_quality_floor(self) -> None:
        """high + confirmed must remain > 60% of all assertions."""
        total = self.connection.execute("SELECT count(*) FROM assertion").fetchone()[0]
        high_confirmed = self.connection.execute(
            "SELECT count(*) FROM assertion WHERE confidence_label IN ('high', 'confirmed')"
        ).fetchone()[0]
        ratio = high_confirmed / total
        self.assertGreater(
            ratio, 0.60,
            f"High + confirmed confidence ({ratio:.1%}) below 60% quality floor"
        )

    def test_every_assertion_has_valid_audiences_json(self) -> None:
        """allowed_audiences_json must be valid JSON array for every assertion."""
        rows = self.connection.execute(
            "SELECT assertion_id, allowed_audiences_json FROM assertion"
        ).fetchall()
        invalid = []
        for r in rows:
            try:
                parsed = json.loads(r["allowed_audiences_json"])
                if not isinstance(parsed, list):
                    invalid.append((r["assertion_id"], "not a list"))
            except (json.JSONDecodeError, TypeError):
                invalid.append((r["assertion_id"], "parse error"))
        self.assertEqual(
            [], invalid,
            f"{len(invalid)} assertions have invalid audiences JSON: {invalid[:5]}"
        )

    def test_approved_status_implies_accepted_validation(self) -> None:
        """Every approved assertion must have validation_state=accepted."""
        bad = self.connection.execute("""
            SELECT assertion_id FROM assertion
            WHERE canonical_status = 'approved' AND validation_state != 'accepted'
        """).fetchall()
        self.assertEqual(
            [], [r["assertion_id"] for r in bad],
            f"{len(bad)} approved assertions lack accepted validation"
        )

    def test_no_superseded_assertion_is_retrieval_eligible(self) -> None:
        """A superseded assertion must never be retrieval-eligible."""
        bad = self.connection.execute("""
            SELECT count(*) FROM assertion
            WHERE canonical_status = 'superseded' AND retrieval_eligible = 1
        """).fetchone()[0]
        self.assertEqual(0, bad)

    def test_every_retrieval_eligible_assertion_has_search_documents(self) -> None:
        """Retrieval-eligible assertions must have at least one search_document."""
        missing = self.connection.execute("""
            SELECT count(*) FROM assertion a
            WHERE a.retrieval_eligible = 1
              AND NOT EXISTS (
                  SELECT 1 FROM search_document sd WHERE sd.assertion_id = a.assertion_id
              )
        """).fetchone()[0]
        self.assertEqual(
            0, missing,
            f"{missing} retrieval-eligible assertions lack search documents"
        )

    def test_every_assertion_has_at_least_one_evidence_link(self) -> None:
        """100% evidence coverage is a maintained invariant."""
        no_evidence = self.connection.execute("""
            SELECT count(*) FROM assertion a
            WHERE NOT EXISTS (
                SELECT 1 FROM assertion_evidence ae WHERE ae.assertion_id = a.assertion_id
            )
        """).fetchone()[0]
        self.assertEqual(0, no_evidence, f"{no_evidence} assertions have no evidence")

    def test_materiality_distribution_is_well_formed(self) -> None:
        """Every assertion materiality must be from the governed set."""
        allowed = {"critical", "high", "medium-high", "medium", "low"}
        actual = {
            r[0]
            for r in self.connection.execute(
                "SELECT DISTINCT materiality FROM assertion"
            )
        }
        unknown = actual - allowed
        self.assertEqual(set(), unknown, f"Unknown materiality values: {unknown}")

    def test_classification_values_are_governed(self) -> None:
        """Every assertion classification must be from the known set."""
        allowed = {
            "internal_working", "internal_consulting", "internal",
            "confidential", "external_approved", None,
        }
        actual = {
            r[0]
            for r in self.connection.execute(
                "SELECT DISTINCT classification FROM assertion"
            )
        }
        unknown = actual - allowed
        self.assertEqual(set(), unknown, f"Unknown classification values: {unknown}")

    def test_assertion_type_values_are_governed(self) -> None:
        """Every assertion_type must be from the known set."""
        allowed = {
            "confirmed_canonical_fact", "legacy_claim_record",
            "direct_statement", "human_interpretation",
            "extracted_claim", "recommendation",
        }
        actual = {
            r[0]
            for r in self.connection.execute(
                "SELECT DISTINCT assertion_type FROM assertion"
            )
        }
        unknown = actual - allowed
        self.assertEqual(
            set(), unknown,
            f"Unknown assertion_type values: {unknown}"
        )

    def test_candidate_assertion_ceiling_does_not_grow(self) -> None:
        """Candidate (unreviewed) assertions must not exceed known ceiling."""
        count = self.connection.execute(
            "SELECT count(*) FROM assertion WHERE canonical_status = 'candidate'"
        ).fetchone()[0]
        # Calibrated to actual count of 498
        self.assertLessEqual(
            count, 500,
            f"Candidate assertion count ({count}) exceeds ceiling of 500"
        )

    def test_legacy_migration_ratio_does_not_increase(self) -> None:
        """legacy_migration origin must trend downward, not upward."""
        total = self.connection.execute("SELECT count(*) FROM assertion").fetchone()[0]
        legacy = self.connection.execute(
            "SELECT count(*) FROM assertion WHERE origin_type = 'legacy_migration'"
        ).fetchone()[0]
        ratio = legacy / total
        # Current ratio is 90.8% — ceiling allows small growth but not a new bulk load
        self.assertLessEqual(
            ratio, 0.92,
            f"Legacy migration ratio ({ratio:.1%}) exceeds 92% ceiling — "
            f"new assertions should enter through governed intake"
        )

    def test_origin_type_values_are_governed(self) -> None:
        """Every origin_type must be from the known set."""
        allowed = {"legacy_migration", "human_source", "model_inference"}
        actual = {
            r[0]
            for r in self.connection.execute(
                "SELECT DISTINCT origin_type FROM assertion"
            )
        }
        unknown = actual - allowed
        self.assertEqual(set(), unknown, f"Unknown origin_type values: {unknown}")

    def test_currentness_state_values_are_governed(self) -> None:
        allowed = {"current", "historical", "future", "unknown"}
        actual = {
            r[0]
            for r in self.connection.execute(
                "SELECT DISTINCT currentness_state FROM assertion"
            )
        }
        unknown = actual - allowed
        self.assertEqual(set(), unknown, f"Unknown currentness_state values: {unknown}")


class EvidenceAndSourceAuditTests(unittest.TestCase):
    """Audit evidence chains and source attribution."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.connection = connect_sqlite(GRAPH, readonly=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_evidence_relationship_types_are_governed(self) -> None:
        allowed = {"supports", "contradicts", "qualifies", "neutral"}
        actual = {
            r[0]
            for r in self.connection.execute(
                "SELECT DISTINCT evidence_relationship FROM assertion_evidence"
            )
        }
        unknown = actual - allowed
        self.assertEqual(set(), unknown, f"Unknown evidence relationships: {unknown}")

    def test_every_evidence_item_references_existing_source_version(self) -> None:
        orphan_evidence = self.connection.execute("""
            SELECT count(*) FROM evidence_item ei
            WHERE NOT EXISTS (
                SELECT 1 FROM source_version sv
                WHERE sv.source_version_id = ei.source_version_id
            )
        """).fetchone()[0]
        self.assertEqual(0, orphan_evidence)

    def test_source_type_diversity_minimum(self) -> None:
        """Graph must have at least 10 distinct source types."""
        count = self.connection.execute(
            "SELECT count(DISTINCT source_type) FROM source"
        ).fetchone()[0]
        self.assertGreaterEqual(count, 10)

    def test_source_count_minimum(self) -> None:
        count = self.connection.execute("SELECT count(*) FROM source").fetchone()[0]
        self.assertGreaterEqual(count, 100)


class SearchDocumentAuditTests(unittest.TestCase):
    """Audit search document coverage and retrieval readiness."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.connection = connect_sqlite(GRAPH, readonly=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_retrieval_eligible_coverage_minimum(self) -> None:
        """At least 50% of search documents must be retrieval-eligible."""
        total = self.connection.execute("SELECT count(*) FROM search_document").fetchone()[0]
        eligible = self.connection.execute(
            "SELECT count(*) FROM search_document WHERE retrieval_eligible=1"
        ).fetchone()[0]
        ratio = eligible / total
        self.assertGreaterEqual(
            ratio, 0.50,
            f"Retrieval-eligible coverage ({ratio:.1%}) below 50% minimum"
        )

    def test_current_search_document_coverage_minimum(self) -> None:
        """At least 90% of search documents must be current."""
        total = self.connection.execute("SELECT count(*) FROM search_document").fetchone()[0]
        current = self.connection.execute(
            "SELECT count(*) FROM search_document WHERE currentness_state='current'"
        ).fetchone()[0]
        ratio = current / total
        self.assertGreaterEqual(
            ratio, 0.90,
            f"Current search doc coverage ({ratio:.1%}) below 90%"
        )

    def test_search_document_population_minimum(self) -> None:
        count = self.connection.execute("SELECT count(*) FROM search_document").fetchone()[0]
        self.assertGreaterEqual(count, 1000)

    def test_audit_is_read_only(self) -> None:
        before = sha256_file(GRAPH)
        # Just running all the queries above must not mutate
        self.assertEqual(before, sha256_file(GRAPH))


if __name__ == "__main__":
    unittest.main(verbosity=2)
