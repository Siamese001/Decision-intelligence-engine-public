"""Unit tests for storage hygiene pins resolver, policy, and canonical receipts."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.storage_hygiene_core.pins import resolve_pins
from tools.storage_hygiene_core.policy import (
    GenerationPolicy,
    PolicyValidationError,
    load_storage_retention_policy,
)
from tools.storage_hygiene_core.receipt import (
    create_hygiene_report,
    verify_report_digest,
)


class TestStorageHygienePolicy(unittest.TestCase):
    def test_default_policy_loads_valid(self):
        pol = load_storage_retention_policy()
        self.assertEqual(pol.version, "1.0.0")
        self.assertGreaterEqual(pol.generations.rollback_depth, 1)
        self.assertTrue(pol.generations.preserve_receipt_cited)
        self.assertTrue(len(pol.never_touch) > 0)
        self.assertIn("env_di", pol.never_touch)
        self.assertTrue(len(pol.digest) == 64)

    def test_policy_validation_catches_invalid_bounds(self):
        with self.assertRaises(PolicyValidationError):
            GenerationPolicy(rollback_depth=0).validate()

        with self.assertRaises(PolicyValidationError):
            GenerationPolicy(min_age_days=-1).validate()


class TestPinResolver(unittest.TestCase):
    def test_real_repo_pins_contain_live_and_tracked(self):
        pol = load_storage_retention_policy()
        res = resolve_pins(policy=pol)
        self.assertFalse(res.fail_closed)

        # Active generations must contain the active pair from .local/dual_pipeline/active_pair.json
        active_pair_path = Path(".local/dual_pipeline/active_pair.json")
        if active_pair_path.exists():
            data = json.loads(active_pair_path.read_text(encoding="utf-8"))
            live_gen = data.get("generation_id")
            if live_gen:
                self.assertIn(
                    live_gen,
                    res.active_generations,
                    f"Active generations missing live generation {live_gen}: {res.active_generations}",
                )
        else:
            self.assertTrue(len(res.active_generations) > 0)
        # Tracked dual_pipeline/retrieval/active_pair.json points to current or rollback generation
        retrieval_pair_path = Path("dual_pipeline/retrieval/active_pair.json")
        if retrieval_pair_path.exists():
            rdata = json.loads(retrieval_pair_path.read_text(encoding="utf-8"))
            retrieval_gen = rdata.get("generation_id")
            if retrieval_gen:
                self.assertIn(
                    retrieval_gen,
                    res.active_generations,
                    f"Active generations missing retrieval gen {retrieval_gen}: {res.active_generations}",
                )
        self.assertTrue(
            any("6018bfb5" in g for g in res.all_pinned),
            f"Pinned generations missing historical rollback gen 6018bfb5: {res.all_pinned}",
        )
        # Rollback generations should contain previous_generation_id from live active pair
        if active_pair_path.exists():
            prev_gen = data.get("previous_generation_id")
            if prev_gen:
                self.assertIn(
                    prev_gen,
                    res.rollback_generations,
                    f"Rollback generations missing previous gen {prev_gen}: {res.rollback_generations}",
                )
        else:
            self.assertTrue(len(res.rollback_generations) > 0)
        # The pytest temp path leak in .local/dual_pipeline/runtime/active_pair.json must be caught as invalid_source if present
        runtime_leak_path = Path(".local/dual_pipeline/runtime/active_pair.json")
        if runtime_leak_path.exists():
            self.assertTrue(
                any("runtime/active_pair.json" in s.path for s in res.invalid_sources),
                f"Expected invalid source for runtime/active_pair.json: {res.invalid_sources}",
            )

    def test_corrupt_active_pair_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            active_pair_path = tmp_root / ".local" / "dual_pipeline" / "active_pair.json"
            active_pair_path.parent.mkdir(parents=True)
            active_pair_path.write_text("{corrupt json ...", encoding="utf-8")

            res = resolve_pins(repo_root=tmp_root, runtime_root=tmp_root / ".local" / "dual_pipeline")
            self.assertTrue(res.fail_closed)
            self.assertIn("Corrupt active_pair", res.fail_reason)


class TestCanonicalReceipt(unittest.TestCase):
    def test_receipt_digest_creation_and_verification(self):
        pins = {
            "active_generations": ["generation-test-1"],
            "rollback_generations": ["generation-test-0"],
            "preserved_generations": [],
            "receipt_cited_generations": [],
            "invalid_sources": [],
        }
        summary = {
            "total_candidates": 0,
            "total_pinned": 2,
            "reclaimable_bytes": 0,
        }
        report = create_hygiene_report(
            mode="plan",
            scheduled=False,
            policy_digest="a" * 64,
            pins=pins,
            summary=summary,
        )
        self.assertIn("canonical_digest", report)
        self.assertTrue(verify_report_digest(report))

        # Tampering with payload invalidates digest
        report["summary"]["total_candidates"] = 99
        self.assertFalse(verify_report_digest(report))


if __name__ == "__main__":
    unittest.main()
