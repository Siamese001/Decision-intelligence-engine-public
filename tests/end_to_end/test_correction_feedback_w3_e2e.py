"""
tests/end_to_end/test_correction_feedback_w3_e2e.py

End-to-end qualification suite for Wave 3: Correction Feedback.
Exercises human operator correction capture (speaker attributions, entity resolution overrides),
multi-confirmation cohort aggregation, holdout replay evaluation, false merge prevention,
and governed proposal application.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import read_json, write_json
from dual_pipeline.learning_adapters import (
    CorrectionFeedbackIngestor,
    OperatorCorrection,
)
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.learning_engine_core.provenance import (
    PROVENANCE_EXTENSION_KEY,
    verify_proposal_provenance,
)
from dual_pipeline.system_learning import (
    HoldoutCase,
    HoldoutEvaluationReport,
    HoldoutReplayEvaluator,
)


class CorrectionFeedbackW3E2ETests(unittest.TestCase):
    """E2E verification of operator correction feedback and holdout replay evaluation."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.tmp_dir.name)
        self.proposals_dir = self.runtime_root / "proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        self.engine = ClosedLoopLearningEngine(
            runtime_root=self.runtime_root,
            proposals_dir=self.proposals_dir,
        )
        self.ingestor = CorrectionFeedbackIngestor(
            runtime_root=self.runtime_root,
            min_confirmations=2,
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_e2e_speaker_correction_to_holdout_validated_proposal(self) -> None:
        """Capture speaker corrections, evaluate on holdout set, and verify no false merge regression."""
        # 1. Capture confirmed speaker corrections across two sessions
        c1 = self.ingestor.record_speaker_correction(
            raw_speaker="SPEAKER_01",
            corrected_speaker="Alice Chen (VP Eng)",
            operator_id="Amit Ayer",
            session_id="meet-20261001-eng",
            rationale="Verified voice signature matches Alice Chen",
        )
        self.assertEqual(c1.category, "speaker_attribution")

        # Below threshold: 1 confirmation does not meet min_confirmations=2
        cohorts_1 = self.ingestor.aggregate_cohorts(min_confirmations=2)
        self.assertEqual(len(cohorts_1), 0)

        # Second confirmation from another session
        self.ingestor.record_speaker_correction(
            raw_speaker="SPEAKER_01",
            corrected_speaker="Alice Chen (VP Eng)",
            operator_id="Amit Ayer",
            session_id="meet-20261002-arch",
            rationale="Confirmed Alice Chen speaking",
        )
        cohorts_2 = self.ingestor.aggregate_cohorts(min_confirmations=2)
        self.assertEqual(len(cohorts_2), 1)
        self.assertEqual(cohorts_2[0].confirmations, 2)

        # 2. Setup historical holdout cases
        holdout_cases = [
            HoldoutCase(
                case_id="case-spk-01",
                category="speaker_attribution",
                input_text="SPEAKER_01: Let's finalize the Q4 roadmap.",
                raw_label="SPEAKER_01",
                ground_truth_label="Alice Chen (VP Eng)",
                negative_labels=["Bob Miller", "Charlie Davis"],
            ),
            HoldoutCase(
                case_id="case-spk-02",
                category="speaker_attribution",
                input_text="SPEAKER_01: I will approve the budget variance.",
                raw_label="SPEAKER_01",
                ground_truth_label="Alice Chen (VP Eng)",
                negative_labels=["Bob Miller", "Charlie Davis"],
            ),
            HoldoutCase(
                case_id="case-spk-03",
                category="speaker_attribution",
                input_text="SPEAKER_02: Cloud architecture review complete.",
                raw_label="SPEAKER_02",
                ground_truth_label="Bob Miller",
                negative_labels=["Alice Chen (VP Eng)", "Charlie Davis"],
            ),
        ]
        evaluator = HoldoutReplayEvaluator(holdout_cases)

        # 3. Synthesize proposals with holdout validation
        proposals, reports = self.ingestor.synthesize_proposals(
            evaluator=evaluator,
            holdout_cases=holdout_cases,
            min_confirmations=2,
        )

        self.assertEqual(len(proposals), 1)
        self.assertEqual(len(reports), 1)

        prop = proposals[0]
        report = reports[0]

        # Verify holdout evaluation metrics
        self.assertEqual(report.verdict, "PASS")
        self.assertGreater(report.accuracy_delta, 0)
        self.assertFalse(report.false_merge_regression)
        self.assertEqual(report.false_merge_rate_after, 0.0)

        # Verify cryptographic provenance binding
        validate_artifact(prop)
        self.assertIn(PROVENANCE_EXTENSION_KEY, prop["extensions"])
        is_prov_valid, prov_msg = verify_proposal_provenance(prop)
        self.assertTrue(is_prov_valid, prov_msg)

        # 4. Dry-run simulation guarantees zero graph mutations
        sim_res = self.engine.simulate_proposal(prop)
        self.assertTrue(sim_res.success)
        self.assertEqual(sim_res.status, "SIMULATED_PASS")
        self.assertTrue(sim_res.details.get("zero_mutation_guaranteed"))

        # 5. Apply with authentic operator approval
        aliases_file = self.runtime_root / "learned_aliases.json"
        write_json(aliases_file, {"schema_version": "1.0.0", "aliases": {}})

        approval_receipt = {
            "receipt_id": "receipt-human-speaker-001",
            "proposal_id": prop["proposal_id"],
            "proposal_digest": prop["canonical_digest"]["value"],
            "approved_by": "Amit Ayer (Human Operator)",
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "user_command": "proceed with implementation",
        }
        apply_receipt = self.engine.apply_approved_proposal(
            proposal=prop,
            approval_receipt=approval_receipt,
            aliases_path=aliases_file,
            skip_decision_clearance=True,
        )
        self.assertEqual(apply_receipt["status"], "APPLIED")
        aliases = read_json(aliases_file)["aliases"]
        self.assertEqual(aliases.get("SPEAKER_01"), "Alice Chen (VP Eng)")

    def test_e2e_entity_resolution_override_lifecycle(self) -> None:
        """Capture entity overrides, verify holdout gain, and ensure zero unapproved writes."""
        # 1. Capture confirmed entity overrides
        self.ingestor.record_entity_override(
            ambiguous_entity="person:jon_smith",
            canonical_entity="person:john_smith_partner_advisory",
            operator_id="Amit Ayer",
            session_id="meet-ent-01",
        )
        self.ingestor.record_entity_override(
            ambiguous_entity="person:jon_smith",
            canonical_entity="person:john_smith_partner_advisory",
            operator_id="Amit Ayer",
            session_id="meet-ent-02",
        )

        holdout_cases = [
            HoldoutCase(
                case_id="case-ent-01",
                category="entity_resolution",
                input_text="person:jon_smith",
                raw_label="person:jon_smith",
                ground_truth_label="person:john_smith_partner_advisory",
                negative_labels=["person:john_smith_external"],
            ),
        ]
        evaluator = HoldoutReplayEvaluator(holdout_cases)

        proposals, reports = self.ingestor.synthesize_proposals(
            evaluator=evaluator,
            holdout_cases=holdout_cases,
        )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(reports[0].verdict, "PASS")
        self.assertEqual(reports[0].accuracy_after, 1.0)


if __name__ == "__main__":
    unittest.main()
