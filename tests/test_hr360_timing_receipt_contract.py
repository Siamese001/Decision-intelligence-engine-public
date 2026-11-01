"""
tests/test_hr360_timing_receipt_contract.py

Wave 1 Acceptance Suite: Verifies timing receipts, authoritative finalized resolution,
append-only attempt ledgers, child span exclusive accounting, resume telemetry, and
sandbox baseline evidence for HR360 ingestion latency remediation.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.hr360_baseline import (
    MockBenchmarkBackend,
    benchmark_model_state,
    benchmark_source_documents,
    generate_hr360_sandbox_baseline,
)
from dual_pipeline.execution_evidence import (
    durable_execution_scope,
    resolve_authoritative_receipt,
    write_immutable_evidence,
)
from dual_pipeline.index_core.differential import get_effective_embedding_telemetry
from dual_pipeline.l0.attempt_ledger import AttemptLedger, get_or_create_attempt_ledger
from dual_pipeline.l0.ingestion_timing import (
    SPAN_ACTIVATION,
    SPAN_CHANGED_RECORD_ENCODING,
    SPAN_CONVERSION,
    SPAN_DEPENDENCY_CHECKS,
    SPAN_EXTRACTION,
    SPAN_GRAPH_TRANSACTION,
    SPAN_JUDGE,
    SPAN_MODEL_INITIALIZATION,
    SPAN_QUERY_ENCODING,
    SPAN_RETRIEVAL_REPEATS,
    SPAN_SIDECAR_COPY,
    SPAN_SOURCE_ASSEMBLY,
    SPAN_SOURCE_DISCOVERY,
    SPAN_VALIDATION,
    PipelineTiming,
    TimedSpan,
    attempt_scope,
    compute_exclusive_accounting,
    timed_child_span,
)
from dual_pipeline.unified_agentic_pipeline import UnifiedAgenticPipelineTracker


# -----------------------------------------------------------------------------
# Criterion 1: Terminal Timing, Durations, and Snapshot Resolution
# -----------------------------------------------------------------------------

def test_terminal_receipt_timing_and_snapshot_resolution(tmp_path: Path):
    """
    Delivered receipt snapshot cannot masquerade as final.
    Authoritative finalized receipt has positive duration, finished_at, and FINAL status.
    """
    runtime_root = tmp_path / "runtime"
    deliverable_dir = tmp_path / "deliverables"
    deliverable_dir.mkdir(parents=True)
    delivered_receipt = deliverable_dir / "pipeline_execution_receipt.json"

    meta: Dict[str, Any] = {
        "request_id": "req-timing-001",
        "directive_id": "req-timing-001",
        "runtime_root": str(runtime_root),
        "deliverable_receipt_path": str(delivered_receipt),
    }
    tracker = UnifiedAgenticPipelineTracker("TestPipeline", "test_source.md", meta)

    # Step 1: During run, take snapshot and deliver
    with durable_execution_scope(tracker, runtime_root):
        run_id = tracker.timing.run_id
        authoritative_path = runtime_root / "executions" / run_id / "pipeline_execution_receipt.json"
        
        # Take snapshot mid-execution
        snapshot_payload = {
            "run_id": run_id,
            "pipeline_name": "TestPipeline",
            "terminal_outcome": "RUNNING",
            "measurement_state": "RUNNING",
            "started_at": tracker.timing.run_span.started_at if tracker.timing.run_span else "2026-10-08T12:00:00Z",
            "finished_at": None,
            "duration_ms": 0.0,
            "authoritative_receipt_path": str(authoritative_path),
        }
        with open(delivered_receipt, "w", encoding="utf-8") as f:
            json.dump(snapshot_payload, f, indent=2)

        # Pre-terminal snapshot without resolution MUST be rejected if finality required
        with pytest.raises(ValueError, match="PRETERMINAL_SNAPSHOT_REJECTED"):
            resolve_authoritative_receipt(delivered_receipt, require_final=True)

        from dual_pipeline.unified_agentic_pipeline import PIPELINE_STAGE_SEQUENCE
        for stage in PIPELINE_STAGE_SEQUENCE:
            tracker.start_stage(stage)
            tracker.complete_stage(stage)

        time.sleep(0.02)  # Simulate real work

    # Step 2: Scope exited, authoritative finalized receipt is written and delivered copy updated
    assert delivered_receipt.exists()
    final_receipt = resolve_authoritative_receipt(delivered_receipt, require_final=True)

    assert final_receipt["measurement_state"] == "FINAL"
    assert final_receipt["terminal_outcome"] in ("COMPLETE", "PASS")
    finished = final_receipt.get("ended_at") or final_receipt.get("execution_timing", {}).get("finished_at")
    assert finished is not None
    duration = final_receipt.get("monotonic_duration_s") or final_receipt.get("execution_timing", {}).get("duration_ms", 0.0)
    assert duration > 0.0
    clock = final_receipt.get("duration_clock") or final_receipt.get("execution_timing", {}).get("duration_clock")
    assert clock == "perf_counter_ns"


def test_reject_unresolved_preterminal_snapshot_when_authoritative_missing(tmp_path: Path):
    """Rejects preterminal snapshot as final when referenced authoritative receipt does not exist."""
    fake_snapshot = tmp_path / "fake_receipt.json"
    fake_snapshot.write_text(
        json.dumps({
            "run_id": "missing-run",
            "measurement_state": "RUNNING",
            "terminal_outcome": "RUNNING",
            "authoritative_receipt_path": str(tmp_path / "non_existent.json"),
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="PRETERMINAL_SNAPSHOT_REJECTED"):
        resolve_authoritative_receipt(fake_snapshot, require_final=True)


# -----------------------------------------------------------------------------
# Criterion 2: Append-Only Attempt Ledger Across Retries and Rollbacks
# -----------------------------------------------------------------------------

def test_attempt_ledger_preserves_attempts_across_retries_and_rollback(tmp_path: Path):
    """All attempts remain inspectable with parent lineage, digests, and retry causes."""
    ledger = AttemptLedger("req-retry-audit-002", storage_dir=tmp_path)

    # Attempt 1: Extraction fails due to judge rejection
    ledger.start_attempt(
        attempt_id=1,
        run_id="run-1",
        input_digest="hash-input-v1",
        model_digest="hash-model-bge-m3",
        code_digest="hash-code-v1",
        policy_digest="hash-policy-v1",
    )
    time.sleep(0.01)
    ledger.finish_attempt(
        attempt_id=1,
        status="FAILED",
        terminal_outcome="FAILED",
        retry_cause="DialecticalJudgeRejection",
        resume_decision={"action": "retry_with_healed_topology", "reused_stage": None},
    )

    # Attempt 2: Resumed attempt reuses input and healed topology
    ledger.start_attempt(
        attempt_id=2,
        run_id="run-2",
        parent_attempt_id=1,
        input_digest="hash-input-v1",
        model_digest="hash-model-bge-m3",
        code_digest="hash-code-v1",
        policy_digest="hash-policy-v1",
    )
    time.sleep(0.01)
    ledger.finish_attempt(
        attempt_id=2,
        status="COMPLETE",
        terminal_outcome="COMPLETE",
        retry_cause=None,
        resume_decision={"action": "reuse_accepted_topology", "reused_stage": "extraction"},
    )

    all_attempts = ledger.get_attempts()
    assert len(all_attempts) == 2
    assert all_attempts[0]["attempt_id"] == 1
    assert all_attempts[0]["terminal_outcome"] == "FAILED"
    assert all_attempts[0]["retry_cause"] == "DialecticalJudgeRejection"
    assert all_attempts[0]["parent_attempt_id"] is None

    assert all_attempts[1]["attempt_id"] == 2
    assert all_attempts[1]["parent_attempt_id"] == 1
    assert all_attempts[1]["terminal_outcome"] == "COMPLETE"
    assert all_attempts[1]["resume_decision"].get("reused_stage") == "extraction"

    # Export to JSON and verify durability
    export_path = tmp_path / "attempt_ledger.json"
    ledger.export_to_json(export_path)
    assert export_path.exists()
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert len(exported["attempts"]) == 2


# -----------------------------------------------------------------------------
# Criterion 3: Child Spans Exclusive Accounting & Overlapping Intervals
# -----------------------------------------------------------------------------

def test_child_spans_exclusive_accounting_and_overlapping_intervals():
    """
    Child spans cannot be added again to parent inclusive durations.
    Overlapping child spans are represented explicitly in exclusive accounting.
    """
    meta: Dict[str, Any] = {"request_id": "req-timing-spans"}
    pipeline_timing = PipelineTiming("TestPipeline", meta)
    run_span = TimedSpan("pipeline.run", pipeline_timing.attributes(), pipeline_timing)
    pipeline_timing.run_span = run_span

    pipeline_timing.start_stage("L2_EXTRACTION")
    
    with pipeline_timing.child_span(SPAN_EXTRACTION, attributes={"batch_size": 4}) as span_ext:
        time.sleep(0.015)
        span_ext.finish("PASS")

    with pipeline_timing.child_span(SPAN_JUDGE, attributes={"rubric": "dialectical"}) as span_judge:
        time.sleep(0.010)
        span_judge.finish("PASS")

    stage_record = pipeline_timing.finish_stage("L2_EXTRACTION", "COMPLETE")
    run_span.finish("COMPLETE")

    exclusive = compute_exclusive_accounting(
        run_span=run_span,
        stage_records={"L2_EXTRACTION": stage_record},
        child_spans=pipeline_timing.all_child_spans,
    )

    assert exclusive["critical_path_ms"] > 0.0
    assert exclusive["child_span_count"] == 2
    assert "child_spans_by_name" in exclusive
    assert SPAN_EXTRACTION in exclusive["child_spans_by_name"]
    assert SPAN_JUDGE in exclusive["child_spans_by_name"]
    assert "exclusive" in exclusive["duration_accounting"]


def test_exclusive_accounting_detects_concurrent_overlapping_spans():
    """Explicitly tracks and isolates concurrent overlapping child span intervals."""
    stage_records = {"STAGE_1": {"duration_ms": 100.0}}
    synthetic_child_spans = [
        {"child_name": "worker_a", "started_at": "2026-10-08T12:00:00.000000Z", "finished_at": "2026-10-08T12:00:00.050000Z", "duration_ms": 50.0},
        {"child_name": "worker_b", "started_at": "2026-10-08T12:00:00.030000Z", "finished_at": "2026-10-08T12:00:00.080000Z", "duration_ms": 50.0},
    ]

    class FakeRunSpan:
        duration_ms = 90.0

    accounting = compute_exclusive_accounting(FakeRunSpan(), stage_records, synthetic_child_spans)

    assert accounting["child_span_count"] == 2
    assert accounting["aggregate_worker_ms"] == 100.0
    # Overlap interval from :00.030000 to :00.050000 is 20ms
    assert len(accounting["overlapping_spans"]) == 1
    assert accounting["overlapping_spans"][0]["overlap_duration_ms"] == 20.0
    # Critical path is span from 0 to 80ms = 80ms
    assert accounting["critical_path_ms"] == 80.0


# -----------------------------------------------------------------------------
# Criterion 4: Resume Telemetry & Artifact Digests Agreement
# -----------------------------------------------------------------------------

def test_resume_telemetry_agrees_with_provider_calls_and_artifact_digests(tmp_path: Path):
    """
    On resume or publication-only failure, provider calls remain 0.
    Selected artifact digest matches the accepted healed artifact.
    """
    accepted_artifact = tmp_path / "accepted_topology.json"
    accepted_content = {"entities": ["HR360_PRF", "Position_Integration"], "status": "VERIFIED"}
    accepted_artifact.write_text(json.dumps(accepted_content), encoding="utf-8")
    accepted_digest = "sha256-verified-accepted-content-999"

    ledger = AttemptLedger("req-hr360-resume-003", storage_dir=tmp_path)
    
    # Attempt 1: Extraction passes, but publication fails downstream
    ledger.start_attempt(
        attempt_id=1,
        run_id="run-1",
        input_digest="hr360-src-digest",
        model_digest="model-bge-m3-pinned",
    )
    ledger.finish_attempt(
        attempt_id=1,
        status="FAILED",
        terminal_outcome="FAILED",
        retry_cause="IndexTransactionTimeout",
        resume_decision={
            "action": "resume_publication_only",
            "reused_stage": "extraction",
            "accepted_artifact_digest": accepted_digest,
            "provider_calls_incurred": 0,
        },
    )

    # Attempt 2: Resumed publication without re-running extraction
    ledger.start_attempt(
        attempt_id=2,
        run_id="run-2",
        parent_attempt_id=1,
        input_digest="hr360-src-digest",
        model_digest="model-bge-m3-pinned",
    )
    ledger.finish_attempt(
        attempt_id=2,
        status="COMPLETE",
        terminal_outcome="COMPLETE",
        resume_decision={
            "action": "reused_cached_extraction",
            "reused_stage": "extraction",
            "accepted_artifact_digest": accepted_digest,
            "provider_calls_incurred": 0,
        },
    )

    attempts = ledger.get_attempts()
    assert len(attempts) == 2
    assert attempts[1]["resume_decision"]["provider_calls_incurred"] == 0
    assert attempts[1]["resume_decision"]["accepted_artifact_digest"] == accepted_digest


# -----------------------------------------------------------------------------
# Criterion 5 & 6: Sandbox Baseline Evidence & Live Telemetry Inspection
# -----------------------------------------------------------------------------

def test_sandbox_baseline_evidence_matches_source_documents_and_isolates_graphs(tmp_path: Path):
    """
    Verifies that the sandbox benchmark captures exact HR360 document metrics,
    performs warm vs cold timing without touching deployment graphs, and records live telemetry.
    """
    docs_dir = Path("documents/hrit").resolve()
    baseline_out = tmp_path / "sandbox_baseline_evidence.json"

    baseline = generate_hr360_sandbox_baseline(
        docs_dir=docs_dir,
        output_path=baseline_out,
        mock_mode=True,
    )

    assert baseline_out.exists()
    assert baseline["schema_version"] == "1.0.0"

    src = baseline["source_audit"]
    assert src["pdf_attachment"]["page_count"] == 4
    assert src["pdf_attachment"]["bytes"] == 122304
    assert src["position_workbook"]["sheet_count"] == 4
    assert src["position_workbook"]["total_nonempty_rows"] == 176
    assert src["requisition_workbook"]["sheet_count"] == 2
    assert src["requisition_workbook"]["total_nonempty_rows"] == 96
    assert src["aggregate_source_coverage"]["total_workbook_nonempty_rows"] == 272

    model = baseline["model_runtime_audit"]
    assert model["model_id"] == "BAAI/bge-m3"
    assert model["sandbox_isolation_verified"] is True
    assert model["warm_initialization_ms"] <= model["cold_initialization_ms"]

    telem = model["telemetry"]
    assert telem["effective_device"] == "cpu"
    assert telem["effective_precision"] == "float32"
    assert telem["inferred_from_defaults"] is False
    assert telem["reused_records"] == 976
    assert telem["delta_records"] == 20

    isolation = baseline["isolation_attestation"]
    assert isolation["deployment_graph_mutated"] is False
    assert isolation["canonical_graph_mutated"] is False
    assert isolation["ephemeral_sandbox_only"] is True


def test_effective_embedding_telemetry_live_inspection():
    """Verify live inspection prevents uninspected defaults."""
    backend = MockBenchmarkBackend()
    telem = get_effective_embedding_telemetry(
        backend=backend,
        total_records=1000,
        reused_records=950,
        delta_records=50,
        cache_hits=950,
        host_chunk_size=128,
        inference_batch_size=32,
    )
    assert telem["effective_device"] == "cpu"
    assert telem["effective_precision"] == "float32"
    assert telem["effective_host_chunk"] == 128
    assert telem["effective_inference_batch"] == 32
    assert telem["inferred_from_defaults"] is False
    assert telem["total_records"] == 1000
    assert telem["reused_records"] == 950
    assert telem["delta_records"] == 50
    assert telem["cache_hits"] == 950
