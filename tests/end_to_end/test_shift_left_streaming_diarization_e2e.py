"""
tests/end_to_end/test_shift_left_streaming_diarization_e2e.py

Wave 5 E2E tests:
- Stage-0 preload (<150ms) resolving attendees from GraphDB + 256-D VoiceprintStore.
- StreamingTurnEmbedder per-channel VAD + 15% Ch0->Ch1 bleed suppression + live 256-D Copilot matches.
- Constrained global clustering over 600 pre-extracted 256-D turns in <200ms CPU time with 100% Ch0/Ch1 separation.
- Veto-preserving utterance assembly ("No, hold on." retains distinct speaker attribution).
"""
from __future__ import annotations

import math
import sqlite3
import struct
import time
import wave
from pathlib import Path
from typing import Dict, List

import numpy as np

from dual_pipeline.audio_ingestion_core.constrained_global_clusterer import (
    run_constrained_global_clustering,
)
from dual_pipeline.audio_ingestion_core.utterance_assembler import (
    SHORT_ACKNOWLEDGMENTS,
    assemble_utterances,
)
from dual_pipeline.audio_ingestion_core.voiceprint_store import (
    VoiceprintStore,
    generate_deterministic_embedding,
)
from dual_pipeline.live_meeting_recorder_core.stage0_speaker_preloader import (
    preload_stage0_speaker_context,
)
from dual_pipeline.live_meeting_recorder_core.streaming_turn_embedder import (
    StreamingTurnEmbedder,
)


def _write_stereo_bleed_wav(path: Path, sr: int = 16000) -> None:
    """Writes a 5.0s stereo WAV:
    - [0.0..2.5s]: Remote speaker on Ch0 (amplitude 14000) + 15% room bleed on Ch1 (amplitude 2100).
    - [2.5..5.0s]: Local operator on Ch1 (amplitude 15000) + near-silent Ch0.
    """
    n_First = int(2.5 * sr)
    n_Second = int(2.5 * sr)
    buf = bytearray()
    for i in range(n_First):
        t = i / float(sr)
        ch0 = int(14000.0 * math.sin(2.0 * math.pi * 240.0 * t))
        ch1 = int(0.15 * ch0)  # 15% acoustic bleed from Ch0 into Ch1
        buf.extend(struct.pack("<hh", ch0, ch1))
    for i in range(n_Second):
        t = i / float(sr)
        ch0 = 0
        ch1 = int(15000.0 * math.sin(2.0 * math.pi * 150.0 * t))
        buf.extend(struct.pack("<hh", ch0, ch1))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(buf))


def test_stage0_preload_and_streaming_bleed_suppression_e2e(tmp_path: Path, monkeypatch) -> None:
    """Verifies <150ms Stage-0 preload, 15% Ch0->Ch1 bleed suppression, and live 256-D Copilot matching."""
    monkeypatch.setenv("NEE_OPERATOR_NAME", "Sora Takahashi")
    vp_db = tmp_path / "vp_stage0.sqlite"
    store = VoiceprintStore(db_path=vp_db)

    emb_remote = generate_deterministic_embedding("remote_speaker_priya", dim=256)
    store.enroll_voiceprint("Priya Patel", emb_remote, project="PROJ_X", is_verified=True)

    graph_db = tmp_path / "graph.sqlite"
    conn = sqlite3.connect(str(graph_db))
    conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, node_type TEXT);")
    conn.execute("INSERT INTO node VALUES ('n1', 'Priya Patel', 'person');")
    conn.execute("INSERT INTO node VALUES ('n2', 'Devon Brooks', 'person');")
    conn.commit()
    conn.close()

    stage0 = preload_stage0_speaker_context(
        project="PROJ_X",
        voiceprint_db_path=vp_db,
        graph_db_path=graph_db,
    )
    assert stage0.elapsed_ms < 150.0
    assert stage0.operator_name == "Sora Takahashi"
    assert "Priya Patel" in stage0.enrolled_centroids
    assert len(stage0.enrolled_centroids["Priya Patel"]) == 256

    wav_chunk = tmp_path / "chunk_0000.wav"
    _write_stereo_bleed_wav(wav_chunk)
    jsonl_path = tmp_path / "turn_embeddings.jsonl"

    embedder = StreamingTurnEmbedder(
        output_jsonl_path=jsonl_path,
        stage0_context=stage0,
        window_sec=2.5,
        embedding_fn=lambda samples, sr, ch: emb_remote if ch == 0 else generate_deterministic_embedding("local_sora", dim=256),
    )
    records = embedder.process_chunk_wav(wav_chunk, chunk_index=0, chunk_start_sec=0.0)

    # Window 1 [0..2.5s] should emit Ch0 (Priya) and suppress the 15% bleed on Ch1!
    # Window 2 [2.5..5.0s] should emit Ch1 (Sora Takahashi)!
    assert embedder.suppressed_bleed_windows == 1
    assert len(records) == 2
    ch0_rec = next(r for r in records if r.channel == 0)
    ch1_rec = next(r for r in records if r.channel == 1)
    assert ch0_rec.live_match_speaker == "Priya Patel"
    assert ch1_rec.live_match_speaker == "Sora Takahashi"


def test_600_turn_constrained_global_clustering_under_200ms_and_veto_preservation(tmp_path: Path) -> None:
    """Verifies 600 pre-extracted 256-D turns cluster in <200ms CPU time with 0 Ch0/Ch1 violations and intact vetoes."""
    emb_host = np.asarray(generate_deterministic_embedding("host_ch1_voice", dim=256), dtype=np.float32)
    emb_rem1 = np.asarray(generate_deterministic_embedding("remote_enrolled_voice", dim=256), dtype=np.float32)
    emb_rem2 = np.asarray(generate_deterministic_embedding("remote_novel_voice", dim=256), dtype=np.float32)

    rng = np.random.default_rng(42)
    turns: List[Dict[str, object]] = []
    for i in range(600):
        if i % 3 == 0:
            ch = 1
            base = emb_host
        elif i % 3 == 1:
            ch = 0
            base = emb_rem1
        else:
            ch = 0
            base = emb_rem2
        noisy = base + rng.normal(0.0, 0.015, size=256).astype(np.float32)
        noisy = noisy / float(np.linalg.norm(noisy))
        turns.append({
            "turn_id": f"turn_{i:04d}",
            "channel": ch,
            "start_sec": float(i * 2.0),
            "end_sec": float(i * 2.0 + 1.8),
            "duration_sec": 1.8,
            "embedding": noisy.tolist(),
        })

    t_start = time.perf_counter()
    res = run_constrained_global_clustering(
        turns,
        enrolled_centroids={"Priya Patel": emb_rem1.tolist()},
        ch1_operator_name="Sora Takahashi",
        expected_roster_count=3,
    )
    wall_ms = (time.perf_counter() - t_start) * 1000.0

    assert wall_ms < 200.0
    assert res.elapsed_ms < 200.0
    assert res.cannot_link_violations == 0

    ch1_clusters = {t["cluster_id"] for t in res.turns if t["channel"] == 1}
    ch0_clusters = {t["cluster_id"] for t in res.turns if t["channel"] == 0}
    assert ch1_clusters == {"SPEAKER_00"}
    assert ch0_clusters == {"SPEAKER_01", "SPEAKER_02"}
    assert res.speaker_map["SPEAKER_00"] == "Sora Takahashi"
    assert res.speaker_map["SPEAKER_01"] == "Priya Patel"

    # Verify short veto ("No, hold on.") is preserved as a substantive distinct turn
    assert "no" not in SHORT_ACKNOWLEDGMENTS
    assert "nope" not in SHORT_ACKNOWLEDGMENTS
    utt_segments = [
        {"speaker": "Priya Patel", "start": 0.0, "end": 3.0, "text": "We should deploy straight to production today."},
        {"speaker": "Sora Takahashi", "start": 3.1, "end": 3.6, "text": "No, hold on."},
        {"speaker": "Priya Patel", "start": 3.8, "end": 6.5, "text": "Let us check the staging gates first."},
    ]
    utterances = assemble_utterances(utt_segments)
    assert len(utterances) == 3
    assert utterances[1]["speaker"] == "Sora Takahashi"
    assert utterances[1]["text"] == "No, hold on."
    assert utterances[1]["is_short_acknowledgment"] is False
