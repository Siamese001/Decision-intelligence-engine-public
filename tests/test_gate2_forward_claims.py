"""
tests/test_gate2_forward_claims.py

Wave 2 F-04: Gate 2.3 forward-looking business value claim grounding advisory tests.
Validates that forward-looking integration claims without [MM:SS] locators in the
Business Value section are flagged as advisory items.
"""

import unittest

from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2

# Output 2 with a forward-looking claim in Business Value but NO timestamp locator
OUTPUT2_WITH_UNANCHORED_FWD_CLAIM = """
**Email Subject:** LENS Pipeline Review - 09/18/2026

**Date:** 09/18/2026
**Time:** 12:43 PM – 1:39 PM ET
**Attendees:**
- PartnerAdvisory: Jake Stark, Amit Ayer
- NEA: Miles Erickson

## Meeting Objective
Reviewed the LENS pipeline architecture zones and discussed accuracy thresholds.

## Key Discussion Points & Findings

1. **Jake Stark relaxed the confidence threshold from mandatory 100% to a configurable band**
   The pipeline accuracy interpretation was clarified. [12:05]

2. **Miles Erickson confirmed OCR is under active investigation but excluded from client-facing numbers**
   The dual-LLM approach was discussed. [24:10]

## Business Meaning and Value Case

This engagement will enable future work-order integration with the enterprise asset registry,
positioning the organization for seamless data exchange in the next fiscal year.

## Appendix: Detailed Stakeholder Perspectives

### Jake Stark - PartnerAdvisory, Solution Architect

**Threshold Configuration**: Jake clarified that the 100% accuracy bar was aspirational, not contractual.
The system should allow operators to configure acceptable confidence bands.
This significantly de-risks the delivery scope.

**OCR Boundaries**: Jake explicitly scoped out OCR from the current engagement metrics.
This prevents misaligned client expectations. [33:15]

### Amit Ayer - PartnerAdvisory, Lead Enterprise Architect

**Architecture Zones**: Amit walked through the six pipeline zones, highlighting the pre-flight
and quality gate layers as the primary reliability enforcement points.

**Dual-LLM Strategy**: The two-LLM approach (extraction + validation) was presented as the
core architectural differentiator. [45:20]

### Miles Erickson - NEA

**Stakeholder Perspective**: Miles validated the architectural approach from the NEA perspective,
noting that the configurable accuracy threshold addresses a real operational need.
"""

# Output 2 with forward-looking claim that HAS a timestamp locator
OUTPUT2_WITH_ANCHORED_FWD_CLAIM = """
**Email Subject:** LENS Pipeline Review - 09/18/2026

**Date:** 09/18/2026
**Time:** 12:43 PM – 1:39 PM ET
**Attendees:**
- PartnerAdvisory: Jake Stark, Amit Ayer
- NEA: Miles Erickson

## Meeting Objective
Reviewed the LENS pipeline architecture zones.

## Key Discussion Points & Findings

1. **Jake Stark relaxed confidence threshold interpretation** [12:05]

## Business Meaning and Value Case

Amit noted that the pipeline design will enable future integration with the asset registry [45:20],
positioning the team for the next phase of delivery.

## Appendix: Detailed Stakeholder Perspectives

### Jake Stark - PartnerAdvisory

**Threshold Configuration**: Jake clarified the accuracy interpretation. [12:05]

### Amit Ayer - PartnerAdvisory

**Pipeline Design**: Amit outlined the zone architecture. [28:10]

### Miles Erickson - NEA

**Validation**: Miles confirmed the approach meets NEA requirements. [35:00]
"""

# Clean output with no forward-looking claims at all
OUTPUT2_NO_FWD_CLAIMS = """
**Email Subject:** LENS Pipeline Review - 09/18/2026

**Date:** 09/18/2026
**Time:** 12:43 PM – 1:39 PM ET
**Attendees:**
- PartnerAdvisory: Jake Stark

## Meeting Objective
Reviewed the LENS pipeline architecture zones and discussed accuracy thresholds.

## Key Discussion Points & Findings

1. **Jake Stark relaxed confidence threshold interpretation from 100% mandatory to configurable band** [12:05]

## Business Meaning and Value Case

The pipeline delivers measurable accuracy improvements in document processing workflows. [45:00]

## Appendix: Detailed Stakeholder Perspectives

### Jake Stark - PartnerAdvisory

**Threshold Configuration**: Jake clarified the accuracy bar is configurable. [12:05]
"""


class TestForwardClaimGrounding(unittest.TestCase):

    def test_unanchored_forward_claim_generates_advisory(self):
        meta = {}
        result = run_qa_gate_2(OUTPUT2_WITH_UNANCHORED_FWD_CLAIM, meta)
        gate_2_3 = result["gates"]["Gate_2_3_Evidence_Lineage_and_Epistemic_Status"]
        advisories = gate_2_3.get("forward_claim_advisory", [])
        self.assertTrue(
            len(advisories) > 0,
            f"Expected forward claim advisory for unanchored 'will enable future work-order integration'. Got: {advisories}"
        )
        self.assertTrue(any("GATE_2_3_FORWARD_CLAIM" in a for a in advisories))

    def test_anchored_forward_claim_no_advisory(self):
        meta = {}
        result = run_qa_gate_2(OUTPUT2_WITH_ANCHORED_FWD_CLAIM, meta)
        gate_2_3 = result["gates"]["Gate_2_3_Evidence_Lineage_and_Epistemic_Status"]
        advisories = gate_2_3.get("forward_claim_advisory", [])
        self.assertEqual(
            advisories, [],
            f"Expected no advisory for properly anchored forward claim. Got: {advisories}"
        )

    def test_no_bv_section_no_advisory(self):
        """When Business Meaning section is absent, no forward claim advisory fires."""
        meta = {}
        result = run_qa_gate_2(OUTPUT2_NO_FWD_CLAIMS, meta)
        gate_2_3 = result["gates"]["Gate_2_3_Evidence_Lineage_and_Epistemic_Status"]
        advisories = gate_2_3.get("forward_claim_advisory", [])
        self.assertEqual(advisories, [])


class TestActionStatusRationale(unittest.TestCase):
    """F-02: Validate action_status_rationale field values."""

    def test_explicit_no_action_phrase_gives_confirmed_rationale(self):
        f2_with_phrase = """
**Email Subject:** LENS Review

**Date:** 09/18/2026
**Attendees:** - PartnerAdvisory: Jake

## Meeting Objective
Review session.

## Key Discussion Points & Findings
No formal actions were established in this session. The discussion was exploratory.

## Appendix: Stakeholder Perspectives

### Jake Stark - PartnerAdvisory, Architect

**Design Discussion**: Jake explored the pipeline zones and thresholds at length.
The discussion was informational rather than decision-driving. [12:05]
"""
        meta = {}
        result = run_qa_gate_2(f2_with_phrase, meta)
        rationale = result["gates"]["Gate_2_2_Typed_Action_Register"]["action_status_rationale"]
        self.assertEqual(rationale, "NO_ACTIONS_CONFIRMED", f"Got: {rationale}")
        self.assertTrue(result["gates"]["Gate_2_2_Typed_Action_Register"]["passed"])

    def test_empty_table_no_phrase_gives_indeterminate(self):
        f2_empty = """
**Email Subject:** LENS Review

**Date:** 09/18/2026
**Attendees:** - PartnerAdvisory: Jake

## Meeting Objective
Review session.

## Key Discussion Points & Findings
The team reviewed the pipeline.

## Appendix: Stakeholder Perspectives

### Jake Stark - PartnerAdvisory

**Pipeline**: Jake discussed the architecture. [05:00]
"""
        meta = {}
        result = run_qa_gate_2(f2_empty, meta)
        rationale = result["gates"]["Gate_2_2_Typed_Action_Register"]["action_status_rationale"]
        self.assertEqual(rationale, "INDETERMINATE_ZERO_ACTIONS", f"Got: {rationale}")
        self.assertFalse(result["gates"]["Gate_2_2_Typed_Action_Register"]["passed"])


if __name__ == "__main__":
    unittest.main()
