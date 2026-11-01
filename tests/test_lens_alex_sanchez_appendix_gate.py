"""
tests/test_lens_alex_sanchez_appendix_gate.py

Regression test suite for Project LENS Alex Sanchez meeting (09/22/2026):
- Validates Gate 2.8 Appendix Completeness on Deliverable 02
- Asserts >= 5 distinct titled themes and >= 500 words per speaker
- Asserts run_qa_gate_2 passes 100% with zero failures
- Asserts evaluate_all_qa_gates passes across all three deliverables
"""

import json
from pathlib import Path

import pytest

from dual_pipeline.transcript_qa import evaluate_all_qa_gates, run_qa_gate_2
from dual_pipeline.transcript_qa_core.gate2 import _check_appendix_completeness

MEETING_DIR = Path(__file__).resolve().parent.parent / "output" / "project_lens" / "09-22-2026" / "LENS_Alex_Sanchez"
F1_PATH = MEETING_DIR / "01_LENS_Alex_Sanchez_09-22-2026_0607PM_0717PM_ET_hitl_resolved_transcript.md"
F2_PATH = MEETING_DIR / "02_LENS_Alex_Sanchez_09-22-2026_0607PM_0717PM_ET_executive_recap.md"
F3_PATH = MEETING_DIR / "03_LENS_Alex_Sanchez_09-22-2026_0607PM_0717PM_ET_canonical_signal_projection.json"


@pytest.fixture
def f2_text() -> str:
    assert F2_PATH.exists(), f"Deliverable 02 not found at {F2_PATH}"
    return F2_PATH.read_text(encoding="utf-8")


@pytest.fixture
def session_metadata() -> dict:
    return {
        "project": "project_lens",
        "meeting_name": "LENS_Alex_Sanchez",
        "meeting_date": "09/22/2026",
        "date": "09/22/2026",
        "start_time": "06:07 PM ET",
        "end_time": "07:17 PM ET",
        "duration_minutes": 70,
        "archetype": "GOVERNANCE_REVIEW",
        "attachments": [
            {
                "filename": "LENS_Autonomous_Asset_Intelligence_Pipeline_v1.2_modified.txt",
                "attribution_mode": "COLLATERAL_REVIEWED",
                "attachment_type": "document",
            }
        ],
    }


from dual_pipeline.transcript_qa_core.appendix_standard import audit_appendix_gold_standard


def test_gate_2_8_appendix_completeness_lens_alex_sanchez(f2_text: str, session_metadata: dict) -> None:
    """Gate 2.8: Appendix must have >= 5 themes and >= 500 words per primary speaker."""
    passed, has_appendix, failures = _check_appendix_completeness(f2_text, session_metadata)
    assert passed is True, f"Gate 2.8 failed with: {failures}"
    assert has_appendix is True
    assert not failures, f"Unexpected failures in Gate 2.8: {failures}"

    res = audit_appendix_gold_standard(
        f2_text,
        duration_minutes=session_metadata["duration_minutes"],
        primary_speakers=["Amit Ayer", "Alex Sanchez"],
    )
    assert res.passed is True
    assert res.has_appendix is True
    assert not res.failures
    assert res.speaker_sections_count >= 2
    assert res.thematic_perspectives_count >= 10

    # Verify each primary speaker in details
    speaker_details = res.speaker_details
    for name in ["Amit Ayer", "Alex Sanchez"]:
        matching_key = next((k for k in speaker_details if name.lower() in k.lower()), None)
        assert matching_key is not None, f"Speaker {name} not found in {list(speaker_details.keys())}"
        spk = speaker_details[matching_key]
        assert spk["theme_count"] >= 5, f"{name} theme count {spk['theme_count']} < 5"
        assert spk["body_words"] >= 500, f"{name} body words {spk['body_words']} < 500"


def test_run_qa_gate_2_passes_lens_alex_sanchez(f2_text: str, session_metadata: dict) -> None:
    """Full Gate 2 suite must pass on Deliverable 02 with zero failures."""
    res = run_qa_gate_2(f2_text, metadata=session_metadata)
    assert res["passed"] is True, f"Gate 2 failed: {res.get('failures')}"
    assert len(res.get("failures", [])) == 0, f"Gate 2 failures: {res.get('failures')}"
    assert res["gates"]["Gate_2_8_Appendix_Completeness"]["passed"] is True


def test_evaluate_all_qa_gates_passes_lens_alex_sanchez(session_metadata: dict) -> None:
    """All QA gates must pass across deliverables 1, 2, and 3."""
    assert F1_PATH.exists()
    assert F2_PATH.exists()
    assert F3_PATH.exists()

    f1 = F1_PATH.read_text(encoding="utf-8")
    f2 = F2_PATH.read_text(encoding="utf-8")
    f3 = F3_PATH.read_text(encoding="utf-8")

    report = evaluate_all_qa_gates(
        f1_text=f1,
        f2_text=f2,
        f3_text=f3,
        source_text=f1,
        metadata=session_metadata,
        auto_heal=False,
    )

    assert report["passed"] is True, (
        f"QA gates evaluation failed! "
        f"G1={report['output_01_forensic_transcript'].get('passed')}, "
        f"G2={report['output_02_executive_recap'].get('passed')}, "
        f"G3={report['output_03_canonical_signal_projection'].get('passed')}, "
        f"Cross={report['cross_artifact_consistency'].get('passed')}. "
        f"Failures: {json.dumps(report.get('cross_artifact_consistency', {}).get('failures', []))}"
    )
