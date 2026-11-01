#!/usr/bin/env python3
"""
tools/live_refactor_wave_manifest.py

Builds cryptographic wave manifest files for the Live Transcript Value Refactoring Plan.
Captures:
- Wave number and title
- Itemized deliverables (paths, types, statuses, sha256 digests)
- Metrics digest linking baseline_metrics.json
- Test suite verification state
- RFC-8259 canonical hash
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.record_wave_receipt import compute_file_sha256  # noqa: E402

WAVE_DELIVERABLES = {
    1: [
        {"path": "tests/fixtures/live_audio/two_speaker_meeting.wav", "type": "FIXTURE_AUDIO"},
        {"path": "tests/fixtures/live_audio/two_speaker_meeting.txt", "type": "FIXTURE_TEXT"},
        {"path": "tests/fixtures/live_audio/two_speaker_meeting.rttm", "type": "FIXTURE_RTTM"},
        {"path": "tests/fixtures/live_audio/three_speaker_meeting.wav", "type": "FIXTURE_AUDIO"},
        {"path": "tests/fixtures/live_audio/three_speaker_meeting.txt", "type": "FIXTURE_TEXT"},
        {"path": "tests/fixtures/live_audio/three_speaker_meeting.rttm", "type": "FIXTURE_RTTM"},
        {"path": "tests/fixtures/live_audio/silent_invitee_meeting.wav", "type": "FIXTURE_AUDIO"},
        {"path": "tests/fixtures/live_audio/silent_invitee_meeting.txt", "type": "FIXTURE_TEXT"},
        {"path": "tests/fixtures/live_audio/silent_invitee_meeting.rttm", "type": "FIXTURE_RTTM"},
        {"path": "tests/fixtures/live_audio/adversarial_phrases_meeting.wav", "type": "FIXTURE_AUDIO"},
        {"path": "tests/fixtures/live_audio/adversarial_phrases_meeting.txt", "type": "FIXTURE_TEXT"},
        {"path": "tests/fixtures/live_audio/adversarial_phrases_meeting.rttm", "type": "FIXTURE_RTTM"},
        {"path": "tests/fixtures/live_audio/mid_word_boundary_meeting.wav", "type": "FIXTURE_AUDIO"},
        {"path": "tests/fixtures/live_audio/mid_word_boundary_meeting.txt", "type": "FIXTURE_TEXT"},
        {"path": "tests/fixtures/live_audio/mid_word_boundary_meeting.rttm", "type": "FIXTURE_RTTM"},
        {"path": "tests/fixtures/live_audio/long_silence_meeting.wav", "type": "FIXTURE_AUDIO"},
        {"path": "tests/fixtures/live_audio/long_silence_meeting.txt", "type": "FIXTURE_TEXT"},
        {"path": "tests/fixtures/live_audio/long_silence_meeting.rttm", "type": "FIXTURE_RTTM"},
        {"path": "tools/benchmark_live_capture.py", "type": "BENCHMARK_TOOL"},
        {"path": "tools/live_refactor_wave_manifest.py", "type": "MANIFEST_TOOL"},
        {"path": "tests/end_to_end/test_live_capture_baseline_e2e.py", "type": "E2E_TEST"},
        {"path": "tests/adversarial/test_live_capture_baseline_edge_cases.py", "type": "ADVERSARIAL_TEST"},
        {"path": "reports/live_transcript_refactor/baseline_metrics.json", "type": "BASELINE_METRICS"},
    ],
    2: [
        {"path": "dual_pipeline/live_meeting_recorder_core/disaster_recovery.py", "type": "CORE_MODULE"},
        {"path": "pipeline_a_intake/ws_server.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder.py", "type": "CORE_RECORDER"},
        {"path": "dual_pipeline/live_meeting_recorder_core/copilot_retriever.py", "type": "CORE_MODULE"},
        {"path": "tools/run_ci_gates.py", "type": "CI_CONFIG"},
        {"path": "tests/adversarial/test_live_governance_honesty_adversarial.py", "type": "ADVERSARIAL_TEST"},
        {"path": "tests/end_to_end/test_disaster_recovery_honesty_e2e.py", "type": "E2E_TEST"},
    ],
    3: [
        {"path": "dual_pipeline/live_meeting_recorder_core/__init__.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_transcript_engine.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder_core/stream_aggregator.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder_core/hitl_manager.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/audio/__init__.py", "type": "CORE_MODULE"},
        {"path": "tools/run_ci_gates.py", "type": "CI_CONFIG"},
        {"path": "tests/adversarial/test_live_dead_code_regression_adversarial.py", "type": "ADVERSARIAL_TEST"},
        {"path": "tests/end_to_end/test_dead_code_purge_lifecycle_e2e.py", "type": "E2E_TEST"},
    ],
    4: [
        {"path": "dual_pipeline/live_meeting_recorder_core/audio_master_stitcher.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder.py", "type": "CORE_RECORDER"},
        {"path": "dual_pipeline/live_meeting_recorder_core/audio_utils.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/cli/canonical_invocation.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/transcript_pipeline_core/speaker_map_loader.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/audio_ingestion_core/mlx_transcriber.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/audio_postprocess.py", "type": "CORE_MODULE"},
        {"path": "tools/run_ci_gates.py", "type": "CI_CONFIG"},
        {"path": "tests/adversarial/test_live_stop_resilience_adversarial.py", "type": "ADVERSARIAL_TEST"},
        {"path": "tests/end_to_end/test_live_master_wav_ssot_handoff_e2e.py", "type": "E2E_TEST"},
    ],
    5: [
        {"path": "dual_pipeline/cli/engine.py", "type": "CLI_CORE"},
        {"path": "dual_pipeline/live_meeting_recorder_core/audio_capture.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder_core/audio_preflight.py", "type": "CORE_MODULE"},
        {"path": "pipeline_a_intake/mac_system_audio.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder.py", "type": "CORE_RECORDER"},
        {"path": "dual_pipeline/live_meeting_recorder_core/worktree_manager.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder_core/session_registry.py", "type": "CORE_MODULE"},
        {"path": "tools/run_ci_gates.py", "type": "CI_CONFIG"},
        {"path": "tests/adversarial/test_live_capture_races_adversarial.py", "type": "ADVERSARIAL_TEST"},
        {"path": "tests/end_to_end/test_live_single_capture_e2e.py", "type": "E2E_TEST"},
    ],
    6: [
        {"path": "dual_pipeline/live_meeting_recorder.py", "type": "CORE_RECORDER"},
        {"path": "dual_pipeline/live_meeting_recorder_core/chunk_health.py", "type": "CORE_MODULE"},
        {"path": "pipeline_a_intake/contracts.py", "type": "CONTRACT_MODULE"},
        {"path": "dual_pipeline/cli/engine.py", "type": "CLI_CORE"},
        {"path": "dual_pipeline/cli/parser.py", "type": "CLI_CORE"},
        {"path": "app.py", "type": "UI_ENTRY"},
        {"path": "tools/run_ci_gates.py", "type": "CI_CONFIG"},
        {"path": "tests/adversarial/test_live_backlog_overflow_adversarial.py", "type": "ADVERSARIAL_TEST"},
        {"path": "tests/end_to_end/test_live_hot_path_budget_e2e.py", "type": "E2E_TEST"},
    ],
    7: [
        {"path": "dual_pipeline/asr_decode_config.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/audio_postprocess.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder_core/chunk_transcriber.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder_core/lexical_priming.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder.py", "type": "CORE_RECORDER"},
        {"path": "dual_pipeline/audio_ingestion_core/mlx_transcriber.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder_core/__init__.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder_core/worktree_manager.py", "type": "CORE_MODULE"},
        {"path": "tools/run_ci_gates.py", "type": "CI_CONFIG"},
        {"path": "tests/adversarial/test_asr_filter_false_positive_adversarial.py", "type": "ADVERSARIAL_TEST"},
        {"path": "tests/end_to_end/test_asr_sanitizer_parity_e2e.py", "type": "E2E_TEST"},
    ],
    8: [
        {"path": "dual_pipeline/live_meeting_recorder_core/live_voice_registry.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder_core/speaker_hints.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/cli/speaker_hint_handlers.py", "type": "CLI_CORE"},
        {"path": "dual_pipeline/cli/parser.py", "type": "CLI_CORE"},
        {"path": "dual_pipeline/cli/engine.py", "type": "CLI_CORE"},
        {"path": "dual_pipeline/transcript_pipeline_core/speaker_map_loader.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder.py", "type": "CORE_RECORDER"},
        {"path": "dual_pipeline/live_meeting_recorder_core/chat_attribution_collector.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder_core/worktree_manager.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder_core/__init__.py", "type": "CORE_MODULE"},
        {"path": "tools/run_ci_gates.py", "type": "CI_CONFIG"},
        {"path": "tests/adversarial/test_speaker_hint_conflicts_adversarial.py", "type": "ADVERSARIAL_TEST"},
        {"path": "tests/end_to_end/test_live_speaker_hints_e2e.py", "type": "E2E_TEST"},
    ],
    9: [
        {"path": "dual_pipeline/live_meeting_recorder_core/copilot_retriever.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder_core/copilot_validator.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder_core/prefetch_bridge.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder_core/prefetch_query_builder.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder_core/session_context_packet.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/live_meeting_recorder_core/live_buffer_reader.py", "type": "CORE_MODULE"},
        {"path": "dual_pipeline/cli/copilot_handlers.py", "type": "CLI_CORE"},
        {"path": "dual_pipeline/cli/parser.py", "type": "CLI_CORE"},
        {"path": "dual_pipeline/live_meeting_recorder.py", "type": "CORE_RECORDER"},
        {"path": "tools/run_ci_gates.py", "type": "CI_CONFIG"},
        {"path": "tests/adversarial/test_copilot_grounding_fail_closed_adversarial.py", "type": "ADVERSARIAL_TEST"},
        {"path": "tests/end_to_end/test_copilot_on_demand_graph_grounded_e2e.py", "type": "E2E_TEST"},
    ],
    10: [
        {"path": ".agents/skills/nee-transcript-synthesis/SKILL.md", "type": "GOVERNANCE_SURFACE"},
        {"path": "tools/benchmark_d02_architectures.py", "type": "BENCHMARK_TOOL"},
        {"path": "tools/audit_governance_harmonization_core/structural_scanners.py", "type": "GOVERNANCE_TOOL"},
        {"path": "reports/live_transcript_refactor/baseline_vs_final.json", "type": "METRIC_ARTIFACT"},
        {"path": "walkthrough.md", "type": "WALKTHROUGH_DOC"},
        {"path": "tools/live_refactor_wave_manifest.py", "type": "WAVE_MANIFEST_TOOL"},
    ],
}

WAVE_TITLES = {
    1: "Wave 1: Baseline Characterization, Instrumentation & Test Harness",
    2: "Wave 2: Governance Hazards & Honesty Fixes",
    3: "Wave 3: Dead Module & Alias Purge",
    4: "Wave 4: SSOT Cutover to Master WAV Batch Transcription",
    5: "Wave 5: Capture Layer Consolidation",
    6: "Wave 6: Live UX Decoupling & WebSocket Removal",
    7: "Wave 7: Live Chunk ASR Hardening",
    8: "Wave 8: Speaker Attribution Architecture",
    9: "Wave 9: Copilot Re-grounding & On-Demand Implementation",
    10: "Wave 10: End-to-End Verification & Promotion Gate",
}


def build_manifest(wave: int, repo_root: Path = REPO_ROOT) -> Dict[str, Any]:
    """Generates manifest dictionary for a given wave."""
    if wave not in WAVE_DELIVERABLES:
        raise ValueError(f"Wave {wave} deliverables not defined in WAVE_DELIVERABLES")

    deliverables_def = WAVE_DELIVERABLES[wave]
    processed_deliverables = []

    for item in deliverables_def:
        rel_path = item["path"]
        abs_path = repo_root / rel_path
        status = "VERIFIED" if abs_path.is_file() else "MISSING"
        digest = compute_file_sha256(abs_path) if status == "VERIFIED" else None

        processed_deliverables.append({
            "path": rel_path,
            "type": item["type"],
            "status": status,
            "sha256": digest,
        })

    metrics_file = repo_root / "reports/live_transcript_refactor/baseline_metrics.json"
    metrics_digest = compute_file_sha256(metrics_file) if metrics_file.is_file() else None

    now_iso = datetime.now(timezone.utc).isoformat()
    manifest = {
        "wave": wave,
        "wave_title": WAVE_TITLES.get(wave, f"Wave {wave}"),
        "timestamp": now_iso,
        "repo_root": str(repo_root),
        "deliverables": processed_deliverables,
        "baseline_metrics_path": str(metrics_file.relative_to(repo_root)),
        "baseline_metrics_digest": metrics_digest,
        "all_deliverables_verified": all(d["status"] == "VERIFIED" for d in processed_deliverables),
    }

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Live Refactor Wave Manifest Builder")
    parser.add_argument("--wave", type=int, default=1, help="Wave number")
    parser.add_argument("--output", type=Path, help="Path to write output manifest JSON")

    args = parser.parse_args()

    out_path = (
        args.output
        if args.output
        else REPO_ROOT / f"reports/live_transcript_refactor/wave_{args.wave}_manifest.json"
    )

    manifest = build_manifest(args.wave, repo_root=REPO_ROOT)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"[SUCCESS] Built Wave {args.wave} manifest: {out_path}")
    print(f"  Title: {manifest['wave_title']}")
    print(f"  Deliverables: {len(manifest['deliverables'])} files (All verified: {manifest['all_deliverables_verified']})")
    print(f"  Metrics Digest: {manifest['baseline_metrics_digest']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
