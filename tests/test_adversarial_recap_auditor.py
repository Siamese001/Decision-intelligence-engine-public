"""
tests/test_adversarial_recap_auditor.py

Unit tests for AdversarialRecapAuditor multi-agent prosecutorial reviewer
and its integration into DecomposedMultiJudge.
"""
from __future__ import annotations

import unittest

from dual_pipeline.llm_judge_core.adversarial_recap_auditor import (
    AdversarialRecapAuditor,
)
from dual_pipeline.llm_judge_core.orchestrator import DecomposedMultiJudge


class TestAdversarialRecapAuditor(unittest.TestCase):
    def setUp(self) -> None:
        self.auditor = AdversarialRecapAuditor()

    def test_auditor_detects_upstream_truncation_gaps(self) -> None:
        """Auditor must detect unannotated silence gaps > 180s in Output 01."""
        f1 = """## Transcript
**Dan:** [01:00] First turn.
**Sarah:** [10:00] Second turn nine minutes later without gap notation.
"""
        f2 = """# Subject: Brief
**Date:** 09/14/2026
**Time:** 05:00 PM - 05:30 PM ET (Duration: ~30m)
**Attendees:** Dan, Sarah

## Meeting Objective
Test objective.

## Key Discussion Points & Findings
- Finding 1.

## Next Steps & Action Items
| 1 | Action | Co | Dan | 09/15/2026 | [01:00] |
"""
        res = self.auditor.audit(f1_text=f1, f2_text=f2, mock=True)
        self.assertTrue(res.upstream_truncation_detected)
        self.assertIn("UPSTREAM TRUNCATION DETECTED", res.prosecutorial_findings[0])

    def test_auditor_detects_sycophancy_when_primary_judge_rates_defective_output_high(self) -> None:
        """When primary judge awards >= 88 to a recap with missing synthesis/density, auditor must indict."""
        f1 = """## Transcript
**Dan:** [01:00] Turn 1.
**Sarah:** [02:00] Turn 2.
"""
        f2 = """# Subject: Light Recap
**Date:** 09/14/2026
**Time:** 05:00 PM - 05:30 PM ET (Duration: ~30m)
**Attendees:** Dan, Sarah

## Meeting Objective
Short objective.

## Key Discussion Points & Findings
- Only one bullet point.

## Next Steps & Action Items
| 1 | Action | Co | Dan | 09/15/2026 | [01:00] |
"""
        fake_primary_eval = {
            "composite_score": 92,
            "verdict": "PASS",
            "forensic_rationale": "High executive altitude despite brevity.",
        }
        res = self.auditor.audit(
            f1_text=f1,
            f2_text=f2,
            out2_eval=fake_primary_eval,
            metadata={"duration_minutes": 30},
            mock=True,
        )
        self.assertTrue(res.sycophancy_detected)
        self.assertTrue(res.hard_veto)
        self.assertEqual(res.status, "HARD_VETO")
        self.assertIn("sycophantic primary judge pass", res.indictment_summary)

    def test_auditor_clears_dense_grounded_recap(self) -> None:
        """Dense recap with Strategic Synthesis and substantive Wedge must be cleared."""
        f1 = """## Transcript
**Dan:** [01:00] Turn 1.
**Sarah:** [02:00] Turn 2.
"""
        f2 = """# Subject: Enriched Executive Recap | 09/14/2026

**Date:** 09/14/2026
**Time:** 05:00 PM - 05:30 PM ET (Duration: ~30m)
**Attendees:**
- Dan (PartnerAdvisory)
- Sarah (NEA)

## Meeting Objective
Align on architecture and delivery boundaries across both enterprise engineering workstreams, establishing rigorous acceptance criteria for SOW 1 rule authoring and pre-commit validation while scoping SOW 2 boundaries.

## Key Discussion Points & Findings
### 1. The Institutional Imperative for Observability
Across seven years of historical telemetry rollouts at Apex Global Enterprises, tools fail when field operators cannot inspect backend reasoning. Rotating field engineer cohorts require clear self-documenting interfaces.

### 2. Pre-Commit Impact Preview Requirements
The team aligned on introducing a six-row modal table previewing impact before candidate rules are committed into production systems.

### 3. Review Queue Navigation Friction
Operators experience friction matching raw job identifiers ending in 21. Direct linking from confirmation dialogs straight to review queue entities eliminates manual dashboard filtering.

### 4. Bounded Structured Evidence Record
Enterprise audit standards require persisting execution ID, field locator, rule ID, expected versus observed values, and pass or fail reason for every single rule execution.

### 5. Root-Cause Attribution: OCR vs. Rule Logic
Extraction errors such as 211H versus 21H originate in vision model transcription rather than business rule logic. Disambiguating model errors from rule mismatches is mandatory.

## Strategic Synthesis
| # | What We Heard (NEA Operational Friction) | What It Means (Enterprise Architecture Implication) |
| :--- | :--- | :--- |
| 1 | Lack of observability causes software abandonment | Must provide pre-commit preview table to verify impact before applying rules |
| 2 | Field engineers rotate frequently | Software must be completely self-documenting without relying on unverified user faith |
| 3 | Matching raw job IDs in review queue is error-prone | Confirmation dialog must provide direct deeplinks to candidate queue items |
| 4 | Unclear whether errors stem from OCR or rules | Telemetry pipeline must isolate vision extraction failures from rule evaluation |

## Business Meaning and Value Case
- **Business Problem:** Opaque rule execution risks user abandonment across rotating field engineering cohorts who lack visibility into backend decision pipelines.
- **Value at Stake:** Prevents costly rollbacks, manual inspection cycles, and unverified data corruption in downstream enterprise systems of record.
- **Baseline:** Current workflow requires manual review queue hunting and blind commit approvals without impact previews.
- **Target:** 100% auditable rule execution with immediate pre-commit previews and structured evidence persistence.
- **Value Mechanism:** Pre-commit preview table and structured audit records provide immediate verification.
- **Confidence:** HIGH (95%) based on verified operator feedback and architectural consensus.
- **Consequence of Delay:** Continued rollout friction and potential delivery slippage into SOW 2 scope.

## Executive Decision
- **Executive Conclusion:** Proposed SOW 1 candidate acceptance criteria must be bounded strictly to sample-validation preview and direct navigation.
- **Decision Required:** Formal agreement on SOW 1 acceptance line cutoff between sample validation and historical reprocessing.
- **Recommended Choice:** Restrict SOW 1 to sample-validation preview table, direct queue linking, and structured evidence records.
- **Alternatives Rejected or Deferred:** Broad historical reprocessing across millions of legacy records is deferred to SOW 2.
- **Reason:** Protecting delivery momentum and contractual timelines.
- **Decision Owner:** Dan & Sarah.
- **Decision Deadline:** 09/15/2026.
- **Consequence of No Decision:** Uncontrolled scope creep and milestone delays.
- **Decision Status:** PROPOSED.

## Bounded Execution Wedge
- **Target User:** EOSS Field Engineers and Rule Authors operating within the LENS platform.
- **Business Capability:** Pre-commit impact verification and auditable rule validation.
- **In Scope:** Six-row preview table displaying Image ID, Old Value, New Value, and Pass/Fail status, plus direct queue linking.
- **Out of Scope:** Bulk retroactive reprocessing, database schema migrations, and OCR vision model replacement.
- **Systems Touched:** LENS UI, Rule Builder, Review Queue, Audit Telemetry Store.
- **Systems Explicitly Not Replaced:** Model A OCR engine, core image ingestion pipeline.
- **Required Dependencies:** Finalized wireframes and sample test photo set.
- **Known Constraints:** Maximum 6 preview rows rendered synchronously in modal dialog.
- **First Deliverable:** High-fidelity wireframes for rule preview modal.
- **Acceptance Test:** Validate candidate rule across 6 sample photos with zero unhandled exceptions.
- **Exit Criterion:** Operator can preview rule changes and navigate to review items in under 2 clicks.
- **Owner:** Jennifer Pan (PartnerAdvisory Lead Engineer).
- **Decision Gate:** 09/15/2026 Afternoon Review Session.
- **Success Metric:** 100% attribution clarity between OCR vision transcription and business rule logic.

## Next Steps & Action Items
| # | Action Item | Company | Owner | Target Date / Cadence | Transcript Locator |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | Prepare preview table wireframes | PartnerAdvisory | Dan | 09/15/2026 | [01:00] |
| 2 | Compile representative sample test photos | NEA | Sarah | 09/15/2026 | [02:00] |

## Open Decisions / Blockers
- Final signoff on SOW 1 feature line scheduled for tomorrow afternoon sync.
"""
        fake_primary_eval = {
            "composite_score": 96,
            "verdict": "PASS",
        }
        res = self.auditor.audit(
            f1_text=f1,
            f2_text=f2,
            out2_eval=fake_primary_eval,
            metadata={"duration_minutes": 30},
            mock=True,
        )
        self.assertFalse(res.hard_veto)
        self.assertFalse(res.sycophancy_detected)
        self.assertEqual(res.status, "CLEARED")

    def test_orchestrator_blocks_write_authorization_on_adversarial_veto(self) -> None:
        """DecomposedMultiJudge must set write_authorized=False when adversarial audit issues hard veto."""
        f1 = """## Transcript
**Dan:** [01:00] Start.
**Sarah:** [12:00] Big gap without notation.
"""
        f2 = """# Subject: Brief
**Date:** 09/14/2026
**Time:** 05:00 PM - 05:30 PM ET (Duration: ~30m)
**Attendees:** Dan, Sarah

## Meeting Objective
Objective.

## Key Discussion Points & Findings
- Minimal finding.

## Next Steps & Action Items
| 1 | Action | Co | Dan | 09/15/2026 | [01:00] |
"""
        f3 = "{}"
        judge = DecomposedMultiJudge(mock=True)
        res = judge.evaluate_parallel(
            output_1_text=f1,
            output_2_text=f2,
            output_3_text=f3,
            raw_transcript=f1,
            metadata={"duration_minutes": 30},
        )
        self.assertEqual(res["status"], "FAIL")
        self.assertFalse(res["authoritative_write_authorized"])
        self.assertEqual(res["adversarial_recap_audit"]["status"], "HARD_VETO")


if __name__ == "__main__":
    unittest.main()
