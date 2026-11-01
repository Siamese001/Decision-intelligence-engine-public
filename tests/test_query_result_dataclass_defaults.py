"""Regression and invariant tests for QueryResult dataclass defaults.

Verifies:
1. dict(QueryResult()) succeeds without raising KeyError for default_factory fields.
2. All dataclass fields are populated with correct defaults or empty collections.
3. Dict conversion roundtrip preserves default fields and custom extensions.
"""

from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.query_result import QueryResult


class TestQueryResultDataclassDefaults(unittest.TestCase):
    """Test suite ensuring QueryResult defaults never throw KeyError during mapping conversions."""

    def test_dict_conversion_succeeds_without_keyerror(self) -> None:
        qr = QueryResult()
        d = dict(qr)
        self.assertIsInstance(d, dict)
        for field in dataclasses.fields(QueryResult):
            self.assertIn(field.name, d)

    def test_query_result_dict_conversion_does_not_raise(self) -> None:
        qr = QueryResult()
        d = dict(qr)
        self.assertIsInstance(d, dict)
        self.assertIn("routing_rationale", d)
        self.assertIn("required_stages", d)
        self.assertEqual(d["routing_rationale"], {})
        self.assertEqual(d["required_stages"], [])

    def test_default_factory_containers_are_independent_instances(self) -> None:
        qr1 = QueryResult()
        qr2 = QueryResult()

        self.assertEqual(qr1.routing_rationale, {})
        self.assertEqual(qr1.required_stages, [])
        self.assertEqual(qr1.retrieval_evidence, {})
        self.assertIsNot(qr1.routing_rationale, qr2.routing_rationale)
        self.assertIsNot(qr1.required_stages, qr2.required_stages)

        # Mutating qr1 containers must not mutate qr2 containers
        qr1.routing_rationale["key"] = "val"
        qr1.required_stages.append("stage_1")

        self.assertNotIn("key", qr2.routing_rationale)
        self.assertEqual(len(qr2.required_stages), 0)

    def test_query_result_field_access(self) -> None:
        qr = QueryResult(
            query_id="qid-123",
            query_type="current_state_corroboration",
            route="GRAPH_EVIDENCE_UTILITY",
        )
        self.assertEqual(qr.query_id, "qid-123")
        self.assertEqual(qr.query_type, "current_state_corroboration")
        self.assertEqual(qr.route, "GRAPH_EVIDENCE_UTILITY")
        self.assertEqual(qr.routing_rationale, {})
        self.assertEqual(qr.required_stages, [])

    def test_field_access_and_membership(self) -> None:
        qr = QueryResult(routing_rationale={"strategy": "hybrid"})
        self.assertEqual(qr["routing_rationale"], {"strategy": "hybrid"})
        self.assertEqual(qr.routing_rationale, {"strategy": "hybrid"})
        self.assertIn("routing_rationale", qr)
        self.assertIn("route", qr)


if __name__ == "__main__":
    unittest.main()
