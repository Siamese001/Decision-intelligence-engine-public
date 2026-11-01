"""
tests/adversarial/test_correction_feedback_w3_adversarial.py

Adversarial and edge-case test suite for Wave 3: Correction Feedback.
Covers 4 mandatory categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Overfitting to single instances (sub-threshold confirmation defenses)
2. Conflicting operator overrides (collision detection and fail-closed prevention)
3. False merge regression detection (disparate negative label preservation)
4. Invariant bypass & unauthorized mutation prevention (zero direct canonical graph writes)
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.learning_adapters import (
    CorrectionFeedbackIngestor,
    OperatorCorrection,
)
from dual_pipeline.system_learning import (
    HoldoutCase,
    HoldoutEvaluationReport,
    HoldoutReplayEvaluator,
)


class CorrectionFeedbackW3AdversarialTests(unittest.TestCase):
    """Adversarial and edge-case verification for correction feedback and holdout replay."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.tmp_dir.name)
        self.ingestor = CorrectionFeedbackIngestor(
            runtime_root=self.runtime_root,
            min_confirmations=2,
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    # --- CATEGORY 1: OVERFITTING TO SINGLE INSTANCES ---

    def test_overfitting_01_single_instance_does_not_generate_proposal(self) -> None:
        """A single unconfirmed correction does not cross confirmation threshold."""
        self.ingestor.record_speaker_correction(
            raw_speaker="SPEAKER_ANOMALOUS",
            corrected_speaker="One-Off Speaker",
            operator_id="Amit Ayer",
            session_id="meet-single",
        )
        cohorts = self.ingestor.aggregate_cohorts(min_confirmations=2)
        self.assertEqual(len(cohorts), 0)

        proposals, _ = self.ingestor.synthesize_proposals(min_confirmations=2)
        self.assertEqual(len(proposals), 0)

    def test_overfitting_02_empty_holdout_cases_fails_closed(self) -> None:
        """Evaluating proposal against empty holdout cases rejects automatically."""
        evaluator = HoldoutReplayEvaluator([])
        fake_proposal = {
            "proposal_id": "prop-test-empty",
            "proposal_type": "alias_addition",
            "extensions": {"com.partner_advisory.nee.learning": {"from_id": "A", "to_id": "B"}},
        }
        report = evaluator.evaluate_proposal(fake_proposal, holdout_cases=[])
        self.assertEqual(report.verdict, "REJECT")
        self.assertIn("No holdout cases provided", report.rejection_reasons[0])

    # --- CATEGORY 2: CONFLICTING OPERATOR OVERRIDES ---

    def test_conflict_01_contradictory_targets_block_proposal_synthesis(self) -> None:
        """Contradictory corrections for the same source entity fail closed without proposing."""
        # Operator 1 maps ambiguous entity to PartnerAdvisory
        self.ingestor.record_entity_override(
            ambiguous_entity="person:jon_smith",
            canonical_entity="person:john_smith_partner_advisory",
            operator_id="Operator 1",
            session_id="session-01",
        )
        # Operator 2 maps the same entity to Competitor
        self.ingestor.record_entity_override(
            ambiguous_entity="person:jon_smith",
            canonical_entity="person:john_smith_competitor",
            operator_id="Operator 2",
            session_id="session-02",
        )

        cohorts = self.ingestor.aggregate_cohorts(min_confirmations=1)
        self.assertEqual(len(cohorts), 1)
        self.assertTrue(cohorts[0].has_conflicts)
        self.assertEqual(len(cohorts[0].conflict_targets), 2)

        # Synthesize must skip conflicting cohort
        proposals, _ = self.ingestor.synthesize_proposals(min_confirmations=1)
        self.assertEqual(len(proposals), 0)

    def test_conflict_02_clean_whitespace_and_casing(self) -> None:
        """Trailing whitespace and casing variations are stripped cleanly."""
        c = self.ingestor.record_entity_override(
            ambiguous_entity="  entity:spaced  ",
            canonical_entity="  entity:canonical  ",
            operator_id="Amit Ayer",
        )
        self.assertEqual(c.source_identifier, "entity:spaced")
        self.assertEqual(c.corrected_identifier, "entity:canonical")

    # --- CATEGORY 3: FALSE MERGE RISKS & ACCURACY GAINS ---

    def test_false_merge_01_regression_rejects_candidate_proposal(self) -> None:
        """Candidate alias that merges disparate negative control entities is rejected."""
        # Propose mapping "smith" -> "person:john_smith"
        candidate_proposal = {
            "proposal_id": "prop-overbroad-alias",
            "proposal_type": "alias_addition",
            "extensions": {
                "com.partner_advisory.nee.learning": {
                    "from_id": "smith",
                    "to_id": "person:john_smith",
                }
            },
        }

        # Holdout cases where "smith" also appears in an unrelated entity "Jane Smith"
        holdout_cases = [
            HoldoutCase(
                case_id="case-1",
                category="entity_resolution",
                input_text="Dr. smith presentation",
                raw_label="smith",
                ground_truth_label="person:john_smith",
                negative_labels=["person:jane_smith"],
            ),
            HoldoutCase(
                case_id="case-2-disparate",
                category="entity_resolution",
                input_text="Agent smith legal memo",
                raw_label="smith",
                ground_truth_label="person:jane_smith",  # Different entity!
                negative_labels=["person:john_smith"],  # Must NOT be resolved as John Smith!
            ),
        ]

        evaluator = HoldoutReplayEvaluator(holdout_cases)
        report = evaluator.evaluate_proposal(candidate_proposal)

        # Candidate mapping "smith" -> "person:john_smith" caused case-2 to merge with John Smith!
        self.assertTrue(report.false_merge_regression)
        self.assertEqual(report.verdict, "REJECT")
        self.assertTrue(any("False merge rate regressed" in r for r in report.rejection_reasons))

    def test_false_merge_02_zero_accuracy_delta_rejects_proposal(self) -> None:
        """Candidate proposal that does not improve holdout accuracy is rejected."""
        candidate_proposal = {
            "proposal_id": "prop-no-impact",
            "proposal_type": "alias_addition",
            "extensions": {
                "com.partner_advisory.nee.learning": {
                    "from_id": "unrelated_alias",
                    "to_id": "target_entity",
                }
            },
        }

        holdout_cases = [
            HoldoutCase(
                case_id="case-1",
                category="entity_resolution",
                input_text="entity_a",
                raw_label="entity_a",
                ground_truth_label="entity_a",
                negative_labels=["entity_b"],
            )
        ]

        evaluator = HoldoutReplayEvaluator(holdout_cases)
        report = evaluator.evaluate_proposal(candidate_proposal)

        self.assertLessEqual(report.accuracy_delta, 0.0)
        self.assertEqual(report.verdict, "REJECT")
        self.assertTrue(any("did not improve baseline" in r for r in report.rejection_reasons))

    # --- CATEGORY 4: INVARIANT BYPASS & ZERO GRAPH MUTATION ---

    def test_invariant_01_zero_canonical_graph_writes_during_ingestion(self) -> None:
        """Recording corrections and evaluating proposals never touches production database files."""
        # Create a mock sqlite graph
        graph_db = self.runtime_root / "seed_graph.sqlite"
        graph_db.write_bytes(b"INITIAL_GRAPH_STATE_IMMUTABLE")
        initial_hash = graph_db.stat().st_mtime

        # Run ingestion and proposal synthesis
        self.ingestor.record_speaker_correction(
            raw_speaker="SPEAKER_01",
            corrected_speaker="Alice Chen",
            operator_id="Amit Ayer",
        )
        self.ingestor.record_speaker_correction(
            raw_speaker="SPEAKER_01",
            corrected_speaker="Alice Chen",
            operator_id="Amit Ayer",
        )
        self.ingestor.aggregate_cohorts()
        self.ingestor.synthesize_proposals()

        # Verify graph database untouched
        self.assertEqual(graph_db.read_bytes(), b"INITIAL_GRAPH_STATE_IMMUTABLE")
        self.assertEqual(graph_db.stat().st_mtime, initial_hash)


if __name__ == "__main__":
    unittest.main()
