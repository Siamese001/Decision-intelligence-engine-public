"""
tests/test_copilot_predicate_ssot.py

Unit tests for Copilot & Query Predicate SSOT Alignment (Wave 1).
Verifies:
1. Zero literal predicate sets remain in copilot_retriever.py (AST scan).
2. Every copilot predicate is strictly registered in assertion_predicate_registry.
3. E3 regression pin: HAS_RISK, SUPPORTED_BY, CANDIDATE_FOR, CONTAINS are priority.
4. QUESTION_PREDICATES include HAS_RISK and CANDIDATE_FOR.
5. cross_workstream_synthesis blueprint in planning.py includes DEPENDS_ON, HAS_RISK, CONTAINS.
6. normalize_copilot_predicate handles aliases, whitespace, and case.
"""
from __future__ import annotations

import ast
import sqlite3
import unittest
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.copilot_predicates import (
    ACTION_PREDICATES,
    ALL_COPILOT_PREDICATES,
    CANONICAL_PREDICATE_REGISTRY,
    PREDICATE_ALIASES,
    PREFETCH_ACTION_PREDICATES,
    PREFETCH_ATTENDEE_PREDICATES,
    PREFETCH_DECISION_PREDICATES,
    PREFETCH_RISK_PREDICATES,
    PREFETCH_TOPIC_PREDICATES,
    PRIORITY_PREDICATES,
    PRIORITY_RELATIONSHIPS,
    QUESTION_PREDICATES,
    normalize_copilot_predicate,
    validate_copilot_predicates_against_registry,
)
from dual_pipeline.query_core.planning import _QUERY_TYPE_BLUEPRINTS


class TestCopilotPredicateSSOT(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent
        self.graph_db_path = self.repo_root / "graph" / "nee_graph.sqlite"

    def test_zero_literal_predicate_sets_in_copilot_retriever(self) -> None:
        """Acceptance Criterion 1: AST scan proves zero literal predicate sets remain in copilot_retriever.py."""
        retriever_file = (
            self.repo_root
            / "dual_pipeline"
            / "live_meeting_recorder_core"
            / "copilot_retriever.py"
        )
        self.assertTrue(retriever_file.is_file(), "copilot_retriever.py must exist")

        tree = ast.parse(retriever_file.read_text(encoding="utf-8"))
        predicate_markers = {
            "DECISION",
            "DECISION_NEEDED",
            "HAS_OPEN_QUESTION",
            "RISK",
            "RISK_OF",
            "BLOCKED_BY",
            "OWNS",
            "LEADS",
            "DELIVERS",
            "DEPENDS_ON",
            "HAS_RISK",
            "CONTAINS",
            "SUPPORTED_BY",
            "CANDIDATE_FOR",
            "CONSTRAINS",
            "CRITICAL_DEPENDENCY",
        }

        literal_violations = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Set, ast.Tuple)):
                str_constants = [
                    e.value
                    for e in node.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ]
                matched = [c for c in str_constants if c in predicate_markers]
                if matched:
                    literal_violations.append((node.lineno, matched))

        self.assertEqual(
            [],
            literal_violations,
            f"Found forbidden literal predicate sets/tuples in copilot_retriever.py: {literal_violations}",
        )

    def test_all_copilot_predicates_registered_in_database(self) -> None:
        """Acceptance Criterion 2: Every predicate in copilot_predicates exists in assertion_predicate_registry."""
        if not self.graph_db_path.is_file():
            self.skipTest(f"Graph database not found at {self.graph_db_path}")

        # Validates against SQLite assertion_predicate_registry
        is_valid = validate_copilot_predicates_against_registry(self.graph_db_path)
        self.assertTrue(is_valid)

        # Directly query sqlite to double check
        conn = sqlite3.connect(str(self.graph_db_path))
        try:
            cur = conn.cursor()
            rows = cur.execute("SELECT predicate FROM assertion_predicate_registry").fetchall()
            db_predicates = {r[0] for r in rows if r[0]}
        finally:
            conn.close()

        unregistered = ALL_COPILOT_PREDICATES - db_predicates
        self.assertEqual(
            set(),
            unregistered,
            f"Copilot predicates not registered in DB assertion_predicate_registry: {unregistered}",
        )

    def test_e3_regression_pin_priority_predicates(self) -> None:
        """Regression pin on Finding E3: HAS_RISK, SUPPORTED_BY, CANDIDATE_FOR, CONTAINS in priority predicates."""
        e3_required = {"HAS_RISK", "SUPPORTED_BY", "CANDIDATE_FOR", "CONTAINS"}
        for pred in e3_required:
            self.assertIn(pred, PRIORITY_PREDICATES)
            self.assertIn(pred, PRIORITY_RELATIONSHIPS)

    def test_question_predicates_cover_risks_and_candidate_for(self) -> None:
        """Acceptance Criterion 3 prerequisite: question predicates must include HAS_RISK and CANDIDATE_FOR."""
        self.assertIn("HAS_RISK", QUESTION_PREDICATES)
        self.assertIn("CANDIDATE_FOR", QUESTION_PREDICATES)
        self.assertIn("CONSTRAINS", QUESTION_PREDICATES)

    def test_action_predicates_members(self) -> None:
        """Verifies action predicates contain expected deliverable/action relationships."""
        expected = {"OWNS_OR_LEADS", "DELIVERS", "DEPENDS_ON", "GOVERNS", "VALIDATES", "CONTRIBUTES_TO"}
        self.assertTrue(expected.issubset(ACTION_PREDICATES))

    def test_prefetch_tuples_subset_of_canonical(self) -> None:
        """Verifies all prefetch tuples contain only canonical registry predicates."""
        for name, spec_tuple in [
            ("PREFETCH_DECISION_PREDICATES", PREFETCH_DECISION_PREDICATES),
            ("PREFETCH_RISK_PREDICATES", PREFETCH_RISK_PREDICATES),
            ("PREFETCH_ACTION_PREDICATES", PREFETCH_ACTION_PREDICATES),
            ("PREFETCH_TOPIC_PREDICATES", PREFETCH_TOPIC_PREDICATES),
            ("PREFETCH_ATTENDEE_PREDICATES", PREFETCH_ATTENDEE_PREDICATES),
        ]:
            with self.subTest(tuple_name=name):
                for p in spec_tuple:
                    self.assertIn(p, CANONICAL_PREDICATE_REGISTRY)

    def test_cross_workstream_blueprint_includes_dependencies_and_risks(self) -> None:
        """Verifies query_core/planning.py cross_workstream_synthesis blueprint contains DEPENDS_ON, HAS_RISK, CONTAINS."""
        blueprint = _QUERY_TYPE_BLUEPRINTS.get("cross_workstream_synthesis")
        self.assertIsNotNone(blueprint)
        relationships = blueprint[1]
        self.assertIn("DEPENDS_ON", relationships)
        self.assertIn("HAS_RISK", relationships)
        self.assertIn("CONTAINS", relationships)
        self.assertIn("CONTRIBUTES_TO", relationships)
        self.assertIn("USES_PATTERN", relationships)
        self.assertIn("SUPPORTED_BY", relationships)

    def test_predicate_normalizer_and_alias_mapping(self) -> None:
        """Verifies normalize_copilot_predicate correctly resolves legacy aliases and formats."""
        self.assertIn("RISK", PREDICATE_ALIASES)
        self.assertEqual(PREDICATE_ALIASES["RISK"], "HAS_RISK")
        self.assertEqual(normalize_copilot_predicate("risk"), "HAS_RISK")
        self.assertEqual(normalize_copilot_predicate("  RISK_OF  "), "HAS_RISK")
        self.assertEqual(normalize_copilot_predicate("decision"), "CONTEXT_FOR")
        self.assertEqual(normalize_copilot_predicate("blocked_by"), "CONSTRAINS")
        self.assertEqual(normalize_copilot_predicate("owns"), "OWNS_OR_LEADS")
        self.assertEqual(normalize_copilot_predicate("leads"), "OWNS_OR_LEADS")
        self.assertEqual(normalize_copilot_predicate("critical_dependency"), "DEPENDS_ON")
        self.assertEqual(normalize_copilot_predicate("advises"), "AFFILIATED_WITH")
        self.assertEqual(normalize_copilot_predicate("employed_by"), "AFFILIATED_WITH")
        self.assertEqual(normalize_copilot_predicate("has_risk"), "HAS_RISK")
        self.assertEqual(normalize_copilot_predicate(""), "")
        self.assertEqual(normalize_copilot_predicate(None), "")


if __name__ == "__main__":
    unittest.main()
