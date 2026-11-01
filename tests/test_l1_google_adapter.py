"""Unit tests for Stage L1 Google metadata adapter and format validators."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from dual_pipeline.l1_feedback_loop import resolve_l1_metadata_adapter
from dual_pipeline.l1_google_adapter import (
    DATE_REGEX,
    PROJECT_CODE_REGEX,
    TIME_REGEX,
    L1GoogleMetadataAdapter,
    validate_l1_header_metadata,
)


def test_regex_invariants():
    """Verify strict regex patterns for date, project code, and time."""
    assert DATE_REGEX.match("09/25/2026")
    assert DATE_REGEX.match("12/01/2025")
    assert not DATE_REGEX.match("2026-09-25")
    assert not DATE_REGEX.match("9/25/2026")
    assert not DATE_REGEX.match("09/32/2026")

    assert PROJECT_CODE_REGEX.match("project_nexus")
    assert PROJECT_CODE_REGEX.match("lens-analytics")
    assert not PROJECT_CODE_REGEX.match("bad/path/injection")
    assert not PROJECT_CODE_REGEX.match("a")

    assert TIME_REGEX.match("10:00 AM")
    assert TIME_REGEX.match("2:30 PM")
    assert TIME_REGEX.match("14:00")
    assert not TIME_REGEX.match("invalid_time")


def test_validate_l1_header_metadata_valid():
    """Verify validation passes for compliant metadata."""
    valid_data = {
        "meeting_name": "Executive Strategy Alignment",
        "date": "09/25/2026",
        "time": "10:00 AM",
        "project_name": "project_nexus",
        "attendee_roster": ["Alex Brennan (Engineering)", "Amit Ayer (Product)"],
    }
    is_valid, errors = validate_l1_header_metadata(valid_data)
    assert is_valid is True
    assert errors == []


def test_validate_l1_header_metadata_invalid_fields():
    """Verify validation errors are caught for malformed fields."""
    invalid_data = {
        "meeting_name": "Bad Meeting",
        "date": "2026-09-25",  # Invalid format (needs MM/DD/YYYY)
        "time": "morning",  # Invalid time
        "project_name": "invalid/project!name",
        "attendee_roster": "not a list",
    }
    is_valid, errors = validate_l1_header_metadata(invalid_data)
    assert is_valid is False
    assert len(errors) == 4


def test_validate_l1_header_metadata_non_dict():
    """Verify non-dict input is rejected."""
    is_valid, errors = validate_l1_header_metadata(["not", "a", "dict"])
    assert is_valid is False
    assert "Metadata must be a dictionary" in errors[0]


def test_l1_google_metadata_adapter_extraction():
    """Verify L1GoogleMetadataAdapter extracts and validates metadata from transcript."""
    adapter = L1GoogleMetadataAdapter(model="gemini-3.8-flash")

    raw_json = json.dumps({
        "meeting_name": "Sprint Retrospective",
        "date": "09/25/2026",
        "time": "11:00 AM",
        "project_name": "project_nexus",
        "attendee_roster": ["Alice", "Bob"],
    })

    with patch(
        "dual_pipeline.l1_google_adapter.call_gemini_generate_content",
        return_value=raw_json,
    ) as mock_call:
        res = adapter.extract_header_metadata("Transcript snippet...")
        assert res["meeting_name"] == "Sprint Retrospective"
        assert res["date"] == "09/25/2026"
        assert mock_call.call_count == 1


def test_l1_google_metadata_adapter_date_sanitization():
    """Verify L1GoogleMetadataAdapter auto-sanitizes dashed dates to MM/DD/YYYY."""
    adapter = L1GoogleMetadataAdapter()

    # Case 1: MM-DD-YYYY
    raw_json1 = json.dumps({
        "meeting_name": "Standup",
        "date": "09-25-2026",
        "time": "09:00 AM",
        "project_name": "project_core",
    })
    with patch(
        "dual_pipeline.l1_google_adapter.call_gemini_generate_content",
        return_value=raw_json1,
    ):
        res1 = adapter.extract_header_metadata("Snippet...")
        assert res1["date"] == "09/25/2026"

    # Case 2: YYYY-MM-DD
    raw_json2 = json.dumps({
        "meeting_name": "Standup",
        "date": "2026-09-25",
        "time": "09:00 AM",
        "project_name": "project_core",
    })
    with patch(
        "dual_pipeline.l1_google_adapter.call_gemini_generate_content",
        return_value=raw_json2,
    ):
        res2 = adapter.extract_header_metadata("Snippet...")
        assert res2["date"] == "09/25/2026"


def test_l1_google_metadata_adapter_error_handling():
    """Verify adapter handles API exceptions gracefully returning empty dict."""
    adapter = L1GoogleMetadataAdapter()

    with patch(
        "dual_pipeline.l1_google_adapter.call_gemini_generate_content",
        side_effect=RuntimeError("Google API 503 Service Unavailable"),
    ):
        res = adapter.extract_header_metadata("Snippet...")
        assert res == {}


def test_resolve_l1_metadata_adapter_factory():
    """Verify resolve_l1_metadata_adapter instantiates L1GoogleMetadataAdapter."""
    adapter = resolve_l1_metadata_adapter(provider="google")
    assert isinstance(adapter, L1GoogleMetadataAdapter)

    adapter2 = resolve_l1_metadata_adapter(provider="gemini")
    assert isinstance(adapter2, L1GoogleMetadataAdapter)

    with pytest.raises(ValueError, match="Unsupported L1 metadata adapter provider"):
        resolve_l1_metadata_adapter(provider="unsupported_provider")
