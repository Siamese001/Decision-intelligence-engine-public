"""Hygiene-agent scheduled prompt: governance invariants (adversarial / edge cases).

Pins automations/prompts/graph_hygiene_agent.md: pre-flight baseline, rollback copy,
dry-run first, per-wave HITL approval, numeric post-checks, and no self-approval.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PROMPT = Path(__file__).resolve().parents[1] / "automations" / "prompts" / "graph_hygiene_agent.md"

REQUIRED_PHRASES = (
    "PRE-FLIGHT",
    "ROLLBACK COPY",
    "integrity_check",
    "foreign_key_check",
    "ask_user_question",
    "HITL_REQUIRED",
    "POST_VERIFICATION_FAILED",
    "POST_DELTA_MISSING",
    "PRE_BASELINE_INVALID",
    "UNSAFE_ENVIRONMENT",
    "Dry run first",
    "Granular Graph DB Delta Table",
    "Itemized Assertions Table",
)

def _mode_violations(text: str) -> list[str]:
    found: list[str] = []
    if re.search(r"^MODE:\s*", text, re.MULTILINE):
        found.append("PROMPT_MODE_MARKER")
    if "AuthorityContext" not in text:
        found.append("MISSING_AUTHORITY_CONTEXT")
    if "guided by `ask_user_question`" not in text:
        found.append("MISSING_UNIFIED_INTERACTIVE_PIPELINE")
    if "apply nothing to the live graph" not in text.lower():
        found.append("UNAPPROVED_CAN_APPLY")
    return found


def violations(text: str) -> list[str]:
    """Return governance violations for a hygiene prompt text."""
    return _base_violations(text) + _mode_violations(text)


def _base_violations(text: str) -> list[str]:
    """Return governance violations for a hygiene prompt text."""
    found = [f"MISSING:{p}" for p in REQUIRED_PHRASES if p not in text]
    if re.search(r"(?i)\bauto[- ]?approv(e|al)\b(?!.*never)", text.replace("synthetic or auto-approvals", "")):
        found.append("AUTO_APPROVAL_LANGUAGE")
    if re.search(r"(?i)assume approval(?!\.)", text) and "Do not assume approval" not in text:
        found.append("ASSUMED_APPROVAL")
    if "Never approve for yourself" not in text:
        found.append("MISSING_SELF_APPROVAL_BAN")
    if "Never edit on `main`" not in text:
        found.append("MISSING_MAIN_BRANCH_BAN")
    if re.search(r"(?i)unknown", text) and "never written as \"unknown\"" not in text:
        found.append("UNKNOWN_ESCAPE")
    return found


class HygienePromptGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = PROMPT.read_text(encoding="utf-8")

    def test_committed_prompt_has_no_violations(self) -> None:
        self.assertEqual(violations(self.text), [])

    def test_five_reviewers_and_plain_english_format_preserved(self) -> None:
        for name in ("Structural Inspector", "Timeline Detective", "Fact-Checker", "Security Officer", "Presiding Judge"):
            self.assertIn(name, self.text)
        for heading in ("## 1. Executive Summary", "## 2. The Five Reviewers", "## 3. Fix Execution Status", "## 4. Technical Tables"):
            self.assertIn(heading, self.text)

    def test_discovery_is_read_only(self) -> None:
        self.assertIn("Discovery is READ-ONLY", self.text)

    # --- adversarial: tampering must be detected ---------------------------------------

    def test_removed_hitl_stop_is_detected(self) -> None:
        self.assertIn("MISSING:HITL_REQUIRED", violations(self.text.replace("HITL_REQUIRED", "DONE")))

    def test_removed_rollback_is_detected(self) -> None:
        self.assertIn("MISSING:ROLLBACK COPY", violations(self.text.replace("ROLLBACK COPY", "NOTE")))

    def test_removed_self_approval_ban_is_detected(self) -> None:
        self.assertIn("MISSING_SELF_APPROVAL_BAN", violations(self.text.replace("Never approve for yourself", "Approve as needed")))

    def test_removed_main_branch_ban_is_detected(self) -> None:
        self.assertIn("MISSING_MAIN_BRANCH_BAN", violations(self.text.replace("Never edit on `main`", "Edit anywhere")))

    def test_unknown_escape_is_detected(self) -> None:
        tampered = self.text.replace('never written as "unknown"', "may be unknown")
        self.assertIn("UNKNOWN_ESCAPE", violations(tampered))

    # --- unified interactive ask_user_question pipeline --------------------------------

    def test_prompt_mode_marker_injection_is_detected(self) -> None:
        tampered = "MODE: scheduled\n" + self.text
        self.assertIn("PROMPT_MODE_MARKER", violations(tampered))

    def test_authority_context_removal_is_detected(self) -> None:
        tampered = self.text.replace("AuthorityContext", "CallerHeader")
        self.assertIn("MISSING_AUTHORITY_CONTEXT", violations(tampered))

    def test_unapproved_apply_nothing_removal_is_detected(self) -> None:
        tampered = self.text.replace("apply NOTHING to the live graph", "apply low-risk fixes")
        self.assertIn("UNAPPROVED_CAN_APPLY", violations(tampered))

    def test_unified_interactive_pipeline_removal_is_detected(self) -> None:
        tampered = self.text.replace("guided by `ask_user_question`", "in background mode")
        self.assertIn("MISSING_UNIFIED_INTERACTIVE_PIPELINE", violations(tampered))

    def test_interactive_keeps_per_wave_approval_and_rollback(self) -> None:
        for needed in ("for EACH wave", "one wave at a time", "ROLLBACK COPY", "Dry run first"):
            self.assertIn(needed, self.text)

    # --- boundary / malformed ----------------------------------------------------------

    def test_empty_prompt_is_rejected(self) -> None:
        found = violations("")
        self.assertIn("MISSING:PRE-FLIGHT", found)
        self.assertIn("MISSING_SELF_APPROVAL_BAN", found)

    def test_whitespace_only_prompt_is_rejected(self) -> None:
        self.assertTrue(violations("   \n\t\n"))


if __name__ == "__main__":
    unittest.main()
