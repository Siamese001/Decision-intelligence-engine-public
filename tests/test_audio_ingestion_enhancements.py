"""
tests/test_audio_ingestion_enhancements.py

Tests for enhanced audio ingestion and metadata auto-scaffolding.
"""


from dual_pipeline.audio_ingestion import ensure_ffmpeg
from dual_pipeline.transcript_metadata_helper import (
    extract_date_from_text,
    scaffold_meeting_metadata,
)
from dual_pipeline.transcript_pipeline import RawTranscriptPipeline


def test_ensure_ffmpeg():
    """Verify ensure_ffmpeg returns a valid binary path or command."""
    exe = ensure_ffmpeg()
    assert exe is not None
    assert "ffmpeg" in exe.lower()


def test_extract_date_from_text():
    """Verify strict date parsing from strings and filenames."""
    slash, hyphen = extract_date_from_text("Reid - 9.14.2026.m4a")
    assert slash == "09/14/2026"
    assert hyphen == "10-02-2026"

    slash2, hyphen2 = extract_date_from_text("meeting_2026-09-14_sync.mp3")
    assert slash2 == "09/14/2026"
    assert hyphen2 == "10-02-2026"


def test_scaffold_meeting_metadata():
    """Verify scaffold_meeting_metadata produces governed metadata."""
    meta = scaffold_meeting_metadata(
        input_path="/Users/test/Desktop/Reid - 9.14.2026.m4a",
        project="project_nexus",
        attendees="Reid Schenk",
    )
    assert meta["date"] == "09/14/2026"
    assert meta["date_filename"] == "10-02-2026"
    assert meta["project"] == "project_nexus"
    assert meta["classification"] == "confidential"
    assert "reid" in meta["meeting_name"].lower()
    assert len(meta["roster"]) >= 1


def test_audio_pipeline_with_scaffolded_metadata(tmp_path):
    """Verify audio ingestion works directly without an external metadata JSON."""
    fake_audio = tmp_path / "Reid - 9.14.2026.m4a"
    fake_audio.write_text("dummy audio stream", encoding="utf-8")

    meta = scaffold_meeting_metadata(
        input_path=fake_audio,
        project="project_nexus",
        attendees=["Reid Schenk", "Drew Oakley"],
    )
    meta["attendees"] = ["Reid Schenk", "Drew Oakley"]
    meta["roster"] = [
        {"name": "Reid Schenk", "org": "PartnerAdvisory", "role": "Lead Consultant"},
        {"name": "Drew Oakley", "org": "Apex Global", "role": "Technical Lead"},
    ]
    meta["speaker_map"] = {"Speaker 1": "Reid Schenk", "Speaker 2": "Drew Oakley"}
    meta["allow_duplicate"] = True
    meta["collision_override_rationale"] = "Unit test isolated execution"

    out_dir = tmp_path / "project_nexus" / "10-02-2026"
    pipeline = RawTranscriptPipeline(provider_type="offline")
    result = pipeline.run(str(fake_audio), meta, str(out_dir))

    assert "01_transcript" in result
    assert "02_recap" in result
    assert "03_graph" in result
    assert out_dir.exists()
