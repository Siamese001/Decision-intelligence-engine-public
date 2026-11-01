"""
tests/end_to_end/test_unattributed_vtt_docx_adversarial.py

Adversarial & Edge-Case Regression Suite for Unattributed WebVTT .docx Ingestion.
Covers 4 mandatory edge-case categories:
1. Boundary payload: Header-only or empty WebVTT .docx raises deterministic ValueError.
2. Malformed input: Single-line VTT cues with malformed timestamps, missing arrows, and HTML/XML tags.
3. Invariant bypass / prose-colon contamination: Trailing 'Purpose:', 'Core pain:', 'Interviewee:'
   lines cannot contaminate candidate attendees or bypass U0_UNMAPPED_SPEAKERS / U0_ATTENDEE_CONFIRMATION.
4. Exact production regression: Single-line UUID WebVTT + trailing 'Findings and Analysis' blocks
   fail-closed at Stage U0 with both U0_UNMAPPED_SPEAKERS and U0_ATTENDEE_CONFIRMATION.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest
from tests.end_to_end.test_unattributed_vtt_docx_e2e import _build_minimal_docx

from dual_pipeline.canonical_pipeline.u0_ingress import (
    UnifiedPreflightCascadeError,
    batch_validate_preflight_checks,
)
from dual_pipeline.transcript_metadata_helper import is_document_content
from dual_pipeline.transcript_pipeline_core.format_parsers import parse_vtt_or_srt
from dual_pipeline.transcript_pipeline_core.normalizer import (
    _extract_docx_text,
    normalize_raw_input_text,
)
from dual_pipeline.transcript_stages_core.attendee_gate import (
    extract_candidate_attendees,
    is_audio_or_raw_source,
    verify_mandatory_attendee_confirmation,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError
from dual_pipeline.transcript_stages_core.u0_ingestion import (
    U0IngestionBoundary,
    _extract_raw_speakers,
)


def _create_docx(path: Path, paragraphs: List[str]) -> Path:
    return _build_minimal_docx(path, paragraphs)


def test_boundary_payload_header_only_vtt_raises_deterministic_error() -> None:
    """Category 1 (Boundary payload): Header-only WebVTT without valid cues fails closed."""
    header_only_vtt = "WEBVTT\n\nNOTE Empty interview export\n"
    with pytest.raises(ValueError, match="Failed to parse any dialogue turns"):
        parse_vtt_or_srt(header_only_vtt)


def test_malformed_single_line_vtt_cues_and_html_tags() -> None:
    """Category 2 (Malformed input): Corrupted cue lines are skipped while HTML-tagged cues survive."""
    malformed_vtt = "\n".join(
        [
            "WEBVTT",
            "bad-cue-1 00:00:05.000 -- 00:00:08.000 Missing arrow should be ignored",
            "good-cue-1 00:00:10.000 --> 00:00:12.500 <b>Hello</b> <i>world</i> from cue one.",
            "bad-cue-2 99:99:99 --> invalid_timestamp Corrupt timestamp line",
            "good-cue-2 00:00:13.000 --> 00:00:16.000 <v Dustin>Checking the outage dashboard.</v>",
        ]
    )
    parsed = parse_vtt_or_srt(malformed_vtt)
    assert len(parsed.turns) == 2
    assert "Hello world from cue one." in parsed.text
    assert "<b>" not in parsed.text
    assert "Missing arrow" not in parsed.text
    assert parsed.turns[1].speaker == "Dustin"


def test_invariant_bypass_prose_colons_never_become_speakers_or_attendees() -> None:
    """Category 3 (Invariant bypass): Prose colon headings cannot pollute speaker or attendee rosters."""
    raw_with_prose = "\n".join(
        [
            "### [00:00:14]",
            "",
            "**Unknown:** Let us walk through the daily workflow.",
            "",
            "Findings and Analysis",
            "Purpose: Understand a typical day for an operations lead.",
            "Source: Interview #1 transcript",
            "Interviewee: Operations Lead",
            "Core pain: Fragmented spreadsheets and manual email tracking.",
            "Top feature requests: Unified outage dashboard and mobile alerts.",
            "Key adoption risk: Change fatigue across field crews.",
            "Training expectation: Hands-on coaching during rollout.",
        ]
    )
    forbidden_labels = {
        "Purpose",
        "Source",
        "Interviewee",
        "Core pain",
        "Top feature requests",
        "Key adoption risk",
        "Training expectation",
    }

    extracted_raw = _extract_raw_speakers(raw_with_prose)
    assert extracted_raw.isdisjoint(forbidden_labels)
    assert extracted_raw == set()

    candidates = extract_candidate_attendees({}, raw_with_prose)
    assert set(candidates).isdisjoint(forbidden_labels)
    assert candidates == ["Unknown Speaker(s)"]


def test_production_regression_unattributed_vtt_docx_forces_u0_hitl(tmp_path: Path) -> None:
    """Category 4 (Exact production regression): Unlabeled WebVTT .docx halts at Stage U0 for HITL."""
    paragraphs = [
        "WEBVTT",
        "c20462fa-373e-4666-a5f4-fdbe1de69e11/12-0 00:00:14.009 --> 00:00:15.049 Yeah.",
        "c20462fa-373e-4666-a5f4-fdbe1de69e11/19-0 00:00:17.289 --> 00:00:19.249 Thank you guys for joining.",
        "c20462fa-373e-4666-a5f4-fdbe1de69e11/28-0 00:00:22.409 --> 00:00:28.129 Walk us through a day in the life.",
        "Findings and Analysis",
        "Purpose: Understand a typical day, pain points, and tool gaps.",
        "Source: Interview #1 transcript",
        "Interviewee: Operations Lead",
        "Core pain: Manual spreadsheets and disconnected systems.",
        "Top feature requests: Single pane of glass.",
        "Key adoption risk: Lack of field input.",
        "Training expectation: Role-based sessions.",
    ]
    docx_path = _create_docx(tmp_path / "JARVIS_Day_in_the_Life_Interview_1.docx", paragraphs)
    extracted = _extract_docx_text(docx_path)

    assert is_document_content(extracted, docx_path.name) is False
    norm = normalize_raw_input_text(extracted, input_ext=".docx", metadata={"input_path": str(docx_path)})
    assert len(norm.turns) == 3

    metadata = {
        "input_path": str(docx_path),
        "input_ext": ".docx",
        "project": "nea",
        "meeting_name": "JARVIS Day in the Life Interview #1",
        "date": "10/27/2026",
        "time": "09:00 AM - 10:00 AM ET",
    }
    assert is_audio_or_raw_source(metadata) is True

    report = batch_validate_preflight_checks(metadata, raw_transcript=norm.text, input_path=docx_path)
    assert report.is_valid is False
    check_names = {d.check_name for d in report.diagnostics}
    assert "U0_UNMAPPED_SPEAKERS" in check_names
    assert "U0_ATTENDEE_CONFIRMATION" in check_names

    u0 = U0IngestionBoundary()
    with pytest.raises(UnifiedPreflightCascadeError) as exc_info:
        u0.execute(norm.text, dict(metadata))
    assert "speaker_map" in str(exc_info.value)
    assert "attendees_confirmed_by_operator" in str(exc_info.value)
    cascade_checks = {d.check_name for d in exc_info.value.report.diagnostics}
    assert "U0_UNMAPPED_SPEAKERS" in cascade_checks
    assert "U0_ATTENDEE_CONFIRMATION" in cascade_checks

    with pytest.raises(HITLRequiredError) as att_exc:
        verify_mandatory_attendee_confirmation(dict(metadata), norm.text)
    assert att_exc.value.hitl_type == "attendee_confirmation"
    assert att_exc.value.unmapped_speakers == ["Unknown Speaker(s)"]
