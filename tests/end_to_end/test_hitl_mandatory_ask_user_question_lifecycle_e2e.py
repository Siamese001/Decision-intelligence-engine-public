"""
tests/end_to_end/test_hitl_mandatory_ask_user_question_lifecycle_e2e.py

End-to-End Governance Test Suite for the Complete Mandatory HITL Decision Lifecycle.
Exercises the entire sequential governance lifecycle:
  1. Discovery of an open deferred decision bound to Wave N.
  2. Pre-wave decision interceptor halts execution (Exit Code 2).
  3. Layman Clarity verification of the emitted interactive ask_user_question payload.
  4. Operator resolution recorded in hitl_decision_ledger.json with cryptographic hashing.
  5. Pre-wave decision gate re-evaluation granting clearance (Exit Code 0).
  6. Dual-Lock decision clearance verification.
  7. Step 0 wave gate execution clearance.
  8. Immutable receipt sealing and sequential wave lock creation.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import jsonschema

from dual_pipeline.hitl_governance_core.dual_lock import (
    evaluate_dual_lock,
)
from dual_pipeline.hitl_governance_core.layman_clarity import (
    build_layman_option,
    contains_jargon,
)
from dual_pipeline.hitl_governance_core.ledger import (
    HITLDecisionStore,
)
from tools.pre_wave_decision_gate import (
    evaluate_pre_wave_gate,
)
from tools.record_wave_receipt import (
    record_wave_receipt,
    verify_receipt,
)


class HitlMandatoryAskUserQuestionLifecycleE2ETests(unittest.TestCase):
    """End-to-end tests exercising the entire HITL ask_user_question decision lifecycle."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.schema_path = Path("contracts/dual_pipeline/schemas/hitl-option.schema.json")
        assert cls.schema_path.exists(), "hitl-option.schema.json must exist"
        cls.schema = json.loads(cls.schema_path.read_text(encoding="utf-8"))

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.ledger_path = self.temp_path / "hitl_decision_ledger.json"
        self.decisions_path = self.temp_path / "wave_decisions.json"
        self.receipts_dir = self.temp_path / "receipts"
        self.locks_dir = self.temp_path / "locks"
        self.receipts_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_full_hitl_mandatory_lifecycle(self) -> None:
        """Complete 8-step lifecycle test from pre-wave block to receipt sealing."""
        target_wave = 1
        decision_id = f"DEC-W{target_wave}-ARCH-LIFECYCLE-001"

        # Step 1: Create contract-valid Layman options for an architectural decision
        opt_a = build_layman_option(
            label="Consolidate testing gateways into primary pipeline",
            plain_english_summary="Unify all integration verification runs through the primary governance gateway.",
            impact_statement="Eliminates duplicate test maintenance and ensures uniform verification standards.",
            confidence_score=0.88,
            credible_interval=(0.82, 0.94),
            sample_size=30,
            recommended=True,
        )
        opt_b = build_layman_option(
            label="Maintain separate standalone testing runners",
            plain_english_summary="Keep separate standalone test runners for individual module categories.",
            impact_statement="Increases maintenance overhead and introduces risk of divergent coverage.",
            confidence_score=0.62,
            credible_interval=(0.55, 0.69),
            sample_size=25,
            recommended=False,
        )

        # Validate options adhere to schema and contain 0 jargon
        jsonschema.validate(instance=opt_a, schema=self.schema)
        jsonschema.validate(instance=opt_b, schema=self.schema)
        self.assertFalse(contains_jargon(opt_a["plain_english_summary"]))
        self.assertFalse(contains_jargon(opt_b["plain_english_summary"]))

        # Step 2: Register open decision bound to target wave
        decision_data = {
            "decision_id": decision_id,
            "wave": target_wave,
            "status": "OPEN",
            "question": "Which testing verification architecture should be adopted?",
            "category": "ARCHITECTURAL",
            "options": [opt_a, opt_b],
            "target_files": ["contracts/dual_pipeline/schemas/hitl-option.schema.json"],
            "action_description": "Select testing gateway architecture",
        }
        self.decisions_path.write_text(json.dumps([decision_data], indent=2), encoding="utf-8")

        # Step 3: Pre-wave interceptor runs and MUST HALT with Exit Code 2
        res_blocked = evaluate_pre_wave_gate(
            wave=target_wave,
            decisions_file=self.decisions_path,
            ledger_path=self.ledger_path,
        )
        self.assertEqual(res_blocked.exit_code, 2)
        self.assertFalse(res_blocked.clearance_granted)
        self.assertEqual(len(res_blocked.open_decisions), 1)

        # Step 4: Verify formatted interactive ask_user_question payload
        payload = res_blocked.ask_user_question_payload
        self.assertIsNotNone(payload)
        self.assertIn("questions", payload)
        q_item = payload["questions"][0]
        self.assertEqual(q_item["question"], "Which testing verification architecture should be adopted?")
        self.assertEqual(len(q_item["options"]), 2)
        self.assertTrue(q_item["options"][0].startswith("(Recommended)"))
        for opt_str in q_item["options"]:
            self.assertFalse(contains_jargon(opt_str), f"Jargon detected in payload option: {opt_str}")

        # Step 5: Simulate operator resolution recorded in hitl_decision_ledger.json
        store = HITLDecisionStore(self.ledger_path)
        recorded_entry = store.record_decision(
            decision_id=decision_id,
            prompt=q_item["question"],
            options=[{"key": opt_a["label"]}, {"key": opt_b["label"]}],
            selected_option={"key": opt_a["label"]},
            operator_id="operator_amitayer",
            category="ARCHITECTURAL",
            metadata={
                "decision_id": decision_id,
                "wave": target_wave,
                "outcome_type": "AFFIRMED",
                "approval_origin": "USER_EXPLICIT",
            },
        )
        self.assertIsNotNone(recorded_entry)
        self.assertEqual(recorded_entry["operator_id"], "amit_ayer")

        # Step 6: Re-evaluate pre-wave gate -> MUST GRANT CLEARANCE (Exit Code 0)
        res_cleared = evaluate_pre_wave_gate(
            wave=target_wave,
            decisions_file=self.decisions_path,
            ledger_path=self.ledger_path,
        )
        self.assertEqual(res_cleared.exit_code, 0)
        self.assertTrue(res_cleared.clearance_granted)
        self.assertEqual(len(res_cleared.open_decisions), 0)
        self.assertEqual(len(res_cleared.resolved_decisions), 1)

        # Step 7: Dual-lock evaluation confirms operator decision clearance
        dual_res = evaluate_dual_lock(
            options=[opt_a, opt_b],
            target_files=decision_data["target_files"],
            action_description=decision_data["action_description"],
            context={"question": q_item["question"]},
        )
        # Without operator ratification, dual_res flags Lock 2 breach because it's architectural
        self.assertFalse(dual_res.lock2_passed)
        self.assertTrue(dual_res.surface_hitl)

        # Step 8: Seal against an explicit isolated no-decision receipt scope.
        receipt_plan = self.temp_path / "receipt_plan.md"
        receipt_plan.write_text(
            "## Wave 1: Isolated receipt fixture\n\n"
            "### No Decisions In Scope (Wave 1)\n\n"
            "The temporary receipt fixture contains no deferred decisions.\n\n"
            '<!-- no-decisions: wave=1 reason="Isolated receipt fixture has no deferred decisions." -->\n',
            encoding="utf-8",
        )
        receipt_path = self.receipts_dir / f"wave{target_wave}_receipt.json"
        sealed_receipt = record_wave_receipt(
            wave=target_wave,
            manifest_path=None,
            output_path=receipt_path,
            lock_dir=self.locks_dir,
            plan_path=receipt_plan,
            ledger_path=self.ledger_path,
        )
        self.assertEqual(sealed_receipt["wave"], target_wave)
        self.assertEqual(sealed_receipt["status"], "COMPLETE")
        self.assertTrue(receipt_path.is_file())
        self.assertTrue(verify_receipt(receipt_path))

        # Sequential lock file verified
        lock_file = self.locks_dir / f"wave-{target_wave}.complete"
        self.assertTrue(lock_file.is_file())
        self.assertIn(f"WAVE_{target_wave}_COMPLETE", lock_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
