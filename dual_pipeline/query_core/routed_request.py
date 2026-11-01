"""Terminal handler for routed requests that do not require NEE reasoning."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from dual_pipeline.core import write_json
from dual_pipeline.drift_guard import assert_runtime_binding_unchanged
from dual_pipeline.index import EmbeddingBackend
from dual_pipeline.query_result import QueryResult
from dual_pipeline.query_router import DestinationDecision, QueryDestination
from dual_pipeline.release import ReleaseManager
from dual_pipeline.telemetry import RunRecorder

from .artifact_writer import mirror_artifacts_to_output_dir
from .cache import _correlation


def _finish_routed_request(
    *,
    manager: ReleaseManager,
    backend: EmbeddingBackend,
    decision: DestinationDecision,
    decision_artifact: dict[str, Any],
    recorder: RunRecorder,
    request_id: str,
    run_id: str,
    current_trace_id: str,
    active: dict[str, Any],
    graph_hash_before: str,
    runtime_binding: dict[str, Any],
    runtime_policy_digests: Mapping[str, str | None],
    summary_path: Path,
    output_dir: Path | None,
) -> dict[str, Any]:
    """Seal a non-NEE route without retrieval, embeddings, or provider calls."""

    recorder.record(
        stage="L1",
        event_type="pipeline_event",
        payload=decision_artifact,
        correlation=_correlation(request_id),
    )
    blocked = decision.destination is QueryDestination.ROUTE_AMBIGUOUS
    recorder.record(
        stage="L0",
        event_type="route_decision",
        payload=decision_artifact,
        correlation=_correlation(request_id),
        status="BLOCKED" if blocked else "PASS",
    )
    required_stages = ["U0", "L1", "L0", "L4"]
    seal = recorder.seal(
        required_stages,
        _correlation(request_id),
        status="BLOCKED" if blocked else "PASS",
    )
    terminal_binding = assert_runtime_binding_unchanged(
        manager=manager,
        expected_binding=runtime_binding,
        backend=backend,
        policy_digests=runtime_policy_digests,
        stage="routed-terminal",
    )
    final = {
        "status": "BLOCKED" if blocked else "ROUTED",
        "request_id": request_id,
        "run_id": run_id,
        "trace_id": current_trace_id,
        "destination": decision.destination.value,
        "eligibility_status": decision.eligibility_status,
        "eligibility_checks": dict(decision.eligibility_checks),
        "destination_reason_codes": list(decision.reason_codes),
        "query_destination_decision_digest": decision_artifact["canonical_digest"][
            "value"
        ],
        "delivery_profile": decision.delivery_profile,
        "artifact_type": decision.artifact_type,
        "handoff_permitted": decision.handoff_permitted,
        "automatic_egress_count": 0,
        "terminal_outcome": "ROUTE_AMBIGUOUS" if blocked else "HANDOFF_REQUIRED",
        "graph_snapshot": active["graph_snapshot"],
        "generation_id": active["generation_id"],
        "embedding_backend": active["embedding_backend"],
        "embedding_call_count": 0,
        "external_l2_call_count": 0,
        "judge_call_count": 0,
        "challenger_call_count": 0,
        "total_external_model_call_count": 0,
        "graph_sha256_before": graph_hash_before,
        "graph_sha256_after": graph_hash_before,
        "runtime_binding": terminal_binding,
        "graph_mutation_count": 0,
        "learning_event_count": seal["event_count"],
        "learning_event_chain_digest": seal["event_chain_digest"],
        "required_stages": required_stages,
        "answer": None,
        "idempotent_replay": False,
    }
    write_json(summary_path, final)
    if output_dir is not None:
        mirror_artifacts_to_output_dir(
            output_dir,
            {
                "runtime_binding.json": terminal_binding,
                "answer.json": final,
                "query_destination_decision.json": decision_artifact,
            },
        )
    return QueryResult(**final)
