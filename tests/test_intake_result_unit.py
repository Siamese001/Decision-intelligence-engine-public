"""Unit tests for IntakeResult in dual_pipeline/intake_result.py.

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

from dual_pipeline.intake_result import IntakeResult


class TestIntakeResultUnit(unittest.TestCase):
    """Unit test suite for IntakeResult mapping dataclass."""

    def test_default_initialization(self) -> None:
        ir = IntakeResult()
        self.assertEqual(ir.status, "")
        self.assertEqual(ir.milestone, "")
        self.assertEqual(ir.candidate_count, 0)
        self.assertFalse(ir.production_qualified)
        self.assertFalse(ir.idempotent_replay)

    def test_custom_fields_initialization(self) -> None:
        ir = IntakeResult(
            status="COMPLETE",
            intake_id="intake_001",
            candidate_count=15,
            production_qualified=True,
            extra_diagnostic="diagnostic_ok",
        )
        self.assertEqual(ir.status, "COMPLETE")
        self.assertEqual(ir.intake_id, "intake_001")
        self.assertEqual(ir.candidate_count, 15)
        self.assertTrue(ir.production_qualified)
        self.assertEqual(ir["extra_diagnostic"], "diagnostic_ok")

    def test_mapping_protocol_getitem_and_get(self) -> None:
        ir = IntakeResult(intake_id="intake_xyz", status="BLOCKED")
        self.assertEqual(ir["intake_id"], "intake_xyz")
        self.assertEqual(ir.get("intake_id"), "intake_xyz")
        self.assertEqual(ir.get("missing_key", "default_val"), "default_val")

        with self.assertRaises(KeyError):
            _ = ir["missing_key"]

    def test_as_dict_export(self) -> None:
        ir = IntakeResult(status="COMPLETE", source_kind="transcript", custom_flag=True)
        data = ir.as_dict()
        self.assertIsInstance(data, dict)
        self.assertEqual(data["status"], "COMPLETE")
        self.assertEqual(data["source_kind"], "transcript")
        self.assertEqual(data["custom_flag"], True)

    def test_mapping_iteration_and_length(self) -> None:
        ir = IntakeResult(extra_field_a="a", extra_field_b="b")
        keys = list(ir)
        self.assertIn("status", keys)
        self.assertIn("extra_field_a", keys)
        self.assertIn("extra_field_b", keys)
        self.assertEqual(len(ir), len(keys))


if __name__ == "__main__":
    unittest.main()
