"""Unit and regression tests for Historical Voiceprint Harvester & Post-HITL Auto-Enrollment (Wave 2)."""
import json
import os
import sqlite3
import struct
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pytest

from dual_pipeline.audio_ingestion_core.voiceprint_harvester import (
    extract_speaker_turns_from_transcript,
    harvest_voiceprints_from_meeting,
    parse_verified_speakers_from_transcript,
)
from dual_pipeline.audio_ingestion_core.voiceprint_store import VoiceprintStore
from dual_pipeline.speaker_attribution_core.attribution_engine import (
    AudioSpeakerAttributionEngine,
)


def _create_synthetic_wav(path: Path, duration_sec: float = 4.0, sample_rate: int = 16000) -> None:
    """Creates a hermetic synthetic 16kHz mono WAV file."""
    num_samples = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # Generate 440 Hz tone
        raw_bytes = bytearray()
        for i in range(num_samples):
            val = int(32767.0 * 0.5 * np.sin(2.0 * np.pi * 440.0 * (i / sample_rate)))
            raw_bytes.extend(struct.pack("<h", val))
        wf.writeframes(raw_bytes)


SAMPLE_OUTPUT_01 = """# Output 01
**Generated Filename:** `01_Sherpa_Demo_hitl_resolved_transcript.md`

## Participants

| Participant | Role | Transcript coverage |
|---|---|---|
| Alice Smith | Enterprise Architect | Discrete turns present |
| Bob Jones | Project Manager | Non-speaking attendee (present on call; verified zero vocal participation) |
| Charlie Brown | Tech Lead | Discrete turns present |

## Attribution Summary

| Attendee | Organization | Role | Attributed Turns | Confidence Tier | Anchor Cue / Attribution Grounding |
|---|---|---|---|---|---|
| Alice Smith | Apex Global Enterprises | Enterprise Architect | 12 turns | Confirmed via Operator HITL | BAA catalog integration architecture |
| Bob Jones | PartnerAdvisory | Project Manager | 0 turns | Confirmed Grounded (Silent) | Zero spoken participation in recorded window |
| Charlie Brown | PartnerAdvisory | Tech Lead | 5 turns | High | Infrastructure pipeline deployment remarks |
| Speaker 1 | Unknown | Placeholder | 2 turns | Low | Generic unmapped cluster |

## Verbatim Attributed Transcript

### [00:00:00]

**Alice Smith:** Welcome everybody to the architecture alignment session. Today we cover catalog integration.

### [00:00:15]

**Alice Smith:** Looking at the delivery plan, our goal is to streamline the certification flow.

### [00:00:30]

**Charlie Brown:** On the infrastructure side, the pipelines are running cleanly in the sandbox environment.

### [00:00:45]

**Speaker 1:** Can someone check the link?

"""


class TestVoiceprintHarvesterUnit:
    """Wave 2 verification suite for historical voiceprint harvesting."""

    def test_parse_verified_speakers_excludes_silent_and_generic(self) -> None:
        """Verifies that silent attendees and generic placeholders are filtered out."""
        verified = parse_verified_speakers_from_transcript(SAMPLE_OUTPUT_01)
        # Alice Smith and Charlie Brown should be verified
        assert "Alice Smith" in verified
        assert "Charlie Brown" in verified
        # Bob Jones is silent (0 turns)
        assert "Bob Jones" not in verified
        # Speaker 1 is generic
        assert "Speaker 1" not in verified

    def test_extract_speaker_turns_from_transcript(self) -> None:
        """Verifies parsing of timestamped turns and duration calculation."""
        turns = extract_speaker_turns_from_transcript(SAMPLE_OUTPUT_01)
        assert len(turns) >= 3
        speakers = [t["speaker"] for t in turns]
        assert "Alice Smith" in speakers
        assert "Charlie Brown" in speakers
        assert "Speaker 1" not in speakers  # generic filtered by extractor

        alice_turn = next(t for t in turns if t["speaker"] == "Alice Smith")
        assert alice_turn["start"] == 0.0
        assert alice_turn["duration"] >= 2.0

    def test_harvest_meeting_end_to_end_and_idempotency(self) -> None:
        """Verifies end-to-end harvesting from a sandbox meeting directory."""
        with tempfile.TemporaryDirectory() as td:
            meeting_dir = Path(td) / "Sherpa_Demo_2026"
            meeting_dir.mkdir(parents=True, exist_ok=True)

            wav_file = meeting_dir / "00_master_audio.wav"
            _create_synthetic_wav(wav_file, duration_sec=6.0)

            transcript_file = meeting_dir / "01_Sherpa_Demo_hitl_resolved_transcript.md"
            transcript_file.write_text(SAMPLE_OUTPUT_01, encoding="utf-8")

            db_path = Path(td) / "voiceprints_test.sqlite"
            store = VoiceprintStore(db_path=db_path)

            # 1. Harvest with dry_run
            res_dry = harvest_voiceprints_from_meeting(
                meeting_dir=meeting_dir,
                store=store,
                dry_run=True,
                project="project_beacon",
            )
            assert res_dry["status"] == "SUCCESS"
            assert res_dry["enrolled_count"] == 2
            assert all(e["action"] == "DRY_RUN_ENROLLED" for e in res_dry["enrolled"])

            # Verify dry-run did not mutate DB
            assert len(store.get_enrolled_speakers()) == 0

            # 2. Live harvest
            res_live = harvest_voiceprints_from_meeting(
                meeting_dir=meeting_dir,
                store=store,
                dry_run=False,
                project="project_beacon",
            )
            assert res_live["status"] == "SUCCESS"
            assert res_live["enrolled_count"] == 2
            assert all(e["action"] == "ENROLLED" for e in res_live["enrolled"])

            # Verify rows in DB
            enrolled = store.get_enrolled_speakers(project="project_beacon")
            assert len(enrolled) == 2
            names = {e["canonical_name"] for e in enrolled}
            assert names == {"Alice Smith", "Charlie Brown"}

            # Verify physical SQLite integrity
            ro_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = ro_conn.cursor()
            cur.execute("PRAGMA integrity_check;")
            assert cur.fetchone()[0] == "ok"
            ro_conn.close()

            # 3. Idempotent re-harvest of the same audio_sha256
            res_reharvest = harvest_voiceprints_from_meeting(
                meeting_dir=meeting_dir,
                store=store,
                dry_run=False,
                project="project_beacon",
            )
            assert res_reharvest["status"] == "SUCCESS"
            assert all(e["action"] == "ALREADY_ENROLLED" for e in res_reharvest["enrolled"])
            assert len(store.get_enrolled_speakers(project="project_beacon")) == 2

            # 4. Invariant: 01_* file was NOT modified (read-only guarantee)
            assert transcript_file.read_text(encoding="utf-8") == SAMPLE_OUTPUT_01

    def test_post_hitl_auto_enrollment(self) -> None:
        """Verifies that AudioSpeakerAttributionEngine auto-enrolls resolved clusters."""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "post_hitl_test.sqlite"
            store = VoiceprintStore(db_path=db_path)
            engine = AudioSpeakerAttributionEngine(voiceprint_store=store)

            # Generate synthetic cluster centroid
            vec = np.random.randn(256).astype(np.float32)
            vec /= np.linalg.norm(vec)

            metadata: Dict[str, Any] = {
                "project": "project_beacon",
                "audio_sha256": "sha256_mock_meeting_123",
                "date_filename": "09-18-2026",
                "cluster_centroids": {
                    "SPEAKER_00": vec.tolist(),
                },
                "attendees": [
                    {"name": "David Childers", "org": "Apex Global Enterprises"},
                ],
                "speaker_map": {
                    "SPEAKER_00": "David Childers",
                },
            }

            transcript = (
                "## Transcript\n\n"
                "**David Childers:** This is David Childers presenting the Jira plan.\n"
            )

            res = engine.analyze_and_resolve(
                transcript=transcript,
                metadata=metadata,
                autonomous_if_decisive=True,
            )

            assert res.speaker_map.get("SPEAKER_00") == "David Childers"

            # Verify auto-enrollment into VoiceprintStore
            enrolled = store.get_enrolled_speakers(project="project_beacon")
            assert len(enrolled) == 1
            assert enrolled[0]["canonical_name"] == "David Childers"
            assert enrolled[0]["is_verified"] is True

            samples = store.get_enrolled_samples(project="project_beacon", canonical_name="David Childers")
            assert len(samples) == 1
            assert samples[0]["audio_sha256"] == "sha256_mock_meeting_123"
