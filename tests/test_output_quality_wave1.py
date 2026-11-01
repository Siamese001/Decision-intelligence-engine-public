"""
tests/test_output_quality_wave1.py

Wave 1 Acceptance Suite: Source-bound transcript formatting, turn parity,
idempotent speaker attribution, Mandy opener integrity, and modality provenance.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Dict

import pytest

from dual_pipeline.manifest import IngestionMeetingManifest
from dual_pipeline.manifest_runner.transcript_formatter import (
    format_output_01,
    parse_raw_transcript,
)
from dual_pipeline.transcript_pipeline_core.format_parsers import parse_vtt_or_srt
from dual_pipeline.transcript_pipeline_core.source_modality import is_text_modality
from dual_pipeline.transcript_qa_core.gate1 import run_qa_gate_1
from dual_pipeline.transcript_qa_core.gate1_helpers import (
    _extract_all_turns,
    check_transcript_formatting_and_provenance,
)
from dual_pipeline.transcript_stages_core.attribution_builder import AttributionSummaryBuilder
from dual_pipeline.transcript_stages_core.forensic_normalizer import ForensicNormalizer
from dual_pipeline.transcript_stages_core.l1_attribution import (
    apply_speaker_map,
    assemble_native_hitl_transcript,
)
from dual_pipeline.transcript_stages_core.lexical_normalizer import (
    format_ambiguity_lock_register,
)


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_mandy_vtt_opener_and_discrete_replies():
    """Verify Mandy VTT opener integrity [00:00:04] Amit Ayer and discrete short replies."""
    vtt_path = REPO_ROOT / "inputs" / "transcripts" / "Mandy LENS Catch-up _ Amit Ayer (PartnerAdvisory).vtt"
    assert vtt_path.exists(), f"Missing fixture: {vtt_path}"
    raw_vtt = vtt_path.read_text(encoding="utf-8")

    parsed = parse_vtt_or_srt(raw_vtt)
    assert len(parsed.turns) > 0

    first_turn = parsed.turns[0]
    assert first_turn.start_time == "00:00:04"
    assert first_turn.speaker == "Amit Ayer"
    assert "But I'd love to take a look at it again" in first_turn.text

    # Header in parsed.text must be standard unbolded markdown heading ### [00:00:04]
    assert "### [00:00:04]" in parsed.text
    assert "**###" not in parsed.text
    assert "### [00:**" not in parsed.text

    # Verify short discrete reply "Yep." at 00:00:22 remains an independent turn
    yep_turns = [t for t in parsed.turns if t.text.strip() == "Yep."]
    assert len(yep_turns) >= 1
    assert any(t.speaker == "Amit Ayer" for t in yep_turns)

    # Execute native stage flow and verify no asterisk duplication on speaker labels
    metadata = {
        "meeting_name": "Mandy LENS Catch-up",
        "date": "09/24/2026",
        "platform": "Microsoft Teams",
        "source_modality": "VTT",
        "source_type": "VTT",
        "attendees": ["Amit Ayer", "harper Hoffman"],
        "speaker_map": {"Hopmann, Mandy": "harper Hoffman", "Amit Ayer": "Amit Ayer"},
    }
    speaker_map = metadata["speaker_map"]
    mapped_text = apply_speaker_map(parsed.text, speaker_map)
    # Repeated application must remain idempotent
    mapped_twice = apply_speaker_map(mapped_text, speaker_map)
    assert mapped_twice == mapped_text
    assert not re.search(r"\*\*[^*:\n]+?:\*\*\*\*+", mapped_text)

    assembled = assemble_native_hitl_transcript(mapped_text, metadata, speaker_map)
    # Re-assembling must not duplicate headers
    assembled_twice = assemble_native_hitl_transcript(assembled, metadata, speaker_map)
    assert assembled_twice.count("# Mandy LENS Catch-up -- HITL-Resolved Transcript") == 1

    # Gate 1 QA verification
    qa_res = run_qa_gate_1(assembled, source_text=raw_vtt, metadata=metadata)
    assert qa_res["gates"]["Gate_1_11_Formatting_And_Provenance"]["passed"] is True


def test_conduit_136_turn_parity_and_tuple_order():
    """Verify Conduit 136-turn sequence, speaker, and utterance tuple parity."""
    conduit_path = REPO_ROOT / "inputs" / "transcripts" / "pd_conduit_831_raw_transcript.md"
    assert conduit_path.exists(), f"Missing fixture: {conduit_path}"
    raw_md = conduit_path.read_text(encoding="utf-8")

    source_turns = _extract_all_turns(raw_md)
    assert len(source_turns) == 136, f"Expected exactly 136 turns, found {len(source_turns)}"

    # Check first and last turns
    assert source_turns[0][0] == "00:00:57"
    assert source_turns[0][1] == "Amit Ayer"
    assert "Hey, michael, how are you doing?" in source_turns[0][2]

    # Verify idempotence of apply_speaker_map on Conduit
    speaker_map = {
        "Amit Ayer": "Amit Ayer",
        "Michael Maione": "Michael Maione",
        "Siara Montgelas": "Siara Montgelas",
        "Mike Homorody": "Mike Homorody",
    }
    mapped_conduit = apply_speaker_map(raw_md, speaker_map)
    assert not re.search(r"\*\*[^*:\n]+?:\*\*\*\*+", mapped_conduit)

    processed_turns = _extract_all_turns(mapped_conduit)
    assert len(processed_turns) == 136

    # Verify exact tuple parity across all 136 turns
    for idx, (s_turn, p_turn) in enumerate(zip(source_turns, processed_turns)):
        assert s_turn[0] == p_turn[0], f"Timestamp mismatch at turn {idx}: {s_turn[0]} vs {p_turn[0]}"
        assert s_turn[1] == p_turn[1], f"Speaker mismatch at turn {idx}: {s_turn[1]} vs {p_turn[1]}"
        assert s_turn[2] == p_turn[2], f"Text mismatch at turn {idx}"


def test_neet_283_turn_parity_and_no_dropped_replies():
    """Verify APEX_TRANS September 11 source 283 spoken dialogue turns parity without dropped replies."""
    manifest_path = REPO_ROOT / "manifests" / "apex_trans" / "09-11-2026.manifest.json"
    assert manifest_path.exists(), f"Missing manifest: {manifest_path}"
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))

    source_path = Path(manifest_data["source_transcript_path"])
    assert source_path.exists(), f"Missing source transcript: {source_path}"
    raw_text = source_path.read_text(encoding="utf-8")

    turns = parse_raw_transcript(
        raw_text,
        manifest_data["speaker_map"],
        manifest_data.get("term_replacements", {}),
    )
    assert len(turns) == 283, f"Expected exactly 283 APEX_TRANS turns, found {len(turns)}"

    manifest = IngestionMeetingManifest.from_dict(manifest_data)
    output_01 = format_output_01(manifest, turns)

    # Output 01 must contain all 283 spoken turns
    extracted_output_turns = _extract_all_turns(output_01)
    assert len(extracted_output_turns) == 283

    # Raw UTC header note must be suppressed on text source Output 01
    assert "Raw Source Header: September 11, 2026, 7:36PM UTC" not in output_01
    assert "UTC | Duration:" not in output_01

    # Gate 1.11 check
    formatting_ok, failures = check_transcript_formatting_and_provenance(output_01, manifest_data)
    assert formatting_ok is True, f"Gate 1.11 failed: {failures}"


def test_timed_audio_micro_turn_aggregation_and_word_conservation():
    """Verify audio micro-turn aggregation merges continuous speaker turns, conserves words, and respects intervening speakers."""
    audio_transcript = (
        "### [00:01:00]\n\n**Carlos Gomez:** We are reviewing the turbine sensors today.\n\n"
        "### [00:01:04]\n\n**Carlos Gomez:** And the telemetry looks good.\n\n"
        "### [00:01:08]\n\n**Amit Ayer:** Yes.\n\n"
        "### [00:01:10]\n\n**Carlos Gomez:** Let's proceed to the next agenda item."
    )

    turns_orig = _extract_all_turns(audio_transcript)
    words_orig = [w for _, _, txt in turns_orig for w in txt.split()]

    stitched = ForensicNormalizer._stitch_acoustic_micro_turns(audio_transcript)

    # Turns 1 and 2 (consecutive Carlos Gomez) must be aggregated
    assert "We are reviewing the turbine sensors today. And the telemetry looks good." in stitched

    # Turn 3 (Amit Ayer: Yes.) is an intervening speaker and MUST be preserved as a discrete turn
    assert "### [00:01:08]\n\n**Amit Ayer:** Yes." in stitched or "**Amit Ayer:** Yes." in stitched

    # Turn 4 (Carlos Gomez) must remain separate after intervening speaker
    assert "### [00:01:10]\n\n**Carlos Gomez:** Let's proceed" in stitched or "**Carlos Gomez:** Let's proceed" in stitched

    stitched_turns = _extract_all_turns(stitched)
    assert len(stitched_turns) == 3, f"Expected 3 stitched turns, found {len(stitched_turns)}"

    # Verify all dialogue words are 100% conserved
    words_stitch = [w for _, _, txt in stitched_turns for w in txt.split()]
    assert words_orig == words_stitch
    assert len(words_orig) == len(words_stitch)


def test_modality_provenance_gate_and_alr_suppression():
    """Verify text modalities never claim acoustic verification while audio retains authentic metadata."""
    text_meta: Dict[str, Any] = {
        "meeting_name": "LENS Strategy Review",
        "date": "10/09/2026",
        "platform": "Microsoft Teams",
        "source_modality": "TEXT_DIARIZED",
        "source_type": "TEXT_DIARIZED",
        "attendees": ["Amit Ayer", "harper Hoffman", "Carlos Gomez"],
        "speaker_map": {"Amit Ayer": "Amit Ayer"},
    }
    assert is_text_modality(text_meta) is True

    # Generate ALR for text
    text_alr = format_ambiguity_lock_register(text_meta, text_meta["speaker_map"])
    assert "Multi-Tier Acoustic Isolation Protocol" not in text_alr
    assert "forensic acoustic identification" not in text_alr
    assert "resolved via acoustic profile" not in text_alr
    assert "Verified Participant Roster Alignment" in text_alr
    assert "transcript attribution" in text_alr

    # Generate Attribution Summary for text
    text_summary = AttributionSummaryBuilder.build_summary(text_meta, dialogue_text="**Amit Ayer:** Hello\n**harper Hoffman:** Hi")
    assert "forensic acoustic identification" not in text_summary
    assert "conversational context attribution" in text_summary

    # Text output passing Gate 1.11
    text_doc = f"# LENS Strategy Review -- HITL-Resolved Transcript\n\n**Date:** 10/09/2026\n\n---\n\n### [00:00:01]\n\n**Amit Ayer:** Hello\n\n---\n## Ambiguity Lock Register\n{text_alr}\n{text_summary}"
    ok, fails = check_transcript_formatting_and_provenance(text_doc, text_meta)
    assert ok is True, f"Expected pass, got: {fails}"

    # If acoustic claim is injected into text modality, Gate 1.11 must fail
    corrupt_text_doc = text_doc.replace("Verified Participant Roster Alignment", "Multi-Tier Acoustic Isolation Protocol")
    ok_bad, fails_bad = check_transcript_formatting_and_provenance(corrupt_text_doc, text_meta)
    assert ok_bad is False
    assert any("Multi-Tier Acoustic Isolation Protocol" in f for f in fails_bad)

    # Audio modality
    audio_meta: Dict[str, Any] = {
        "meeting_name": "LENS Audio Review",
        "date": "10/09/2026",
        "platform": "In-Person Recording",
        "source_modality": "AUDIO_DIARIZED",
        "source_type": "AUDIO_DIARIZED",
        "attendees": ["Amit Ayer", "Carlos Gomez", "David Miller"],
        "speaker_map": {"Speaker 1": "Amit Ayer", "Speaker 2": "Carlos Gomez"},
    }
    assert is_text_modality(audio_meta) is False

    audio_alr = format_ambiguity_lock_register(audio_meta, audio_meta["speaker_map"])
    assert "Multi-Tier Acoustic Isolation Protocol" in audio_alr
    assert "resolved via acoustic profile" in audio_alr

    audio_summary = AttributionSummaryBuilder.build_summary(audio_meta, dialogue_text="**Amit Ayer:** Hello\n**Carlos Gomez:** Hi")
    assert "forensic acoustic identification" in audio_summary

    audio_doc = f"# LENS Audio Review -- HITL-Resolved Transcript\n\n**Date:** 10/09/2026\n\n---\n\n### [00:00:01]\n\n**Amit Ayer:** Hello\n\n---\n## Ambiguity Lock Register\n{audio_alr}\n{audio_summary}"
    ok_audio, fails_audio = check_transcript_formatting_and_provenance(audio_doc, audio_meta)
    assert ok_audio is True, f"Audio doc should allow acoustic claims: {fails_audio}"
