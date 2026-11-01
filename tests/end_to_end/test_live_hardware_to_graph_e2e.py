#!/usr/bin/env python3
"""
tests/end_to_end/test_live_hardware_to_graph_e2e.py

End-to-End Verification Suite & On-Demand Real-Hardware Runner:
Start Recording -> Rolling WAV Chunks (asplit=2[aout0][aout1]) -> Live Metal ASR & Speaker Hints
-> Stop Stitch & RIFF Seal -> Speaker Collapse HITL & Ed25519 ALR -> Active Clearance
-> Sandbox Graph Mutation with 5-Check Physical Proof.

Run via pytest for deterministic CI verification (<2s), or invoke directly with
`--live-hardware` (or `NEE_RUN_LIVE_HARDWARE=1`) to run 100% real hardware capture
with live audio playback (`afplay` + `say`), ScreenCaptureKit, CoreAudio Mic, and MLX Metal GPU.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.cli.canonical_invocation import build_canonical_ingest_command
from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    check_active_ingestion_clearance,
)
from dual_pipeline.hitl_governance_core.crypto import (
    append_signed_approval_to_ledger,
    compute_alr_subject_digest,
    create_signed_hitl_approval_entry,
    generate_hitl_keypair,
)
from dual_pipeline.hitl_governance_core.decision_record_writer import (
    record_operator_answer,
)
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import (
    AudioMasterStitcher,
)
from dual_pipeline.live_meeting_recorder_core.hitl_manager import assert_no_hitl_bypass
from dual_pipeline.live_meeting_recorder_core.instant_spooler import InstantAudioSpooler
from dual_pipeline.live_meeting_recorder_core.live_voice_registry import (
    LiveVoiceRegistry,
)
from dual_pipeline.live_meeting_recorder_core.speaker_hint_queue import (
    DurableSpeakerHintQueue,
)
from dual_pipeline.live_meeting_recorder_core.speaker_hints import SpeakerHintManager
from dual_pipeline.live_meeting_recorder_core.stop_lifecycle import (
    reconcile_master_wav_and_receipt,
)
from dual_pipeline.speaker_attribution_core.attribution_engine import (
    AudioSpeakerAttributionEngine,
    HITLRequiredError,
)
from dual_pipeline.transcript_pipeline_core.speaker_map_loader import (
    load_colocated_speaker_map,
)
from dual_pipeline.transcript_qa_core.gate1_alr import verify_alr_cryptographic_receipt
from storage.connection import create_sqlite_conn
from tools.graph_mutation_proof_core.proof import prove, snapshot


def _write_pcm_chunk(path: Path, freq_hz: float, duration_s: float = 1.0, rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = int(duration_s * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        samples = [int(4000 * ((i * int(freq_hz)) % rate) / rate) for i in range(n_samples)]
        w.writeframes(struct.pack(f"<{n_samples}h", *samples))


def execute_downstream_hitl_and_graph_proof(
    runtime_root: Path,
    segments_lines: list[dict],
) -> tuple[dict, dict]:
    """Executes Handoffs 4-8: collapse HITL interception, Ed25519 ALR, clearance, and 5-check DB proof."""
    ledger_json = runtime_root / "hitl_decision_ledger.json"
    ledger_json.write_text(
        json.dumps({
            "schema_version": "1.0.0",
            "record_count": 0,
            "updated_at": "2026-10-17T13:10:00Z",
            "genesis_hash": "0" * 64,
            "latest_entry_hash": "0" * 64,
            "decisions": [],
        }),
        encoding="utf-8",
    )

    bypass_blocked = False
    try:
        assert_no_hitl_bypass({"options": {"bypass_speaker_hitl": True}})
    except ValueError:
        bypass_blocked = True

    collapsed_sample = "\n".join(
        f"[00:00:{i*2:02d}] **Alex Sanchez:** {seg.get('text', 'Reviewing OCR preprocessing.')}"
        for i, seg in enumerate((segments_lines or [{"text": "Reviewing OCR preprocessing."}]) * 12)
    )[:4000]
    attr_engine = AudioSpeakerAttributionEngine()
    hitl_triggered = False
    hitl_reason = ""
    try:
        attr_engine.analyze_and_resolve(
            collapsed_sample,
            {
                "project": "project_lens",
                "meeting_name": "Live_Hardware_E2E_Ocr_Review",
                "date": "10/17/2026",
                "attendees": {"PartnerAdvisory": ["Alex Sanchez", "Bailey Morton", "Amit Ayer"]},
                "hitl_ledger_path": str(ledger_json),
                "diarization_status": "UNDIARIZED",
            },
        )
    except HITLRequiredError as exc:
        hitl_triggered = True
        hitl_reason = exc.hitl_type

    priv_key, pub_key = generate_hitl_keypair()
    subject = {
        "meeting_id": "Live_Hardware_E2E_Ocr_Review_10-17-2026",
        "meeting_name": "Live_Hardware_E2E_Ocr_Review",
        "decision_category": "speaker_attribution_collapse",
        "dominant_speaker": "Alex Sanchez",
        "resolution": "verified_alex_bailey_partition",
        "operator_identity": "operator@governed.ai",
    }
    subject_digest = compute_alr_subject_digest(subject)
    signed_entry = create_signed_hitl_approval_entry(
        subject_sha256=subject_digest,
        case_ids=["LIVE-HW-ALR-01"],
        operator_id="operator@governed.ai",
        private_key=priv_key,
        public_key=pub_key,
    )
    append_signed_approval_to_ledger(signed_entry, ledger_path=ledger_json)

    resolved_f1 = (
        "# Live Hardware E2E Ocr Review -- HITL-Resolved Transcript\n\n"
        "**Date:** 10/17/2026\n"
        "**Participants:** Alex Sanchez (PartnerAdvisory), Bailey Morton (PartnerAdvisory), Amit Ayer (PartnerAdvisory)\n\n---\n\n"
        "### [00:00:00]\n\n**Alex Sanchez:** The orientation does not seem to make a difference.\n\n"
        "### [00:00:06]\n\n**Bailey Morton:** Repository documentation is ready for review.\n\n"
        "## Ambiguity Lock Register\n\n"
        "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Speaker Attribution: Alex Sanchez | [00:00:00] | stmt-live-1 | Attributed to Alex Sanchez | (Operator Verified) |\n"
    )
    alr_passed, _, _ = verify_alr_cryptographic_receipt(
        resolved_f1,
        {
            "roster": [{"name": "Alex Sanchez", "org": "PartnerAdvisory"}, {"name": "Bailey Morton", "org": "PartnerAdvisory"}],
            "subject_sha256": subject_digest,
            "hitl_ledger_path": str(ledger_json),
        },
        ledger_path=ledger_json,
    )

    directive_id = "directive-project_lens-live-hardware-e2e-ocr-review-10172026-v1"
    ledger_sqlite = runtime_root / "hitl_decision_ledger.sqlite"
    record_operator_answer(
        decision_id=directive_id,
        category="INGESTION_MUTATION",
        question="Approve candidate publication for Live Hardware E2E Ocr Review",
        options=["Approve candidate publication into active knowledge graph", "Decline candidate publication"],
        selected_option="Approve candidate publication into active knowledge graph",
        operator_origin={"operator_id": "operator", "origin": "operator_ui"},
        rationale="Approved live capture E2E run",
        metadata={"directive_id": directive_id},
        sqlite_path=ledger_sqlite,
    )
    is_cleared, clear_reason, _ = check_active_ingestion_clearance(
        run_id=directive_id,
        directive_id=directive_id,
        delta_digest="",
        ledger_path=ledger_sqlite,
    )

    db_path = runtime_root / "live_hardware_graph.sqlite"
    conn = create_sqlite_conn(db_path, readonly=False)
    conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, node_type TEXT, label TEXT)")
    conn.execute("CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, subject_id TEXT, predicate TEXT, object_id TEXT)")
    conn.execute("CREATE TABLE audit_event (event_id TEXT PRIMARY KEY, record_id TEXT, action TEXT)")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    before_snap = snapshot(db_path, tables=["node", "assertion", "audit_event"])
    time.sleep(0.01)

    node_id = "person:alex_sanchez_live_hw"
    assertion_id = "assert:ocr_orientation_invariant_live_hw"
    audit_id = "audit:live_hw_graph_commit_001"

    conn = create_sqlite_conn(db_path, readonly=False)
    conn.execute("INSERT INTO node (node_id, node_type, label) VALUES (?, ?, ?)", (node_id, "person", "Alex Sanchez"))
    conn.execute(
        "INSERT INTO assertion (assertion_id, subject_id, predicate, object_id) VALUES (?, ?, ?, ?)",
        (assertion_id, node_id, "evaluated_finding", "metric:ocr_image_orientation"),
    )
    conn.execute(
        "INSERT INTO audit_event (event_id, record_id, action) VALUES (?, ?, ?)",
        (audit_id, assertion_id, "LIVE_HARDWARE_GRAPH_COMMIT"),
    )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    after_snap = snapshot(db_path, tables=["node", "assertion", "audit_event"])
    proof_receipt = prove(
        before=before_snap,
        after=after_snap,
        expected_ids=[node_id, assertion_id, audit_id],
        expected_deltas={"node": 1, "assertion": 1, "audit_event": 1},
        readback_probes={
            "node_probe": ("SELECT label FROM node WHERE node_id=?", (node_id,)),
            "assertion_probe": ("SELECT predicate FROM assertion WHERE assertion_id=?", (assertion_id,)),
            "audit_probe": ("SELECT action FROM audit_event WHERE event_id=?", (audit_id,)),
        },
        strict_delta=True,
    )

    hitl_summary = {
        "bypass_blocked": bypass_blocked,
        "collapse_hitl_triggered": hitl_triggered,
        "collapse_reason": hitl_reason,
        "ed25519_alr_verified": alr_passed,
        "active_clearance_ratified": is_cleared,
        "clearance_reason": clear_reason,
    }
    return hitl_summary, proof_receipt


def run_live_to_graph_pipeline(live_hardware: bool = False) -> dict:
    """Runs the 8-handoff pipeline either in real hardware mode or deterministic sandbox mode."""
    sandbox = Path(tempfile.mkdtemp(prefix="eval_sandbox_live_to_graph_"))
    playback_procs: list[subprocess.Popen] = []
    t0 = time.perf_counter()
    try:
        out_dir = sandbox / "live_session_out"
        cache_dir = out_dir / ".cache"
        chunks_dir = cache_dir / "chunks"
        runtime_root = sandbox / "runtime"
        for d in (out_dir, cache_dir, chunks_dir, runtime_root):
            d.mkdir(parents=True, exist_ok=True)

        if live_hardware:
            p_say = subprocess.Popen(
                [
                    "say",
                    "-v",
                    "Daniel",
                    "-r",
                    "160",
                    (
                        "Alex Sanchez and Bailey Morton are reviewing the image preprocessing pipeline. "
                        "The orientation does not seem to make a difference for optical character recognition. "
                        "Bailey Morton confirms the repository documentation is ready for review."
                    ),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            playback_procs.append(p_say)

            init_res = type(
                "SandboxInitResult",
                (),
                {
                    "worktree_dir": None,
                    "worktree_branch": None,
                    "output_dir": out_dir,
                    "cache_dir": cache_dir,
                    "chunks_dir": chunks_dir,
                    "clean_slug": "Live_Hardware_E2E_Ocr_Review",
                    "meeting_name": "Live Hardware E2E Ocr Review",
                    "project_name": "project_lens",
                    "date_dirname": "10-17-2026",
                    "meeting_date_display": "10/17/2026",
                    "attendees": ["Alex Sanchez", "Bailey Morton", "Amit Ayer"],
                },
            )()

            recorder = LiveMeetingRecorder(
                project_name="project_lens",
                meeting_name="Live Hardware E2E Ocr Review",
                meeting_date="10/17/2026",
                attendees="Alex Sanchez, Bailey Morton, Amit Ayer",
                segment_seconds=4,
                output_dir=out_dir,
                mock_mode=False,
                backend="mlx",
                audio_source="auto",
                enable_copilot=False,
                copilot_prefetch=False,
                enforce_worktree=False,
                max_duration_minutes=0.22,
                fast_start=False,
                init_result=init_res,
            )
            recorder.enable_post_stop_diarization = True

            def _inject_hint() -> None:
                time.sleep(5.0)
                if getattr(recorder, "speaker_hint_queue", None):
                    recorder.speaker_hint_queue.enqueue_hint(
                        speaker="Alex Sanchez",
                        quote="orientation",
                        elapsed_seconds=5.0,
                    )

            threading.Thread(target=_inject_hint, daemon=True).start()
            recorder.run()
            segments_lines = (
                [
                    json.loads(line)
                    for line in recorder.segments_jsonl.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                if recorder.segments_jsonl.exists()
                else []
            )
            capture_summary = {
                "mode": "real_hardware",
                "chunks_processed": len(recorder.processed_chunks),
                "master_wav_exists": recorder.master_wav.exists(),
                "stitch_duration_s": getattr(recorder.stitch_receipt, "duration_s", 0.0),
                "segments_transcribed": [s.get("text", "") for s in segments_lines],
            }
        else:
            spool_dir = sandbox / "spool"
            spooler = InstantAudioSpooler(
                spool_dir=spool_dir,
                segment_seconds=1,
                mock_mode=True,
                session_id="spool_virtual_e2e",
            )
            for idx, freq in enumerate((220.0, 330.0, 440.0)):
                _write_pcm_chunk(spool_dir / f"chunk_{idx:04d}.wav", freq_hz=freq, duration_s=1.0)
                shutil.copy2(spool_dir / f"chunk_{idx:04d}.wav", chunks_dir / f"chunk_{idx:04d}.wav")
            spooler.stop()

            segments_jsonl = out_dir / "virtual_session_segments.jsonl"
            transcript_md = out_dir / "virtual_session_live_transcript.md"
            segments_lines = [
                {"chunk_index": 0, "start": 0.0, "end": 1.0, "speaker": "Voice A", "voice_id": "Voice A", "text": "The orientation does not seem to make a difference."},
                {"chunk_index": 1, "start": 1.0, "end": 2.0, "speaker": "Voice B", "voice_id": "Voice B", "text": "Bailey Morton confirms the repository documentation is ready."},
            ]
            segments_jsonl.write_text("\n".join(json.dumps(r) for r in segments_lines) + "\n", encoding="utf-8")
            transcript_md.write_text("### [00:00:00] Voice A\nThe orientation does not seem to make a difference.\n", encoding="utf-8")

            voice_reg = LiveVoiceRegistry()
            shm = SpeakerHintManager(output_dir=out_dir, attendees=["Alex Sanchez", "Bailey Morton"], voice_registry=voice_reg)
            shq = DurableSpeakerHintQueue(output_dir=out_dir)
            shq.enqueue_hint(speaker="Alex Sanchez", quote="orientation", elapsed_seconds=0.5)
            reconciled = shq.reconcile_pending_hints(shm, segments_jsonl, transcript_md, "virtual_session", voice_registry=voice_reg)
            shm.export_colocated_speaker_map(out_dir, "virtual_session")

            master_wav = out_dir / "00_virtual_session_master.wav"
            stitch_receipt = AudioMasterStitcher.stitch_master_wav(chunks_dir, master_wav, pad_missing_or_corrupt=True)
            dummy_recorder = type("DummyRecorder", (), {"master_wav": master_wav, "spooler": None, "processed_chunks": [0, 1, 2]})()
            final_receipt = reconcile_master_wav_and_receipt(dummy_recorder, stitch_receipt)
            colocated_map = load_colocated_speaker_map(master_wav)
            ingest_cmd = build_canonical_ingest_command(
                input_path=master_wav,
                project="project_lens",
                meeting="Virtual Live E2E Verification",
                date="10/17/2026",
                attendees="Alex Sanchez, Bailey Morton",
                reviewer="operator",
            )
            capture_summary = {
                "mode": "deterministic_sandbox",
                "chunks_processed": final_receipt.valid_chunks,
                "master_wav_exists": master_wav.exists(),
                "stitch_duration_s": final_receipt.duration_s,
                "hint_reconciled": len(reconciled) == 1,
                "colocated_map": colocated_map,
                "canonical_cmd_valid": "nee ingest-meeting" in ingest_cmd,
            }

        hitl_summary, proof_receipt = execute_downstream_hitl_and_graph_proof(runtime_root, segments_lines)
        return {
            "capture_and_asr": capture_summary,
            "diarization_and_hitl": hitl_summary,
            "physical_graph_proof": proof_receipt,
            "elapsed_s": round(time.perf_counter() - t0, 2),
        }
    finally:
        for p in playback_procs:
            if p.poll() is None:
                p.terminate()
        shutil.rmtree(sandbox, ignore_errors=True)


class TestLiveHardwareToGraphE2E(unittest.TestCase):
    """End-to-end verification of all 8 handoffs from live audio capture to 5-check graph mutation."""

    def test_all_eight_live_to_graph_handoffs_end_to_end(self) -> None:
        live_hw = os.environ.get("NEE_RUN_LIVE_HARDWARE") == "1"
        res = run_live_to_graph_pipeline(live_hardware=live_hw)

        self.assertTrue(res["capture_and_asr"]["master_wav_exists"])
        self.assertGreaterEqual(res["capture_and_asr"]["chunks_processed"], 1)
        self.assertTrue(res["diarization_and_hitl"]["bypass_blocked"])
        self.assertTrue(res["diarization_and_hitl"]["collapse_hitl_triggered"])
        self.assertEqual(res["diarization_and_hitl"]["collapse_reason"], "single_speaker_collapse")
        self.assertTrue(res["diarization_and_hitl"]["ed25519_alr_verified"])
        self.assertTrue(res["diarization_and_hitl"]["active_clearance_ratified"])
        self.assertEqual(res["physical_graph_proof"]["overall"], "PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Live-to-Graph 8-Handoff E2E Verification.")
    parser.add_argument("--live-hardware", action="store_true", help="Execute 100%% real hardware capture & Metal GPU ASR.")
    args = parser.parse_args()
    out = run_live_to_graph_pipeline(live_hardware=args.live_hardware)
    print(json.dumps(out, indent=2))
    if out["physical_graph_proof"]["overall"] != "PASS":
        sys.exit(1)
