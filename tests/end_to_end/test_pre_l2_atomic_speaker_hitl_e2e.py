"""
tests/end_to_end/test_pre_l2_atomic_speaker_hitl_e2e.py

End-to-End verification for Wave 6:
1. Pre-L2 U0-Exit Atomic Speaker HITL Gate halts into AWAITING_SPEAKER_HITL
   before any Stage L2 LLM call when unresolved SPEAKER_XX, Unknown, collapse,
   or <= 20% ambiguity margin exists.
2. Partial speaker resolution sets diarization_status='PARTIAL_ACOUSTIC_RESOLVED'
   rather than 'DIARIZED_ACOUSTIC_RESOLVED' until ALL active clusters are resolved.
3. Timestamped live speaker hints (`elapsed_seconds`) and acoustic-cluster-bound
   hint propagation resolve the target cluster without overwriting intervening
   turns from other acoustic clusters.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.u0_live_artifacts import _hydrate_speaker_artifacts
from dual_pipeline.canonical_pipeline.u0_speaker_hints import resolve_pre_hitl_speaker_hints
from dual_pipeline.canonical_pipeline.u0_speaker_hitl_gate import (
    are_all_clusters_resolved,
    enforce_pre_l2_speaker_hitl_gate,
)
from dual_pipeline.canonical_pipeline.u0_hint_reconciler import reconcile_queued_hints_against_turns
from dual_pipeline.cli.speaker_hint_handlers import handle_speaker_hint
from dual_pipeline.exceptions import HITLRequiredError
from dual_pipeline.speaker_attribution_core.attribution_engine import AudioSpeakerAttributionEngine
from dual_pipeline.storage.hitl_sqlite_store import HITLSQLiteStore


def test_partial_speaker_map_sets_partial_status_and_blocks_pre_l2_gate(tmp_path: Path) -> None:
    """Verifies that mapping 1 of 3 clusters sets PARTIAL_ACOUSTIC_RESOLVED and halts at Pre-L2 gate."""
    meeting_dir = tmp_path / "meeting"
    meeting_dir.mkdir(parents=True, exist_ok=True)
    # 1 of 3 clusters resolved, 1 generic, 1 Unknown
    spk_map_file = meeting_dir / "test_sync_speaker_map.json"
    spk_map_file.write_text(
        json.dumps({
            "SPEAKER_00": "Amit Ayer",
            "SPEAKER_01": "SPEAKER_01",
            "SPEAKER_02": "Unknown",
        }),
        encoding="utf-8",
    )
    meta: dict = {"filename_slug": "test_sync"}
    _hydrate_speaker_artifacts(meta, [meeting_dir], "test_sync")
    assert meta["diarization_status"] == "PARTIAL_ACOUSTIC_RESOLVED"
    assert not are_all_clusters_resolved(meta["speaker_map"])

    transcript = (
        "**[00:05] Amit Ayer:** Welcome everyone to the architecture review.\n\n"
        "**[00:12] SPEAKER_01:** Let's look at the ingestion pipeline latency.\n\n"
        "**[00:20] Unknown:** I have the benchmark numbers ready.\n"
    )
    with pytest.raises(HITLRequiredError) as exc_info:
        enforce_pre_l2_speaker_hitl_gate(transcript=transcript, metadata=meta)

    assert meta.get("pipeline_status") == "AWAITING_SPEAKER_HITL"
    assert "SPEAKER_01" in exc_info.value.unmapped_speakers
    assert "Unknown" in exc_info.value.unmapped_speakers

    # Once all 3 clusters are resolved to human names, status transitions to DIARIZED_ACOUSTIC_RESOLVED
    spk_map_file.write_text(
        json.dumps({
            "SPEAKER_00": "Amit Ayer",
            "SPEAKER_01": "Alice Chen",
            "SPEAKER_02": "Bob Smith",
        }),
        encoding="utf-8",
    )
    meta_full: dict = {
        "filename_slug": "test_sync",
        "roster": [
            {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Director"},
            {"name": "Alice Chen", "org": "Client", "role": "VP Engineering"},
            {"name": "Bob Smith", "org": "Client", "role": "Architect"},
        ],
    }
    _hydrate_speaker_artifacts(meta_full, [meeting_dir], "test_sync")
    assert meta_full["diarization_status"] == "DIARIZED_ACOUSTIC_RESOLVED"
    resolved_transcript = (
        "**[00:05] Amit Ayer:** Welcome everyone to the architecture review.\n\n"
        "**[00:12] Alice Chen:** Let's look at the ingestion pipeline latency.\n\n"
        "**[00:20] Bob Smith:** I have the benchmark numbers ready.\n"
    )
    gate_res = enforce_pre_l2_speaker_hitl_gate(transcript=resolved_transcript, metadata=meta_full)
    assert gate_res["status"] == "PASS"
    assert meta_full.get("pre_l2_speaker_hitl_verified") is True


def test_timestamped_live_speaker_hint_and_cluster_scoped_propagation(tmp_path: Path) -> None:
    """Verifies elapsed_seconds persistence on live hints and cluster-scoped propagation without clobbering."""
    session_dir = tmp_path / "live_session"
    session_dir.mkdir(parents=True, exist_ok=True)
    segments_path = session_dir / "segments.jsonl"
    segs = [
        {"chunk_idx": 0, "start_sec": 5.0, "end_sec": 10.0, "voice_id": "Voice A", "speaker": "Voice A", "text": "We deployed the new schema."},
        {"chunk_idx": 1, "start_sec": 12.0, "end_sec": 18.0, "voice_id": "Voice B", "speaker": "Voice B", "text": "How did the migration perform in staging?"},
        {"chunk_idx": 2, "start_sec": 20.0, "end_sec": 26.0, "voice_id": "Voice A", "speaker": "Voice A", "text": "We deployed the new schema with zero downtime."},
    ]
    segments_path.write_text("\n".join(json.dumps(s) for s in segs) + "\n", encoding="utf-8")
    (session_dir / "session_manifest.json").write_text(
        json.dumps({
            "meeting": "Schema_Sync",
            "project": "test_proj",
            "attendees": ["Amit Ayer", "Sarah Jenkins"],
            "segments_jsonl": str(segments_path),
        }),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        quote="deployed the new schema",
        speaker="Sarah Jenkins",
        role="Lead Engineer",
        org="Client",
        meeting_dir=str(session_dir),
        session=None,
        active=False,
        force=False,
        remove_attendee=None,
        project="test_proj",
        elapsed_seconds=21.5,
        runtime_root=str(tmp_path),
        json=True,
    )
    res = handle_speaker_hint(args)
    assert res["status"] == "PASS"

    queue_file = session_dir / "speaker_hints_queue.json"
    assert queue_file.is_file()
    q_data = json.loads(queue_file.read_text(encoding="utf-8"))
    assert q_data[0]["session_elapsed_seconds"] == 21.5

    # Verify turn reconciliation propagates to Voice A turns without overwriting intervening Voice B turn
    raw_turns = [
        {"start_sec": 5.0, "voice_id": "Voice A", "speaker": "Voice A", "text": "We deployed the new schema."},
        {"start_sec": 12.0, "voice_id": "Voice B", "speaker": "Voice B", "text": "How did the migration perform in staging?"},
        {"start_sec": 20.0, "voice_id": "Voice A", "speaker": "Voice A", "text": "Latency dropped by forty percent."},
    ]
    updated_turns, _ = reconcile_queued_hints_against_turns(
        raw_turns,
        [{"quote": "deployed the new schema", "speaker_name": "Sarah Jenkins", "session_elapsed_seconds": 6.0}],
    )
    assert updated_turns[0]["speaker"] == "Sarah Jenkins"
    assert updated_turns[1]["speaker"] == "Voice B"  # Intervening turn from Voice B preserved!
    assert updated_turns[2]["speaker"] == "Sarah Jenkins"  # Same cluster Voice A propagated!


def test_attribution_engine_surfaces_unknown_clusters_under_ssot_20pct_margin(tmp_path: Path) -> None:
    """Verifies that 'Unknown' clusters are surfaced for attribution and SSOT 0.20 margin triggers HITL."""
    db_path = tmp_path / "hitl_ledger.sqlite"
    store = HITLSQLiteStore(db_path=db_path)
    engine = AudioSpeakerAttributionEngine(decision_store=store)
    assert abs(engine.margin_threshold - 0.20) < 1e-6

    transcript = (
        "## Transcript\n\n"
        "**[00:01] Unknown:** Let's review the quarterly cloud budget.\n\n"
        "**[00:05] Amit Ayer:** Sounds good, I have the spreadsheet open.\n\n"
        "**[00:10] Unknown:** We need to optimize the storage tier.\n\n"
        "**[00:15] Amit Ayer:** Agreed, let's archive cold snapshots.\n\n"
        "**[00:20] Unknown:** I will file the infrastructure ticket today.\n"
    )
    unmapped = engine._find_unmapped_generic_speakers(transcript, {"Speaker 1": "Amit Ayer"})
    assert any(u.lower() == "unknown" for u in unmapped)

    meta = {
        "project": "cloud_ops",
        "meeting_name": "Cloud Budget Review",
        "date": "10/29/2026",
        "roster": [
            {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Director"},
            {"name": "Alice Chen", "org": "Client", "role": "Cloud FinOps Lead"},
            {"name": "Bob Smith", "org": "Client", "role": "Platform Lead"},
        ],
    }
    with pytest.raises(HITLRequiredError) as exc_info:
        engine.analyze_and_resolve(transcript, meta, interactive_resolver=None, autonomous_if_decisive=True)

    assert exc_info.value.hitl_type == "speaker_attribution"
    assert len(exc_info.value.review_contexts) >= 1
    assert exc_info.value.context_digest != ""
