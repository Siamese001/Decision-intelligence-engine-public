"""
tests/test_appendix_disclaimer_rejection.py

Validates that Gate 2.8 and LLM Judge strictly reject defensive cop-out disclaimers
and mandate Copilot-depth multi-thematic speaker breakdowns.
"""

from __future__ import annotations

import unittest

from dual_pipeline.llm_judge_core.recap import _check_appendix_defect

from dual_pipeline.transcript_qa_core.appendix_standard import audit_appendix_gold_standard


class TestAppendixDisclaimerRejection(unittest.TestCase):
    def test_rejects_evasive_disclaimers(self) -> None:
        evasive_appendix = """# Subject: Test Meeting
Date: 09/18/2026
Time: 12:43 PM - 01:39 PM ET (Duration: ~56m)
Attendees:
- PartnerAdvisory: Jake Stark (Team Contributor), Amit Ayer (Lead Enterprise Architect), Miles Erickson (Team Contributor)

## Meeting Objective
Evaluate single-turbine model comparison.

## Key Discussion Points & Findings
- Model comparison was reviewed.

## Next Steps & Action Items
| 1 | Action | PartnerAdvisory | Jake Stark | 09/25/2026 | [10:00] |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

# Jake Stark - PartnerAdvisory, PartnerAdvisory Team Contributor

Dataset clarification: Jake is explicitly addressed during the single-turbine discussion at 20:24. That establishes his relevance to dataset clarification, but not his answer or endorsement of the criticism that follows. The later photograph-count discussion likewise lacks speaker mapping. Confirming Jake’s personal assessment requires speaker-resolved evidence; this recap therefore preserves those conclusions as meeting-level findings.

Evaluation coordination: The roster establishes Jake’s participation, not ownership of configuration changes. The same-day expectation concerned another person’s work and included an explicit caveat. Jake’s proposed coordination assignment is consequently a planning recommendation, not a recovered commitment. Confirmation should establish whether he can coordinate results, who executes the change, and who independently checks accepted records.

# Amit Ayer - PartnerAdvisory, Lead Enterprise Architect

Architecture and measurement: Amit’s architectural role is established by metadata. The discussion contains substantive critiques of model agreement, capture-quality handling, and insufficient validation data, but no reliable utterance mapping attributes them to him. References to “Ayer” are not equivalent to direct attributable testimony. His individual architectural position therefore remains unconfirmed rather than inferred from his title.

Governance and readiness: The record supports a collective distinction between development evidence and production conclusions. It does not establish that Amit accepted threshold-approval authority or committed to an evaluation specification. His proposed gate responsibilities concern coordination and evidence review only. Speaker confirmation and an identified client approver are still required before presenting these proposals as assigned governance responsibilities.

# Miles Erickson - PartnerAdvisory, PartnerAdvisory Team Contributor

Participation and calibration: Metadata identifies Miles as an active participant, while the transcript includes attendance-related references without resolving his substantive contributions. The concerns about late SME input and unclear production framing are material meeting findings, but cannot safely be presented as Miles’s personal critique. Neither agreement nor disagreement should be inferred from his inclusion in the roster.

Data-readiness coordination: Miles is proposed to coordinate a broader-data and calibration agenda because that is an unresolved team need, not because an attributable promise appears in the recording. Confirmation should establish his intended contribution and the relevant client data contact. The record supports discussing representative samples and reusable rules, but establishes no Miles-owned delivery date or accepted sample-size requirement.
"""
        res = audit_appendix_gold_standard(evasive_appendix, duration_minutes=56)
        self.assertFalse(res.passed)
        self.assertTrue(any("GATE_2_8_DEFENSIVE_DISCLAIMER_DETECTED" in f for f in res.failures))

        defect = _check_appendix_defect(evasive_appendix.split("# Appendix: Detailed Stakeholder Perspectives")[1], dur_min=56)
        self.assertIsNotNone(defect)
        self.assertIn("GATE_2_8_DEFENSIVE_DISCLAIMER_DETECTED", defect)

    def test_rejects_insufficient_thematic_depth(self) -> None:
        single_theme_appendix = """# Subject: Test
Date: 09/18/2026
Time: 12:00 PM - 12:30 PM ET (Duration: ~30m)
Attendees:
- PartnerAdvisory: Drew Oakley (Delivery Lead)

## Meeting Objective
Test single theme.

## Key Discussion Points & Findings
- Finding 1.

## Next Steps & Action Items
| 1 | Action | PartnerAdvisory | Drew Oakley | 09/25/2026 | [10:00] |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

# Drew Oakley - PartnerAdvisory, Delivery Lead

- **General Project Overview**: Dan provided a comprehensive general overview of the project status and discussed the timeline for upcoming milestones without getting into specific architectural decisions or engineering tradeoffs. He emphasized that everyone on the engineering team should stay aligned on programmatic goals and delivery targets across sprints.
"""
        res = audit_appendix_gold_standard(single_theme_appendix, duration_minutes=30)
        self.assertFalse(res.passed)
        self.assertTrue(any("GATE_2_8_INSUFFICIENT_THEMATIC_DEPTH" in f for f in res.failures))

    def test_accepts_copilot_gold_standard(self) -> None:
        gold_standard_appendix = """# Subject: Test
Date: 09/18/2026
Time: 12:00 PM - 12:30 PM ET (Duration: ~30m)
Attendees:
- PartnerAdvisory: Drew Oakley (Delivery Lead), Amit Ayer (Lead Architect)

## Meeting Objective
Test gold standard.

## Key Discussion Points & Findings
- Finding 1.

## Next Steps & Action Items
| 1 | Action | PartnerAdvisory | Drew Oakley | 09/25/2026 | [10:00] |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

#### Drew Oakley
- **Context on Team Evolution and Hiring Expectations**: Dan provided extensive background explaining why the team was interested in capturing technical screening experiences. He described how delivery teams were historically assembled from specialists across multiple disciplines and how expectations evolved toward a full-stack engineering model.
- **Information Gathering and Validation Effort**: Throughout the conversation, Dan acted as a facilitator who continuously sought clarity around interview questions, technical exercises, interviewer identities, and evaluation criteria to help future candidates.

#### Amit Ayer
- **Deep Examination of Technical Reasoning**: Amit focused heavily on understanding the thought process behind the technical interview rather than simply documenting outcomes. He asked candidates to reconstruct their debugging workflows from encountering failing unit tests through root cause identification.
- **Exploration of Client Expectations and Project Pivots**: Amit was particularly interested in understanding how project pivots were discussed during interviews. He asked candidates to explain principles of transparency, tradeoff analysis, and stakeholder communication.
"""
        res = audit_appendix_gold_standard(gold_standard_appendix, duration_minutes=30)
        self.assertTrue(res.passed, f"Failed on gold standard: {res.failures}")
        self.assertEqual(res.failures, [])

    def test_tiered_primary_driver_density_enforcement(self) -> None:
        two_theme_appendix = """# Subject: Test
Date: 09/18/2026
Time: 12:00 PM - 12:30 PM ET (Duration: ~30m)
Attendees:
- PartnerAdvisory: Drew Oakley (Delivery Lead)

## Meeting Objective
Test primary driver.

## Key Discussion Points & Findings
- Finding 1.

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

#### Drew Oakley
- **Context on Team Evolution**: Dan provided extensive background explaining why the team was interested in capturing technical screening experiences across engineering teams.
- **Information Gathering Effort**: Dan acted as a facilitator seeking clarity around interview questions and criteria to help future candidates.
"""
        # Without primary driver designation, passes default 2-theme floor
        res_default = audit_appendix_gold_standard(two_theme_appendix, duration_minutes=30)
        self.assertTrue(res_default.passed)

        # With primary driver designation, fails because it only has 2 themes and < 150 words
        res_primary = audit_appendix_gold_standard(
            two_theme_appendix,
            duration_minutes=30,
            primary_speakers=["Drew Oakley"],
        )
        self.assertFalse(res_primary.passed)
        self.assertTrue(any("GATE_2_8_INSUFFICIENT_THEMATIC_DEPTH" in f or "GATE_2_8_THIN_SPEAKER_SECTION" in f for f in res_primary.failures))

        # With >= 3 themes and >= 150 words, passes primary driver check
        deep_appendix = """# Subject: Test
Date: 09/18/2026
Time: 12:00 PM - 12:30 PM ET (Duration: ~30m)
Attendees:
- PartnerAdvisory: Drew Oakley (Delivery Lead)

## Meeting Objective
Test primary driver.

## Key Discussion Points & Findings
- Finding 1.

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

#### Drew Oakley
- **Context on Team Evolution and Hiring Expectations**: Dan provided extensive background explaining why the team was interested in capturing technical screening experiences across delivery disciplines. He described how engineering teams historically assembled specialized talent and how industry expectations progressively evolved toward a full-stack engineering model requiring hands-on adaptability across architecture and implementation.
- **Information Gathering and Validation Strategy**: Throughout the session, Dan served as the primary facilitator who continuously sought concrete clarity around specific interview questions, coding exercise expectations, evaluation rubrics, and feedback mechanisms. He pressed participants to describe the nuance of client-facing questions and behavioral assessments.
- **Candidate Coaching and Onboarding Standards**: Dan focused heavily on how to synthesize interview learnings into actionable preparation modules for prospective technical staff, ensuring that onboarding pipelines reflect modern AI-assisted engineering practices. He emphasized that structured debriefs ensure candidates enter placement interviews thoroughly grounded. He argued that systematic review of technical interview loops provides invaluable intelligence that directly improves interview performance across the entire consulting practice.
"""
        res_deep = audit_appendix_gold_standard(
            deep_appendix,
            duration_minutes=30,
            speaker_shares={"Drew Oakley": 0.45},
        )
        self.assertTrue(res_deep.passed, f"Deep primary driver failed: {res_deep.failures}")


if __name__ == "__main__":
    unittest.main()
