"""
tests/test_gate2_healer_x_gates_sync.py

Unit tests verifying consistency between Gate 2, Pre-Flight Deterministic Healer,
and X1-X3 Exit Gates for clean markdown and structural invariants.
"""

from __future__ import annotations

import unittest

from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.transcript_stages_core.x_gates import XExitGatesEvaluator


class TestGate2HealerXGatesSync(unittest.TestCase):
    def setUp(self) -> None:
        self.clean_recap = """# Subject: Project LENS Catchup -- SOW 1 Observability | 09/14/2026

Date: 09/14/2026
Time: 05:00 PM - 05:10 PM ET (Duration: ~10m)
Attendees:
- Dan (PartnerAdvisory)
- Mandy (NEA)

# 1. Meeting Objective
Align on product boundaries and resolve core operational blockers.

# 2. Key Discussion Points & Findings
- Mandy noted that field operators require immediate observability before adopting automated rules.
- Brett highlighted that lack of visibility has historically caused software abandonment across multiple workstreams.

# 3. Strategic Synthesis
| # | What We Heard | What It Means |
| :--- | :--- | :--- |
| 1 | Product is not ready | Deliver observable preview table before full deployment |

# 4. Business Meaning and Value Case
- Business Problem: Opaque rule execution risks user abandonment across rotating field engineering cohorts.
- Value at Stake: Prevents costly rollbacks, field distrust, and manual auditing cycles.
- Target: Full adoption with self-explanatory preview interfaces and verified accuracy.
- Value Mechanism: Pre-commit preview table and structured audit records provide immediate verification.

# 5. Executive Decision
- Decision Required: Formal agreement on SOW 1 acceptance line and release boundaries.
- Recommended Choice: Restrict SOW 1 to sample-validation preview table and direct queue linking.
- Decision Owner: Dan (PartnerAdvisory) & Mandy (NEA).
- Decision Deadline: 09/15/2026.
- Consequence of Delay: SOW 1 acceptance freeze and team reallocation risks across Q4 delivery sprints.

# 6. Immediate Delivery Scope & Release Boundaries
- Target User: EOSS Field Engineers and Rule Authors.
- In Scope: Six-row preview table and direct links from confirmation dialog.
- Out of Scope: Full historical reprocessing backfills across legacy catalogs.
- First Deliverable: Wireframes for preview modal.
- Acceptance Test: Validate candidate rule across 6 representative sample photos.

# 7. Action & Governance Ledger
| # | Action Item | Accountable Owner | Responsible Party | Target Timing | Evidence IDs | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | Prepare preview table wireframes | Dan | PartnerAdvisory | 09/15/2026 | ev-01 | Proposed |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

# Dan - PartnerAdvisory, Solution Architect
- Observability and User Verification: Dan emphasized that lack of observability causes user abandonment across rotating field engineering cohorts, arguing that an interactive pre-commit preview table is critical to demonstrate value and prevent costly rollbacks.

# Mandy - Apex Global Enterprises, Product Manager
- Technical Architecture Constraints: Mandy confirmed enterprise technical constraints and data availability, noting that sample photos must be evaluated against candidate rules to ensure high precision before any wide release.
"""

    def test_clean_recap_passes_gate_2(self) -> None:
        """Verify that clean single-hash recap passes Gate 2 with 100% gate pass rate."""
        result = run_qa_gate_2(self.clean_recap)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["gates"]["Gate_2_1_Parsed_Structural_Completeness"]["passed"])
        self.assertTrue(result["gates"]["Gate_2_2_Typed_Action_Register"]["passed"])
        self.assertTrue(result["gates"]["Gate_2_4_Archetype_and_Altitude_Governance"]["passed"])
        self.assertTrue(result["gates"]["Gate_2_6_Strategic_Synthesis_Matrix"]["passed"])
        self.assertTrue(result["gates"]["Gate_2_7_Deep_Section_Density"]["passed"])

    def test_x2_exit_gate_accepts_numbered_clean_sections(self) -> None:
        """Verify that X2 exit gate accepts '# 1. Meeting Objective' and clean headers."""
        evaluator = XExitGatesEvaluator()
        evaluator._validate_recap_structure(self.clean_recap)

    def test_healer_preserves_clean_markdown_without_injecting_double_hashes_or_stars(self) -> None:
        """Verify that DeterministicHealer does not inject '##' or '**' when healing clean text."""
        input_text = """# Clean Header

Date: 09/14/2026
Duration: ~10m

# Objective
Align on product boundaries.

# Next Steps
| # | Action Item | Accountable Owner | Responsible Party | Target Timing | Evidence IDs | Status |
| 1 | Note the design review | Dan (PartnerAdvisory) | PartnerAdvisory | 09/15/2026 | ev-01 | Proposed |
"""
        healed, changes = DeterministicHealer.heal_output_2(
            input_text,
            metadata={"meeting_date": "09/14/2026", "roster": [{"name": "Dan", "org": "PartnerAdvisory"}]},
        )
        self.assertNotIn("##", healed)
        self.assertNotIn("**", healed)
        self.assertIn("# Meeting Objective", healed)

    def test_healer_does_not_reintroduce_bounded_wedge_when_immediate_delivery_scope_present(self) -> None:
        """Verify that healer leaves 'Immediate Delivery Scope' intact without re-injecting 'Bounded Execution Wedge'."""
        healed, changes = DeterministicHealer.heal_output_2(
            self.clean_recap,
            metadata={"meeting_type": "STRATEGIC_DISCOVERY"},
        )
        self.assertNotIn("Bounded Execution Wedge", healed)
        self.assertIn("Immediate Delivery Scope", healed)

    def test_producer_to_consumer_chain_rejects_unassigned_and_tbd_across_all_gates(self) -> None:
        """Verify Pass 2B validator, Recap Judge, and X2 Exit Gate all reject Unassigned and TBD action rows."""
        from dual_pipeline.llm_judge_core.recap_checks import check_action_ledger_integrity
        from dual_pipeline.prompts.contracts.versioned_judge_contracts import (
            MeetingXExitContract,
        )
        from dual_pipeline.transcript_stages_core.recap_modular_dag import (
            Pass2BStructureError,
            validate_pass_2b_structure,
        )

        bad_recap = self.clean_recap.replace(
            "| 1 | Prepare preview table wireframes | Dan | PartnerAdvisory | 09/15/2026 | ev-01 | Proposed |",
            "| 1 | Prepare preview table wireframes | Unassigned (Pending Owner Assignment) | PartnerAdvisory | TBD | ev-01 | Proposed |",
        )
        with self.assertRaises(Pass2BStructureError):
            validate_pass_2b_structure(bad_recap)

        ledger_ok, ledger_defects = check_action_ledger_integrity(bad_recap)
        self.assertFalse(ledger_ok)
        self.assertTrue(len(ledger_defects) >= 1)

        x2_ok, x2_violations = MeetingXExitContract.validate_x2_recap_structure(bad_recap)
        self.assertFalse(x2_ok)
        self.assertTrue(any("Unassigned" in v or "TBD" in v for v in x2_violations))

