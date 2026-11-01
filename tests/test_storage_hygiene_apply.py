"""Unit tests for quarantine apply, restore, and zero graph mutation proofs."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.storage_hygiene_core.apply import (
    MissingApprovalError,
    PlanDriftError,
    ScheduledMutationBlockedError,
    apply_quarantine,
    restore_quarantine,
)
from tools.storage_hygiene_core.receipt import create_hygiene_report


def _hash_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class TestStorageHygieneApply(unittest.TestCase):
    def test_scheduled_mutation_blocked(self):
        plan = create_hygiene_report(
            mode="plan",
            scheduled=False,
            policy_digest="0" * 64,
            pins={
                "active_generations": [],
                "rollback_generations": [],
                "preserved_generations": [],
                "invalid_sources": [],
            },
            summary={"total_candidates": 0, "total_pinned": 0, "reclaimable_bytes": 0},
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as app_f:
            app_f.write('{"status": "APPROVED", "operator": "Amit Ayer"}')
            app_f.flush()

            with self.assertRaises(ScheduledMutationBlockedError):
                apply_quarantine(plan, Path(app_f.name), scheduled=True)

    def test_missing_or_unapproved_receipt_blocked(self):
        plan = create_hygiene_report(
            mode="plan",
            scheduled=False,
            policy_digest="0" * 64,
            pins={
                "active_generations": [],
                "rollback_generations": [],
                "preserved_generations": [],
                "invalid_sources": [],
            },
            summary={"total_candidates": 0, "total_pinned": 0, "reclaimable_bytes": 0},
        )
        # Non-existent receipt
        with self.assertRaises(MissingApprovalError):
            apply_quarantine(plan, Path("/nonexistent/approval.json"))

        # Unapproved receipt
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as bad_app:
            bad_app.write('{"status": "REJECTED"}')
            bad_app.flush()
            with self.assertRaises(MissingApprovalError):
                apply_quarantine(plan, Path(bad_app.name))

    def test_plan_drift_rejection(self):
        plan = create_hygiene_report(
            mode="plan",
            scheduled=False,
            policy_digest="0" * 64,
            pins={
                "active_generations": [],
                "rollback_generations": [],
                "preserved_generations": [],
                "invalid_sources": [],
            },
            summary={"total_candidates": 0, "total_pinned": 0, "reclaimable_bytes": 0},
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as app_f:
            app_f.write('{"status": "APPROVED", "operator": "Amit Ayer"}')
            app_f.flush()

            with self.assertRaises(PlanDriftError):
                apply_quarantine(
                    plan,
                    Path(app_f.name),
                    expected_plan_digest="different_digest_string",
                )

    def test_quarantine_and_restore_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            trash_root = repo_root / ".local" / "trash"
            gens_dir = repo_root / ".local" / "dual_pipeline" / "generations"
            gens_dir.mkdir(parents=True)

            # Setup candidate generation
            cand_gen = gens_dir / "generation-candidate-to-quarantine"
            cand_gen.mkdir()
            cand_file = cand_gen / "graph.sqlite"
            cand_file.write_text("graph sqlite bytes to quarantine", encoding="utf-8")
            hash_before = _hash_file(cand_file)

            # Setup approval receipt
            approval_file = repo_root / "approval.json"
            approval_file.write_text(
                json.dumps({"status": "APPROVED", "operator": "Amit Ayer"}),
                encoding="utf-8",
            )

            # Build mock plan report
            plan = create_hygiene_report(
                mode="plan",
                scheduled=False,
                policy_digest="0" * 64,
                pins={
                    "active_generations": [],
                    "rollback_generations": [],
                    "preserved_generations": [],
                    "invalid_sources": [],
                },
                summary={"total_candidates": 1, "total_pinned": 0, "reclaimable_bytes": 100},
                generations_analysis={
                    "candidates": [
                        {
                            "generation_id": cand_gen.name,
                            "path": str(cand_gen),
                            "size_bytes": 100,
                        }
                    ]
                },
            )

            # 1. Apply quarantine
            apply_receipt = apply_quarantine(
                plan,
                approval_file,
                trash_root=trash_root,
                repo_root=repo_root,
            )

            self.assertEqual(apply_receipt["status"], "APPLIED")
            self.assertEqual(apply_receipt["total_items_count"], 1)
            # Original file should no longer exist
            self.assertFalse(cand_gen.exists())

            # Quarantined file should exist in trash
            q_path = Path(apply_receipt["quarantined_items"][0]["quarantine_path"])
            self.assertTrue(q_path.exists())
            self.assertEqual(_hash_file(q_path / "graph.sqlite"), hash_before)

            # 2. Reverse restore
            receipt_path = q_path.parent.parent / "apply_receipt.json"
            self.assertTrue(receipt_path.exists())

            restore_receipt = restore_quarantine(receipt_path)
            self.assertEqual(restore_receipt["status"], "RESTORED")
            self.assertEqual(restore_receipt["restored_items_count"], 1)

            # Original file should be back with identical hash
            self.assertTrue(cand_gen.exists())
            self.assertEqual(_hash_file(cand_file), hash_before)


if __name__ == "__main__":
    unittest.main()
