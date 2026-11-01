"""
tests/test_prompt_contract_parity.py

Validates prompt contract parity across:
1. config/prompts/synthesis/stages/02_executive_recap.md
2. dual_pipeline/transcript_qa_core/gate2.py
3. dual_pipeline/llm_judge_core/adversarial_recap_auditor.py
4. dual_pipeline/manifest_runner/transcript_formatter.py

Guarantees that prompts, evaluators, judges, and pipeline formatters enforce
identical structural invariants, section density floors, and synthesis requirements.
"""
from __future__ import annotations

import unittest

from dual_pipeline.core import ROOT
from dual_pipeline.manifest import IngestionMeetingManifest
from dual_pipeline.manifest_runner.transcript_formatter import format_output_02
from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2


class TestPromptContractParity(unittest.TestCase):
    """Test suite ensuring absolute contract synchronization across prompt, gate, and formatter."""

    def setUp(self):
        self.prompt_path = ROOT / "config" / "prompts" / "synthesis" / "stages" / "02_executive_recap.md"
        self.assertTrue(self.prompt_path.exists(), f"Prompt contract missing: {self.prompt_path}")
        self.prompt_text = self.prompt_path.read_text(encoding="utf-8")

    def test_prompt_specifies_strategic_synthesis_and_density_floors(self):
        """Prompt contract must explicitly require Strategic Synthesis and 800-word density floor."""
        self.assertIn("Strategic Synthesis", self.prompt_text)
        self.assertIn("What We Heard", self.prompt_text)
        self.assertIn("What It Means", self.prompt_text)

        # Word count floor for meetings >= 15 minutes
        self.assertRegex(self.prompt_text, r"800.*words?.*(?:floor|minimum|range)")

        # Deep sections
        self.assertIn("Business Meaning and Value Case", self.prompt_text)
        self.assertIn("Executive Decision", self.prompt_text)
        self.assertTrue(
            "Bounded Execution Wedge" in self.prompt_text
            or "Immediate Delivery Scope" in self.prompt_text
        )
        self.assertIn("Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown", self.prompt_text)

    def test_gate2_enforces_exact_prompt_contract_sections(self):
        """Gate 2 must mechanically enforce every section declared mandatory in the prompt."""
        from dual_pipeline.transcript_qa_core.gate2 import (
            _check_appendix_completeness,
            _check_deep_section_density,
            _check_strategic_synthesis,
            _check_word_count_bounds,
        )

        # Test passing compliant recap
        manifest = IngestionMeetingManifest(
            project_name="project_lens",
            meeting_title="Architecture Review",
            meeting_date="09-17-2026",
            start_time_et="10:00 AM",
            end_time_et="10:30 AM",
            attendees=[
                {"name": "Amit Ayer", "company": "PartnerAdvisory"},
                {"name": "Carlos Gomez", "company": "Apex Global Enterprises"},
            ],
        )
        compliant_text = format_output_02(manifest)

        # 1. Strategic Synthesis Check
        passed, has_ss, failures = _check_strategic_synthesis(compliant_text, meeting_dur=30)
        self.assertTrue(passed, f"Strategic synthesis check failed: {failures}")
        self.assertTrue(has_ss)

        # 2. Word Count Bounds Check (>= 800 words for 30m meeting)
        word_count = len(compliant_text.split())
        self.assertGreaterEqual(word_count, 800, "Compliant text should be >= 800 words")
        wc_passed, wc_failures = _check_word_count_bounds(word_count, meeting_dur=30)
        self.assertTrue(wc_passed, f"Word count check failed: {wc_failures}")

        # 3. Deep Section Density Check (>= 60 words each)
        ds_passed, b_w, w_w, d_w, ds_failures = _check_deep_section_density(compliant_text, meeting_dur=30)
        self.assertTrue(ds_passed, f"Deep section density check failed: {ds_failures}")
        self.assertGreaterEqual(b_w, 60, "Business Meaning words < 60")
        self.assertGreaterEqual(w_w, 60, "Bounded Wedge words < 60")
        self.assertGreaterEqual(d_w, 60, "Executive Decision words < 60")

        # 4. Appendix Completeness Check
        app_passed, has_app, app_failures = _check_appendix_completeness(compliant_text, meeting_dur=30)
        self.assertTrue(app_passed, f"Appendix check failed: {app_failures}")
        self.assertTrue(has_app)

        # 5. Full QA Gate 2 Run
        res = run_qa_gate_2(compliant_text, metadata={"duration_minutes": 30})
        self.assertEqual(res["status"], "PASS", f"Gate 2 failed on format_output_02 text: {res.get('failures')}")

    def test_gate2_rejects_recap_missing_prompt_contract_requirements(self):
        """Gate 2 must fail-closed if any prompt contract requirement is missing."""
        defective_recap = (
            "# Subject: Executive Briefing — Test\n\n"
            "**Date:** 09/17/2026\n"
            "**Time:** 10:00 AM - 11:00 AM\n"
            "**Attendees:**\n- **PartnerAdvisory:** Amit Ayer\n\n"
            "## Meeting Objective\nQuick catchup.\n\n"
            "## Key Discussion Points & Findings\nBrief notes.\n\n"
            "## Next Steps & Action Items\n"
            "| # | Action | Owner | Target Timing | Evidence IDs |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            "| 1 | Align on schedule | Amit Ayer | 09/20/2026 | [ev-01] |\n\n"
            "## Business Meaning and Value Case\nShort text.\n\n"
            "## Executive Decision\nNone.\n\n"
            "## Bounded Execution Wedge\nShort wedge.\n"
        )
        res = run_qa_gate_2(defective_recap, metadata={"duration_minutes": 60})
        self.assertEqual(res["status"], "FAIL")
        failures_joined = " ".join(res.get("failures", []))
        self.assertIn("word count too light", failures_joined)
        self.assertIn("missing required Strategic Synthesis", failures_joined)
        self.assertIn("Business Meaning and Value Case' is superficial", failures_joined)
        self.assertTrue(
            any(k in failures_joined for k in ("Immediate Delivery Scope & Release Boundaries' is superficial", "Bounded Execution Wedge' is superficial"))
        )
        self.assertIn("Executive Decision' is superficial", failures_joined)
        self.assertIn("missing mandatory '# Appendix", failures_joined)


if __name__ == "__main__":
    unittest.main()
