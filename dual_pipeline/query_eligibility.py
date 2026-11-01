"""Post-retrieval graph-fit enforcement for NEE query candidates."""
from __future__ import annotations

from typing import Any, Mapping

from .contracts import create_artifact, validate_artifact
from .core import stable_id, utc_now


def assess_query_graph_fit(
    *,
    destination_decision: Mapping[str, Any],
    plan: Mapping[str, Any],
    connected_evidence_map: Mapping[str, Any],
    evidence_packet: Mapping[str, Any],
    active_pair: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a provider-free, active-pair-bound graph-fit decision."""

    for artifact in (destination_decision, plan, connected_evidence_map):
        validate_artifact(artifact)
    destination = str(destination_decision["destination"])
    if destination not in {"GRAPH_EVIDENCE_UTILITY", "NEE_DECISION_INTELLIGENCE"}:
        raise ValueError("graph-fit assessment requires a graph-entering destination")
    if plan["run_id"] != connected_evidence_map["run_id"]:
        raise ValueError("graph-fit inputs do not share a run")
    if destination_decision["run_id"] != plan["run_id"]:
        raise ValueError("destination and query plan run bindings differ")
    if connected_evidence_map["graph_snapshot"] != active_pair["graph_snapshot"]:
        raise ValueError("connected evidence map does not match active graph snapshot")
    if connected_evidence_map["graph_sha256"] != active_pair["graph_sha256"]:
        raise ValueError("connected evidence map does not match active graph hash")
    if evidence_packet.get("graph_snapshot") != active_pair["graph_snapshot"]:
        raise ValueError("evidence packet does not match active graph snapshot")

    lane_ids = connected_evidence_map["selected_record_ids_by_lane"]
    lane_counts = {
        lane: len(lane_ids[lane])
        for lane in ("approved", "candidate", "external_public")
    }
    selected_count = sum(lane_counts.values())
    path_count = len(connected_evidence_map["paths"])
    minimum_records = 1
    reason_codes: list[str] = []
    if selected_count < minimum_records:
        reason_codes.append("NO_AUTHORIZED_GRAPH_RECORDS")
    if destination == "NEE_DECISION_INTELLIGENCE" and path_count < 1:
        reason_codes.append("NO_CONNECTED_GRAPH_EVIDENCE")
    passed = not reason_codes
    if passed:
        reason_codes.append(
            "GRAPH_FIT_CONFIRMED"
            if destination == "NEE_DECISION_INTELLIGENCE"
            else "BOUNDED_GRAPH_EVIDENCE_AVAILABLE"
        )
    artifact_id = stable_id(
        "query-graph-fit",
        plan["run_id"],
        destination_decision["canonical_digest"]["value"],
        connected_evidence_map["canonical_digest"]["value"],
        length=28,
    )
    return create_artifact(
        "query-graph-fit-decision",
        artifact_id,
        decision_id=artifact_id,
        run_id=plan["run_id"],
        destination_decision_digest=destination_decision["canonical_digest"]["value"],
        query_plan_digest=plan["canonical_digest"]["value"],
        connected_evidence_map_digest=connected_evidence_map["canonical_digest"]["value"],
        evidence_packet_digest=evidence_packet["packet_digest"],
        graph_snapshot=active_pair["graph_snapshot"],
        graph_sha256=active_pair["graph_sha256"],
        generation_id=active_pair["generation_id"],
        embedding_backend=active_pair["embedding_backend"],
        model_id=active_pair["model_id"],
        model_revision=active_pair.get("model_revision"),
        model_manifest_digest=active_pair.get("model_manifest_digest"),
        production_qualified=bool(active_pair["production_qualified"]),
        destination=destination,
        query_type=plan["query_type"],
        status="PASS" if passed else "FAIL",
        reason_codes=reason_codes,
        minimum_record_count=minimum_records,
        selected_record_count=selected_count,
        graph_path_count=path_count,
        authorized_lane_counts=lane_counts,
        l2_allowed=bool(destination == "NEE_DECISION_INTELLIGENCE" and passed),
        automatic_provider_call_count=0,
        decided_at=utc_now(),
    )
