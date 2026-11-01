"""Governed Project Beacon & Project LENS cross-workstream isolation, node promotion, and structural edge remediation."""
from __future__ import annotations

from typing import Any, Dict, Tuple

from dual_pipeline.core import stable_id

LENS_CONTAMINATED_NODE_IDS: Tuple[str, ...] = (
    "entity-69a6725884f1f89598fd38e83c20",  # Alex Sanchez
    "entity-3e530750606b21a5ba8527216ba8",  # Jennifer Pan
    "entity-fd6b0bb4fc500912789477c8d1e7",  # Jake Stark
    "entity-070f32e7328290987c118bcbe757",  # Morgan Vance Krutilla
)

SHERPA_CANONICAL_NODES: Tuple[Tuple[str, str, str, str], ...] = (
    ("entity-06ea5229f6c62bd4a1346f262928", "stakeholder", "Egle Tamasevice", "Apex Global Enterprises Client Tech Lead for Project Beacon"),
    ("entity-83d85ee4e07e75e23f5dd5b5d9fb", "stakeholder", "Brandon Que", "Apex Global Enterprises Solution Architect Lead for Project Beacon"),
    ("entity-6a26db0c264ca5d90c540a595c19", "stakeholder", "Speaker_Beta Tharakan", "PartnerAdvisory Senior Consultant leading Project Beacon AWS OpenSearch migration and data pipelines"),
    ("entity-79a958f8aa3bd05f10e7fb6bf57f", "stakeholder", "Jeffrey Glade", "Apex Global Enterprises Product Owner for Project Beacon"),
    ("person_andy_k", "stakeholder", "Andy Kilinskis", "PartnerAdvisory Solution Architect and Delivery Lead for Project Beacon"),
    ("person_brien", "stakeholder", "Speaker_Alpha Muschett", "Apex Global Enterprises Principal Architect for Project Beacon"),
    ("stakeholder_marcus_candidate", "stakeholder", "Marcus Balk", "Apex Global Enterprises Executive Sponsor for Project Beacon"),
    ("entity-69ef9733972222d01c2fcb3af0ce", "role_group", "Tech Lead", "Technical Lead role"),
    ("entity-65809d18c2d42b814ef43eccc548", "role_group", "Solution Architect Lead", "Solution Architect Lead role"),
    ("deliverable_sherpa_opensearch_migration", "deliverable", "Sherpa AWS OpenSearch Migration", "Migration of Project Beacon search infrastructure from Elasticsearch to AWS OpenSearch led by Speaker_Beta Tharakan"),
    ("pattern_agent_to_agent_protocol", "pattern", "Agent-to-Agent Protocol (A2A)", "Agent-to-Agent interoperability protocol shaped by Egle Tamasevice and Andy Kilinskis for Project Beacon"),
    ("org_nee", "org", "Apex Global Enterprises", "Apex Global Enterprises client organization"),
    ("org_partner_advisory", "org", "PartnerAdvisory", "PartnerAdvisory consulting partner organization"),
    ("workstream_project_beacon", "workstream", "Project Beacon", "Apex Global Enterprises Project Beacon agentic AI workstream"),
    ("workstream_lens", "workstream", "Project LENS", "Apex Global Enterprises Project LENS workstream"),
)

SHERPA_ALIASES: Tuple[Tuple[str, str, str, str], ...] = (
    ("alias-sherpa-egle", "egle", "Egle", "entity-06ea5229f6c62bd4a1346f262928"),
    ("alias-sherpa-ninan", "ninan", "Speaker_Beta", "entity-6a26db0c264ca5d90c540a595c19"),
    ("alias-sherpa-jeff", "jeff", "Jeff", "entity-79a958f8aa3bd05f10e7fb6bf57f"),
    ("alias-sherpa-jeffrey", "jeffrey", "Jeffrey", "entity-79a958f8aa3bd05f10e7fb6bf57f"),
    ("alias-sherpa-brien", "brien", "Speaker_Alpha", "person_brien"),
    ("alias-sherpa-marcus", "marcus", "Marcus", "stakeholder_marcus_candidate"),
)

SHERPA_STRUCTURAL_EDGES: Tuple[Tuple[str, str, str, str, str], ...] = (
    (
        "entity-06ea5229f6c62bd4a1346f262928",
        "HAS_ROLE_IN",
        "entity-69ef9733972222d01c2fcb3af0ce",
        "source-c8ba9093a5a3687e0d016e35",
        "Egle Tamasevice (person) HAS_ROLE_IN Tech Lead (role_group): Apex Global Enterprises hired Egle Tamasevice as the client-side Project Beacon Tech Lead.",
    ),
    (
        "entity-06ea5229f6c62bd4a1346f262928",
        "AFFILIATED_WITH",
        "org_nee",
        "source-c8ba9093a5a3687e0d016e35",
        "Egle Tamasevice (person) AFFILIATED_WITH Apex Global Enterprises (organization) as Client Tech Lead on Project Beacon.",
    ),
    (
        "entity-06ea5229f6c62bd4a1346f262928",
        "OWNS_OR_LEADS",
        "workstream_project_beacon",
        "source-c8ba9093a5a3687e0d016e35",
        "Egle Tamasevice (Apex Global Enterprises Client Tech Lead) OWNS_OR_LEADS technical architecture governance on Project Beacon.",
    ),
    (
        "entity-06ea5229f6c62bd4a1346f262928",
        "USES_PATTERN",
        "pattern_agent_to_agent_protocol",
        "source-244602bbe3c399331a745e51",
        "Egle Tamasevice (Apex Global Enterprises Tech Lead) shapes and reviews the Agent-to-Agent Protocol (A2A) for Project Beacon.",
    ),
    (
        "entity-83d85ee4e07e75e23f5dd5b5d9fb",
        "HAS_ROLE_IN",
        "entity-65809d18c2d42b814ef43eccc548",
        "source-c8ba9093a5a3687e0d016e35",
        "Brandon Que (person) HAS_ROLE_IN Solution Architect Lead (role_group) on Project Beacon.",
    ),
    (
        "entity-83d85ee4e07e75e23f5dd5b5d9fb",
        "AFFILIATED_WITH",
        "org_nee",
        "source-c8ba9093a5a3687e0d016e35",
        "Brandon Que (person) AFFILIATED_WITH Apex Global Enterprises (organization) as Solution Architect Lead.",
    ),
    (
        "entity-83d85ee4e07e75e23f5dd5b5d9fb",
        "CONTRIBUTES_TO",
        "workstream_project_beacon",
        "source-c8ba9093a5a3687e0d016e35",
        "Brandon Que (Apex Global Enterprises Solution Architect Lead) CONTRIBUTES_TO Project Beacon architecture.",
    ),
    (
        "entity-79a958f8aa3bd05f10e7fb6bf57f",
        "OWNS_OR_LEADS",
        "workstream_project_beacon",
        "source-ea51c8983147880052dc880e",
        "Jeffrey Glade (Apex Global Enterprises Product Owner) OWNS_OR_LEADS Project Beacon product backlog and stakeholder prioritization.",
    ),
    (
        "entity-79a958f8aa3bd05f10e7fb6bf57f",
        "AFFILIATED_WITH",
        "org_nee",
        "source-ea51c8983147880052dc880e",
        "Jeffrey Glade (person) AFFILIATED_WITH Apex Global Enterprises (organization) as Project Beacon Product Owner.",
    ),
    (
        "entity-6a26db0c264ca5d90c540a595c19",
        "AFFILIATED_WITH",
        "org_partner_advisory",
        "source-ea51c8983147880052dc880e",
        "Speaker_Beta Tharakan (person) AFFILIATED_WITH PartnerAdvisory (organization) as Senior Engineer on Project Beacon.",
    ),
    (
        "entity-6a26db0c264ca5d90c540a595c19",
        "CONTRIBUTES_TO",
        "workstream_project_beacon",
        "source-ea51c8983147880052dc880e",
        "Speaker_Beta Tharakan (PartnerAdvisory) CONTRIBUTES_TO Project Beacon data pipelines, ingestion, and OpenSearch infrastructure.",
    ),
    (
        "entity-6a26db0c264ca5d90c540a595c19",
        "OWNS_OR_LEADS",
        "deliverable_sherpa_opensearch_migration",
        "source-2c2ac695afc1a9fc97da5a56",
        "Speaker_Beta Tharakan (PartnerAdvisory) OWNS_OR_LEADS the Project Beacon Elasticsearch to AWS OpenSearch migration and pipeline cutover.",
    ),
    (
        "person_andy_k",
        "AFFILIATED_WITH",
        "org_partner_advisory",
        "source-c8ba9093a5a3687e0d016e35",
        "Andy Kilinskis (person) AFFILIATED_WITH PartnerAdvisory (organization) as Solution Architect and Delivery Lead on Project Beacon.",
    ),
    (
        "person_andy_k",
        "OWNS_OR_LEADS",
        "workstream_project_beacon",
        "source-c8ba9093a5a3687e0d016e35",
        "Andy Kilinskis (PartnerAdvisory Delivery Lead) OWNS_OR_LEADS PartnerAdvisory engineering delivery on Project Beacon.",
    ),
)


def _has_table(conn: Any, name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _ensure_edge_type_domain_ranges(conn: Any, now_iso: str) -> None:
    if _has_table(conn, "edge_type_registry"):
        for etype in ("HAS_ROLE_IN", "OWNS_OR_LEADS", "USES_PATTERN", "AFFILIATED_WITH", "CONTRIBUTES_TO"):
            conn.execute(
                "INSERT OR IGNORE INTO edge_type_registry (edge_type_id, canonical_predicate, definition, "
                "relationship_family, direction, symmetric_flag, transitive_flag, cardinality, "
                "temporal_requirement, evidence_required, qualifier_rules_json, registry_state, allow_self_loop, created_at) "
                "VALUES (?, ?, 'Governed structural predicate', ?, 'directed', 0, 0, 'many_to_many', 'recommended', 1, '{}', 'active', 0, ?)",
                (etype, etype, etype, now_iso),
            )
    if _has_table(conn, "edge_type_domain_range"):
        pairs = [
            ("HAS_ROLE_IN", "stakeholder", "role_group"),
            ("OWNS_OR_LEADS", "stakeholder", "deliverable"),
            ("OWNS_OR_LEADS", "stakeholder", "workstream"),
            ("USES_PATTERN", "stakeholder", "pattern"),
            ("AFFILIATED_WITH", "stakeholder", "org"),
            ("CONTRIBUTES_TO", "stakeholder", "workstream"),
        ]
        if not _has_table(conn, "node_type_registry") or conn.execute("SELECT 1 FROM node_type_registry WHERE node_type_id='person'").fetchone():
            pairs.extend([
                ("HAS_ROLE_IN", "person", "role_group"),
                ("OWNS_OR_LEADS", "person", "deliverable"),
                ("OWNS_OR_LEADS", "person", "workstream"),
                ("USES_PATTERN", "person", "pattern"),
                ("AFFILIATED_WITH", "person", "org"),
                ("CONTRIBUTES_TO", "person", "workstream"),
            ])
        for etype, stype, ttype in pairs:
            conn.execute(
                "INSERT OR IGNORE INTO edge_type_domain_range (edge_type_id, source_node_type, target_node_type, active) "
                "VALUES (?, ?, ?, 1)",
                (etype, stype, ttype),
            )


def _retire_contaminated_lens_and_filler_assertions(conn: Any, now_iso: str) -> int:
    ph = ",".join("?" for _ in LENS_CONTAMINATED_NODE_IDS)
    cur1 = conn.execute(
        f"UPDATE assertion SET canonical_status = 'rejected', currentness_state = 'historical', "
        f"retrieval_eligible = 0, updated_at = ? "
        f"WHERE primary_source_id IN ('source-c8ba9093a5a3687e0d016e35', 'source-12af631893ae3f76b62973ff') "
        f"AND (subject_node_id IN ({ph}) OR object_node_id IN ({ph}) "
        f"OR scope_text LIKE '%Alex Sanchez%' OR scope_text LIKE '%Jennifer Pan%' "
        f"OR scope_text LIKE '%Jake Stark%' OR scope_text LIKE '%Morgan Vance Krutilla%' "
        f"OR scope_text LIKE '%Sounds good%' OR scope_text LIKE '%Wait for Dan%' OR scope_text LIKE '%see you%')",
        [now_iso, *LENS_CONTAMINATED_NODE_IDS, *LENS_CONTAMINATED_NODE_IDS],
    )
    conn.execute(
        "UPDATE search_document SET canonical_status = (SELECT a.canonical_status FROM assertion a WHERE a.assertion_id = search_document.assertion_id), "
        "currentness_state = (SELECT a.currentness_state FROM assertion a WHERE a.assertion_id = search_document.assertion_id), "
        "retrieval_eligible = 0, updated_at = ? "
        "WHERE assertion_id IN (SELECT assertion_id FROM assertion WHERE canonical_status = 'rejected')",
        (now_iso,),
    )
    return cur1.rowcount


def _promote_sherpa_nodes_and_aliases(conn: Any, now_iso: str) -> int:
    default_src_row = conn.execute("SELECT source_id FROM source ORDER BY created_at LIMIT 1").fetchone()
    default_src = default_src_row[0] if default_src_row else "source-c8ba9093a5a3687e0d016e35"
    promoted = 0
    for nid, ntype, cname, desc in SHERPA_CANONICAL_NODES:
        conn.execute(
            "INSERT OR IGNORE INTO node (node_id, node_type, canonical_name, display_name, description, status, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'canonical', 1, ?, ?)",
            (nid, ntype, cname, cname, desc, now_iso, now_iso),
        )
        cur = conn.execute(
            "UPDATE node SET status = 'canonical', active = 1, description = COALESCE(NULLIF(description, ''), ?), updated_at = ? WHERE node_id = ?",
            (desc, now_iso, nid),
        )
        promoted += cur.rowcount
        conn.execute(
            "INSERT INTO node_state (node_id, lifecycle_state, validation_state, currentness_state, "
            "approval_state, rag_state, resolution_state, retrieval_eligibility, publication_state, review_state, "
            "intentional_orphan, updated_at) VALUES (?, 'active', 'accepted_legacy', 'current', 'approved', "
            "'green', 'resolved', 'eligible', 'internal', 'reviewed', 0, ?) "
            "ON CONFLICT(node_id) DO UPDATE SET lifecycle_state='active', validation_state='accepted_legacy', "
            "currentness_state='current', approval_state='approved', resolution_state='resolved', "
            "retrieval_eligibility='eligible', review_state='reviewed', updated_at=excluded.updated_at",
            (nid, now_iso),
        )
        existing_sdoc = conn.execute(
            "SELECT search_document_id FROM search_document WHERE object_type='node' AND object_id=?", (nid,)
        ).fetchone()
        if existing_sdoc:
            conn.execute(
                "UPDATE search_document SET canonical_status='canonical', currentness_state='current', "
                "retrieval_eligible=1, updated_at=? WHERE search_document_id=?",
                (now_iso, existing_sdoc[0]),
            )
        else:
            sdoc_id = stable_id("search-node", nid, length=28)
            conn.execute(
                "INSERT INTO search_document (search_document_id, object_type, object_id, title, body, "
                "canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id, updated_at) "
                "VALUES (?, 'node', ?, ?, ?, 'canonical', 'current', 1, NULL, NULL, ?)",
                (sdoc_id, nid, cname, f"{cname}: {desc}", now_iso),
            )
    if conn.execute("SELECT 1 FROM node WHERE node_id = 'entity-2a2648a1b03f0a600f85378dc03c'").fetchone():
        conn.execute(
            "UPDATE assertion SET subject_node_id = 'entity-6a26db0c264ca5d90c540a595c19', updated_at = ? "
            "WHERE subject_node_id = 'entity-2a2648a1b03f0a600f85378dc03c'",
            (now_iso,),
        )
        conn.execute(
            "UPDATE assertion SET object_node_id = 'entity-6a26db0c264ca5d90c540a595c19', updated_at = ? "
            "WHERE object_node_id = 'entity-2a2648a1b03f0a600f85378dc03c'",
            (now_iso,),
        )
        conn.execute(
            "UPDATE node_state SET lifecycle_state='inactive', currentness_state='historical', "
            "retrieval_eligibility='blocked', updated_at=? WHERE node_id='entity-2a2648a1b03f0a600f85378dc03c'",
            (now_iso,),
        )
        conn.execute(
            "UPDATE node SET active = 0, status = 'merged', updated_at = ? WHERE node_id = 'entity-2a2648a1b03f0a600f85378dc03c'",
            (now_iso,),
        )
        conn.execute(
            "UPDATE search_document SET canonical_status='historical', currentness_state='historical', "
            "retrieval_eligible=0, updated_at=? WHERE object_type='node' AND object_id='entity-2a2648a1b03f0a600f85378dc03c'",
            (now_iso,),
        )
    for aid, norm_a, disp_a, eid in SHERPA_ALIASES:
        if conn.execute("SELECT 1 FROM node WHERE node_id = ?", (eid,)).fetchone():
            conn.execute(
                "INSERT OR IGNORE INTO canonical_alias (canonical_alias_id, normalized_alias, display_alias, "
                "entity_id, alias_type, source_id, confidence_label, confidence_score, ambiguity_state, "
                "preferred_flag, review_state, active, created_at) VALUES "
                "(?, ?, ?, ?, 'first_name_or_short', ?, 'high', 0.95, 'unique', 1, 'reviewed', 1, ?)",
                (aid, norm_a, disp_a, eid, default_src, now_iso),
            )
            conn.execute(
                "UPDATE canonical_alias SET entity_id = ?, active = 1 WHERE normalized_alias = ?",
                (eid, norm_a),
            )
    return promoted


def remediate_sherpa_lens_and_role_edges(conn: Any, now_iso: str) -> Dict[str, int]:
    """Execute governed remediation of Sherpa/LENS cross-workstream assertions, role predicates, and structural edges."""
    _ensure_edge_type_domain_ranges(conn, now_iso)
    retired = _retire_contaminated_lens_and_filler_assertions(conn, now_iso)
    promoted_nodes = _promote_sherpa_nodes_and_aliases(conn, now_iso)
    pred_upgraded = conn.execute(
        "UPDATE assertion SET predicate = 'HAS_ROLE_IN', updated_at = ? "
        "WHERE predicate = 'RELATED_TO' AND scope_text LIKE '% HAS_ROLE %'",
        (now_iso,),
    ).rowcount

    superseded = conn.execute(
        "UPDATE assertion SET currentness_state = 'historical', retrieval_eligible = 0, superseded_at = ?, updated_at = ? "
        "WHERE assertion_id IN ('asrt_claim_claim_v36_sherpa_technical_lead_resolved_product_architecture_open', "
        "'asrt_claim_claim_v36_lillian_current_sherpa_technical_lead')",
        (now_iso, now_iso),
    ).rowcount
    conn.execute(
        "UPDATE search_document SET currentness_state = 'historical', retrieval_eligible = 0, updated_at = ? "
        "WHERE assertion_id IN ('asrt_claim_claim_v36_sherpa_technical_lead_resolved_product_architecture_open', "
        "'asrt_claim_claim_v36_lillian_current_sherpa_technical_lead')",
        (now_iso,),
    )
    conn.execute(
        "UPDATE assertion SET scope_text = 'Apex Global Enterprises hired Egle Tamasevice as the client-side Project Beacon Tech Lead; PartnerAdvisory engineering delivery and architecture remain active.', updated_at = ? "
        "WHERE assertion_id = 'assertion-baa73919e0484a69f5774f071555'",
        (now_iso,),
    )

    fallback_src_row = conn.execute("SELECT source_id FROM source ORDER BY created_at DESC LIMIT 1").fetchone()
    fallback_src = fallback_src_row[0] if fallback_src_row else "source-c8ba9093a5a3687e0d016e35"
    has_ae = _has_table(conn, "assertion_evidence")
    has_ei = _has_table(conn, "evidence_item")
    edges_added = 0
    assertions_inserted = 0
    for subj, pred, obj, pref_src, scope in SHERPA_STRUCTURAL_EDGES:
        src_id = pref_src if conn.execute("SELECT 1 FROM source WHERE source_id = ?", (pref_src,)).fetchone() else fallback_src
        aid = stable_id("assertion-sherpa-edge", subj, pred, obj, length=28)
        afp = stable_id("afp-sherpa-edge", subj, pred, obj, length=64).replace("afp-sherpa-edge-", "").ljust(64, "c")[:64]
        eid = stable_id("edge-sherpa", subj, pred, obj, length=28)
        sdoc_id = stable_id("search-sherpa-edge", aid, length=28)
        ins_cur = conn.execute(
            "INSERT OR IGNORE INTO assertion (assertion_id, assertion_fingerprint, subject_node_id, predicate, "
            "object_node_id, assertion_type, atomicity_state, scope_text, speaker_or_author, primary_source_id, "
            "extraction_method, origin_type, confidence_label, confidence_score, validation_state, currentness_state, "
            "valid_from, date_precision, date_status, recorded_at, resolution_state, canonical_status, "
            "canonical_eligible, retrieval_eligible, materiality, classification, allowed_audiences_json, "
            "hitl_required, validation_priority, qualifiers_json, legacy_record_type, legacy_record_ids_json, "
            "created_at, updated_at, approval_reference) VALUES "
            "(?, ?, ?, ?, ?, 'extracted_claim', 'atomic', ?, 'Project Beacon Governance Remediation', ?, "
            "'dual_pipeline_structured_adapter', 'human_source', 'high', 0.96, 'needs_review', 'current', "
            "'2026-10-16', 'day', 'exact', '2026-10-16T15:00:00Z', 'resolved', 'candidate', 0, 1, 'high', 'internal', "
            "'[\"internal_consulting\",\"nee-engineering\"]', 0, 'P1', '{}', 'edge', '[]', ?, ?, NULL)",
            (aid, afp, subj, pred, obj, scope, src_id, now_iso, now_iso),
        )
        if has_ae and has_ei:
            ev_row = conn.execute("SELECT evidence_id FROM evidence_item WHERE source_id = ? LIMIT 1", (src_id,)).fetchone()
            if ev_row:
                conn.execute(
                    "INSERT OR IGNORE INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, independent_corroboration, created_at) "
                    "VALUES (?, ?, 'supports', 0, ?)",
                    (aid, ev_row[0], now_iso),
                )
        conn.execute(
            "INSERT INTO search_document (search_document_id, object_type, object_id, title, body, "
            "canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id, updated_at) "
            "VALUES (?, 'assertion', ?, ?, ?, 'candidate', 'current', 0, ?, ?, ?) "
            "ON CONFLICT(search_document_id) DO NOTHING",
            (sdoc_id, aid, scope, scope, src_id, aid, now_iso),
        )
        conn.execute(
            "UPDATE assertion SET canonical_status = 'canonical', canonical_eligible = 1, "
            "validation_state = 'accepted_legacy', currentness_state = 'current', "
            "retrieval_eligible = 1, approval_reference = 'REQ_INGEST_CEC57955C84BCE3A', updated_at = ? "
            "WHERE assertion_id = ?",
            (now_iso, aid),
        )
        sdoc_elig = (
            conn.execute(
                "SELECT CASE WHEN EXISTS (SELECT 1 FROM assertion_evidence ae WHERE ae.assertion_id = ? AND ae.evidence_relationship = 'supports') THEN 1 ELSE 0 END",
                (aid,),
            ).fetchone()[0]
            if has_ae
            else 1
        )
        conn.execute(
            "UPDATE search_document SET canonical_status = 'canonical', currentness_state = 'current', "
            "retrieval_eligible = ?, updated_at = ? WHERE search_document_id = ?",
            (sdoc_elig, now_iso, sdoc_id),
        )
        conn.execute(
            "INSERT INTO edge (edge_id, from_node_id, edge_type, to_node_id, description, "
            "evidence_posture, confidence, materiality, validation_priority, hitl_required, source_id, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'direct_empirical', 'high', 'high', 'P1', 0, ?, ?, ?) "
            "ON CONFLICT(edge_id) DO UPDATE SET edge_type=excluded.edge_type, description=excluded.description, updated_at=excluded.updated_at",
            (eid, subj, pred, obj, scope, src_id, now_iso, now_iso),
        )
        assertions_inserted += ins_cur.rowcount
        edges_added += 1
    return {
        "contaminated_assertions_retired": retired,
        "promoted_canonical_nodes": promoted_nodes,
        "upgraded_has_role_in_predicates": pred_upgraded,
        "superseded_stale_tech_lead_assertions": superseded,
        "structural_edges_materialized": edges_added,
        "assertions_inserted": assertions_inserted,
    }
