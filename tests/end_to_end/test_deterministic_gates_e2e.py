"""
tests/end_to_end/test_deterministic_gates_e2e.py

End-to-End lifecycle evaluation test suite for deterministic judge gates (Wave 3).
Verifies:
1. Google_PartnerAdvisory_apex_global Output 1 fails D3 due to timestamp collapse across 253 turns.
2. Candidate Output 2 cannot evade structural density or Strategic Synthesis by claiming TACTICAL_ALIGNMENT.
3. Clean Output 1 passes end-to-end evaluation with monotonic timestamps and valid ALR.
4. Clean Output 2 passes end-to-end evaluation with unified Action & Governance Ledger.
5. Clean Output 3 passes end-to-end evaluation with canonical W2 predicates and zero action items.
6. Tactical Alignment Output 3 omits Dim 1 while retaining Dim 2.
"""
import json
import pytest
from pathlib import Path

from dual_pipeline.llm_judge_core.judges.forensic import ForensicTranscriptJudge
from dual_pipeline.llm_judge_core.judges.recap import ExecutiveRecapJudge
from dual_pipeline.llm_judge_core.judges.graph import GraphExtractionJudge
from dual_pipeline.prompts.contracts import MeetingArchetype


def test_google_partner_advisory_apex_global_e2e_timestamp_collapse_failure():
    """Verify that authentic Google_PartnerAdvisory_apex_global transcript fails D3 deterministically."""
    apex_global_path = Path("output/partner_alliances/07-24-2026/Google_PartnerAdvisory_apex_global_Rusty_PartnerAdvisory/01_Google_PartnerAdvisory_apex_global_Rusty_PartnerAdvisory_07-24-2026_0129PM0200PMEDT_hitl_resolved_transcript.md")
    if not apex_global_path.exists():
        pytest.skip("Google_PartnerAdvisory_apex_global fixture not found")

    transcript_text = apex_global_path.read_text(encoding="utf-8")
    judge = ForensicTranscriptJudge()
    res = judge.evaluate(
        f1_text=transcript_text,
        raw_source_text=transcript_text + "\nraw audio markers",
        mock=True,
    )

    assert res["verdict"] == "FAIL"
    assert res["dimension_3_chronological_and_timestamp_integrity"] == 5
    assert "ERR_TIMESTAMP_COLLAPSE" in res.get("defect_codes", [])
    assert "TIMESTAMP DEFECT" in res["forensic_rationale"]


def test_recap_archetype_spoofing_e2e_rejection():
    """Candidate recap claiming TACTICAL_ALIGNMENT cannot evade STRATEGIC_DISCOVERY requirements."""
    # A candidate recap that omits Strategic Synthesis and claims archetype: TACTICAL_ALIGNMENT
    f2_spoofed = """# Meeting Objective
Operational context and catalyst for the meeting.
Archetype: TACTICAL_ALIGNMENT

# Key Discussion Points & Findings
Substantive discussion points with technical substance.

# Action & Governance Ledger
| Type | Action Item | Owner | Target Date | Governance Scope | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Action | Deploy service | Alice | 2026-08-01 | Architecture | Open |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
- **Alice (PartnerAdvisory Lead):** Substantive comments on technical topics.
"""
    f1 = """**Date:** 08/01/2026
Duration: ~30m
**Participants:** Alice (PartnerAdvisory)
Verbatim transcript about discovery and strategic direction.
"""
    judge = ExecutiveRecapJudge()
    res = judge.evaluate(
        f2_text=f2_spoofed,
        f1_text=f1,
        mock=True,
        metadata={"archetype": "STRATEGIC_DISCOVERY"},
    )

    assert res["meeting_archetype"] == "STRATEGIC_DISCOVERY"
    assert res["verdict"] == "FAIL"
    assert "STRUCTURAL DENSITY FAILURE" in res["forensic_rationale"]


def test_clean_output1_e2e_lifecycle_pass():
    """Clean Output 1 transcript passes end-to-end evaluation."""
    clean_f1 = """# Project Sync -- Forensic Attributed Transcript

**Date:** 08/01/2026
**Participants:**
- **PartnerAdvisory:** Alice (Solution Lead)
- **Google:** Bob (Cloud Architect)

---

[00:01:00] **Alice:** Welcome everyone to our technical alignment session.
[00:02:15] **Bob:** Thanks Alice, excited to review the architecture.
[00:03:30] **Alice:** We will establish the deployment sequence today.
[00:04:45] **Bob:** Agreed, let us proceed with the review.

## Ambiguity Lock Register
| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |
|---|---|---|---|---|---|
| 1 | Speaker Attribution | [00:01:00] | stmt-01 | Alice (PartnerAdvisory) confirmed | Roster Grounded |
| 2 | Speaker Attribution | [00:02:15] | stmt-02 | Bob (Google) confirmed | Roster Grounded |
"""
    judge = ForensicTranscriptJudge()
    res = judge.evaluate(
        f1_text=clean_f1,
        raw_source_text=clean_f1 + "\nraw audio log",
        mock=True,
        metadata={"attendees": ["Alice", "Bob"]},
    )

    assert res["verdict"] == "PASS"
    assert res["dimension_1_forensic_grounding_and_fidelity"] >= 14
    assert res["dimension_2_attribution_and_room_mic_hygiene"] >= 14
    assert res["dimension_3_chronological_and_timestamp_integrity"] >= 14
    assert res["dimension_4_asr_cleanup_and_domain_preservation"] >= 14
    assert res["composite_score"] >= 90


def test_clean_output2_e2e_lifecycle_pass():
    """Clean Output 2 recap passes end-to-end evaluation."""
    clean_f2 = """# Meeting Objective
Operational context establishes the baseline for the partner integration.

# Key Discussion Points & Findings
- **Alice Lead Architecture Alignment:** Team confirmed the target architecture for the data pipeline with 70 to 110 words of substantive explanation and detailed findings.

# Strategic Synthesis
| What We Heard | What It Means |
| :--- | :--- |
| Integration requires low latency | Deploy streaming connectors |

# Action & Governance Ledger
| Type | Action Item | Owner | Target Date | Governance Scope | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Action | Finalize connectors | Alice | 2026-08-01 | Architecture | Open |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
### Alice (PartnerAdvisory Lead)
- **Low Latency Pipeline Architecture**: Emphasized low latency requirements and stream processing stability across distributed data nodes.
- **Integration Timeline & Milestones**: Confirmed deployment targets and streaming connector specifications for the upcoming sprint.
"""
    f1 = """**Date:** 08/01/2026
Duration: ~20m
**Participants:** Alice (PartnerAdvisory)
Discussion of latency and connectors.
"""
    judge = ExecutiveRecapJudge()
    res = judge.evaluate(
        f2_text=clean_f2,
        f1_text=f1,
        mock=True,
        metadata={"attendees": ["Alice"], "archetype": "STRATEGIC_DISCOVERY"},
    )

    assert res["verdict"] == "PASS"
    assert res["dimension_1_executive_altitude"] >= 14
    assert res["dimension_2_technical_substance"] >= 14
    assert res["dimension_3_strategic_synthesis"] >= 14
    assert res["dimension_4_scoping_and_modality"] >= 14
    assert res["composite_score"] >= 90


def test_clean_output3_e2e_lifecycle_pass():
    """Clean Output 3 JSON projection passes end-to-end evaluation."""
    clean_f3 = {
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:alice", "type": "person", "evidence_ids": ["ev-1"]},
                {"id": "org:partner_advisory", "type": "organization", "evidence_ids": ["ev-1"]},
                {"id": "system:pipeline", "type": "system", "evidence_ids": ["ev-2"]},
            ],
            "relationship_signals": [
                {"from_id": "person:alice", "to_id": "org:partner_advisory", "predicate": "MEMBER_OF", "evidence_ids": ["ev-1"]},
                {"from_id": "person:alice", "to_id": "system:pipeline", "predicate": "OWNS_OR_LEADS", "evidence_ids": ["ev-2"]},
            ],
            "evidence": [
                {"id": "ev-1", "quote": "Alice spoke about PartnerAdvisory delivery", "locator": "[00:01:00]"},
                {"id": "ev-2", "quote": "Alice confirmed pipeline architecture", "locator": "[00:02:00]"},
            ],
            "action_updates": [],
        }
    }
    f1 = "Alice spoke about PartnerAdvisory delivery. Later, Alice confirmed pipeline architecture."
    judge = GraphExtractionJudge(mock=True)
    res = judge.evaluate(
        f3_text=json.dumps(clean_f3),
        f1_text=f1,
        mock=True,
        archetype="STRATEGIC_DISCOVERY",
    )

    assert res["verdict"] == "PASS"
    assert res["dimension_4_predicate_specificity"] >= 11
    assert res["dimension_5_exclusion_of_action_items"] == 15
    assert res["dimension_6_evidence_grounding_and_schema"] >= 14
    assert res["composite_score"] >= 90


def test_tactical_alignment_output3_e2e_omits_d1_keeps_d2():
    """Tactical alignment Output 3 omits Dim 1 but retains and evaluates Dim 2."""
    clean_f3 = {
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:alice", "type": "person", "evidence_ids": ["ev-1"]},
                {"id": "system:pipeline", "type": "system", "evidence_ids": ["ev-1"]},
            ],
            "relationship_signals": [
                {"from_id": "person:alice", "to_id": "system:pipeline", "predicate": "DEPLOYS", "evidence_ids": ["ev-1"]},
            ],
            "evidence": [
                {"id": "ev-1", "quote": "Alice deployed the pipeline", "locator": "[00:01:00]"},
            ],
            "action_updates": [],
        }
    }
    f1 = "Alice deployed the pipeline."
    judge = GraphExtractionJudge(mock=True)
    res = judge.evaluate(
        f3_text=json.dumps(clean_f3),
        f1_text=f1,
        mock=True,
        archetype="TACTICAL_ALIGNMENT",
    )

    assert res["verdict"] == "PASS"
    assert res["dimension_1_strategic_edge"] is None
    assert res["dimension_2_knowledge_layer_density"] == 15
    assert res["dimension_4_predicate_specificity"] == 15
    assert res["dimension_5_exclusion_of_action_items"] == 15
    assert res["dimension_6_evidence_grounding_and_schema"] >= 14
