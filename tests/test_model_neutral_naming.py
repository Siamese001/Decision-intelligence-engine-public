"""Unit tests for Model-Neutral Artifact Naming and Pre-Commit Sanitization.

Tests:
1. Prohibited model token detection in filenames and paths (gpt*, astra, luna, sol, terra, claude*, gemini*, llama*, haiku, sonnet, opus, o1, o3).
2. Domain-functional filename acceptance.
3. Path exemption validation (.agents, contract schemas, test fixtures).
4. Rule C-19 career leverage token detection in scan_credential_leaks.py.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.lint_model_neutral_naming import (
    is_path_exempt,
    scan_path_for_model_tokens,
)
from tools.scan_credential_leaks import scan_file


class TestModelNeutralNaming(unittest.TestCase):
    """Verifies all invariants of tools/lint_model_neutral_naming.py."""

    def test_prohibited_tokens_rejected(self) -> None:
        """Verifies files with LLM model names or tier tags are flagged as violations."""
        prohibited_paths = [
            "reports/gpt56_luna_eval.md",
            "reports/luna_recap.md",
            "docs/sol_high_spec.md",
            "output/project/09-10-2026/01_claude_transcript.md",
            "receipts/gemini_receipt.json",
            "docs/llama_notes.md",
            "reports/o1_analysis.md",
            "reports/o3_mini_run.md",
            "docs/haiku_plan.md",
            "docs/sonnet_review.md",
            "receipts/opus_eval.json",
            "output/apex_trans/terra_synthesis.json",
        ]

        for path in prohibited_paths:
            violations = scan_path_for_model_tokens(path)
            self.assertGreater(
                len(violations),
                0,
                f"Expected prohibited token violation for path: {path}",
            )
            self.assertIn(
                violations[0].matched_token.lower(),
                path.lower(),
                f"Matched token should be substring of {path}",
            )

    def test_domain_functional_paths_accepted(self) -> None:
        """Verifies domain-bound, functional artifact paths pass with 0 violations."""
        valid_paths = [
            "reports/output2_hardening_refactor_specification.md",
            "reports/wave1_layer_governance_receipt.json",
            "reports/local_main_readiness_receipt.json",
            "output/apex_trans/09-10-2026/01_NEET_Insights_Hub_Interview_hitl_resolved_transcript.md",
            "output/apex_trans/09-10-2026/02_NEET_Insights_Hub_Interview_executive_recap.md",
            "output/apex_trans/09-10-2026/03_NEET_Insights_Hub_Interview_canonical_signal_projection.json",
            "docs/architecture_decision_record.md",
            "receipts/wave2_sanitization_receipt.json",
            "dual_pipeline/transcript_pipeline_core/pipeline.py",
            "storage/database.py",
        ]

        for path in valid_paths:
            violations = scan_path_for_model_tokens(path)
            self.assertEqual(
                len(violations),
                0,
                f"Expected 0 violations for valid domain path: {path}, got {violations}",
            )

    def test_exemptions_respected(self) -> None:
        """Verifies grandfathered fixtures, rules, and tests are exempt."""
        exempt_paths = [
            ".agents/rules/model-neutral-artifact-naming.md",
            ".git/HEAD",
            "contracts/dual_pipeline/fixtures/evidence_fixture.json",
            "tests/test_gpt_5_6_sol_high_agents.py",
            "output/gemini_enablement/09-30-2026/Working_Files_Data_Rose_Amanda/final_release_decision.json",
        ]

        for path in exempt_paths:
            self.assertTrue(is_path_exempt(path), f"Path should be exempt: {path}")
            violations = scan_path_for_model_tokens(path)
            self.assertEqual(len(violations), 0, f"Exempt path should have 0 violations: {path}")

    def test_career_leverage_firewall_detection(self) -> None:
        """Verifies scan_credential_leaks flags Rule C-19 career leverage tokens in team deliverables."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            deliverable = root / "output/apex_trans/09-10-2026/02_executive_recap.md"
            deliverable.parent.mkdir(parents=True, exist_ok=True)
            deliverable.write_text(
                "# Executive Recap\n\nExecutive positioning for executive compensation band discussed.\n",
                encoding="utf-8",
            )

            violations = scan_file(deliverable, root)
            career_leaks = [v for v in violations if v.leak_type == "CAREER_FIREWALL_LEAK"]
            self.assertEqual(len(career_leaks), 1)
            self.assertIn("Rule C-19 violation: Personal career leverage token detected", career_leaks[0].detail)


if __name__ == "__main__":
    unittest.main()
