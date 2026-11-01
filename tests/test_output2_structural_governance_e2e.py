"""
tests/test_output2_structural_governance_e2e.py

End-to-End and Adversarial Verification Suite for Output 02 Structural Normalization:
1. Purging legacy section headers: Next Steps & Action Items, Open Decisions / Blockers, Executive Decision.
2. Enforcing Two Universal Mandatory Sections Topology:
   - Meeting Objective
   - Key Discussion Points & Findings
   - Action & Governance Ledger (7 columns + consolidated blockers)
   - Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
3. Gate 2.7 Dynamic Value Test Protection:
   - Stripping superficial (<60w) conditional sections.
   - Preserving substantive (>=60w) conditional sections.
4. OfflineTranscriptSynthesizer E2E contract compliance across archetypes.
5. Gate 2 QA compliance certification.
"""
from __future__ import annotations

import re
import unittest

from dual_pipeline.prompts.contracts.output_contract import MeetingArchetype
from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2
from dual_pipeline.transcript_qa_core.recap_healer import heal_output_2
from dual_pipeline.transcript_stages_core.offline_synthesizer import OfflineTranscriptSynthesizer


def _generate_substantive_prose(word_count: int) -> str:
    """Generates substantive architectural prose of exact word count."""
    phrase = (
        "The architecture review board evaluated the pipeline decoupling requirements, "
        "ratifying the zero-loss audio invariant and the contract-bound boundary gates. "
        "Deterministic routing guarantees that all outputs conform to governance protocols. "
    )
    repeats = max(1, word_count // 20)
    prose = " ".join([phrase] * repeats)
    words = prose.split()
    return " ".join(words[:word_count])


class TestOutput2StructuralGovernanceE2E(unittest.TestCase):
    """E2E and adversarial tests for Output 02 structural normalization."""

    def test_recap_healer_normalizes_legacy_action_headers(self) -> None:
        """Legacy action headers are deterministically normalized to Action & Governance Ledger."""
        legacy_variants = [
            "## Next Steps & Action Items",
            "## Next Steps",
            "## Action Items",
            "## Governance & Action Ledger",
        ]
        for variant in legacy_variants:
            raw_recap = f"""# Subject: Test Ingestion Session

**Date:** 09/25/2026
**Time:** 10:00 AM - 11:00 AM ET
**Attendees:** Amit Ayer (PartnerAdvisory), harper Hoffman (NEA)

## Meeting Objective
Validate structural header normalization across all pipeline entrypoints. [00:01:00]

## Key Discussion Points & Findings
- **Ingestion Boundaries**: Reviewed the single entrypoint mandate. [00:05:00]

{variant}
| # | Action | Owner | Target Timing | Locators | Status |
|---|---|---|---|---|---|
| 1 | Enforce universal entrypoint | Amit Ayer | 09/26/2026 | [00:10:00] | Proposed |

## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
- **Amit Ayer**: Led architectural validation. [00:05:00]
"""
            healed = heal_output_2(raw_recap, metadata={"meeting_date": "09/25/2026"})
            assert "Next Steps & Action Items" not in healed
            assert "Action & Governance Ledger" in healed
            assert re.search(r"#+\s+Action & Governance Ledger", healed)

    def test_recap_healer_consolidates_standalone_blockers(self) -> None:
        """Standalone Open Decisions / Blockers are consolidated into Action & Governance Ledger."""
        raw_recap = """# Subject: Blocker Consolidation Session

**Date:** 09/25/2026
**Time:** 02:00 PM - 03:00 PM ET
**Attendees:** Amit Ayer (PartnerAdvisory), harper Hoffman (NEA)

## Meeting Objective
Establish consolidated governance ledger for operational tracking. [00:01:00]

## Key Discussion Points & Findings
- **Pipeline Decoupling**: Validated stage U0 independence. [00:05:00]

## Open Decisions / Blockers
- **Contract Boundary**: SOW amendment ratification pending legal counsel signoff. [00:15:00]
- **MLX Setting**: Hardware fallback policy requires formal confirmation. [00:20:00]

## Action & Governance Ledger
| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Finalize SOW amendment draft | Amit Ayer | 09/30/2026 | [00:12:00] | Proposed |

## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
- **Amit Ayer**: Addressed blocker consolidation. [00:05:00]
"""
        healed = heal_output_2(raw_recap, metadata={"meeting_date": "09/25/2026"})
        assert "Open Decisions / Blockers" not in healed
        assert "## Open Decisions" not in healed
        assert "# Open Decisions" not in healed
        assert "**Ratified Governance Blockers & Open Items:**" in healed
        assert "SOW amendment ratification pending legal counsel signoff" in healed
        assert "Hardware fallback policy requires formal confirmation" in healed

    def test_recap_healer_c20_decisions_eradication(self) -> None:
        """Unratified or superficial Executive Decision sections are stripped; substantive are converted."""
        # Case A: Superficial / unratified decision -> stripped
        superficial_recap = """# Subject: Unratified Decision Pruning

**Date:** 09/25/2026
**Time:** 09:00 AM - 10:00 AM ET
**Attendees:** Amit Ayer (PartnerAdvisory), Alex Sanchez (PartnerAdvisory)

## Meeting Objective
Review exploratory options without formal ratification. [00:01:00]

## Key Discussion Points & Findings
- **Exploratory Options**: Evaluated design choices. [00:05:00]

## Executive Decision
The team discussed potential options but no formal decision was ratified during this session.

## Action & Governance Ledger
| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Revisit discussion points | Alex Sanchez | 09/30/2026 | [00:10:00] | Proposed |

## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
- **Amit Ayer**: Stated exploratory constraints. [00:05:00]
"""
        healed_superficial = heal_output_2(superficial_recap, metadata={"meeting_date": "09/25/2026"})
        assert "Executive Decision" not in healed_superficial
        assert "no formal decision was ratified" not in healed_superficial

        # Case B: Substantive decision framework (>= 60 words) -> converted to Recommendations & Decisions Required
        substantive_body = (
            "The architecture committee evaluated two architectural alternatives for retrieval scoring. "
            "Option A maintains dense vector search with cosine similarity, while Option B introduces hybrid "
            "MaxSim Colbert representations. Option B provides superior domain terminology fidelity but increases "
            "memory footprint. The committee recommends advancing Option B subject to latency benchmarks. "
            "Failure to ratify this decision by Friday risks downstream milestone delays under SOW scope."
        )
        assert len(substantive_body.split()) >= 60

        substantive_recap = f"""# Subject: Substantive Decision Normalization

**Date:** 09/25/2026
**Time:** 09:00 AM - 10:00 AM ET
**Attendees:** Amit Ayer (PartnerAdvisory), Alex Sanchez (PartnerAdvisory)

## Meeting Objective
Ratify retrieval architecture recommendations. [00:01:00]

## Key Discussion Points & Findings
- **Retrieval Architecture**: Evaluated hybrid scoring models. [00:05:00]

## Executive Decision
{substantive_body}

## Action & Governance Ledger
| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Implement MaxSim benchmarks | Alex Sanchez | 09/30/2026 | [00:10:00] | Proposed |

## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
- **Amit Ayer**: Outlined retrieval trade-offs. [00:05:00]
"""
        healed_substantive = heal_output_2(substantive_recap, metadata={"meeting_date": "09/25/2026"})
        assert "Executive Decision" not in healed_substantive
        assert "Recommendations & Decisions Required" in healed_substantive
        assert "hybrid MaxSim Colbert representations" in healed_substantive

    def test_recap_healer_strips_superficial_conditional_under_gate_2_7(self) -> None:
        """Superficial conditional sections (<60w for 30m+ meeting) are stripped to protect Gate 2.7."""
        superficial_biz = "This meeting provides critical strategic context for project alignment and deliverables."
        superficial_wedge = "The scope includes initial pipeline configuration and test runner updates."

        raw_recap = f"""# Subject: Dynamic Value Test Protection

**Date:** 09/25/2026
**Time:** 10:00 AM - 11:00 AM ET
**Attendees:** Amit Ayer (PartnerAdvisory), harper Hoffman (NEA)

## Meeting Objective
Verify pre-flight stripping of thin conditional sections. [00:01:00]

## Business Meaning and Value Case
{superficial_biz}

## Immediate Delivery Scope & Release Boundaries
{superficial_wedge}

## Key Discussion Points & Findings
- **Substance Verification**: Core narrative contains rich findings. [00:05:00]

## Action & Governance Ledger
| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Audit conditional sections | Amit Ayer | 09/28/2026 | [00:10:00] | Proposed |

## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
- **Amit Ayer**: Stated architectural boundaries. [00:05:00]
"""
        healed = heal_output_2(raw_recap, metadata={"meeting_date": "09/25/2026", "duration_minutes": 60})
        assert "Business Meaning and Value Case" not in healed
        assert "Immediate Delivery Scope & Release Boundaries" not in healed
        assert superficial_biz not in healed
        assert superficial_wedge not in healed
        assert "Key Discussion Points & Findings" in healed
        assert "Action & Governance Ledger" in healed

    def test_recap_healer_preserves_substantive_conditional(self) -> None:
        """Substantive conditional sections (>=60w) are preserved intact."""
        substantive_biz = _generate_substantive_prose(75)

        raw_recap = f"""# Subject: Substantive Section Preservation

**Date:** 09/25/2026
**Time:** 10:00 AM - 11:00 AM ET
**Attendees:** Amit Ayer (PartnerAdvisory), harper Hoffman (NEA)

## Meeting Objective
Verify preservation of value-additive conditional sections. [00:01:00]

## Business Meaning and Value Case
{substantive_biz}

## Key Discussion Points & Findings
- **Substantive Business Value**: Verified deep narrative. [00:05:00]

## Action & Governance Ledger
| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Finalize business value charter | Amit Ayer | 09/28/2026 | [00:10:00] | Proposed |

## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
- **Amit Ayer**: Presented business meaning. [00:05:00]
"""
        healed = heal_output_2(raw_recap, metadata={"meeting_date": "09/25/2026", "duration_minutes": 60})
        assert "Business Meaning and Value Case" in healed
        assert "zero-loss audio invariant" in healed

    def test_offline_synthesizer_emits_canonical_headers_e2e(self) -> None:
        """OfflineTranscriptSynthesizer emits recaps conforming to canonical topology."""
        synth = OfflineTranscriptSynthesizer()
        for archetype in (MeetingArchetype.GENERAL, MeetingArchetype.STRATEGIC_DISCOVERY, MeetingArchetype.ARCHITECTURE_DEEP_DIVE):
            metadata = {
                "meeting_date": "09/25/2026",
                "date": "09/25/2026",
                "time": "10:00 AM - 11:00 AM ET",
                "duration_minutes": 60,
                "archetype": archetype.value,
                "attendees": {"PartnerAdvisory": ["Amit Ayer"], "Apex Global Enterprises": ["harper Hoffman"]},
            }
            recap = synth.synthesize_recap("System prompt", metadata)
            assert "Next Steps & Action Items" not in recap
            assert "Open Decisions / Blockers" not in recap
            assert "Executive Decision" not in recap
            assert "Meeting Objective" in recap
            assert "Key Discussion Points & Findings" in recap
            assert "Action & Governance Ledger" in recap
            assert "Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown" in recap

    def test_gate2_qa_compliance_certified(self) -> None:
        """Synthesized and healed recaps pass Gate 2 QA across multiple archetypes."""
        synth = OfflineTranscriptSynthesizer()
        for archetype in (MeetingArchetype.GENERAL, MeetingArchetype.STRATEGIC_DISCOVERY, MeetingArchetype.ARCHITECTURE_DEEP_DIVE):
            metadata = {
                "meeting_date": "09/25/2026",
                "date": "09/25/2026",
                "time": "10:00 AM - 11:00 AM ET",
                "duration_minutes": 60,
                "archetype": archetype.value,
                "attendees": {"PartnerAdvisory": ["Amit Ayer"], "Apex Global Enterprises": ["harper Hoffman"]},
            }
            recap = synth.synthesize_recap("System prompt", metadata)
            result = run_qa_gate_2(recap, metadata=metadata)
            assert result["passed"] is True, f"Gate 2 QA failed for archetype {archetype.value}: {result.get('errors')}"
