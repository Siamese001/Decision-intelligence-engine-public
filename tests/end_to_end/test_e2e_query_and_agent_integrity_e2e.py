"""End-to-end verification tests for Wave 2 Query Routing, Pre-L2 Block, Decision Health & Agent Fallbacks (E2E-CRIT-05..06)."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dual_pipeline.a2a import A2ATask, A2ATaskStatus
from dual_pipeline.decision_api import DecisionHTTPServer
from dual_pipeline.query_router import QueryDestination, route_query
from dual_pipeline.retrieval.agents.multi_perspective_dispatcher import (
    _execute_local_mlx,
)
from pipeline_b_extraction.agents.friction_blocker_agent import FrictionBlockerAgent
from pipeline_b_extraction.agents.stakeholder_dynamics_agent import StakeholderDynamicsAgent


def test_e2e_strategic_multi_record_decision_routes_to_nee_decision_intelligence() -> None:
    """E2E-CRIT-05: Verifies strategic multi-record decision queries route to NEE_DECISION_INTELLIGENCE."""
    q = "Evaluate the strategic trade-offs and material risks across multiple records for the platform architecture decision."
    decision = route_query(q)
    assert decision.destination == QueryDestination.NEE_DECISION_INTELLIGENCE
    assert decision.query_type == "cross_workstream_synthesis"
    assert decision.enters_query_runtime is True


def test_e2e_decision_http_server_deep_health_check_validates_active_graph(tmp_path: Path) -> None:
    """E2E-CRIT-05: Verifies DecisionHTTPServer.deep_health_check inspects active_pair.json graph when available."""
    gen_dir = tmp_path / "generations" / "gen-test"
    gen_dir.mkdir(parents=True)
    graph_db = gen_dir / "graph.sqlite"
    with sqlite3.connect(graph_db) as conn:
        conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY)")

    (tmp_path / "active_pair.json").write_text(
        json.dumps({"generation_id": "gen-test", "graph_path": "generations/gen-test/graph.sqlite"}),
        encoding="utf-8",
    )

    dummy_service = SimpleNamespace(runtime_root=tmp_path)
    server = DecisionHTTPServer(("127.0.0.1", 0), dummy_service)
    try:
        healthy, payload = server.deep_health_check()
        assert healthy is True
        assert payload["status"] == "HEALTHY"
        assert payload["checks"]["sqlite"] == "OK"
        assert payload["checks"]["sqlite_target"] == "active_graph"
    finally:
        server.server_close()


def test_e2e_extraction_and_retrieval_agents_record_fallback_telemetry_on_exception() -> None:
    """E2E-CRIT-06: Verifies StakeholderDynamicsAgent, FrictionBlockerAgent, and MultiPerspectiveDispatcher record fallback_used."""
    task = A2ATask(
        task_id="task-e2e-01",
        parent_task_id=None,
        sender_agent_id="orchestrator",
        recipient_agent_id="worker",
        objective="Extract stakeholder dynamics and blockers",
        status=A2ATaskStatus.WORKING,
        input_payload={
            "evidence_id": "ev-01",
            "speaker": "Mark Lemasney",
            "text": "We must resolve the cross-workstream dependency blocker before Q4 cutover.",
            "query": "What are the Q4 cutover blockers and architecture risks?",
        },
    )

    sd_agent = StakeholderDynamicsAgent(mock_mode=False)
    fb_agent = FrictionBlockerAgent(mock_mode=False)

    with (
        patch("mlx_lm.load", side_effect=RuntimeError("simulated local MLX failure")),
        patch(
            "dual_pipeline.runtime.model_runtime.get_model_runtime",
            side_effect=RuntimeError("simulated local MLX failure"),
        ),
    ):
        sd_res = asyncio.run(sd_agent.handle_task(task))
        fb_res = asyncio.run(fb_agent.handle_task(task))
        mp_res = _execute_local_mlx("task-e2e-02", "prompt", "What are the Q4 cutover blockers?")

    for res in (sd_res, fb_res, mp_res):
        assert res["status"] == "COMPLETED"
        assert res["fallback_used"] is True
        assert "RuntimeError: simulated local MLX failure" in res["degraded_reason"]


def test_e2e_document_resume_defers_until_exit_qa_and_invalidates_on_x_gate_failure(tmp_path: Path) -> None:
    """F-E2E-02: Verifies document synthesis defers VALIDATED_SYNTHESIS until Stage X1-X3 passes and invalidates on X_EXIT_GATES failure."""
    from dual_pipeline.canonical_pipeline.document_resume import (
        _ACTIVE_DOCUMENT_CHECKPOINT,
        record_document_failure,
        refresh_validated_document_checkpoint,
        run_synthesis_with_resume,
    )
    from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
    from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
    from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
    from dual_pipeline.checkpoint import PipelineCheckpointManager

    ckpt_path = tmp_path / "doc_resume.json"
    syn = SynthesisResult(output_3='{"graph_mutations": {"entity_upserts": []}}')
    ev = QAEvaluationResult(
        passed=True,
        qa_report={"overall_verdict": "PASS", "all_gates_passed": True},
        judge_report={},
        final_decision={"status": "PASS"},
        authoritative_write_authorized=True,
    )
    meta = {
        "_document_checkpoint_path": str(ckpt_path),
        "defer_checkpoint_until_exit_qa": True,
        "run_id": "doc_run_1",
        "project": "nexus",
        "is_document": True,
    }
    fake_binding = {
        "binding_sha256": "bind_doc_123",
        "reusable": True,
        "reuse_blockers": [],
    }

    with (
        patch("dual_pipeline.canonical_pipeline.document_resume._binding", return_value=fake_binding),
        patch("dual_pipeline.canonical_pipeline.document_resume.verify_attempt_binding"),
        patch("dual_pipeline.canonical_pipeline.execution_identity.execution_reuse_blockers", return_value=[]),
    ):
        attempt, out_syn, out_ev = run_synthesis_with_resume(
            synthesize=lambda **_: (1, syn, ev),
            source_text="Document text body",
            metadata=meta,
            provider=SimpleNamespace(),
            provider_type="offline",
            modality=CanonicalModality.DOCUMENT,
        )

    active_ckpt = _ACTIVE_DOCUMENT_CHECKPOINT.get()
    assert active_ckpt is not None
    assert not active_ckpt.has_stage("VALIDATED_SYNTHESIS")
    assert active_ckpt.has_stage("PENDING_EXIT_QA_SYNTHESIS")

    healed_syn = SynthesisResult(output_3='{"graph_mutations": {"entity_upserts": [{"id": "role:owner"}]}}')
    refresh_validated_document_checkpoint(healed_syn, out_ev, attempt, meta)
    assert active_ckpt.has_stage("VALIDATED_SYNTHESIS")
    sealed = active_ckpt.get_stage("VALIDATED_SYNTHESIS")
    assert sealed is not None
    assert sealed["exit_gates_verified"] is True
    assert "role:owner" in sealed["synthesis"]["output_3"]

    record_document_failure(meta, "X_EXIT_GATES", ValueError("X1-X3 FAIL"))
    reloaded = PipelineCheckpointManager(ckpt_path, require_binding=True)
    reloaded.bind(fake_binding)
    assert not reloaded.has_stage("VALIDATED_SYNTHESIS")
    assert not reloaded.has_stage("PENDING_EXIT_QA_SYNTHESIS")
    last_fail = reloaded.get_stage("LAST_FAILURE")
    assert last_fail is not None
    assert last_fail["reason"] == "X_EXIT_GATES_FAILED_SYNTHESIS_INVALIDATED"


def test_e2e_guarded_graph_publication_prefers_staging_vector_file_over_stale_target(tmp_path: Path) -> None:
    """F-E2E-03: Verifies _guarded_graph_publication binds vector_staging.sqlite from active staging directory over stale target directory."""
    from dual_pipeline.canonical_pipeline.publish_helper import _guarded_graph_publication

    staging_dir = tmp_path / ".staging_meeting_abc123"
    target_dir = tmp_path / "meeting_final"
    staging_dir.mkdir(parents=True)
    target_dir.mkdir(parents=True)

    staging_vec = staging_dir / "vector_staging.sqlite"
    target_vec = target_dir / "vector_staging.sqlite"
    staging_vec.write_bytes(b"fresh_staging_vectors")
    target_vec.write_bytes(b"stale_target_vectors")

    intake_pkg = staging_dir / "intake_package.json"
    intake_pkg.write_text("{}", encoding="utf-8")

    meta = {"target_output_dir": str(target_dir), "project": "nexus"}
    written: dict[str, str] = {}

    with (
        patch(
            "dual_pipeline.canonical_pipeline.publish_helper._validate_auth_and_runtime",
            return_value=({}, tmp_path / "runtime", {"status": "AWAITING_APPROVAL"}),
        ),
        patch(
            "dual_pipeline.canonical_pipeline.publication_bindings.canonical_publication_runtime",
            return_value=tmp_path / "runtime",
        ),
    ):
        res = _guarded_graph_publication(
            intake_pkg_path=intake_pkg,
            metadata=meta,
            written=written,
            directive_id=None,
            reviewer="operator",
            rationale="test",
            embedding_backend="bge-m3",
        )

    assert res == {"status": "AWAITING_APPROVAL"}
    assert written["vector_staging"] == str(staging_vec)
    assert not written["vector_staging"].endswith("meeting_final/vector_staging.sqlite")


def test_e2e_document_prepass_forwards_provider_type_and_prevents_duplicate_execution(tmp_path: Path) -> None:
    """E2E-CRIT-03: Verifies _run_5_agent_prepass forwards provider_type and inject_5_agent_intelligence_if_needed skips duplicate runs when 5_agent_intelligence_report is already present."""
    from dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence import (
        inject_5_agent_intelligence_if_needed,
    )
    from dual_pipeline.document_pipeline_core.pipeline import _run_5_agent_prepass

    doc_file = tmp_path / "agile_delivery_roles_spec.md"
    doc_file.write_text("Role specification content. " * 20, encoding="utf-8")

    meta = {
        "title": "Agile Delivery Roles Matrix",
        "provider_type": "offline",
    }

    calls: list[dict] = []

    def _fake_5_agent(document_text: str, document_title: str = "Document", **kwargs):
        calls.append({"title": document_title, **kwargs})
        return SimpleNamespace(
            coverage_score=0.95,
            primacy_bias_detected=False,
            total_entities_detected=0,
            entities=[],
            relationships=[],
            arbiter_verdict="PASS",
        )

    with patch(
        "dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence.run_5_agent_document_intelligence",
        side_effect=_fake_5_agent,
    ):
        _run_5_agent_prepass(doc_file, meta)
        inject_5_agent_intelligence_if_needed(doc_file.read_text(encoding="utf-8"), meta)

    assert len(calls) == 1
    assert calls[0].get("provider_type") == "offline"
    assert "5_agent_intelligence_report" in meta


def test_e2e_live_meeting_recorder_stop_lifecycle_preserves_errors_in_seal_receipt(tmp_path: Path) -> None:
    """E2E-CRIT-04: Verifies stop_lifecycle records errors even when stop_lifecycle_errors was not pre-allocated and exposes them in seal_receipt."""
    from unittest.mock import MagicMock
    from dual_pipeline.live_meeting_recorder_core.stop_lifecycle import (
        finalize_diarization_and_seal,
        finalize_hints_and_attributions,
        migrate_all_chunk_sources_on_drain,
    )

    src_dir = tmp_path / "staging_chunks"
    src_dir.mkdir()
    target_dir = tmp_path / "final_chunks"
    target_dir.mkdir()

    bad_queue = MagicMock()
    bad_queue.load.side_effect = RuntimeError("corrupted hint queue on stop")

    # Note: stop_lifecycle_errors intentionally omitted on recorder to verify defensive initialization and seal_receipt propagation
    recorder = SimpleNamespace(
        chunks_dir=target_dir,
        _staging_chunks_dir=src_dir,
        capture_manager=None,
        spooler=None,
        speaker_hint_queue=bad_queue,
        speaker_hint_manager=MagicMock(),
        segments_jsonl=tmp_path / "seg.jsonl",
        transcript_md=tmp_path / "trans.md",
        session_manifest=tmp_path / "manifest.json",
        acoustic_evidence_receipt_path=tmp_path / "acoustic_receipt.json",
        clean_slug="test_slug",
        voice_registry=None,
        output_dir=tmp_path,
        master_wav=None,
        _run_post_recording_diarization=lambda: None,
        _cleanup_empty_session_or_seal=lambda: None,
        _release_slug_lock=lambda: None,
    )

    with patch(
        "dual_pipeline.live_meeting_recorder_core.stop_lifecycle.migrate_staged_files",
        side_effect=OSError("disk quota exceeded"),
    ):
        migrate_all_chunk_sources_on_drain(recorder)

    finalize_hints_and_attributions(recorder)
    finalize_diarization_and_seal(recorder)

    assert hasattr(recorder, "stop_lifecycle_errors")
    assert len(recorder.stop_lifecycle_errors) == 2
    assert recorder.seal_receipt["degraded"] is True
    assert len(recorder.seal_receipt["stop_lifecycle_errors"]) == 2


def test_e2e_recording_service_adapter_and_cli_session_field_wiring(tmp_path: Path) -> None:
    """E2E-CRIT-01: Verifies RecordingSessionHandle._init_recorder, _handle_record_meeting, and dispatch_record_meeting_cli preserve session_id, meeting_date, host_chat_id, idempotency_key, is_cold_start, and normalized attendees."""
    import argparse
    from dual_pipeline.live_meeting_recorder_core.recording_host_integration import (
        dispatch_record_meeting_cli,
    )
    from dual_pipeline.live_meeting_recorder_core.recording_service_adapter import (
        RecordingSessionHandle,
    )
    from dual_pipeline.live_meeting_recorder_core.recording_service_models import (
        RecordingStartRequest,
        RecordingState,
        RecordingStatusResponse,
    )

    out_dir = tmp_path / "rec_out"
    req = RecordingStartRequest(
        request_id="req_test_01",
        idempotency_key="idem_key_01",
        session_id="sess_explicit_01",
        host_chat_id="chat_host_01",
        project_name="jarvis",
        meeting_name="Architecture Sync",
        meeting_date="10/15/2026",
        attendees=["Mark Lemasney", "Amit"],
        audio_source="mixed",
        mock_mode=True,
        device_index=":0",
        segment_seconds=15,
        whisper_model="mlx-community/whisper-large-v3-turbo",
        is_cold_start=True,
        output_dir=str(out_dir),
    )
    handle = RecordingSessionHandle(request=req, session_id="sess_explicit_01", repo_root=tmp_path)
    rec = handle._init_recorder()
    try:
        assert rec.session_id == "sess_explicit_01"
        assert rec.meeting_date_display == "10/15/2026"
        assert rec.host_chat_id == "chat_host_01"
        assert rec.idempotency_key == "idem_key_01"
        assert rec.is_cold_start is True
        assert isinstance(rec.output_dir, Path)
    finally:
        rec._release_slug_lock()
        if getattr(rec, "_global_mlx_lock", None):
            rec._global_mlx_lock.release()

    captured_reqs: list[RecordingStartRequest] = []

    class _FakeAdapter:
        def start_recording_with_receipt(self, r: RecordingStartRequest, detached: bool = False):
            captured_reqs.append(r)
            st = SimpleNamespace(
                session_id=r.session_id or "sess_det_01",
                request_id=r.request_id,
                recording_state=RecordingState.STARTING,
                started_at_iso="2026-10-30T12:00:00Z",
                dispatch_monotonic=1.02,
                intent_received_monotonic=1.00,
                master_wav_path=None,
                is_cold_start=False,
            )
            from dual_pipeline.live_meeting_recorder_core.recording_host_integration import (
                build_immediate_start_receipt,
            )
            return st, build_immediate_start_receipt(st)

    args = argparse.Namespace(
        detach=True,
        meeting="Detached Sync",
        date="10/15/2026",
        attendees="Mark Lemasney, Amit",
        audio_source="mixed",
        mock=True,
        device=":0",
        segment_seconds=15,
        whisper_model=None,
        session_id="sess_det_01",
    )
    with patch(
        "dual_pipeline.live_meeting_recorder_core.recording_service_adapter.get_recording_service_adapter",
        return_value=_FakeAdapter(),
    ):
        res = dispatch_record_meeting_cli(args, "jarvis", False)
    assert res is not None
    assert len(captured_reqs) == 1
    assert isinstance(captured_reqs[0].attendees, list)
    assert captured_reqs[0].attendees == ["Mark Lemasney", "Amit"]


def test_e2e_synthesis_strategy_forwards_document_and_notes_overrides_and_deduplicates_prepass(tmp_path: Path) -> None:
    """E2E-CRIT-02: Verifies get_synthesis_strategy honors pipeline overrides for DOCUMENT and MEETING_NOTES and _run_5_agent_prepass prevents duplicate execution after failure."""
    from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
    from dual_pipeline.canonical_pipeline.strategies.synthesis import get_synthesis_strategy
    from dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence import (
        inject_5_agent_intelligence_if_needed,
    )
    from dual_pipeline.document_pipeline_core.pipeline import _run_5_agent_prepass

    custom_doc_extractor = SimpleNamespace(execute=lambda **_: '{"custom": true}')
    custom_recap = SimpleNamespace(execute=lambda **_: "# Custom Recap")
    custom_graph = SimpleNamespace(execute=lambda **_: '{"custom_notes": true}')
    fake_pipeline = SimpleNamespace(
        document_extractor=custom_doc_extractor,
        l2_recap=custom_recap,
        l2_graph=custom_graph,
    )
    doc_strat = get_synthesis_strategy(CanonicalModality.DOCUMENT, SimpleNamespace(), pipeline=fake_pipeline)
    assert doc_strat.document_extractor is custom_doc_extractor

    notes_strat = get_synthesis_strategy(CanonicalModality.MEETING_NOTES, SimpleNamespace(), pipeline=fake_pipeline)
    assert notes_strat.l2_recap is custom_recap
    assert notes_strat.l2_graph is custom_graph

    doc_file = tmp_path / "agile_delivery_roles_failure.md"
    doc_file.write_text("Role specification content. " * 25, encoding="utf-8")
    meta = {"title": "Agile Delivery Roles Matrix", "provider_type": "llm"}
    call_count = 0

    def _failing_5_agent(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("transient 5-agent failure")

    with patch(
        "dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence.run_5_agent_document_intelligence",
        side_effect=_failing_5_agent,
    ):
        _run_5_agent_prepass(doc_file, meta)
        inject_5_agent_intelligence_if_needed(doc_file.read_text(encoding="utf-8"), meta)

    assert call_count == 1


def test_e2e_stop_lifecycle_records_attribution_and_speaker_map_export_errors(tmp_path: Path) -> None:
    """E2E-CRIT-02: Verifies finalize_hints_and_attributions appends roster sync, chat attribution save, and speaker map export errors to stop_lifecycle_errors."""
    from unittest.mock import MagicMock
    from dual_pipeline.live_meeting_recorder_core.stop_lifecycle import (
        finalize_diarization_and_seal,
        finalize_hints_and_attributions,
    )

    bad_chat = MagicMock()
    bad_chat.attributions = {"0": "Mark"}
    bad_chat.save.side_effect = OSError("chat attribution write failed")

    bad_hint_mgr = MagicMock()
    bad_hint_mgr.export_colocated_speaker_map.side_effect = OSError("speaker map write failed")

    recorder = SimpleNamespace(
        chunks_dir=tmp_path,
        _staging_chunks_dir=None,
        capture_manager=None,
        spooler=None,
        speaker_hint_queue=None,
        speaker_hint_manager=bad_hint_mgr,
        chat_attributions=bad_chat,
        chat_attributions_path=tmp_path / "chat_attr.json",
        segments_jsonl=tmp_path / "seg.jsonl",
        transcript_md=tmp_path / "trans.md",
        session_manifest=tmp_path / "manifest.json",
        acoustic_evidence_receipt_path=tmp_path / "acoustic_receipt.json",
        clean_slug="test_slug_2",
        voice_registry=None,
        output_dir=tmp_path,
        master_wav=None,
        _run_post_recording_diarization=lambda: None,
        _cleanup_empty_session_or_seal=lambda: None,
        _release_slug_lock=lambda: None,
    )

    with patch(
        "dual_pipeline.live_meeting_recorder_core.stop_lifecycle.sync_roster_from_disk_and_hints",
        side_effect=RuntimeError("roster sync error"),
    ):
        finalize_hints_and_attributions(recorder)

    finalize_diarization_and_seal(recorder)
    assert recorder.seal_receipt["degraded"] is True
    errs = recorder.seal_receipt["stop_lifecycle_errors"]
    assert any("sync_roster_from_disk_and_hints" in e for e in errs)
    assert any("chat_attributions" in e for e in errs)
    assert any("export_colocated_speaker_map" in e for e in errs)


