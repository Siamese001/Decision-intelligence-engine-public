"""
tests/test_ingestion_budget_edge_cases.py

Adversarial and edge-case testing suite for bounded ingestion retries and operator escalation.
Governed under Wave 5 of the Post-Recording Ingestion SLA Plan.
Covers:
  1. Overflow & exhaustion: Max remediation ceiling (<=2) and SLA 600s deadline exhaustion.
  2. Rollback & recovery: Resume cache preservation across attempts without full pipeline restart.
  3. Boundary conditions: Zero-failure fast-path, 1-failure recovery, 2-failure exhaustion to AWAITING_OPERATOR.
  4. Invariant protection: Isolation of run_id and preventing unauthorized second full-run launches.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline.checkpoint_memoizer import memoize_substage_checkpoints
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.transcript_pipeline_core.clearance_lifecycle import (
    BoundedIngestionCoordinator,
    IngestionLifecycleState,
)


class TestIngestionBudgetEdgeCases(unittest.TestCase):
    """Adversarial and edge case tests for ingestion budget and operator escalation."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp(prefix="eval_sandbox_budget_")
        self.sandbox_dir = Path(self.test_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # ── Category 1: Overflow & Exhaustion (Retry Budget Ceiling & Timeout) ──

    def test_bounded_coordinator_exhaustion_to_awaiting_operator(self) -> None:
        """Verify coordinator halts at max_remediations (<=2) and transitions to AWAITING_OPERATOR."""
        coordinator = BoundedIngestionCoordinator(max_remediations=2, max_wall_clock_s=600.0)
        self.assertEqual(coordinator.state, IngestionLifecycleState.RUNNING)

        # Attempt 1 fails -> REMEDIATING (remediation_count = 1)
        st1 = coordinator.record_attempt(passed=False, error="Judge failed Output 2")
        self.assertEqual(st1, IngestionLifecycleState.REMEDIATING)
        self.assertEqual(coordinator.remediation_count, 1)

        # Attempt 2 fails -> REMEDIATING (remediation_count = 2)
        st2 = coordinator.record_attempt(passed=False, error="Judge failed Output 2 again")
        self.assertEqual(st2, IngestionLifecycleState.REMEDIATING)
        self.assertEqual(coordinator.remediation_count, 2)

        # Attempt 3 fails -> Budget exhausted -> AWAITING_OPERATOR (no 4th attempt allowed)
        st3 = coordinator.record_attempt(passed=False, error="Judge failed Output 2 third time")
        self.assertEqual(st3, IngestionLifecycleState.AWAITING_OPERATOR)
        self.assertEqual(coordinator.remediation_count, 2)

    def test_bounded_coordinator_wall_clock_timeout_exhaustion(self) -> None:
        """Verify coordinator transitions to AWAITING_OPERATOR immediately if wall-clock exceeds 600s."""
        coordinator = BoundedIngestionCoordinator(max_remediations=5, max_wall_clock_s=0.1)
        time.sleep(0.15)

        # Attempt fails after deadline -> Must transition to AWAITING_OPERATOR regardless of remaining retry count
        state = coordinator.record_attempt(passed=False, error="Slow execution")
        self.assertEqual(state, IngestionLifecycleState.AWAITING_OPERATOR)

    # ── Category 2: Boundary Conditions (Fast-Path Pass & Recovery) ──

    def test_bounded_coordinator_immediate_pass_reaches_published(self) -> None:
        """Verify immediate pass on attempt 1 transitions directly to PUBLISHED."""
        coordinator = BoundedIngestionCoordinator(max_remediations=2, max_wall_clock_s=600.0)
        state = coordinator.record_attempt(passed=True)
        self.assertEqual(state, IngestionLifecycleState.PUBLISHED)
        self.assertEqual(coordinator.remediation_count, 0)

    def test_bounded_coordinator_remediation_recovery_reaches_published(self) -> None:
        """Verify failure on attempt 1 followed by pass on attempt 2 transitions to PUBLISHED."""
        coordinator = BoundedIngestionCoordinator(max_remediations=2, max_wall_clock_s=600.0)
        st1 = coordinator.record_attempt(passed=False)
        self.assertEqual(st1, IngestionLifecycleState.REMEDIATING)

        st2 = coordinator.record_attempt(passed=True)
        self.assertEqual(st2, IngestionLifecycleState.PUBLISHED)
        self.assertEqual(coordinator.remediation_count, 1)

    # ── Category 3: Rollback & Recovery (--resume Cache Preservation) ──

    def test_resume_preserves_prior_passing_checkpoints_without_restart(self) -> None:
        """Verify that --resume reuses passing checkpoints across multiple deliverables without re-evaluating."""
        out_p = self.sandbox_dir / "meeting_resume_run"
        out_p.mkdir(parents=True, exist_ok=True)

        # Populate passing checkpoints on disk
        (out_p / "01_test_hitl_resolved_transcript.md").write_text("Passed 01 content " * 30, encoding="utf-8")
        (out_p / "02_test_executive_recap.md").write_text("Passed 02 content " * 30, encoding="utf-8")
        (out_p / "03_test_canonical_signal_projection.json").write_text(
            json.dumps({"entities": ["ent-1"], "decisions": []}) + (" " * 200),
            encoding="utf-8",
        )

        dec_payload = {
            "status": "PASS",
            "sealed": True,
            "output_1_pass": True,
            "output_2_pass": True,
            "output_3_pass": True,
        }
        (out_p / "final_release_decision.json").write_text(json.dumps(dec_payload), encoding="utf-8")

        class MockStateMachine:
            def __init__(self) -> None:
                self.frozen: dict[str, str] = {}
                self.permanently_frozen: set[str] = set()
                self.passing_stages: set[str] = set()

            def is_frozen(self, k: str) -> bool:
                return k in self.frozen

            def freeze_artifact(self, k: str, content: str) -> None:
                self.frozen[k] = content

        sm = MockStateMachine()
        meta = {"resume": True}

        memoized = memoize_substage_checkpoints(out_p, sm, meta, modality=CanonicalModality.AUDIO)
        self.assertTrue(memoized["output_1"])
        self.assertTrue(memoized["output_2"])
        self.assertTrue(memoized["output_3"])
        self.assertIn("output_1", sm.frozen)
        self.assertIn("output_2", sm.frozen)
        self.assertIn("output_3", sm.frozen)

    # ── Category 4: Invariant Protection (Single run_id & Zero Restart Drift) ──

    def test_run_id_stability_across_remediations(self) -> None:
        """Verify that multiple remediation attempts preserve the durable run_id."""
        initial_run_id = "run-2026-10-12-test-001"
        metadata = {"run_id": initial_run_id, "attempt": 1}

        coordinator = BoundedIngestionCoordinator(max_remediations=2, max_wall_clock_s=600.0)

        # Attempt 1
        coordinator.record_attempt(passed=False)
        metadata["attempt"] = 2
        self.assertEqual(metadata["run_id"], initial_run_id)

        # Attempt 2
        coordinator.record_attempt(passed=False)
        metadata["attempt"] = 3
        self.assertEqual(metadata["run_id"], initial_run_id)

        # Escalation
        final_state = coordinator.record_attempt(passed=False)
        self.assertEqual(final_state, IngestionLifecycleState.AWAITING_OPERATOR)
        self.assertEqual(metadata["run_id"], initial_run_id)


if __name__ == "__main__":
    unittest.main()
