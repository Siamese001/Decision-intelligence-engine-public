"""Graph change set creation and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from dual_pipeline.a2a_finalization import VerifiedA2AFinalization
from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import (
    connect_sqlite,
    digest,
    sha256_file,
    stable_id,
)
from dual_pipeline.project_status import candidate_snapshot_profiles
from dual_pipeline.strategic_ingestion import candidate_strategic_profile

from .change_set_helpers import (
    collect_document_connected_entities,
    emit_assertion_operations,
    emit_entity_creation_operations,
    process_supersession_plans,
    resolve_base_graph_snapshot,
    resolve_relationship_predicate,
    resolve_unbound_internal_entities,
    should_suppress_document_candidate,
    validate_a2a_staging_finalization,
    validate_and_query_entity_existence,
)
from .constants import EXTERNAL_CONCEPT_OUTCOME
from .hitl import (
    _candidate_governance,
    _decision_map,
    _forbid_a2a_approved_fact_promotion,
    _validate_a2a_source_span_review_binding,
)
from .proposals import (
    _a2a_guidance_anchor,
    _candidate_resolution_map,
    _external_concept_proposal,
    _new_internal_entity_proposal,
    _semantic_correction_control,
)


def build_graph_change_set(
    bundle: dict[str, Any],
    dispositions: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
    graph_path: Path,
    decisions: Iterable[dict[str, Any]] = (),
    *,
    a2a_finalization_proof: VerifiedA2AFinalization | None = None,
    a2a_finalization_reference: dict[str, Any] | None = None,
    a2a_finalization_runtime_root: Path | None = None,
    base_graph_snapshot: str | None = None,
) -> dict[str, Any] | None:
    candidate_by_id = {value["candidate_id"]: value for value in bundle["candidates"]}
    resolution_by_candidate = _candidate_resolution_map(bundle, resolutions)
    decision_by_candidate = _decision_map(decisions)
    status_profiles_by_candidate = candidate_snapshot_profiles(bundle)
    edge_traits_map = bundle.get("extensions", {}).get("com.partner_advisory.nee.edge-traits", {})
    _forbid_a2a_approved_fact_promotion(bundle, decision_by_candidate)
    operations: list[dict[str, Any]] = []
    payloads: dict[str, dict[str, Any]] = {}
    assertion_plans: list[dict[str, Any]] = []
    seen_assertion_fps: dict[tuple[Any, ...], dict[str, Any]] = {}
    external_entities: dict[str, dict[str, Any]] = {}
    internal_entities: dict[str, dict[str, Any]] = {}
    supersession_plans: list[dict[str, Any]] = []
    decision: dict[str, Any] | None = None
    internal_proposal: dict[str, Any] | None = None

    a2a_source_bundle, verified_a2a_binding, verified_a2a_reference = validate_a2a_staging_finalization(
        bundle=bundle, dispositions=dispositions, a2a_finalization_proof=a2a_finalization_proof,
        a2a_finalization_reference=a2a_finalization_reference, a2a_finalization_runtime_root=a2a_finalization_runtime_root,
    )
    doc_connected_entities = collect_document_connected_entities(
        bundle, dispositions, candidate_by_id, resolution_by_candidate, decision_by_candidate
    )

    for disposition in dispositions:
        candidate = candidate_by_id[disposition["candidate_id"]]
        candidate_id = candidate["candidate_id"]
        candidate_resolutions = resolution_by_candidate[candidate_id]
        decision = decision_by_candidate.get(candidate_id)
        governance = _candidate_governance(bundle, candidate_id)
        strategic_profile = candidate_strategic_profile(bundle, candidate_id)
        correction = _semantic_correction_control(bundle, candidate_id)
        if correction:
            if correction["retire_only"]:
                if decision is None:
                    if disposition["disposition"] == "QUARANTINE":
                        continue
                    raise ValueError(
                        "retire-only semantic correction requires a formal HITL decision"
                    )
                if decision["decision"] != "REJECT" or disposition["disposition"] != "REJECTED_NOOP":
                    raise ValueError(
                        "retire-only semantic correction requires a rejected replacement candidate"
                    )
                supersession_plans.append({
                    "candidate": candidate, "disposition": disposition["disposition"],
                    "replacement_assertion_id": None, "superseded_assertion_id": correction["supersedes_assertion_id"],
                })
                continue
        if disposition["disposition"] not in {"APPROVED", "CANDIDATE"}:
            continue
        if correction and decision is None:
            raise ValueError(
                "semantic correction graph staging requires an exact formal HITL decision"
            )
        a2a_source = a2a_source_bundle
        if a2a_source:
            if governance.get("force_hitl") is not True:
                raise ValueError("A2A candidate graph staging requires forced individual HITL")
            if decision is None:
                raise ValueError(
                    "A2A candidate graph staging requires an exact formal HITL decision"
                )
            if (
                candidate["candidate_type"] == "assertion"
                and not governance.get("source_selection_bindings")
            ):
                raise ValueError(
                    "A2A assertion graph staging requires selected, attested source spans"
                )
            if governance.get("source_selection_bindings"):
                if governance.get("finalization_binding") is None:
                    raise ValueError(
                        "A2A selected-span graph staging requires a replayed finalization receipt"
                    )
                if governance["finalization_binding"] != verified_a2a_binding:
                    raise ValueError(
                        "A2A candidate graph staging receipt binding is not the verifier-issued proof"
                    )
            _validate_a2a_source_span_review_binding(governance, decision)
            if decision["decision"] in {"HOLD", "REJECT", "QUARANTINE"}:
                raise ValueError(
                    "a held, rejected, or quarantined A2A decision cannot stage a graph candidate"
                )
        decision_value = decision or {}
        selected_entities = [
            value["selected_entity_id"]
            for value in candidate_resolutions
            if value["selected_entity_id"]
        ]
        selected_by_reviewer = decision_value.get("extensions", {}).get(
            "com.partner_advisory.nee.selected-entity-id"
        )
        guidance_anchor = _a2a_guidance_anchor(bundle, candidate, decision)
        if selected_by_reviewer:
            selected_entities = [selected_by_reviewer] + [
                value for value in selected_entities if value != selected_by_reviewer
            ]
        internal_proposal = _new_internal_entity_proposal(
            candidate=candidate,
            resolutions=candidate_resolutions,
            decision=decision,
        )
        if internal_proposal is not None:
            matching_res = [
                res for res in candidate_resolutions
                if res.get("stable_identifier", {}).get("value") == internal_proposal["source_stable_identifier"]
                and res.get("reported_name") == internal_proposal["canonical_name"]
            ]
            if selected_by_reviewer or any(res.get("selected_entity_id") for res in matching_res) or (
                len(candidate_resolutions) == 1 and selected_entities
            ):
                raise ValueError(
                    "CREATE_NEW cannot substitute or reuse an existing governed entity"
                )
            entity_plan = {
                "candidate": candidate,
                "disposition": "CANDIDATE",
                "entity": internal_proposal,
                "entity_kind": "internal",
            }
            existing = internal_entities.get(internal_proposal["entity_id"])
            if existing is not None and existing["entity"] != internal_proposal:
                raise ValueError("internal entity identity has conflicting proposals")
            internal_entities.setdefault(internal_proposal["entity_id"], entity_plan)
            if len(candidate_resolutions) <= 1:
                selected_entities = [internal_proposal["entity_id"]]
            else:
                selected_entities = resolve_unbound_internal_entities(
                    candidate_resolutions, internal_entities, selected_entities, candidate
                )
        else:
            selected_entities = resolve_unbound_internal_entities(
                candidate_resolutions, internal_entities, selected_entities, candidate
            )
        if guidance_anchor is not None:
            connection = connect_sqlite(graph_path, readonly=True)
            try:
                anchor_row = connection.execute(
                    "SELECT node_type,active FROM node WHERE node_id=?",
                    (guidance_anchor,),
                ).fetchone()
            finally:
                connection.close()
            if (
                anchor_row is None
                or anchor_row["node_type"] != "pattern"
                or anchor_row["active"] != 1
            ):
                raise ValueError(
                    "A2A guidance anchor must reference an active governed pattern node"
                )
            selected_entities = [guidance_anchor]
        external_proposals = [
            _external_concept_proposal(value, bundle)
            for value in candidate_resolutions
            if value["outcome"] == EXTERNAL_CONCEPT_OUTCOME
        ]
        if external_proposals:
            if decision_value.get("decision") != EXTERNAL_CONCEPT_OUTCOME:
                raise ValueError(
                    "external concept cannot produce graph operations without an exact HITL creation decision"
                )
            if selected_by_reviewer or selected_entities:
                raise ValueError(
                    "source-scoped external concept cannot be substituted with an internal entity"
                )
            selected_entities = [proposal["external_concept_id"] for proposal in external_proposals]
            for proposal in external_proposals:
                existing = external_entities.get(proposal["external_concept_id"])
                entity_plan = {
                    "candidate": candidate,
                    "disposition": "CANDIDATE",
                    "entity": proposal,
                    "entity_kind": "external",
                }
                if existing is not None and existing["entity"] != proposal:
                    raise ValueError(
                        "external concept identity collides with different source scope"
                    )
                external_entities.setdefault(proposal["external_concept_id"], entity_plan)
        corroborate_id = (
            disposition.get("extensions", {}).get("com.partner_advisory.nee.corroborate-assertion-id")
            or candidate.get("corroborate_assertion_id")
        )
        if corroborate_id and not selected_entities:
            conn_corr = connect_sqlite(graph_path, readonly=True)
            try:
                corr_row = conn_corr.execute(
                    "SELECT subject_node_id FROM assertion WHERE assertion_id=?",
                    (corroborate_id,),
                ).fetchone()
                if corr_row and corr_row[0]:
                    selected_entities = [corr_row[0]]
            finally:
                conn_corr.close()
        if not selected_entities or (
            not corroborate_id
            and should_suppress_document_candidate(
                candidate, selected_entities, doc_connected_entities, correction, a2a_source
            )
        ):
            continue
        if corroborate_id:
            operation = "add_evidence"
            target_id = corroborate_id
            operation_id = stable_id(
                "graph-operation", bundle["run_id"], "evidence", candidate["candidate_id"], length=28
            )
        elif (
            candidate["candidate_type"] in {"architecture_relationship", "relationship"}
            and len(selected_entities) >= 2
        ):
            operation = "add_relationship"
            target_id = stable_id("assertion", candidate["candidate_id"], length=28)
            operation_id = stable_id("graph-operation", bundle["run_id"], target_id, length=28)
        elif candidate["candidate_type"] == "action":
            operation = "add_action_item"
            target_id = stable_id("assertion", candidate["candidate_id"], length=28)
            operation_id = stable_id("graph-operation", bundle["run_id"], target_id, length=28)
        elif disposition["disposition"] == "APPROVED":
            operation = "add_approved_assertion"
            target_id = stable_id("assertion", candidate["candidate_id"], length=28)
            operation_id = stable_id("graph-operation", bundle["run_id"], target_id, length=28)
        else:
            operation = "add_candidate_assertion"
            target_id = stable_id("assertion", candidate["candidate_id"], length=28)
            operation_id = stable_id("graph-operation", bundle["run_id"], target_id, length=28)
        rel_pred = (
            resolve_relationship_predicate(candidate, candidate_id, edge_traits_map)
            if operation == "add_relationship"
            else "HAS_CLAIM_TEXT"
        )
        payload = {
            "candidate": candidate,
            "disposition": disposition["disposition"],
            "subject_node_id": selected_entities[0],
            "object_node_id": selected_entities[1] if (operation == "add_relationship" and len(selected_entities) >= 2) else None,
            "predicate": rel_pred,
            "action_status": "open" if operation == "add_action_item" else None,
        }
        if candidate_id in edge_traits_map:
            payload["edge_traits"] = edge_traits_map[candidate_id]
        if operation == "add_evidence":
            payload["assertion_id"] = target_id
        status_profile = status_profiles_by_candidate.get(candidate_id)
        if status_profile is not None:
            if candidate["candidate_type"] != "source_chunk" or disposition["disposition"] != "CANDIDATE":
                raise ValueError("project-status snapshots may persist only as candidate source chunks")
            payload["project_status_snapshot"] = status_profile
        if governance:
            payload["retrieval_governance"] = {
                key: governance[key]
                for key in (
                    "source_item_id",
                    "retrieval_kind",
                    "answer_use",
                    "fact_promotion_eligible",
                    "promotion_status",
                )
            }
            if governance.get("source_selection_bindings") is not None:
                payload["retrieval_governance"]["a2a_source_span_bindings"] = (
                    governance["source_selection_bindings"]
                )
            if governance.get("finalization_binding") is not None:
                payload["retrieval_governance"]["a2a_finalization_binding"] = (
                    governance["finalization_binding"]
                )
            if verified_a2a_reference is not None:
                payload["retrieval_governance"]["a2a_finalization_reference"] = (
                    verified_a2a_reference
                )
            if a2a_source:
                payload["retrieval_governance"]["source_kind"] = "a2a_fact_vector_packet"
        if strategic_profile is not None:
            payload["strategic_profile"] = strategic_profile
        raw_scope_text = str(candidate.get("normalized_text", ""))
        norm_scope_text = " ".join(
            __import__("re")
            .sub(r"\s+in\s+[^.]+?\.\s*$", "", raw_scope_text)
            .replace("\u2019", "'")
            .replace("\u2014", "-")
            .casefold()
            .split()
        ).strip(" .;:,-")
        norm_pred = str(payload.get("predicate") or "HAS_CLAIM_TEXT").strip().upper().replace(" ", "_")
        fp_key = (
            payload.get("subject_node_id"),
            norm_pred,
            payload.get("object_node_id"),
            None if payload.get("object_node_id") else norm_scope_text,
            norm_scope_text,
        )
        if operation in {"add_candidate_assertion", "add_approved_assertion", "add_relationship", "add_action_item"} and fp_key in seen_assertion_fps:
            prior_plan = seen_assertion_fps[fp_key]
            for eid in candidate.get("evidence_ids", []):
                if eid not in prior_plan["payload"]["candidate"]["evidence_ids"]:
                    prior_plan["payload"]["candidate"]["evidence_ids"].append(eid)
            continue
        plan_dict = {
            "operation": operation,
            "operation_id": operation_id,
            "payload": payload,
            "target_id": target_id,
        }
        seen_assertion_fps[fp_key] = plan_dict
        assertion_plans.append(plan_dict)
        if operation in {"add_candidate_assertion", "add_approved_assertion"}:
            for ws in (e for e in selected_entities[1:] if e != selected_entities[0] and (e.startswith(("workstream_", "program_", "sow_", "project:", "org_", "org:", "deliverable_", "pattern_")) or ":workstream:" in e.lower() or "conduit" in e.lower())):
                ws_tid = stable_id("assertion", candidate["candidate_id"], ws, length=28)
                ws_opid = stable_id("graph-operation", bundle["run_id"], ws_tid, length=28)
                ws_pl = {**payload, "subject_node_id": ws, "object_node_id": None, "predicate": "HAS_CLAIM_TEXT"}
                ws_fp = (ws, "HAS_CLAIM_TEXT", None, norm_scope_text, norm_scope_text)
                if ws_fp not in seen_assertion_fps:
                    ws_p = {"operation": operation, "operation_id": ws_opid, "payload": ws_pl, "target_id": ws_tid}
                    seen_assertion_fps[ws_fp] = ws_p
                    assertion_plans.append(ws_p)
        if correction:
            supersession_plans.append({
                "candidate": candidate, "disposition": disposition["disposition"],
                "replacement_assertion_id": target_id,
                "superseded_assertion_id": correction["supersedes_assertion_id"],
            })

    existing_external_ids, existing_internal_ids = validate_and_query_entity_existence(
        graph_path, external_entities, internal_entities, decision, internal_proposal
    )
    emit_entity_creation_operations(
        bundle, external_entities, existing_external_ids, internal_entities, existing_internal_ids, payloads, operations
    )

    emit_assertion_operations(assertion_plans, payloads, operations)
    process_supersession_plans(
        bundle=bundle,
        graph_path=graph_path,
        supersession_plans=supersession_plans,
        payloads=payloads,
        operations=operations,
    )
    if not operations:
        return None
    base_graph_snapshot = resolve_base_graph_snapshot(
        graph_path=graph_path,
        base_graph_snapshot=base_graph_snapshot,
        a2a_finalization_runtime_root=a2a_finalization_runtime_root,
    )
    change_set_id = stable_id("change-set", bundle["run_id"], digest(operations), length=28)
    source_metadata = bundle.get("extensions", {}).get("com.partner_advisory.nee.source", {})
    context = bundle.get("extensions", {}).get("com.partner_advisory.nee.context", {})
    return create_artifact(
        "graph-change-set",
        change_set_id,
        change_set_id=change_set_id,
        run_id=bundle["run_id"],
        trace_id=bundle["trace_id"],
        status="proposed",
        base_graph_snapshot=base_graph_snapshot,
        base_graph_hash=sha256_file(graph_path),
        approval_id=None,
        operations=operations,
        transaction_required=True,
        rollback_required=True,
        extensions={
            "com.partner_advisory.nee.operation-payloads": payloads,
            "com.partner_advisory.nee.external-concepts": [external_entities[k]["entity"] for k in sorted(external_entities)],
            "com.partner_advisory.nee.internal-entity-proposals": [internal_entities[k]["entity"] for k in sorted(internal_entities)],
            "com.partner_advisory.nee.source": {
                **source_metadata,
                "source_id": bundle["source_id"], "source_version_id": bundle["source_version_id"],
                "source_posture": context.get("source_posture", "derivative_internal"),
                "authority_rank": int(context.get("authority_rank", 5)), "evidence": bundle["evidence"],
            },
            "com.partner_advisory.nee.edge-traits": edge_traits_map,
        },
    )
