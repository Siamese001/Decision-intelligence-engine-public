"""Helper utilities for graph change set entity resolution and operation emission."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dual_pipeline.a2a_finalization import VerifiedA2AFinalization, reverify_ingress_finalization_evidence, validated_ingress_finalization_reference, verified_finalization_binding
from dual_pipeline.core import ZERO_HASH, connect_sqlite, digest, sha256_file, stable_id
from dual_pipeline.node_type_policy import require_creatable_node_type

from .hitl import _candidate_governance
from .proposals import _is_a2a_source_bundle, _semantic_correction_control


def _significant_tokens(label: Any) -> list[str]:
    return [t for t in str(label or "").lower().split() if len(t) > 2 and t not in {"the", "and", "for", "with"}]


def _passes_token_score_gate(tokens: list[str], top_tokens: list[str], score: float, margin: Any) -> bool:
    eff_margin = 1.0 if margin is None else float(margin)
    if len(tokens) >= 2:
        return len(top_tokens) >= 2 and score >= 0.78 and eff_margin >= 0.20
    return len(tokens) == 1 and score >= 0.90 and eff_margin >= 0.25


def _select_high_confidence_existing_entity(res: dict[str, Any]) -> str | None:
    """Return top catalog entity_id when multi-token or single-token high-confidence gates pass."""
    if res.get("outcome") in ("AUTO_LINK_EXACT", "AUTO_LINK_HIGH_CONFIDENCE") and res.get("selected_entity_id"):
        return str(res["selected_entity_id"])
    top = (res.get("alternatives") or [None])[0]
    if not isinstance(top, dict) or top.get("identity_conflicts"):
        return None
    has_containment = any("Reported tokens occur in the canonical name" in s for s in (top.get("supporting_context") or []))
    if not has_containment:
        return None
    tokens, top_tokens = _significant_tokens(res.get("reported_name")), _significant_tokens(top.get("canonical_name"))
    if _passes_token_score_gate(tokens, top_tokens, float(top.get("match_score") or 0.0), top.get("margin_over_next")):
        return top.get("entity_id")
    return None


def _extract_entity_proposal(
    res: dict[str, Any], candidate: dict[str, Any], internal_entities: dict[str, dict[str, Any]],
) -> str | None:
    """Extract candidate proposal entity ID if valid assessment exists."""
    existing_id = _select_high_confidence_existing_entity(res)
    if existing_id:
        return existing_id
    assessment = res.get("extensions", {}).get("com.partner_advisory.nee.new-node-assessment")
    if not (assessment and assessment.get("status") == "PROPOSE_NEW_CANDIDATE"):
        return None
    prop = assessment.get("proposal")
    if not prop or not prop.get("entity_id"):
        return None
    ent_id = prop["entity_id"]
    if ent_id not in internal_entities:
        internal_entities[ent_id] = {"candidate": candidate, "disposition": "CANDIDATE", "entity": dict(prop), "entity_kind": "internal"}
    return ent_id


def resolve_unbound_internal_entities(
    candidate_resolutions: list[dict[str, Any]], internal_entities: dict[str, dict[str, Any]],
    selected_entities: list[str], candidate: dict[str, Any],
) -> list[str]:
    """Resolve entity proposals from candidate mentions when selected_entity_id is unbound."""
    ordered: list[str] = []
    for res in candidate_resolutions:
        ent_id = res.get("selected_entity_id") or _extract_entity_proposal(res, candidate, internal_entities)
        if ent_id and ent_id not in ordered:
            ordered.append(ent_id)
    return ordered if ordered and (not selected_entities or len(ordered) > len(selected_entities)) else selected_entities


def _query_and_validate_external_entities(
    graph_path: Path, external_entities: dict[str, dict[str, Any]],
) -> set[str]:
    """Query external concept nodes in SQLite and assert node_type and active constraints."""
    existing_external_ids: set[str] = set()
    if not external_entities:
        return existing_external_ids
    connection = connect_sqlite(graph_path, readonly=True)
    try:
        placeholders = ",".join("?" for _ in external_entities)
        rows = connection.execute(
            f"SELECT node_id,node_type,active FROM node WHERE node_id IN ({placeholders})", sorted(external_entities),
        ).fetchall()
        for row in rows:
            if row["node_type"] != "external_concept":
                raise ValueError("external concept identity collides with a non-external node")
            if row["active"] != 1:
                raise ValueError("external concept identity collides with an inactive node")
            existing_external_ids.add(row["node_id"])
    finally:
        connection.close()
    return existing_external_ids


def _query_and_validate_internal_entities(
    graph_path: Path, internal_entities: dict[str, dict[str, Any]],
    decision: dict[str, Any] | None, internal_proposal: dict[str, Any] | None,
) -> set[str]:
    """Query internal nodes in SQLite, assert creatable types, and check CREATE_NEW collisions."""
    existing_internal_ids: set[str] = set()
    if not internal_entities:
        return existing_internal_ids
    connection = connect_sqlite(graph_path, readonly=True)
    try:
        for value in internal_entities.values():
            require_creatable_node_type(connection, value["entity"].get("node_type"))
        placeholders = ",".join("?" for _ in internal_entities)
        rows = connection.execute(
            f"SELECT node_id FROM node WHERE node_id IN ({placeholders})", sorted(internal_entities),
        ).fetchall()
        existing_internal_ids.update(row["node_id"] for row in rows)
    finally:
        connection.close()
    if decision and decision.get("decision") == "CREATE_NEW" and internal_proposal and internal_proposal["entity_id"] in existing_internal_ids:
        raise ValueError("CREATE_NEW internal entity target already exists")
    return existing_internal_ids


def validate_and_query_entity_existence(
    graph_path: Path, external_entities: dict[str, dict[str, Any]], internal_entities: dict[str, dict[str, Any]],
    decision: dict[str, Any] | None, internal_proposal: dict[str, Any] | None,
) -> tuple[set[str], set[str]]:
    """Validate entity constraints against SQLite and query existing node IDs."""
    return (
        _query_and_validate_external_entities(graph_path, external_entities),
        _query_and_validate_internal_entities(graph_path, internal_entities, decision, internal_proposal),
    )


def _append_entity_create_op(
    operations: list[dict[str, Any]], payloads: dict[str, dict[str, Any]],
    operation_id: str, target_id: str, payload: dict[str, Any], candidate: dict[str, Any],
) -> None:
    payloads[operation_id] = payload
    operations.append({
        "operation_id": operation_id, "sequence": len(operations) + 1, "operation": "create_entity",
        "target_type": "entity", "target_id": target_id, "before_state_hash": ZERO_HASH,
        "after_state_hash": digest(payload), "evidence_ids": candidate["evidence_ids"],
        "classification": candidate["classification"], "audiences": candidate["audiences"],
        "candidate_id": candidate["candidate_id"],
    })


def emit_entity_creation_operations(
    bundle: dict[str, Any], external_entities: dict[str, dict[str, Any]], existing_external_ids: set[str],
    internal_entities: dict[str, dict[str, Any]], existing_internal_ids: set[str],
    payloads: dict[str, dict[str, Any]], operations: list[dict[str, Any]],
) -> None:
    """Emit create_entity operations for external and internal entity candidates."""
    for external_concept_id in sorted(external_entities):
        if external_concept_id not in existing_external_ids:
            payload = external_entities[external_concept_id]
            op_id = stable_id("graph-operation", bundle["run_id"], "external-concept", external_concept_id, length=28)
            _append_entity_create_op(operations, payloads, op_id, external_concept_id, payload, payload["candidate"])
    for entity_id in sorted(internal_entities):
        if entity_id not in existing_internal_ids:
            payload = internal_entities[entity_id]
            op_id = stable_id("graph-operation", bundle["run_id"], "internal-entity", entity_id, length=28)
            _append_entity_create_op(operations, payloads, op_id, entity_id, payload, payload["candidate"])


def _collect_selected_span_a2a_bindings(
    bundle: dict[str, Any], staged_dispositions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    for disposition in staged_dispositions:
        governance = _candidate_governance(bundle, disposition["candidate_id"])
        if governance.get("source_selection_bindings"):
            binding = governance.get("finalization_binding")
            if binding is None:
                raise ValueError("A2A selected-span graph staging requires a replayed finalization receipt")
            bindings.append(binding)
    return bindings


def _verify_a2a_binding_match(ref: dict[str, Any], binding: dict[str, str], run_id: str) -> None:
    keys = ("package_file_sha256", "receipt_file_sha256", "archive_root_id")
    if ref.get("run_id") != run_id or any(ref.get(k) != binding.get(k) for k in keys):
        raise ValueError("A2A candidate graph staging finalization evidence does not match the verifier proof")


def _replay_and_verify_a2a(
    bundle: dict[str, Any], bindings: list[dict[str, Any]],
    a2a_finalization_proof: VerifiedA2AFinalization | None,
    a2a_finalization_reference: dict[str, Any] | None, a2a_finalization_runtime_root: Path | None,
) -> tuple[dict[str, str], dict[str, str]]:
    if a2a_finalization_reference is None or a2a_finalization_runtime_root is None:
        raise ValueError("A2A candidate graph staging requires replayable finalization evidence and runtime")
    if len({digest(b) for b in bindings}) != 1:
        raise ValueError("A2A selected candidates disagree on their finalization receipt binding")
    verified_ref = validated_ingress_finalization_reference(a2a_finalization_reference)
    replayed_proof = reverify_ingress_finalization_evidence(
        runtime_root=a2a_finalization_runtime_root, reference=verified_ref, expected_binding=bindings[0],
    )
    verified_binding = verified_finalization_binding(replayed_proof)
    if a2a_finalization_proof is not None and verified_finalization_binding(a2a_finalization_proof) != verified_binding:
        raise ValueError("A2A candidate graph staging verifier capability disagrees with archive replay")
    _verify_a2a_binding_match(verified_ref, verified_binding, bundle["run_id"])
    return verified_binding, verified_ref


def validate_a2a_staging_finalization(
    bundle: dict[str, Any], dispositions: list[dict[str, Any]],
    a2a_finalization_proof: VerifiedA2AFinalization | None,
    a2a_finalization_reference: dict[str, Any] | None, a2a_finalization_runtime_root: Path | None,
) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    """Validate A2A candidate graph staging finalization evidence and return bindings."""
    a2a_source_bundle = _is_a2a_source_bundle(bundle)
    staged = [v for v in dispositions if v["disposition"] in {"APPROVED", "CANDIDATE"}]
    bindings = _collect_selected_span_a2a_bindings(bundle, staged) if a2a_source_bundle else []
    verified_binding, verified_reference = None, None
    if bindings:
        verified_binding, verified_reference = _replay_and_verify_a2a(
            bundle, bindings, a2a_finalization_proof, a2a_finalization_reference, a2a_finalization_runtime_root
        )
    return a2a_source_bundle, verified_binding, verified_reference


def process_supersession_plans(
    bundle: dict[str, Any], graph_path: Path, supersession_plans: list[dict[str, Any]],
    payloads: dict[str, dict[str, Any]], operations: list[dict[str, Any]],
) -> None:
    """Validate supersession plans against existing graph state and append graph operations."""
    if not supersession_plans:
        return
    connection = connect_sqlite(graph_path, readonly=True)
    try:
        for plan in supersession_plans:
            row = connection.execute("SELECT * FROM assertion WHERE assertion_id=?", (plan["superseded_assertion_id"],)).fetchone()
            if row is None:
                raise ValueError("semantic correction target assertion does not exist")
            if row["currentness_state"] == "superseded" or row["superseded_at"] is not None or row["superseded_by_assertion_id"] is not None:
                raise ValueError("semantic correction target is already superseded")
            control = _semantic_correction_control(bundle, plan["candidate"]["candidate_id"])
            if row["extraction_run_id"] != control["replaces_run_id"]:
                raise ValueError("semantic correction target does not belong to the bound prior run")
            plan["before_state_hash"] = digest(dict(row))
    finally:
        connection.close()

    for plan in supersession_plans:
        candidate = plan["candidate"]
        operation_id = stable_id("graph-operation", bundle["run_id"], "supersede", plan["superseded_assertion_id"], length=28)
        payload = {
            "candidate": candidate, "disposition": plan["disposition"],
            "supersession": {"replacement_assertion_id": plan["replacement_assertion_id"], "superseded_assertion_id": plan["superseded_assertion_id"]},
        }
        payloads[operation_id] = payload
        operations.append({
            "operation_id": operation_id, "sequence": len(operations) + 1, "operation": "supersede_assertion",
            "target_type": "assertion", "target_id": plan["superseded_assertion_id"],
            "before_state_hash": plan["before_state_hash"], "after_state_hash": digest(payload),
            "evidence_ids": candidate["evidence_ids"], "classification": candidate["classification"],
            "audiences": candidate["audiences"], "candidate_id": candidate["candidate_id"],
        })


def resolve_base_graph_snapshot(
    graph_path: Path, base_graph_snapshot: str | None, a2a_finalization_runtime_root: Path | None,
) -> str:
    """Resolve base graph snapshot identifier from pair manifest, active pair, or file hash."""
    if base_graph_snapshot:
        return base_graph_snapshot
    for cand_path in (
        graph_path.parent / "pair_manifest.json",
        (a2a_finalization_runtime_root / "active_pair.json") if a2a_finalization_runtime_root is not None else None,
    ):
        if cand_path is not None and cand_path.is_file():
            try:
                data = json.loads(cand_path.read_text(encoding="utf-8"))
                if data.get("graph_sha256") == sha256_file(graph_path):
                    return data.get("graph_snapshot")
            except Exception:
                pass
    return stable_id("graph-snapshot", sha256_file(graph_path), length=24)


def emit_assertion_operations(
    assertion_plans: list[dict[str, Any]],
    payloads: dict[str, dict[str, Any]],
    operations: list[dict[str, Any]],
) -> None:
    """Emit graph operations for planned assertions, relationships, and actions."""
    for plan in assertion_plans:
        payload = plan["payload"]
        candidate = payload["candidate"]
        operation_id = plan["operation_id"]
        payloads[operation_id] = payload
        operation = plan["operation"]
        operations.append({
            "operation_id": operation_id, "sequence": len(operations) + 1, "operation": operation,
            "target_type": "relationship" if operation == "add_relationship" else ("action_item" if operation == "add_action_item" else "assertion"),
            "target_id": plan["target_id"], "before_state_hash": ZERO_HASH, "after_state_hash": digest(payload),
            "evidence_ids": candidate["evidence_ids"], "classification": candidate["classification"],
            "audiences": candidate["audiences"], "candidate_id": candidate["candidate_id"],
        })


def resolve_relationship_predicate(
    candidate: dict[str, Any], candidate_id: str, edge_traits_map: dict[str, Any],
) -> str:
    """Resolve relationship predicate from edge traits or text pattern matching."""
    known = {
        "AFFILIATED_WITH", "CANDIDATE_FOR", "CONSTRAINS", "CONTAINS", "CONTEXT_FOR", "CONTRIBUTES_TO",
        "DELIVERS", "DEPENDS_ON", "GOVERNS", "HAS_RISK", "HAS_ROLE_IN", "OWNS_OR_LEADS",
        "RELATED_TO", "REPORTS_TO", "ROUTES_TO", "SUPPORTED_BY", "TRANSITIONS_TO", "USES_PATTERN", "VALIDATES",
    }
    aliases = {
        "MEMBER_OF": "AFFILIATED_WITH", "ASSIGNED_TO": "AFFILIATED_WITH", "PART_OF": "AFFILIATED_WITH", "OWNS": "OWNS_OR_LEADS", "LEADS": "OWNS_OR_LEADS",
        "MANAGES": "OWNS_OR_LEADS", "SPONSORS": "GOVERNS", "INTEGRATES_WITH": "RELATED_TO", "ACCELERATES": "CONTRIBUTES_TO", "DELIVERS_VIA": "DELIVERS", "DELIVERS_VALUE_TO": "DELIVERS",
        "HAS_ROLE": "HAS_ROLE_IN", "USES_SYSTEM": "DEPENDS_ON", "HAS_METRIC": "CONTAINS",
    }
    raw = str((edge_traits_map.get(candidate_id) or {}).get("relationship_predicate") or "").upper()
    if raw in known: return raw
    if raw in aliases: return aliases[raw]
    text = candidate.get("normalized_text", "")
    for raw_p, mapped_p in aliases.items():
        if f" {raw_p} " in text: return mapped_p
    for p in known:
        if f" {p} " in text: return p
    return "RELATED_TO"


def _is_placeholder_entity_or_mention(candidate: dict[str, Any], selected_entities: list[str]) -> bool:
    placeholders = {"not specified", "unattributed participant", "system", "metadata"}
    if selected_entities and all(
        str(eid).strip().lower() in placeholders or "not_specified" in str(eid).strip().lower() or "not-specified" in str(eid).strip().lower()
        for eid in selected_entities
    ):
        return True
    mentions = candidate.get("entity_mentions") or [{"reported_text": "not specified"}]
    return all(str(m.get("reported_text") or "").strip().lower() in placeholders for m in mentions)


def should_suppress_document_candidate(
    candidate: dict[str, Any], selected_entities: list[str], doc_connected_entities: set[str], correction: dict[str, Any] | None, a2a_source: bool,
) -> bool:
    """Return True if a document bundle candidate is a redundant stub or unattributed OCR row."""
    if not doc_connected_entities or correction or a2a_source:
        return False
    ctype = candidate.get("candidate_type")
    if ctype == "entity_mention" and selected_entities and selected_entities[0] in doc_connected_entities:
        norm_low = str(candidate.get("normalized_text") or "").lower()
        if "is an identified" in norm_low or not candidate.get("description"):
            return True
    return ctype in {"assertion", "source_chunk", "entity_mention"} and _is_placeholder_entity_or_mention(candidate, selected_entities)


def _is_document_source_bundle(bundle: dict[str, Any]) -> bool:
    src_ext = bundle.get("extensions", {}).get("com.partner_advisory.nee.source", {}) or {}
    ctx_ext = bundle.get("extensions", {}).get("com.partner_advisory.nee.context", {}) or {}
    source_type = str(src_ext.get("source_type") or ctx_ext.get("source_type") or (bundle.get("meeting") or {}).get("source_type") or "").strip().lower()
    src_name, src_loc = str(src_ext.get("name") or "").strip().lower(), str(src_ext.get("source_location") or "").strip().lower()
    if source_type in {"meeting_transcript", "transcript", "audio_transcript", "live_transcript"} or src_loc.endswith(("_transcript.md", "_transcript.txt", ".vtt", ".srt", ".wav", ".mp3", ".m4a")):
        return False
    meeting_id = str((bundle.get("meeting") or {}).get("meeting_id") or "").strip().lower()
    attendees = [str(a).strip().lower() for a in (src_ext.get("attendees") or [])]
    is_planning_doc = "planning" in src_name and not any(w in src_name for w in ("meeting", "sync", "call"))
    return bool(source_type == "document" or meeting_id.startswith("document-") or src_name.startswith("document ") or "statement of work" in src_name or is_planning_doc or "planning" in src_loc or attendees == ["not specified"] or src_loc.endswith((".pdf", ".docx", ".pptx", ".xlsx", "source_document.txt")))


def collect_document_connected_entities(bundle: dict[str, Any], dispositions: list[dict[str, Any]], candidate_by_id: dict[str, dict[str, Any]], resolution_by_candidate: dict[str, list[dict[str, Any]]], decision_by_candidate: dict[str, dict[str, Any]]) -> set[str]:
    """Return resolved entity IDs participating in architecture_relationship edges or substantive dialogue assertions."""
    connected: set[str] = set()
    tmp_internal: dict[str, dict[str, Any]] = {}
    is_doc = _is_document_source_bundle(bundle)
    for disp in dispositions:
        if disp.get("disposition") not in {"APPROVED", "CANDIDATE"}:
            continue
        cand = candidate_by_id.get(disp["candidate_id"])
        if not cand:
            continue
        ctype = cand.get("candidate_type")
        if is_doc and ctype != "architecture_relationship":
            continue
        if not is_doc and ctype not in {"architecture_relationship", "assertion", "decision", "risk", "action"}:
            continue
        cid, res_list = cand["candidate_id"], resolution_by_candidate.get(cand["candidate_id"], [])
        sel = [v["selected_entity_id"] for v in res_list if v.get("selected_entity_id")]
        sel_rev = (decision_by_candidate.get(cid) or {}).get("extensions", {}).get("com.partner_advisory.nee.selected-entity-id")
        if sel_rev:
            sel = [sel_rev] + [v for v in sel if v != sel_rev]
        connected.update(resolve_unbound_internal_entities(res_list, tmp_internal, sel, cand))
    return connected
