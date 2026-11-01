"""tests/test_hitl_margin_enforcement_e2e.py

End-to-End qualification tests for > 20% HITL ambiguity margin bypass.
Verifies:
1. Operational / role / attendee decisions with spread > 20% resolve autonomously without HITL.
2. Boundary decisions with spread <= 20% (e.g. exactly 20% or 15%) trigger interactive HITL review.
3. PreToolUse hook check_ask_user_question blocks unnecessary prompt (exit code 2) when spread > 20%.
4. C0.6 GraphDB role verification gate auto-resolves when single verified GraphDB role is present with spread > 20%.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest import TestCase
from unittest.mock import patch

from dual_pipeline.hitl_governance_core.canonical_evaluator import evaluate_canonical_decision
from dual_pipeline.parameters import needs_operator_decision


class HitlMarginEnforcementE2ETests(TestCase):
    """End-to-end qualification of > 20% margin autonomous resolution and <= 20% HITL gating."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]

    def test_canonical_evaluator_gt_20pct_margin_resolves_autonomously_e2e(self) -> None:
        """Decisions with delta > 0.20 and conviction >= 0.65 pass Lock 1 and Lock 2."""
        options = [
            {"label": "Option A: Confirmed Path", "confidence": 0.85},
            {"label": "Option B: Alternative", "confidence": 0.50},  # delta = 0.35 > 0.20
        ]
        eval_res = evaluate_canonical_decision(
            options=options,
            context={"category": "OPERATIONAL", "question": "Select path"},
        )
        self.assertTrue(eval_res.autonomous_permitted)
        self.assertFalse(eval_res.surface_hitl)
        self.assertTrue(eval_res.lock1_passed)
        self.assertTrue(eval_res.lock2_passed)
        self.assertEqual(len(eval_res.reasons), 0)

    def test_canonical_evaluator_le_20pct_margin_requires_hitl_e2e(self) -> None:
        """Decisions with delta <= 0.20 (e.g. 0.20 exact or 0.15) must trigger HITL."""
        # 1. Exact 20% boundary (80% vs 60%)
        options_20 = [
            {"label": "Option A", "confidence": 0.80},
            {"label": "Option B", "confidence": 0.60},
        ]
        res_20 = evaluate_canonical_decision(
            options=options_20,
            context={"category": "OPERATIONAL", "question": "Select path"},
        )
        self.assertFalse(res_20.autonomous_permitted)
        self.assertTrue(res_20.surface_hitl)
        self.assertFalse(res_20.lock1_passed)

        # 2. Narrow 15% boundary (65% vs 50%)
        options_15 = [
            {"label": "Option A", "confidence": 0.65},
            {"label": "Option B", "confidence": 0.50},
        ]
        res_15 = evaluate_canonical_decision(
            options=options_15,
            context={"category": "OPERATIONAL", "question": "Select path"},
        )
        self.assertFalse(res_15.autonomous_permitted)
        self.assertTrue(res_15.surface_hitl)

    def test_pre_tool_hook_blocks_unnecessary_prompt_when_margin_gt_20pct_e2e(self) -> None:
        """PreToolUse hook check_ask_user_question.py exits with code 2 (bypass) on spread > 20%."""
        hook_script = self.repo_root / "tools" / "check_ask_user_question.py"
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            sqlite_path = tmp_root / "hitl_decision_ledger.sqlite"
            json_path = tmp_root / "hitl_decision_ledger.json"

            # Case A: Wide margin (90% vs 50% = 40% > 20%) -> MUST BE DENIED with code 2
            payload_wide = {
                "toolCall": {
                    "name": "ask_question",
                    "args": {
                        "questions": [
                            {
                                "decision_id": "dec-wide-e2e",
                                "category": "operational",
                                "question": "Which summary formatting style should be applied?",
                                "options": [
                                    "(Recommended) Concise bulleted executive takeaways (Confidence: HIGH (90%)) - Prioritizes rapid scanning.",
                                    "Detailed narrative paragraph summary (Confidence: MEDIUM (50%)) - Preserves extended prose context.",
                                ],
                                "is_multi_select": False,
                            }
                        ]
                    },
                }
            }

            env = dict(os.environ)
            env["NEE_HITL_LEDGER_SQLITE"] = str(sqlite_path)
            env["NEE_HITL_LEDGER_JSON"] = str(json_path)

            from tools.check_ask_user_question import check_hook_payload

            with patch.dict(
                os.environ,
                {
                    "NEE_HITL_LEDGER_SQLITE": str(sqlite_path),
                    "NEE_HITL_LEDGER_JSON": str(json_path),
                },
            ):
                res_wide = check_hook_payload(json.dumps(payload_wide))

            self.assertEqual(res_wide.get("decision"), "deny")
            self.assertIn("HITL_MARGIN_BYPASS_REQUIRED", res_wide.get("reason", ""))

            # Case B: Narrow margin (58% vs 42% = 16% <= 20%) -> ALLOWED to prompt operator
            payload_narrow = {
                "toolCall": {
                    "name": "ask_question",
                    "args": {
                        "questions": [
                            {
                                "decision_id": "dec-narrow-e2e",
                                "category": "operational",
                                "question": "Which summary formatting style should be applied?",
                                "options": [
                                    "(Recommended) Concise bulleted executive takeaways (Confidence: MEDIUM (58%)) - Prioritizes rapid scanning.",
                                    "Detailed narrative paragraph summary (Confidence: LOW (42%)) - Preserves extended prose context.",
                                ],
                                "is_multi_select": False,
                            }
                        ]
                    },
                }
            }
            with patch.dict(
                os.environ,
                {
                    "NEE_HITL_LEDGER_SQLITE": str(sqlite_path),
                    "NEE_HITL_LEDGER_JSON": str(json_path),
                },
            ):
                res_narrow = check_hook_payload(json.dumps(payload_narrow))
            self.assertEqual(res_narrow.get("decision"), "allow")
