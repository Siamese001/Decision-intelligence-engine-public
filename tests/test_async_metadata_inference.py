from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.transcript_metadata_helper import scaffold_meeting_metadata


@patch("dual_pipeline.transcript_metadata_helper._read_sample_and_detect_notes")
@patch("dual_pipeline.audio_ingestion.slice_audio_chunk")
@patch("dual_pipeline.audio_ingestion.LocalMLXAudioTranscriber")
@patch("dual_pipeline.l1_google_adapter.L1GoogleMetadataAdapter")
def test_async_metadata_inference(mock_adapter_cls, mock_transcriber_cls, mock_slice, mock_read):
    # Mocking read_sample to return empty string (as audio files do)
    mock_read.return_value = ("", False)

    # Mock the transcriber
    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = "Hello, this is a test meeting about project nexus."
    mock_transcriber_cls.return_value = mock_transcriber

    # Mock the adapter
    mock_adapter = MagicMock()
    mock_adapter.extract_header_metadata.return_value = {
        "meeting_name": "Inferred Meeting",
        "project_name": "project_nexus",
        "attendee_roster": ["Alice", "Bob"],
        "date": "10/02/2026"
    }
    mock_adapter_cls.return_value = mock_adapter

    # Execute scaffold
    meta = scaffold_meeting_metadata(
        input_path=Path("dummy.wav"),
        project=None,
        meeting_name=None,
        attendees=None,
        date_str=None
    )

    assert mock_transcriber.transcribe.called
    assert mock_adapter.extract_header_metadata.called
    assert meta["meeting_name"] == "Inferred Meeting"
    assert meta["project"] == "project_nexus"
    assert "10/02/2026" in str(meta["date_filename"]) or "10/02/2026" in str(meta["date"])
