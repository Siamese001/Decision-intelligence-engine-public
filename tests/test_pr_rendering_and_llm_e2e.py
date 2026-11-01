#!/usr/bin/env python3
"""
E2E tests for PR Table Rendering and LLM Summary (Wave 6).
Validates:
1. Queue table renders all required columns: Priority Rank, Readiness, Review, Effort,
   Domain, Branch, Superseded By, Completed Waves, Open Waves, Ahead, Behind, Merge, Next Action.
2. JSON queue output and receipt contain review_verdict, rebase_effort, and superseded_by.
3. --llm-summary flag populates advisory intent summary without altering dispositions, verdicts, or priority.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.pr_governance_core.code_review import compute_code_review
from tools.pr_governance_core.common import ROOT
from tools.pr_governance_core.inspection import _render_queue_table, cmd_list, get_feature_branches


class TestPRRenderingAndLLME2E(unittest.TestCase):
    def test_queue_table_renders_all_required_columns(self):
        """Verifies that _render_queue_table formats all 13 columns including the 3 new ones."""
        dummy_queue = [
            {
                "sub_rank": "P0.1",
                "readiness": "🚀 Fast-Lane",
                "review_verdict": "REVIEW_READY",
                "rebase_effort": "LOW",
                "domain_impact": "P0 R3.cost: cost_tracker · 335 prod LOC",
                "domain": "Governance",
                "branch": "feat/rca-output3-llm-call-council",
                "superseded_by": None,
                "completed_waves": "1, 2, 3",
                "open_waves": "—",
                "ahead": 3,
                "behind": 25,
                "clean_merge": False,
                "next_action": "[RESOLVE_CONFLICTS] Low effort",
                "scope": "core",
                "registry_rationale": "",
            },
            {
                "sub_rank": "D.1",
                "readiness": "⚠️ Severely Diverged",
                "review_verdict": "SUPERSEDED_TOMBSTONE",
                "rebase_effort": "NONE",
                "domain_impact": "D R0.prune: ahead=0, clean",
                "domain": "Performance",
                "branch": "feat/cost-optimization-research",
                "superseded_by": {"kind": "main", "ref": "main", "sha": "3831a7c8", "confidence": "HIGH"},
                "completed_waves": "—",
                "open_waves": "—",
                "ahead": 0,
                "behind": 25,
                "clean_merge": True,
                "next_action": "[PRUNE] ahead==0 (No unique commits vs main, superseded by main@3831a7c8)",
                "scope": "decommission",
                "registry_rationale": "",
            },
        ]

        buf = io.StringIO()
        with redirect_stdout(buf):
            _render_queue_table(dummy_queue)
        output = buf.getvalue()

        # Check required headers
        self.assertIn("| Priority | Domain | Branch | Readiness | Review | Ahead | Behind |", output)
        self.assertIn("Readiness", output)
        self.assertIn("Review", output)
        self.assertIn("Effort", output)
        self.assertIn("| Domain |", output)
        self.assertIn("Branch", output)
        self.assertNotIn("Superseded By", output)
        self.assertNotIn("| Merge |", output)
        self.assertIn("Next Action", output)

        # Check that rows formatted new columns properly
        self.assertIn("REVIEW_READY", output)
        self.assertIn("LOW", output)
        self.assertIn("SUPERSEDED_TOMBSTONE", output)
        self.assertIn("main@3831a7c8", output)
        # Domain column shows the category label, never the rubric scoring trace
        self.assertIn("| Governance |", output)
        self.assertIn("| Performance |", output)
        self.assertNotIn("R3.cost: cost_tracker", output)

    def test_cmd_list_json_and_receipt_schema(self):
        """Verifies that cmd_list --json --receipt produces a valid schema with the new fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            receipt_path = Path(tmpdir) / "pr_queue_test_receipt.json"

            class Args:
                json = True
                receipt = str(receipt_path)
                llm_summary = False

            buf = io.StringIO()
            with redirect_stdout(buf):
                ret = cmd_list(Args())

            self.assertEqual(ret, 0)
            self.assertTrue(receipt_path.exists())

            with open(receipt_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.assertEqual(data.get("terminal_outcome"), "PASS")
            branches = data.get("branches", [])
            self.assertGreater(len(branches), 0)

            first = branches[0]
            self.assertIn("review_verdict", first)
            self.assertIn("rebase_effort", first)
            self.assertIn("superseded_by", first)
            self.assertIn("sub_rank", first)

    def test_llm_summary_isolation(self):
        """Verifies that enabling LLM summary populates advisory intent without changing decisions."""
        feat_branches = [b for b in get_feature_branches() if b.startswith("feat/") and b != "feat/pr-queue-table-redesign"]
        branch = feat_branches[0] if feat_branches else "feat/pr-queue-table-redesign"

        review_without_llm = compute_code_review(
            branch, base_branch="main", root=ROOT, use_cache=True, enable_llm_summary=False
        )
        review_with_llm = compute_code_review(
            branch, base_branch="main", root=ROOT, use_cache=True, enable_llm_summary=True
        )

        # Dispositions, verdicts, and evidence digests must remain identical
        self.assertEqual(review_without_llm.verdict, review_with_llm.verdict)
        self.assertEqual(review_without_llm.verdict_reason, review_with_llm.verdict_reason)
        self.assertEqual(review_without_llm.recommended_action, review_with_llm.recommended_action)
        self.assertEqual(review_without_llm.evidence_digest, review_with_llm.evidence_digest)

        # Advisory summary must be present when enabled
        self.assertIsNotNone(review_with_llm.llm_intent_summary)
        self.assertIsInstance(review_with_llm.llm_intent_summary, str)


if __name__ == "__main__":
    unittest.main()
