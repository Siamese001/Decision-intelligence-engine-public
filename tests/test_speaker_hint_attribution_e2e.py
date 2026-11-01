"""
End-to-end verification for Wave 1:
- Appearance-order cluster_label_map (SPEAKER_01 -> Speaker 1, SPEAKER_00 -> Speaker 2)
  without index inversion across u0_live_artifacts, voiceprint_binding, and voiceprint_auto_enrollment.
- Contraction expansion ("he is old" <-> "he's old", "it's" <-> "it is") and 1-word distinctive
  quote hints ("implicitly") in quote_hint_turn_mapper, candidate_scorer, and speaker_hint_matcher.
- Post-recording `nee speaker-hint` reconciliation against *.utterances.json and 01_*.md.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from dual_pipeline.audio_ingestion_core.base import BaseAudioTranscriber
from dual_pipeline.audio_ingestion_core.voiceprint_binding import _extract_and_bind_voiceprints
from dual_pipeline.canonical_pipeline.u0_live_artifacts import _hydrate_speaker_artifacts
from dual_pipeline.canonical_pipeline.u0_speaker_hints import resolve_pre_hitl_speaker_hints
from dual_pipeline.cli.speaker_hint_handlers import handle_speaker_hint
from dual_pipeline.speaker_attribution_core.candidate_scorer import (
    _check_quote_hint_match,
    normalize_search_text,
)
from dual_pipeline.speaker_attribution_core.quote_hint_turn_mapper import (
    apply_quote_hints_to_turns,
    apply_quote_hints_to_utterances,
)
from dual_pipeline.speaker_attribution_core.voiceprint_auto_enrollment import auto_enroll_voiceprints


class _DummyTranscriber(BaseAudioTranscriber):
    def _live_transcribe(self, audio_file_path: str, metadata: Dict[str, Any] | None = None) -> str:
        return ""


def _build_debrief_utterance_fixtures() -> List[Dict[str, Any]]:
    """Representative 10/21/2026 debrief utterances where SPEAKER_01 spoke first (2.3s) and SPEAKER_00 spoke second (22.0s)."""
    return [
        {
            "utterance_id": "utt_0001",
            "speaker": "SPEAKER_01",
            "raw_speaker": "SPEAKER_01",
            "start_time_sec": 2.34,
            "end_time_sec": 8.21,
            "text": "Talking about. No, the point that I'm making, even implicitly, why is it calling to all the same things?",
        },
        {
            "utterance_id": "utt_0002",
            "speaker": "SPEAKER_00",
            "raw_speaker": "SPEAKER_00",
            "start_time_sec": 22.01,
            "end_time_sec": 35.50,
            "text": "He's on top of all of this, but it's also about making sure that we're coaching them along the way.",
        },
        {
            "utterance_id": "utt_0003",
            "speaker": "SPEAKER_01",
            "raw_speaker": "SPEAKER_01",
            "start_time_sec": 160.20,
            "end_time_sec": 175.40,
            "text": "I know, wait until we start changing things, right? He's old.",
        },
        {
            "utterance_id": "utt_0004",
            "speaker": "SPEAKER_01",
            "raw_speaker": "SPEAKER_01",
            "start_time_sec": 259.99,
            "end_time_sec": 265.36,
            "text": "It's just bizarre because like what does the data scientist do? They explain numbers.",
        },
        {
            "utterance_id": "utt_0005",
            "speaker": "SPEAKER_02",
            "raw_speaker": "SPEAKER_02",
            "start_time_sec": 401.08,
            "end_time_sec": 402.08,
            "text": "So we both do now.",
        },
    ]


def test_cluster_index_appearance_order_prevents_inversion(tmp_path: Path) -> None:
    """Verifies SPEAKER_01 (spoke first -> Speaker 1) and SPEAKER_00 (spoke second -> Speaker 2) never invert."""
    transcriber = _DummyTranscriber()
    utterances = _build_debrief_utterance_fixtures()
    metadata: Dict[str, Any] = {
        "project": "PROJECT_LENS",
        "audio_sha256": "deadbeef" * 8,
        "cluster_centroids": {
            "SPEAKER_00": [1.0, 0.0, 0.0, 0.0],
            "SPEAKER_01": [0.0, 1.0, 0.0, 0.0],
            "SPEAKER_02": [0.0, 0.0, 1.0, 0.0],
        },
    }

    formatted = transcriber._format_diarized_json({"segments": utterances}, metadata=metadata)
    assert "**Speaker 1** [00:00:02]:" in formatted
    assert "**Speaker 2** [00:00:22]:" in formatted
    assert metadata["cluster_label_map"] == {
        "SPEAKER_01": "Speaker 1",
        "SPEAKER_00": "Speaker 2",
        "SPEAKER_02": "Speaker 3",
    }

    # Pre-populate Speaker 1 -> Miles Erickson, Speaker 2 -> Amit Ayer, Speaker 3 -> Skyler Carlson
    metadata["speaker_map"] = {
        "Speaker 1": "Miles Erickson",
        "Speaker 2": "Amit Ayer",
        "Speaker 3": "Skyler Carlson",
    }
    _hydrate_speaker_artifacts(metadata, [tmp_path], slug="debrief_skyler_partner_advisory")

    # SPEAKER_00 MUST map to Amit Ayer (Speaker 2), NOT Miles Erickson (Speaker 1)!
    assert metadata["speaker_map"]["SPEAKER_00"] == "Amit Ayer"
    assert metadata["speaker_map"]["SPEAKER_01"] == "Miles Erickson"
    assert metadata["speaker_map"]["SPEAKER_02"] == "Skyler Carlson"

    # Verify voiceprint auto-enrollment binds SPEAKER_00's centroid ([1,0,0,0]) to Amit Ayer
    enrolled_records: List[Dict[str, Any]] = []

    class _FakeVoiceprintStore:
        def enroll_voiceprint(self, **kwargs: Any) -> Dict[str, Any]:
            enrolled_records.append(kwargs)
            return {"action": "ENROLLED"}

    auto_enroll_voiceprints(
        metadata=metadata,
        speaker_map=metadata["speaker_map"],
        voiceprint_store=_FakeVoiceprintStore(),
    )
    by_speaker = {r["speaker_name"]: r["embedding"] for r in enrolled_records}
    assert by_speaker["Amit Ayer"] == [1.0, 0.0, 0.0, 0.0]
    assert by_speaker["Miles Erickson"] == [0.0, 1.0, 0.0, 0.0]
    assert by_speaker["Skyler Carlson"] == [0.0, 0.0, 1.0, 0.0]


def test_all_three_miles_erickson_quote_hints_match_turns_and_utterances(tmp_path: Path) -> None:
    """Verifies 'implicitly', 'he is old' -> 'He's old.', and 'what does the data scientist do' all match."""
    utterances = _build_debrief_utterance_fixtures()
    hints = [
        {"speaker_name": "Miles Erickson", "quote": "implicitly", "status": "QUEUED"},
        {"speaker_name": "Miles Erickson", "quote": "he is old", "status": "QUEUED"},
        {
            "speaker_name": "Miles Erickson",
            "quote": "It's just bizarre because like what does the data scientist do they explain numbers",
            "status": "QUEUED",
        },
    ]

    # 1. Verify contraction normalization
    assert "he is old" in normalize_search_text("I know, wait until we start changing things, right? He's old.")
    assert "it is just bizarre" in normalize_search_text(
        "It's just bizarre because like what does the data scientist do? They explain numbers."
    )

    # 2. Verify candidate_scorer._check_quote_hint_match matches each hint individually
    for h in hints:
        matched_q = _check_quote_hint_match(
            [h],
            spk_id="Speaker 1",
            name="Miles Erickson",
            full_text=" ".join(u["text"] for u in utterances),
            turns=[u["text"] for u in utterances if u["raw_speaker"] == "SPEAKER_01"],
        )
        assert matched_q == h["quote"], f"Hint failed to match in candidate_scorer: {h}"

    # 3. Verify apply_quote_hints_to_turns relabels all 3 turns even if previously attributed to Skyler Carlson
    wrong_transcript = (
        "**Skyler Carlson** [00:00:02]: Talking about. No, the point that I'm making, even implicitly, why is it calling to all the same things?\n\n"
        "**Amit Ayer** [00:00:22]: He's on top of all of this, but it's also about making sure that we're coaching them along the way.\n\n"
        "**Skyler Carlson** [00:02:40]: I know, wait until we start changing things, right? He's old.\n\n"
        "**Skyler Carlson** [00:04:19]: It's just bizarre because like what does the data scientist do? They explain numbers.\n"
    )
    corrected_transcript, reassignments = apply_quote_hints_to_turns(wrong_transcript, hints)
    assert len(reassignments) == 3
    assert "**Miles Erickson** [00:00:02]:" in corrected_transcript
    assert "**Miles Erickson** [00:02:40]:" in corrected_transcript
    assert "**Miles Erickson** [00:04:19]:" in corrected_transcript
    assert "**Amit Ayer** [00:00:22]:" in corrected_transcript

    # 4. Verify resolve_pre_hitl_speaker_hints overrides pre-populated speaker_map
    utt_path = tmp_path / "sample.utterances.json"
    utt_path.write_text(json.dumps(utterances, indent=2), encoding="utf-8")
    meta: Dict[str, Any] = {
        "structured_utterances_path": str(utt_path),
        "speaker_hints": hints,
        "speaker_map": {"Speaker 1": "Skyler Carlson", "SPEAKER_01": "Skyler Carlson"},
        "raw_transcript": wrong_transcript,
    }
    cluster_map = resolve_pre_hitl_speaker_hints(meta)
    assert cluster_map["SPEAKER_01"] == "Miles Erickson"
    assert meta["speaker_map"]["SPEAKER_01"] == "Miles Erickson"
    assert meta["speaker_map"]["Speaker 1"] == "Miles Erickson"
    assert "**Miles Erickson** [00:02:40]:" in meta["raw_transcript"]


def test_post_recording_cli_speaker_hint_reconciles_utterances_and_markdown(tmp_path: Path) -> None:
    """Verifies `nee speaker-hint` updates *.utterances.json and 01_*.md when segments.jsonl is absent."""
    meeting_dir = tmp_path / "output" / "project_lens" / "10-21-2026" / "debrief_skyler_partner_advisory"
    cache_dir = tmp_path / "output" / "transcript_cache" / "project_lens" / "10-21-2026"
    meeting_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)

    utterances = _build_debrief_utterance_fixtures()
    utt_file = cache_dir / "00_10-21-2026_Debrief_master_abc123.utterances.json"
    seg_file = cache_dir / "00_10-21-2026_Debrief_master_abc123.segments.json"
    tc_file = cache_dir / "00_10-21-2026_Debrief_master_abc123.transcript_cache.json"
    utt_file.write_text(json.dumps(utterances, indent=2), encoding="utf-8")
    seg_file.write_text(json.dumps(utterances, indent=2), encoding="utf-8")
    tc_file.write_text(
        "**Speaker 1** [00:04:19]: It's just bizarre because like what does the data scientist do? They explain numbers.\n",
        encoding="utf-8",
    )

    md_01 = meeting_dir / "01_10-21-2026_Debrief_HITL_Resolved_Transcript.md"
    md_01.write_text(
        "# Debrief -- HITL-Resolved Transcript | 10/21/2026\n\n"
        "**Skyler Carlson** [00:04:19]: It's just bizarre because like what does the data scientist do? They explain numbers.\n",
        encoding="utf-8",
    )

    manifest = {
        "meeting": "debrief_skyler_partner_advisory",
        "project": "project_lens",
        "attendees": ["Amit Ayer", "Skyler Carlson", "Miles Erickson"],
    }
    (meeting_dir / "session_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    args = argparse.Namespace(
        meeting_dir=str(meeting_dir),
        speaker="Miles Erickson",
        quote="It's just bizarre because like what does the data scientist do they explain numbers",
        role="",
        org="",
        force=False,
        remove_attendee=None,
        runtime_root=str(tmp_path),
        json=True,
    )
    res = handle_speaker_hint(args)
    assert res["status"] == "PASS"
    assert res["action"] == "APPLIED"
    assert res["speaker_name"] == "Miles Erickson"
    assert "**Miles Erickson** [00:04:19]:" in md_01.read_text(encoding="utf-8")
    updated_utts = json.loads(utt_file.read_text(encoding="utf-8"))
    utt_4 = next(u for u in updated_utts if u["utterance_id"] == "utt_0004")
    assert utt_4["speaker"] == "Miles Erickson"
    assert utt_4["raw_speaker"] == "SPEAKER_01"
