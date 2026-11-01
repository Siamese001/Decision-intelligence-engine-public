#!/usr/bin/env python3
"""Integration tests for the approval-bound cards administrative release."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from dual_pipeline.card_refactor_release import (
    apply_card_refactor_plan,
    assert_card_refactor_plan,
    build_card_refactor_plan,
    semantic_organization_change_set_artifact,
)
from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import digest, sha256_file

ROOT = Path(__file__).resolve().parents[1]
SEED_GRAPH = ROOT / "graph" / "nee_graph.sqlite"


class CardRefactorReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        graph_hash = sha256_file(SEED_GRAPH)
        cls.plan = build_card_refactor_plan(
            SEED_GRAPH,
            active_pair={
                "generation_id": "generation-cards-refactor-test-baseline",
                "graph_snapshot": "graph-snapshot-cards-refactor-test",
                "graph_sha256": graph_hash,
            },
        )

    @classmethod
    def _approval(cls, approval_id: str) -> dict[str, object]:
        return {
            "approval_id": approval_id,
            "approval_reference": approval_id.upper(),
            "approval_type": "release_implementation",
            "scope": {
                "plan_id": cls.plan["plan_id"],
                "allowed_actions": sorted(
                    {
                        operation["operation"]
                        for operation in cls.plan["operations"]
                    }
                ),
            },
            "baseline_graph_sha256": cls.plan["base_graph_sha256"],
            "proposal_sha256": cls.plan["plan_digest"],
            "approval_statement_sha256": digest(
                {
                    "approval_id": approval_id,
                    "plan_digest": cls.plan["plan_digest"],
                }
            ),
            "approved_at": "2026-07-27T12:00:00Z",
            "expires_at": "2099-07-27T12:00:00Z",
            "status": "approved",
            "approved_by": "cards-refactor-test-authority",
            "release_version": "2026-07-27.1",
            "active": 1,
        }

    def test_plan_tamper_and_contract_projection(self) -> None:
        assert_card_refactor_plan(self.plan)
        tampered = deepcopy(self.plan)
        tampered["operations"][-1]["retirement_reason"] = "tampered"
        with self.assertRaisesRegex(RuntimeError, "plan digest mismatch"):
            assert_card_refactor_plan(tampered)

        artifact = semantic_organization_change_set_artifact(self.plan)
        validate_artifact(artifact)
        self.assertEqual("proposed", artifact["status"])
        self.assertIsNone(artifact["approval_id"])
        self.assertEqual(
            self.plan["plan_digest"],
            artifact["extensions"]["com.partner_advisory.nee.executable-plan-digest"],
        )

    def test_atomic_rollback_and_full_staged_apply(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-card-release-") as directory:
            root = Path(directory)
            rollback_graph = root / "rollback.sqlite"
            applied_graph = root / "applied.sqlite"
            shutil.copyfile(SEED_GRAPH, rollback_graph)
            shutil.copyfile(SEED_GRAPH, applied_graph)
            original_hash = sha256_file(SEED_GRAPH)

            rollback_id = "approval-cards-refactor-rollback-test"
            with self.assertRaisesRegex(RuntimeError, "injected"):
                apply_card_refactor_plan(
                    rollback_graph,
                    plan=self.plan,
                    approval_id=rollback_id,
                    approval_record=self._approval(rollback_id),
                    run_id="run-cards-refactor-rollback-test",
                    fail_after_operations=100,
                )
            self.assertEqual(original_hash, sha256_file(rollback_graph))

            approval_id = "approval-cards-refactor-apply-test"
            receipt = apply_card_refactor_plan(
                applied_graph,
                plan=self.plan,
                approval_id=approval_id,
                approval_record=self._approval(approval_id),
                run_id="run-cards-refactor-apply-test",
            )
            self.assertEqual("COMMITTED", receipt["status"])
            self.assertTrue(receipt["approval_staged"])
            self.assertEqual(
                len(self.plan["operations"]),
                receipt["operation_count"],
            )
            self.assertEqual("PASS", receipt["coverage"]["status"])
            self.assertEqual(
                0,
                receipt["coverage"]["active_assertion_intentional_unrouted"],
            )
            self.assertNotEqual(original_hash, sha256_file(applied_graph))
            self.assertEqual("ok", receipt["integrity"]["integrity_check"])
            self.assertEqual(
                self.plan["summary"]["legacy_card_state"],
                receipt["legacy_card_state"],
            )


if __name__ == "__main__":
    unittest.main()
