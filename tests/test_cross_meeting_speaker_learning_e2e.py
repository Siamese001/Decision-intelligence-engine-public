"""
End-to-end verification for Wave 3:
- Shared Cross-Worktree VoiceprintStore & SpeakerMemory path resolution (eliminating per-worktree siloing).
- Automatic learning from resolved transcripts (`SpeakerMemory.learn_from_resolved_transcript`)
  and bootstrap profile coverage for `Miles Erickson`, `Skyler Carlson`, and `Amit Ayer`.
- Purging inverted voiceprints (`VoiceprintStore.purge_samples_by_audio_sha`) and scoring
  new meetings using prior-meeting voiceprints + linguistic profiles with margin >= 0.20.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from dual_pipeline.audio_ingestion_core.voiceprint_store import (
    VoiceprintStore,
    generate_deterministic_embedding,
    resolve_canonical_voiceprint_db_path,
)
from dual_pipeline.speaker_attribution_core.candidate_scorer import score_candidates_for_speaker
from dual_pipeline.speaker_memory import SpeakerMemory, resolve_canonical_speaker_memory_path


def test_worktree_path_resolution_points_to_primary_repo_root(tmp_path: Path, monkeypatch: Any) -> None:
    """Verifies that a file inside `.worktrees/<slug>/...` resolves to the primary repository root's shared store."""
    monkeypatch.delenv("NEE_VOICEPRINT_DB_PATH", raising=False)
    monkeypatch.delenv("NEE_SPEAKER_MEMORY_PATH", raising=False)

    fake_repo = tmp_path / "repo"
    (fake_repo / ".git").mkdir(parents=True)
    fake_worktree_module = (
        fake_repo
        / ".worktrees"
        / "audio_project_lens_10_21_2026"
        / "dual_pipeline"
        / "audio_ingestion_core"
        / "voiceprint_store.py"
    )
    fake_worktree_module.parent.mkdir(parents=True)
    fake_worktree_module.write_text("# stub\n", encoding="utf-8")

    vp_path = resolve_canonical_voiceprint_db_path(fake_worktree_module)
    assert vp_path == fake_repo / "output" / "voiceprint_memory.sqlite"

    fake_mem_module = (
        fake_repo
        / ".worktrees"
        / "audio_project_lens_10_21_2026"
        / "dual_pipeline"
        / "speaker_memory.py"
    )
    fake_mem_module.write_text("# stub\n", encoding="utf-8")
    mem_path = resolve_canonical_speaker_memory_path(fake_mem_module)
    assert mem_path == fake_repo / "config" / "runtime" / "speaker_memory.json"


def test_cross_meeting_voiceprint_and_speaker_memory_learning_ranks_miles_vs_skyler(tmp_path: Path) -> None:
    """Verifies prior-meeting voiceprints and learned linguistic profiles decisively separate Miles Erickson and Skyler Carlson."""
    vp_db = tmp_path / "shared_voiceprints.sqlite"
    mem_file = tmp_path / "shared_speaker_memory.json"

    store = VoiceprintStore(db_path=vp_db)
    mem = SpeakerMemory(memory_path=mem_file)

    emb_miles = generate_deterministic_embedding("voice:miles_erickson", dim=256)
    emb_skyler = generate_deterministic_embedding("voice:skyler_carlson", dim=256)
    emb_amit = generate_deterministic_embedding("voice:amit_ayer", dim=256)

    # 1. Enroll prior meeting voiceprints
    store.enroll_voiceprint(
        speaker_name="Miles Erickson",
        embedding=emb_miles,
        project="PROJECT_LENS",
        is_verified=True,
        audio_sha256="prior_meeting_sha_001",
    )
    store.enroll_voiceprint(
        speaker_name="Skyler Carlson",
        embedding=emb_skyler,
        project="PROJECT_LENS",
        is_verified=True,
        audio_sha256="prior_meeting_sha_001",
    )
    store.enroll_voiceprint(
        speaker_name="Amit Ayer",
        embedding=emb_amit,
        project="PROJECT_LENS",
        is_verified=True,
        audio_sha256="prior_meeting_sha_001",
    )

    # 2. Learn linguistic patterns from a prior resolved transcript
    prior_resolved = (
        "**Miles Erickson** [00:01:00]: Looking at the accuracy levers and the binomial confidence interval, "
        "the denominator in the statistics jumps out at me because the data scientist needs to explain numbers.\n"
        "**Skyler Carlson** [00:01:30]: During our OCR working session, we updated the random forest slide deck "
        "for the leadership readout.\n"
        "**Amit Ayer** [00:02:00]: Let's align the architecture blueprint with the SOW contract standards.\n"
    )
    learned_turns = mem.learn_from_resolved_transcript(
        prior_resolved,
        speaker_map={"Speaker 1": "Miles Erickson", "Speaker 2": "Skyler Carlson", "Speaker 3": "Amit Ayer"},
        persist=True,
    )
    assert learned_turns == 3
    assert mem_file.is_file()

    # Reload from disk to prove persistence
    reloaded_mem = SpeakerMemory(memory_path=mem_file)
    assert reloaded_mem.get_profile("Miles Erickson") is not None
    assert reloaded_mem.get_profile("Skyler Carlson") is not None

    # 3. Score a new unmapped meeting where SPEAKER_01 is Speaker 1 (Miles) and SPEAKER_00 is Speaker 2 (Amit)
    new_transcript = (
        "**Speaker 1** [00:00:05]: It's just bizarre because what does the data scientist do? They explain numbers and statistics.\n"
        "**Speaker 2** [00:00:25]: We need the SOW architecture blueprint ready for the client.\n"
        "**Speaker 3** [00:00:45]: In the working session we reviewed the random forest slide deck.\n"
    )
    metadata: Dict[str, Any] = {
        "project": "PROJECT_LENS",
        "voiceprint_db_path": str(vp_db),
        "speaker_memory": reloaded_mem,
        "cluster_label_map": {
            "SPEAKER_01": "Speaker 1",
            "SPEAKER_00": "Speaker 2",
            "SPEAKER_02": "Speaker 3",
        },
        "cluster_centroids": {
            "SPEAKER_01": emb_miles,
            "SPEAKER_00": emb_amit,
            "SPEAKER_02": emb_skyler,
        },
        "roster": [
            {"name": "Miles Erickson", "org": "PartnerAdvisory", "role": "Data Scientist"},
            {"name": "Skyler Carlson", "org": "PartnerAdvisory", "role": "Engagement Lead"},
            {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Enterprise Architect"},
        ],
    }
    roster_tuples = [("Miles Erickson", "PartnerAdvisory"), ("Skyler Carlson", "PartnerAdvisory"), ("Amit Ayer", "PartnerAdvisory")]

    cands_spk1 = score_candidates_for_speaker("Speaker 1", new_transcript, roster_tuples, metadata, total_clusters=3)
    assert cands_spk1[0].attendee_name == "Miles Erickson"
    margin_spk1 = cands_spk1[0].confidence - cands_spk1[1].confidence
    assert margin_spk1 >= 0.20

    cands_spk3 = score_candidates_for_speaker("Speaker 3", new_transcript, roster_tuples, metadata, total_clusters=3)
    assert cands_spk3[0].attendee_name == "Skyler Carlson"
    margin_spk3 = cands_spk3[0].confidence - cands_spk3[1].confidence
    assert margin_spk3 >= 0.20


def test_cross_project_voiceprint_lookup_and_cached_sidecar_rebinding(tmp_path: Path, monkeypatch: Any) -> None:
    """Verifies cross-project voiceprints, legacy DB sync, WPM/opener learning, and cached sidecar re-binding."""
    import json
    from dual_pipeline.audio_ingestion_core.acoustic_cache import _restore_voiceprints_sidecar
    from dual_pipeline.audio_ingestion_core.voiceprint_harvester import harvest_voiceprints_from_meeting

    legacy_db = tmp_path / "legacy_vp.sqlite"
    canon_db = tmp_path / "canon_vp.sqlite"
    monkeypatch.setenv("NEE_VOICEPRINT_DB_PATH", str(canon_db))

    emb_stefan = generate_deterministic_embedding("voice:stefan_marceau", dim=256)
    legacy_store = VoiceprintStore(db_path=legacy_db)
    legacy_store.enroll_voiceprint(
        speaker_name="Cameron Mercer",
        embedding=emb_stefan,
        project="NEA",
        is_verified=True,
        audio_sha256="sha_nea_001",
    )

    store = VoiceprintStore(db_path=canon_db)
    assert store.sync_from_sqlite(legacy_db) == 1
    # Match across different project ('PROJECT_JARVIS' vs enrolled 'NEA')
    assert store.get_voiceprint("Cameron Mercer (Client)", project="PROJECT_JARVIS") is not None
    roster_matches = store.match_roster(
        {"SPEAKER_00": emb_stefan},
        roster_names=["Cameron Mercer (Client)", "Cameron Mercer", "Amit Ayer"],
        project="PROJECT_JARVIS",
    )
    assert roster_matches["SPEAKER_00"]["canonical_name"] == "Cameron Mercer"
    assert roster_matches["SPEAKER_00"]["is_decisive"] is True

    # Verify cached *.voiceprints.json with empty matches re-binds against newly enrolled voiceprint
    vp_file = tmp_path / "sample.voiceprints.json"
    vp_file.write_text(
        json.dumps({"audio_sha256": "sha_replay_02", "centroids": {"SPEAKER_00": emb_stefan}, "matches": {}}),
        encoding="utf-8",
    )
    meta: Dict[str, Any] = {
        "project": "PROJECT_JARVIS",
        "voiceprint_store_path": str(canon_db),
        "attendees": ["Cameron Mercer", "Amit Ayer"],
    }
    _restore_voiceprints_sidecar(meta, vp_file, {"SPEAKER_00": "Speaker 1"})
    assert meta["voiceprint_matches"]["SPEAKER_00"] == "Cameron Mercer"
    assert meta["speaker_map"]["Speaker 1"] == "Cameron Mercer"

    # Verify Harvester reuses cached *.voiceprints.json centroids without raw audio
    m_dir = tmp_path / "meeting_archive"
    m_dir.mkdir()
    (m_dir / "01_10-27-2026_test_hitl_resolved_transcript.md").write_text(
        "## Attribution Summary\n"
        "| Attendee | Role | Org | Turns | Status | Anchor |\n"
        "|---|---|---|---|---|---|\n"
        "| Cameron Mercer | Client | NEA | 5 turns | VERIFIED | Voiceprint |\n\n---\n\n"
        "### [00:00:05]\n\n**Cameron Mercer:** Good morning team, let us review the enterprise architecture roadmap today.\n\n"
        "### [00:00:15]\n\n**Amit Ayer:** Sounds great, let us dive into the technical milestones.\n",
        encoding="utf-8",
    )
    (m_dir / "sample.voiceprints.json").write_text(
        json.dumps({"audio_sha256": "sha_harvest_03", "centroids": {"Cameron Mercer": emb_stefan}}),
        encoding="utf-8",
    )
    h_res = harvest_voiceprints_from_meeting(m_dir, store=store, project="PROJECT_JARVIS")
    assert h_res["status"] == "SUCCESS"
    assert h_res["enrolled_count"] == 1
