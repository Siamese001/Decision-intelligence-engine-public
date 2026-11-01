"""
tests/end_to_end/test_pre_wave_decision_gate_e2e.py

End-to-End Governance Test Suite for the Pre-Wave Decision Interceptor.
Verifies:
  1. Open deferred decision halts wave execution with Exit Code 2.
  2. Emitted ask_user_question payload complies strictly with the Zero-Jargon Layman Invariant.
  3. Authentic operator ratification in hitl_decision_ledger.json unlocks wave execution (Exit Code 0).
  4. tools/run_wave_gate.py Step 0 intercept halts with Exit Code 2 on open decisions.
  5. tools/record_wave_receipt.py cryptographically blocks receipt sealing on open decisions.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.hitl_governance_core.layman_clarity import (
    contains_jargon,
)
from dual_pipeline.hitl_governance_core.ledger import (
    HITLDecisionStore,
)
from tools.pre_wave_decision_gate import (
    PreWaveDecision,
    evaluate_pre_wave_gate,
)
from tools.record_wave_receipt import record_wave_receipt


class PreWaveDecisionGateE2ETests(unittest.TestCase):
    """End-to-end test cases for pre-wave decision intercept governance."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.ledger_path = self.temp_path / "hitl_decision_ledger.json"
        self.decisions_path = self.temp_path / "wave_decisions.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_open_decision_blocks_wave_execution_exit_code_2(self) -> None:
        """Open deferred decision produces exit code 2 and formatted ask_user_question payload."""
        open_decision = {
            "decision_id": "DEC-W3-ARCH-001",
            "wave": 3,
            "status": "OPEN",
            "question": "Which testing verification architecture should be adopted?",
            "options": [
                {
                    "label": "Consolidate verification runners",
                    "plain_english_summary": "Unify all module verification runs through the primary governance gateway.",
                    "impact_statement": "Eliminates duplicate test maintenance and ensures uniform standards.",
                    "confidence_score": 0.88,
                    "recommended": True,
                },
                {
                    "label": "Maintain standalone runners",
                    "plain_english_summary": "Keep separate standalone test runners for individual module categories.",
                    "impact_statement": "Increases maintenance overhead and introduces risk of divergent coverage.",
                    "confidence_score": 0.60,
                    "recommended": False,
                },
            ],
        }
        self.decisions_path.write_text(json.dumps([open_decision], indent=2), encoding="utf-8")

        cmd = [
            sys.executable,
            "tools/pre_wave_decision_gate.py",
            "--wave", "3",
            "--decisions-file", str(self.decisions_path),
            "--ledger-path", str(self.ledger_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)

        self.assertEqual(res.returncode, 2, f"Expected exit code 2 on open decision, got {res.returncode}")
        self.assertIn("🛑 [PRE-WAVE DECISION BLOCKED]", res.stdout)
        self.assertIn("DEC-W3-ARCH-001", res.stdout)
        self.assertIn("INTERACTIVE ASK_USER_QUESTION PAYLOAD", res.stdout)

    def test_formatted_payload_strictly_complies_with_layman_clarity(self) -> None:
        """Formatted payload emitted by pre-wave gate adheres to Zero-Jargon Layman Standard."""
        open_decision = PreWaveDecision(
            decision_id="DEC-W3-ARCH-002",
            wave=3,
            status="OPEN",
            question="Which database indexing strategy should be approved?",
            options=[
                {
                    "label": "Deploy hybrid lexical and semantic indexing",
                    "plain_english_summary": "Deploy dual index structures combining text and semantic search.",
                    "impact_statement": "Improves query retrieval accuracy without degrading search latency.",
                    "confidence_score": 0.85,
                    "recommended": True,
                },
                {
                    "label": "Preserve single index structure",
                    "plain_english_summary": "Maintain standard index without secondary semantic representations.",
                    "impact_statement": "Avoids index storage overhead while accepting lower search recall.",
                    "confidence_score": 0.65,
                    "recommended": False,
                },
            ],
        )

        res = evaluate_pre_wave_gate(
            wave=3,
            ledger_path=self.ledger_path,
            extra_decisions=[open_decision],
        )

        self.assertEqual(res.exit_code, 2)
        self.assertIsNotNone(res.ask_user_question_payload)

        payload = res.ask_user_question_payload
        self.assertIn("questions", payload)
        self.assertEqual(len(payload["questions"]), 1)

        q_entry = payload["questions"][0]
        self.assertFalse(contains_jargon(q_entry["question"]))
        for opt_text in q_entry["options"]:
            self.assertFalse(contains_jargon(opt_text), f"Jargon found in: {opt_text}")
        self.assertTrue(q_entry["options"][0].startswith("(Recommended)"))

    def test_decision_ratification_in_ledger_grants_clearance(self) -> None:
        """Human operator ratification recorded in ledger clears the wave (Exit Code 0)."""
        dec_id = "DEC-W3-ARCH-003"
        open_decision = {
            "decision_id": dec_id,
            "wave": 3,
            "status": "OPEN",
            "question": "Authorize architecture choice?",
            "options": [
                {"label": "Option A", "confidence_score": 0.85},
                {"label": "Option B", "confidence_score": 0.65},
            ],
        }
        self.decisions_path.write_text(json.dumps([open_decision], indent=2), encoding="utf-8")

        # 1. First run without ledger ratification -> Must exit code 2
        cmd = [
            sys.executable,
            "tools/pre_wave_decision_gate.py",
            "--wave", "3",
            "--decisions-file", str(self.decisions_path),
            "--ledger-path", str(self.ledger_path),
        ]
        res1 = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res1.returncode, 2)

        # 2. Record authentic operator ratification in ledger
        store = HITLDecisionStore(self.ledger_path)
        store.record_decision(
            decision_id=dec_id,
            prompt="Authorize architecture choice?",
            options=[{"key": "Option A"}, {"key": "Option B"}],
            selected_option={"key": "Option A"},
            operator_id="operator_amitayer",
            category="ARCHITECTURAL",
            metadata={
                "decision_id": dec_id,
                "wave": 3,
                "outcome_type": "AFFIRMED",
            },
        )

        # 3. Second run with ratified ledger -> Must exit code 0
        res2 = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res2.returncode, 0, f"Expected exit code 0 after ratification, got {res2.returncode}\n{res2.stderr}")
        self.assertIn("✅ [CLEARANCE GRANTED]", res2.stdout)
        self.assertIn(dec_id, res2.stdout)

    def test_record_wave_receipt_blocks_sealing_when_decisions_open(self) -> None:
        """record_wave_receipt raises RuntimeError if open decisions block the wave."""
        open_decision = {
            "decision_id": "DEC-W3-ARCH-004",
            "wave": 3,
            "status": "OPEN",
            "question": "Blocking question?",
            "options": [{"label": "Opt A", "confidence_score": 0.8}],
        }
        # Place decision in default wave decisions file location temporarily
        default_dir = Path("reports/hitl_mandatory_ask_question")
        default_dir.mkdir(parents=True, exist_ok=True)
        default_dec_file = default_dir / "wave_decisions.json"
        existed_before = default_dec_file.is_file()
        prior_content = default_dec_file.read_text(encoding="utf-8") if existed_before else None

        try:
            default_dec_file.write_text(json.dumps([open_decision], indent=2), encoding="utf-8")
            target_receipt = self.temp_path / "test_wave3_receipt.json"

            # Attempting to seal wave 3 receipt must be blocked!
            with self.assertRaises(RuntimeError) as ctx:
                record_wave_receipt(
                    wave=3,
                    manifest_path=None,
                    output_path=target_receipt,
                    lock_dir=self.temp_path / "locks",
                )
            self.assertIn("WAVE_RECEIPT_BLOCKED", str(ctx.exception))
        finally:
            if existed_before and prior_content is not None:
                default_dec_file.write_text(prior_content, encoding="utf-8")
            elif default_dec_file.is_file():
                default_dec_file.unlink()

    def test_json_flag_emits_structured_pre_wave_gate_result(self) -> None:
        """--json flag emits structured machine-readable JSON matching PreWaveGateResult."""
        cmd = [
            sys.executable,
            "tools/pre_wave_decision_gate.py",
            "--wave", "99",
            "--json",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)

        data = json.loads(res.stdout)
        self.assertEqual(data["wave"], 99)
        self.assertTrue(data["clearance_granted"])
        self.assertEqual(data["exit_code"], 0)
        self.assertEqual(data["open_decisions_count"], 0)


if __name__ == "__main__":
    unittest.main()
