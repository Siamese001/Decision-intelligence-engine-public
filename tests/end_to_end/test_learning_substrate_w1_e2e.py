"""
tests/end_to_end/test_learning_substrate_w1_e2e.py

End-to-end qualification suite for Wave 1: Trustworthy Learning Substrate.
Exercises live pipeline failure recording, multi-cohort aggregation, signed provenance
verification, dry-run zero-mutation simulation, governed approval, and rollback.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import read_json, write_json
from dual_pipeline.learning_adapters import LivePipelineIntegrator
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.learning_engine_core.provenance import (
    PROVENANCE_EXTENSION_KEY,
    verify_proposal_provenance,
)


class LearningSubstrateW1E2ETests(unittest.TestCase):
    """E2E verification of the Wave 1 learning substrate."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.tmp_dir.name)
        self.proposals_dir = self.runtime_root / "proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        self.engine = ClosedLoopLearningEngine(
            runtime_root=self.runtime_root,
            proposals_dir=self.proposals_dir,
        )
        self.integrator = LivePipelineIntegrator(
            runtime_root=self.runtime_root,
            events_log_path=self.runtime_root / "ledger" / "live_outcomes.jsonl",
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_e2e_live_recording_to_governed_application_and_rollback(self) -> None:
        """Complete lifecycle: live failure capture -> aggregation -> simulation -> approval -> rollback."""
        # 1. Record live failures across multiple runs for an entity ambiguity
        self.integrator.record_adjudication_error(
            candidate_a="person:jon_smith",
            candidate_b="person:john_smith_partner_advisory",
            conflict_type="identity_mismatch",
            details={"confidence_a": 0.88, "confidence_b": 0.85},
            run_id="run-ingest-101",
        )
        self.integrator.record_adjudication_error(
            candidate_a="person:jon_smith",
            candidate_b="person:john_smith_partner_advisory",
            conflict_type="identity_mismatch",
            details={"confidence_a": 0.89, "confidence_b": 0.84},
            run_id="run-ingest-102",
        )

        # Record a repeated HITL decision override
        self.integrator.record_decision_override(
            decision_id="dec-strategy-001",
            recommended_option={"option_id": "opt-1", "label": "Fast Path", "confidence": 0.90},
            chosen_option={"option_id": "opt-2", "label": "Review Path", "confidence": 0.60},
            margin=0.30,
            run_id="run-decision-201",
        )
        self.integrator.record_decision_override(
            decision_id="dec-strategy-002",
            recommended_option={"option_id": "opt-1", "label": "Fast Path", "confidence": 0.91},
            chosen_option={"option_id": "opt-2", "label": "Review Path", "confidence": 0.61},
            margin=0.30,
            run_id="run-decision-202",
        )

        # 2. Drain live events into cohort analysis
        cohort_items = self.integrator.drain_to_cohort_items()
        self.assertEqual(len(cohort_items), 4)

        cohorts = self.engine.analyze_failure_cohorts(cohort_items, min_occurrences=2)
        self.assertGreaterEqual(len(cohorts), 2)

        # 3. Multi-cohort proposal aggregation
        target_overrides = {
            "outcome:adjudication_error:entity_resolution": {
                "from_id": "person:jon_smith",
                "to_id": "person:john_smith_partner_advisory",
            },
            "outcome:decision_override:disambiguation": {
                "parameter_name": "hitl.bayesian_prior_strength",
                "proposed_value": 5.0,
            },
        }
        proposals = self.engine.aggregate_proposals(cohorts, target_overrides=target_overrides)
        self.assertGreaterEqual(len(proposals), 2)

        # 4. Verify signed provenance on all aggregated proposals
        for prop in proposals:
            validate_artifact(prop)
            self.assertIn(PROVENANCE_EXTENSION_KEY, prop["extensions"])
            is_valid, msg = verify_proposal_provenance(prop)
            self.assertTrue(is_valid, f"Provenance verification failed: {msg}")

        # 5. Dry-run simulation with zero-mutation assertion
        alias_prop = next(p for p in proposals if p["proposal_type"] == "alias_addition")
        sim_res = self.engine.simulate_proposal(alias_prop)
        self.assertTrue(sim_res.success)
        self.assertEqual(sim_res.status, "SIMULATED_PASS")
        self.assertTrue(sim_res.details.get("zero_mutation_guaranteed"))

        # 6. Governed approval and application
        aliases_file = self.runtime_root / "aliases.json"
        write_json(aliases_file, {"schema_version": "1.0.0", "aliases": {}})

        approval_receipt = {
            "receipt_id": "receipt-human-001",
            "proposal_id": alias_prop["proposal_id"],
            "proposal_digest": alias_prop["canonical_digest"]["value"],
            "approved_by": "Amit Ayer (Human Operator)",
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "user_command": "proceed with implementation",
        }

        apply_receipt = self.engine.apply_approved_proposal(
            proposal=alias_prop,
            approval_receipt=approval_receipt,
            aliases_path=aliases_file,
            skip_decision_clearance=True,
        )
        self.assertEqual(apply_receipt["status"], "APPLIED")

        # Verify mutation took effect in target config
        aliases_data = read_json(aliases_file)
        self.assertEqual(aliases_data["aliases"].get("person:jon_smith"), "person:john_smith_partner_advisory")

        # 7. Rollback verification
        rollback_receipt = self.engine.rollback_proposal(
            proposal_id=alias_prop["proposal_id"],
            aliases_path=aliases_file,
        )
        self.assertEqual(rollback_receipt["status"], "ROLLED_BACK")

        # Verify alias was restored (removed from dictionary)
        restored_aliases = read_json(aliases_file)
        self.assertNotIn("person:jon_smith", restored_aliases["aliases"])


if __name__ == "__main__":
    unittest.main()
