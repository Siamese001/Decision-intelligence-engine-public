"""tests/end_to_end/test_decision_clearance_e2e.py

End-to-End test suite for Decision Clearance (Wave 6).
Verifies:
1. Open qualifying decision in plan blocks pre-wave gate (exit code 2).
2. record_wave_receipt halts with OPERATOR_INPUT_REQUIRED when decisions are open.
3. Authentic operator ratification unlocks pre-wave gate (exit code 0).
4. record_wave_receipt completes receipt sealing and sequential lock after ratification.
5. Decision D6 fail-closed behavior: 0 decisions in plan halts with exit code 3.
6. Decision D6 --allow-zero-decisions flag grants clearance with exit code 0.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.hitl_governance_core.decision_record_writer import (
    record_operator_answer,
)
from tools.record_wave_receipt import record_wave_receipt


class DecisionClearanceE2ETests(unittest.TestCase):
    """End-to-end integration tests for the Decision Clearance lifecycle."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.sqlite_path = self.temp_path / "hitl_decision_ledger.sqlite"
        self.json_path = self.temp_path / "hitl_decision_ledger.json"
        self.plan_path = self.temp_path / "test_plan.md"
        self.lock_dir = self.temp_path / "locks"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_full_decision_clearance_lifecycle_e2e(self) -> None:
        """Full lifecycle: open decision blocks, ratification clears, receipt seals."""
        dec_id = "DEC-E2E-001"
        plan_content = f"""# E2E Test Implementation Plan

## Decisions
| ID | Decision | Options (confidence) | Δ | Handling |
|---|---|---|---|---|
| {dec_id} | Choice of serialization format (Wave 1) | Protocol Buffers (65%) vs JSON Schema (40%) | 25% | **Ask** |

## Wave 1: Data Serialization
### Milestones & Deliverables
- [NEW] serializer.py
### Acceptance Criteria
- 1. Deterministic serialization verified
### Runtime Receipt & Completion Gate
- .venv/bin/python test
"""
        self.plan_path.write_text(plan_content, encoding="utf-8")

        # 1. Pre-wave gate must halt with exit code 2 (open decision)
        cmd_gate = [
            sys.executable,
            "tools/pre_wave_decision_gate.py",
            "--wave", "1",
            "--plan-path", str(self.plan_path),
            "--ledger-path", str(self.json_path),
            "--json",
        ]
        res1 = subprocess.run(cmd_gate, capture_output=True, text=True)
        self.assertEqual(res1.returncode, 2, f"Expected exit code 2 on open decision, got {res1.returncode}\n{res1.stderr}")
        gate_json1 = json.loads(res1.stdout)
        self.assertFalse(gate_json1["clearance_granted"])
        self.assertIn(dec_id, gate_json1["open_decision_ids"])
        self.assertIsNotNone(gate_json1["ask_user_question_payload"])

        # 2. record_wave_receipt must block sealing
        target_receipt = self.temp_path / "wave1_receipt.json"
        with self.assertRaises(RuntimeError) as ctx:
            record_wave_receipt(
                wave=1,
                manifest_path=None,
                output_path=target_receipt,
                lock_dir=self.lock_dir,
                plan_path=self.plan_path,
                ledger_path=self.json_path,
            )
        self.assertIn("WAVE_RECEIPT_BLOCKED", str(ctx.exception))
        self.assertIn("OPERATOR_INPUT_REQUIRED", str(ctx.exception))

        # 3. Record authentic operator ratification in ledger
        record_operator_answer(
            decision_id=dec_id,
            question="Choice of serialization format?",
            options=[
                {"label": "Protocol Buffers", "confidence_score": 0.65},
                {"label": "JSON Schema", "confidence_score": 0.40},
            ],
            selected_option={"label": "Protocol Buffers", "confidence_score": 0.65},
            operator_origin="Approval verified from authentic human operator origin.",
            prior_user_command="implement",
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )

        # 4. Pre-wave gate must now succeed with exit code 0
        res2 = subprocess.run(cmd_gate, capture_output=True, text=True)
        self.assertEqual(res2.returncode, 0, f"Expected exit code 0 after ratification, got {res2.returncode}\n{res2.stderr}")
        gate_json2 = json.loads(res2.stdout)
        self.assertTrue(gate_json2["clearance_granted"])
        self.assertEqual(gate_json2["open_decisions_count"], 0)

        # 5. record_wave_receipt must now succeed
        receipt = record_wave_receipt(
            wave=1,
            manifest_path=None,
            output_path=target_receipt,
            lock_dir=self.lock_dir,
            plan_path=self.plan_path,
            ledger_path=self.json_path,
        )
        self.assertEqual(receipt["status"], "COMPLETE")
        self.assertTrue((self.lock_dir / "wave-1.complete").is_file())

    def test_decision_d6_zero_decisions_fail_closed_e2e(self) -> None:
        """Decision D6: zero decisions found in scope halts with exit code 3."""
        empty_plan = self.temp_path / "empty_plan.md"
        empty_plan.write_text("# Empty Plan\n## Wave 1: Setup\n### Milestones\n- Out\n### Criteria\n- Pass\n### Gate\n- Run\n", encoding="utf-8")

        cmd = [
            sys.executable,
            "tools/pre_wave_decision_gate.py",
            "--wave", "1",
            "--plan-path", str(empty_plan),
            "--json",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 3, f"Expected exit code 3 for zero decisions, got {res.returncode}")

        # With --allow-zero-decisions, exit code is 0
        cmd_allowed = cmd + ["--allow-zero-decisions"]
        res_allowed = subprocess.run(cmd_allowed, capture_output=True, text=True)
        self.assertEqual(res_allowed.returncode, 0, f"Expected exit code 0 with --allow-zero-decisions, got {res_allowed.returncode}")


if __name__ == "__main__":
    unittest.main()
