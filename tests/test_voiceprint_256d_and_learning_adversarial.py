"""
tests/test_voiceprint_256d_and_learning_adversarial.py

Wave 4 Adversarial & Edge-Case Suite:
- 21-D vector, SHA-256 hash string/bytes, and poison embedding_model rejection (ValueError).
- 256-D -> 21-D destructive pooling block in align_acoustic_dimension.
- 48 kHz -> 16 kHz resampling verification in DiarizationEngine.
- Local Ch1 (SPEAKER_00 / channel=1) exclusion from remote subcluster splitting.
- Unverified pre-QA speaker guesses blocked from mutating speaker_memory.json.
- 16-worker concurrent SQLite WAL stress test on VoiceprintStore.
"""
from __future__ import annotations

import concurrent.futures
import math
import struct
import wave
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest

from dual_pipeline.audio_ingestion_core.diarization_engine import DiarizationEngine
from dual_pipeline.audio_ingestion_core.subcluster_splitter import split_merged_remote_cluster
from dual_pipeline.audio_ingestion_core.voiceprint_store import (
    VoiceprintStore,
    generate_deterministic_embedding,
)
from dual_pipeline.canonical_pipeline.c0_pa_runner import _learn_speaker_memory
from dual_pipeline.live_meeting_recorder_core.acoustic_features import align_acoustic_dimension


def _write_wav_at_sr(path: Path, freq_hz: float, sr: int, duration_sec: float = 1.5) -> None:
    n_samples = int(duration_sec * sr)
    buf = bytearray()
    for i in range(n_samples):
        t = i / float(sr)
        val = int(12000.0 * math.sin(2.0 * np.pi * freq_hz * t))
        buf.extend(struct.pack("<h", val))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(buf))


def test_rejects_21d_and_hash_poison_vectors(tmp_path: Path) -> None:
    """Verifies VoiceprintStore and align_acoustic_dimension reject 21-D and hash poison inputs."""
    store = VoiceprintStore(db_path=tmp_path / "vp_adversarial.sqlite")

    # 1. 21-D handcrafted vector rejected
    vec_21d = [0.2] * 21
    with pytest.raises(ValueError, match="dimension mismatch"):
        store.enroll_voiceprint("Elena Rostova", vec_21d, is_verified=True)

    with pytest.raises(ValueError, match="dimension mismatch"):
        store.match_voiceprint(vec_21d)

    # 2. Raw SHA-256 hex string or bytes rejected
    with pytest.raises(ValueError, match="hash strings or raw bytes"):
        store.enroll_voiceprint("Elena Rostova", "a" * 256, is_verified=True)  # type: ignore[arg-type]

    # 3. Non-WeSpeaker / hash model tag rejected
    valid_256 = generate_deterministic_embedding("seed_valid", dim=256)
    with pytest.raises(ValueError, match="256-D Model Gate"):
        store.enroll_voiceprint(
            "Elena Rostova",
            valid_256,
            is_verified=True,
            embedding_model="sha256_hash_pseudo_vector",
        )

    # 4. Destructive 256-D -> 21-D pooling blocked by default
    with pytest.raises(ValueError, match="refusing to project"):
        align_acoustic_dimension(np.asarray(valid_256, dtype=np.float32), target_dim=21)


def test_48khz_to_16khz_resampling_parity(tmp_path: Path) -> None:
    """Verifies 48 kHz and 16 kHz recordings of the same signal resample to matching 16 kHz 256-D embeddings."""
    wav_16k = tmp_path / "tone_16k.wav"
    wav_48k = tmp_path / "tone_48k.wav"
    _write_wav_at_sr(wav_16k, freq_hz=320.0, sr=16000, duration_sec=2.0)
    _write_wav_at_sr(wav_48k, freq_hz=320.0, sr=48000, duration_sec=2.0)

    engine = DiarizationEngine(mock=True)
    emb_16k = np.asarray(engine.extract_turn_embedding(wav_16k, 0.0, 1.5), dtype=np.float32)
    emb_48k = np.asarray(engine.extract_turn_embedding(wav_48k, 0.0, 1.5), dtype=np.float32)

    assert emb_16k.shape == (256,)
    assert emb_48k.shape == (256,)
    cosine_sim = float(np.dot(emb_16k, emb_48k))
    assert cosine_sim >= 0.98


def test_local_ch1_excluded_from_remote_subcluster_splitting() -> None:
    """Verifies local Ch1 (SPEAKER_00, channel=1) is never split into remote speakers even when dominant."""
    emb_host = generate_deterministic_embedding("host_ch1", dim=256)
    emb_rem_a = generate_deterministic_embedding("remote_alice", dim=256)
    emb_rem_b = generate_deterministic_embedding("remote_bob", dim=256)

    segments: List[Dict[str, Any]] = []
    # 8 local Ch1 turns (dominant cluster SPEAKER_00)
    for i in range(8):
        segments.append({
            "speaker": "SPEAKER_00",
            "raw_speaker": "SPEAKER_00",
            "channel": 1,
            "start": float(i * 2),
            "end": float(i * 2 + 1.5),
            "text": f"Local host statement {i}.",
            "embedding": emb_host,
        })
    # 4 remote Ch0 turns (merged in SPEAKER_01 for 2 remote attendees)
    for j in range(4):
        segments.append({
            "speaker": "SPEAKER_01",
            "raw_speaker": "SPEAKER_01",
            "channel": 0,
            "start": float(20 + j * 2),
            "end": float(20 + j * 2 + 1.5),
            "text": f"Remote statement {j}.",
            "embedding": emb_rem_a if j % 2 == 0 else emb_rem_b,
        })

    meta = {"attendees": ["Amit Ayer", "Alice Smith", "Bob Jones"]}
    updated, split_meta = split_merged_remote_cluster(segments, metadata=meta)

    assert split_meta["split_performed"] is True
    assert split_meta["merged_cluster"] == "SPEAKER_01"
    ch1_speakers = {s["speaker"] for s in updated if s.get("channel") == 1}
    assert ch1_speakers == {"SPEAKER_00"}


def test_unverified_pre_qa_guesses_do_not_mutate_speaker_memory(tmp_path: Path) -> None:
    """Verifies _learn_speaker_memory blocks unverified pre-QA speaker guesses from persisting."""
    class _SpyMemory:
        def __init__(self) -> None:
            self.calls = 0

        def learn_from_resolved_transcript(self, *args: Any, **kwargs: Any) -> None:
            self.calls += 1

    spy = _SpyMemory()
    unverified_meta = {
        "speaker_memory": spy,
        "speaker_map": {"SPEAKER_00": "Guessed Person"},
    }
    _learn_speaker_memory(unverified_meta, "**Guessed Person:** Hello world.")
    assert spy.calls == 0

    verified_meta = {
        "speaker_memory": spy,
        "speaker_map": {"SPEAKER_00": "Verified Person"},
        "gate_1_7_passed": True,
    }
    _learn_speaker_memory(verified_meta, "**Verified Person:** Hello world.")
    assert spy.calls == 1


def test_16_worker_sqlite_wal_concurrency_stress(tmp_path: Path) -> None:
    """Verifies 16 concurrent workers enrolling and matching 256-D voiceprints produce zero lock errors."""
    db_file = tmp_path / "wal_stress_voiceprints.sqlite"
    store = VoiceprintStore(db_path=db_file)

    def _worker_task(worker_id: int) -> bool:
        w_store = VoiceprintStore(db_path=db_file)
        for k in range(4):
            spk = f"Speaker_{worker_id}_{k}"
            emb = generate_deterministic_embedding(f"w:{worker_id}:{k}", dim=256)
            w_store.enroll_voiceprint(
                speaker_name=spk,
                embedding=emb,
                project="STRESS_PROJ",
                is_verified=True,
                audio_sha256=f"sha_{worker_id}_{k}",
            )
            matched = w_store.match_voiceprint(emb, project="STRESS_PROJ", threshold=0.80)
            if not matched or matched[0] != spk:
                return False
        return True

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_worker_task, range(16)))

    assert all(results)
    assert store.verify_integrity() is True
    assert len(store.get_enrolled_speakers(project="STRESS_PROJ")) == 64
