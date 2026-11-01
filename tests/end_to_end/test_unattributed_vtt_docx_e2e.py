"""
End-to-end verification of single-line WebVTT .docx modality detection,
cue preservation without single-turn collapse, and trailing prose isolation.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Sequence

from dual_pipeline.canonical_pipeline.content_loader import load_canonical_source_content
from dual_pipeline.canonical_pipeline.modalities import (
    CanonicalModality,
    detect_modality,
    get_deliverables_for_modality,
)
from dual_pipeline.cli.ingest import is_document_source
from dual_pipeline.transcript_metadata_helper import is_document_content
from dual_pipeline.transcript_pipeline_core.format_parsers import parse_vtt_or_srt
from dual_pipeline.transcript_pipeline_core.normalizer import _extract_docx_text


def _build_minimal_docx(path: Path, paragraphs: Sequence[str]) -> Path:
    """Create a valid minimal .docx archive with the given paragraph strings."""
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    body_xml = "".join(
        f"<w:p><w:r><w:t>{p.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')}</w:t></w:r></w:p>"
        for p in paragraphs
    )
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body_xml}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", doc_xml)
    return path


def test_single_line_vtt_docx_modality_detection_e2e(tmp_path: Path) -> None:
    """Verify .docx files with WebVTT cues classify as MEETING_TRANSCRIPT (01, 02, 03), not DOCUMENT."""
    vtt_docx = _build_minimal_docx(
        tmp_path / "interview_vtt.docx",
        [
            "WEBVTT",
            "764b7cf2-fcbe-4eb0-a1a7-a7dbbfca12b3/8-0 00:00:14.009 --> 00:00:15.049 Yeah, OK, because I.",
            "764b7cf2-fcbe-4eb0-a1a7-a7dbbfca12b3/12-0 00:00:20.100 --> 00:00:22.400 Dustin.",
            "764b7cf2-fcbe-4eb0-a1a7-a7dbbfca12b3/15-0 00:00:32.000 --> 00:00:35.500 This is not a deposition, right?",
        ],
    )
    extracted = _extract_docx_text(vtt_docx)
    assert is_document_content(extracted, vtt_docx.name) is False
    assert is_document_source(vtt_docx, {}) is False

    modality = detect_modality(vtt_docx, extracted, {})
    assert modality == CanonicalModality.MEETING_TRANSCRIPT
    assert get_deliverables_for_modality(modality) == ["01", "02", "03"]

    prose_docx = _build_minimal_docx(
        tmp_path / "statement_of_work.docx",
        [
            "Statement of Work: Enterprise Architecture Advisory",
            "1. Executive Summary",
            "This document defines the deliverables and milestones for Phase 1.",
        ],
    )
    prose_extracted = _extract_docx_text(prose_docx)
    assert is_document_content(prose_extracted, prose_docx.name) is True
    assert is_document_source(prose_docx, {}) is True


def test_parse_vtt_or_srt_preserves_single_line_cues_and_excludes_trailing_summary() -> None:
    """Verify single-line WebVTT cues are preserved individually without collapsing or absorbing trailing prose."""
    raw_vtt = "\n".join(
        [
            "WEBVTT",
            "764b7cf2-fcbe-4eb0-a1a7-a7dbbfca12b3/8-0 00:00:14.009 --> 00:00:15.049 Yeah, OK, because I.",
            "764b7cf2-fcbe-4eb0-a1a7-a7dbbfca12b3/12-0 00:00:20.100 --> 00:00:22.400 Dustin.",
            "764b7cf2-fcbe-4eb0-a1a7-a7dbbfca12b3/15-0 00:01:05.000 --> 00:01:10.000 If you want to give me a little overview of what Jarvis is.",
            "764b7cf2-fcbe-4eb0-a1a7-a7dbbfca12b3/997-0 01:02:29.889 --> 01:02:33.569 Do you have any follow-up thoughts? Yeah, later.",
            'Jarvis "Day in the Life" Discovery Interview: Findings and Analysis',
            "Source: Transcript of a ~1 hr 2 min recorded discovery interview (WEBVTT).",
            "1. Executive summary",
            "Purpose: Jarvis is a project-organizing layer with AI on top.",
            "Core pain: accountability.",
        ]
    )
    parsed = parse_vtt_or_srt(raw_vtt)
    assert len(parsed.turns) == 4
    assert parsed.turns[0].start_time == "00:00:14"
    assert parsed.turns[0].text == "Yeah, OK, because I."
    assert parsed.turns[0].speaker is None
    assert parsed.turns[-1].start_time == "01:02:29"
    assert parsed.turns[-1].text == "Do you have any follow-up thoughts? Yeah, later."
    assert "Findings and Analysis" not in parsed.turns[-1].text
    assert "Purpose:" not in parsed.text


def test_load_canonical_source_content_single_line_vtt_docx_e2e(tmp_path: Path) -> None:
    """Verify load_canonical_source_content loads all single-line WebVTT cues from a .docx file."""
    vtt_docx = _build_minimal_docx(
        tmp_path / "jarvis_interview.docx",
        [
            "WEBVTT",
            "cue-1 00:00:14.009 --> 00:00:15.049 First spoken cue in the interview.",
            "cue-2 00:00:20.100 --> 00:00:22.400 Second spoken cue in the interview.",
            "cue-3 00:05:19.000 --> 00:05:25.000 Third spoken cue discussing UX discovery.",
            "cue-4 01:02:29.889 --> 01:02:33.569 Final spoken cue before wrap-up.",
            "Discovery Interview: Findings and Analysis",
            "Purpose: Summary section appended to the end of the Word document.",
        ],
    )
    meta: dict = {}
    source_text, input_hash, resolved_path = load_canonical_source_content(vtt_docx, meta)
    assert resolved_path == vtt_docx.resolve()
    assert len(input_hash) == 64
    assert "### [00:00:14]" in source_text
    assert "First spoken cue in the interview." in source_text
    assert "### [01:02:29]" in source_text
    assert "Final spoken cue before wrap-up." in source_text
    assert "Purpose: Summary section" not in source_text
    assert source_text.count("**Unknown:**") == 4
