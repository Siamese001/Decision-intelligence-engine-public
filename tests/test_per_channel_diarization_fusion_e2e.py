"""End-to-End Verification for Platform Per-Channel Diarization & MLX Lexical Fusion."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from dual_pipeline.audio_ingestion_core.mlx_transcriber import LocalMLXAudioTranscriber
from dual_pipeline.audio_ingestion_core.platform_transcript_fusion import (
    fuse_mlx_segments_with_platform_diarization,
    parse_platform_diarized_transcript,
)
from dual_pipeline.audio_ingestion_core.utterance_assembler import assemble_utterances


def test_parse_and_fuse_platform_per_channel_with_mlx_rapid_interjections() -> None:
    """Verifies per-channel speaker turns and rapid interjections are preserved with MLX lexical fusion."""
    raw_google_meet = (
        "00:05:10\n"
        "Robson N. Silva: We signed the $350K Infrastructure Automation SOW with Fallon for NextA.\n"
        "00:05:14\n"
        "Craig Finch: How many weeks are left on the Solar and Battery dashboard in Oracle unifier?\n"
        "00:05:18\n"
        "Robson N. Silva: Seven weeks.\n"
        "00:05:19\n"
        "Alex Brannan: Now five.\n"
        "00:05:21\n"
        "Rusty Gattmann: I will send a note to Puja on the Bungie reference.\n"
    )
    platform_turns = parse_platform_diarized_transcript(raw_google_meet)
    assert len(platform_turns) == 5
    assert [t["speaker"] for t in platform_turns] == [
        "Robson N. Silva",
        "Craig Finch",
        "Robson N. Silva",
        "Alex Brannan",
        "Rusty Gattmann",
    ]

    mlx_segments: List[Dict[str, Any]] = [
        {
            "start": 310.0,
            "end": 313.8,
            "speaker": "SPEAKER_00",
            "text": "We signed the $350K Infrastructure Automation SOW with PartnerAdvisory for Apex Global.",
        },
        {
            "start": 314.0,
            "end": 317.8,
            "speaker": "SPEAKER_00",
            "text": "How many weeks are left on the Solar and Battery dashboard in Oracle Unifier?",
        },
        {
            "start": 318.0,
            "end": 320.5,
            "speaker": "SPEAKER_00",
            "text": "Seven weeks. Now five.",
        },
        {
            "start": 321.0,
            "end": 325.0,
            "speaker": "SPEAKER_01",
            "text": "I will send a note to Pooja on the Bunge reference.",
        },
    ]

    fused = fuse_mlx_segments_with_platform_diarization(mlx_segments, platform_turns)
    assert len(fused) == 5
    assert fused[0]["speaker"] == "Robson N. Silva"
    assert "PartnerAdvisory" in fused[0]["text"] and "Apex Global" in fused[0]["text"]
    assert fused[1]["speaker"] == "Craig Finch"
    assert "Oracle Unifier" in fused[1]["text"]
    assert fused[2]["speaker"] == "Robson N. Silva"
    assert fused[2]["text"] == "Seven weeks."
    assert fused[3]["speaker"] == "Alex Brannan"
    assert fused[3]["text"] == "Now five."
    assert fused[4]["speaker"] == "Rusty Gattmann"
    assert "Pooja" in fused[4]["text"] and "Bunge" in fused[4]["text"]

    utterances = assemble_utterances(fused)
    assert len(utterances) == 5
    assert [u["speaker"] for u in utterances] == [
        "Robson N. Silva",
        "Craig Finch",
        "Robson N. Silva",
        "Alex Brannan",
        "Rusty Gattmann",
    ]
    assert all(u["timing_provenance"] == "platform_per_channel" for u in utterances)


def test_mlx_transcriber_diarize_and_align_uses_platform_fusion(tmp_path: Path) -> None:
    """Verifies LocalMLXAudioTranscriber._diarize_and_align fuses platform per-channel diarization."""
    transcriber = LocalMLXAudioTranscriber(mock=True)
    platform_md = (
        "### [00:01:00]\n\n"
        "**Rusty Gattmann:** Welcome to the NextA alignment with Fallon.\n\n"
        "### [00:01:04]\n\n"
        "**Robson N. Silva:** Seven weeks.\n\n"
        "### [00:01:05]\n\n"
        "**Craig Finch:** What is CGI Grid 360?\n"
    )
    p_file = tmp_path / "google_meet_transcript.md"
    p_file.write_text(platform_md, encoding="utf-8")

    sanitized_segments = [
        {"start": 60.0, "end": 63.5, "text": "Welcome to the Apex Global alignment with PartnerAdvisory."},
        {"start": 64.0, "end": 68.0, "text": "Seven weeks. What is CGI Grid 360?"},
    ]
    meta: Dict[str, Any] = {"platform_transcript_path": str(p_file)}
    aligned = transcriber._diarize_and_align(sanitized_segments, str(tmp_path / "dummy.wav"), meta)

    assert len(aligned) == 3
    assert meta["diarization_method"] == "platform_per_channel_mlx_fusion"
    assert meta["diarization_status"] == "DIARIZED_PLATFORM_PER_CHANNEL_RESOLVED"
    assert aligned[0]["speaker"] == "Rusty Gattmann"
    assert "Apex Global" in aligned[0]["text"] and "PartnerAdvisory" in aligned[0]["text"]
    assert aligned[1]["speaker"] == "Robson N. Silva"
    assert aligned[2]["speaker"] == "Craig Finch"

    formatted = transcriber._format_diarized_json({"segments": aligned}, metadata=meta)
    assert "**Rusty Gattmann** [00:01:00]:" in formatted
    assert "**Robson N. Silva** [00:01:04]: Seven weeks." in formatted
    assert "**Craig Finch** [00:01:05]: What is CGI Grid 360?" in formatted


def test_u0_companion_auto_hydration_and_phonetic_healing(tmp_path: Path) -> None:
    """Verifies Stage U0 auto-discovers companion Google Meet transcripts and IdentityResolver repairs homophones."""
    from dual_pipeline.canonical_pipeline.u0_live_artifacts import hydrate_live_container_artifacts
    from dual_pipeline.identity_resolver_core.resolver import IdentityResolver

    container = tmp_path / "output" / "partner_alliances" / "10-27-2026" / "Weekly_PartnerAdvisory_Gcp_Alignment"
    att_dir = container / "attachments"
    att_dir.mkdir(parents=True)

    meet_file = att_dir / "Oct 27, 2026 - Weekly Alignment - Transcript.txt"
    meet_file.write_text(
        "00:00:32\n"
        "Rusty Gattmann: This is the broader partnership with Fallon and NextA.\n"
        "00:00:35\n"
        "Amit Ayer: Hey Rusty and Robson, how is the Oracle unifier integration?\n"
        "00:00:38\n"
        "Robson N. Silva: Puja shared the Bungie architecture notes.\n",
        encoding="utf-8",
    )
    master_wav = container / "00_Weekly_PartnerAdvisory_Gcp_Alignment_master.wav"
    master_wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")

    meta: Dict[str, Any] = {"input_path": str(master_wav)}
    hydrated = hydrate_live_container_artifacts(meta, input_path=master_wav)
    assert "platform_transcript" in hydrated
    assert meta["platform_transcript_path"] == str(meet_file)
    assert meta["platform_speakers"] == ["Rusty Gattmann", "Amit Ayer", "Robson N. Silva"]

    resolver = IdentityResolver()
    repaired_lines, repairs, _ = resolver.normalize_phonetic_dialogue(
        [
            (1, "Rusty Gattmann", "Fallon and NextA reviewed the Bungie note with Puja on Oracle unifier."),
        ]
    )
    repaired_text = repaired_lines[0][2]
    assert "PartnerAdvisory" in repaired_text
    assert "Apex Global" in repaired_text
    assert "Bunge" in repaired_text
    assert "Pooja" in repaired_text
    assert "Oracle Unifier" in repaired_text
    assert len(repairs) >= 5


def test_harvest_voiceprints_from_platform_per_channel_transcript(tmp_path: Path) -> None:
    """Verifies voiceprint harvester enrolls 256-D speaker centroids from a companion platform transcript."""
    from dual_pipeline.audio_ingestion_core.voiceprint_harvester import harvest_voiceprints_from_meeting
    from dual_pipeline.audio_ingestion_core.voiceprint_store import VoiceprintStore, generate_deterministic_embedding

    m_dir = tmp_path / "output" / "partner_alliances" / "10-27-2026" / "Weekly_PartnerAdvisory_Gcp_Alignment"
    m_dir.mkdir(parents=True)
    wav_file = m_dir / "00_master_recording.wav"
    wav_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00")
    comp_file = m_dir / "google_meet_raw_transcript.txt"
    comp_file.write_text(
        "00:01:10\n"
        "Robson N. Silva: We have seven weeks left on the Solar and Battery dashboard.\n"
        "00:01:16\n"
        "Craig Finch: I will review the Identity-Aware Proxy architecture.\n",
        encoding="utf-8",
    )

    class _StubDiarizationEngine:
        def extract_cluster_centroids(self, audio_path: Any, turns: Any, **kwargs: Any) -> Dict[str, List[float]]:
            speakers = {t["speaker"] for t in turns}
            return {spk: generate_deterministic_embedding(f"stub:{spk}", dim=256) for spk in speakers}

    store = VoiceprintStore(db_path=tmp_path / "isolated_voiceprints.sqlite")
    res = harvest_voiceprints_from_meeting(
        meeting_dir=m_dir,
        store=store,
        diarization_engine=_StubDiarizationEngine(),
        project="PARTNER_ALLIANCES",
    )
    assert res["status"] == "SUCCESS"
    assert res["enrolled_count"] == 2
    enrolled_names = {e["canonical_name"] for e in res["enrolled"]}
    assert enrolled_names == {"Robson N. Silva", "Craig Finch"}

