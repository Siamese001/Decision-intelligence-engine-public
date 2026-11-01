"""Unit tests for CLI provider inferencing and fallback prevention."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from dual_pipeline.cli.handlers import _ingest_meeting
from dual_pipeline.cli.parser import build_parser
from dual_pipeline.transcript_cli import build_parser as build_transcript_parser


def test_cli_parser_provider_default_is_none():
    parser = build_parser()
    args = parser.parse_args(["ingest-meeting", "--input", "test.txt"])
    assert args.provider is None


def test_transcript_cli_parser_provider_default_is_none():
    parser = build_transcript_parser()
    args = parser.parse_args(["--input", "test.txt"])
    assert args.provider is None


@patch("dual_pipeline.cli.handlers.RawTranscriptPipeline")
def test_handlers_infers_llm_when_model_specified(mock_pipeline_cls):
    mock_instance = MagicMock()
    mock_pipeline_cls.return_value = mock_instance
    mock_instance.run.return_value = {"status": "SUCCESS"}

    parser = build_parser()
    args = parser.parse_args([
        "ingest-meeting",
        "--input", "test.txt",
        "--project", "nea",
        "--meeting", "Test_Meeting",
        "--date", "09/14/2026",
        "--time", "03:00 PM - 03:30 PM ET",
        "--model", "gpt-5.6-sol",
    ])

    _ingest_meeting(args)

    mock_pipeline_cls.assert_called_once()
    _, kwargs = mock_pipeline_cls.call_args
    assert kwargs.get("provider_type") == "llm"


@patch("dual_pipeline.cli.handlers.RawTranscriptPipeline")
def test_handlers_defaults_to_offline_when_no_model(mock_pipeline_cls):
    mock_instance = MagicMock()
    mock_pipeline_cls.return_value = mock_instance
    mock_instance.run.return_value = {"status": "SUCCESS"}

    parser = build_parser()
    args = parser.parse_args([
        "ingest-meeting",
        "--input", "test.txt",
        "--project", "nea",
        "--meeting", "Test_Meeting",
        "--date", "09/14/2026",
        "--time", "03:00 PM - 03:30 PM ET",
    ])

    _ingest_meeting(args)

    mock_pipeline_cls.assert_called_once()
    _, kwargs = mock_pipeline_cls.call_args
    assert kwargs.get("provider_type") == "offline"


@patch("dual_pipeline.cli.handlers.RawTranscriptPipeline")
def test_handlers_respects_explicit_offline_even_with_model(mock_pipeline_cls):
    mock_instance = MagicMock()
    mock_pipeline_cls.return_value = mock_instance
    mock_instance.run.return_value = {"status": "SUCCESS"}

    parser = build_parser()
    args = parser.parse_args([
        "ingest-meeting",
        "--input", "test.txt",
        "--project", "nea",
        "--meeting", "Test_Meeting",
        "--date", "09/14/2026",
        "--time", "03:00 PM - 03:30 PM ET",
        "--model", "gpt-5.6-sol",
        "--provider", "offline",
    ])

    _ingest_meeting(args)

    mock_pipeline_cls.assert_called_once()
    _, kwargs = mock_pipeline_cls.call_args
    assert kwargs.get("provider_type") == "offline"
