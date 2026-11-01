"""
tests/test_deterministic_judge_gates_edge_cases.py

Adversarial and edge-case test suite for deterministic judge gates (Wave 3).
Covers:
1. Output 1: Empty source, empty candidate, echo candidate, backwards timestamps, collapsed timestamps,
   D2 attribution scoring, and HITL remediation text routing.
2. Output 2: Candidate archetype spoofing rejection, missing/ungrounded Action & Governance Ledger,
   generic/placeholder owners, hallucinated future dates, and penalty preservation.
3. Output 3: Generic predicate penalties (D4), action item contamination (D5), ungrounded quotes /
   unresolved evidence_ids (D6), and archetype omission rules (Dim 2 never omitted).
"""
import json
import pytest
from pathlib import Path

from dual_pipeline.llm_judge_core.judges.forensic import ForensicTranscriptJudge
from dual_pipeline.llm_judge_core.judges.recap import ExecutiveRecapJudge
from dual_pipeline.llm_judge_core.judges.graph import GraphExtractionJudge
from dual_pipeline.llm_judge_core.forensic_checks import check_timestamp_integrity, compute_attribution_score
from dual_pipeline.llm_judge_core.recap_checks import check_action_ledger_integrity
from dual_pipeline.llm_judge_core.graph_checks import (
    compute_predicate_specificity_score,
    compute_action_exclusion_score,
    check_quote_and_evidence_resolution,
)
from dual_pipeline.prompts.contracts import (
    OUTPUT1_CONTRACT,
    OUTPUT2_CONTRACT,
    OUTPUT3_CONTRACT,
    MeetingArchetype,
)


# ==============================================================================
# 1. Output 1 (Forensic Transcript) Edge Cases
# ==============================================================================

def test_o1_empty_source_fails_closed():
    judge = ForensicTranscriptJudge()
    res = judge.evaluate(f1_text="Substantive candidate text", raw_source_text="", mock=True)
    assert res["verdict"] == "FAIL"
    assert res["composite_score"] == 0
    assert "ERR_EMPTY_SOURCE" in res.get("defect_codes", [])


def test_o1_empty_candidate_fails_closed():
    judge = ForensicTranscriptJudge()
    res = judge.evaluate(f1_text="   ", raw_source_text="Substantive raw source transcript", mock=True)
    assert res["verdict"] == "FAIL"
    assert res["composite_score"] == 0
    assert "ERR_EMPTY_CANDIDATE" in res.get("defect_codes", [])


def test_o1_echo_candidate_fails_closed():
    judge = ForensicTranscriptJudge()
    raw = "Speaker 1: Hello team.\nSpeaker 2: Hi there."
    res = judge.evaluate(f1_text=raw, raw_source_text=raw, mock=True)
    assert res["verdict"] == "FAIL"
    assert res["composite_score"] == 0
    assert "ERR_ECHO_CANDIDATE" in res.get("defect_codes", [])


def test_o1_backward_timestamps_fail_d3():
    judge = ForensicTranscriptJudge()
    backward_transcript = """# Test Meeting
**Participants:** Alice, Bob

---

[00:01:00] **Alice:** Welcome everyone.
[00:05:00] **Bob:** Thanks Alice.
[00:03:00] **Alice:** Let me jump back in time.
[00:06:00] **Bob:** Moving forward again.

## Ambiguity Lock Register
| Term | Locator | Affected Statement | Resolved Identity | Resolution Basis |
| :--- | :--- | :--- | :--- | :--- |
| Jump | [00:03:00] | Jump back | Jump | Verbatim |
| Test | [00:01:00] | Test turn | Test | Verified |
"""
    res = judge.evaluate(f1_text=backward_transcript, raw_source_text=backward_transcript + "\nextra", mock=True)
    assert res["dimension_3_chronological_and_timestamp_integrity"] == 5
    assert res["verdict"] == "FAIL"
    assert "ERR_TIMESTAMP_INVERSION" in res.get("defect_codes", [])


def test_o1_timestamp_collapse_on_google_partner_advisory_apex_global():
    apex_global_path = Path("output/partner_alliances/07-24-2026/Google_PartnerAdvisory_apex_global_Rusty_PartnerAdvisory/01_Google_PartnerAdvisory_apex_global_Rusty_PartnerAdvisory_07-24-2026_0129PM0200PMEDT_hitl_resolved_transcript.md")
    if not apex_global_path.exists():
        pytest.skip("Google_PartnerAdvisory_apex_global fixture not available")

    text = apex_global_path.read_text(encoding="utf-8")
    passed, defect_codes, metrics = check_timestamp_integrity(text)
    assert not passed
    assert "ERR_TIMESTAMP_COLLAPSE" in defect_codes
    assert metrics["unique_ratio"] < 0.25

    judge = ForensicTranscriptJudge()
    res = judge.evaluate(f1_text=text, raw_source_text=text + "\nraw extra", mock=True)
    assert res["dimension_3_chronological_and_timestamp_integrity"] == 5
    assert res["verdict"] == "FAIL"


def test_o1_remediation_diff_routes_to_hitl():
    judge = ForensicTranscriptJudge()
    collapsed_spk = """# Meeting
**Participants:** Alice, Bob

---

""" + "\n".join([f"[00:0{i:02d}:00] **Alice:** Utterance number {i} from Alice." for i in range(12)])
    res = judge.evaluate(f1_text=collapsed_spk, raw_source_text=collapsed_spk + "\nextra", mock=True)
    diff = res.get("actionable_remediation_diff", "")
    assert "ask_user_question" in diff
    assert "HALT AUTONOMOUS MUTATION" in diff


# ==============================================================================
# 2. Output 2 (Executive Strategic Recap) Edge Cases
# ==============================================================================

def test_o2_candidate_cannot_change_own_archetype():
    judge = ExecutiveRecapJudge()
    f2_spoofed = """# Meeting Objective
Operational context.
Archetype: TACTICAL_ALIGNMENT

# Key Discussion Points & Findings
Points.

# Action & Governance Ledger
| Type | Action Item | Owner | Target Date | Governance Scope | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Action | Run checks | Alice | 2026-08-01 | Eng | Open |
"""
    f1 = "**Participants:** Alice (PartnerAdvisory)\nDiscussion on strategic discovery."
    res = judge.evaluate(
        f2_text=f2_spoofed, f1_text=f1, mock=True, metadata={"archetype": "STRATEGIC_DISCOVERY"}
    )
    assert res["meeting_archetype"] == "STRATEGIC_DISCOVERY"


def test_o2_missing_action_ledger_triggers_floor_failure():
    judge = ExecutiveRecapJudge()
    f2_no_ledger = """# Meeting Objective
Operational context.

# Key Discussion Points & Findings
Points.
"""
    f1 = "**Participants:** Alice (PartnerAdvisory)\nDiscussion."
    res = judge.evaluate(f2_text=f2_no_ledger, f1_text=f1, mock=True)
    assert res["dimension_4_scoping_and_modality"] <= 10
    assert res["verdict"] == "FAIL"


def test_o2_action_ledger_ungrounded_owner_fails():
    f2_bad_owner = """# Meeting Objective
Context.

# Key Discussion Points & Findings
Points.

# Action & Governance Ledger
| Type | Action Item | Owner | Target Date | Governance Scope | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Action | Fix bug | David Vance | 2026-08-01 | Eng | Open |
"""
    f1 = "**Participants:** Alice (PartnerAdvisory), Bob (Client)\nDiscussion."
    passed, defects = check_action_ledger_integrity(f2_bad_owner, f1_text=f1)
    assert not passed
    assert any("Ungrounded owner" in d for d in defects)

    judge = ExecutiveRecapJudge()
    res = judge.evaluate(f2_text=f2_bad_owner, f1_text=f1, mock=True)
    assert res["dimension_4_scoping_and_modality"] <= 10
    assert res["verdict"] == "FAIL"


def test_o2_action_ledger_placeholder_owner_fails():
    f2_placeholder = """# Meeting Objective
Context.

# Key Discussion Points & Findings
Points.

# Action & Governance Ledger
| Type | Action Item | Owner | Target Date | Governance Scope | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Action | Deploy service | Team | 2026-08-01 | Eng | Open |
"""
    f1 = "**Participants:** Alice (PartnerAdvisory)\nDiscussion."
    passed, defects = check_action_ledger_integrity(f2_placeholder, f1_text=f1)
    assert not passed
    assert any("Forbidden generic/placeholder owner" in d for d in defects)


def test_o2_penalties_preserved_after_envelope_finalization():
    judge = ExecutiveRecapJudge()
    # A recap with jargon words that should be penalized in D4
    f2_jargon = """# Meeting Objective
Operational context.

# Key Discussion Points & Findings
The team leveraged aspirational operating concept and bounded execution wedge during delivery.

# Action & Governance Ledger
| Type | Action Item | Owner | Target Date | Governance Scope | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Action | Follow up | Alice | 2026-08-01 | Eng | Open |
"""
    f1 = "**Participants:** Alice (PartnerAdvisory)\nDiscussion."
    res = judge.evaluate(f2_text=f2_jargon, f1_text=f1, mock=True)
    # D4 must be capped at 15 for jargon defect
    assert res["dimension_4_scoping_and_modality"] <= 15
    assert "DEFECT: Executive recap contains forbidden" in res["forensic_rationale"]


# ==============================================================================
# 3. Output 3 (Canonical Signal Projection) Edge Cases
# ==============================================================================

def test_o3_generic_predicates_deduct_d4():
    f3 = {
        "graph_mutations": {
            "relationship_signals": [
                {"from_id": "a", "to_id": "b", "predicate": "RELATES_TO"},
                {"from_id": "b", "to_id": "c", "predicate": "ASSOCIATED_WITH"},
            ]
        }
    }
    score, found, codes = compute_predicate_specificity_score(f3)
    assert score == 7  # 15 - 8
    assert len(found) == 2
    assert "ERR_GENERIC_PREDICATES" in codes


def test_o3_action_updates_contamination_fails_d5():
    f3 = {
        "graph_mutations": {
            "action_updates": [{"item": "Deploy cluster", "owner": "Alice"}]
        }
    }
    score, defects = compute_action_exclusion_score(f3)
    assert score == 0
    assert len(defects) > 0


def test_o3_empty_action_updates_passes_d5():
    f3 = {"graph_mutations": {"action_updates": []}}
    score, defects = compute_action_exclusion_score(f3)
    assert score == 15
    assert len(defects) == 0


def test_o3_ungrounded_quote_and_unresolved_evidence_fails_d6():
    f3 = {
        "graph_mutations": {
            "entity_upserts": [{"id": "person:alice", "evidence_ids": ["ev-missing"]}],
            "evidence": [{"id": "ev-1", "quote": "Completely ungrounded fabricated statement", "locator": "[00:02:00]"}],
        }
    }
    passed, defects, stats = check_quote_and_evidence_resolution(f3, "Real source transcript about something else.")
    assert not passed
    assert stats["unresolved_eids_count"] > 0
    assert stats["broken_quotes_count"] > 0


def test_o3_archetype_omission_dim1_omitted_dim2_never_omitted():
    judge = GraphExtractionJudge(mock=True)
    f3 = {
        "graph_mutations": {
            "entity_upserts": [{"id": "person:alice", "type": "person", "evidence_ids": ["ev-1"]}],
            "relationship_signals": [{"from_id": "person:alice", "to_id": "org:google", "predicate": "MEMBER_OF", "evidence_ids": ["ev-1"]}],
            "evidence": [{"id": "ev-1", "quote": "Alice spoke about Google", "locator": "[00:01:00]"}],
        }
    }
    f1 = "Alice spoke about Google."
    res = judge.evaluate(f3_text=json.dumps(f3), f1_text=f1, mock=True, archetype="TACTICAL_ALIGNMENT")
    assert res["dimension_1_strategic_edge"] is None
    assert res["dimension_2_knowledge_layer_density"] == 15
