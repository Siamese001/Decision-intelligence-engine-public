"""End-to-end integration tests for multi-format direct U0 ingestion.

Verifies that live transcripts, audio files, WebVTT subtitles, CoPilot JSON,
and unformatted dialogue text ingest directly without customized Python scripts.
"""

from __future__ import annotations

import json
import tempfile
import wave
from pathlib import Path

from dual_pipeline.cli.handlers import _ingest_meeting
from dual_pipeline.cli.parser import build_parser
from dual_pipeline.transcript_models import CanonicalSignalProjectionV57
from dual_pipeline.transcript_pipeline import RawTranscriptPipeline


def _create_minimal_wav(path: Path) -> None:
    """Create a minimal 1-second valid WAV audio file."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 16000)


def test_direct_u0_ingestion_webvtt_format() -> None:
    """Verify WebVTT file direct ingestion through U0."""
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        vtt_file = temp_dir / "copilot_meeting.vtt"
        vtt_content = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:05.000\n"
            "<v Drew Oakley>We need to align on the candidate screening criteria.</v>\n\n"
            "00:00:05.500 --> 00:00:10.000\n"
            "<v Amit Ayer>Kannan demonstrated deep knowledge in distributed graph databases.</v>\n"
        )
        vtt_file.write_text(vtt_content, encoding="utf-8")

        out_dir = temp_dir / "project_nea" / "10-02-2026"
        out_dir.mkdir(parents=True, exist_ok=True)
        pipeline = RawTranscriptPipeline(provider_type="offline")
        result = pipeline.run(
            input_path=str(vtt_file),
            metadata={
                "meeting_name": "Kannan_Debrief_VTT",
                "date": "09/14/2026",
                "time": "03:00 PM - 03:30 PM ET",
                "classification": "confidential",
            },
            output_dir=str(out_dir),
            mutate_graph=False,
        )

        assert Path(result["01_transcript"]).exists()
        assert Path(result["02_recap"]).exists()
        assert Path(result["03_graph"]).exists()

        f3 = json.loads(Path(result["03_graph"]).read_text(encoding="utf-8"))
        CanonicalSignalProjectionV57.model_validate(f3)

        target_dir = Path(result["output_dir"]) if "output_dir" in result else out_dir
        assert (target_dir / "qa_gates_report.json").exists()
        assert (target_dir / "final_release_decision.json").exists()
        assert (target_dir / "intake_package.json").exists()


def test_direct_u0_ingestion_copilot_json_format() -> None:
    """Verify CoPilot structured JSON export direct ingestion through U0."""
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        json_file = temp_dir / "copilot_export.json"
        json_content = [
            {"speaker": "Drew Oakley", "timestamp": "00:01:00", "text": "Let's review the interview notes."},
            {"speaker": "Amit Ayer", "timestamp": "00:01:30", "text": "Kannan is strong in systems engineering."},
        ]
        json_file.write_text(json.dumps(json_content), encoding="utf-8")

        out_dir = temp_dir / "project_nea" / "10-02-2026"
        out_dir.mkdir(parents=True, exist_ok=True)
        pipeline = RawTranscriptPipeline(provider_type="offline")
        result = pipeline.run(
            input_path=str(json_file),
            metadata={
                "meeting_name": "Kannan_Debrief_JSON",
                "date": "09/14/2026",
                "time": "03:00 PM - 03:30 PM ET",
                "classification": "confidential",
            },
            output_dir=str(out_dir),
            mutate_graph=False,
        )

        assert Path(result["01_transcript"]).exists()
        assert Path(result["02_recap"]).exists()
        assert Path(result["03_graph"]).exists()
        target_dir = Path(result["output_dir"]) if "output_dir" in result else out_dir
        assert (target_dir / "intake_package.json").exists()


def test_direct_u0_ingestion_audio_file() -> None:
    """Verify raw audio file (.wav) direct ingestion through U0."""
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        wav_file = temp_dir / "kannan_interview.wav"
        _create_minimal_wav(wav_file)

        out_dir = temp_dir / "project_nea" / "10-02-2026"
        out_dir.mkdir(parents=True, exist_ok=True)
        pipeline = RawTranscriptPipeline(provider_type="offline")
        result = pipeline.run(
            input_path=str(wav_file),
            metadata={
                "meeting_name": "Kannan_Interview_Audio",
                "date": "09/14/2026",
                "time": "03:00 PM - 03:30 PM ET",
                "classification": "confidential",
                "attendees": {"PartnerAdvisory": ["Drew Oakley", "Amit Ayer"]},
                "speaker_map": {"Speaker 1": "Drew Oakley", "Speaker 2": "Amit Ayer"},
            },
            output_dir=str(out_dir),
            mutate_graph=False,
        )

        assert Path(result["01_transcript"]).exists()
        assert Path(result["02_recap"]).exists()
        assert Path(result["03_graph"]).exists()
        target_dir = Path(result["output_dir"]) if "output_dir" in result else out_dir
        assert (target_dir / "intake_package.json").exists()


def test_direct_cli_ingest_meeting_with_pure_flags_no_metadata_file() -> None:
    """Verify direct CLI nee ingest-meeting invocation without any metadata JSON file."""
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        raw_txt = temp_dir / "raw_copilot_notes.txt"
        raw_txt.write_text(
            "Drew Oakley 10:15 AM\n"
            "We agreed to extend the offer.\n"
            "Amit Ayer 10:16 AM\n"
            "Offer terms confirmed.\n",
            encoding="utf-8",
        )

        out_dir = temp_dir / "project_nea" / "10-02-2026"
        out_dir.mkdir(parents=True, exist_ok=True)
        parser = build_parser()
        args = parser.parse_args([
            "ingest-meeting",
            "--input", str(raw_txt),
            "--project", "nea",
            "--meeting", "Offer_Review_CLI",
            "--date", "09/14/2026",
            "--time", "10:15 AM - 10:20 AM ET",
            "--classification", "confidential",
            "--output-dir", str(out_dir),
            "--provider", "offline",
        ])

        result = _ingest_meeting(args)
        assert result is not None
        assert Path(result["01_transcript"]).exists()
        assert Path(result["02_recap"]).exists()
        assert Path(result["03_graph"]).exists()
        target_dir = Path(result["output_dir"]) if "output_dir" in result else out_dir
        assert (target_dir / "intake_package.json").exists()


def test_direct_u0_ingestion_zero_metadata_auto_defaults() -> None:
    """Verify raw file ingestion with metadata=None auto-defaults all required fields in U0."""
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        raw_file = temp_dir / "Project_LENS_Phase_II_Catchup_10-02-2026.txt"
        raw_file.write_text(
            "# Project LENS Phase II Catchup 09/14/2026\n\n"
            "harper Hoffman 03:00 PM\n"
            "We need to review the turbine image classification accuracy.\n"
            "Amit Ayer 03:05 PM\n"
            "Understood, we are establishing the rule creation engine fix.\n",
            encoding="utf-8",
        )

        out_dir = temp_dir / "project_lens" / "10-02-2026"
        out_dir.mkdir(parents=True, exist_ok=True)
        pipeline = RawTranscriptPipeline(provider_type="offline")

        # Ingest directly with metadata=None!
        result = pipeline.run(
            input_path=str(raw_file),
            metadata=None,
            output_dir=str(out_dir),
            mutate_graph=False,
        )

        assert Path(result["01_transcript"]).exists()
        assert Path(result["02_recap"]).exists()
        assert Path(result["03_graph"]).exists()
        target_dir = Path(result["output_dir"]) if "output_dir" in result else out_dir
        assert (target_dir / "intake_package.json").exists()


def test_direct_u0_ingestion_raw_string_in_memory() -> None:
    """Verify in-memory raw text (live stream buffer) ingests directly without file on disk."""
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        raw_stream_text = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:05.000\n"
            "<v Drew Oakley>We are deploying the Decision Intelligence service.</v>\n\n"
            "00:00:05.500 --> 00:00:10.000\n"
            "<v Amit Ayer>Deployment verified and health checks are green.</v>\n"
        )

        out_dir = temp_dir / "project_nexus" / "10-02-2026"
        out_dir.mkdir(parents=True, exist_ok=True)
        pipeline = RawTranscriptPipeline(provider_type="offline")

        # Ingest directly with raw text in-memory and metadata=None!
        result = pipeline.run(
            input_path=raw_stream_text,
            metadata=None,
            output_dir=str(out_dir),
            mutate_graph=False,
        )

        assert Path(result["01_transcript"]).exists()
        assert Path(result["02_recap"]).exists()
        assert Path(result["03_graph"]).exists()
        target_dir = Path(result["output_dir"]) if "output_dir" in result else out_dir
        assert (target_dir / "intake_package.json").exists()

        assert (target_dir / "final_release_decision.json").exists()


import unittest


class DirectU0MultiFormatE2ETests(unittest.TestCase):
    def test_webvtt_format(self) -> None:
        test_direct_u0_ingestion_webvtt_format()

    def test_copilot_json_format(self) -> None:
        test_direct_u0_ingestion_copilot_json_format()

    def test_audio_file(self) -> None:
        test_direct_u0_ingestion_audio_file()

    def test_cli_ingest_meeting_flags(self) -> None:
        test_direct_cli_ingest_meeting_with_pure_flags_no_metadata_file()

    def test_zero_metadata_auto_defaults(self) -> None:
        test_direct_u0_ingestion_zero_metadata_auto_defaults()

    def test_raw_string_in_memory(self) -> None:
        test_direct_u0_ingestion_raw_string_in_memory()

