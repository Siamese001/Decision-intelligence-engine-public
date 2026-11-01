"""End-to-end test for semantic unit policy audit dry-run and receipt sealing."""
from __future__ import annotations

import argparse
import unittest
from pathlib import Path

from dual_pipeline.cli.operational import execute_semantic_unit_audit
from dual_pipeline.core import ROOT
from dual_pipeline.index_core.policy import DEFAULT_POLICY_PATH


class TestSemanticUnitPolicyE2E(unittest.TestCase):
    def test_e2e_semantic_unit_audit_dry_run_with_policy(self):
        rt_root = ROOT / ".local" / "dual_pipeline"
        if not (rt_root / "active_pair.json").is_file():
            self.skipTest("active_pair.json not found")

        args = argparse.Namespace(
            runtime_root=rt_root,
            graph=None,
            sidecar=None,
            query_cache=None,
            hop_budget=1,
            margin_threshold=0.15,
            policy=DEFAULT_POLICY_PATH,
            dry_run=True,
            output_receipt=None,
            emit_json=False,
        )

        report = execute_semantic_unit_audit(args)

        # 1. Structure assertions
        self.assertEqual(report.get("audit_kind"), "semantic_units_and_reachability")
        self.assertEqual(report.get("schema_version"), "1.0.0")
        self.assertIn("receipt_digest", report)
        self.assertIn("receipt_path", report)

        # 2. Receipt file existence on disk
        receipt_path = Path(report["receipt_path"])
        self.assertTrue(receipt_path.is_file(), f"Receipt not saved to disk: {receipt_path}")

        # 3. Policy binding verification
        self.assertIn("policy", report)
        policy_info = report["policy"]
        self.assertEqual(policy_info.get("policy_id"), "semantic-unit-policy-v1")
        self.assertEqual(policy_info.get("policy_version"), "1.0.0")
        self.assertEqual(policy_info.get("approval_id"), "directive-semantic-unit-policy-w2-v1")
        self.assertEqual(len(policy_info.get("policy_digest", "")), 64)

        # 4. Reachability zero gap verification
        summary = report.get("summary", {})
        self.assertEqual(summary.get("unreachable_unexplained_count"), 0)
        self.assertGreater(summary.get("total_current_assertions", 0), 0)

        # 5. Policy projection verification
        self.assertIn("projected_reachability", report)
        proj = report["projected_reachability"]
        self.assertEqual(proj.get("projected_unreachable_unexplained_count"), 0)
        self.assertGreater(proj.get("projected_dense_edges_removed", 0), 0)
        self.assertGreater(proj.get("projected_direct_propositions", 0), 0)
        self.assertGreater(proj.get("projected_reachable_via_passage", 0), 0)


if __name__ == "__main__":
    unittest.main()
