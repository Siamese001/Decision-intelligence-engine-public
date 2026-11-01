"""
tests/test_transcript_cache_integrity_gate.py

Wave 1 Acceptance Gate: Cryptographic transcript cache seal and utterance parity verification.
Verifies that manual edits, spliced turn counts, or unauthorized Unknown speaker reassignments
in cached transcript sidecars fail closed with CacheIntegrityViolationError.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from dual_pipeline.exceptions import CacheIntegrityViolationError
from dual_pipeline.transcript_pipeline_core.cache_seal import (
    create_cache_seal,
    verify_cache_seal,
    find_associated_sidecars,
)
from dual_pipeline.audio_ingestion_core.mlx_transcriber import _try_load_cache


def _build_sample_data(num_turns: int = 5, unknown_indices: list[int] | None = None) -> tuple[list[dict], list[dict], str]:
    unknown_indices = unknown_indices or []
    turns = []
    utterances = []
    for i in range(num_turns):
        spk = "Unknown" if i in unknown_indices else f"Speaker_{i % 2}"
        turns.append({
            "speaker": spk,
            "text": f"This is turn {i} spoken content.",
            "start": float(i * 10),
            "end": float(i * 10 + 8),
        })
        utterances.append({
            "speaker": spk,
            "text": f"This is turn {i} spoken content.",
            "start": float(i * 10),
            "end": float(i * 10 + 8),
        })
    formatted_cache = "\n".join(f"[{t['speaker']}]: {t['text']}" for t in turns)
    return turns, utterances, formatted_cache


def test_valid_cache_with_seal_passes(tmp_path: Path):
    """Verifies that an authentic cache with matching seal passes verification cleanly."""
    stem = "meeting_valid"
    turns, utterances, text = _build_sample_data(10)

    structured_file = tmp_path / f"{stem}.segments.json"
    cache_file = tmp_path / f"{stem}.transcript_cache.json"
    utterance_file = tmp_path / f"{stem}.utterances.json"

    structured_file.write_text(json.dumps(turns, indent=2), encoding="utf-8")
    cache_file.write_text(text, encoding="utf-8")
    utterance_file.write_text(json.dumps(utterances, indent=2), encoding="utf-8")

    seal_file = create_cache_seal(
        structured_file=structured_file,
        cache_file=cache_file,
        utterance_file=utterance_file,
        audio_sha256="testaudiohash123",
        cache_key="testkeyabc",
    )
    assert seal_file.is_file()

    # 1. Direct verify_cache_seal
    res = verify_cache_seal(structured_file=structured_file)
    assert res["turn_count"] == 10
    assert res["cache_key"] == "testkeyabc"

    # 2. _try_load_cache
    meta: dict = {}
    loaded_text = _try_load_cache(cache_file, structured_file, utterance_file, meta)
    assert loaded_text == text
    assert meta.get("cache_seal_verified") is True
    assert meta.get("turn_count") == 10


def test_tampered_segments_fails_with_cache_integrity_violation(tmp_path: Path):
    """Verifies that tampering with .segments.json content triggers CacheIntegrityViolationError."""
    stem = "meeting_tampered_seg"
    turns, utterances, text = _build_sample_data(5)

    structured_file = tmp_path / f"{stem}.segments.json"
    cache_file = tmp_path / f"{stem}.transcript_cache.json"
    utterance_file = tmp_path / f"{stem}.utterances.json"

    structured_file.write_text(json.dumps(turns, indent=2), encoding="utf-8")
    cache_file.write_text(text, encoding="utf-8")
    utterance_file.write_text(json.dumps(utterances, indent=2), encoding="utf-8")

    create_cache_seal(
        structured_file=structured_file,
        cache_file=cache_file,
        utterance_file=utterance_file,
    )

    # Tamper with turn 0 text
    turns[0]["text"] = "MODIFIED TEXT TO SIMULATE MANUAL ATTRIBUTION/TEXT BYPASS"
    structured_file.write_text(json.dumps(turns, indent=2), encoding="utf-8")

    with pytest.raises(CacheIntegrityViolationError, match="Cryptographic segments cache seal mismatch"):
        verify_cache_seal(structured_file=structured_file)

    with pytest.raises(CacheIntegrityViolationError):
        _try_load_cache(cache_file, structured_file, utterance_file, {})


def test_tampered_transcript_cache_fails_closed(tmp_path: Path):
    """Verifies that tampering with .transcript_cache.json fails verification."""
    stem = "meeting_tampered_cache"
    turns, utterances, text = _build_sample_data(5)

    structured_file = tmp_path / f"{stem}.segments.json"
    cache_file = tmp_path / f"{stem}.transcript_cache.json"
    utterance_file = tmp_path / f"{stem}.utterances.json"

    structured_file.write_text(json.dumps(turns, indent=2), encoding="utf-8")
    cache_file.write_text(text, encoding="utf-8")
    utterance_file.write_text(json.dumps(utterances, indent=2), encoding="utf-8")

    create_cache_seal(
        structured_file=structured_file,
        cache_file=cache_file,
        utterance_file=utterance_file,
    )

    # Modify transcript cache content
    cache_file.write_text(text + "\n[Speaker_0]: Spliced turn.", encoding="utf-8")

    with pytest.raises(CacheIntegrityViolationError, match="Cryptographic transcript cache seal mismatch"):
        verify_cache_seal(structured_file=structured_file)


def test_spliced_turn_count_divergence_fails_closed(tmp_path: Path):
    """
    RCA Scenario Reproduction:
    Simulates .segments.json having 404 turns while .utterances.json has 367 turns.
    Must fail closed with CacheIntegrityViolationError.
    """
    stem = "meeting_rca_spliced"
    turns_367, utterances_367, text = _build_sample_data(367)
    turns_404, _, _ = _build_sample_data(404)

    structured_file = tmp_path / f"{stem}.segments.json"
    cache_file = tmp_path / f"{stem}.transcript_cache.json"
    utterance_file = tmp_path / f"{stem}.utterances.json"

    # Write 404 turns to segments, but 367 turns to utterances (no seal)
    structured_file.write_text(json.dumps(turns_404, indent=2), encoding="utf-8")
    cache_file.write_text(text, encoding="utf-8")
    utterance_file.write_text(json.dumps(utterances_367, indent=2), encoding="utf-8")

    with pytest.raises(CacheIntegrityViolationError, match="turn-count divergence detected"):
        verify_cache_seal(structured_file=structured_file)

    with pytest.raises(CacheIntegrityViolationError):
        _try_load_cache(cache_file, structured_file, utterance_file, {})


def test_unauthorized_unknown_speaker_relabeling_fails_closed(tmp_path: Path):
    """
    RCA Scenario Reproduction:
    Simulates turning Unknown acoustic utterances into named speakers without a verified seal.
    Must fail closed with CacheIntegrityViolationError.
    """
    stem = "meeting_rca_unknown_relabel"
    turns, utterances, text = _build_sample_data(10, unknown_indices=[2, 7])

    structured_file = tmp_path / f"{stem}.segments.json"
    cache_file = tmp_path / f"{stem}.transcript_cache.json"
    utterance_file = tmp_path / f"{stem}.utterances.json"

    # In segments, replace 'Unknown' with 'Eduardo Juan More'
    for t in turns:
        if t["speaker"] == "Unknown":
            t["speaker"] = "Eduardo Juan More"

    structured_file.write_text(json.dumps(turns, indent=2), encoding="utf-8")
    cache_file.write_text(text, encoding="utf-8")
    utterance_file.write_text(json.dumps(utterances, indent=2), encoding="utf-8")

    with pytest.raises(CacheIntegrityViolationError, match="Unauthorized speaker attribution detected"):
        verify_cache_seal(structured_file=structured_file)


def test_corrupted_json_seal_fails_closed(tmp_path: Path):
    """Verifies that malformed or truncated seal JSON raises CacheIntegrityViolationError."""
    stem = "meeting_corrupted_seal"
    turns, utterances, text = _build_sample_data(5)

    structured_file = tmp_path / f"{stem}.segments.json"
    cache_file = tmp_path / f"{stem}.transcript_cache.json"
    utterance_file = tmp_path / f"{stem}.utterances.json"
    seal_file = tmp_path / f"{stem}.cache_seal.json"

    structured_file.write_text(json.dumps(turns, indent=2), encoding="utf-8")
    cache_file.write_text(text, encoding="utf-8")
    utterance_file.write_text(json.dumps(utterances, indent=2), encoding="utf-8")
    seal_file.write_text("{malformed_json: true", encoding="utf-8")

    with pytest.raises(CacheIntegrityViolationError, match="Corrupted cache seal file"):
        verify_cache_seal(structured_file=structured_file)


def test_ingress_hydrate_segments_blocks_tampered_cache(tmp_path: Path):
    """Verifies that Stage U0 ingress _hydrate_segments_from_cache fails closed on tampered cache."""
    from dual_pipeline.transcript_pipeline_core.ingress import transcribe_audio_with_cache
    from dual_pipeline.transcript_pipeline_core.recorder import PipelineSpanRecorder

    stem = "ingress_tampered_test"
    turns_10, utterances_5, text = _build_sample_data(10)
    _, utterances_5, _ = _build_sample_data(5)

    c_dir = tmp_path / "transcript_cache"
    c_dir.mkdir(parents=True, exist_ok=True)

    audio_dummy = tmp_path / f"{stem}.wav"
    audio_dummy.write_bytes(b"RIFF" + b"\x00" * 100)

    # Write divergent segments and utterances in cache dir
    seg_file = c_dir / f"{stem}.segments.json"
    seg_file.write_text(json.dumps(turns_10, indent=2), encoding="utf-8")
    utt_file = c_dir / f"{stem}.utterances.json"
    utt_file.write_text(json.dumps(utterances_5, indent=2), encoding="utf-8")
    cache_f = c_dir / f"{stem}.transcript_cache.json"
    cache_f.write_text(text, encoding="utf-8")

    meta = {
        "transcript_cache_dir": str(c_dir),
        "transcript_cache_path": str(cache_f),
    }

    with pytest.raises(CacheIntegrityViolationError):
        transcribe_audio_with_cache(
            audio_path=str(audio_dummy),
            metadata=meta,
            recorder=PipelineSpanRecorder(),
            mock=True,
        )


from hypothesis import given, settings, strategies as st


@settings(max_examples=25, deadline=5000)
@given(
    num_turns=st.integers(min_value=1, max_value=20),
    tamper_idx=st.integers(min_value=0, max_value=19),
    tamper_char=st.text(min_size=1, max_size=10),
)
def test_hypothesis_tampered_segments_always_detected(tmp_path_factory, num_turns, tamper_idx, tamper_char):
    """Property test: Any arbitrary string injection into .segments.json is always caught."""
    tmp = tmp_path_factory.mktemp("hypo_tamper")
    stem = "hypo_meeting"
    actual_tamper_idx = tamper_idx % num_turns

    turns, utterances, text = _build_sample_data(num_turns)
    structured_file = tmp / f"{stem}.segments.json"
    cache_file = tmp / f"{stem}.transcript_cache.json"
    utterance_file = tmp / f"{stem}.utterances.json"

    structured_file.write_text(json.dumps(turns, indent=2), encoding="utf-8")
    cache_file.write_text(text, encoding="utf-8")
    utterance_file.write_text(json.dumps(utterances, indent=2), encoding="utf-8")

    create_cache_seal(
        structured_file=structured_file,
        cache_file=cache_file,
        utterance_file=utterance_file,
    )

    # Tamper
    turns[actual_tamper_idx]["text"] += tamper_char
    structured_file.write_text(json.dumps(turns, indent=2), encoding="utf-8")

    with pytest.raises(CacheIntegrityViolationError):
        verify_cache_seal(structured_file=structured_file)


@pytest.mark.parametrize("turn_count", [0, 1, 50])
def test_boundary_payloads(tmp_path: Path, turn_count: int):
    """Verifies boundary cases: 0 turns, 1 turn, 50 turns."""
    stem = f"boundary_{turn_count}"
    turns, utterances, text = _build_sample_data(turn_count)

    structured_file = tmp_path / f"{stem}.segments.json"
    cache_file = tmp_path / f"{stem}.transcript_cache.json"
    utterance_file = tmp_path / f"{stem}.utterances.json"

    structured_file.write_text(json.dumps(turns, indent=2), encoding="utf-8")
    cache_file.write_text(text, encoding="utf-8")
    utterance_file.write_text(json.dumps(utterances, indent=2), encoding="utf-8")

    seal = create_cache_seal(
        structured_file=structured_file,
        cache_file=cache_file,
        utterance_file=utterance_file,
    )
    assert seal.is_file()

    res = verify_cache_seal(structured_file=structured_file)
    assert res["turn_count"] == turn_count
    assert res["sealed"] is True
