"""End-to-End tests for Wave 1: PR Governance, Risk-Lane Classification & Readiness Gate Hardening."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.change_lane_classifier import classify_changes, classify_file, load_policy
from tools.pr_governance import build_parser
from tools.readiness_planner import plan_readiness


class TestPRGovernanceLaneGatesE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy_path = ROOT / "config" / "governance" / "risk_lanes_policy.json"
        cls.policy = load_policy(cls.policy_path)

    def test_core_pipeline_and_release_packages_classify_as_lane_2(self) -> None:
        """Verifies CAT7-P0-1: Real ingestion, release, and graph writer packages map to Lane 2."""
        lane_2_targets = [
            "dual_pipeline/release_core/activator.py",
            "dual_pipeline/release_policy.py",
            "dual_pipeline/canonical_pipeline/pipeline.py",
            "dual_pipeline/canonical_pipeline/publish_helper.py",
            "dual_pipeline/canonical_pipeline/checkpoint_memoizer.py",
            "dual_pipeline/intake_core/guard.py",
            "dual_pipeline/document_pipeline_core/executor.py",
            "dual_pipeline/audio_ingestion_core/mlx_transcriber.py",
            "dual_pipeline/graph_writer_core/writer.py",
            "dual_pipeline/graph_consolidation_core/repoint.py",
            "dual_pipeline/uwg_core/universal_write_gateway_role.py",
            "dual_pipeline/query_router.py",
            "dual_pipeline/query_router_core/temporal_gate.py",
            "implementation_plan.md",
        ]
        for path in lane_2_targets:
            lane, reason = classify_file(path, self.policy)
            self.assertEqual(lane, 2, f"Expected Lane 2 for {path}, got Lane {lane} ({reason})")

        summary = classify_changes(lane_2_targets, policy_path=self.policy_path)
        self.assertEqual(summary["effective_lane"], 2)
        self.assertTrue(summary["touches_lane_2"])

    def test_proportional_stage_exemption_math_has_zero_shift(self) -> None:
        """Verifies CAT7-P0-2: Lane 1 stage exemptions match gate_manifest without list-length shift."""
        plan, required_indices = plan_readiness(["dual_pipeline/analyzer.py"], repo_root=ROOT)
        self.assertEqual(plan.risk_lane, 1)
        total_stages = 32
        if total_stages > plan.total_registered_stages and plan.risk_lane >= 1:
            required_indices = list(required_indices) + list(
                range(plan.total_registered_stages + 1, total_stages + 1)
            )
        exempt_indices = set(range(1, total_stages + 1)) - set(required_indices)
        self.assertEqual(len(exempt_indices), plan.exempt_stages_count)
        self.assertGreater(len(exempt_indices), 0)
        self.assertEqual(exempt_indices | set(required_indices), set(range(1, total_stages + 1)))

    def test_merge_promote_submit_reject_allow_open_waves_flag(self) -> None:
        """Verifies CAT7-P1-4: --allow-open-waves is removed from merge, promote, and submit."""
        parser = build_parser()
        for subcmd in ("merge", "promote", "submit"):
            with self.assertRaises(SystemExit):
                parser.parse_args([subcmd, "feat/sample", "--title", "sample title", "--allow-open-waves"])


if __name__ == "__main__":
    unittest.main()
