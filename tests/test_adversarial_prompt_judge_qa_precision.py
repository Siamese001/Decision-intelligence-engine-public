"""
tests/test_adversarial_prompt_judge_qa_precision.py

Comprehensive test suite verifying prompt, judge, and QA gate hardening across
Outputs 1, 2, and 3 resulting from the 5-agent adversarial debate.
"""
from __future__ import annotations

import re

from dual_pipeline.llm_judge_core.forensic_checks import (
    assign_forensic_verdict,
)
from dual_pipeline.transcript_qa_core.gate1_attribution import (
    _check_single_speaker_collapse as gate1_check_collapse,
)
from dual_pipeline.transcript_qa_core.gate2 import _validate_action_item_line
from dual_pipeline.transcript_qa_core.gate3 import (
    _check_evidence_kinds,
    _check_temporal_hygiene,
    run_qa_gate_3,
)


def test_gate1_single_speaker_collapse_detection():
    """Verify Gate 1 flags single-speaker collapse when dominant speaker >= 85% of turns."""
    # 10 turns: 9 for Stefan, 1 for Chakra (90% >= 85%)
    spk_counts = {"Cameron Mercer": 9, "Chakra Sankaraiah": 1}
    roster_names = ["Cameron Mercer", "Chakra Sankaraiah"]
    failures = gate1_check_collapse(spk_counts, roster_names, bypass=False)
    assert len(failures) == 1
    assert "SINGLE_SPEAKER_COLLAPSE_DETECTED" in failures[0]
    assert "Cameron Mercer" in failures[0]

    # 10 turns: 8 for Stefan, 2 for Chakra (80% < 85%)
    spk_counts_ok = {"Cameron Mercer": 8, "Chakra Sankaraiah": 2}
    failures_ok = gate1_check_collapse(spk_counts_ok, roster_names, bypass=False)
    assert len(failures_ok) == 0


def test_forensic_judge_per_dimension_floor_and_envelope():
    """Verify Forensic Judge enforces per-dimension floor (<14 triggers FAIL) and critique envelope."""
    # Composite is 92, but dimension 2 is 12 (<14 floor)
    parsed = {
        "dimension_1_forensic_grounding_and_fidelity": 25,
        "dimension_2_attribution_and_room_mic_hygiene": 12,
        "dimension_3_chronological_and_timestamp_integrity": 25,
        "dimension_4_asr_cleanup_and_domain_preservation": 25,
        "composite_score": 87,
        "forensic_rationale": "Attribution has flaws.",
        "actionable_remediation_diff": "Fix speaker labels.",
    }
    result = assign_forensic_verdict(parsed)
    assert result["verdict"] == "FAIL"
    assert "PER-DIMENSION FLOOR FAILURE" in result["forensic_rationale"]
    assert "critique_diff_envelope" in result
    assert result["critique_diff_envelope"]["artifact"] == "OUTPUT01"
    assert result["critique_diff_envelope"]["target_path"] == "Forensic Transcript"


def test_gate2_action_ledger_single_dri():
    """Verify Gate 2 rejects generic owners ('Team', 'All', 'PartnerAdvisory / NEE') in action items."""
    passive_verbs = re.compile(r"^\s*(Acknowledge|Note|Consider|Be aware)", re.IGNORECASE)

    # 7-column row with generic owner "PartnerAdvisory / NEE"
    generic_line = "| 1 | Action | Deploy new inference pipeline | Proposed | PartnerAdvisory / NEE | 09/25/2026 | Critical |"
    no_passive, no_unassigned, fails = _validate_action_item_line(generic_line, is_tech_sync=False, passive_verbs=passive_verbs)
    assert no_unassigned is False
    assert any("single accountable DRI required" in f for f in fails)

    # 7-column row with generic owner "Team"
    team_line = "| 2 | Action | Review architecture document | Open | Team | 09/26/2026 | Required |"
    no_passive, no_unassigned, fails = _validate_action_item_line(team_line, is_tech_sync=False, passive_verbs=passive_verbs)
    assert no_unassigned is False
    assert any("single accountable DRI required" in f for f in fails)

    # 7-column row with named single DRI "Drew Oakley"
    valid_line = "| 3 | Action | Ratify migration playbook | In Progress | Drew Oakley | 09/28/2026 | High priority |"
    no_passive, no_unassigned, fails = _validate_action_item_line(valid_line, is_tech_sync=False, passive_verbs=passive_verbs)
    assert no_unassigned is True
    assert len(fails) == 0


def test_gate3_closed_evidence_kinds():
    """Verify Gate 3 rejects evidence items outside the 6 closed kinds."""
    invalid_evidence = {
        "ev-01": {"kind": "fact", "text": "Something happened", "exact_text": "Something happened"},
        "ev-02": {"kind": "decision", "text": "Decided X", "exact_text": "Decided X"},
    }
    passed, fails = _check_evidence_kinds(invalid_evidence)
    assert passed is False
    assert len(fails) == 1
    assert "invalid kind 'fact'" in fails[0]

    valid_evidence = {
        "ev-01": {"kind": "summary", "text": "Something happened", "exact_text": "Something happened"},
        "ev-02": {"kind": "technical", "text": "Decided X", "exact_text": "Decided X"},
        "ev-03": {"kind": "strategic", "text": "Wedge opportunity", "exact_text": "Wedge opportunity"},
        "ev-04": {"kind": "dependency", "text": "Depends on Y", "exact_text": "Depends on Y"},
        "ev-05": {"kind": "risk", "text": "Risk of Z", "exact_text": "Risk of Z"},
        "ev-06": {"kind": "decision", "text": "Decided W", "exact_text": "Decided W"},
    }
    passed, fails = _check_evidence_kinds(valid_evidence)
    assert passed is True
    assert len(fails) == 0


def test_gate3_temporal_hygiene_stale_after():
    """Verify Gate 3 validates observed_at and stale_after dates."""
    # Invalid stale_after format
    decisions = [{"observed_at": "09/20/2026", "stale_after": "2026-10-01"}]
    risks = [{"observed_at": "09/20/2026", "stale_after": "next week"}]
    passed, fails = _check_temporal_hygiene(decisions, risks)
    assert passed is False
    assert len(fails) == 2
    assert "must be MM/DD/YYYY" in fails[0]
    assert "must be MM/DD/YYYY" in fails[1]

    # Valid MM/DD/YYYY dates
    decisions_ok = [{"observed_at": "09/20/2026", "stale_after": "10/01/2026"}]
    risks_ok = [{"observed_at": "09/20/2026", "stale_after": "10/15/2026"}]
    passed_ok, fails_ok = _check_temporal_hygiene(decisions_ok, risks_ok)
    assert passed_ok is True
    assert len(fails_ok) == 0


def test_gate3_empty_action_updates_invariant():
    """Verify Gate 3 fails if action_updates contains any tactical tasks."""
    payload = {
        "schema_version": "5.7",
        "meeting_id": "meeting-20260920-sync",
        "classification": "internal",
        "audiences": ["PartnerAdvisory", "Client"],
        "expected_base_graph_version": "1.0.0",
        "evidence_index": {
            "ev-01": {"kind": "technical", "text": "Grounded quote", "exact_text": "Grounded quote"}
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "system:nexus", "name": "Nexus System", "type": "system", "mutation": "CREATE"}
            ],
            "relationship_signals": [
                {"from_id": "system:nexus", "to_id": "system:nexus", "relationship": "INTEGRATES_WITH", "evidence_ids": ["ev-01"]}
            ],
            "decision_updates": [],
            "proposal_updates": [],
            "action_updates": [{"action_id": "action:task-1", "description": "Fix the bug"}],
            "risk_updates": [],
        },
        "evidence_only_statement_ids": [],
    }
    result = run_qa_gate_3(payload, f1_text="Grounded quote", metadata={"enforce_empty_actions": True})
    assert result["passed"] is False
    assert any("Non-empty action_updates detected" in f for f in result["failures"])
