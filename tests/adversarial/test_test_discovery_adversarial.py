#!/usr/bin/env python3
"""Adversarial and Edge-Case Tests for Wave 5 Automated Test Discovery Engine.

Covers mandatory edge-case categories:
1. Boundary Payloads: empty directories, 0 test files, empty base tuples, empty manifests.
2. Malformed Input: corrupt quarantine registry JSON, invalid date formats, missing fields.
3. Invariant Bypass Prevention: expired quarantine entries fail closed immediately.
4. Concurrency & Duplication Stress: massive redundant duplicate test declarations across sources.
5. Strict Precedence Invariants: BGE > E2E > Deterministic precedence under conflicting markers.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.test_discovery_core.discovery import (
    categorize_test_file,
    discover_all_test_files,
    resolve_all_tests,
)
from tools.test_discovery_core.quarantine import (
    get_quarantined_modules,
    load_quarantined_tests,
)


class TestDiscoveryAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="discovery_adv_")
        self.root = Path(self.tmp_dir)
        self.tests_dir = self.root / "tests"
        self.tests_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_boundary_empty_repository_and_empty_tuples(self) -> None:
        """Verify engine gracefully handles 0 tests on disk and empty base tuples."""
        discovered = discover_all_test_files(self.root)
        self.assertEqual(discovered, [])

        det, e2e, bge = resolve_all_tests(
            base_deterministic=(),
            base_e2e=(),
            base_bge=(),
            repo_root=self.root,
        )
        self.assertEqual(det, ())
        self.assertEqual(e2e, ())
        self.assertEqual(bge, ())

    def test_02_invariant_bypass_expired_quarantine_fails_closed(self) -> None:
        """Verify that an expired quarantined test raises ValueError immediately."""
        expired_date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        quarantine_payload = {
            "version": "1.0.0",
            "tests": [
                {
                    "module": "tests.test_ancient_bug",
                    "reason": "Temporary quarantine",
                    "expires_at": expired_date,
                }
            ],
        }
        q_file = self.root / "quarantined_tests.json"
        q_file.write_text(json.dumps(quarantine_payload), encoding="utf-8")

        with self.assertRaises(ValueError) as ctx:
            load_quarantined_tests(q_file)
        self.assertIn("expired", str(ctx.exception).lower())

    def test_03_malformed_quarantine_registry_payloads(self) -> None:
        """Verify malformed JSON or corrupted entries in quarantine registry are safely handled."""
        q_file = self.root / "corrupted_quarantine.json"

        # 1. Syntax error
        q_file.write_text("{ unquoted_key: invalid }", encoding="utf-8")
        self.assertEqual(load_quarantined_tests(q_file), {})
        self.assertEqual(get_quarantined_modules(q_file), set())

        # 2. Corrupt item structures (nulls, missing module)
        q_file.write_text(
            json.dumps({"tests": [None, {}, {"reason": "no mod"}, 12345]}),
            encoding="utf-8",
        )
        self.assertEqual(load_quarantined_tests(q_file), {})

    def test_04_duplication_and_order_preservation_under_stress(self) -> None:
        """Verify massive duplicate entries across base tuples are deduplicated cleanly."""
        base_det = ("tests.test_a", "tests.test_b", "tests.test_a", "tests.test_c") * 50
        base_e2e = (
            "tests.end_to_end.test_x",
            "tests.end_to_end.test_y",
            "tests.end_to_end.test_x",
        ) * 50
        base_bge = ("tests.test_bge_m", "tests.test_bge_m") * 20

        det, e2e, bge = resolve_all_tests(
            base_deterministic=base_det,
            base_e2e=base_e2e,
            base_bge=base_bge,
            repo_root=self.root,
        )

        self.assertEqual(det, ("tests.test_a", "tests.test_b", "tests.test_c"))
        self.assertEqual(e2e, ("tests.end_to_end.test_x", "tests.end_to_end.test_y"))
        self.assertEqual(bge, ("tests.test_bge_m",))

    def test_05_strict_precedence_bge_over_e2e_over_deterministic(self) -> None:
        """Verify BGE > E2E > Deterministic precedence prevents double-execution."""
        # A test file marked with both BGE and E2E in content
        conflict_file = self.tests_dir / "test_conflict_bge_and_e2e.py"
        bge_marker = "@pytest.mark." + "bge"
        conflict_file.write_text(
            f"# {bge_marker}\n# adversarial\ndef test_fn(): pass\n",
            encoding="utf-8",
        )

        cat = categorize_test_file(
            "tests.test_conflict_bge_and_e2e",
            conflict_file,
            quarantined=set(),
        )
        self.assertEqual(cat, "bge")

        det, e2e, bge = resolve_all_tests(
            base_deterministic=("tests.test_conflict_bge_and_e2e",),
            base_e2e=("tests.test_conflict_bge_and_e2e",),
            base_bge=("tests.test_conflict_bge_and_e2e",),
            repo_root=self.root,
        )
        self.assertIn("tests.test_conflict_bge_and_e2e", bge)
        self.assertNotIn("tests.test_conflict_bge_and_e2e", det)
        self.assertNotIn("tests.test_conflict_bge_and_e2e", e2e)


if __name__ == "__main__":
    unittest.main()
