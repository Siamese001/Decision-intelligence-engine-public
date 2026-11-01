"""Unit tests for QueryResult in dual_pipeline/query_result.py.

Verifies:
1. Default initialization and field types.
2. Collections Mapping protocol (__getitem__, __iter__, __len__, get).
3. Handling of extra undeclared attributes.
4. as_dict serialization roundtrip.
5. KeyError behavior for invalid keys.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.query_result import QueryResult


class TestQueryResultUnit(unittest.TestCase):
    """Unit test suite for QueryResult mapping dataclass."""

    def test_default_initialization(self) -> None:
        qr = QueryResult()
        self.assertEqual(qr.query_id, "")
        self.assertFalse(qr.production_qualified)
        self.assertEqual(qr.approved_record_count, 0)
        self.assertEqual(qr.latency_ms, 0.0)
        self.assertEqual(qr.route, "")

    def test_custom_fields_initialization(self) -> None:
        qr = QueryResult(
            query_id="q_123",
            route="governed_route",
            production_qualified=True,
            approved_record_count=42,
            routing_rationale={"rule": "deterministic"},
            custom_extension_val="extended",
        )
        self.assertEqual(qr.query_id, "q_123")
        self.assertEqual(qr.route, "governed_route")
        self.assertTrue(qr.production_qualified)
        self.assertEqual(qr.approved_record_count, 42)
        self.assertEqual(qr.routing_rationale, {"rule": "deterministic"})
        self.assertEqual(qr["custom_extension_val"], "extended")

    def test_mapping_protocol_getitem_and_get(self) -> None:
        qr = QueryResult(query_id="q_abc", latency_ms=12.5)
        self.assertEqual(qr["query_id"], "q_abc")
        self.assertEqual(qr.get("query_id"), "q_abc")
        self.assertEqual(qr.get("nonexistent_key", "default_val"), "default_val")

        with self.assertRaises(KeyError):
            _ = qr["nonexistent_key"]

    def test_as_dict_export(self) -> None:
        qr = QueryResult(query_id="q_export", delivery_profile="executive", custom_meta="meta_1")
        data = qr.as_dict()
        self.assertIsInstance(data, dict)
        self.assertEqual(data["query_id"], "q_export")
        self.assertEqual(data["delivery_profile"], "executive")
        self.assertEqual(data["custom_meta"], "meta_1")

    def test_mapping_iteration_and_length(self) -> None:
        qr = QueryResult(extra_one=1, extra_two=2)
        keys = list(qr)
        self.assertIn("query_id", keys)
        self.assertIn("extra_one", keys)
        self.assertIn("extra_two", keys)
        self.assertEqual(len(qr), len(keys))


if __name__ == "__main__":
    unittest.main()
