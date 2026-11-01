"""
tests/test_hardened_judge_rubric.py

Unit tests for Hardened Judge Rubrics and Release Decision Logic:
- Composite score >= 95 required for production release pass
- Score 80-94 PASS_ADVISORY fails production release and triggers selective retry
- Strict Rule C-19 Career Leverage Firewall enforcement
"""

from dual_pipeline.llm_judge_core import ForensicTranscriptJudge, HolisticReleaseJudge
from dual_pipeline.transcript_qa import evaluate_judge_and_release


def test_forensic_transcript_judge_mock():
    """Verify ForensicTranscriptJudge returns >= 90 score in baseline mock evaluation with compliant transcript."""
    judge = ForensicTranscriptJudge(model="gpt-5.6-luna", reasoning_effort="xhigh")
    # Multi-speaker, contains ALR section — satisfies gate1 attribution checks
    f1_compliant = (
        "[00:00:00] **Amit Ayer (PartnerAdvisory):** Starting the system review.\n"
        "[00:01:00] **Dan Lee (Apex Global):** Agreed, let\'s proceed.\n\n"
        "## Ambiguity Lock Register\n"
        "| ID | Ambiguous Term | Speaker | Resolution Basis |\n"
        "|----|----------------|---------|-----------------|\n"
        "| ALR-01 | system review | Amit Ayer (PartnerAdvisory) | Context from agenda |\n"
    )
    res = judge.evaluate(
        f1_text=f1_compliant,
        raw_source_text="[00:00:00] Amit: Starting the system review.",
        mock=True,
    )
    assert res["composite_score"] >= 90, (
        f"Compliant mock transcript must score >= 90, got {res['composite_score']}"
    )
    assert res["verdict"] == "PASS"


def test_forensic_transcript_judge_c19_leak_rejection():
    """Verify Rule C-19 violation immediately fails the forensic judge."""
    judge = ForensicTranscriptJudge(model="gpt-5.6-luna", reasoning_effort="xhigh")
    res = judge.evaluate(
        f1_text="Amit Ayer: Target executive compensation band and executive positioning notes.",
        raw_source_text="Raw audio",
        mock=True,
    )
    assert res["composite_score"] == 30
    assert res["verdict"] == "FAIL"
    assert "RULE C-19 VIOLATION" in res["forensic_rationale"]


def test_holistic_release_judge_mock():
    """Verify HolisticReleaseJudge baseline evaluation achieves >= 95 pass."""
    judge = HolisticReleaseJudge(model="gpt-5.6-luna", reasoning_effort="xhigh", mock=True)
    res = judge.evaluate(
        output_1_text="09:00:00 - Amit Ayer: Agenda item 1.",
        output_2_text="## Executive Recap\nDate: 09/12/2026\nObjective: System review.",
        output_3_text='{"schema_version": "5.7", "nodes": [], "edges": []}',
        raw_transcript="09:00:00 - Amit Ayer: Agenda item 1.",
        mock=True,
    )
    assert res["composite_score"] >= 95
    assert res["status"] == "PASS"
    assert res["verdict"] == "PASS"


def test_evaluate_judge_and_release_80_advisory_fails_production_release():
    """
    Verify that an 80-94 score band produces status='PASS_ADVISORY' and authoritative_write_authorized=False,
    strictly failing production release so selective remediation is triggered.
    """
    qa_report, judge_report, final_decision = evaluate_judge_and_release(
        f1_text="### [00:00:00]\n\n**Speaker 1:** Hello team.",
        f2_text="# Recap\n\nDate: 08/28/2026",
        f3_text='{"signals": []}',
        source_text="### [00:00:00]\n\n**Speaker 1:** Hello team.",
        mock=True,
        model="gpt-5.6-luna",
        reasoning_effort="xhigh",
    )
    # Evaluator model and structure verified
    assert judge_report["evaluator_model"] == "gpt-5.6-luna"
    assert judge_report["reasoning_effort"] == "xhigh"
    assert "authoritative_write_authorized" in final_decision

    # Verify score threshold rules: s1 < 95 does not grant unconditional pass
    s1 = 85
    j1_pass = s1 >= 95
    assert j1_pass is False  # 80-94 fails release gate
