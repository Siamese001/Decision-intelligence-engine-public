"""
tests/test_recap_qa_gates.py

Unit tests for Gate 2.5 Word Count Bounds, Gate 2.6 Strategic Synthesis Matrix,
and Gate 2.7 Deep Section Density.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2


class TestRecapQAGates(unittest.TestCase):
    def setUp(self) -> None:
        self.base_recap = """# Subject: Executive Recap | 09/14/2026

**Date:** 09/14/2026
**Time:** 05:00 PM - 05:30 PM ET (Duration: ~30m)
**Attendees:**
- Dan (PartnerAdvisory)
- Sarah (NEA)

## Meeting Objective
Align on architecture and delivery boundaries.

## Key Discussion Points & Findings
- Substantive discussion point one on observability and user confidence.
- Substantive discussion point two on OCR model disambiguation.

## Strategic Synthesis
| # | What We Heard (NEA Operational Friction) | What It Means (Enterprise Architecture Implication) |
| :--- | :--- | :--- |
| 1 | Lack of observability causes software abandonment | Must provide pre-commit preview table to verify impact before applying rules |

## Business Meaning and Value Case
- **Business Problem:** Opaque rule execution risks user abandonment across rotating field engineering cohorts.
- **Value at Stake:** Prevents costly rollbacks and manual auditing cycles.
- **Target:** Full adoption with self-explanatory preview interfaces.
- **Value Mechanism:** Pre-commit preview table and structured audit records provide immediate verification.

## Executive Decision
- **Decision Required:** Formal agreement on SOW 1 acceptance line.
- **Recommended Choice:** Restrict SOW 1 to sample-validation preview table and direct queue linking.
- **Decision Owner:** Dan & Sarah.
- **Decision Deadline:** 09/15/2026.

## Bounded Execution Wedge
- **Target User:** EOSS Field Engineers and Rule Authors.
- **In Scope:** Six-row preview table and direct links from confirmation dialog.
- **Out of Scope:** Full historical reprocessing backfills.
- **First Deliverable:** Wireframes for preview modal.
- **Acceptance Test:** Validate candidate rule across 6 sample photos.

## Action & Governance Ledger
| # | Action Item | Company | Owner | Target Date / Cadence | Transcript Locator |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | Prepare preview table wireframes | PartnerAdvisory | Dan | 09/15/2026 | [10:00] |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Dan (PartnerAdvisory)
- **Observability Strategy**: Dan emphasized that lack of observability causes user abandonment across rotating field engineering cohorts, arguing that an interactive pre-commit preview table is critical to demonstrate value and prevent costly rollbacks.

### Sarah (NEA)
- **Technical Architecture Constraints**: Sarah confirmed enterprise technical constraints and data availability, noting that sample photos must be evaluated against candidate rules to ensure high precision before any wide release.
"""

    def test_gate_2_6_strategic_synthesis_matrix_presence_and_conditional_omission(self) -> None:
        """Under Two Universal Mandatory Sections topology, Strategic Synthesis presence is tracked and conditional."""
        # 1. When present in base recap
        res_present = run_qa_gate_2(self.base_recap)
        self.assertTrue(res_present["gates"]["Gate_2_6_Strategic_Synthesis_Matrix"]["has_strategic_synthesis"])

        # 2. When missing, has_strategic_synthesis is False
        text = self.base_recap.replace("## Strategic Synthesis", "## Notes")
        text = text.replace("| What We Heard (NEA Operational Friction) | What It Means (Enterprise Architecture Implication) |", "| Col1 | Col2 |")
        res_missing = run_qa_gate_2(text)
        self.assertFalse(res_missing["gates"]["Gate_2_6_Strategic_Synthesis_Matrix"]["has_strategic_synthesis"])

    def test_gate_2_7_fails_when_bounded_wedge_superficial(self) -> None:
        """Recap with <60 words in Bounded Execution Wedge must fail."""
        text = self.base_recap.replace(
            """## Bounded Execution Wedge
- **Target User:** EOSS Field Engineers and Rule Authors.
- **In Scope:** Six-row preview table and direct links from confirmation dialog.
- **Out of Scope:** Full historical reprocessing backfills.
- **First Deliverable:** Wireframes for preview modal.
- **Acceptance Test:** Validate candidate rule across 6 sample photos.""",
            """## Bounded Execution Wedge
- Small brief note.""",
        )
        res = run_qa_gate_2(text)
        self.assertEqual(res["status"], "FAIL")
        self.assertFalse(res["gates"]["Gate_2_7_Deep_Section_Density"]["passed"])
        self.assertTrue(any("Bounded Execution Wedge" in f for f in res["failures"]))

    def test_immediate_delivery_scope_passes_gate_2(self) -> None:
        """Recap using renamed 'Immediate Delivery Scope & Release Boundaries' must pass."""
        text = self.base_recap.replace(
            "## Bounded Execution Wedge",
            "## Immediate Delivery Scope & Release Boundaries",
        ).replace("Duration: ~30m", "Duration: ~10m")
        res = run_qa_gate_2(text)
        self.assertEqual(res["status"], "PASS")
        self.assertTrue(res["gates"]["Gate_2_1_Parsed_Structural_Completeness"]["passed"])
        self.assertTrue(res["gates"]["Gate_2_7_Deep_Section_Density"]["passed"])

    def test_clustered_minto_pyramid_recap_passes_gate_2(self) -> None:
        """Recap with Executive Decision at the top and clustered H3 sections must pass."""
        clustered_recap = """# Subject: Executive Decision Brief -- Project LENS Catchup | 09/14/2026

**Date:** 09/14/2026
**Time:** 05:00 PM - 05:10 PM ET (Duration: ~10m)
**Attendees:**
- Dan (PartnerAdvisory)
- Sarah (NEA)

## 1. Executive Summary & Core Decision (BLUF)

### Executive Decision
- **Decision Required:** Formal agreement on SOW 1 acceptance line and release boundaries.
- **Recommended Choice:** Restrict SOW 1 to sample-validation preview table and direct queue linking.
- **Decision Owner:** Dan (PartnerAdvisory) & Sarah (NEA).
- **Decision Deadline:** 09/15/2026.
- **Consequence of Delay:** SOW 1 acceptance freeze and team reallocation risks across Q4 delivery sprints.

### Meeting Objective
Confront live demo validation failures, address client dissatisfaction regarding rule visibility, and align on proposed SOW 1 acceptance criteria.

## 2. Strategic Findings & Business Impact

### Key Discussion Points & Findings
- Substantive discussion point one on observability and user confidence.
- Substantive discussion point two on OCR model disambiguation.

### Strategic Synthesis
| # | What We Heard (NEA Operational Friction) | What It Means (Enterprise Architecture Implication) |
| :--- | :--- | :--- |
| 1 | Lack of observability causes software abandonment | Must provide pre-commit preview table to verify impact before applying rules |

### Business Meaning and Value Case
- **Business Problem:** Opaque rule execution risks user abandonment across rotating field engineering cohorts.
- **Value at Stake:** Prevents costly rollbacks, field distrust, and manual auditing cycles.
- **Target:** Full adoption with self-explanatory preview interfaces and verified accuracy.
- **Value Mechanism:** Pre-commit preview table and structured audit records provide immediate verification.

## 3. Immediate Delivery Scope & Release Boundaries
- **Target User:** EOSS Field Engineers and Rule Authors.
- **In Scope:** Six-row preview table and direct links from confirmation dialog.
- **Out of Scope:** Full historical reprocessing backfills across legacy catalogs.
- **First Deliverable:** Wireframes for preview modal.
- **Acceptance Test:** Validate candidate rule across 6 representative sample photos.

## 4. Governance & Action Ledger

### Next Steps & Action Items
| # | Action Item | Company | Owner | Target Date / Cadence | Transcript Locator |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | Prepare preview table wireframes | PartnerAdvisory | Dan | 09/15/2026 | [10:00] |

### Open Decisions / Blockers
- Final signoff on SOW 1 feature line scheduled for tomorrow.

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Dan (PartnerAdvisory)
- **Observability Strategy**: Dan emphasized that lack of observability causes user abandonment across rotating field engineering cohorts, arguing that an interactive pre-commit preview table is critical to demonstrate value and prevent costly rollbacks.

### Sarah (NEA)
- **Technical Architecture Constraints**: Sarah confirmed enterprise technical constraints and data availability, noting that sample photos must be evaluated against candidate rules to ensure high precision before any wide release.
"""
        res = run_qa_gate_2(clustered_recap)
        self.assertEqual(res["status"], "PASS")
        self.assertTrue(res["gates"]["Gate_2_1_Parsed_Structural_Completeness"]["passed"])
        self.assertTrue(res["gates"]["Gate_2_6_Strategic_Synthesis_Matrix"]["passed"])
        self.assertTrue(res["gates"]["Gate_2_7_Deep_Section_Density"]["passed"])

    def test_recap_with_no_double_hashes_or_stars_passes_gate_2(self):
        clean_recap = """# Subject: Project LENS Catchup -- SOW 1 Observability | 09/14/2026

Date: 09/14/2026
Time: 05:02 PM - 05:12 PM ET (Duration: ~10m)
Attendees:
- Apex Global Enterprises (NEA): harper Hoffman, Brett Gavagni
- PartnerAdvisory: Jennifer Pan, Alex Sanchez, Amit Ayer

# Meeting Objective & Operational Context
Project LENS is an enterprise computer vision and asset verification platform built by PartnerAdvisory for Apex Global Enterprises. This catchup was convened to confront live demo validation failures, address explicit client feedback regarding product readiness, and resolve the SOW 1 acceptance line.

# Key Discussion Points, Live Demo Failures & Negative Client Tone
The meeting opened with critical client pushback. harper Hoffman stated that the product is not ready for rollout because field operators cannot verify rule outcomes. Brett Gavagni warned that across seven years at NEA, software rollouts consistently fail when operators lack visibility into backend decision-making.

# Strategic Synthesis
| # | What We Heard | What It Means |
| 1 | The product is not ready | Visual proof required before signoff |

# Business Meaning and Value Case
Business Problem: Opaque rule execution risks user abandonment across rotating field engineering cohorts. Value at Stake: Protects multimillion-dollar field inspection efficiency and avoids field engineer distrust. Target: Verified operator adoption, intuitive rule authoring, pre-commit impact preview across sample photos, and direct navigation hyperlinks. Value Mechanism: Implementing the 6-row preview table and structured audit logs provides immediate empirical proof of correctness prior to database commit.

# Executive Decision & Acceptance Standoff
Executive Conclusion: SOW 1 contractual signoff is actively on hold pending delivery of the preview modal and direct hyperlinks. Decision Required: Formal contractual ratification of the SOW 1 completion line between PartnerAdvisory and Apex Global Enterprises. Current Status: Apex Global Enterprises has frozen acceptance pending delivery of an interactive preview modal and direct hyperlinks. Recommended Choice: Freeze SOW 1 scope around the 6-photo preview modal and direct hyperlinks. Decision Owner: harper Hoffman and Alex Sanchez. Decision Deadline: 09/15/2026. Consequence of Delay: Ongoing scope expansion.

# Immediate Delivery Scope & Release Boundaries
Target User: EOSS Field Engineers and Rule Authors. In Scope for SOW 1 Exit: Six-row pre-commit impact preview modal, direct hyperlink navigation to review queue items, structured evidence record schema, and benchmark test passing on approximately 6 representative photos. Explicitly Out of Scope: Full historical catalog reprocessing and foundational OCR replacement. First Deliverable: UI wireframes for preview modal. Acceptance Test: Mandy validates expected transformations across ~6 benchmark photos with 100% observability.

# Next Steps & Action Items
| # | Action Item | Accountable Owner | Responsible Party | Target Timing | Transcript Locator |
| 1 | Construct Rule Builder UI wireframes | PartnerAdvisory | Jennifer Pan | 09/15/2026 | [35:20] |

# Open Decisions / Blockers
Blocker on SOW 1 signoff until wireframes are approved.

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

# harper Hoffman - Apex Global Enterprises, Client Lead
- Observability and User Verification: Mandy stressed that the software cannot proceed to production rollout without direct visual verification of decision rules. Field engineers require verifiable proof on turbine image assets before committing decisions.

# Alex Sanchez - PartnerAdvisory, Solution Architect
- Pipeline Architecture and Preview Implementation: Alex outlined the technical path for the pre-commit preview table, committing to delivering interactive wireframes and establishing automated unit test suites for model validation across turbine assets.
"""
        self.assertEqual(clean_recap.count("##"), 0)
        self.assertEqual(clean_recap.count("**"), 0)
        res = run_qa_gate_2(clean_recap)
        self.assertEqual(res["status"], "PASS")
        self.assertTrue(res["gates"]["Gate_2_1_Parsed_Structural_Completeness"]["passed"])
        self.assertTrue(res["gates"]["Gate_2_2_Typed_Action_Register"]["passed"])
        self.assertTrue(res["gates"]["Gate_2_6_Strategic_Synthesis_Matrix"]["passed"])
        self.assertTrue(res["gates"]["Gate_2_7_Deep_Section_Density"]["passed"])

    def test_synthetic_observability_recap_has_no_double_hashes_or_stars_and_passes(self):
        from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer

        clean_input = (
            self.base_recap.replace("### ", "# ")
            .replace("## ", "# ")
            .replace("**", "")
            .replace("Duration: ~30m", "Duration: ~10m")
            .replace("# Bounded Execution Wedge", "# Immediate Delivery Scope & Release Boundaries")
        )
        healed_text, _ = DeterministicHealer.heal_output_2(
            clean_input,
            metadata={
                "meeting_date": "09/14/2026",
                "duration_minutes": 10,
                "roster": [{"name": "Dan", "org": "PartnerAdvisory"}, {"name": "Sarah", "org": "NEA"}],
            },
        )
        self.assertEqual(healed_text.count("##"), 0)
        self.assertEqual(healed_text.count("**"), 0)
        res = run_qa_gate_2(healed_text, {"duration_minutes": 10})
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["failures"], [])

    def test_offline_synthesizer_generates_appendix_for_long_meetings(self) -> None:
        from dual_pipeline.transcript_stages_core.offline_synthesizer import (
            OfflineTranscriptSynthesizer,
        )
        provider = OfflineTranscriptSynthesizer()
        recap = provider.synthesize_recap(
            system_prompt_or_prompt="Stage 2 prompt",
            user_content_or_metadata={
                "meeting_name": "Test Long Meeting",
                "date": "09/17/2026",
                "time": "10:00AM - 11:00AM",
                "duration_minutes": 60,
                "attendees": [
                    {"name": "Amit Ayer", "company": "PartnerAdvisory", "role": "Enterprise Architect"},
                    {"name": "Stefan", "company": "NEA", "role": "Executive Sponsor"},
                ],
            },
        )
        self.assertIn("# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown", recap)
        self.assertTrue(re.search(r"#{1,3}\s+Amit Ayer", recap))
        self.assertTrue(re.search(r"#{1,3}\s+Stefan", recap))

    def test_deterministic_healer_scaffolds_appendix_when_missing(self) -> None:
        from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
        app_header = "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown"
        recap_without_app = self.base_recap[:self.base_recap.find(app_header)]
        self.assertNotIn("# Appendix:", recap_without_app)
        healed, changes = DeterministicHealer.heal_output_2(
            recap_without_app,
            metadata={"duration_minutes": 30, "roster": [{"name": "Dan", "org": "PartnerAdvisory"}, {"name": "Sarah", "org": "NEA"}]},
        )
        self.assertIn("# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown", healed)
        self.assertTrue(re.search(r"#{1,3}\s+Dan", healed))
        self.assertTrue(re.search(r"#{1,3}\s+Sarah", healed))
        self.assertTrue(any("Appendix" in c for c in changes))

    def test_gate_2_8_fails_when_appendix_missing_for_long_meeting(self) -> None:
        """Recap for 30m meeting missing Appendix must fail Gate 2.8."""
        app_header = "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown"
        text = self.base_recap[:self.base_recap.find(app_header)]
        res = run_qa_gate_2(text)
        self.assertEqual(res["status"], "FAIL")
        self.assertFalse(res["gates"]["Gate_2_8_Appendix_Completeness"]["passed"])
        self.assertTrue(any("Appendix" in f for f in res["failures"]))

    def test_gate_2_8_fails_when_appendix_has_no_speaker_subsections(self) -> None:
        """Recap for 30m meeting with superficial Appendix lacking speaker subsections must fail Gate 2.8."""
        app_header = "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown"
        app_idx = self.base_recap.find(app_header)
        text = self.base_recap[:app_idx + len(app_header)] + "\n\nGeneral notes without any named speaker subsections.\n"
        res = run_qa_gate_2(text)
        self.assertEqual(res["status"], "FAIL")
        self.assertFalse(res["gates"]["Gate_2_8_Appendix_Completeness"]["passed"])
        self.assertTrue(any("Appendix is superficial" in f for f in res["failures"]))

    def test_llm_judge_fails_when_appendix_missing_for_long_meeting(self) -> None:
        """LLM Judge must fail closed when candidate recap for >=15m meeting lacks Appendix."""
        from dual_pipeline.llm_judge_core.judges.recap import ExecutiveRecapJudge
        judge = ExecutiveRecapJudge()
        app_header = "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown"
        text_without_app = self.base_recap[:self.base_recap.find(app_header)]
        res = judge.evaluate(f2_text=text_without_app, f1_text="Forensic transcript content Duration: ~30m", mock=True)
        self.assertEqual(res["verdict"], "FAIL")
        self.assertIn("Appendix", res.get("forensic_rationale", ""))

    def test_gate_2_8_fails_when_boilerplate_detected(self) -> None:
        """Recap for 30m meeting with generic 3-bullet placeholder boilerplate must fail Gate 2.8."""
        app_header = "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown"
        app_idx = self.base_recap.find(app_header)
        boilerplate_sec = (
            "\n\n### Dan (PartnerAdvisory)\n"
            "- **Core Thesis & Strategic Perspective:** Endorsed architectural decoupling.\n"
            "- **Key Contributions & Discussion Focus:** Affirmed data lineage.\n"
            "- **Commitments & Next Steps:** Confirmed ownership.\n"
        )
        text = self.base_recap[:app_idx + len(app_header)] + boilerplate_sec
        res = run_qa_gate_2(text)
        self.assertEqual(res["status"], "FAIL")
        self.assertFalse(res["gates"]["Gate_2_8_Appendix_Completeness"]["passed"])
        self.assertTrue(any("BOILERPLATE_DETECTED" in f for f in res["failures"]))

    def test_gate_2_8_fails_when_speaker_section_thin(self) -> None:
        """Recap for 30m meeting with speaker section < 40 words must fail Gate 2.8."""
        app_header = "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown"
        app_idx = self.base_recap.find(app_header)
        thin_sec = (
            "\n\n# Drew Oakley - PartnerAdvisory, Director\nShort note on architecture.\n"
            "# Sarah Jenkins - NEA, Director\nAnother short note.\n"
        )
        text = self.base_recap[:app_idx + len(app_header)] + thin_sec
        res = run_qa_gate_2(text)
        self.assertEqual(res["status"], "FAIL")
        self.assertFalse(res["gates"]["Gate_2_8_Appendix_Completeness"]["passed"])
        self.assertTrue(any("THIN_SPEAKER_SECTION" in f for f in res["failures"]))

    def test_gate_2_8_fails_when_cross_speaker_duplication_detected(self) -> None:
        """Recap where speakers share copy-pasted multi-sentence phrases must fail Gate 2.8."""
        dupe_text = (
            "We must ensure that the pre-flight preview table provides verified accuracy across all sample photos "
            "before database commit. This architecture decouples on-device checks from cloud model invocations completely."
        )
        app_header = "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown"
        app_idx = self.base_recap.find(app_header)
        dupe_sec = (
            f"\n\n# Drew Oakley - PartnerAdvisory, Director\n{dupe_text}\n"
            f"# Sarah Jenkins - NEA, Director\n{dupe_text}\n"
        )
        text = self.base_recap[:app_idx + len(app_header)] + dupe_sec
        res = run_qa_gate_2(text)
        self.assertEqual(res["status"], "FAIL")
        self.assertFalse(res["gates"]["Gate_2_8_Appendix_Completeness"]["passed"])
        self.assertTrue(any("CROSS_SPEAKER_DUPLICATION" in f for f in res["failures"]))


if __name__ == "__main__":
    unittest.main()

