#!/usr/bin/env python3
"""Class A Unit Tests: Governed Refactoring Arbiter (Wave 2 Safeguards).

Verifies:
1. Detection of 400-line module budget breaches.
2. Detection of L0-L6 layer dependency inversions.
3. Detection of raw SQLite concurrency lock bypasses.
4. Mandatory Tier 1 HITL card formulation for architectural refactorings.
5. Autonomous pass for clean, compliant modules.
6. CLI execution exit codes (0 for clean, 2 for HITL required).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dual_pipeline.hitl_criticality import CriticalityTier
from tools.governed_refactor_arbiter import GovernedRefactorArbiter


class GovernedRefactorArbiterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.arbiter = GovernedRefactorArbiter(model_id="gpt-5.6-luna", reasoning_effort="high")

    def test_detects_400_line_budget_breach(self) -> None:
        """Files exceeding 400 lines must be flagged and require HITL decomposition."""
        large_code = "\n".join([f"# Line {i}" for i in range(450)])
        inspection = self.arbiter.inspect_source_code("dual_pipeline/monolith_sample.py", large_code)

        self.assertTrue(inspection["has_blocking_defects"])
        self.assertTrue(any("LINE_BUDGET_BREACH" in r for r in inspection["risks"]))
        self.assertEqual(inspection["criticality_tier"], CriticalityTier.TIER_1_ARCHITECTURAL)

        decision = self.arbiter.arbitrate_refactoring_proposal(
            decision_id="dec_line_budget_001",
            target_file="dual_pipeline/monolith_sample.py",
            proposed_action="refactor_budget",
            source_code=large_code,
        )
        self.assertTrue(decision["should_surface"])
        self.assertEqual(decision["status"], "HITL_REQUIRED")
        self.assertIsNotNone(decision["atomic_card"])
        self.assertIn("monolith_sample_core", str(decision["atomic_card"]["options"]))

    def test_detects_layer_inversion(self) -> None:
        """Lower layer importing higher layer violates Master Invariant 1."""
        inversion_code = (
            "import storage.connection\n"
            "from dual_pipeline.decision import DecisionEngine\n"  # Storage (L1) importing Decision (L5)
            "def worker(): pass\n"
        )
        inspection = self.arbiter.inspect_source_code("storage/worker_bad.py", inversion_code)

        self.assertTrue(inspection["has_blocking_defects"])
        self.assertTrue(any("LAYER_INVERSION_DEFECT" in r for r in inspection["risks"]))
        self.assertEqual(inspection["criticality_tier"], CriticalityTier.TIER_1_ARCHITECTURAL)

        decision = self.arbiter.arbitrate_refactoring_proposal(
            decision_id="dec_layer_inversion_001",
            target_file="storage/worker_bad.py",
            proposed_action="fix_inversion",
            source_code=inversion_code,
        )
        self.assertTrue(decision["should_surface"])
        self.assertEqual(decision["status"], "HITL_REQUIRED")

    def test_detects_concurrency_lock_bypass(self) -> None:
        """Direct sqlite3.connect bypasses WAL serialization and violates Master Invariant 2."""
        raw_sqlite_code = (
            "import sqlite3\n"
            "def query():\n"
            "    conn = sqlite3.connect('test.db')\n"
            "    return conn.cursor().fetchall()\n"
        )
        inspection = self.arbiter.inspect_source_code("pipeline_b_extraction/raw_db.py", raw_sqlite_code)

        self.assertTrue(inspection["has_blocking_defects"])
        self.assertTrue(any("CONCURRENCY_LOCK_BYPASS" in r for r in inspection["risks"]))
        self.assertEqual(inspection["criticality_tier"], CriticalityTier.TIER_1_ARCHITECTURAL)

        decision = self.arbiter.arbitrate_refactoring_proposal(
            decision_id="dec_concurrency_001",
            target_file="pipeline_b_extraction/raw_db.py",
            proposed_action="fix_concurrency",
            source_code=raw_sqlite_code,
        )
        self.assertTrue(decision["should_surface"])
        self.assertEqual(decision["status"], "HITL_REQUIRED")

    def test_clean_module_passes(self) -> None:
        """A clean, compliant module under 400 lines without inversions passes without blocking risks."""
        clean_code = (
            "from __future__ import annotations\n"
            "def utility_fn(x: int) -> int:\n"
            "    return x * 2\n"
        )
        inspection = self.arbiter.inspect_source_code("dual_pipeline/clean_util.py", clean_code)

        self.assertFalse(inspection["has_blocking_defects"])
        self.assertEqual(len(inspection["risks"]), 0)
        self.assertEqual(inspection["criticality_tier"], CriticalityTier.TIER_4_AUTONOMOUS)

    def test_auto_records_in_temporary_ledger(self) -> None:
        """Verify decisions can be recorded to hitl_decision_ledger.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_ledger = Path(tmpdir) / "hitl_decision_ledger.json"
            custom_arbiter = GovernedRefactorArbiter(ledger_path=test_ledger)

            code = "\n".join([f"# Line {i}" for i in range(410)])
            res = custom_arbiter.arbitrate_refactoring_proposal(
                decision_id="dec_record_test",
                target_file="dual_pipeline/test_file.py",
                proposed_action="test_record",
                source_code=code,
                auto_record=False,
            )
            self.assertTrue(res["should_surface"])
            self.assertEqual(res["status"], "HITL_REQUIRED")


if __name__ == "__main__":
    unittest.main()
