"""
tests/test_healing_mesh_qa_synchronization.py

Regression test suite validating Wave 1:
1. Deterministic gap healing synchronization with Gate 1.5 (32s < delta <= 180s).
2. Deterministic attendees header injection in Output 02.
3. Deterministic no-action confirmation phrase injection in Output 02.
4. Fail-closed deterministic-only error detection without judge catch-22.
"""
from dual_pipeline.transcript_pipeline_core.qa_runner import _is_deterministic_only_failure
from dual_pipeline.transcript_qa_core.healing_mesh import (
    _heal_output_1_gaps,
    _heal_output_2_actions,
)
from dual_pipeline.transcript_qa_core.recap_healer import _heal_output_2_attendees


def test_heal_output_1_gaps_sub_180s():
    """Gaps between 32s and 180s with low word count must be annotated to satisfy Rule C-11 and Gate 1.5."""
    raw_transcript = (
        "# Meeting Transcript\n\n"
        "### [00:47:28] -- stmt-063\n\n"
        "**[PartnerAdvisory Speaker]:** Yeah.\n\n"
        "### [00:48:32] -- stmt-064\n\n"
        "**[PartnerAdvisory Speaker]:** Yeah, we'll talk about it there too.\n"
    )
    changes = []
    audit_log = []
    healed = _heal_output_1_gaps(raw_transcript, changes, audit_log)
    assert len(changes) == 1
    assert "Natural pause: 00:47:28 – 00:48:32 (no speech)" in healed
    assert "64s gap" in changes[0]


def test_heal_output_2_attendees_injection():
    """When Output 2 lacks Attendees header, healer injects it from metadata."""
    f2_missing_attendees = (
        "# Subject: Test Meeting -- Executive Brief\n\n"
        "Date: 09/18/2026\n\n"
        "# Meeting Objective\n\n"
        "Test objective description with sufficient length to pass gates.\n"
    )
    meta = {
        "attendees": {
            "PartnerAdvisory": ["Amit Ayer (Lead Enterprise Architect)", "Jake Stark (LENS Lead)"]
        }
    }
    changes = []
    audit_log = []
    healed = _heal_output_2_attendees(f2_missing_attendees, meta, changes, audit_log)
    assert len(changes) == 1
    assert "# Attendees" in healed
    assert "Amit Ayer" in healed
    assert "Jake Stark" in healed


def test_heal_output_2_actions_confirmation_injection():
    """When action table is empty, healer injects compliant no-action ratification phrase."""
    f2_empty_actions = (
        "# Subject: Test Meeting -- Executive Brief\n\n"
        "# Recommendations & Decisions Required\n\n"
        "The proposed work remains unassigned.\n\n"
        "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
        "Appendix details here.\n"
    )
    changes = []
    audit_log = []
    healed = _heal_output_2_actions(f2_empty_actions, changes, audit_log)
    assert len(changes) == 1
    assert "No formal action items were assigned during this session" in healed


def test_is_deterministic_only_failure_when_judge_skipped():
    """When judge is short-circuited and all failures are deterministic markers, must fail closed immediately."""
    qa_report = {
        "output_01_forensic_transcript": {
            "failures": [
                "Gate_1_10_Referenced_Entity_Context failure: referenced entity 'Alex' ungrounded in Output 2."
            ]
        },
        "output_02_executive_recap": {"failures": []},
        "output_03_canonical_signal_projection": {"failures": []},
        "cross_artifact_consistency": {"failures": []},
    }
    # Judge was short-circuited due to QA failure: llm_score_pass is False across all outputs
    final_decision = {
        "status": "FAIL",
        "output_1_deterministic_gates_pass": False,
        "output_1_llm_score_pass": False,
        "output_2_deterministic_gates_pass": True,
        "output_2_llm_score_pass": False,
        "output_3_deterministic_gates_pass": True,
        "output_3_llm_score_pass": False,
    }
    assert _is_deterministic_only_failure(qa_report, final_decision) is True
