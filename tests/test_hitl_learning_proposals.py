"""tests/test_hitl_learning_proposals.py

Tests for Wave 4: System Learning HITL Proposal Category and Scanning.
Verifies HITL parameter bounding, hitl_confidence proposal generation,
Brier score dry-run simulation, and govern_learning_proposals.py hitl-scan CLI.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import write_json
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.parameters import (
    HITL_PARAMETER_BOUNDS,
    get_hitl_uncertainty_display,
    validate_hitl_parameter_bounds,
)
from tools.govern_learning_proposals import cmd_hitl_scan, cmd_list, cmd_simulate


class HITLLearningProposalsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.runtime_root = self.tmp_path / "runtime"
        self.ledger_dir = self.runtime_root / "ledger"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self.proposals_dir = self.tmp_path / "proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)

        self.engine = ClosedLoopLearningEngine(
            runtime_root=self.runtime_root,
            proposals_dir=self.proposals_dir,
        )

        self.hitl_path = self.ledger_dir / "hitl_decision_ledger.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_01_hitl_parameter_bounds_and_uncertainty_display(self) -> None:
        """Verify parameter bounds enforcement and default accessors."""
        self.assertIn(get_hitl_uncertainty_display(), ("always", "sparse_only"))

        # Test valid values
        self.assertTrue(validate_hitl_parameter_bounds("hitl.bayesian_prior_strength", 4.0))
        self.assertTrue(validate_hitl_parameter_bounds("hitl.bayesian_prior_strength", 1.0))
        self.assertTrue(validate_hitl_parameter_bounds("hitl.bayesian_prior_strength", 10.0))
        self.assertTrue(validate_hitl_parameter_bounds("hitl.ambiguity_margin_threshold", 0.15))
        self.assertTrue(validate_hitl_parameter_bounds("hitl.human_feedback_weight", 1.0))

        # Test out of bounds values
        self.assertFalse(validate_hitl_parameter_bounds("hitl.bayesian_prior_strength", 0.5))
        self.assertFalse(validate_hitl_parameter_bounds("hitl.bayesian_prior_strength", 15.0))
        self.assertFalse(validate_hitl_parameter_bounds("hitl.ambiguity_margin_threshold", 0.50))
        self.assertFalse(validate_hitl_parameter_bounds("unknown_parameter", 1.0))

        # Check all declared parameters have valid defaults within their bounds
        for param, (lo, hi, default) in HITL_PARAMETER_BOUNDS.items():
            self.assertLessEqual(lo, default, f"{param} default below min")
            self.assertGreaterEqual(hi, default, f"{param} default above max")

    def test_02_proposal_generation_for_hitl_confidence_cohort(self) -> None:
        """Verify generation of contract-valid hitl_confidence proposals."""
        cohort = {
            "cohort_key": "hitl_override:speaker_disambiguation",
            "occurrence_count": 8,
            "run_ids": ["run-intake-001", "run-intake-002"],
            "items": [
                {"decision_id": f"dec-0{i}", "category": "speaker_disambiguation", "outcome_type": "OVERRIDDEN"}
                for i in range(8)
            ],
        }

        proposal = self.engine.generate_proposal_for_cohort(
            cohort,
            proposal_type="hitl_confidence",
            target_override={
                "parameter_name": "hitl.bayesian_prior_strength",
                "proposed_value": 6.0,
                "current_value": 4.0,
                "category": "speaker_disambiguation",
            },
        )

        self.assertEqual(proposal["contract_id"], "urn:nee:dual-pipeline:learning-change-proposal:1.0.0")
        self.assertEqual(proposal["schema_version"], "1.0.0")
        self.assertEqual(proposal["proposal_type"], "hitl_confidence")
        self.assertEqual(proposal["status"], "proposed")
        self.assertTrue(proposal["approval_required"])
        self.assertFalse(proposal["auto_apply"])
        self.assertIn("canonical_digest", proposal)
        self.assertEqual(proposal["canonical_digest"]["algorithm"], "sha256")

        ext = proposal.get("extensions", {}).get("com.partner_advisory.nee.learning", {})
        self.assertEqual(ext.get("target_type"), "hitl_confidence")
        self.assertEqual(ext.get("parameter_name"), "hitl.bayesian_prior_strength")
        self.assertEqual(ext.get("proposed_value"), 6.0)

        saved_path = self.proposals_dir / f"{proposal['proposal_id']}.json"
        self.assertTrue(saved_path.is_file())

    def test_03_hitl_confidence_dry_run_simulation(self) -> None:
        """Verify dry-run simulation of hitl_confidence proposals with Brier score calculations."""
        # Seed mock decisions with some overrides
        mock_decisions = [
            {
                "decision_id": f"dec-aff-{i}",
                "category": "speaker_disambiguation",
                "outcome_type": "AFFIRMED",
                "confidence": 0.85,
            }
            for i in range(6)
        ] + [
            {
                "decision_id": f"dec-ovr-{i}",
                "category": "speaker_disambiguation",
                "outcome_type": "OVERRIDDEN",
                "confidence": 0.70,
            }
            for i in range(4)
        ]
        write_json(self.hitl_path, {"decisions": mock_decisions})

        cohort = {
            "cohort_key": "hitl_override:speaker_disambiguation",
            "occurrence_count": 4,
            "run_ids": ["run-01"],
            "items": mock_decisions,
        }

        # Valid proposal simulation
        proposal = self.engine.generate_proposal_for_cohort(
            cohort,
            proposal_type="hitl_confidence",
            target_override={
                "parameter_name": "hitl.bayesian_prior_strength",
                "proposed_value": 6.0,
                "current_value": 4.0,
                "category": "speaker_disambiguation",
            },
        )
        sim_res = self.engine.simulate_proposal(proposal)
        self.assertTrue(sim_res.success)
        self.assertEqual(sim_res.status, "SIMULATED_PASS")
        self.assertIn("current_brier_score", sim_res.details)
        self.assertIn("simulated_brier_score", sim_res.details)
        self.assertEqual(sim_res.details["bounds_check"], "VALID")

        # Out-of-bounds proposal simulation fails
        oob_proposal = self.engine.generate_proposal_for_cohort(
            cohort,
            proposal_type="hitl_confidence",
            target_override={
                "parameter_name": "hitl.bayesian_prior_strength",
                "proposed_value": 25.0,  # exceeds max 10.0
                "category": "speaker_disambiguation",
            },
        )
        sim_oob = self.engine.simulate_proposal(oob_proposal)
        self.assertFalse(sim_oob.success)
        self.assertEqual(sim_oob.status, "SIMULATED_FAIL")
        self.assertIn("outside governed bounds", sim_oob.details.get("error", ""))

    def test_04_cmd_hitl_scan_cli_lifecycle(self) -> None:
        """Verify end-to-end hitl-scan CLI execution, proposal synthesis, and listing."""
        # Seed 10 decisions: 6 overridden (60%), 4 affirmed in 'entity_resolution'
        mock_decisions = [
            {
                "decision_id": f"dec-o-{i}",
                "category": "entity_resolution",
                "outcome_type": "OVERRIDDEN",
                "confidence": 0.65,
                "run_id": f"run-{i}",
            }
            for i in range(6)
        ] + [
            {
                "decision_id": f"dec-a-{i}",
                "category": "entity_resolution",
                "outcome_type": "AFFIRMED",
                "confidence": 0.90,
                "run_id": f"run-aff-{i}",
            }
            for i in range(4)
        ]
        write_json(self.hitl_path, {"decisions": mock_decisions})

        # Run hitl-scan
        ret = cmd_hitl_scan(
            self.engine,
            self.proposals_dir,
            ledger_path=self.hitl_path,
            override_threshold=0.20,
            min_decisions=5,
            generate_proposals=True,
        )
        self.assertEqual(ret, 0)

        # Verify proposal was created
        proposal_files = list(self.proposals_dir.glob("*.json"))
        self.assertEqual(len(proposal_files), 1)

        # Verify cmd_list displays it
        list_ret = cmd_list(self.engine, self.proposals_dir)
        self.assertEqual(list_ret, 0)

        # Verify simulation succeeds on generated proposal
        sim_ret = cmd_simulate(self.engine, self.proposals_dir, proposal_files[0].stem)
        self.assertEqual(sim_ret, 0)


if __name__ == "__main__":
    unittest.main()
