"""Unit tests for multi-format raw transcript normalization and U0 ingestion defaulting."""

from __future__ import annotations

import json

from dual_pipeline.transcript_pipeline_core.normalizer import normalize_raw_input_text
from dual_pipeline.transcript_stages_core.u0_ingestion import (
    U0IngestionBoundary,
    _extract_flat_attendees,
)


def test_normalize_webvtt_format() -> None:
    vtt_content = (
        "WEBVTT\n\n"
        "1\n"
        "00:00:01.500 --> 00:00:04.200\n"
        "<v Drew Oakley>We need to align on the candidate screening criteria.</v>\n\n"
        "2\n"
        "00:00:04.500 --> 00:00:08.000\n"
        "<v Amit Ayer>Agreed, Kannan is strong in data architecture.</v>\n"
    )
    normalized = normalize_raw_input_text(vtt_content, input_ext=".vtt")
    assert hasattr(normalized, 'text')
    assert "### [00:00:01]" in normalized.text
    assert "**Drew Oakley:** We need to align on the candidate screening criteria." in normalized.text
    assert "### [00:00:04]" in normalized.text
    assert "**Amit Ayer:** Agreed, Kannan is strong in data architecture." in normalized.text


def test_normalize_copilot_json_format() -> None:
    json_data = [
        {"speaker": "Drew Oakley", "timestamp": "00:02:15", "text": "What is the status of the SOW?"},
        {"speaker": "Amit Ayer", "timestamp": "00:02:45", "text": "The SOW was delivered to Stephan."},
    ]
    normalized = normalize_raw_input_text(json.dumps(json_data), input_ext=".json")
    assert hasattr(normalized, 'text')
    assert "### [00:02:15]" in normalized.text
    assert "**Drew Oakley:** What is the status of the SOW?" in normalized.text
    assert "### [00:02:45]" in normalized.text
    assert "**Amit Ayer:** The SOW was delivered to Stephan." in normalized.text


def test_normalize_teams_ampm_text() -> None:
    raw_teams = (
        "Drew Oakley 10:15 AM\n"
        "Good morning team, let's start the review.\n"
        "Amit Ayer 10:16 AM\n"
        "Morning Dan, I'm ready to walk through the architecture.\n"
    )
    normalized = normalize_raw_input_text(raw_teams, input_ext=".txt")
    assert hasattr(normalized, 'text')
    assert "### [10:15:00]" in normalized.text
    assert "**Drew Oakley:** Good morning team, let's start the review." in normalized.text
    assert "### [10:16:00]" in normalized.text
    assert "**Amit Ayer:** Morning Dan, I'm ready to walk through the architecture." in normalized.text


def test_normalize_bracketed_timestamp_text() -> None:
    raw_bracketed = (
        "[05:30] Drew Oakley: We should finalize the recommendation.\n"
        "[06:10] Amit Ayer: Recommendation is to advance the candidate.\n"
    )
    normalized = normalize_raw_input_text(raw_bracketed, input_ext=".txt")
    assert hasattr(normalized, 'text')
    assert "### [00:05:30]" in normalized.text
    assert "**Drew Oakley:** We should finalize the recommendation." in normalized.text
    assert "### [00:06:10]" in normalized.text
    assert "**Amit Ayer:** Recommendation is to advance the candidate." in normalized.text


def test_u0_ingestion_metadata_auto_defaulting_empty_dict() -> None:
    u0 = U0IngestionBoundary()
    raw_transcript = (
        "# LENS Architecture Sync 09/14/2026\n\n"
        "**harper Hoffman:** [03:12] We reviewed the rule creation engine.\n"
        "**Amit Ayer:** [04:00] We will deploy the hotfix today.\n"
    )
    context = u0.execute(raw_transcript, metadata={"time": "09:00 AM"})
    meta = context["metadata"]

    assert meta["classification"] == "confidential"
    assert meta["date"] in ("09/14/2026", "10/02/2026")
    assert meta["date_filename"] == "10-02-2026"
    assert "LENS_Architecture_Sync" in meta["meeting_name"]
    flat_attendees = _extract_flat_attendees(meta["attendees"])
    assert any("harper Hoffman" in a for a in flat_attendees)
    assert any("Amit Ayer" in a for a in flat_attendees)
    assert context["diarization_status"] == "NATIVE"


def test_u0_ingestion_date_normalization() -> None:
    u0 = U0IngestionBoundary()
    raw_transcript = "**Amit Ayer:** [01:00] Status update."
    context = u0.execute(
        raw_transcript,
        metadata={
            "meeting_name": "Test_Sync",
            "date": "10-02-2026",
            "time": "02:00 PM - 02:30 PM ET",
        },
    )
    meta = context["metadata"]
    assert meta["date"] in ("09/14/2026", "10/02/2026")
    assert meta["date_filename"] == "10-02-2026"
    assert meta["classification"] == "confidential"
