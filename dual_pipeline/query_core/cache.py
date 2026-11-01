"""Query caching context, output caching, and receipt emission utilities."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from dual_pipeline.authorization import AuthorizationContext
from dual_pipeline.contracts import create_artifact, validate_artifact
from dual_pipeline.core import digest, stable_id
from dual_pipeline.index import HYBRID_RETRIEVAL_POLICY_DIGEST
from dual_pipeline.l1_control import L1ControlPolicy
from dual_pipeline.l2 import L2Config
from dual_pipeline.query_l1_shadow import (
    ShadowReceiptSet,
    build_shadow_receipts,
    shadow_telemetry_payload,
    write_shadow_receipts,
)
from dual_pipeline.telemetry import RunRecorder
from storage.semantic_cache import SemanticCacheLookup

from .constants import CITATION_ONLY_SOURCE_EVIDENCE_RETRIEVAL_MODES


def _delivery_evidence_ready(plan: dict[str, Any], packet: dict[str, Any]) -> bool:
    """Decide whether the permitted lanes contain deliverable evidence.

    Current and historical project-status routes are intentionally candidate
    lane, citation-only evidence.  They have no approved-fact lane by design,
    so treating them like ordinary factual retrieval would incorrectly block
    a citation-complete, provider-free response at X2.
    """

    approved_ready = any(
        not (isinstance(item, dict) and item.get("is_quarantined"))
        for item in packet.get("approved", [])
    )
    candidate_ready = any(
        not (isinstance(item, dict) and item.get("is_quarantined"))
        for item in packet.get("candidate", [])
    )
    return approved_ready or (
        plan["retrieval_mode"] in CITATION_ONLY_SOURCE_EVIDENCE_RETRIEVAL_MODES
        and candidate_ready
    )


def _correlation(query_id: str) -> dict[str, str | None]:
    return {"source_id": None, "candidate_id": None, "query_id": query_id, "graph_operation_id": None, "hitl_item_id": None}


def _emit_l1_shadow_receipts(
    *,
    mode: str,
    policy: L1ControlPolicy | None,
    plan: dict[str, Any],
    coverage_manifest: dict[str, Any],
    evidence_report: dict[str, Any],
    active: dict[str, Any],
    run_dir: Path,
    output_dir: Path | None,
    recorder: RunRecorder,
    request_id: str,
    runtime_terminal_outcome: str,
    runtime_next_stage: str,
    graph_sha256_before: str,
    graph_sha256_after: str,
    comparison_projection: dict[str, Any],
) -> ShadowReceiptSet | None:
    """Write Wave 4 artifacts without adding a query stage or doing work."""

    if mode == "off":
        return None
    if policy is None:
        raise RuntimeError("shadow L1 mode requires a loaded controller policy")
    receipts = build_shadow_receipts(
        plan_v2=plan,
        coverage_manifest=coverage_manifest,
        evidence_report=evidence_report,
        active_pair=active,
        policy=policy,
        mode=mode,
        runtime_terminal_outcome=runtime_terminal_outcome,
        runtime_next_stage=runtime_next_stage,
        graph_sha256_before=graph_sha256_before,
        graph_sha256_after=graph_sha256_after,
        off_projection=comparison_projection,
        shadow_projection=comparison_projection,
    )
    write_shadow_receipts(run_dir, receipts, output_dir=output_dir)
    telemetry = shadow_telemetry_payload(
        receipts,
        runtime_terminal_outcome=runtime_terminal_outcome,
        runtime_next_stage=runtime_next_stage,
    )
    recorder.record(
        stage="L1",
        event_type="l1_shadow",
        payload=telemetry,
        correlation=_correlation(request_id),
        input_digest=plan["canonical_digest"]["value"],
        record_count=telemetry.get("selected_record_count", 0),
    )
    return receipts


def _query_cache_context(
    *,
    active: dict[str, Any],
    route: dict[str, Any],
    plan: dict[str, Any],
    classification: str,
    audiences: list[str],
    user_id: str,
    authorization_context: AuthorizationContext | None,
    l2_config: L2Config,
    destination_policy_digest: str,
    x1_policy_digest: str,
    provider_egress_allowed: bool,
    coverage_requirements_digest: str,
    card_scope: dict[str, Any] | None = None,
    l1_controller_mode: str = "off",
    l1_control_policy: L1ControlPolicy | None = None,
    decision_frame_digest: str | None = None,
) -> dict[str, Any]:
    """Bind semantic replay to policy, authorization, and an optional card view."""

    context = {
        "use_case": "query",
        "cache_payload_kind": "sealed_query_output_v4",
        "query_plan_version": plan["schema_version"],
        "l1_controller_mode": l1_controller_mode,
        "l1_controller_policy_digest": l1_control_policy.policy_digest if l1_control_policy is not None else None,
        "l1_action_policy_digest": digest(l1_control_policy.action_allowlist) if l1_control_policy is not None else None,
        "l1_budget_profile": (
            {
                "max_additional_attempts": l1_control_policy.max_additional_attempts,
                "max_actions": l1_control_policy.max_actions,
                "max_unique_records": l1_control_policy.max_unique_records,
                "max_result_bytes": l1_control_policy.max_result_bytes,
                "max_elapsed_ms": l1_control_policy.max_elapsed_ms,
            }
            if l1_control_policy is not None
            else None
        ),
        "destination": plan["destination"],
        "eligibility_policy_digest": destination_policy_digest,
        "assurance_profile": plan["assurance_profile"],
        "delivery_profile": plan["delivery_profile"],
        "artifact_type": plan["artifact_type"],
        "route": route["query_route"],
        "query_type": plan["query_type"],
        "materiality": plan["materiality"],
        "retrieval_mode": plan["retrieval_mode"],
        "allowed_lanes": list(plan["allowed_lanes"]),
        "retrieval_policy_digest": digest(
            {
                "plan": plan["retrieval"],
                "hybrid": HYBRID_RETRIEVAL_POLICY_DIGEST,
            }
        ),
        "classification": classification,
        "audiences": sorted(audiences),
        "subject_scope_digest": digest(
            {
                "subject": user_id,
                "classification": classification,
                "audiences": sorted(audiences),
            }
        ),
        "authorization_context_digest": (
            authorization_context.context_digest if authorization_context else None
        ),
        "active_pair": {
            key: active.get(key)
            for key in (
                "generation_id",
                "graph_snapshot",
                "graph_sha256",
                "sidecar_sha256",
                "embedding_backend",
                "model_id",
                "model_revision",
                "model_manifest_digest",
                "production_qualified",
            )
        },
        "l2_policy_digest": l2_config.config_digest,
        "x1_policy_digest": x1_policy_digest,
        "provider_egress_allowed": provider_egress_allowed,
        "coverage_requirements_digest": coverage_requirements_digest,
        "decision_frame_digest": decision_frame_digest,
    }
    if card_scope is not None:
        context["card_scope"] = {
            "card_id": card_scope["card_id"],
            "card_view_id": card_scope["card_view_id"],
            "view_version": card_scope["view_version"],
            "selector_digest": card_scope["selector_digest"],
            "membership_digest": card_scope["membership_digest"],
        }
    return context


def _validate_cached_controller(
    controller: dict[str, Any], l1_controller_mode: str,
    l1_control_policy: L1ControlPolicy | None, plan: dict[str, Any], report: dict[str, Any],
) -> bool:
    expected_policy = l1_control_policy.policy_digest if l1_control_policy is not None else None
    expected_report = report.get("canonical_digest", {}).get("value")
    return not (
        controller.get("mode") != l1_controller_mode
        or controller.get("policy_digest") != expected_policy
        or controller.get("plan_version") != plan.get("schema_version")
        or controller.get("final_evidence_report_digest") != expected_report
    )


def _validate_cached_digests(
    value: dict[str, Any], packet: dict[str, Any], manifest: dict[str, Any],
    report: dict[str, Any], claim_map: dict[str, Any], connected_map: dict[str, Any],
) -> bool:
    man_dig = manifest.get("canonical_digest", {}).get("value")
    rep_dig = report.get("canonical_digest", {}).get("value")
    pkt_dig = packet.get("packet_digest")
    con_dig = connected_map.get("canonical_digest", {}).get("value")
    return not (
        report.get("coverage_manifest_digest") != man_dig
        or report.get("evidence_packet_digest") != pkt_dig
        or report.get("validated_request_digest") != manifest.get("validated_request_digest")
        or report.get("query_plan_digest") != manifest.get("query_plan_digest")
        or report.get("authorization_scope_digest") != manifest.get("authorization_scope_digest")
        or claim_map.get("evidence_report_digest") != rep_dig
        or claim_map.get("coverage_manifest_digest") != man_dig
        or claim_map.get("evidence_packet_digest") != pkt_dig
        or claim_map.get("answer_digest") != digest(value["answer"])
        or con_dig != value["graph_retrieval"].get("connected_evidence_map_digest")
        or con_dig != value["graph_hydration"].get("connected_evidence_map_digest")
    )


def _validate_cached_card_scope(packet: dict[str, Any], plan: dict[str, Any]) -> bool:
    planned = plan.get("extensions", {}).get("com.partner_advisory.nee.card-scope")
    cached = packet.get("card_scope")
    if planned is None:
        return cached is None
    if not isinstance(cached, dict):
        return False
    return (
        cached.get("membership_digest") == planned.get("membership_digest")
        and cached.get("selector_digest") == planned.get("selector_digest")
    )


def _validate_cached_packet_bindings(
    packet: dict[str, Any], connected_map: dict[str, Any], active: dict[str, Any],
    plan: dict[str, Any], lookup: SemanticCacheLookup,
    manifest: dict[str, Any], report: dict[str, Any], claim_map: dict[str, Any],
) -> bool:
    source_run_id = lookup.source_run_id
    artifact_run_ids = {artifact.get("run_id") for artifact in (manifest, report, claim_map, connected_map)}
    if len(artifact_run_ids) != 1 or None in artifact_run_ids or (source_run_id is not None and artifact_run_ids != {source_run_id}):
        return False
    if packet.get("graph_snapshot") != active["graph_snapshot"] or packet.get("retrieval_mode") != plan["retrieval_mode"]:
        return False
    if active.get("graph_sha256") and connected_map.get("graph_sha256") != active.get("graph_sha256"):
        return False
    if active.get("generation_id") and packet.get("generation_id") and packet.get("generation_id") != active.get("generation_id"):
        return False
    return packet.get("packet_digest") == digest({key: item for key, item in packet.items() if key != "packet_digest"})


def _cached_query_output(
    lookup: SemanticCacheLookup,
    *,
    active: dict[str, Any],
    plan: dict[str, Any],
    l1_controller_mode: str = "off",
    l1_control_policy: L1ControlPolicy | None = None,
) -> dict[str, Any] | None:
    """Reject cache material unless it is still bound to this exact active pair."""

    value = lookup.output
    if not lookup.hit or not isinstance(value, dict):
        return None
    required = {
        "exact_retrieval", "hybrid_retrieval", "graph_retrieval", "connected_evidence_map",
        "graph_hydration", "evidence_packet", "answer", "query_coverage_manifest",
        "retrieval_evidence_report", "claim_evidence_map", "l1_controller",
    }
    if not required <= set(value) or not all(isinstance(value[key], dict) for key in required):
        return None
    report = value["retrieval_evidence_report"]
    if not _validate_cached_controller(value["l1_controller"], l1_controller_mode, l1_control_policy, plan, report):
        return None
    packet, manifest = value["evidence_packet"], value["query_coverage_manifest"]
    claim_map, connected_map = value["claim_evidence_map"], value["connected_evidence_map"]
    try:
        for artifact in (manifest, report, claim_map, connected_map):
            validate_artifact(artifact)
    except (KeyError, TypeError, ValueError):
        return None
    if not _validate_cached_packet_bindings(packet, connected_map, active, plan, lookup, manifest, report, claim_map):
        return None
    if not _validate_cached_digests(value, packet, manifest, report, claim_map, connected_map):
        return None
    return value if _validate_cached_card_scope(packet, plan) else None


def _rebind_cached_connected_evidence_map(
    source: dict[str, Any],
    *,
    run_id: str,
    query_plan_digest: str,
    lookup: SemanticCacheLookup,
) -> dict[str, Any]:
    """Re-seal cached graph evidence for the replay run without new retrieval."""

    source_digest = source["canonical_digest"]["value"]
    map_id = stable_id(
        "connected-evidence-map",
        run_id,
        source_digest,
        query_plan_digest,
        length=28,
    )
    extensions = deepcopy(source.get("extensions", {}))
    extensions["com.partner_advisory.nee.semantic-cache-replay"] = {
        "cache_entry_id": lookup.entry_id,
        "source_run_id": lookup.source_run_id or source["run_id"],
        "source_trace_id": lookup.source_trace_id,
        "source_artifact_digest": source_digest,
        "source_output_digest": lookup.source_output_digest,
    }
    return create_artifact(
        "connected-evidence-map",
        map_id,
        map_id=map_id,
        run_id=run_id,
        query_plan_digest=query_plan_digest,
        graph_snapshot=source["graph_snapshot"],
        graph_sha256=source["graph_sha256"],
        retrieval_policy=deepcopy(source["retrieval_policy"]),
        requested_hops=source["requested_hops"],
        max_hop_reached=source["max_hop_reached"],
        seed_record_ids=list(source["seed_record_ids"]),
        selected_record_ids_by_lane=deepcopy(
            source["selected_record_ids_by_lane"]
        ),
        paths=deepcopy(source["paths"]),
        counterevidence=deepcopy(source["counterevidence"]),
        gaps=list(source["gaps"]),
        metrics=deepcopy(source["metrics"]),
        automatic_graph_mutation_count=0,
        extensions=extensions,
    )


def _rebind_cached_retrieval_artifacts(
    cached_output: dict[str, Any],
    *,
    run_id: str,
    plan: dict[str, Any],
    lookup: SemanticCacheLookup,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return current-run graph/map/packet artifacts over cached selections."""

    connected_map = _rebind_cached_connected_evidence_map(
        cached_output["connected_evidence_map"],
        run_id=run_id,
        query_plan_digest=plan["canonical_digest"]["value"],
        lookup=lookup,
    )
    map_id = connected_map["map_id"]
    map_digest = connected_map["canonical_digest"]["value"]

    graph_retrieval = deepcopy(cached_output["graph_retrieval"])
    graph_retrieval["connected_evidence_map_id"] = map_id
    graph_retrieval["connected_evidence_map_digest"] = map_digest

    hydrated = deepcopy(cached_output["graph_hydration"])
    hydrated["connected_evidence_map_id"] = map_id
    hydrated["connected_evidence_map_digest"] = map_digest

    packet = deepcopy(cached_output["evidence_packet"])
    packet["connected_evidence_map"] = {
        "map_id": map_id,
        "map_digest": map_digest,
        "requested_hops": connected_map["requested_hops"],
        "max_hop_reached": connected_map["max_hop_reached"],
        "metrics": deepcopy(connected_map["metrics"]),
    }
    packet["packet_digest"] = digest(
        {key: value for key, value in packet.items() if key != "packet_digest"}
    )
    return graph_retrieval, connected_map, hydrated, packet


