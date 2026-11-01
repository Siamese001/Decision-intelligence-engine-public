"""
tests/test_transcript_continuity_gates.py

Unit tests for Gate 1.5 Timestamp Continuity (Rule C-11) and Gate 1.6 Duration Turn Density.
"""
from __future__ import annotations

import unittest

from dual_pipeline.transcript_qa_core.gate1 import run_qa_gate_1


class TestTranscriptContinuityGates(unittest.TestCase):
    def test_gate_1_5_fails_on_unannotated_large_gap(self) -> None:
        """Transcripts with >180s gaps between spoken turns without Rule C-11 marker must fail."""
        transcript = """# Subject: Architecture Review
**Date:** 09/14/2026
**Time:** 05:00 PM - 05:30 PM ET (Duration: ~30m)
**Attendees:** Dan, Sarah

## Ambiguity Lock Register
| ALR-01 | text | context | resolution | HIGH (95%) |

## Transcript
**Dan:** [01:00] Starting session.
**Sarah:** [10:00] Resuming session nine minutes later without notation.
"""
        res = run_qa_gate_1(transcript)
        self.assertEqual(res["status"], "FAIL")
        self.assertFalse(res["gates"]["Gate_1_5_Timestamp_Continuity"]["passed"])
        self.assertTrue(any("unannotated recording gap" in f for f in res["failures"]))

    def test_gate_1_5_passes_with_recording_gap_annotation(self) -> None:
        """Transcripts with >180s gaps annotated with Rule C-11 marker must pass."""
        transcript = """# Subject: Architecture Review
**Date:** 09/14/2026
**Time:** 05:00 PM - 05:30 PM ET (Duration: ~30m)
**Attendees:** Dan, Sarah

## Ambiguity Lock Register
| ALR-01 | text | context | resolution | HIGH (95%) |

## Transcript
**Dan:** [01:00] Starting session.

[Recording gap: 01:05 – 09:55]

**Sarah:** [10:00] Resuming session after pause.
"""
        res = run_qa_gate_1(transcript)
        self.assertTrue(res["gates"]["Gate_1_5_Timestamp_Continuity"]["passed"])

    def test_gate_1_6_fails_on_low_turn_density_for_long_meeting(self) -> None:
        """A 37-minute meeting with only 5 turns and 200 words must fail density checks."""
        transcript = """# Subject: Long Session
**Date:** 09/14/2026
**Time:** 05:00 PM - 05:37 PM ET (Duration: ~37m)
**Attendees:** Dan, Sarah

## Ambiguity Lock Register
| ALR-01 | text | context | resolution | HIGH (95%) |

## Transcript
**Dan:** [00:00] Start.
**Sarah:** [01:00] Point 1.
**Dan:** [02:00] Point 2.
**Sarah:** [03:00] Point 3.
**Dan:** [04:00] End.
"""
        res = run_qa_gate_1(transcript)
        self.assertEqual(res["status"], "FAIL")
        self.assertFalse(res["gates"]["Gate_1_6_Duration_Turn_Density"]["passed"])
        self.assertTrue(any("turn density failure" in f for f in res["failures"]))


    def test_gate_1_6_passes_on_multiline_markdown_headers(self) -> None:
        """A 30-minute meeting with 25 turns formatted as multiline markdown headers must pass."""
        turns = []
        turn_text = (
            "We reviewed the overall target state architecture and confirmed that the streaming integration "
            "between the ingestion layer and the graph database operates in write-ahead-log mode with zero lock "
            "contention. Stefan highlighted the operational risk around developer velocity and pipeline stability."
        )
        for i in range(25):
            mm = i
            turns.append(f"### [00:{mm:02d}:00]\n\n**Cameron Mercer:**\nTurn {i}: {turn_text}\n")
        transcript = f"""# Subject: Chakra Architecture Review
**Date:** 08/27/2026
**Time:** 09:00 AM - 09:30 AM ET (Duration: ~30m)
**Attendees:** Cameron Mercer, Amit Ayer

## Ambiguity Lock Register
| ALR-01 | text | context | resolution | HIGH (95%) |

## Transcript
{"".join(turns)}
"""
        res = run_qa_gate_1(transcript)
        self.assertTrue(res["gates"]["Gate_1_6_Duration_Turn_Density"]["passed"])
        self.assertGreaterEqual(res["gates"]["Gate_1_6_Duration_Turn_Density"]["total_turns"], 21)
        self.assertGreaterEqual(res["gates"]["Gate_1_6_Duration_Turn_Density"]["total_words"], 900)

    def test_gate_1_6_passes_on_webvtt_and_timestamp_prefix(self) -> None:
        """Transcripts with WebVTT or timestamp-first turns must accurately count turns."""
        turns = []
        turn_text = (
            "Reviewing operational data points for the distributed data mesh across all enterprise domains. "
            "We verified that telemetry events are emitted with schema version compliance and strict causal ordering "
            "to prevent out-of-order execution across background worker nodes."
        )
        for i in range(25):
            mm = i
            turns.append(f"[00:{mm:02d}:00] Amit Ayer: Milestone {i}: {turn_text}\n")
        transcript = f"""# Subject: Kannan Debrief
**Date:** 09/14/2026
**Time:** 03:00 PM - 03:30 PM ET (Duration: ~30m)
**Attendees:** Amit Ayer, Drew Oakley

## Ambiguity Lock Register
| ALR-01 | text | context | resolution | HIGH (95%) |

## Transcript
{"".join(turns)}
"""
        res = run_qa_gate_1(transcript)
        self.assertTrue(res["gates"]["Gate_1_6_Duration_Turn_Density"]["passed"])
        self.assertGreaterEqual(res["gates"]["Gate_1_6_Duration_Turn_Density"]["total_turns"], 21)
        self.assertGreaterEqual(res["gates"]["Gate_1_6_Duration_Turn_Density"]["total_words"], 900)

    def test_gate_2_word_count_and_header_variations(self) -> None:
        """Verify Gate 2 passes at 1637 words and accepts Executive Objective header."""
        from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2

        # Build a compliant recap with 1,637 words and Executive Objective header
        body_words = ["substantive", "architectural", "decision", "milestone", "delivery", "pipeline"]
        filler = " ".join([body_words[i % len(body_words)] for i in range(1100)])

        deep_text_biz = (
            "Enterprise value case established around operational efficiency and developer capacity expansion. "
            "The current multi-system landscape spans disparate tooling across legacy platforms and siloed databases, "
            "resulting in significant manual reconciliation overhead and developer context switching. "
            "By establishing a unified execution spine and automated ingestion pipeline, the organization unlocks "
            "substantial time-to-market acceleration while eliminating costly manual data migration errors and reducing "
            "overall infrastructure operating expenses across delivery workstreams."
        )
        deep_text_decision = (
            "Strategic direction affirmed for initial execution phase across the enterprise platform modernization. "
            "Leadership evaluated alternative incremental options against the unified architecture and concluded that "
            "deferring architectural decoupling introduces unacceptable technical debt and risk of downstream failure. "
            "The ratified decision mandates immediate commencement of the wave-based delivery model with continuous "
            "governance gates and verifiable execution receipts for all production promotions. Downstream teams are authorized "
            "to proceed immediately."
        )
        deep_text_wedge = (
            "Targeted delivery wedge scoped for current operational milestone focusing on core pipeline enablement. "
            "In-scope boundaries include automated ingestion, deterministic routing, and schema validation components, "
            "while deferred legacy integrations remain explicitly quarantined for phase two execution. "
            "Clear acceptance criteria mandate sub-second parsing latency, zero unannotated timestamp gaps, and strict "
            "schema compliance before downstream graph persistence is authorized across test and production environments."
        )

        recap_1637 = f"""# Subject: Executive Recap | 08/27/2026
**Date:** 08/27/2026
**Time:** 09:00 AM - 09:30 AM ET
**Attendees:**
- Cameron Mercer (Executive)
- Amit Ayer (Lead Enterprise Architect)

## Executive Objective
Strategic alignment on the Chakra operating model and delivery timeline across cross-functional enterprise teams.

## Strategic Synthesis
| What We Heard | What It Means |
|---|---|
| Platform stabilization is critical | Delivery focus shifts to telemetry |

## Key Discussion Points & Findings
{filler}

## Next Steps & Action Items
| 1 | Finalize delivery charter | Delivery Partner (Amit Ayer) | 10/01/2026 | 00:01:00 |

## Open Decisions / Blockers
- **Decision 1** -- Governance protocol ratified.

## Business Meaning and Value Case
{deep_text_biz}

## Executive Decision
{deep_text_decision}

## Phased Execution Roadmap
{deep_text_wedge}

## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
### Cameron Mercer
- **Architectural Governance**: Cameron Mercer emphasized the critical necessity of enterprise architectural governance and telemetry alignment. He articulated clear delivery boundaries across all workstreams, noting that automated verification receipts eliminate manual testing latency and ensure consistent delivery quality across deployments and operational environments.
- **Delivery Boundaries**: Cameron Mercer noted that verified milestone gates ensure cross-functional execution alignment across all project phases and operational environments.

### Amit Ayer
- **Platform Verification**: Amit Ayer confirmed that technical execution must follow the wave-based delivery model with continuous gate verification. He highlighted the critical importance of deterministic routing and schema validation, establishing concrete operational guardrails for all downstream platform components, pipeline stages, and enterprise deployments.
- **Operational Guardrails**: Amit Ayer verified that deterministic routing protocols enforce schema compliance and reliable pipeline execution across environments.
"""
        meta = {"date": "08/27/2026", "duration_minutes": 30}
        res = run_qa_gate_2(recap_1637, metadata=meta)
        self.assertTrue(res["gates"]["Gate_2_1_Parsed_Structural_Completeness"]["passed"])
        self.assertTrue(res["gates"]["Gate_2_4_Archetype_and_Altitude_Governance"]["passed"])
        self.assertEqual(res["status"], "PASS")

        # Now test that >1800 words passes under advisory ceiling (exceeding ceiling does not fail Gate 2.5)
        extra_filler = " ".join(["overflow" for _ in range(600)])
        recap_1900 = recap_1637.replace("## Key Discussion Points & Findings", f"## Key Discussion Points & Findings\n{extra_filler}")
        res_1900 = run_qa_gate_2(recap_1900, metadata=meta)
        self.assertEqual(res_1900["status"], "PASS")
        self.assertTrue(res_1900["gates"]["Gate_2_5_Word_Count_Bounds"]["passed"])
        self.assertFalse(any("exceeded ceiling" in f for f in res_1900.get("failures", [])))

    def test_gate_1_fails_on_single_speaker_collapse(self) -> None:
        """Gate 1 must fail closed when multi-attendee session collapses all turns to 1 speaker."""
        turns = "\n".join(f"**[00:{i:02d}] Jen Pan:** Discussion turn number {i} regarding LENS." for i in range(12))
        f1 = f"""# LENS Sync -- HITL-Resolved Transcript
**Date:** 09/17/2026
**Platform Reference:** Microsoft Teams
**Participants:**
- **PartnerAdvisory:** Jen Pan (Product Manager), Amit Ayer (Lead Architect)
- **Apex Global Enterprises (NEA):** harper Hoffman (Product Manager)

---
## Forensic Spoken Transcript
{turns}

---
## Ambiguity Lock Register
| Timestamp | Raw Acoustic | Forensic Interpretation | Status | Affected Statement IDs |
| **00:00** | LENS | LENS OCR tool | LOCKED | stmt-1 |
"""
        meta = {"roster": [{"name": "Jen Pan"}, {"name": "Amit Ayer"}, {"name": "harper Hoffman"}]}
        res = run_qa_gate_1(f1, metadata=meta)
        self.assertEqual(res["status"], "FAIL")
        self.assertTrue(any("SINGLE_SPEAKER_COLLAPSE_DETECTED" in f for f in res["failures"]))

    def test_gate_1_fails_on_speaker_dialogue_contradiction(self) -> None:
        """Gate 1 must fail closed when turn speaker label contradicts first-person self-identification."""
        f1 = """# LENS Sync -- HITL-Resolved Transcript
**Date:** 09/17/2026
**Platform Reference:** Microsoft Teams
**Participants:**
- **PartnerAdvisory:** Jen Pan (Product Manager), Amit Ayer (Lead Architect)

---
## Forensic Spoken Transcript
**[00:01] Jen Pan:** Initial opening.
**[00:05] Jen Pan:** I'm Amit Ayer, and I'm here to implement how we're doing this.
**[00:10] Amit Ayer:** Responding here.

---
## Ambiguity Lock Register
| Timestamp | Raw Acoustic | Forensic Interpretation | Status | Affected Statement IDs |
| **00:01** | LENS | LENS tool | LOCKED | stmt-1 |
"""
        meta = {"roster": [{"name": "Jen Pan"}, {"name": "Amit Ayer"}]}
        res = run_qa_gate_1(f1, metadata=meta)
        self.assertEqual(res["status"], "FAIL")
        self.assertTrue(any("SPEAKER_DIALOGUE_CONTRADICTION" in f for f in res["failures"]))

    def test_gate_1_fails_on_misattributed_corporate_affiliation(self) -> None:
        """Gate 1 must fail closed when PartnerAdvisory attendee is categorized under Client/Unknown."""
        f1 = """# LENS Sync -- HITL-Resolved Transcript
**Date:** 09/17/2026
**Platform Reference:** Microsoft Teams
**Participants:**
- **Client / Unknown:** Jen Pan (Participant, Unknown)
- **PartnerAdvisory:** Amit Ayer (Lead Architect)

---
## Forensic Spoken Transcript
**[00:01] Jen Pan:** First turn.
**[00:05] Amit Ayer:** Second turn.

---
## Ambiguity Lock Register
| Timestamp | Raw Acoustic | Forensic Interpretation | Status | Affected Statement IDs |
| **00:01** | LENS | LENS tool | LOCKED | stmt-1 |
"""
        meta = {"roster": [{"name": "Jen Pan"}, {"name": "Amit Ayer"}]}
        res = run_qa_gate_1(f1, metadata=meta)
        self.assertEqual(res["status"], "FAIL")
        self.assertTrue(any("MISATTRIBUTED_CORPORATE_AFFILIATION" in f for f in res["failures"]))


if __name__ == "__main__":
    unittest.main()
