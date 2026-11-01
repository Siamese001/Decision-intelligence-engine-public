#!/usr/bin/env python3
"""tests/test_hitl_lock_advisory_rules.py

Adversarial and rule verification suite for Wave 4 HITL Lock Remediation Advisory Guard.
Enforces:
1. Exact production regression pin: raw shell lock deletion options (rm -f index.lock, git reset HEAD)
   are strictly rejected.
2. Invariant bypass attempts: evasive syntax variants (wildcards, flags, subcommands) fail closed.
3. Mandatory governed repair tool: lock remediation decisions must reference tools/pr_governance.py repair-index.
4. Compliant governed repair payloads pass validation with zero false positives.
"""
from __future__ import annotations

import unittest
import pytest

from dual_pipeline.hitl_governance_core.question_builder import QuestionBuilder
from dual_pipeline.hitl_governance_core.question_validator import (
    QuestionPayloadValidationError,
    validate_question_payload,
)


class HitlLockAdvisoryRulesTests(unittest.TestCase):
    """Verifies that HITL decision options reject raw shell lock deletion and enforce governed repair."""

    # -------------------------------------------------------------------------
    # Category 1: Exact Production Regression Pin
    # -------------------------------------------------------------------------
    def test_cat1_exact_incident_lock_deletion_rejected(self) -> None:
        """Pins the exact option from the APEX_TRANS Session 2 stale lock incident:
        rm -f .git/worktrees/transcript_neet_insights_hub_input_session_2/index.lock && git reset HEAD
        """
        question = "A stale lock file is preventing file operations for the session. How should we proceed?"
        options = [
            (
                "(Recommended) Clear the stale lock file (Confidence: HIGH (95%)) — "
                "Cleans the lock.\nrm -f .git/worktrees/transcript_session_2/index.lock && git reset HEAD"
            ),
            "Cancel operations (Confidence: LOW (20%)) — Halt execution.",
        ]

        result = validate_question_payload(question=question, options=options)
        self.assertFalse(result.is_valid, "Must reject raw shell lock deletion proposal")
        self.assertTrue(
            any("DESTRUCTIVE_LOCK_COMMAND_VIOLATION" in err for err in result.errors),
            f"Expected DESTRUCTIVE_LOCK_COMMAND_VIOLATION in errors: {result.errors}",
        )

    # -------------------------------------------------------------------------
    # Category 2: Invariant Bypass Attempts
    # -------------------------------------------------------------------------
    def test_cat2_evasive_destructive_variants_rejected(self) -> None:
        """Adversarial bypass attempts using alternate flags, casing, and reset patterns."""
        bypass_options = [
            "Run RM -F .git/worktrees/target/index.lock (Confidence: HIGH (90%)) — Force unlock.",
            "Execute rm index.lock (Confidence: MEDIUM (70%)) — Delete file lock.",
            "Run git -C target reset HEAD (Confidence: MEDIUM (60%)) — Mixed reset.",
            "Perform rm -rf *.lock (Confidence: LOW (40%)) — Wipe locks.",
            "Execute git reset --hard (Confidence: LOW (30%)) — Reset state.",
        ]

        question = "How should we resolve this index lock conflict?"
        for bad_opt in bypass_options:
            options = [
                "(Recommended) " + bad_opt,
                "Halt process (Confidence: LOW (10%)) — Stand by.",
            ]
            result = validate_question_payload(question=question, options=options)
            self.assertFalse(result.is_valid, f"Must reject evasive lock command: {bad_opt}")
            self.assertTrue(
                any("DESTRUCTIVE_LOCK_COMMAND_VIOLATION" in err for err in result.errors),
                f"Missing DESTRUCTIVE_LOCK_COMMAND_VIOLATION for: {bad_opt}",
            )

    def test_cat2_destructive_command_in_question_prompt_rejected(self) -> None:
        """Rejects raw shell commands even if embedded in the question prompt."""
        bad_question = "Should we run rm -f index.lock to proceed?"
        options = [
            "(Recommended) Run repair tool (Confidence: HIGH (90%)) — Proceed safely.",
            "Cancel (Confidence: LOW (20%)) — Abort.",
        ]
        result = validate_question_payload(question=bad_question, options=options)
        self.assertFalse(result.is_valid, "Must reject destructive command in prompt")
        self.assertTrue(
            any("DESTRUCTIVE_LOCK_COMMAND_VIOLATION in question prompt" in err for err in result.errors),
            f"Expected prompt violation in: {result.errors}",
        )

    # -------------------------------------------------------------------------
    # Category 3: Mandatory Governed Repair Enforcement
    # -------------------------------------------------------------------------
    def test_cat3_lock_context_requires_governed_repair_cli(self) -> None:
        """Lock remediation context without governed CLI reference is rejected."""
        question = "A stale lock file is blocking this directory. How should we proceed?"
        # Neither option mentions tools/pr_governance.py repair-index
        options = [
            "(Recommended) Clear lock manually (Confidence: HIGH (90%)) — Unlock safely.",
            "Cancel process (Confidence: LOW (20%)) — Stand by.",
        ]
        result = validate_question_payload(question=question, options=options)
        self.assertFalse(result.is_valid, "Must mandate governed repair reference")
        self.assertTrue(
            any("MANDATORY_GOVERNED_REPAIR_VIOLATION" in err for err in result.errors),
            f"Expected MANDATORY_GOVERNED_REPAIR_VIOLATION in: {result.errors}",
        )

    def test_cat3_compliant_governed_repair_passes_validation(self) -> None:
        """Options referencing governed CLI repair pass validation cleanly."""
        question = "A stale lock file is blocking this directory. How should we proceed?"
        options = [
            (
                "(Recommended) Run governed workspace repair (Confidence: HIGH (95%)) — "
                "Reclaim abandoned lock via .venv/bin/python tools/pr_governance.py repair-index session_2."
            ),
            "Cancel processing (Confidence: LOW (20%)) — Do not modify workspace.",
        ]
        result = validate_question_payload(question=question, options=options)
        self.assertTrue(result.is_valid, f"Expected compliant payload to pass: {result.errors}")

    def test_cat3_question_builder_raises_on_destructive_lock_option(self) -> None:
        """QuestionBuilder strictly raises QuestionPayloadValidationError on destructive option."""
        builder = QuestionBuilder(
            question="A stale lock file was detected. How should we proceed?"
        )
        builder.add_option(
            label="Run raw shell deletion",
            plain_english_summary="Delete lock file via rm -f index.lock",
            impact_statement="Removes the lock but risks unextracted deletions",
            confidence_score=0.95,
            recommended=True,
        )
        builder.add_option(
            label="Cancel",
            plain_english_summary="Abort task safely",
            impact_statement="Preserves workspace in current state",
            confidence_score=0.20,
        )

        with pytest.raises(QuestionPayloadValidationError) as exc_info:
            builder.build()

        self.assertIn("DESTRUCTIVE_LOCK_COMMAND_VIOLATION", str(exc_info.value))


if __name__ == "__main__":
    unittest.main()
