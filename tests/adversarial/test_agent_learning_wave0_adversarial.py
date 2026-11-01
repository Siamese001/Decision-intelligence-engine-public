"""
tests/adversarial/test_agent_learning_wave0_adversarial.py

Adversarial and edge-case test suite for Wave 0: Agent Learning & Autonomy Opportunities.
Covers 3 mandatory categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload (empty maps, empty lists, boundary confidence scores)
2. Malformed input (invalid outcome_types, pattern violations, missing required keys)
3. Invariant bypass attempt (synthetic stop-hook approvals, auto_apply tampering, unsupported proposal simulation)
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from dual_pipeline.contracts import create_artifact, validate_artifact
from dual_pipeline.core import read_json, seal_artifact, write_json
from dual_pipeline.hitl_governance import validate_approval_origin
from dual_pipeline.learning_adapters import AgentOutcomeAdapter, AgentOutcomeEvent, OutcomeType
from dual_pipeline.learning_engine import ClosedLoopLearningEngine


class AgentLearningWave0AdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.runtime_root = self.tmp_path / "runtime"
        self.proposals_dir = self.tmp_path / "proposals"
        self.engine = ClosedLoopLearningEngine(
            runtime_root=self.runtime_root,
            proposals_dir=self.proposals_dir,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # --- CATEGORY 1: BOUNDARY PAYLOAD ---

    def test_boundary_01_empty_observed_outcome_and_minimal_fields(self) -> None:
        """Verify handling of empty observed outcome dictionary and minimal fields."""
        event = AgentOutcomeAdapter.create_outcome_event(
            outcome_type=OutcomeType.ADJUDICATION_ERROR,
            source_agent="AdjudicationJudgeAgent",
            stage="L2",
            category="boundary_test",
            observed_outcome={},
        )
        self.assertEqual(event.observed_outcome, {})
        art = AgentOutcomeAdapter.to_contract_artifact(event)
        validate_artifact(art)

    def test_boundary_02_confidence_clamping_and_extremes(self) -> None:
        """Verify that confidence is properly bounded to [0.0, 1.0] and edge values are valid."""
        # Test negative clamped to 0.0
        evt_low = AgentOutcomeAdapter.create_outcome_event(
            outcome_type=OutcomeType.RETRIEVAL_MISS,
            source_agent="RetrievalCoordinator",
            stage="L1",
            category="coverage",
            observed_outcome={"query": "test"},
            confidence=-0.5,
        )
        self.assertEqual(evt_low.confidence, 0.0)

        # Test >1.0 clamped to 1.0
        evt_high = AgentOutcomeAdapter.create_outcome_event(
            outcome_type=OutcomeType.RETRIEVAL_MISS,
            source_agent="RetrievalCoordinator",
            stage="L1",
            category="coverage",
            observed_outcome={"query": "test"},
            confidence=1.75,
        )
        self.assertEqual(evt_high.confidence, 1.0)

        art_low = AgentOutcomeAdapter.to_contract_artifact(evt_low)
        art_high = AgentOutcomeAdapter.to_contract_artifact(evt_high)
        validate_artifact(art_low)
        validate_artifact(art_high)

    def test_boundary_03_empty_conflicts_and_missing_requirements_lists(self) -> None:
        """Verify builder methods gracefully handle empty entity conflict and requirement lists."""
        evt_adj = AgentOutcomeAdapter.build_adjudication_error_event(
            conflicting_entities=[],
            reason="",
        )
        self.assertEqual(evt_adj.observed_outcome["conflicting_entities"], [])
        art_adj = AgentOutcomeAdapter.to_contract_artifact(evt_adj)
        validate_artifact(art_adj)

        evt_ret = AgentOutcomeAdapter.build_retrieval_miss_event(
            query_text="",
            missing_requirements=[],
        )
        self.assertEqual(evt_ret.observed_outcome["missing_requirements"], [])
        art_ret = AgentOutcomeAdapter.to_contract_artifact(evt_ret)
        validate_artifact(art_ret)

    # --- CATEGORY 2: MALFORMED INPUT ---

    def test_malformed_01_invalid_outcome_type_raises_value_error(self) -> None:
        """Verify that invalid outcome_type raises ValueError immediately."""
        with self.assertRaises(ValueError) as ctx:
            AgentOutcomeAdapter.create_outcome_event(
                outcome_type="unauthorized_outcome_type",
                source_agent="TestAgent",
                stage="L1",
                category="test",
                observed_outcome={},
            )
        self.assertIn("Invalid outcome_type", str(ctx.exception))

    def test_malformed_02_invalid_trace_id_pattern_fails_contract_validation(self) -> None:
        """Verify that a malformed trace_id pattern is rejected by schema validator."""
        event = AgentOutcomeAdapter.create_outcome_event(
            outcome_type=OutcomeType.SPEAKER_CORRECTION,
            source_agent="RosterReconcilerAgent",
            stage="L1",
            category="speaker",
            observed_outcome={"assigned": "S1"},
            trace_id="invalid-non-hex-trace-id-too-short",
        )
        with self.assertRaises(Exception) as ctx:
            AgentOutcomeAdapter.to_contract_artifact(event)
        self.assertIn("SCHEMA_PATTERN", str(ctx.exception))

    def test_malformed_03_missing_required_fields_fails_validation(self) -> None:
        """Verify that an artifact missing required fields fails validation."""
        incomplete_payload = seal_artifact({
            "contract_id": "urn:nee:dual-pipeline:agent-outcome-event:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": "art-incomplete-001",
            "extensions": {},
        })
        with self.assertRaises(Exception) as ctx:
            validate_artifact(incomplete_payload)
        self.assertIn("SCHEMA_REQUIRED", str(ctx.exception))

    # --- CATEGORY 3: INVARIANT BYPASS ATTEMPTS ---

    def test_bypass_01_unsupported_proposal_simulation_fails_closed(self) -> None:
        """Verify that unknown/unsupported proposal types cannot simulate a PASS."""
        fake_proposal = {
            "proposal_id": "prop-bypass-001",
            "proposal_type": "arbitrary_unsupported_mutation",
            "status": "proposed",
            "problem": "Bypass attempt",
            "proposed_change": "Direct DB mutation",
            "evaluation_plan": ["None"],
            "risk": "high",
            "approval_required": True,
            "auto_apply": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "extensions": {},
        }
        result = self.engine.simulate_proposal(fake_proposal)
        self.assertFalse(result.success)
        self.assertEqual(result.status, "SIMULATED_FAIL")
        self.assertIn("no replay simulator", result.details.get("error", "").lower())

    def test_bypass_02_synthetic_stop_hook_approval_origin_rejected(self) -> None:
        """Verify that synthetic auto-approval markers are rejected fail-closed."""
        synthetic_receipts = [
            {
                "receipt_id": "rcpt-synth-01",
                "approved_by": "IDE Stop Hook [auto-approved]",
                "user_command": "plan only",
            },
            {
                "receipt_id": "rcpt-synth-02",
                "approved_by": "automated-pipeline-reviewer",
                "source": "synthetic_pipeline",
            },
            {
                "receipt_id": "rcpt-synth-03",
                "approved_by": "system_message auto_approval",
                "user_command": "do not implement",
            },
        ]
        for receipt in synthetic_receipts:
            is_valid, reason = validate_approval_origin(receipt)
            self.assertFalse(is_valid)

    def test_bypass_03_proposal_digest_mismatch_rejected_on_apply(self) -> None:
        """Verify that an approval receipt with tampered proposal digest fails verification."""
        proposal = {
            "proposal_id": "prop-tamper-001",
            "proposal_type": "alias_addition",
            "canonical_digest": {"value": "real_digest_1234567890123456789012345678901234567890123456789012345678901234"},
            "extensions": {"com.partner_advisory.nee.learning": {"from_id": "a", "to_id": "b"}},
        }
        tampered_receipt = {
            "receipt_id": "rcpt-tamper-01",
            "proposal_id": "prop-tamper-001",
            "proposal_digest": "tampered_digest_00000000000000000000000000000000000000000000000000000000000",
            "approved_by": "Operator (Human)",
            "user_command": "proceed with implementation",
        }
        with self.assertRaises(PermissionError) as ctx:
            self.engine.apply_approved_proposal(proposal, tampered_receipt)
        self.assertIn("Cannot apply proposal", str(ctx.exception))
