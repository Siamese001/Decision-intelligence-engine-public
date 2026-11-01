"""
Wave 5 Adversarial & Regression Test Suite: Recording Stop to HITL Diarization Handoff.

Covers all 7 mandatory edge-case categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload: 1-turn recording & boundary first/last turn context windows
2. Malformed input: Corrupted 00_*_manifest.json & malformed speaker_hints_queue.json entries
3. Concurrency & race: Multi-threaded concurrent container hydration & turn scoring
4. Overflow & exhaustion: 250-turn bag-of-words token scatter across full call without single-turn hit
5. Invariant bypass attempt: Polluted generic speaker_map & double-colon header injection attempt
6. Rollback & recovery: Stale 1-turn Output 01 checkpoint invalidated when source audio hash changes
7. Exact production regression: 10-16-2026 Ed_Amit 340-turn handoff regression
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List

from dual_pipeline.canonical_pipeline.checkpoint_memoizer import (
    _attempt_hash_mismatch,
    _read_attempt_checkpoint,
)
from dual_pipeline.canonical_pipeline.u0_ingress import hydrate_live_container_artifacts
from dual_pipeline.hitl_governance import HITLDecisionStore
from dual_pipeline.speaker_attribution_core.attribution_engine import (
    AudioSpeakerAttributionEngine,
)
from dual_pipeline.speaker_attribution_core.candidate_scorer import (
    extract_ordered_dialogue_turns,
    score_candidates_for_speaker,
)
from dual_pipeline.speaker_attribution_core.quote_hint_turn_mapper import (
    apply_quote_hints_to_turns,
)
from dual_pipeline.speaker_attribution_core.review_context_render import (
    build_speaker_review_question,
    validate_visible_speaker_context,
)
from dual_pipeline.transcript_stages_core.l1_attribution import apply_speaker_map
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


def test_category_1_boundary_single_turn_and_edge_context_windows() -> None:
    """Category 1 (Boundary payload): Single-turn transcript and first/last turn boundary counts."""
    single_turn = "**Speaker 1** [00:00:01]: Only one turn exists in this short recording."
    q = build_speaker_review_question(
        single_turn,
        "Speaker 1",
        "HITL Decision: Assign Speaker 1",
        source_id="src_boundary_1",
    )
    digest = validate_visible_speaker_context(q)
    assert isinstance(digest, str) and len(digest) >= 16
    assert "Transcript start: 2 earlier turn(s) unavailable at this boundary." in q
    assert "Transcript end: 2 later turn(s) unavailable at this boundary." in q


def test_category_2_malformed_manifest_and_hints_graceful_hydration(tmp_path: Path) -> None:
    """Category 2 (Malformed input): Truncated JSON in 00_*_manifest.json and invalid hint entries."""
    container = tmp_path / "output" / "NEE" / "10-16-2026" / "malformed"
    container.mkdir(parents=True)
    wav_file = container / "00_10-16-2026_malformed_recording.wav"
    wav_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")

    # Write truncated JSON to manifest and mixed valid/malformed hints
    (container / "00_10-16-2026_malformed_manifest.json").write_text("{corrupted_json: true", encoding="utf-8")
    (container / "speaker_hints_queue.json").write_text(
        json.dumps([
            "not_a_dict",
            {"speaker": "", "quote": ""},
            {"speaker": "Eduardo More", "quote": "93 reviewed, 62 rejected", "status": "QUEUED"},
        ]),
        encoding="utf-8",
    )
    meta: Dict[str, Any] = {
        "project": "NEE",
        "output_dir": str(container),
        "attendees": ["Amit Ayer", "Eduardo More"],
    }
    hydrate_live_container_artifacts(meta, input_path=str(wav_file))
    assert len(meta.get("turn_hints", [])) >= 1
    text, reassignments = apply_quote_hints_to_turns(
        "**Speaker 2** [00:00:05]: Out of the batch, 93 reviewed, 62 rejected.",
        meta["turn_hints"],
    )
    assert "Eduardo More" in reassignments.values()
    assert "**Eduardo More**" in text


def test_category_3_concurrent_container_hydration_and_scoring(tmp_path: Path) -> None:
    """Category 3 (Concurrency & race): Multiple threads hydrating container artifacts & scoring turns concurrently."""
    container = tmp_path / "output" / "NEE" / "10-16-2026" / "concurrent"
    container.mkdir(parents=True)
    wav_file = container / "00_10-16-2026_concurrent_recording.wav"
    wav_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    (container / "00_10-16-2026_concurrent_manifest.json").write_text(
        json.dumps({
            "project": "NEE",
            "attendees": [
                {"name": "Amit Ayer", "role": "MD", "org": "PartnerAdvisory"},
                {"name": "Eduardo More", "role": "Director", "org": "NEE"},
            ],
        }),
        encoding="utf-8",
    )
    (container / "speaker_hints_queue.json").write_text(
        json.dumps([{"speaker": "Eduardo More", "quote": "93 reviewed, 62 rejected", "status": "QUEUED"}]),
        encoding="utf-8",
    )
    tx = (
        "**Speaker 1** [00:00:01]: Let us review the Maximo status.\n\n"
        "**Speaker 2** [00:00:05]: We have 93 reviewed, 62 rejected in the queue."
    )
    roster = [("Amit Ayer", "PartnerAdvisory"), ("Eduardo More", "NEE")]
    results: List[str] = []
    lock = threading.Lock()

    def _worker() -> None:
        m: Dict[str, Any] = {
            "project": "NEE",
            "output_dir": str(container),
            "attendees": ["Amit Ayer", "Eduardo More"],
        }
        hydrate_live_container_artifacts(m, input_path=str(wav_file))
        cands = score_candidates_for_speaker("Speaker 2", tx, roster, m, total_clusters=2)
        with lock:
            results.append(cands[0].attendee_name)

    threads = [threading.Thread(target=_worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert len(results) == 6
    assert all(name == "Eduardo More" for name in results)


def test_category_4_overflow_bag_of_words_scatter_across_250_turns() -> None:
    """Category 4 (Overflow & exhaustion): 250 turns scattering quote words must not trigger false-positive hint match."""
    scattered_words = ["we", "want", "to", "have", "maximo", "populated", "with", "reviewed", "assets"]
    turns: List[str] = []
    for idx in range(250):
        w = scattered_words[idx % len(scattered_words)]
        turns.append(f"**Speaker 1** [00:{(idx // 60):02d}:{(idx % 60):02d}]: Routine discussion item {idx} mentioning {w} only.")
    turns.append(
        "**Speaker 2** [00:15:00]: We want to have Maximo populated with reviewed assets before Friday."
    )
    transcript = "\n\n".join(turns)
    roster = [("Amit Ayer", "PartnerAdvisory"), ("Eduardo More", "NEE")]
    meta: Dict[str, Any] = {
        "speaker_hints": [
            {"speaker": "Eduardo More", "quote": "We want to have Maximo populated with reviewed assets"}
        ]
    }
    spk1_cands = {
        c.attendee_name: c
        for c in score_candidates_for_speaker("Speaker 1", transcript, roster, meta, total_clusters=2)
    }
    spk2_cands = {
        c.attendee_name: c
        for c in score_candidates_for_speaker("Speaker 2", transcript, roster, meta, total_clusters=2)
    }
    assert spk2_cands["Eduardo More"].confidence >= 0.92
    assert spk1_cands["Eduardo More"].confidence <= 0.50
    assert spk1_cands["Amit Ayer"].confidence >= 0.85


def test_category_5_invariant_bypass_polluted_speaker_map_and_double_colon() -> None:
    """Category 5 (Invariant bypass attempt): Generic speaker_map self-mapping and double-colon formatting check."""
    transcript = "**Speaker 1** [00:00:01]: Hello team.\n\n**Speaker 2** [00:00:05]: Good morning."
    polluted_meta: Dict[str, Any] = {
        "speaker_map": {"SPEAKER_00": "Speaker 1", "SPEAKER_01": "Speaker 2"},
        "has_acoustic_diarization": True,
    }
    assert U0IngestionBoundary._detect_diarization_status(transcript, polluted_meta) == "DIARIZED_ACOUSTIC"

    mapped = apply_speaker_map(transcript, {"Speaker 1": "Amit Ayer", "Speaker 2": "Eduardo More"})
    assert "**Amit Ayer** [00:00:01]: Hello team." in mapped
    assert "**Eduardo More** [00:00:05]: Good morning." in mapped
    assert "**Amit Ayer:**" not in mapped
    assert "**Eduardo More:**" not in mapped


def test_category_6_rollback_recovery_stale_1_turn_checkpoint_invalidation(tmp_path: Path) -> None:
    """Category 6 (Rollback & recovery): Stale 1-turn Output 01 checkpoint invalidated when source audio hash changes."""
    att_dir = tmp_path / "attempt_1"
    att_dir.mkdir(parents=True)
    ckpt_file = att_dir / "01_checkpoint.md"
    ckpt_file.write_text("**Amit Ayer** [00:00:00]: " + ("Quick 5-second test. " * 20), encoding="utf-8")
    (att_dir / "metadata.json").write_text(
        json.dumps({"audio_sha256": "old_5_second_wav_hash"}),
        encoding="utf-8",
    )
    (att_dir / "final_release_decision.json").write_text(
        json.dumps({"sealed": True, "output1_pass": True, "audio_sha256": "old_5_second_wav_hash"}),
        encoding="utf-8",
    )
    assert _attempt_hash_mismatch(att_dir, "new_33_minute_wav_hash") is True
    assert _read_attempt_checkpoint(
        att_dir,
        "01_checkpoint.md",
        "output1_pass",
        expected_source_hash="new_33_minute_wav_hash",
    ) is None
    assert _read_attempt_checkpoint(
        att_dir,
        "01_checkpoint.md",
        "output1_pass",
        expected_source_hash="old_5_second_wav_hash",
    ) is not None


def test_category_7_exact_ed_amit_340_turn_production_regression(tmp_path: Path) -> None:
    """Category 7 (Exact production regression): 10-16-2026 Ed_Amit 340-turn speaker attribution handoff."""
    turns: List[str] = []
    words_h1 = ["We", "want", "to", "have", "Maximo", "populated", "for", "the", "field", "crew"]
    for i in range(340):
        mm = (i * 5) // 60
        ss = (i * 5) % 60
        ts = f"00:{mm:02d}:{ss:02d}"
        if i == 31:
            turns.append(f"**Speaker 2** [{ts}]: Looking at the work orders, 93 reviewed, 62 rejected so far.")
        elif i % 2 == 0:
            w = words_h1[(i // 2) % len(words_h1)]
            turns.append(f"**Speaker 1** [{ts}]: Turn {i} status check covering {w} in our workflow.")
        else:
            turns.append(f"**Speaker 2** [{ts}]: Turn {i} update on the engineering schedule.")

    transcript = "\n\n".join(turns)
    extracted = extract_ordered_dialogue_turns(transcript)
    assert len(extracted) == 340

    meta: Dict[str, Any] = {
        "project": f"NEE_REG_{tmp_path.name}",
        "meeting_name": "Ed_Amit",
        "date": "10-16-2026",
        "diarization_status": "DIARIZED_ACOUSTIC",
        "has_acoustic_diarization": True,
        "attendees": [
            {"name": "Amit Ayer", "role": "Managing Director", "org": "PartnerAdvisory"},
            {"name": "Eduardo More", "role": "Director", "org": "NEE"},
        ],
        "speaker_hints": [
            {"speaker": "Eduardo More", "quote": "We want to have Maximo populated for the field crew"},
            {"speaker": "Eduardo More", "quote": "93 reviewed, 62 rejected"},
        ],
    }
    store = HITLDecisionStore(ledger_path=tmp_path / "hitl_ledger.json")
    engine = AudioSpeakerAttributionEngine(decision_store=store)
    res = engine.analyze_and_resolve(transcript, meta)

    assert res.speaker_map["Speaker 2"] == "Eduardo More"
    assert res.speaker_map["Speaker 1"] == "Amit Ayer"
    assert "**Eduardo More** [00:02:35]:" in res.resolved_transcript
    assert "**Amit Ayer** [00:00:00]:" in res.resolved_transcript
    assert "**Eduardo More:**" not in res.resolved_transcript
