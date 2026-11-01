"""End-to-end verification for SSOT configuration, atomic cache sealing, and RapidFuzz deduplication."""
from __future__ import annotations

import ast
import json
import tempfile
from pathlib import Path

from dual_pipeline.audio_ingestion_core.asr_deduplicator import (
    _is_consecutive_loop,
    _is_delayed_echo,
    deduplicate_consecutive_asr_segments,
)
from dual_pipeline.audio_ingestion_core.audio_asr_cache import (
    load_audio_diarization_cache,
    save_audio_diarization_cache,
)
from dual_pipeline.config_hasher import (
    compute_active_config_snapshot,
    compute_config_canonical_sha256,
    verify_config_integrity,
)
from dual_pipeline.transcript_pipeline_core.cache_seal import (
    create_cache_seal,
    verify_cache_seal,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_config_hasher_ssot_credential_isolation_and_audio_norm_digest() -> None:
    """Verify config_hasher uses CredentialManager (zero raw os.environ.get) and hashes audio profiles."""
    src_path = REPO_ROOT / "dual_pipeline" / "config_hasher.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "environ":
            raise AssertionError("dual_pipeline/config_hasher.py must not access raw os.environ directly")

    snapshot = compute_active_config_snapshot()
    assert "audio_normalization_digest" in snapshot
    assert isinstance(snapshot["audio_normalization_digest"], str)
    assert len(snapshot["audio_normalization_digest"]) == 64

    # Also exercise the CredentialManager fallback path when live provider resolution raises
    import dual_pipeline.config_hasher as ch_mod
    orig_openai = ch_mod.resolve_openai_credentials
    orig_anth = ch_mod.resolve_anthropic_credentials
    try:
        ch_mod.resolve_openai_credentials = lambda allow_mock=False: (_ for _ in ()).throw(RuntimeError("no key"))
        ch_mod.resolve_anthropic_credentials = lambda allow_mock=False: (_ for _ in ()).throw(RuntimeError("no key"))
        fallback_snap = compute_active_config_snapshot()
        assert fallback_snap["providers"]["openai"]["configured"] is False
        assert fallback_snap["providers"]["anthropic"]["configured"] is False
    finally:
        ch_mod.resolve_openai_credentials = orig_openai
        ch_mod.resolve_anthropic_credentials = orig_anth

    digest_hex = compute_config_canonical_sha256(snapshot)
    assert len(digest_hex) == 64
    assert verify_config_integrity(digest_hex) is True


def test_atomic_diarization_and_cache_seal_e2e_lifecycle() -> None:
    """Verify diarization sidecar and cache seal write atomically via .tmp staging and pass seal verification."""
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_ssot_e2e_") as tmp_dir:
        c_dir = Path(tmp_dir)
        audio_sha = "a" * 64
        turns = [
            {"start": 0.0, "end": 2.5, "speaker": "SPEAKER_00"},
            {"start": 2.5, "end": 5.0, "speaker": "SPEAKER_01"},
        ]
        diar_path = save_audio_diarization_cache(
            audio_sha256=audio_sha,
            turns=turns,
            model_name="pyannote/speaker-diarization-3.1",
            stem="meeting_test",
            cache_dir=c_dir,
            centroids={"SPEAKER_00": [0.1, 0.2]},
        )
        assert diar_path.is_file()
        assert not diar_path.with_suffix(".diarization.json.tmp").exists()
        loaded = load_audio_diarization_cache(
            audio_sha256=audio_sha,
            model_name="pyannote/speaker-diarization-3.1",
            stem="meeting_test",
            cache_dir=c_dir,
        )
        assert loaded is not None
        assert len(loaded[0]) == 2
        assert loaded[1] == {"SPEAKER_00": [0.1, 0.2]}

        seg_file = c_dir / "meeting_test.segments.json"
        cache_file = c_dir / "meeting_test.transcript_cache.json"
        utt_file = c_dir / "meeting_test.utterances.json"
        segments_payload = [
            {"speaker": "Alice", "start": 0.0, "end": 2.5, "text": "Welcome everyone to the architecture review."},
            {"speaker": "Bob", "start": 2.5, "end": 5.0, "text": "Thank you Alice let us begin."},
        ]
        seg_file.write_text(json.dumps(segments_payload), encoding="utf-8")
        cache_file.write_text(json.dumps({"segments": segments_payload}), encoding="utf-8")
        utt_file.write_text(json.dumps(segments_payload), encoding="utf-8")

        seal_path = create_cache_seal(
            structured_file=seg_file,
            cache_file=cache_file,
            utterance_file=utt_file,
            audio_sha256=audio_sha,
            cache_key="deadbeef12345678",
        )
        assert seal_path.is_file()
        assert not seal_path.with_suffix(".cache_seal.json.tmp").exists()

        seal_verdict = verify_cache_seal(
            structured_file=seg_file,
            cache_file=cache_file,
            utterance_file=utt_file,
            seal_file=seal_path,
        )
        assert seal_verdict["sealed"] is True
        assert seal_verdict["turn_count"] == 2


def test_asr_deduplicator_rapidfuzz_consolidation_and_ssot_defaults() -> None:
    """Verify asr_deduplicator uses rapidfuzz similarity ratio and SSOT default parameters."""
    dedup_src = (REPO_ROOT / "dual_pipeline" / "audio_ingestion_core" / "asr_deduplicator.py").read_text(
        encoding="utf-8"
    )
    assert "rapidfuzz" in dedup_src
    assert "_compute_similarity_ratio" in dedup_src

    is_loop, reason = _is_consecutive_loop(
        "we need to finalize the quarterly infrastructure budget today",
        "we need to finalize the quarterly infrastructure budget now",
        9,
        0.85,
    )
    assert is_loop is True
    assert "CONSECUTIVE_NEAR_DUPLICATE_LOOP" in reason

    assert _is_delayed_echo(
        seg_start=5.0,
        norm_text="we need to finalize the quarterly infrastructure budget today",
        words_len=9,
        recent_history=[(1.0, 2.0, "we need to finalize the quarterly infrastructure budget now", "SPEAKER_00")],
        echo_window_seconds=10.0,
        similarity_threshold=0.85,
    )

    segments = [
        {"start": 0.0, "end": 2.0, "text": "Let us review the deployment timeline for next week.", "speaker": "A"},
        {"start": 2.0, "end": 4.0, "text": "Let us review the deployment timeline for next week!", "speaker": "A"},
        {"start": 4.5, "end": 7.0, "text": "Agreed, the cutover is scheduled for Friday.", "speaker": "B"},
    ]
    cleaned = deduplicate_consecutive_asr_segments(segments)
    assert len(cleaned) == 2
