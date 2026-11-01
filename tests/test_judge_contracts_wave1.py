"""
tests/test_judge_contracts_wave1.py

Wave 1 Verification Suite for Meeting Output LLM-as-a-Judge Hardening.
Tests:
1. Strict Scorecard Validation (Output 1, Output 2, Output 3, Document Mode O3).
2. Fail-closed rejection of missing dimensions, composite mismatches, verdict mismatches, and incomplete coverage.
3. Authenticated JudgeInputEnvelope binding and cryptographic digest calculation.
4. Meeting X1, X2, and X3 Exit Gate contracts including cross-output coherence.
"""
from __future__ import annotations

import json
from pathlib import Path
import unittest
from pydantic import ValidationError

from dual_pipeline.prompts.contracts.versioned_judge_contracts import (
    DocumentOutput3ScorecardV2,
    JudgeInputEnvelope,
    MeetingXExitContract,
    Output1ScorecardV2,
    Output2ScorecardV2,
    Output3ScorecardV2,
    validate_versioned_judge_scorecard,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "judge_contracts"


class TestJudgeContractsWave1(unittest.TestCase):
    """Verifies versioned contracts, scorecard schemas, envelopes, and exit gates."""

    # -------------------------------------------------------------------------
    # 1. Valid Scorecard Contracts
    # -------------------------------------------------------------------------

    def test_valid_output1_scorecard_passes(self) -> None:
        """Valid O1 scorecard passes strict validation."""
        path = FIXTURES_DIR / "valid_output1_card.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        card = Output1ScorecardV2.model_validate(data)
        self.assertEqual(card.composite_score, 98.0)
        self.assertEqual(card.verdict, "PASS")

        valid, errs = validate_versioned_judge_scorecard(data, output_num=1)
        self.assertTrue(valid, f"Validation failed: {errs}")

    def test_valid_output2_scorecard_passes(self) -> None:
        """Valid O2 scorecard passes strict validation with section scores."""
        path = FIXTURES_DIR / "valid_output2_card.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        card = Output2ScorecardV2.model_validate(data)
        self.assertEqual(card.composite_score, 97.0)
        self.assertEqual(card.verdict, "PASS")

        valid, errs = validate_versioned_judge_scorecard(data, output_num=2)
        self.assertTrue(valid, f"Validation failed: {errs}")

    def test_valid_output3_scorecard_passes(self) -> None:
        """Valid O3 scorecard passes strict validation with 6 dimensions and canonical HAS_ROLE."""
        path = FIXTURES_DIR / "valid_output3_card.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        card = Output3ScorecardV2.model_validate(data)
        self.assertEqual(card.composite_score, 95.0)
        self.assertEqual(card.canonical_role_predicate, "HAS_ROLE")

        valid, errs = validate_versioned_judge_scorecard(data, output_num=3)
        self.assertTrue(valid, f"Validation failed: {errs}")

    def test_valid_document_output3_scorecard_passes(self) -> None:
        """Document mode O3 scorecard passes with explicit NOT_APPLICABLE markers."""
        path = FIXTURES_DIR / "valid_document_output3_card.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        card = DocumentOutput3ScorecardV2.model_validate(data)
        self.assertEqual(card.modality, "DOCUMENT")
        self.assertEqual(card.output_1_status, "NOT_APPLICABLE_DOCUMENT_MODE")

        valid, errs = validate_versioned_judge_scorecard(data, output_num=3, modality="DOCUMENT")
        self.assertTrue(valid, f"Validation failed: {errs}")

    # -------------------------------------------------------------------------
    # 2. Invalid Scorecard Fail-Closed Tests
    # -------------------------------------------------------------------------

    def test_invalid_missing_dimensions_fails_closed(self) -> None:
        """Scorecard missing required dimensions is rejected immediately."""
        path = FIXTURES_DIR / "invalid_missing_dimensions.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        with self.assertRaises(ValidationError):
            Output1ScorecardV2.model_validate(data)

        valid, errs = validate_versioned_judge_scorecard(data, output_num=1)
        self.assertFalse(valid)
        self.assertTrue(len(errs) > 0)

    def test_invalid_composite_mismatch_fails_closed(self) -> None:
        """Scorecard with forged composite_score != sum(dimensions) is rejected."""
        path = FIXTURES_DIR / "invalid_composite_mismatch.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        with self.assertRaises(ValidationError) as ctx:
            Output1ScorecardV2.model_validate(data)
        self.assertIn("does not match dimension sum", str(ctx.exception))

    def test_invalid_verdict_mismatch_fails_closed(self) -> None:
        """Scorecard with forged PASS verdict below floors is rejected."""
        path = FIXTURES_DIR / "invalid_verdict_mismatch.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        with self.assertRaises(ValidationError) as ctx:
            Output1ScorecardV2.model_validate(data)
        self.assertIn("below mandatory floor", str(ctx.exception))

    def test_invalid_incomplete_coverage_fails_closed(self) -> None:
        """Scorecard claiming PASS with evaluated_span_coverage < 1.0 is rejected."""
        path = FIXTURES_DIR / "invalid_incomplete_coverage.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        with self.assertRaises(ValidationError) as ctx:
            Output1ScorecardV2.model_validate(data)
        self.assertIn("Incomplete evaluated span coverage", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 3. Trusted Judge Input Envelope Tests
    # -------------------------------------------------------------------------

    def test_valid_judge_input_envelope_computes_digest(self) -> None:
        """Valid JudgeInputEnvelope computes deterministic RFC-8259 digest."""
        path = FIXTURES_DIR / "valid_judge_input_envelope.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        envelope = JudgeInputEnvelope.model_validate(data)
        digest = envelope.compute_envelope_digest()
        self.assertEqual(len(digest), 64)

        # Invariance check: identical inputs yield identical digest
        digest2 = envelope.compute_envelope_digest()
        self.assertEqual(digest, digest2)

    def test_unverified_c0_receipt_rejects_envelope(self) -> None:
        """JudgeInputEnvelope with c0_receipt_verified=False fails closed."""
        path = FIXTURES_DIR / "valid_judge_input_envelope.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["c0_receipt_verified"] = False
        with self.assertRaises(ValidationError) as ctx:
            JudgeInputEnvelope.model_validate(data)
        self.assertIn("Unverified C0 title/roster receipt", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 4. Meeting X1, X2, X3 Contracts
    # -------------------------------------------------------------------------

    def test_x2_permits_pending_owner_under_v5_8(self) -> None:
        """X2 contract permits 'Unassigned (Pending Owner Assignment)' per V5.8 C-25."""
        recap_text = """### 1. Meeting Objective
Review technical architecture.
### 2. Action & Governance Ledger
| # | Workstream | Action Item | Owner | Target Date |
| 1 | Infrastructure | Deploy VPC peering template | Unassigned (Pending Owner Assignment) | 10/15/2026 |
"""
        ok, errs = MeetingXExitContract.validate_x2_recap_structure(recap_text)
        self.assertTrue(ok, f"X2 validation failed on permitted pending owner: {errs}")

    def test_x3_cross_output_coherence_detects_attendee_omission(self) -> None:
        """X3 contract detects active roster attendee missing from Output 03 entity_upserts."""
        f1_text = "Alice: We discussed VPC peering."
        f2_text = "Alice and Bob reviewed architecture."
        f3_data = {
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "person:alice", "type": "person", "name": "Alice"}
                ]
            }
        }
        metadata = {"attendees": ["Alice", "Bob"]}
        ok, errs = MeetingXExitContract.validate_x3_cross_output_coherence(f1_text, f2_text, f3_data, metadata)
        self.assertFalse(ok)
        self.assertTrue(any("Bob" in e for e in errs))

    def test_x3_cross_output_coherence_detects_deprecated_predicate(self) -> None:
        """X3 contract detects deprecated HAS_ROLE_IN and requires canonical HAS_ROLE."""
        f1_text = "Alice: I lead cloud architecture."
        f2_text = "Alice reviewed cloud architecture."
        f3_data = {
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "person:alice", "type": "person", "name": "Alice"}
                ],
                "relationship_signals": [
                    {"from_id": "person:alice", "relationship": "HAS_ROLE_IN", "to_id": "role:architect"}
                ]
            }
        }
        metadata = {"attendees": ["Alice"]}
        ok, errs = MeetingXExitContract.validate_x3_cross_output_coherence(f1_text, f2_text, f3_data, metadata)
        self.assertFalse(ok)
        self.assertTrue(any("HAS_ROLE_IN" in e for e in errs))

    def test_x3_cross_output_coherence_detects_quote_pointer_length_mismatch(self) -> None:
        """X3 contract detects quote pointer span start/end not matching exact_text length."""
        f1_text = "Alice: We need to provision the VPC networking layer today."
        f2_text = "Alice emphasized VPC networking."
        f3_data = {
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "person:alice", "type": "person", "name": "Alice"}
                ]
            },
            "evidence_index": {
                "ev-01": {
                    "quote_pointer": {
                        "start_offset": 10,
                        "end_offset": 25,  # span length 15
                        "exact_text": "short"  # length 5 -> mismatch!
                    }
                }
            }
        }
        metadata = {"attendees": ["Alice"]}
        ok, errs = MeetingXExitContract.validate_x3_cross_output_coherence(f1_text, f2_text, f3_data, metadata)
        self.assertFalse(ok)
        self.assertTrue(any("span length" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
