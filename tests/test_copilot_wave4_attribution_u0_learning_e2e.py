"""
tests/test_copilot_wave4_attribution_u0_learning_e2e.py

End-to-End verification for Wave 4:
1. Live Speaker Attribution & Role Override:
   - load_session_attendees merges metadata.json, session_manifest.json, and live speaker_hints.json role overrides.
   - seed_attendee_identities overrides stale graph node role fields (e.g., Robson vs Chad role disambiguation) and marks explicit HAS_ROLE assertions with is_priority=True.
   - reconcile_turns_with_hint_queue blocks [ROLE_HINT:...] from injecting synthetic dialogue turns.
   - DurableSpeakerHintQueue syncs RECONCILED CLI hints into LiveVoiceRegistry.
2. Stage U0 Post-Call Handoff & Tier-A Cache Sealing:
   - seed_master_asr_from_recorder seals Tier-A cache sidecars (.transcript_cache.json, .segments.json, .utterances.json, .voiceprints.json) when chunk coverage >= 85%.
   - hydrate_live_container_artifacts hydrates inline_chat_attributions.json, *copilot_feedback*.jsonl, and copilot_prefetch.
   - resolve_pre_hitl_speaker_hints substitutes speaker_map display aliases (Speaker 1, Voice A) in raw_transcript.
3. Closed-Loop Learning & Telemetry:
   - create_attempt_record and record_copilot_attempt populate latency_ms, stage_latencies_ms, age_seconds, temporal_decay_params, retrieval_seeds, and speaker_note_metrics, emitting AgentOutcomeEvents to LivePipelineIntegrator.
   - LivePipelineIntegrator.drain_to_cohort_items drains via O(1) byte-offset cursor.
   - evaluate_ledger_answers summarizes copilot_attempts.jsonl.
"""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from types import SimpleNamespace

from dual_pipeline.live_meeting_recorder_core.copilot_roster_bridge import (
    load_session_attendees,
    seed_attendee_identities,
)
from dual_pipeline.live_meeting_recorder_core.speaker_hint_matcher import reconcile_turns_with_hint_queue
from dual_pipeline.live_meeting_recorder_core.speaker_hint_queue import DurableSpeakerHintQueue
from dual_pipeline.live_meeting_recorder_core.live_voice_registry import LiveVoiceRegistry
from dual_pipeline.live_meeting_recorder_core.master_asr_seeder import seed_master_asr_from_recorder
from dual_pipeline.canonical_pipeline.u0_live_artifacts import hydrate_live_container_artifacts
from dual_pipeline.canonical_pipeline.u0_speaker_hints import resolve_pre_hitl_speaker_hints
from dual_pipeline.live_meeting_recorder_core.copilot_attempt_ledger import (
    create_attempt_record,
    record_copilot_attempt,
)
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
from dual_pipeline.learning_adapters.live_pipeline_integrator import LivePipelineIntegrator
from dual_pipeline.evals.answer_eval import evaluate_ledger_answers


def test_live_role_hint_overrides_stale_graph_role_and_blocks_synthetic_turn(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir(parents=True)

    # Initial metadata has stale or missing role for Robson and Chad
    (session_dir / "metadata.json").write_text(
        json.dumps({
            "attendees": [
                {"name": "Robson", "role": "Success Engineer", "org": "PartnerAdvisory"},
                {"name": "Chad", "role": "Participant", "org": "Google"},
            ]
        }),
        encoding="utf-8",
    )
    # Operator submits live role hints correcting Robson and Chad
    (session_dir / "speaker_hints.json").write_text(
        json.dumps([
            {
                "hint_id": "h_role_1",
                "speaker_name": "Robson",
                "quote": "[ROLE_HINT:Engagement Director]",
                "role": "Engagement Director",
                "org": "PartnerAdvisory",
                "attribution_type": "role_hint",
                "status": "APPLIED",
            },
            {
                "hint_id": "h_role_2",
                "speaker_name": "Chad",
                "quote": "[ROLE_HINT:Customer Success Engineer]",
                "role": "Customer Success Engineer",
                "org": "Google Cloud",
                "attribution_type": "role_hint",
                "status": "APPLIED",
            },
        ]),
        encoding="utf-8",
    )

    attendees = load_session_attendees(session_dir)
    by_name = {a["name"]: a for a in attendees}
    assert by_name["Robson"]["role"] == "Engagement Director"
    assert by_name["Chad"]["role"] == "Customer Success Engineer"

    # Seed into topology where Robson previously had a stale role in graph
    topology_nodes = {
        "person:robson": {
            "node_id": "person:robson",
            "canonical_name": "Robson",
            "display_name": "Robson",
            "node_type": "person",
            "role": "Success Engineer",
        }
    }
    inverted_index: dict[str, set[str]] = defaultdict(set)
    attendee_tokens: set[str] = set()
    attendee_node_ids: set[str] = set()
    allowed_nodes: set[str] = set()
    salient_assertions: dict[str, list[dict]] = defaultdict(list)
    topology_assertions: dict[str, dict] = {}

    seed_attendee_identities(
        attendees,
        topology_nodes,
        inverted_index,
        attendee_tokens,
        attendee_node_ids,
        allowed_nodes,
        salient_assertions,
        topology_assertions,
    )
    assert topology_nodes["person:robson"]["role"] == "Engagement Director"
    robson_role_asrt = salient_assertions["person:robson"][0]
    assert robson_role_asrt["is_priority"] is True
    assert "Engagement Director" in robson_role_asrt["claim_text"]

    # Ensure [ROLE_HINT:...] never creates a synthetic dialogue turn
    turns = [{"speaker": "Chad", "text": "Let us review the mobilization plan.", "turn_sec": 10.0}]
    reconciled_turns = reconcile_turns_with_hint_queue(turns, [session_dir], attendees=attendees)
    assert len(reconciled_turns) == 1
    assert all("[ROLE_HINT" not in t.get("text", "") for t in reconciled_turns)


def test_cli_hint_syncs_to_voice_registry_and_seals_tier_a_sidecars(tmp_path: Path) -> None:
    out_dir = tmp_path / "output" / "NEE" / "10-27-2026" / "google_sync"
    cache_dir = out_dir / ".cache"
    cache_dir.mkdir(parents=True)

    segments_jsonl = cache_dir / "01_10-27-2026_google_sync_segments.jsonl"
    segments_jsonl.write_text(
        json.dumps({"chunk_idx": 0, "start_sec": 0.0, "end_sec": 15.0, "voice_id": "Voice A", "speaker": "Voice A", "text": "We are accelerating the Spark migration."}) + "\n"
        + json.dumps({"chunk_idx": 1, "start_sec": 15.0, "end_sec": 30.0, "voice_id": "Voice B", "speaker": "Voice B", "text": "I will validate the mobilization readiness gates."}) + "\n",
        encoding="utf-8",
    )

    v_reg = LiveVoiceRegistry(attendees=["Chad", "Speaker_Alpha"])
    q = DurableSpeakerHintQueue(output_dir=out_dir, cache_dir=cache_dir)
    hint, _ = q.enqueue_hint(speaker="Chad", quote="validate the mobilization readiness gates")
    # Simulate out-of-band CLI reconciliation
    q.mark_reconciled(hint.hint_id, voice_id="Voice B", updated_segments_count=1)

    # Recorder poll calls reconcile_pending_hints -> syncs RECONCILED CLI hint into in-memory LiveVoiceRegistry
    q.reconcile_pending_hints(
        speaker_hint_manager=SimpleNamespace(voice_registry=v_reg),
        segments_jsonl_path=segments_jsonl,
        voice_registry=v_reg,
    )
    assert v_reg.get_speaker_map().get("Voice B") == "Chad"

    # Seed Tier-A sidecars on stop with >= 85% coverage
    master_wav = cache_dir / "00_10-27-2026_google_sync_master.wav"
    master_wav.write_bytes(b"RIFF0000WAVEfmt ")
    recorder = SimpleNamespace(
        output_dir=out_dir,
        cache_dir=cache_dir,
        clean_slug="google_sync",
        segments_jsonl=segments_jsonl,
        master_wav=master_wav,
        processed_chunks={"chunk_0000.wav", "chunk_0001.wav"},
        voice_registry=v_reg,
        speaker_hint_manager=None,
        whisper_model="mlx-community/whisper-large-v3-turbo",
        language="en",
        project="NEE",
        mock_mode=True,
        _acoustic_diarization_complete=False,
    )
    receipt = SimpleNamespace(
        master_wav_sha256="a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90",
        total_chunks=2,
        duration_s=30.0,
    )
    seeded = seed_master_asr_from_recorder(recorder, receipt)
    assert seeded is True
    sidecars = list(cache_dir.glob("*.transcript_cache.json"))
    assert len(sidecars) >= 1
    assert "Chad" in sidecars[0].read_text(encoding="utf-8")


def test_stage_u0_hydrates_copilot_feedback_and_substitutes_speaker_map(tmp_path: Path) -> None:
    container_dir = tmp_path / "output" / "NEE" / "10-27-2026" / "weekly_sync"
    cache_dir = container_dir / ".cache"
    cache_dir.mkdir(parents=True)

    (container_dir / "inline_chat_attributions.json").write_text(
        json.dumps([{"speaker_name": "Speaker_Alpha", "role": "Principal Architect", "quote": "Let us review Spark."}]),
        encoding="utf-8",
    )
    (cache_dir / "01_10-27-2026_weekly_sync_copilot_feedback.jsonl").write_text(
        json.dumps({"ledger_id": "ans_1", "suggested_script": "Confirm Spark scope for Q4."}) + "\n",
        encoding="utf-8",
    )
    (cache_dir / "session_manifest.json").write_text(
        json.dumps({
            "project": "NEE",
            "meeting": "Weekly Sync",
            "date": "10/27/2026",
            "copilot_prefetch": {"enabled": True, "packet_hash": "pkt_123", "total_items": 4},
        }),
        encoding="utf-8",
    )

    metadata: dict = {
        "input_path": str(container_dir / "01_10-27-2026_weekly_sync_live_transcript.md"),
        "speaker_map": {"Voice A": "Speaker_Alpha", "Speaker 1": "Speaker_Alpha"},
        "raw_transcript": "**Voice A** [00:01:00]: Let us review Spark.\n\n**Speaker 1** [00:02:00]: Agreed on timeline.",
    }
    (container_dir / "01_10-27-2026_weekly_sync_live_transcript.md").write_text(
        metadata["raw_transcript"], encoding="utf-8"
    )

    hydrated = hydrate_live_container_artifacts(metadata, metadata["input_path"])
    assert "inline_chat_attributions" in hydrated
    assert "copilot_feedback" in hydrated
    assert "copilot_prefetch" in hydrated
    assert metadata["pre_enrichment_roles"].get("Speaker_Alpha") == "Principal Architect"

    resolve_pre_hitl_speaker_hints(metadata)
    assert "Voice A" not in metadata["raw_transcript"]
    assert "Speaker 1" not in metadata["raw_transcript"]
    assert "**Speaker_Alpha**" in metadata["raw_transcript"]


def test_closed_loop_learning_telemetry_and_byte_offset_drain(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True)

    ctx = ConversationContextModel(focus_seconds=300.0)
    ctx.add_turn(
        speaker="Chad",
        text="I will deliver the mobilization test plan by Thursday.",
        timestamp="14:10:00",
        turn_sec=100.0,
    )
    ctx.add_turn(
        speaker="Speaker_Alpha",
        text="What is the status of our Spark cluster sizing?",
        timestamp="14:10:15",
        turn_sec=115.0,
    )

    rec = create_attempt_record(
        terminal_outcome="SUCCESS",
        terminal_reason="DELIVERED",
        request="respond to Chad",
        parsed_intent="respond",
        parsed_target="Chad",
        session_id="sess_w4",
        context_model=ctx,
        latency_ms=42.5,
        stage_latencies_ms={"retrieval_ms": 12.0, "verification_ms": 4.5, "render_ms": 26.0},
    )
    assert rec.latency_ms == 42.5
    assert rec.age_seconds == 0.0
    assert rec.speaker_note_metrics.get("commitments_count") == 1
    assert rec.temporal_decay_params["volatile_half_life_days"] == 30.0

    record_copilot_attempt(rec, output_dir=runtime_root, runtime_root=runtime_root)

    integrator = LivePipelineIntegrator(runtime_root=runtime_root)
    integrator.record_success(
        source_agent="LiveCopilotAgent",
        category="respond",
        details={"attempt_id": rec.attempt_id, "latency_ms": 42.5},
        run_id="sess_w4",
    )
    items_first = integrator.drain_to_cohort_items()
    assert len(items_first) >= 1
    cursor_data = json.loads(integrator.cursor_path.read_text(encoding="utf-8"))
    assert cursor_data["last_processed_byte_offset"] > 0

    # Second drain with no new events returns empty in O(1) via byte-offset seek
    assert integrator.drain_to_cohort_items() == []

    eval_res = evaluate_ledger_answers(runtime_root=runtime_root, session_id="sess_w4")
    assert eval_res["copilot_attempt_summary"]["total_attempts"] == 1
    assert eval_res["copilot_attempt_summary"]["success_count"] == 1
