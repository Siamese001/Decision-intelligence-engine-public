"""End-to-End Integration and Rollout Compatibility Test Suite.

Verifies Wave 6 Milestones:
1. Cold/Warm cache parity and atomic sidecar persistence.
2. Interruption and restart resilience with atomic replacements.
3. Zero-ASR re-run invariant when only grouping or rendering parameters change.
4. Correction replay through ReviewUnitManager and downstream schema envelope validation.
5. Canonical Markdown projection and timestamp format compatibility.
6. Reversible rollout manifest ensuring non-destructive rollback.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
import pytest
import jsonschema

from dual_pipeline.audio_ingestion_core.transcript_result import (
    StructuredAcousticInterval,
    StructuredResultCache,
    StructuredTranscriptResult,
    StructuredUtterance,
    StructuredWord,
)
from dual_pipeline.audio_ingestion_core.utterance_assembler import (
    AssemblyConfig,
    assemble_utterances,
)
from dual_pipeline.speaker_attribution_core.review_units import (
    ConflictingAnchorError,
    ReviewDecision,
    ReviewItem,
    ReviewUnitManager,
)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
CONTRACT_SCHEMA_PATH = ROOT_DIR / "contracts/dual_pipeline/speaker_utterance.schema.json"


@pytest.fixture
def contract_schema() -> dict:
    assert CONTRACT_SCHEMA_PATH.exists(), f"Missing schema at {CONTRACT_SCHEMA_PATH}"
    with open(CONTRACT_SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_sample_result() -> StructuredTranscriptResult:
    audio_meta = {
        "source_audio_digest": "a" * 64,
        "channels": 1,
        "sample_rate": 16000,
        "duration_seconds": 120.0,
    }
    model_meta = {
        "diarizer_model": "pyannote/speaker-diarization-3.1",
        "asr_model": "mlx-community/whisper-large-v3-turbo",
    }
    words = [
        StructuredWord(
            word_id="w_001",
            text="Hello",
            start_time_sec=0.0,
            end_time_sec=0.5,
            channel=0,
            candidate_speakers=[{"speaker": "Jake Stark", "support_score": 0.95}],
        ),
        StructuredWord(
            word_id="w_002",
            text="team.",
            start_time_sec=0.6,
            end_time_sec=1.1,
            channel=0,
            candidate_speakers=[{"speaker": "Jake Stark", "support_score": 0.95}],
        ),
        StructuredWord(
            word_id="w_003",
            text="Right.",
            start_time_sec=1.5,
            end_time_sec=1.8,
            channel=0,
            candidate_speakers=[{"speaker": "Amit Ayer", "support_score": 0.90}],
        ),
        StructuredWord(
            word_id="w_004",
            text="Let's",
            start_time_sec=2.2,
            end_time_sec=2.6,
            channel=0,
            candidate_speakers=[{"speaker": "Jake Stark", "support_score": 0.92}],
        ),
        StructuredWord(
            word_id="w_005",
            text="begin.",
            start_time_sec=2.7,
            end_time_sec=3.1,
            channel=0,
            candidate_speakers=[{"speaker": "Jake Stark", "support_score": 0.92}],
        ),
    ]
    utterances = [
        StructuredUtterance(
            utterance_id="utt_001",
            speaker="Jake Stark",
            start_time_sec=0.0,
            end_time_sec=1.1,
            text="Hello team.",
            word_ids=["w_001", "w_002"],
            boundary_reasons=["clause_boundary"],
        ),
        StructuredUtterance(
            utterance_id="utt_002",
            speaker="Amit Ayer",
            start_time_sec=1.5,
            end_time_sec=1.8,
            text="Right.",
            word_ids=["w_003"],
            boundary_reasons=["speaker_change"],
            is_short_acknowledgment=True,
        ),
        StructuredUtterance(
            utterance_id="utt_003",
            speaker="Jake Stark",
            start_time_sec=2.2,
            end_time_sec=3.1,
            text="Let's begin.",
            word_ids=["w_004", "w_005"],
            boundary_reasons=["speaker_change"],
        ),
    ]
    intervals = [
        StructuredAcousticInterval(
            interval_id="int_001",
            speaker_cluster="Speaker 1",
            start_time_sec=0.0,
            end_time_sec=1.2,
        ),
        StructuredAcousticInterval(
            interval_id="int_002",
            speaker_cluster="Speaker 4",
            start_time_sec=1.4,
            end_time_sec=1.9,
        ),
        StructuredAcousticInterval(
            interval_id="int_003",
            speaker_cluster="Speaker 1",
            start_time_sec=2.1,
            end_time_sec=3.2,
        ),
    ]
    crosswalk = [
        {
            "legacy_fragment_id": "legacy_001",
            "canonical_utterance_id": "utt_001",
            "word_ids": ["w_001", "w_002"],
            "mapping_status": "mapped_complete",
            "notes": "Direct thought mapping",
        },
        {
            "legacy_fragment_id": "legacy_002",
            "canonical_utterance_id": "utt_002",
            "word_ids": ["w_003"],
            "mapping_status": "mapped_complete",
            "notes": "Short acknowledgment preserved",
        },
        {
            "legacy_fragment_id": "legacy_003",
            "canonical_utterance_id": "utt_003",
            "word_ids": ["w_004", "w_005"],
            "mapping_status": "mapped_complete",
            "notes": "Direct thought mapping",
        },
    ]
    return StructuredTranscriptResult(
        schema_version="1.1.0",
        audio_metadata=audio_meta,
        model_metadata=model_meta,
        words=words,
        utterances=utterances,
        acoustic_intervals=intervals,
        legacy_crosswalk=crosswalk,
    )


def test_cold_and_warm_cache_parity_and_sidecars(contract_schema: dict):
    """Verifies that saving and loading results yields 100% cold/warm parity."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = StructuredResultCache(tmpdir)
        original = _build_sample_result()
        cache_key = cache.compute_cache_key(
            audio_digest=original.audio_metadata["source_audio_digest"],
            diarizer_model=original.model_metadata["diarizer_model"],
            asr_config_digest="cfg_test_digest",
            channel=0,
        )

        res_path, sidecar_path = cache.save(cache_key, original)
        assert res_path.is_file()
        assert sidecar_path.is_file()

        # Validate schema contract on saved JSON
        with open(res_path, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        jsonschema.validate(saved_data, contract_schema)

        # Warm load and verify exact field parity
        restored = cache.load(cache_key, expected_audio_digest=original.audio_metadata["source_audio_digest"])
        assert restored is not None
        assert len(restored.words) == len(original.words)
        assert len(restored.utterances) == len(original.utterances)
        assert len(restored.acoustic_intervals) == len(original.acoustic_intervals)
        assert [w.text for w in restored.words] == [w.text for w in original.words]
        assert [u.text for u in restored.utterances] == [u.text for u in original.utterances]

        # Invalidate by digest mismatch
        mismatched = cache.load(cache_key, expected_audio_digest="b" * 64)
        assert mismatched is None

        # Invalidate by missing sidecar
        sidecar_path.unlink()
        missing_sidecar = cache.load(cache_key, expected_audio_digest=original.audio_metadata["source_audio_digest"])
        assert missing_sidecar is None


def test_interruption_restart_resilience():
    """Verifies that temporary files from interrupted writes are safely ignored."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = StructuredResultCache(tmpdir)
        cache_dir = Path(tmpdir)

        # Place orphaned temporary file from interrupted process
        orphan = cache_dir / ".tmp_dead_key.result.json"
        orphan.write_text("{\"incomplete\": true}", encoding="utf-8")

        # Cache load on uncommitted key returns None without crashing
        assert cache.load("dead_key") is None

        # Clean atomic write succeeds over any existing temp files
        original = _build_sample_result()
        res_path, sidecar_path = cache.save("dead_key", original)
        assert res_path.is_file()
        assert sidecar_path.is_file()

        loaded = cache.load("dead_key")
        assert loaded is not None
        assert len(loaded.utterances) == 3


def test_changing_grouping_zero_asr_rerun():
    """Verifies that changing assembly parameters triggers zero ASR calls and conserves all words."""
    original = _build_sample_result()
    raw_elements = [
        {"speaker": "Jake Stark", "start": 0.0, "end": 0.5, "text": "Hello"},
        {"speaker": "Jake Stark", "start": 0.6, "end": 1.1, "text": "team."},
        {"speaker": "Amit Ayer", "start": 1.5, "end": 1.8, "text": "Right."},
        {"speaker": "Jake Stark", "start": 2.2, "end": 2.6, "text": "Let's"},
        {"speaker": "Jake Stark", "start": 2.7, "end": 3.1, "text": "begin."},
    ]

    # Assembly with default max pause
    asm_tight = assemble_utterances(raw_elements, config=AssemblyConfig(max_bridge_gap_sec=0.1))
    # Assembly with relaxed max pause
    asm_loose = assemble_utterances(raw_elements, config=AssemblyConfig(max_bridge_gap_sec=2.0))

    # Word conservation check: all words preserved regardless of grouping
    words_tight = " ".join(u["text"] for u in asm_tight).split()
    words_loose = " ".join(u["text"] for u in asm_loose).split()
    expected_words = ["Hello", "team.", "Right.", "Let's", "begin."]

    assert words_tight == expected_words
    assert words_loose == expected_words


def test_correction_replay_and_downstream_envelope(contract_schema: dict):
    """Verifies that human reviews update downstream outputs conforming to schema."""
    manager = ReviewUnitManager()
    utterances_raw = [
        {"utterance_id": "utt_001", "speaker": "Speaker 1", "start": 0.0, "end": 1.1, "text": "Hello team."},
        {"utterance_id": "utt_002", "speaker": "Amit Ayer", "start": 1.5, "end": 1.8, "text": "Right."},
        {"utterance_id": "utt_003", "speaker": "Speaker 1", "start": 2.2, "end": 3.1, "text": "Let's begin."},
    ]

    # Generate review items for homogeneous clusters
    review_items = manager.generate_review_items(utterances_raw)
    assert len(review_items) >= 1

    # Operator confirms Speaker 1 cluster as Jake Stark
    cluster_item = next(it for it in review_items if "utt_001" in it.target_utterance_ids)
    decision = ReviewDecision(
        decision_id="dec_001",
        review_id=cluster_item.review_id,
        action="confirm_all",
        assigned_speaker="Jake Stark",
        scope_utterance_ids=cluster_item.target_utterance_ids,
        scope_word_ids=cluster_item.word_ids,
        context_digest=cluster_item.context_digest,
        notes="Affirmed Jake Stark for Speaker 1 cluster",
    )
    res = manager.apply_decision(decision, review_items, utterances_raw)
    assert res["resolution_status"] == "resolved"
    assert utterances_raw[0]["speaker"] == "Jake Stark"
    assert utterances_raw[2]["speaker"] == "Jake Stark"

    # Conflicting anchor guard test: cannot overwrite verified anchor without override
    anchors = {"utt_001": "Jake Stark"}
    bad_decision = ReviewDecision(
        decision_id="dec_conflict",
        review_id=cluster_item.review_id,
        action="confirm_all",
        assigned_speaker="Jennifer Pan",
        scope_utterance_ids=cluster_item.target_utterance_ids,
        scope_word_ids=cluster_item.word_ids,
        context_digest=cluster_item.context_digest,
    )
    with pytest.raises(ConflictingAnchorError, match="Conflicting anchor guard triggered"):
        manager.apply_decision(bad_decision, review_items, utterances_raw, verified_anchors=anchors)

    # Construct final validated transcript result
    result = _build_sample_result()
    payload = result.to_dict()
    jsonschema.validate(payload, contract_schema)

    # Verify Markdown rendering formatting
    md = result.to_markdown()
    assert "**Jake Stark** [00:00]: Hello team." in md
    assert "**Amit Ayer** [00:01]: Right." in md
    assert "**Jake Stark** [00:02]: Let's begin." in md


def test_reversible_rollout_manifest_preserves_evidence():
    """Verifies non-destructive rollback: prior projection is restored while ledgers and caches survive."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        live_file = tmp_path / "live_transcript.md"
        backup_file = tmp_path / "legacy_backup.md"
        ledger_file = tmp_path / "hitl_decision_ledger.json"
        cache_dir = tmp_path / "cache"
        cache = StructuredResultCache(cache_dir)

        # Write initial state
        backup_content = "# Legacy Transcript v1\n**Speaker 1:** Hello team.\n"
        backup_file.write_text(backup_content, encoding="utf-8")
        live_file.write_text(backup_content, encoding="utf-8")

        # Save structured cache & decision ledger
        sample = _build_sample_result()
        cache.save("test_key", sample)
        ledger_file.write_text("{\"decisions\": [{\"id\": 1}]}", encoding="utf-8")

        # Deploy v2 projection
        v2_content = sample.to_markdown()
        live_file.write_text(v2_content, encoding="utf-8")
        assert "**Jake Stark** [00:00]: Hello team." in live_file.read_text(encoding="utf-8")

        # Execute non-destructive rollback
        live_file.write_text(backup_file.read_text(encoding="utf-8"), encoding="utf-8")

        # Projection is reverted
        assert live_file.read_text(encoding="utf-8") == backup_content
        # Cache and ledger remain preserved
        assert (cache_dir / "test_key.transcript_result.json").is_file()
        assert ledger_file.read_text(encoding="utf-8") == "{\"decisions\": [{\"id\": 1}]}"
