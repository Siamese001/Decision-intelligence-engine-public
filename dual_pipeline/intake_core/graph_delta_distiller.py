"""Graph-Aware Delta Distiller & HITL Overlap Assessor.

Compares extracted high-signal document candidates against existing canonical GraphDB
knowledge before mutation, partitioning candidates into four executive buckets:
1. Net-New Client Knowledge (workstreams, charters, and owners not in existing graph)
2. Corroborated Facts (matching existing workstreams, owners, or relationships)
3. Perspective Deltas & Contradictions (client-internal vs. PartnerAdvisory view shifts or owner changes)
4. HITL Ambiguity & Unclear Items (margin <= 20%, missing charters, placeholder identities, or cross-type collisions)
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from dual_pipeline.core import connect_sqlite, normalize_name, stable_id
from dual_pipeline.identity import NEW_NODE_ASSESSMENT_EXTENSION_KEY
from dual_pipeline.parameters import (
    get_hitl_ambiguity_margin_threshold,
    needs_operator_decision,
)

from .proposals import _candidate_resolution_map

PORTFOLIO_ALLOCATION_PREDICATES = frozenset({
    "MEMBER_OF", "AFFILIATED_WITH", "LEADS", "OWNS_OR_LEADS",
    "USES_SYSTEM", "DEPENDS_ON", "HAS_METRIC", "CONTAINS", "PART_OF", "ASSIGNED_TO",
})


@dataclass(frozen=True)
class DocumentGraphDeltaReport:
    """Structured executive delta comparing an incoming document bundle against GraphDB state."""

    run_id: str
    source_id: str
    perspective_origin: str
    net_new_entities: list[dict[str, Any]] = field(default_factory=list)
    net_new_workstreams: list[str] = field(default_factory=list)
    corroborated_facts: list[dict[str, Any]] = field(default_factory=list)
    corroborated_workstreams: list[str] = field(default_factory=list)
    perspective_deltas: list[dict[str, Any]] = field(default_factory=list)
    hitl_ambiguity_items: list[dict[str, Any]] = field(default_factory=list)
    hitl_review_items: list[dict[str, Any]] = field(default_factory=list)
    same_document_allocations_suppressed: int = 0
    placeholder_reconciliations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_portfolio_allocation_relationship(candidate: dict[str, Any]) -> bool:
    """Return True if candidate is a portfolio/roster relationship that can legitimately repeat across rows."""
    if candidate.get("candidate_type") != "architecture_relationship":
        return False
    norm_text = str(candidate.get("normalized_text") or "")
    return any(f" {pred} " in norm_text for pred in PORTFOLIO_ALLOCATION_PREDICATES)


def _load_existing_graph_state(
    graph_path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Load active nodes, normalized name index, and current assertions from GraphDB."""
    nodes_by_id: dict[str, dict[str, Any]] = {}
    nodes_by_norm_name: dict[str, list[dict[str, Any]]] = {}
    assertions: list[dict[str, Any]] = []
    if not graph_path or not graph_path.exists():
        return nodes_by_id, nodes_by_norm_name, assertions
    conn = connect_sqlite(graph_path, readonly=True)
    try:
        for row in conn.execute(
            "SELECT node_id, canonical_name, node_type, coalesce(description,'') AS description FROM node WHERE active=1"
        ):
            rec = dict(row)
            nodes_by_id[rec["node_id"]] = rec
            norm = normalize_name(rec["canonical_name"])
            nodes_by_norm_name.setdefault(norm, []).append(rec)
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(assertion)").fetchall()
        }
        text_expr = (
            "coalesce(object_literal, '')"
            if "object_literal" in cols and "claim_text" not in cols
            else "coalesce(claim_text, normalized_text, '')"
            if "claim_text" in cols and "normalized_text" in cols
            else "coalesce(claim_text, '')"
            if "claim_text" in cols
            else "''"
        )
        src_col = (
            "primary_source_id"
            if "primary_source_id" in cols and "source_id" not in cols
            else "source_id"
            if "source_id" in cols
            else "NULL"
        )
        for row in conn.execute(
            f"""SELECT assertion_id, subject_node_id, predicate, object_node_id,
                       {text_expr} AS claim_text, {src_col} AS source_id
                FROM assertion WHERE currentness_state='current'"""
        ):
            assertions.append(dict(row))
    except Exception:
        pass
    finally:
        conn.close()
    return nodes_by_id, nodes_by_norm_name, assertions


def _infer_perspective_origin(bundle: dict[str, Any]) -> str:
    src_ext = bundle.get("extensions", {}).get("com.partner_advisory.nee.source", {}) or {}
    loc = str(src_ext.get("source_location") or "").lower()
    name = str(src_ext.get("name") or "").lower()
    if "apex_globalanalytics.com" in loc or "nea planning" in name or "assignments" in name:
        return "CLIENT_INTERNAL_NEA"
    return "partner_advisory_INTERNAL"


def _detect_placeholder_reconciliations(
    reported_name: str,
    nodes_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect existing unresolved_* placeholder nodes matching an incoming canonical person name."""
    raw_tokens = [t.lower() for t in re.split(r"[^a-zA-Z0-9]+", reported_name or "") if len(t) >= 3]
    norm_tokens = [t.lower() for t in normalize_name(reported_name).split() if len(t) >= 3]
    token_sets = [ts for ts in (raw_tokens, norm_tokens) if len(ts) >= 2]
    if not token_sets:
        return []
    matches: list[dict[str, Any]] = []
    for nid, nrec in nodes_by_id.items():
        low_id = nid.lower()
        low_name = nrec["canonical_name"].lower()
        if ("unresolved" in low_id or "unresolved" in low_name) and any(
            all(tok in low_id or tok in low_name for tok in ts) for ts in token_sets
        ):
            matches.append({
                "placeholder_node_id": nid,
                "placeholder_name": nrec["canonical_name"],
                "resolved_canonical_name": reported_name,
                "recommended_action": "MERGE_PLACEHOLDER_INTO_CANONICAL",
            })
    return matches


def _build_hitl_item(
    *,
    run_id: str,
    candidate_id: str,
    category: str,
    question: str,
    top_option: str,
    runner_up_option: str,
    top_confidence: float,
    runner_up_confidence: float,
    evidence_ids: list[str],
) -> dict[str, Any]:
    margin = round(abs(top_confidence - runner_up_confidence), 4)
    requires_operator = needs_operator_decision(top_confidence, runner_up_confidence)
    return {
        "review_item_id": stable_id("delta-hitl", run_id, candidate_id, category, length=26),
        "candidate_id": candidate_id,
        "category": category,
        "question": question,
        "top_confidence": round(top_confidence, 4),
        "runner_up_confidence": round(runner_up_confidence, 4),
        "confidence_margin": margin,
        "requires_operator_decision": requires_operator,
        "options": [
            {"label": f"(Recommended) {top_option}", "confidence": round(top_confidence, 4)},
            {"label": runner_up_option, "confidence": round(runner_up_confidence, 4)},
        ],
        "evidence_ids": list(evidence_ids),
    }


def _record_corroborated_entity(
    res: dict[str, Any],
    candidate: dict[str, Any],
    declared_type: str,
    reported: str,
    nodes_by_id: dict[str, dict[str, Any]],
    corroborated_facts: list[dict[str, Any]],
    corroborated_workstreams: list[str],
) -> None:
    sel_id = res.get("selected_entity_id")
    node_rec = nodes_by_id.get(sel_id or "", {})
    ntype = node_rec.get("node_type") or declared_type
    corroborated_facts.append({
        "candidate_id": candidate["candidate_id"],
        "entity_id": sel_id,
        "canonical_name": reported,
        "node_type": ntype,
        "match_kind": "corroborated_entity",
    })
    if ntype == "workstream" and reported not in corroborated_workstreams:
        corroborated_workstreams.append(reported)


def _record_net_new_entity(
    assessment: dict[str, Any],
    candidate: dict[str, Any],
    declared_type: str,
    reported: str,
    stable_ident: str,
    net_new_entities: list[dict[str, Any]],
    net_new_workstreams: list[str],
) -> None:
    prop = assessment.get("proposal") or {}
    ntype = prop.get("node_type") or declared_type
    net_new_entities.append({
        "candidate_id": candidate["candidate_id"],
        "entity_id": prop.get("entity_id") or stable_ident,
        "canonical_name": reported,
        "node_type": ntype,
        "source_stable_identifier": stable_ident,
    })
    if ntype == "workstream" and reported not in net_new_workstreams:
        net_new_workstreams.append(reported)


def _record_hitl_identity_ambiguity(
    res: dict[str, Any],
    candidate: dict[str, Any],
    bundle: dict[str, Any],
    declared_type: str,
    reported: str,
    hitl_items: list[dict[str, Any]],
) -> None:
    alts = res.get("alternatives") or []
    top_score = float(alts[0]["match_score"]) if alts else 0.55
    second_score = float(alts[1]["match_score"]) if len(alts) > 1 else max(0.40, top_score - 0.12)
    alt_label = alts[0]["canonical_name"] if alts else "candidate"
    dtype = declared_type or "entity"
    hitl_items.append(
        _build_hitl_item(
            run_id=bundle["run_id"],
            candidate_id=candidate["candidate_id"],
            category="IDENTITY_AMBIGUITY",
            question=f"How should {reported} ({dtype}) be linked in the knowledge graph?",
            top_option=f"Create distinct {dtype} node for {reported}",
            runner_up_option=f"Link {reported} to existing graph node {alt_label}",
            top_confidence=top_score,
            runner_up_confidence=second_score,
            evidence_ids=candidate.get("evidence_ids", []),
        )
    )


def _evaluate_entity_resolution_delta(
    res: dict[str, Any],
    candidate: dict[str, Any],
    bundle: dict[str, Any],
    nodes_by_id: dict[str, dict[str, Any]],
    seen_entities: set[str],
    net_new_entities: list[dict[str, Any]],
    net_new_workstreams: list[str],
    corroborated_facts: list[dict[str, Any]],
    corroborated_workstreams: list[str],
    placeholder_reconciliations: list[dict[str, Any]],
    hitl_items: list[dict[str, Any]],
) -> None:
    reported = str(res.get("reported_name") or "").strip()
    outcome = res.get("outcome")
    assessment = res.get("extensions", {}).get(NEW_NODE_ASSESSMENT_EXTENSION_KEY, {}) or {}
    declared_type = str(assessment.get("declared_node_type") or "")
    stable_ident = str((res.get("stable_identifier") or {}).get("value") or reported)
    if stable_ident in seen_entities:
        return
    seen_entities.add(stable_ident)

    for ph in _detect_placeholder_reconciliations(reported, nodes_by_id):
        if ph not in placeholder_reconciliations:
            placeholder_reconciliations.append(ph)

    if outcome == "AUTO_LINK_EXACT":
        _record_corroborated_entity(
            res, candidate, declared_type, reported, nodes_by_id,
            corroborated_facts, corroborated_workstreams,
        )
    elif outcome == "CREATE_NEW_ENTITY_PROPOSAL":
        _record_net_new_entity(
            assessment, candidate, declared_type, reported, stable_ident,
            net_new_entities, net_new_workstreams,
        )
    elif outcome == "HITL_CONFIRM_ENTITY":
        _record_hitl_identity_ambiguity(
            res, candidate, bundle, declared_type, reported, hitl_items,
        )


def _inspect_prior_lead_assertions(
    candidate: dict[str, Any],
    bundle: dict[str, Any],
    subj_name: str,
    obj_name: str,
    obj_node: dict[str, Any],
    nodes_by_id: dict[str, dict[str, Any]],
    assertions: list[dict[str, Any]],
    corroborated_facts: list[dict[str, Any]],
    perspective_deltas: list[dict[str, Any]],
    hitl_items: list[dict[str, Any]],
) -> None:
    prior_leads = [
        a for a in assertions
        if a["object_node_id"] == obj_node["node_id"]
        and a["predicate"] in {"OWNS_OR_LEADS", "LEADS"}
        and a.get("source_id") != bundle.get("source_id")
    ]
    for prior in prior_leads:
        prior_subj = nodes_by_id.get(prior["subject_node_id"], {}).get("canonical_name", "")
        if not prior_subj:
            continue
        if normalize_name(prior_subj) != normalize_name(subj_name):
            perspective_deltas.append({
                "candidate_id": candidate["candidate_id"],
                "workstream_or_target": obj_name,
                "prior_partner_advisory_owner": prior_subj,
                "new_client_owner": subj_name,
                "prior_assertion_id": prior["assertion_id"],
                "delta_type": "OWNER_OR_LEAD_SHIFT",
            })
            hitl_items.append(
                _build_hitl_item(
                    run_id=bundle["run_id"],
                    candidate_id=candidate["candidate_id"],
                    category="PERSPECTIVE_OWNER_SHIFT",
                    question=f"NEA planning lists {subj_name} leading {obj_name}, whereas prior graph records list {prior_subj}. How should this be reconciled?",
                    top_option=f"Update {obj_name} lead to {subj_name} per authoritative NEA Q4 dashboard",
                    runner_up_option=f"Retain {prior_subj} and record {subj_name} as co-lead",
                    top_confidence=0.56,
                    runner_up_confidence=0.44,
                    evidence_ids=candidate.get("evidence_ids", []),
                )
            )
        else:
            corroborated_facts.append({
                "candidate_id": candidate["candidate_id"],
                "entity_id": obj_node["node_id"],
                "canonical_name": f"{subj_name} leads {obj_name}",
                "node_type": "relationship",
                "match_kind": "corroborated_leadership",
            })


def _evaluate_relationship_and_risk_deltas(
    candidate: dict[str, Any],
    bundle: dict[str, Any],
    cand_comp: dict[str, Any],
    nodes_by_id: dict[str, dict[str, Any]],
    nodes_by_norm_name: dict[str, list[dict[str, Any]]],
    assertions: list[dict[str, Any]],
    corroborated_facts: list[dict[str, Any]],
    perspective_deltas: list[dict[str, Any]],
    hitl_items: list[dict[str, Any]],
) -> int:
    """Inspect relationship or risk candidates for cross-source owner shifts or missing charters."""
    norm_text = str(candidate.get("normalized_text") or "")
    suppressed = 1 if (cand_comp.get("partial_overlaps") and is_portfolio_allocation_relationship(candidate)) else 0

    if "no associated charter" in norm_text.lower() or "missing charter" in norm_text.lower():
        hitl_items.append(
            _build_hitl_item(
                run_id=bundle["run_id"],
                candidate_id=candidate["candidate_id"],
                category="MISSING_CHARTER_GOVERNANCE",
                question=f"How should the unchartered workstream in '{norm_text[:90]}' be governed?",
                top_option="Stage workstream with an active Missing Charter governance risk flag",
                runner_up_option="Hold workstream promotion until a formal charter ID is assigned",
                top_confidence=0.58,
                runner_up_confidence=0.42,
                evidence_ids=candidate.get("evidence_ids", []),
            )
        )

    mentions = candidate.get("entity_mentions") or []
    if candidate.get("candidate_type") != "architecture_relationship" or len(mentions) < 2:
        return suppressed
    if " LEADS " not in norm_text and " OWNS_OR_LEADS " not in norm_text:
        return suppressed
    subj_name = str(mentions[0].get("reported_text") or "").strip()
    obj_name = str(mentions[1].get("reported_text") or "").strip()
    for obj_node in nodes_by_norm_name.get(normalize_name(obj_name), []):
        _inspect_prior_lead_assertions(
            candidate, bundle, subj_name, obj_name, obj_node,
            nodes_by_id, assertions, corroborated_facts, perspective_deltas, hitl_items,
        )
    return suppressed


def distill_document_graph_delta(
    bundle: dict[str, Any],
    resolutions: list[dict[str, Any]],
    comparison: dict[str, Any],
    graph_path: Path,
) -> DocumentGraphDeltaReport:
    """Compare extracted document candidates against GraphDB state to distill net-new vs. overlaps and HITL items."""
    nodes_by_id, nodes_by_norm_name, assertions = _load_existing_graph_state(graph_path)
    by_candidate = _candidate_resolution_map(bundle, resolutions)
    perspective = _infer_perspective_origin(bundle)

    net_new_entities: list[dict[str, Any]] = []
    net_new_workstreams: list[str] = []
    corroborated_facts: list[dict[str, Any]] = []
    corroborated_workstreams: list[str] = []
    perspective_deltas: list[dict[str, Any]] = []
    hitl_items: list[dict[str, Any]] = []
    placeholder_reconciliations: list[dict[str, Any]] = []
    seen_entities: set[str] = set()
    suppressed_count = 0

    for candidate in bundle.get("candidates", []):
        cid = candidate["candidate_id"]
        cand_res = by_candidate.get(cid, [])
        cand_comp = (comparison.get("comparisons") or {}).get(cid, {})
        for res in cand_res:
            _evaluate_entity_resolution_delta(
                res, candidate, bundle, nodes_by_id, seen_entities,
                net_new_entities, net_new_workstreams,
                corroborated_facts, corroborated_workstreams,
                placeholder_reconciliations, hitl_items,
            )
        suppressed_count += _evaluate_relationship_and_risk_deltas(
            candidate, bundle, cand_comp, nodes_by_id, nodes_by_norm_name,
            assertions, corroborated_facts, perspective_deltas, hitl_items,
        )

    for ph in placeholder_reconciliations:
        hitl_items.append(
            _build_hitl_item(
                run_id=bundle["run_id"],
                candidate_id=bundle["candidates"][0]["candidate_id"] if bundle.get("candidates") else "bundle-root",
                category="PLACEHOLDER_IDENTITY_RECONCILIATION",
                question=f"Reconcile placeholder node '{ph['placeholder_node_id']}' into canonical stakeholder '{ph['resolved_canonical_name']}'?",
                top_option=f"Merge placeholder {ph['placeholder_node_id']} into {ph['resolved_canonical_name']}",
                runner_up_option=f"Keep {ph['placeholder_node_id']} separate pending manual roster audit",
                top_confidence=0.59,
                runner_up_confidence=0.41,
                evidence_ids=[],
            )
        )

    thresh = get_hitl_ambiguity_margin_threshold()
    filtered_hitl = [
        item for item in hitl_items
        if item["requires_operator_decision"] or item["confidence_margin"] <= thresh
    ]

    return DocumentGraphDeltaReport(
        run_id=bundle["run_id"],
        source_id=bundle["source_id"],
        perspective_origin=perspective,
        net_new_entities=net_new_entities,
        net_new_workstreams=net_new_workstreams,
        corroborated_facts=corroborated_facts,
        corroborated_workstreams=corroborated_workstreams,
        perspective_deltas=perspective_deltas,
        hitl_ambiguity_items=filtered_hitl,
        hitl_review_items=filtered_hitl,
        same_document_allocations_suppressed=suppressed_count,
        placeholder_reconciliations=placeholder_reconciliations,
    )
