#!/usr/bin/env python3
"""Exhaustive End-to-End 16-Pipeline, 29-Scenario (Primary Copilot & Mock Live Recording) Verification Agent.

Executes all 16 canonical pipelines (29 scenarios) in an isolated hermetic sandbox:
  1. P1_AUDIO (Audio intake, L1 attribution, X1-X5 gates, anomaly quarantine, Tier-1 replay)
  2. P2_MEETING_TRANSCRIPT (Verbatim transcript intake & 01/02/03 deployment)
  3. P3_MEETING_NOTES (Curated notes intake, omitting 01 verbatim transcript)
  4. P4_DOCUMENT (Document [03] intake, stage checkpointing & LocalPreEgressSanitizer)
  5. P5_LIVE_RECORDER_AND_COPILOT (17-field IPC round-trip, chronology, stop roster & copilot context)
  6. P6_GRAPH_AND_SIDECAR_16_TABLE_LANDING (GovernedGraphWriter, UWG promotion, 5-check proof, 16-table parity, crash rollback)
  7. P7_QUERY_ROUTER_RETRIEVAL_AND_GATES (Query router, hybrid retrieval reachability, GroundedDeliveryGuard, 11-gate BGE-M3)
  8. P8_DECISION_INTELLIGENCE_A2A_AND_LEARNING (DecisionHTTPServer active_graph health, A2ATaskLedger, HITL Bayesian learning)
  9. P9_INTAKE_STAGE_DAG_U0_TO_L4 (Batch admission manifest sealing, scope guard, MCP safe intake, L0 epistemic TTL & speaker hygiene)
 10. P10_DIARIZATION_DEFENSE_AND_CRYPTO_ALR (Single-speaker collapse >85% fail-closed halt & Ed25519 RFC-8785 ALR signature gate)
 11. P11_SEMANTIC_CACHE_AND_WAL_CONCURRENCY (Subgraph Merkle root invariance, replay sanitization & 16-worker WAL stress harness)
 12. P12_GRAPH_MAINTENANCE_SIGNAL_AND_WAREHOUSE (Temporal conflict supersession, graph freshness, GS-10 signal & warehouse JSONL sync)
 13. P13_STRATEGIC_WAVES_DIALECTICAL_AND_LEDGER (18 roles, 6 modalities, W1-W7 waves, 20% dialectical judge & Answer Ledger hash chain)
 14. P14_MCP_SERVERS_AND_ACTIVE_PAIR_AUDIT (All 5 repository MCP servers, 11-server registry & live 89-table read-only active pair audit)
 15. P15_MOCK_LIVE_RECORDING_STREAMING_AND_INFLECTION (Multi-chunk mock WAV streaming, question/pause inflections, hints, chat & C-19 quarantine)
 16. P16_COPILOT_SIX_INTENTS_SPEAK_GATE_AND_LEDGER (All 6 Copilot intents, argv normalization, clause grounding, speak gate & ledgers)
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.hash_cache import cached_sha256_file  # noqa: E402
from tools.e2e_scenario_agent_core import (  # noqa: E402
    ExhaustiveE2EAgentReport,
    ScenarioVerificationResult,
    build_agent_report,
    render_markdown_report,
    run_p1_audio_ingest_scenario,
    run_p1_tier1_cached_replay_scenario,
    run_p2_transcript_ingest_scenario,
    run_p3_notes_ingest_scenario,
    run_p4_document_and_firewall_scenario,
    run_p5_live_copilot_scenario,
    run_p5_live_recorder_ipc_and_stop_scenario,
    run_p6_atomic_crash_rollback_scenario,
    run_p6_graph_and_sidecar_16_table_scenario,
    run_p7_bge_m3_11_gate_non_compensating_scenario,
    run_p7_query_router_and_retrieval_reachability_scenario,
    run_p8_decision_api_and_a2a_ledger_scenario,
    run_p8_hitl_bayesian_learning_scenario,
    run_p9_batch_admission_and_manifest_scenario,
    run_p9_l0_speaker_qualifier_and_assumption_ttl_scenario,
    run_p10_diarization_collapse_halt_scenario,
    run_p10_ed25519_alr_signature_gate_scenario,
    run_p11_16_worker_wal_concurrency_stress_scenario,
    run_p11_semantic_cache_merkle_lifecycle_scenario,
    run_p12_supersession_and_graph_signal_scenario,
    run_p12_warehouse_export_and_facet_schema_scenario,
    run_p13_18_roles_6_modalities_and_dialectical_scenario,
    run_p13_answer_ledger_hash_chain_scenario,
    run_p14_five_mcp_servers_roundtrip_scenario,
    run_p14_live_active_pair_89_table_readonly_audit_scenario,
    run_p15_mock_live_buffer_and_c19_quarantine_scenario,
    run_p15_mock_live_multi_chunk_and_inflection_scenario,
    run_p16_copilot_six_intents_and_argv_normalization_scenario,
    run_p16_copilot_speak_gate_clause_grounding_and_ledgers_scenario,
)


def _resolve_active_production_digests() -> tuple[str, str]:
    """Return (graph_sha256, sidecar_sha256) of the active production pair without mutating it."""
    candidates = [
        ROOT / ".local" / "dual_pipeline" / "active_pair.json",
        ROOT.parent.parent / ".local" / "dual_pipeline" / "active_pair.json",
    ]
    for ap in candidates:
        if ap.is_file():
            try:
                data = json.loads(ap.read_text(encoding="utf-8"))
                gp = (ap.parent / str(data.get("graph_path", ""))).resolve()
                sp = (ap.parent / str(data.get("sidecar_path", ""))).resolve()
                g_sha = cached_sha256_file(gp) if gp.is_file() else str(data.get("graph_sha256", "absent"))
                s_sha = cached_sha256_file(sp) if sp.is_file() else str(data.get("sidecar_sha256", "absent"))
                return g_sha, s_sha
            except Exception:
                continue
    return "no-active-pair", "no-active-pair"


def run_e2e_pipeline_scenario_agent(
    *,
    run_id: str | None = None,
    output_dir: Path | None = None,
    sandbox_dir: Path | None = None,
) -> ExhaustiveE2EAgentReport:
    """Execute all 29 scenarios across all 16 pipelines in an isolated sandbox and emit sealed report."""
    effective_run_id = run_id or f"e2e-agent-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    pre_g_sha, pre_s_sha = _resolve_active_production_digests()

    def _execute_in_sandbox(sb_root: Path) -> ExhaustiveE2EAgentReport:
        scenarios: list[ScenarioVerificationResult] = [
            run_p1_audio_ingest_scenario(sb_root),
            run_p1_tier1_cached_replay_scenario(sb_root),
            run_p2_transcript_ingest_scenario(sb_root),
            run_p3_notes_ingest_scenario(sb_root),
            run_p4_document_and_firewall_scenario(sb_root),
            run_p5_live_recorder_ipc_and_stop_scenario(sb_root),
            run_p5_live_copilot_scenario(sb_root),
        ]
        r6a, proof, audit, gp, sp = run_p6_graph_and_sidecar_16_table_scenario(sb_root)
        r6b = run_p6_atomic_crash_rollback_scenario(sb_root)
        r7a = run_p7_query_router_and_retrieval_reachability_scenario(sb_root, gp, sp)
        r7b = run_p7_bge_m3_11_gate_non_compensating_scenario(sb_root)
        r8a = run_p8_decision_api_and_a2a_ledger_scenario(sb_root, gp)
        r8b = run_p8_hitl_bayesian_learning_scenario(sb_root)
        r9a = run_p9_batch_admission_and_manifest_scenario(sb_root)
        r9b = run_p9_l0_speaker_qualifier_and_assumption_ttl_scenario(sb_root)
        r10a = run_p10_diarization_collapse_halt_scenario(sb_root)
        r10b = run_p10_ed25519_alr_signature_gate_scenario(sb_root)
        r11a = run_p11_semantic_cache_merkle_lifecycle_scenario(sb_root)
        r11b = run_p11_16_worker_wal_concurrency_stress_scenario(sb_root)
        r12a = run_p12_supersession_and_graph_signal_scenario(sb_root, gp, sp)
        r12b = run_p12_warehouse_export_and_facet_schema_scenario(sb_root)
        r13a = run_p13_18_roles_6_modalities_and_dialectical_scenario(sb_root)
        r13b = run_p13_answer_ledger_hash_chain_scenario(sb_root, gp)
        r14a = run_p14_five_mcp_servers_roundtrip_scenario(sb_root, gp, sp)
        r14b = run_p14_live_active_pair_89_table_readonly_audit_scenario(sb_root)
        r15a = run_p15_mock_live_multi_chunk_and_inflection_scenario(sb_root)
        r15b = run_p15_mock_live_buffer_and_c19_quarantine_scenario(sb_root)
        r16a = run_p16_copilot_six_intents_and_argv_normalization_scenario(sb_root)
        r16b = run_p16_copilot_speak_gate_clause_grounding_and_ledgers_scenario(sb_root)
        scenarios.extend([
            r6a, r6b, r7a, r7b, r8a, r8b,
            r9a, r9b, r10a, r10b, r11a, r11b,
            r12a, r12b, r13a, r13b, r14a, r14b,
            r15a, r15b, r16a, r16b,
        ])

        post_g_sha, post_s_sha = _resolve_active_production_digests()
        return build_agent_report(
            run_id=effective_run_id,
            scenarios=scenarios,
            physical_proof=proof,
            sixteen_table_audit=audit,
            pre_graph_sha=pre_g_sha,
            post_graph_sha=post_g_sha,
            pre_sidecar_sha=pre_s_sha,
            post_sidecar_sha=post_s_sha,
        )

    if sandbox_dir is not None:
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        report = _execute_in_sandbox(sandbox_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="e2e_pipeline_agent_") as tmp:
            report = _execute_in_sandbox(Path(tmp))

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "manifest.json").write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (output_dir / "report.md").write_text(render_markdown_report(report), encoding="utf-8")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Exhaustive End-to-End 16-Pipeline, 29-Scenario (Primary Copilot & Mock Live Recording) Verification Agent"
    )
    parser.add_argument("--run-id", default=None, help="Explicit run identifier")
    parser.add_argument("--output-dir", default=None, help="Directory to write manifest.json and report.md")
    parser.add_argument("--json", action="store_true", help="Emit JSON report to stdout")
    args = parser.parse_args(argv)

    out_path = Path(args.output_dir) if args.output_dir else None
    report = run_e2e_pipeline_scenario_agent(run_id=args.run_id, output_dir=out_path)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(render_markdown_report(report))
    return 0 if report.terminal_outcome == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
