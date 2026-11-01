"""
Adversarial and edge-case verification for Wave 3:
Covers >= 4 mandatory edge-case categories:
1. Boundary payload: Empty transcript learning, < 3-word backchannel turns ("Yeah", "Right"), empty audio_sha256 purge.
2. Malformed input: Generic speaker labels (`Speaker 1`, `SPEAKER_00`, `Unknown`) in `learn_from_resolved_transcript`
   must NEVER be learned as human profiles in `SpeakerMemory`.
3. Invariant bypass attempt: Unverified voiceprint enrollment (`is_verified=False`) must fail closed with `ValueError`.
4. Rollback & recovery / Exact production regression: Purging corrupted `PROJECT_LENS` voiceprints enrolled from
   inverted `audio_sha256 = '55e639ad60b4b5dfd6f900b72417852ca878b7be6d7e21a9f1a5c79477a230d6'` and re-enrolling
   clean centroids without residual cross-contamination.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from dual_pipeline.audio_ingestion_core.voiceprint_store import (
    VoiceprintStore,
    generate_deterministic_embedding,
)
from dual_pipeline.speaker_memory import SpeakerMemory


def test_boundary_and_malformed_generic_speaker_rejection(tmp_path: Path) -> None:
    """Category 1 (Boundary) & Category 2 (Malformed): Short turns and generic labels are never learned as human profiles."""
    mem = SpeakerMemory(memory_path=tmp_path / "speaker_memory.json")
    assert mem.learn_from_resolved_transcript("", persist=False) == 0

    generic_and_short_transcript = (
        "**Speaker 1** [00:00:01]: This is a generic speaker turn that must never create a profile.\n"
        "**SPEAKER_00** [00:00:05]: Another raw acoustic label that must be ignored.\n"
        "**Unknown** [00:00:10]: Unknown speaker label.\n"
        "**Miles Erickson** [00:00:15]: Yeah.\n"  # < 3 words -> ignored for vocabulary/pace
        "**Miles Erickson** [00:00:20]: What does the data scientist do? They explain numbers and binomial levers.\n"
    )
    count = mem.learn_from_resolved_transcript(generic_and_short_transcript, persist=True)
    assert count == 1
    assert mem.get_profile("Speaker 1") is None
    assert mem.get_profile("SPEAKER_00") is None
    assert mem.get_profile("Unknown") is None
    miles_prof = mem.get_profile("Miles Erickson")
    assert miles_prof is not None
    assert "scientist" in miles_prof.common_vocabulary


def test_unverified_voiceprint_enrollment_fails_closed(tmp_path: Path) -> None:
    """Category 3 (Invariant Bypass): Unverified voiceprint enrollment is blocked."""
    store = VoiceprintStore(db_path=tmp_path / "vp.sqlite")
    emb = generate_deterministic_embedding("test_unverified", dim=256)
    with pytest.raises(ValueError, match="Fail-Closed Voiceprint Gate"):
        store.enroll_voiceprint(
            speaker_name="Miles Erickson",
            embedding=emb,
            project="PROJECT_LENS",
            is_verified=False,
        )


def test_rollback_and_recovery_of_inverted_project_lens_voiceprints(tmp_path: Path) -> None:
    """Category 4 (Rollback & Recovery / Production Regression): Purges inverted 10/21/2026 voiceprints by audio_sha256 and re-enrolls clean centroids."""
    store = VoiceprintStore(db_path=tmp_path / "vp.sqlite")
    bad_sha = "55e639ad60b4b5dfd6f900b72417852ca878b7be6d7e21a9f1a5c79477a230d6"

    emb_amit_true = generate_deterministic_embedding("true_amit", dim=256)
    emb_miles_true = generate_deterministic_embedding("true_miles", dim=256)

    # Simulate the Defect 1 inverted enrollment where Amit's voice was enrolled as Skyler Carlson
    store.enroll_voiceprint(
        speaker_name="Skyler Carlson",
        embedding=emb_amit_true,
        project="PROJECT_LENS",
        is_verified=True,
        audio_sha256=bad_sha,
    )
    store.enroll_voiceprint(
        speaker_name="Amit Ayer",
        embedding=emb_miles_true,
        project="PROJECT_LENS",
        is_verified=True,
        audio_sha256=bad_sha,
    )
    assert store.match_voiceprint(emb_amit_true, project="PROJECT_LENS") == ("Skyler Carlson", 1.0)

    # Purge corrupted enrollments for bad_sha
    assert store.purge_samples_by_audio_sha("") == 0
    purged = store.purge_samples_by_audio_sha(bad_sha, project="PROJECT_LENS")
    assert purged == 2
    assert store.get_voiceprint("Skyler Carlson", project="PROJECT_LENS") is None
    assert store.get_voiceprint("Amit Ayer", project="PROJECT_LENS") is None

    # Re-enroll with corrected cluster mapping
    store.enroll_voiceprint(
        speaker_name="Amit Ayer",
        embedding=emb_amit_true,
        project="PROJECT_LENS",
        is_verified=True,
        audio_sha256=bad_sha,
    )
    store.enroll_voiceprint(
        speaker_name="Miles Erickson",
        embedding=emb_miles_true,
        project="PROJECT_LENS",
        is_verified=True,
        audio_sha256=bad_sha,
    )
    assert store.match_voiceprint(emb_amit_true, project="PROJECT_LENS") == ("Amit Ayer", 1.0)
    assert store.match_voiceprint(emb_miles_true, project="PROJECT_LENS") == ("Miles Erickson", 1.0)


def test_ledger_map_cluster_parsing_and_mixed_dimension_auto_enrollment(tmp_path: Path) -> None:
    """Verifies ledger option parsing extracts speaker name cleanly and auto-enrollment skips 21-D vectors without aborting."""
    import numpy as np
    from dual_pipeline.live_meeting_recorder_core.live_voice_registry import LiveVoiceRegistry
    from dual_pipeline.speaker_attribution_core.audio_uncertainty_detector import check_ledger_for_audio_resolution
    from dual_pipeline.speaker_attribution_core.voiceprint_auto_enrollment import auto_enroll_voiceprints

    class _DummyStore:
        def _load_records(self):
            return [
                {
                    "decision_id": "dec_spk1",
                    "segment_id": "speaker_1",
                    "selected_option": "(Recommended) Map Cluster 'Speaker 1' (57 turns) to Jake Stark (Confidence: HIGH (92%))",
                    "metadata": {"project": "NEA", "meeting_name": "sync", "cluster": "speaker 1"},
                }
            ]

    resolved = check_ledger_for_audio_resolution("Speaker 1", _DummyStore(), project="NEA", meeting_name="sync")
    assert resolved == "Jake Stark"

    # Mixed 21-D and 256-D centroids in auto_enroll_voiceprints
    vp_store = VoiceprintStore(db_path=tmp_path / "auto_vp.sqlite")
    emb_256 = generate_deterministic_embedding("voice:jake_stark", dim=256)
    meta = {
        "project": "NEA",
        "cluster_centroids": {
            "Voice Host": [0.1] * 21,  # 21-D live vector must be skipped cleanly without aborting loop
            "SPEAKER_00": emb_256,
        },
        "cluster_label_map": {"SPEAKER_00": "Voice A"},
        "structured_utterances": [{"speaker": "SPEAKER_00", "start": 0.0, "end": 12.5}],
    }
    enrolled = auto_enroll_voiceprints(
        meta,
        speaker_map={"Voice Host": "Host Operator", "Speaker 1": "Jake Stark"},
        voiceprint_store=vp_store,
    )
    assert len(enrolled) == 1
    assert enrolled[0]["speaker"] == "Jake Stark"

    # LiveVoiceRegistry 256-D pre-seed vs 21-D live chunk resolution without broadcast crash
    from dual_pipeline.live_meeting_recorder_core.live_voice_registry import VoiceCluster
    reg = LiveVoiceRegistry(attendees=["Jake Stark", "Host Operator"])
    reg.voice_clusters["Voice A"] = VoiceCluster(voice_id="Voice A", resolved_speaker="Jake Stark", centroid=emb_256, sample_count=0)
    vec_21d = np.ones(21, dtype=np.float32) / np.sqrt(21.0)
    spk, vid, conf = reg._update_cluster_centroid(reg.voice_clusters["Voice A"], vec_21d, 0.91, 15.0)
    assert spk == "Jake Stark"
    assert len(reg.voice_clusters["Voice A"].centroid) == 21


def test_50_meeting_longitudinal_chaos_and_catastrophic_forgetting_guard(tmp_path: Path) -> None:
    """Verifies 50-meeting voiceprint stability under 20% corrupted bursts, opener retention, and orthographic/first-name matching."""
    import numpy as np

    store = VoiceprintStore(db_path=tmp_path / "chaos_vp.sqlite")
    rng = np.random.default_rng(42)
    true_vec = rng.normal(size=(256,)).astype(np.float32)
    true_vec /= np.linalg.norm(true_vec)

    for m_idx in range(50):
        if m_idx >= 3 and m_idx % 5 == 0:
            bad_vec = -true_vec + rng.normal(scale=0.05, size=(256,)).astype(np.float32)
            bad_vec /= np.linalg.norm(bad_vec)
            store.enroll_voiceprint(
                "Amit Ayer", bad_vec.tolist(), project="PROJ", is_verified=True,
                clean_speech_sec=180.0, audio_sha256=f"m_bad_{m_idx}",
            )
        else:
            clean_vec = true_vec + rng.normal(scale=0.03, size=(256,)).astype(np.float32)
            clean_vec /= np.linalg.norm(clean_vec)
            store.enroll_voiceprint(
                "Amit Ayer", clean_vec.tolist(), project="PROJ", is_verified=True,
                clean_speech_sec=20.0, audio_sha256=f"m_clean_{m_idx}",
            )

    prof = store.get_voiceprint("Amit Ayer", project="PROJ")
    assert prof is not None
    sim_after_50 = float(np.dot(np.array(prof["embedding"], dtype=np.float32), true_vec))
    assert sim_after_50 >= 0.95

    # Cross-project orthographic (Stephan/Stefan) & first-name (Speaker_Alpha / Speaker_Alpha McGuire) matching
    store.enroll_voiceprint("Cameron Mercer", true_vec.tolist(), project="NEA", is_verified=True, clean_speech_sec=30.0, audio_sha256="m_stef")
    store.enroll_voiceprint("Speaker_Alpha", true_vec.tolist(), project="partner_advisory", is_verified=True, clean_speech_sec=30.0, audio_sha256="m_brien")
    assert store.get_voiceprint("Cameron Mercer", project="OTHER_PROJ") is not None
    assert store.get_voiceprint("Speaker_Alpha McGuire", project="OTHER_PROJ") is not None
    matches = store.match_roster({"S1": true_vec.tolist()}, ["Cameron Mercer"], project="OTHER_PROJ")
    assert "S1" in matches and matches["S1"]["canonical_name"] == "Cameron Mercer"

    # Opener phrase retention across 50 mid-call turns + WPM EMA clamping
    mem = SpeakerMemory(memory_path=tmp_path / "chaos_mem.json")
    mem.record_attribution(
        "Amit Ayer",
        "Good morning everyone, let's dive into the architecture.",
        is_call_opener=True,
        is_speaker_first_turn=True,
        observed_wpm=150.0,
    )
    for i in range(50):
        mem.record_attribution(
            "Amit Ayer",
            f"Mid call discussion point {i} about the system.",
            is_call_opener=False,
            is_speaker_first_turn=False,
            observed_wpm=150.0 if i < 49 else 450.0,
        )
    p_amit = mem.get_profile("Amit Ayer")
    assert p_amit is not None
    assert any("good morning" in op for op in p_amit.opener_phrases)
    assert 140.0 <= p_amit.avg_words_per_minute <= 190.0


def test_output01_multiline_pollution_and_silent_gap_wpm_hardening(tmp_path: Path) -> None:
    """Verifies Output 01 multiline turn extraction, non-speaker pollution purging, WPM silent-gap filtering, and harvester parenthetical matching."""
    import json
    from dual_pipeline.audio_ingestion_core.voiceprint_harvester import extract_speaker_turns_from_transcript
    from dual_pipeline.speaker_attribution_core.candidate_scorer import (
        extract_ordered_dialogue_turns,
        extract_speaker_turns,
    )
    from dual_pipeline.speaker_attribution_core.perspective_scorer import extract_speaker_wpm

    multiline_t = (
        "### [00:00:00] **Speaker 1:**\n"
        "Good morning team, let's review the roadmap.\n\n"
        "### [00:00:15] **Speaker 2:**\n"
        "Sounds good to me, let's check the metrics.\n"
    )
    assert len(extract_speaker_turns("Speaker 1", multiline_t)) == 1
    assert len(extract_ordered_dialogue_turns(multiline_t)) == 2

    # WPM extraction with timestamp prefix and multiline header + silent gap rejection
    wpm_ts = extract_speaker_wpm(
        "Cameron Mercer",
        "[00:00:00] **Cameron Mercer:** " + " ".join(["word"] * 75) + "\n[00:00:30] **Amit Ayer:** hello",
    )
    assert abs(wpm_ts - 150.0) <= 1.0
    # 6 words across a 90s silent screen-share gap (4 WPM) must be rejected as non-continuous speech
    wpm_silent_gap = extract_speaker_wpm(
        "Cameron Mercer",
        "[00:00:00] **Cameron Mercer:** yeah sounds good to me team\n[00:01:30] **Amit Ayer:** hello",
    )
    assert wpm_silent_gap == 0.0

    # Non-speaker pollution purging and parenthetical split-identity merging
    polluted_path = tmp_path / "polluted_mem.json"
    polluted_path.write_text(
        json.dumps({
            "profiles": {
                "the raw transcript includes a non-spoken collateral annotation linking s8 to 00": {"name": "bad1", "confirmed_turn_count": 644},
                "(non-dialogue asr artifact": {"name": "(non-dialogue asr artifact", "confirmed_turn_count": 413},
                "meeting name": {"name": "Meeting Name", "confirmed_turn_count": 50},
                "amit ayer": {"name": "Amit Ayer", "confirmed_turn_count": 50, "opener_phrases": ["good morning", "00:11:22 turn"], "avg_words_per_minute": 20.2},
                "amit ayer (partner_advisory)": {"name": "Amit Ayer (PartnerAdvisory)", "confirmed_turn_count": 137, "opener_phrases": ["hey everyone"], "avg_words_per_minute": 148.0},
            }
        }),
        encoding="utf-8",
    )
    mem_clean = SpeakerMemory(memory_path=polluted_path)
    assert "meeting name" not in mem_clean._profiles
    assert "(non-dialogue asr artifact" not in mem_clean._profiles
    assert "amit ayer (partner_advisory)" not in mem_clean._profiles
    amit_p = mem_clean.get_profile("Amit Ayer")
    assert amit_p is not None and amit_p.confirmed_turn_count >= 187

    # Harvester parenthetical table + multiline dialogue turn matching
    harvester_md = (
        "## Attribution Summary\n"
        "| Speaker | Role | Attribution Basis | Confidence |\n"
        "| :--- | :--- | :--- | :--- |\n"
        "| Amit Ayer (PartnerAdvisory) | Technologist | Direct anchor | 95% |\n\n"
        "## Verbatim Transcript\n"
        "### [00:00:00] **Amit Ayer:**\n"
        "Welcome everyone to the governance review meeting today.\n"
        "### [00:00:25] **Client Speaker:**\n"
        "Thanks Amit.\n"
    )
    h_turns = extract_speaker_turns_from_transcript(harvester_md)
    assert len(h_turns) == 1 and h_turns[0]["speaker"] == "Amit Ayer"
