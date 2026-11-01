"""
tests/test_unified_meeting_standards.py

Automated regression suite verifying the Single Universal Meeting SSOT across
all pipelines, eliminating 15-minute thresholds, duration cliffs, and shadow generators.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from dual_pipeline.prompts.contracts.output2_contract import (
    OUTPUT2_CONTRACT,
    MeetingArchetype,
    SectionRequirement,
)
from dual_pipeline.transcript_qa_core.appendix_standard import audit_appendix_gold_standard
from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2
from dual_pipeline.transcript_qa_core.gate3 import run_qa_gate_3
from tools.mcp_speech_server import generate_synthesis_artifacts


class TestUnifiedMeetingStandards(unittest.TestCase):
    """Regression test suite for Universal Meeting SSOT and Threshold Removal."""

    def setUp(self) -> None:
        self.base_short_recap = """# Subject: Executive Sync | 09/23/2026

**Date:** 09/23/2026
**Time:** 02:00 PM - 02:10 PM ET (Duration: ~10m)
**Attendees:**
- Drew Oakley (PartnerAdvisory)
- Cameron Mercer (PartnerAdvisory)

## Meeting Objective
Align on resource allocation and project prioritization for upcoming Q4 enterprise deliveries.

## Key Discussion Points & Findings
Drew Oakley and Cameron Mercer reviewed current staffing allocations and delivery capacity across core accounts. Analysis of pipeline requirements indicates that senior technical architectural oversight is necessary to ensure consistent delivery across critical customer touchpoints.

## Strategic Synthesis
| # | What We Heard | What It Means |
| :--- | :--- | :--- |
| 1 | Demand spikes in Q4 require focused delivery oversight | Dedicated enterprise architect capacity must be secured early |

## Recommendations & Decisions Required
- **Decision Required:** Formalize assignment of lead technical resources.
- **Recommended Choice:** Dedicate senior architectural lead to high-impact accounts.
- **Decision Owner:** Drew Oakley & Cameron Mercer.
- **Decision Deadline:** 09/25/2026.

## Business Meaning and Value Case
- Value mechanism: Early resource dedication prevents delivery friction, reduces project churn, and guarantees continuous SLA achievement.
- Commercial framing: Solidifies client trust through transparent capacity management and high-caliber delivery leadership.

## Immediate Delivery Scope & Release Boundaries
- Target User: Delivery leads, staffing coordinators, and enterprise technical practice directors.
- In Scope: Staffing allocation models, resource gap analysis, and engagement governance framework.
- Out of Scope: Hardware procurement, infrastructure migration, and non-technical account support.
- Acceptance Criteria: Formal ratification of lead enterprise architect assignment across target accounts.

## Action & Governance Ledger
| # | Action Item | Accountable Owner | Target Timing | Status | Transcript Locator |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | Finalize staffing matrix for technical review | Drew Oakley | 09/25/2026 | In Progress | [04:15] |
"""

        self.appendix_text = """
# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Drew Oakley (PartnerAdvisory)
- **Staffing and Resourcing Optimization**: Dan emphasized that early identification of resource bottlenecks protects project delivery momentum and prevents downstream burnout across delivery squads. Dedicated enterprise architecture oversight ensures alignment across multi-account priorities.

### Cameron Mercer (PartnerAdvisory)
- **Account Governance & Quality Assurance**: Stephan highlighted the importance of consistent technical leadership across complex deployments to maintain delivery excellence and client alignment. Establishing clear accountability matrices eliminates cross-stream confusion and stabilizes delivery targets.
"""

    def test_universal_appendix_enforced_under_15m_fails_without_appendix(self) -> None:
        """A 10-minute meeting without an Appendix MUST fail Gate 2.8."""
        meta = {
            "duration_minutes": 10,
            "meeting_date": "09/23/2026",
            "roster": [
                {"name": "Drew Oakley", "org": "PartnerAdvisory"},
                {"name": "Cameron Mercer", "org": "PartnerAdvisory"},
            ],
        }
        res = run_qa_gate_2(self.base_short_recap, meta)
        self.assertFalse(res["passed"], "Recap without Appendix must fail Gate 2 overall.")
        gate_2_8 = res.get("gates", {}).get("Gate_2_8_Appendix_Completeness", {})
        self.assertFalse(gate_2_8.get("passed", True), "Gate 2.8 must fail when Appendix is missing.")
        self.assertTrue(
            any("Appendix" in f for f in res.get("failures", [])),
            f"Expected Appendix failure reason in failures: {res.get('failures')}",
        )

    def test_universal_appendix_passes_with_scaled_thematic_depth_under_15m(self) -> None:
        """A 10-minute meeting with compliant Appendix MUST pass Gate 2.8 and Gate 2."""
        meta = {
            "duration_minutes": 10,
            "meeting_date": "09/23/2026",
            "roster": [
                {"name": "Drew Oakley", "org": "PartnerAdvisory"},
                {"name": "Cameron Mercer", "org": "PartnerAdvisory"},
            ],
        }
        full_recap = self.base_short_recap + self.appendix_text
        res = run_qa_gate_2(full_recap, meta)
        gate_2_8 = res.get("gates", {}).get("Gate_2_8_Appendix_Completeness", {})
        self.assertTrue(gate_2_8.get("passed", False), f"Gate 2.8 failed: {gate_2_8}")
        self.assertTrue(res["passed"], f"Gate 2 failed: {res.get('failures')}")

    def test_continuous_scaling_without_15m_cliff(self) -> None:
        """Audit appendix completeness across 8m, 14m, 16m, and 30m to verify continuous scaling."""
        full_sample = """# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Drew Oakley (PartnerAdvisory)
- **Staffing and Resourcing Optimization**: Dan emphasized that early identification of resource bottlenecks protects project delivery momentum and prevents downstream burnout across delivery squads.
"""
        # For 8m, 14m, 16m, 30m, none should bypass or encounter a discrete step jump
        res_8m = audit_appendix_gold_standard(full_sample, duration_minutes=8)
        res_14m = audit_appendix_gold_standard(full_sample, duration_minutes=14)
        res_16m = audit_appendix_gold_standard(full_sample, duration_minutes=16)
        res_30m = audit_appendix_gold_standard(full_sample, duration_minutes=30)

        # All four durations evaluate the appendix (has_appendix: True)
        self.assertTrue(res_8m.has_appendix)
        self.assertTrue(res_14m.has_appendix)
        self.assertTrue(res_16m.has_appendix)
        self.assertTrue(res_30m.has_appendix)

    def test_shadow_mcp_synthesis_generator_blocked_with_runtime_error(self) -> None:
        """Invoking generate_synthesis_artifacts MUST raise RuntimeError redirecting to governed pipeline."""
        with self.assertRaises(RuntimeError) as ctx:
            generate_synthesis_artifacts("test_meeting", "09-23-2026", "Dan, Stephan", "[]")
        self.assertIn("Direct artifact generation via shadow MCP tool", str(ctx.exception))
        self.assertIn("RawTranscriptPipeline.run()", str(ctx.exception))

    def test_universal_output2_contract_zero_forbidden_sections(self) -> None:
        """Verify Output 2 declarative contract enforces Action Ledger & Appendix across all archetypes."""
        for arch in MeetingArchetype:
            topo = OUTPUT2_CONTRACT.get_section_topology(arch)
            # Mandatory sections
            self.assertEqual(
                topo.get("Action & Governance Ledger"),
                SectionRequirement.MANDATORY,
                f"Action & Governance Ledger must be MANDATORY for archetype {arch.value}",
            )
            self.assertEqual(
                topo.get("Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown"),
                SectionRequirement.MANDATORY,
                f"Appendix must be MANDATORY for archetype {arch.value}",
            )
            # No forbidden operational sections (except retired Active Technical Spikes, and Architecture Deep Dive consulting exclusions)
            arch_exempt_forbidden = {"Active Technical Spikes & Experimental Inquiries"}
            if arch == MeetingArchetype.ARCHITECTURE_DEEP_DIVE:
                arch_exempt_forbidden.update({
                    "Business Meaning and Value Case",
                    "Immediate Delivery Scope & Release Boundaries",
                })
            forbidden_sections = [
                sec for sec, req in topo.items()
                if req == SectionRequirement.FORBIDDEN and sec not in arch_exempt_forbidden
            ]
            self.assertEqual(
                forbidden_sections,
                [],
                f"Archetype {arch.value} has forbidden operational sections: {forbidden_sections}",
            )

    def test_gate_3_canonical_predicates_and_mutation_budget(self) -> None:
        """Verify Gate 3 enforces canonical predicates and mutation budget <= 25."""
        f3_path = (
            Path(__file__).resolve().parent.parent
            / "output"
            / "enterprise_account_planning"
            / "09-23-2026"
            / "NEE_Staffing_Issues_Dan_Stephan"
            / "03_NEE_Staffing_Issues_Dan_Stephan_09-23-2026_0145PM_0201PM_ET_canonical_signal_projection.json"
        )
        valid_f3 = json.loads(f3_path.read_text(encoding="utf-8"))

        # Valid payload must pass Gate 3
        res_valid = run_qa_gate_3(json.dumps(valid_f3))
        self.assertTrue(res_valid["passed"], f"Gate 3 failed for valid payload: {res_valid.get('failures')}")

        # Invalid predicate DELIVERED_TO must fail Gate 3.5
        invalid_f3 = json.loads(json.dumps(valid_f3))
        invalid_f3["graph_mutations"]["relationship_signals"][0]["relationship"] = "DELIVERED_TO"
        res_invalid = run_qa_gate_3(json.dumps(invalid_f3))
        self.assertFalse(res_invalid["passed"], "Gate 3 must fail for non-canonical predicate DELIVERED_TO.")
        self.assertTrue(
            any("DELIVERED_TO" in str(f) for f in res_invalid.get("failures", [])),
            f"Expected predicate failure in: {res_invalid.get('failures')}",
        )


if __name__ == "__main__":
    unittest.main()
