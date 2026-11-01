"""
Adversarial & edge-case tests for Wave 3: Stage U0 Dual-Directory Hydration & ASR Cache Alignment.
Covers:
1. Boundary payload (empty/missing manifests, 0-chunk or low-coverage segments.jsonl < 85%)
2. Malformed input (corrupted JSON in manifest, speaker_hints_queue.json, and acoustic receipt)
3. Exact production regression (1-turn unsealed output_1.ckpt.md rejected when full multi-turn transcript arrives)
4. Deduplication & fallback edge cases (overlapping speaker_hints.json and speaker_hints_queue.json)
"""
from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any, Dict

from types import SimpleNamespace
from dual_pipeline.canonical_pipeline.checkpoint_memoizer import memoize_substage_checkpoints
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.u0_ingress import hydrate_live_container_artifacts
from dual_pipeline.live_meeting_recorder_core.master_asr_seeder import seed_master_asr_from_recorder


def _write_wav(path: Path, duration_sec: float = 20.0, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x01\x00" * int(duration_sec * sample_rate))


class _StubStateMachine:
    def __init__(self) -> None:
        self.frozen: Dict[str, str] = {}
        self.permanently_frozen: set[str] = set()
        self.passing_stages: set[str] = set()

    def is_frozen(self, key: str) -> bool:
        return key in self.frozen

    def register_deliverable_pass(self, key: str, content: str) -> None:
        self.frozen[key] = content
        self.permanently_frozen.add(key)
        self.passing_stages.add(key)


def test_boundary_low_segment_coverage_rejects_asr_cache_seeding(tmp_path: Path) -> None:
    """
    Category: Boundary payload / coverage threshold.
    When segments.jsonl covers < 85% of expected chunks or audio duration (e.g. only 1 chunk out of 4,
    or 3s out of 20s WAV), seed_master_asr_from_recorder must return False so full re-transcription runs.
    """
    wav_path = tmp_path / "00_04-16-2026_1138am_et_touch_base_recording.wav"
    _write_wav(wav_path, duration_sec=20.0)

    segments_jsonl = tmp_path / "segments.jsonl"
    segments_jsonl.write_text(
        json.dumps({"chunk_seq": 0, "start_sec": 0.0, "end_sec": 3.0, "text": "Partial chunk only"}) + "\n",
        encoding="utf-8",
    )

    recorder = SimpleNamespace(
        segments_jsonl=segments_jsonl,
        master_wav=wav_path,
        transcript_cache_dir=tmp_path,
        processed_chunks=[0],
        whisper_model="mlx-community/whisper-large-v3-turbo",
        language=None,
        mock_mode=False,
    )
    receipt = SimpleNamespace(
        master_wav_sha256="a" * 64,
        total_chunks=4,
        duration_s=20.0,
    )
    seeded = seed_master_asr_from_recorder(recorder, receipt)
    assert seeded is False
    assert list(tmp_path.glob("*.raw_asr.json")) == []


def test_malformed_manifest_and_hint_files_degrade_gracefully(tmp_path: Path) -> None:
    """
    Category: Malformed input.
    Truncated/invalid JSON in manifest, speaker_hints_queue.json, and acoustic receipt
    must not crash Stage U0 hydration and must fall back cleanly.
    """
    cache_dir = tmp_path / "output" / "transcript_cache" / "NEE" / "04-16-2026"
    container_dir = tmp_path / "output" / "NEE" / "04-16-2026" / "1138am_et_touch_base"
    cache_dir.mkdir(parents=True, exist_ok=True)
    container_dir.mkdir(parents=True, exist_ok=True)

    (cache_dir / "00_04-16-2026_1138am_et_touch_base_manifest.json").write_text("{corrupted_json", encoding="utf-8")
    (cache_dir / "speaker_hints_queue.json").write_text("[not valid json", encoding="utf-8")
    (cache_dir / "00_04-16-2026_1138am_et_touch_base_acoustic_evidence_receipt.json").write_text(
        "null", encoding="utf-8"
    )

    wav_path = cache_dir / "00_04-16-2026_1138am_et_touch_base_recording.wav"
    _write_wav(wav_path, duration_sec=1.0)

    meta: Dict[str, Any] = {"output_dir": str(container_dir), "attendees": "Amit Ayer"}
    hydrate_live_container_artifacts(meta, input_path=str(wav_path))

    # Falls back to slug time parsing (11:38 AM + 30m) without raising
    assert meta.get("time") == "11:38 AM - 12:08 PM ET"
    assert meta.get("attendees") == "Amit Ayer"


def test_production_regression_unsealed_1_turn_checkpoint_rejected_on_multi_turn_source(tmp_path: Path) -> None:
    """
    Category: Exact production regression.
    Even if attempt_1 has no metadata.json or final_release_decision.json, a 1-turn
    output_1.ckpt.md from an earlier 45s truncated WAV must be rejected when the new
    run has a multi-turn source transcript.
    """
    att_dir = tmp_path / "attempts" / "attempt_1"
    att_dir.mkdir(parents=True, exist_ok=True)

    one_turn_ckpt = (
        "# Output 01\n\n"
        + ("Padding text to exceed 200 bytes minimum threshold. " * 6)
        + "\n**[00:00:02] Amit Ayer**: This was the only turn in the 45s truncated file.\n"
    )
    (att_dir / "output_1.ckpt.md").write_text(one_turn_ckpt, encoding="utf-8")

    multi_turn_source = "\n".join(
        f"[00:0{i}:00] **Speaker {(i % 2) + 1}**: Dialogue turn number {i} in the full meeting."
        for i in range(1, 9)
    )
    sm = _StubStateMachine()
    meta: Dict[str, Any] = {"source_text": multi_turn_source}

    memoized = memoize_substage_checkpoints(tmp_path, sm, meta, CanonicalModality.AUDIO)
    assert memoized["output_1"] is False
    assert not sm.is_frozen("output_1")


def test_hint_deduplication_across_container_and_transcript_cache(tmp_path: Path) -> None:
    """
    Category: Boundary / duplicate event reconciliation across dual directories.
    Identical event_ids present in both container_dir/speaker_hints.json and
    transcript_cache/speaker_hints_queue.json are deduplicated while preserving
    distinct QUEUED and RECONCILED hints.
    """
    cache_dir = tmp_path / "output" / "transcript_cache" / "NEE" / "04-16-2026"
    container_dir = tmp_path / "output" / "NEE" / "04-16-2026" / "1138am_et_touch_base"
    cache_dir.mkdir(parents=True, exist_ok=True)
    container_dir.mkdir(parents=True, exist_ok=True)

    wav_path = cache_dir / "00_04-16-2026_1138am_et_touch_base_recording.wav"
    _write_wav(wav_path, duration_sec=1.0)

    (container_dir / "speaker_hints.json").write_text(
        json.dumps([
            {"event_id": "e1", "speaker": "Amit Ayer", "quote": "First hint", "status": "RECONCILED"},
        ]),
        encoding="utf-8",
    )
    (cache_dir / "speaker_hints_queue.json").write_text(
        json.dumps([
            {"event_id": "e1", "speaker": "Amit Ayer", "quote": "First hint", "status": "QUEUED"},
            {"event_id": "e2", "speaker": "Speaker_Beta Chacko", "quote": "Second hint", "status": "QUEUED"},
            {"event_id": "e3", "speaker": "Ignored", "quote": "Dropped", "status": "EXPIRED"},
        ]),
        encoding="utf-8",
    )

    meta: Dict[str, Any] = {"output_dir": str(container_dir)}
    hydrate_live_container_artifacts(meta, input_path=str(wav_path))

    assert len(meta["turn_hints"]) == 2
    assert [h["event_id"] for h in meta["turn_hints"]] == ["e1", "e2"]
