"""
tests/adversarial/test_learning_substrate_w1_adversarial.py

Adversarial and edge-case test suite for Wave 1: Trustworthy Learning Substrate.
Covers 4 mandatory categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload (empty cohorts, sub-threshold cohorts, empty runs)
2. Malformed input (tampered provenance digests, missing extensions, corrupted hashes)
3. Overflow & exhaustion (out-of-bounds parameter clamping, proposal limits)
4. Invariant bypass attempt (unapproved apply prevention, forged provenance rejection, zero-mutation violation catch)
5. Rollback & recovery (missing snapshot fails closed, accurate state restoration)
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import read_json, write_json
from dual_pipeline.learning_adapters import LivePipelineIntegrator
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.learning_engine_core.provenance import (
    PROVENANCE_EXTENSION_KEY,
    build_signed_provenance,
    verify_proposal_provenance,
)
from dual_pipeline.learning_engine_core.rollback import (
    execute_rollback,
    record_rollback_snapshot,
    snapshot_database_digests,
    verify_zero_graph_mutation,
)
from dual_pipeline.parameters import HITL_PARAMETER_BOUNDS


class LearningSubstrateW1AdversarialTests(unittest.TestCase):
    """Adversarial and edge-case verification for Wave 1 learning substrate."""

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

    # --- CATEGORY 1: BOUNDARY PAYLOADS ---

    def test_boundary_01_empty_cohort_aggregation(self) -> None:
        """Aggregation with zero cohorts produces zero proposals safely."""
        proposals = self.engine.aggregate_proposals([])
        self.assertEqual(proposals, [])

    def test_boundary_02_sub_threshold_cohorts_filtered(self) -> None:
        """Single-occurrence events below min_occurrences threshold are not promoted to cohorts."""
        self.integrator.record_adjudication_error(
            candidate_a="person:alpha",
            candidate_b="person:beta",
            conflict_type="identity_mismatch",
            run_id="run-single-001",
        )
        items = self.integrator.drain_to_cohort_items()
        cohorts = self.engine.analyze_failure_cohorts(items, min_occurrences=2)
        self.assertEqual(len(cohorts), 0)

    def test_boundary_03_empty_run_ids_fallback(self) -> None:
        """Cohorts with missing or empty run IDs default to fallback identifier."""
        fake_cohort = {
            "cohort_key": "outcome:retrieval_miss:coverage",
            "occurrence_count": 2,
            "run_ids": [],
            "items": [{"event_id": "evt-1"}, {"event_id": "evt-2"}],
        }
        prop = self.engine.generate_proposal_for_cohort(fake_cohort)
        self.assertIn("run-intake-learned", prop["run_ids"])
        validate_artifact(prop)

    # --- CATEGORY 2: MALFORMED INPUTS ---

    def test_malformed_01_tampered_provenance_digest(self) -> None:
        """Altering proposal contents invalidates cryptographic provenance digest."""
        fake_cohort = {
            "cohort_key": "outcome:decision_override:disambiguation",
            "occurrence_count": 2,
            "run_ids": ["run-101", "run-102"],
            "items": [{"event_id": "evt-1"}, {"event_id": "evt-2"}],
        }
        props = self.engine.aggregate_proposals([fake_cohort])
        self.assertEqual(len(props), 1)
        prop = props[0]

        # Valid initially
        is_valid, msg = verify_proposal_provenance(prop)
        self.assertTrue(is_valid, msg)

        # Tamper with the proposed change text
        prop["proposed_change"] = "MALICIOUS: Unrestricted privilege grant"
        is_valid_after, msg_after = verify_proposal_provenance(prop)
        self.assertFalse(is_valid_after)
        self.assertIn("Proposed change text in proposal does not match", msg_after)

    def test_malformed_02_missing_provenance_extension(self) -> None:
        """Proposal missing provenance extension fails verification."""
        prop = {"proposal_id": "prop-test-01", "extensions": {}}
        is_valid, msg = verify_proposal_provenance(prop)
        self.assertFalse(is_valid)
        self.assertIn("Missing required provenance extension", msg)

    def test_malformed_03_tampered_evidence_event_ids(self) -> None:
        """Tampering with evidence event IDs fails provenance verification."""
        fake_cohort = {
            "cohort_key": "outcome:adjudication_error:entity_resolution",
            "occurrence_count": 2,
            "run_ids": ["run-001"],
            "items": [{"event_id": "evt-a"}, {"event_id": "evt-b"}],
        }
        props = self.engine.aggregate_proposals([fake_cohort])
        prop = props[0]

        # Invalidate evidence IDs
        prop["evidence_event_ids"] = ["evt-forged-999"]
        is_valid, msg = verify_proposal_provenance(prop)
        self.assertFalse(is_valid)
        self.assertIn("Evidence event IDs in provenance do not match", msg)

    # --- CATEGORY 3: OVERFLOW & EXHAUSTION ---

    def test_overflow_01_parameter_bounds_enforcement_and_clamping(self) -> None:
        """Parameter proposals exceeding declared bounds are automatically clamped."""
        param_name = "hitl.bayesian_prior_strength"
        min_bound, max_bound, _ = HITL_PARAMETER_BOUNDS[param_name]

        fake_cohort = {
            "cohort_key": "outcome:decision_override:disambiguation",
            "occurrence_count": 3,
            "run_ids": ["run-p1", "run-p2", "run-p3"],
            "items": [{"event_id": "e1"}, {"event_id": "e2"}, {"event_id": "e3"}],
        }

        # Attempt to propose value way beyond max_bound (e.g. 999.0)
        target_overrides = {
            "outcome:decision_override:disambiguation": {
                "parameter_name": param_name,
                "proposed_value": 999.0,
            }
        }
        props = self.engine.aggregate_proposals([fake_cohort], target_overrides=target_overrides)
        self.assertEqual(len(props), 1)
        prop = props[0]

        calib_ext = prop["extensions"]["com.partner_advisory.nee.calibration"]
        self.assertAlmostEqual(calib_ext["proposed_value"], max_bound)

    def test_overflow_02_proposal_count_capping(self) -> None:
        """Aggregation respects max_proposals limit to prevent flooding."""
        cohorts = [
            {
                "cohort_key": f"outcome:failure:{i}",
                "occurrence_count": 2,
                "run_ids": [f"run-{i}"],
                "items": [{"event_id": f"e{i}-1"}, {"event_id": f"e{i}-2"}],
            }
            for i in range(15)
        ]
        props = self.engine.aggregate_proposals(cohorts, max_proposals=5)
        self.assertEqual(len(props), 5)

    # --- CATEGORY 4: INVARIANT BYPASS ATTEMPTS ---

    def test_invariant_bypass_01_unapproved_proposal_cannot_apply(self) -> None:
        """Attempting to apply an unapproved or synthetic approval proposal fails closed."""
        fake_cohort = {
            "cohort_key": "outcome:adjudication_error:entity_resolution",
            "occurrence_count": 2,
            "run_ids": ["run-001"],
            "items": [{"event_id": "evt-1"}, {"event_id": "evt-2"}],
        }
        props = self.engine.aggregate_proposals([fake_cohort])
        prop = props[0]

        # Missing approval receipt
        with self.assertRaises(PermissionError) as ctx:
            self.engine.apply_approved_proposal(prop, approval_receipt={})
        self.assertIn("Approval receipt is missing", str(ctx.exception))

        # Synthetic stop-hook / auto reviewer
        bad_receipt = {
            "receipt_id": "receipt-synthetic-001",
            "proposal_id": prop["proposal_id"],
            "proposal_digest": prop["canonical_digest"]["value"],
            "approved_by": "auto",
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }
        with self.assertRaises(PermissionError) as ctx:
            self.engine.apply_approved_proposal(prop, approval_receipt=bad_receipt)
        self.assertIn("Rejected non-human approval origin", str(ctx.exception))

    def test_invariant_bypass_02_tampered_provenance_blocks_application(self) -> None:
        """Attempting to apply a proposal with broken provenance fails closed."""
        fake_cohort = {
            "cohort_key": "outcome:adjudication_error:entity_resolution",
            "occurrence_count": 2,
            "run_ids": ["run-001"],
            "items": [{"event_id": "evt-1"}, {"event_id": "evt-2"}],
        }
        props = self.engine.aggregate_proposals([fake_cohort])
        prop = props[0]

        # Break provenance
        prop["extensions"][PROVENANCE_EXTENSION_KEY]["provenance_digest"] = "bad" * 16

        approval_receipt = {
            "receipt_id": "receipt-human-001",
            "proposal_id": prop["proposal_id"],
            "proposal_digest": prop["canonical_digest"]["value"],
            "approved_by": "Amit Ayer (Human Operator)",
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "user_command": "proceed with implementation",
        }
        with self.assertRaises(PermissionError) as ctx:
            self.engine.apply_approved_proposal(prop, approval_receipt=approval_receipt)
        self.assertIn("Provenance invalid", str(ctx.exception))

    def test_invariant_bypass_03_simulation_database_mutation_fails_closed(self) -> None:
        """Database mutation during dry-run simulation causes immediate failure."""
        fake_db = self.runtime_root / "test_knowledge_graph.sqlite"
        fake_db.write_bytes(b"INITIAL_GRAPH_STATE")

        # Snapshot before
        init_hashes = snapshot_database_digests(self.runtime_root)
        self.assertIn(str(fake_db), init_hashes)

        # Mutate database
        fake_db.write_bytes(b"CORRUPTED_MUTATED_STATE")
        curr_hashes = snapshot_database_digests(self.runtime_root)

        is_zero_mut, errors = verify_zero_graph_mutation(init_hashes, curr_hashes)
        self.assertFalse(is_zero_mut)
        self.assertTrue(any("Database modified" in err for err in errors))

    # --- CATEGORY 5: ROLLBACK & RECOVERY ---

    def test_rollback_01_missing_snapshot_fails_closed(self) -> None:
        """Rollback on nonexistent proposal ID raises FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            self.engine.rollback_proposal("nonexistent_proposal_id")

    def test_rollback_02_transactional_parameter_restoration(self) -> None:
        """Rollback restores pre-mutation parameter values cleanly."""
        param_file = self.runtime_root / "test_params.json"
        write_json(param_file, {"hitl.bayesian_prior_strength": 1.0})

        proposal = {
            "proposal_id": "prop-param-rollback-01",
            "proposal_type": "threshold",
        }
        pre_state = {
            "parameter_name": "hitl.bayesian_prior_strength",
            "previous_value": 1.0,
        }
        record_rollback_snapshot(self.proposals_dir, proposal, pre_state)

        # Mutate parameter file
        write_json(param_file, {"hitl.bayesian_prior_strength": 5.0})
        self.assertEqual(read_json(param_file)["hitl.bayesian_prior_strength"], 5.0)

        # Execute rollback
        receipt = self.engine.rollback_proposal(
            proposal_id="prop-param-rollback-01",
            parameter_path=param_file,
        )
        self.assertEqual(receipt["status"], "ROLLED_BACK")
        self.assertEqual(read_json(param_file)["hitl.bayesian_prior_strength"], 1.0)


if __name__ == "__main__":
    unittest.main()
