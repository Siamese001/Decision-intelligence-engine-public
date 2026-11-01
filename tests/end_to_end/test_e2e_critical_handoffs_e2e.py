"""End-to-end verification tests for Wave 1 E2E Critical Functionality Handoffs (E2E-CRIT-01..04)."""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline.document_resume import FreshDocumentStages, open_stage_checkpoint
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.canonical_pipeline.transcript_resume import (
    _ACTIVE_TRANSCRIPT_CHECKPOINT,
    refresh_validated_transcript_checkpoint,
    run_synthesis_with_transcript_resume,
)
from dual_pipeline.checkpoint import PipelineCheckpointManager
from dual_pipeline.cli.executive_brief_handlers import handle_executive_brief_cli
from dual_pipeline.index_core.constants import SIDECAR_SCHEMA
from dual_pipeline.live_meeting_recorder_core.stop_lifecycle import (
    finalize_hints_and_attributions,
    migrate_all_chunk_sources_on_drain,
)
from tools.tier2_dedup_core.sow_duplicate_retirement import _ensure_missing_dense_vectors


def test_e2e_executive_brief_cli_forwards_horizon_and_resolves_db(tmp_path: Path, capsys) -> None:
    """E2E-CRIT-01: Verifies handle_executive_brief_cli forwards --horizon to generate_top_down_csuite_brief."""
    db_file = tmp_path / "test_graph.sqlite"
    with sqlite3.connect(db_file) as conn:
        conn.execute("CREATE TABLE canonical_graph_nodes (node_id TEXT PRIMARY KEY, name TEXT, namespace TEXT)")

    captured_kwargs: dict = {}

    def _fake_brief(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "executive_name": kwargs["stakeholder_name"],
            "audience": kwargs["audience"],
            "horizon": kwargs.get("horizon"),
            "suppress_l4_noise": kwargs.get("suppress_l4_noise"),
        }

    with patch(
        "dual_pipeline.cli.executive_brief_handlers.generate_top_down_csuite_brief",
        side_effect=_fake_brief,
    ):
        rc = handle_executive_brief_cli(
            [
                "--stakeholder",
                "Mark Lemasney",
                "--horizon",
                "grounded",
                "--audience",
                "CLIENT_SAFE",
                "--format",
                "json",
                "--db",
                str(db_file),
            ]
        )

    assert rc == 0
    assert captured_kwargs["horizon"] == "grounded"
    assert captured_kwargs["audience"] == "CLIENT_SAFE"
    assert captured_kwargs["suppress_l4_noise"] is True
    out = json.loads(capsys.readouterr().out)
    assert out["horizon"] == "grounded"


def test_e2e_transcript_resume_defers_until_exit_qa_and_document_stage_cache(tmp_path: Path) -> None:
    """E2E-CRIT-02: Verifies deferred transcript checkpoint sealing and FreshDocumentStages stage caching."""
    # 1. Document stage caching when enabled
    doc_ckpt = open_stage_checkpoint(
        tmp_path / "doc_stage.json",
        {"is_document": True, "cache_document_stages": True},
    )
    assert isinstance(doc_ckpt, FreshDocumentStages)
    assert not doc_ckpt.has_stage("U0")
    doc_ckpt.save_stage("U0", {"source_text": "doc body", "input_hash": "h123"})
    assert doc_ckpt.has_stage("U0")
    assert doc_ckpt.get_stage("U0") == {"source_text": "doc body", "input_hash": "h123"}
    doc_ckpt.clear()
    assert not doc_ckpt.has_stage("U0")

    # 2. Deferred transcript synthesis checkpoint until Stage X1-X3 exit QA succeeds
    ckpt_path = tmp_path / "transcript_resume.json"
    syn = SynthesisResult(output_1="Speaker A: Hello", output_2="Recap", output_3='{"nodes": []}')
    ev = QAEvaluationResult(
        passed=True,
        qa_report={"overall_verdict": "PASS", "all_gates_passed": True},
        judge_report={},
        final_decision={"status": "PASS"},
        authoritative_write_authorized=True,
    )
    meta = {
        "_transcript_checkpoint_path": str(ckpt_path),
        "defer_checkpoint_until_exit_qa": True,
        "run_id": "run_defer_1",
        "project": "nexus",
    }

    with patch("dual_pipeline.canonical_pipeline.transcript_resume.verify_attempt_binding"):
        attempt, out_syn, out_ev = run_synthesis_with_transcript_resume(
            synthesize=lambda **_: (1, syn, ev),
            source_text="Speaker A: Hello",
            metadata=meta,
            provider=SimpleNamespace(),
            provider_type="offline",
            modality=CanonicalModality.MEETING_TRANSCRIPT,
        )

    active_ckpt = _ACTIVE_TRANSCRIPT_CHECKPOINT.get()
    assert active_ckpt is not None
    # Before X1-X3 passes, VALIDATED_SYNTHESIS is not sealed; PENDING_EXIT_QA_SYNTHESIS holds the state
    assert not active_ckpt.has_stage("VALIDATED_SYNTHESIS")
    assert active_ckpt.has_stage("PENDING_EXIT_QA_SYNTHESIS")

    # Once Stage X1-X3 passes, refresh_validated_transcript_checkpoint promotes to VALIDATED_SYNTHESIS
    refresh_validated_transcript_checkpoint(out_syn, out_ev, attempt, meta)
    assert active_ckpt.has_stage("VALIDATED_SYNTHESIS")
    sealed = active_ckpt.get_stage("VALIDATED_SYNTHESIS")
    assert sealed is not None
    assert sealed["exit_gates_verified"] is True


def test_e2e_stop_lifecycle_records_migration_and_hint_errors(tmp_path: Path) -> None:
    """E2E-CRIT-03: Verifies stop_lifecycle logs and records errors rather than swallowing silently."""
    src_dir = tmp_path / "staging_chunks"
    src_dir.mkdir()
    target_dir = tmp_path / "final_chunks"
    target_dir.mkdir()

    bad_queue = MagicMock()
    bad_queue.load.side_effect = RuntimeError("corrupted hint queue")

    recorder = SimpleNamespace(
        chunks_dir=target_dir,
        _staging_chunks_dir=src_dir,
        capture_manager=None,
        spooler=None,
        speaker_hint_queue=bad_queue,
        speaker_hint_manager=MagicMock(),
        segments_jsonl=tmp_path / "seg.jsonl",
        transcript_md=tmp_path / "trans.md",
        clean_slug="test_slug",
        voice_registry=None,
        output_dir=tmp_path,
        stop_lifecycle_errors=[],
    )

    with patch(
        "dual_pipeline.live_meeting_recorder_core.stop_lifecycle.migrate_staged_files",
        side_effect=OSError("disk full during chunk migration"),
    ):
        migrate_all_chunk_sources_on_drain(recorder)

    finalize_hints_and_attributions(recorder)

    assert any("migrate_staged_files:disk full" in e for e in recorder.stop_lifecycle_errors)
    assert any("reconcile_pending_hints:corrupted hint queue" in e for e in recorder.stop_lifecycle_errors)


def test_e2e_sidecar_upsert_preserves_existing_vectors_under_foreign_keys(tmp_path: Path) -> None:
    """E2E-CRIT-04: Verifies ON CONFLICT DO UPDATE preserves dense, sparse, and ColBERT vectors under FK=ON."""
    sidecar_db = tmp_path / "sidecar.sqlite"
    with sqlite3.connect(sidecar_db) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(SIDECAR_SCHEMA)
        conn.execute("INSERT INTO metadata VALUES ('colbert_dimensions', '1024')")
        unit_vec = struct.pack("<1024f", 1.0, *([0.0] * 1023))
        for i in range(5):
            rid = f"rec-{i}"
            conn.execute(
                "INSERT INTO retrieval_record VALUES (?, 'approved', 'assertion', 'CLIENT_SAFE', 'h1', 'assertion', 'approved', 'direct_source', 'factual_answer', '{}')",
                (rid,),
            )
            conn.execute("INSERT INTO dense_vector VALUES (?, 1024, ?)", (rid, unit_vec))
            conn.execute("INSERT INTO sparse_weight VALUES (?, 'tok', 0.9)", (rid,))
            conn.execute("INSERT INTO colbert_token VALUES (?, 0, 'tok', 1024, ?)", (rid, unit_vec))

        # Upsert existing 5 records + 2 net-new records using non-cascading ON CONFLICT DO UPDATE
        sql_upsert = (
            "INSERT INTO retrieval_record "
            "(record_id, lane, record_type, namespace, retrieval_text_hash, "
            "retrieval_kind, epistemic_status, provenance_kind, answer_use, artifact_json) "
            "VALUES (?, 'approved', 'assertion', 'CLIENT_SAFE', 'h2', 'assertion', 'approved', 'direct_source', 'factual_answer', '{}') "
            "ON CONFLICT(record_id) DO UPDATE SET retrieval_text_hash=excluded.retrieval_text_hash"
        )
        for i in range(7):
            conn.execute(sql_upsert, (f"rec-{i}",))

        _ensure_missing_dense_vectors(conn, ["rec-5", "rec-6"])

        rec_cnt = conn.execute("SELECT COUNT(*) FROM retrieval_record").fetchone()[0]
        dense_cnt = conn.execute("SELECT COUNT(*) FROM dense_vector").fetchone()[0]
        sparse_cnt = conn.execute("SELECT COUNT(DISTINCT record_id) FROM sparse_weight").fetchone()[0]
        colbert_cnt = conn.execute("SELECT COUNT(DISTINCT record_id) FROM colbert_token").fetchone()[0]

    assert rec_cnt == 7
    assert dense_cnt == 7
    assert sparse_cnt == 7
    assert colbert_cnt == 7


def test_e2e_record_meeting_cli_and_detached_dispatch_preserve_flags(tmp_path: Path) -> None:
    """F-E2E-01: Verifies _handle_record_meeting and dispatch_record_meeting_cli forward instant, probation_ms, and whisper_model."""
    import argparse
    from dual_pipeline.cli.engine import _handle_record_meeting
    from dual_pipeline.live_meeting_recorder_core.recording_host_integration import dispatch_record_meeting_cli

    args_detached = argparse.Namespace(
        detach=True,
        meeting="Architecture Sync",
        date="10-25-2026",
        attendees=["Alice"],
        audio_source="mixed",
        mock=True,
        device=":1",
        segment_seconds=10,
        whisper_model="mlx-community/whisper-large-v3-turbo",
        session_id="sess_detached_01",
    )
    mock_adapter = MagicMock()
    mock_status = SimpleNamespace(session_id="sess_detached_01", recording_state=SimpleNamespace(value="CAPTURING"))
    mock_receipt = SimpleNamespace(to_dict=lambda: {"status": "PASS", "session_id": "sess_detached_01"})
    mock_adapter.start_recording_with_receipt.return_value = (mock_status, mock_receipt)

    with patch(
        "dual_pipeline.live_meeting_recorder_core.recording_service_adapter.get_recording_service_adapter",
        return_value=mock_adapter,
    ):
        res = dispatch_record_meeting_cli(args_detached, "project_nexus", False)

    assert res is not None
    assert res["status"] == "PASS"
    sent_req = mock_adapter.start_recording_with_receipt.call_args[0][0]
    assert sent_req.whisper_model == "mlx-community/whisper-large-v3-turbo"
    assert sent_req.session_id == "sess_detached_01"

    args_live = argparse.Namespace(
        project="project_nexus",
        meeting="Architecture Sync",
        date="10-25-2026",
        attendees=["Alice"],
        device=":0",
        segment_seconds=15,
        whisper_prompt=None,
        mock=True,
        backend="mlx",
        audio_source="mixed",
        whisper_model="mlx-community/whisper-large-v3-turbo",
        copilot=False,
        no_copilot=True,
        copilot_prefetch=False,
        detach=False,
        resume=False,
        immediate_worktree=False,
        fast_start=True,
        instant=False,
        probation_ms=450,
        inactivity_timeout=12.5,
        reviewer="operator",
    )
    captured_recorders: list = []

    def _capture_live_recorder(recorder, reviewer):
        captured_recorders.append((recorder, reviewer))
        if getattr(recorder, "_slug_lock_fd", None):
            recorder._slug_lock_fd.close()
        return {"status": "PASS", "reviewer": reviewer}

    with (
        patch("dual_pipeline.live_meeting_recorder.ROOT", tmp_path),
        patch("dual_pipeline.live_meeting_recorder.acquire_global_mlx_lock", autospec=True, return_value=None),
        patch("dual_pipeline.live_meeting_recorder.ensure_ffmpeg", autospec=True),
        patch(
            "dual_pipeline.cli.engine._handle_record_meeting_live",
            autospec=True,
            side_effect=_capture_live_recorder,
        ),
    ):
        out = _handle_record_meeting(args_live)

    assert out["status"] == "PASS"
    assert len(captured_recorders) == 1
    real_rec, rev = captured_recorders[0]
    assert rev == "operator"
    assert real_rec.enable_copilot_prefetch is False
    assert real_rec.instant is False
    assert real_rec.probation_ms == 450
    assert real_rec.whisper_model == "mlx-community/whisper-large-v3-turbo"


def test_e2e_decision_health_check_resolves_parent_runtime_and_fails_closed_on_missing_graph(tmp_path: Path) -> None:
    """F-E2E-04: Verifies DecisionHTTPServer.deep_health_check resolves parent active_pair.json and fails closed when graph file is missing."""
    from dual_pipeline.decision_api import DecisionHTTPServer

    runtime_sub = tmp_path / "runtime"
    runtime_sub.mkdir(parents=True)
    gen_dir = tmp_path / "generations" / "gen-parent"
    gen_dir.mkdir(parents=True)
    graph_db = gen_dir / "graph.sqlite"
    with sqlite3.connect(graph_db) as conn:
        conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY)")

    (tmp_path / "active_pair.json").write_text(
        json.dumps({"generation_id": "gen-parent", "graph_path": "generations/gen-parent/graph.sqlite"}),
        encoding="utf-8",
    )

    server = DecisionHTTPServer(("127.0.0.1", 0), SimpleNamespace(runtime_root=runtime_sub))
    try:
        healthy, payload = server.deep_health_check()
        assert healthy is True
        assert payload["checks"]["sqlite"] == "OK"
        assert payload["checks"]["sqlite_target"] == "active_graph"

        # Remove graph_db so active_pair.json points to a missing file; must fail closed instead of falling back to :memory:
        graph_db.unlink()
        healthy_missing, payload_missing = server.deep_health_check()
        assert healthy_missing is False
        assert payload_missing["status"] == "DEGRADED"
        assert payload_missing["checks"]["sqlite"].startswith("FAIL:")
    finally:
        server.server_close()


def test_e2e_decision_serve_parser_and_handler_forward_port_and_queue_size(tmp_path: Path) -> None:
    """E2E-CRIT-01: Verifies decision-serve CLI parses --port and forwards port and max_queue_size to serve_decision_api."""
    from dual_pipeline.cli.engine import _handle_decision_serve
    from dual_pipeline.cli.parser import build_parser

    parser = build_parser()
    args = parser.parse_args(
        [
            "decision-serve",
            "--host",
            "127.0.0.1",
            "--port",
            "8799",
            "--max-concurrency",
            "4",
            "--max-queue-size",
            "16",
            "--runtime-root",
            str(tmp_path),
            "--graph",
            str(tmp_path / "graph.sqlite"),
        ]
    )
    assert args.port == 8799
    assert args.max_queue_size == 16

    captured: dict = {}

    def _fake_serve(service, **kwargs):
        captured["service"] = service
        captured.update(kwargs)

    with patch("dual_pipeline.decision_api.serve_decision_api", side_effect=_fake_serve):
        res = _handle_decision_serve(args)

    assert res == {"status": "STOPPED"}
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8799
    assert captured["max_concurrency"] == 4
    assert captured["max_queue_size"] == 16


def test_e2e_uwg_activate_and_ingestion_reasoning_lazy_imports_resolve(tmp_path: Path) -> None:
    """E2E-CRIT-02: Verifies UniversalWriteGateway.activate and _ingestion_reasoning_service resolve parent-package modules without ModuleNotFoundError."""
    import argparse
    from dual_pipeline.cli.engine import _ingestion_reasoning_service
    from dual_pipeline.cli.parser import DEFAULT_INGESTION_REASONING_POLICY
    from dual_pipeline.uwg_core.gateway import UniversalWriteGateway

    args = argparse.Namespace(
        ingestion_reasoning="shadow",
        ingestion_reasoning_policy=DEFAULT_INGESTION_REASONING_POLICY,
        offline_ingestion_reasoning=True,
    )
    service = _ingestion_reasoning_service(args)
    assert service is not None
    assert service.mode == "shadow"

    # Verify UniversalWriteGateway.activate reaches validate_activation_evaluation without ModuleNotFoundError
    gw = UniversalWriteGateway(tmp_path, tmp_path / "seed.sqlite")
    receipt_file = tmp_path / "prepared_receipt.json"
    receipt_file.write_text(json.dumps({"status": "PREPARED"}), encoding="utf-8")

    with (
        patch("dual_pipeline.uwg_core.gateway._verify_sealed_receipt"),
        patch("dual_pipeline.uwg_core.gateway.release_action_resources"),
        patch(
            "dual_pipeline.retrieval_release.validate_activation_evaluation",
            side_effect=RuntimeError("REACHED_RETRIEVAL_RELEASE"),
        ),
    ):
        try:
            gw.activate(receipt_file)
            raise AssertionError("Expected RuntimeError sentinel")
        except RuntimeError as exc:
            assert "REACHED_RETRIEVAL_RELEASE" in str(exc)

