#!/usr/bin/env python3
"""
E2E tests for PR Priority Engine (Wave 5).
Validates reproduction of Seat 4 mismatch table, CLI validate-rubric and rank commands,
and that explanation cells strictly respect the 90-character budget.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.pr_governance_core.common import ROOT
from tools.pr_governance_core.inspection import get_feature_branches
from tools.pr_priority_engine import (
    cmd_rank,
    cmd_validate_rubric,
    derive_priority,
    load_registry,
    load_rubric,
)


class TestPRPriorityEngineE2E(unittest.TestCase):
    def setUp(self):
        self.rubric = load_rubric()
        self.registry = load_registry()

    def test_seat4_mismatch_table_reproduced(self):
        """
        Acceptance Criteria: Seat 4's mismatch table is reproduced:
        - feat/rca-output3-llm-call-council: P0 (was P3.1)
        - feat/graphdb-freshness-agent-panel: P1 (was P3.1)
        - feat/output-1-pipeline-hardening: P3 cosmetic (was P1.1)
        - feat/rca-llm-judge-cost-opt-v2/v3: P3 research (were P0.1)
        - feat/cost-optimization-research: D prune (was P0.1)
        """
        branches = get_feature_branches()

        # 1. feat/rca-output3-llm-call-council -> P0
        if "feat/rca-output3-llm-call-council" in branches:
            meta = derive_priority("feat/rca-output3-llm-call-council", [], self.registry, self.rubric, repo_root=ROOT)
            self.assertEqual(meta["base_tier"], "P0")
            self.assertEqual(meta["rule_id"], "R3.cost")
            self.assertGreater(meta["in_set_prod_loc"], 20)
            self.assertGreater(meta["value"], 10.0)

        # 2. feat/graphdb-freshness-agent-panel -> P1
        if "feat/graphdb-freshness-agent-panel" in branches:
            meta = derive_priority("feat/graphdb-freshness-agent-panel", [], self.registry, self.rubric, repo_root=ROOT)
            self.assertEqual(meta["base_tier"], "P1")
            self.assertEqual(meta["rule_id"], "R3.graph")
            self.assertGreater(meta["in_set_prod_loc"], 20)

        # 3. feat/output-1-pipeline-hardening -> P3 (cosmetic)
        if "feat/output-1-pipeline-hardening" in branches:
            meta = derive_priority("feat/output-1-pipeline-hardening", [], self.registry, self.rubric, repo_root=ROOT)
            self.assertEqual(meta["base_tier"], "P3")
            self.assertEqual(meta["rule_id"], "R5.cosmetic")

        # 4. feat/rca-llm-judge-cost-opt-v2 & v3 -> P3 (research)
        for v in ("feat/rca-llm-judge-cost-opt-v2", "feat/rca-llm-judge-cost-opt-v3"):
            if v in branches:
                meta = derive_priority(v, [], self.registry, self.rubric, repo_root=ROOT)
                self.assertEqual(meta["base_tier"], "P3")
                self.assertEqual(meta["rule_id"], "R5.research")

        # 5. feat/cost-optimization-research -> D (PRUNE)
        if "feat/cost-optimization-research" in branches:
            meta = derive_priority("feat/cost-optimization-research", [], self.registry, self.rubric, repo_root=ROOT)
            self.assertEqual(meta["base_tier"], "D")
            self.assertEqual(meta["rule_id"], "R0.prune")

    def test_e2e_explanation_under_90_chars(self):
        """Every single explanation cell must be <= 90 characters."""
        branches = get_feature_branches()
        for b in branches:
            meta = derive_priority(b, [], self.registry, self.rubric, repo_root=ROOT)
            expl = meta["explanation"]
            self.assertTrue(len(expl) > 0, f"Empty explanation for {b}")
            self.assertLessEqual(len(expl), 90, f"Explanation exceeds 90 chars ({len(expl)}) for {b}: {expl}")

    def test_e2e_cmd_validate_rubric(self):
        """Validate rubric command passes and produces receipt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            receipt_path = Path(tmpdir) / "rubric_val_receipt.json"
            class Args:
                receipt = str(receipt_path)

            ret = cmd_validate_rubric(Args())
            self.assertEqual(ret, 0)
            self.assertTrue(receipt_path.exists())
            with open(receipt_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data.get("terminal_outcome"), "PASS")
            self.assertEqual(data.get("rubric_version"), "2.0")

    def test_e2e_cmd_rank(self):
        """Rank command computes real intra-P ranking and emits verified receipt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            receipt_path = Path(tmpdir) / "rank_receipt.json"
            class Args:
                receipt = str(receipt_path)

            ret = cmd_rank(Args())
            self.assertEqual(ret, 0)
            self.assertTrue(receipt_path.exists())
            with open(receipt_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data.get("terminal_outcome"), "PASS")
            self.assertIn("ranked_queue", data)
            self.assertIn("rules_fired", data)
            self.assertIn("inputs_digest", data)
            self.assertGreater(data.get("total_branches", 0), 0)


if __name__ == "__main__":
    unittest.main()
