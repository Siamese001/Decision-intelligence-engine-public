"""End-to-end verification suite focused primarily on Copilot & Mock Live Recordings across the 16-Pipeline Agent."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import dual_pipeline.cli.copilot_handlers as copilot_handlers
from dual_pipeline.live_meeting_recorder_core.copilot_engine import execute_copilot_request
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import GraphLoadStatus
from tools.e2e_pipeline_scenario_agent import main as agent_cli_main, run_e2e_pipeline_scenario_agent
from tools.e2e_scenario_agent_core import (
    FOUR_SIDECAR_TABLES,
    TWELVE_GRAPH_TABLES,
    build_mock_copilot_retriever,
    compute_rfc8259_digest,
    run_p15_mock_live_buffer_and_c19_quarantine_scenario,
    run_p15_mock_live_multi_chunk_and_inflection_scenario,
    run_p16_copilot_six_intents_and_argv_normalization_scenario,
    run_p16_copilot_speak_gate_clause_grounding_and_ledgers_scenario,
)

EXPECTED_SIXTEEN_PIPELINES = {
    "P1_AUDIO",
    "P2_MEETING_TRANSCRIPT",
    "P3_MEETING_NOTES",
    "P4_DOCUMENT",
    "P5_LIVE_RECORDER_AND_COPILOT",
    "P6_GRAPH_AND_SIDECAR_16_TABLE_LANDING",
    "P7_QUERY_ROUTER_RETRIEVAL_AND_GATES",
    "P8_DECISION_INTELLIGENCE_A2A_AND_LEARNING",
    "P9_INTAKE_STAGE_DAG_U0_TO_L4",
    "P10_DIARIZATION_DEFENSE_AND_CRYPTO_ALR",
    "P11_SEMANTIC_CACHE_AND_WAL_CONCURRENCY",
    "P12_GRAPH_MAINTENANCE_SIGNAL_AND_WAREHOUSE",
    "P13_STRATEGIC_WAVES_DIALECTICAL_AND_LEDGER",
    "P14_MCP_SERVERS_AND_ACTIVE_PAIR_AUDIT",
    "P15_MOCK_LIVE_RECORDING_STREAMING_AND_INFLECTION",
    "P16_COPILOT_SIX_INTENTS_SPEAK_GATE_AND_LEDGER",
}


@pytest.mark.e2e
def test_mock_live_recording_multi_chunk_streaming_inflection_and_buffer_e2e(tmp_path: Path) -> None:
    """Primary E2E Test 1: Multi-chunk mock WAV recording, question/pause inflections, hints, chat, and C-19 buffer quarantine."""
    res_stream = run_p15_mock_live_multi_chunk_and_inflection_scenario(tmp_path)
    assert res_stream.passed is True
    assert res_stream.metrics["feedback_count"] >= 2
    assert "STOPPING_POINT_QUESTION" in res_stream.metrics["triggers"]
    assert "STOPPING_POINT_PAUSE" in res_stream.metrics["triggers"]
    assert Path(res_stream.verified_deliverables["transcript_md"]).is_file()
    assert Path(res_stream.verified_deliverables["copilot_jsonl"]).is_file()

    res_buf = run_p15_mock_live_buffer_and_c19_quarantine_scenario(tmp_path)
    assert res_buf.passed is True
    assert res_buf.metrics["focus_turns"] == 2
    assert res_buf.metrics["open_questions"] == 1


@pytest.mark.e2e
def test_copilot_six_intents_speak_gate_and_mock_live_session_execution_e2e(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Primary E2E Test 2: All 6 Copilot intents, execute_copilot_request over mock live session, speak gate, and ledgers."""
    res_intents = run_p16_copilot_six_intents_and_argv_normalization_scenario(tmp_path)
    assert res_intents.passed is True
    assert res_intents.metrics["intents_passed"] == 6
    assert all(res_intents.metrics["outcomes"].values())

    res_sg = run_p16_copilot_speak_gate_clause_grounding_and_ledgers_scenario(tmp_path)
    assert res_sg.passed is True
    assert res_sg.metrics["speak_tier"] == "T3"
    assert res_sg.metrics["chain_verified"] is True

    session_dir = tmp_path / "partner_google_nee" / "10-30-2026" / "2026-10-30_1500_Mock_Live_Copilot_Call"
    session_dir.mkdir(parents=True, exist_ok=True)
    turns = [
        {
            "chunk_id": 1,
            "speaker": "Stefanos Georgiades",
            "text": "We need to finalize SCADA Gateway Protocol intake review for Grid Modernization across 2 sprints.",
            "timestamp": "00:01:15",
            "c19_quarantined": False,
        },
        {
            "chunk_id": 2,
            "speaker": "Amit Ayer",
            "text": "Agreed, let us lock in the SCADA Gateway Protocol architecture review.",
            "timestamp": "00:01:45",
            "c19_quarantined": False,
        },
    ]
    (session_dir / "segments.jsonl").write_text("\n".join(json.dumps(t) for t in turns) + "\n", encoding="utf-8")
    retriever = build_mock_copilot_retriever()
    monkeypatch.setattr(
        copilot_handlers,
        "load_copilot_graph",
        lambda **kwargs: (GraphLoadStatus.LOADED_ACTIVE_PAIR, retriever._topology, retriever),
    )

    for intent_name, target_name in (
        ("respond", "Stefanos Georgiades"),
        ("next-questions", None),
        ("next-actions", None),
        ("graph-context", None),
        ("gaps", None),
    ):
        args = argparse.Namespace(
            intent=intent_name,
            target=target_name,
            topic="SCADA Gateway Protocol",
            request=None,
            query=None,
            prompt=None,
            words=45,
            meeting_dir=str(session_dir),
            runtime_root=tmp_path,
            engagement="partner_google_nee",
            session=None,
            json=True,
        )
        out = execute_copilot_request(args)
        assert out.get("status") == "PASS", f"Intent {intent_name} failed on mock live session: {out}"
        assert Path(out["answers_path"]).is_file()


@pytest.mark.e2e
def test_exhaustive_e2e_pipeline_scenario_agent_full_lifecycle(tmp_path: Path) -> None:
    """Verify all 16 pipelines and 29 scenarios pass end-to-end with 5-check proof and 16/89-table parity."""
    sandbox_dir = tmp_path / "sandbox"
    output_dir = tmp_path / "report_out"

    report = run_e2e_pipeline_scenario_agent(
        run_id="e2e-test-run-001",
        output_dir=output_dir,
        sandbox_dir=sandbox_dir,
    )

    assert report.terminal_outcome == "PASS"
    assert report.total_pipelines == 16
    assert report.total_scenarios == 29
    assert report.passed_scenarios == 29
    assert report.failed_scenarios == 0
    assert {s.pipeline_id for s in report.scenarios} == EXPECTED_SIXTEEN_PIPELINES
    assert all(s.passed for s in report.scenarios)
    assert report.zero_production_graph_mutation is True
    assert report.pre_prod_graph_sha256 == report.post_prod_graph_sha256
    assert report.pre_prod_sidecar_sha256 == report.post_prod_sidecar_sha256

    proof = report.physical_landing_proof
    assert proof is not None
    assert proof.all_five_checks_passed is True
    assert proof.file_changed is True
    assert proof.old_sha256 != proof.new_sha256
    assert proof.raw_bytes_verified_ids == ["as-e2e-canonical-01"]
    assert proof.raw_byte_offsets["as-e2e-canonical-01"] > 0
    assert proof.integrity_check_ok is True
    assert proof.foreign_key_violations == 0
    assert proof.fresh_readback_verified_ids == ["as-e2e-canonical-01"]

    audit = report.sixteen_table_audit
    assert audit is not None
    assert audit.passed is True
    assert audit.unmapped_edges_count == 0
    assert audit.dense_retrieval_parity is True
    assert audit.bound_graph_sha256_matches is True
    for g_tbl in TWELVE_GRAPH_TABLES:
        assert audit.graph_table_counts.get(g_tbl, 0) > 0
    for s_tbl in FOUR_SIDECAR_TABLES:
        assert audit.sidecar_table_counts.get(s_tbl, 0) > 0

    manifest_file = output_dir / "manifest.json"
    markdown_file = output_dir / "report.md"
    assert manifest_file.is_file()
    assert markdown_file.is_file()
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert manifest_data["canonical_digest"] == report.canonical_digest
    assert compute_rfc8259_digest(manifest_data) == report.canonical_digest
    md_text = markdown_file.read_text(encoding="utf-8")
    assert "Five-Check Physical Graph Mutation Proof" in md_text
    assert "Sixteen-Table GraphDB & Vector Sidecar Landing Audit" in md_text


@pytest.mark.e2e
def test_exhaustive_e2e_pipeline_scenario_agent_cli_json_entrypoint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify the CLI entrypoint exits 0 and emits valid RFC-8259 JSON with --json across all 16 pipelines."""
    out_dir = tmp_path / "cli_out"
    exit_code = agent_cli_main(["--run-id", "cli-e2e-001", "--output-dir", str(out_dir), "--json"])
    assert exit_code == 0

    captured = capsys.readouterr().out
    parsed = json.loads(captured)
    assert parsed["terminal_outcome"] == "PASS"
    assert parsed["total_pipelines"] == 16
    assert parsed["passed_scenarios"] == 29
    assert compute_rfc8259_digest(parsed) == parsed["canonical_digest"]
    assert parsed["sixteen_table_audit"]["dense_retrieval_parity"] is True
    assert parsed["physical_landing_proof"]["integrity_check_ok"] is True
