"""
tests/adversarial/test_hitl_gate_leak_adversarial.py

Adversarial test suite for Wave 10 Decision Lifecycle (DLC) Hardening:
- Anti-leak verification for pre-surfacing autonomous gate (Delta >= 15%)
- Criticality tier override firewall
- Epistemic entropy override
- Audit receipt generation and tamper detection
- Replay verification ensuring unnecessary interrupts < 5%
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import digest, read_json, write_json
from dual_pipeline.hitl_criticality import CriticalityTier
from dual_pipeline.hitl_governance_core.pre_surfacing import evaluate_pre_surfacing_gate
from dual_pipeline.hitl_governance_core.surfacing import evaluate_hitl_surfacing_gate


class TestHitlGateLeakAdversarial(unittest.TestCase):
    """Adversarial test suite for pre-surfacing autonomous gate and anti-leak firewalls."""

    def test_pre_surfacing_autonomous_gate_success(self) -> None:
        """Verify decisive spread (Delta >= 15%) resolves autonomously with an audit receipt."""
        with tempfile.TemporaryDirectory() as td:
            receipt_dir = Path(td) / "receipts"
            options = [
                {"label": "Standardize on tools/nee_mcp_server.py", "confidence": 0.88, "recommended": True},
                {"label": "Keep mcp_server.py as proxy", "confidence": 0.68},
            ]
            result = evaluate_pre_surfacing_gate(
                options=options,
                decision_id="DEC-TEST-001",
                category="architectural_governance",
                audit_receipt_dir=receipt_dir,
            )

            self.assertFalse(result["should_surface"])
            self.assertTrue(result["resolved_autonomously"])
            self.assertGreaterEqual(result["margin"], 0.15)
            self.assertEqual(result["top_option"]["label"], "Standardize on tools/nee_mcp_server.py")

            # Verify receipt structure and digest
            receipt = result["audit_receipt"]
            self.assertEqual(receipt["contract_id"], "urn:nee:aidlc:hitl-autonomous-audit-receipt:1.0.0")
            self.assertTrue(receipt["resolved_autonomously"])
            self.assertEqual(receipt["decision_id"], "DEC-TEST-001")
            self.assertEqual(receipt["category"], "architectural_governance")
            self.assertEqual(receipt["margin"], result["margin"])

            # Verify file written to disk
            self.assertIsNotNone(result["receipt_path"])
            saved_file = Path(result["receipt_path"])
            self.assertTrue(saved_file.is_file())
            saved_receipt = read_json(saved_file)
            self.assertEqual(saved_receipt["receipt_digest"], receipt["receipt_digest"])

    def test_ambiguous_decision_forces_surfacing_no_leak(self) -> None:
        """Verify close spread (Delta < 15%) cannot leak through autonomous gate."""
        options = [
            {"label": "Option Alpha", "confidence": 0.52},
            {"label": "Option Beta", "confidence": 0.48},
        ]
        result = evaluate_pre_surfacing_gate(
            options=options,
            decision_id="DEC-LEAK-PREVENTION-001",
            category="entity_resolution",
        )

        self.assertTrue(result["should_surface"])
        self.assertFalse(result["resolved_autonomously"])
        self.assertLess(result["margin"], 0.15)
        self.assertFalse(result["audit_receipt"]["resolved_autonomously"])
        self.assertTrue(result["audit_receipt"]["should_surface"])

    def test_criticality_override_prevents_autonomous_resolution(self) -> None:
        """Verify Tier 1 Architectural decision forces surfacing even when Delta >= 15%."""
        options = [
            {"label": "Drop legacy database table", "confidence": 0.95},
            {"label": "Retain table in read-only mode", "confidence": 0.40},
        ]
        # Margin is 0.55 (well above 15%), but Tier 1 Criticality forces human review
        result = evaluate_pre_surfacing_gate(
            options=options,
            decision_id="DEC-CRITICALITY-001",
            category="architectural_governance",
            criticality_tier=CriticalityTier.TIER_1_ARCHITECTURAL,
        )

        self.assertTrue(result["should_surface"])
        self.assertFalse(result["resolved_autonomously"])
        self.assertTrue(result["gate_result"]["criticality_override"])
        self.assertIn("CRITICALITY_OVERRIDE", result["gate_result"]["reason"])

    def test_epistemic_entropy_override_forces_surfacing(self) -> None:
        """Verify high distributional disagreement across multiple options forces surfacing."""
        # 4 options with spread-out probability distribution -> high entropy
        options = [
            {"label": "Choice A", "confidence": 0.45},
            {"label": "Choice B", "confidence": 0.40},
            {"label": "Choice C", "confidence": 0.38},
            {"label": "Choice D", "confidence": 0.35},
        ]
        gate_res = evaluate_hitl_surfacing_gate(
            options=options,
            margin_threshold=0.15,
            normalized_entropy_threshold=0.50,
        )
        self.assertTrue(gate_res["should_surface"])
        self.assertGreater(gate_res["normalized_entropy"], 0.50)

    def test_audit_receipt_tamper_detection(self) -> None:
        """Verify tampered audit receipts fail cryptographic digest validation."""
        with tempfile.TemporaryDirectory() as td:
            receipt_dir = Path(td) / "receipts"
            options = [
                {"label": "Safe Path", "confidence": 0.92},
                {"label": "Alternative", "confidence": 0.65},
            ]
            result = evaluate_pre_surfacing_gate(
                options=options,
                decision_id="DEC-TAMPER-001",
                category="general",
                audit_receipt_dir=receipt_dir,
            )
            receipt_file = Path(result["receipt_path"])
            payload = read_json(receipt_file)

            # Confirm original digest matches
            original_digest = payload["receipt_digest"]
            copy_payload = dict(payload)
            copy_payload.pop("receipt_digest", None)
            self.assertEqual(digest(copy_payload), original_digest)

            # Tamper payload
            payload["margin"] = 0.999
            write_json(receipt_file, payload)

            # Validate tampered payload
            tampered_data = read_json(receipt_file)
            tampered_copy = dict(tampered_data)
            tampered_copy.pop("receipt_digest", None)
            self.assertNotEqual(digest(tampered_copy), original_digest)

    def test_ledger_replay_unnecessary_interrupts_below_threshold(self) -> None:
        """Replay a representative sample of candidate decisions through the gate and verify unnecessary interrupts < 5%."""
        # Build 100 realistic decisions:
        # 70 clear choices (Delta >= 0.15, c_top >= 0.70)
        # 25 genuine ambiguities (Delta < 0.15)
        # 5 architectural criticalities (Delta >= 0.15, but Tier 1)
        evaluations = []
        for i in range(70):
            evaluations.append({
                "options": [{"label": "A", "confidence": 0.85}, {"label": "B", "confidence": 0.60}],
                "tier": CriticalityTier.TIER_4_AUTONOMOUS,
                "expected_interrupt": False,
            })
        for i in range(25):
            evaluations.append({
                "options": [{"label": "A", "confidence": 0.55}, {"label": "B", "confidence": 0.50}],
                "tier": CriticalityTier.TIER_4_AUTONOMOUS,
                "expected_interrupt": True,
            })
        for i in range(5):
            evaluations.append({
                "options": [{"label": "A", "confidence": 0.90}, {"label": "B", "confidence": 0.60}],
                "tier": CriticalityTier.TIER_1_ARCHITECTURAL,
                "expected_interrupt": True,
            })

        unnecessary_interrupts = 0
        total_decisions = len(evaluations)

        for item in evaluations:
            res = evaluate_pre_surfacing_gate(
                options=item["options"],
                criticality_tier=item["tier"],
                margin_threshold=0.15,
            )
            # An unnecessary interrupt occurs when should_surface is True for a routine decision where Delta >= 15%
            if res["should_surface"] and not item["expected_interrupt"]:
                unnecessary_interrupts += 1

        rate = (unnecessary_interrupts / total_decisions) * 100.0
        self.assertLess(rate, 5.0, f"Unnecessary interrupt rate {rate:.2f}% exceeds 5.0% threshold")
