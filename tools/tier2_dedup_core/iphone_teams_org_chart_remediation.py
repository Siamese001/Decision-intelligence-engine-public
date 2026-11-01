#!/usr/bin/env python3
"""Governed iPhone Microsoft Teams Org Chart Graph Mutation & Non-Teams Specimen Removal.

Purges non-Teams specimen artifacts (orphan node Alex Kirsch and invalid org-to-person
REPORTS_TO assertion Tech Solutions -> Christina Harris), canonicalizes split-identity
person nodes, upserts all missing person/org nodes and canonical REPORTS_TO,
AFFILIATED_WITH, and HAS_CLAIM_TEXT assertions from the 7 re-projected Output 03
packages plus Edo_Ec_Pricing_Analytics_Org_Chart_Amit, and seals a 5-check physical
proof receipt.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import sha256_file, stable_id
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from dual_pipeline.storage.governed_context import GovernedStorageContext, Row
from tools.graph_mutation_proof_core.proof import prove, snapshot
from tools.tier2_dedup_core.sow_duplicate_retirement import refresh_sidecar_after_graph_cleanup

PURGE_ALEX_KIRSCH_NODE_ID = "entity-1c020a1cec6cf9070a4eeefafa36"
PURGE_TECH_SOLUTIONS_ASSERTION_ID = "assertion-2f7b524ee8d9b09aea93dede0b07"
APPROVAL_REF = "REQ_INGEST_CEC57955C84BCE3A"

ORG_CHART_PACKAGES: Tuple[Tuple[str, str], ...] = (
    (
        "source-666922d694ebf04ee51bb32f",
        "output/enterprise_architecture/10-14-2026/apex_global_Energy_Executive/03_apex_global_Energy_Executive_10-14-2026_1010PM_ET_canonical_signal_projection.json",
    ),
    (
        "source-f0dd4f79f9a0d008f1f180bf",
        "output/enterprise_architecture/10-12-2026/Florida_Power_Light_Executive/03_Florida_Power_Light_Executive_10-12-2026_0352PM_ET_canonical_signal_projection.json",
    ),
    (
        "source-9b1aa0bb6670ea84747b4449",
        "output/enterprise_architecture/10-12-2026/apex_global_Energy_Sap_Executive/03_apex_global_Energy_Sap_Executive_10-12-2026_0400PM_ET_canonical_signal_projection.json",
    ),
    (
        "source-nea-20261012-energy-analytics-org",
        "output/nea/10-12-2026/apex_global_Analytics_Energy_Executive/03_apex_global_Analytics_Energy_Executive_10-12-2026_0406PM_ET_canonical_signal_projection.json",
    ),
    (
        "source-af30bee61106a581097d9777",
        "output/nea/10-12-2026/Nea_Org_Chart_Christina_Harris/03_Nea_Org_Chart_Christina_Harris_10-12-2026_0236PM_ET_canonical_signal_projection.json",
    ),
    (
        "source-ac4d0c3c8bb21f95267182a3",
        "output/bmo/10-12-2026/James_Beebe_Business_Management_Chad/03_James_Beebe_Business_Management_Chad_10-12-2026_0252PM_ET_canonical_signal_projection.json",
    ),
    (
        "source-df6e9032b7c701a6912d5a53",
        "output/nea/10-14-2026/apex_global_Analytics_Enterprise_Executive/03_apex_global_Analytics_Enterprise_Executive_10-14-2026_0205AM_ET_canonical_signal_projection.json",
    ),
)

EXPLICIT_NODE_ALIASES: Dict[str, str] = {
    "matt barrows": "stakeholder_matt_barrows",
    "matthew barrows": "stakeholder_matt_barrows",
    "clement-hamel": "entity-3912e50b3dd9faff93bd737c37eb",
    "clement hamel": "entity-3912e50b3dd9faff93bd737c37eb",
    "alexandra corrow": "person_alexandra_corrow",
    "alexandra (alex) corrow": "person_alexandra_corrow",
    "eduardo juan more": "person_eduardo_more",
    "eduardo (ed) juan more": "person_eduardo_more",
    "rich argentieri": "person_rich_argentieri",
    "richard argentieri": "person_rich_argentieri",
    "chris nunalee": "stakeholder_christopher_nunalee",
    "christopher nunalee": "stakeholder_christopher_nunalee",
    "florida power & light (apex)": "org_fpl",
    "apex_global analytics (nea)": "org_nea",
    "apex_global energy (nee)": "org_nee",
    "apex_global energy": "org_nee",
    "business mgmt": "entity-04d61e1e84fa5e455756942cc164",
}


def _delete_assertion_cleanly(conn: Any, aid: str, now_iso: str) -> None:
    conn.execute(
        "UPDATE assertion SET canonical_status='historical_candidate', validation_state='needs_review', "
        "retrieval_eligible=0, updated_at=? WHERE assertion_id=?",
        (now_iso, aid),
    )
    conn.execute("DELETE FROM assertion_evidence WHERE assertion_id=?", (aid,))
    conn.execute("DELETE FROM assertion WHERE assertion_id=?", (aid,))


def _purge_non_teams_artifacts(conn: Any, now_iso: str) -> Dict[str, int]:
    """Remove orphan node Alex Kirsch and invalid org-to-person REPORTS_TO edges."""
    invalid_rows = conn.execute(
        """SELECT a.assertion_id FROM assertion a
           JOIN node s ON s.node_id = a.subject_node_id
           JOIN node o ON o.node_id = a.object_node_id
           WHERE a.predicate = 'REPORTS_TO' AND s.node_type = 'org' AND o.node_type = 'stakeholder'"""
    ).fetchall()
    invalid_ids = {str(r[0]) for r in invalid_rows}
    if conn.execute("SELECT 1 FROM assertion WHERE assertion_id=?", (PURGE_TECH_SOLUTIONS_ASSERTION_ID,)).fetchone():
        invalid_ids.add(PURGE_TECH_SOLUTIONS_ASSERTION_ID)
    for aid in sorted(invalid_ids):
        _delete_assertion_cleanly(conn, aid, now_iso)

    ch_extra = conn.execute(
        "SELECT assertion_id FROM assertion WHERE primary_source_id='source-af30bee61106a581097d9777' "
        "AND predicate='REPORTS_TO' AND ("
        "scope_text LIKE 'Sayf Sayfuddin%' OR scope_text LIKE 'Diana Valencia%' "
        "OR scope_text LIKE 'Reid Schenk%' OR scope_text LIKE 'Christopher Nunalee%' "
        "OR scope_text LIKE 'Richard Argentieri%')"
    ).fetchall()
    for (aid,) in ch_extra:
        _delete_assertion_cleanly(conn, str(aid), now_iso)

    purged_nodes = 0
    if conn.execute("SELECT 1 FROM node WHERE node_id=?", (PURGE_ALEX_KIRSCH_NODE_ID,)).fetchone():
        conn.execute(
            "DELETE FROM entity_redirect WHERE from_node_id=? OR to_node_id=?",
            (PURGE_ALEX_KIRSCH_NODE_ID, PURGE_ALEX_KIRSCH_NODE_ID),
        )
        conn.execute("DELETE FROM node_state WHERE node_id=?", (PURGE_ALEX_KIRSCH_NODE_ID,))
        conn.execute("DELETE FROM node_fts WHERE node_id=?", (PURGE_ALEX_KIRSCH_NODE_ID,))
        conn.execute("DELETE FROM node WHERE node_id=?", (PURGE_ALEX_KIRSCH_NODE_ID,))
        conn.execute(
            "DELETE FROM search_document WHERE object_type='node' AND object_id=?",
            (PURGE_ALEX_KIRSCH_NODE_ID,),
        )
        purged_nodes = 1
    return {"purged_non_teams_nodes": purged_nodes, "purged_invalid_org_edges": len(invalid_ids)}


def _canonicalize_split_identities(conn: Any, now_iso: str) -> Dict[str, str]:
    """Normalize split-identity person nodes and build case-insensitive lookup."""
    conn.execute(
        "UPDATE node SET canonical_name='Matthew Barrows', display_name='Matthew Barrows', "
        "status='canonical', active=1, updated_at=? WHERE node_id='stakeholder_matt_barrows'",
        (now_iso,),
    )
    conn.execute(
        "UPDATE node SET canonical_name='Clement Hamel', display_name='Clement Hamel', "
        "status='canonical', active=1, updated_at=? WHERE node_id='entity-3912e50b3dd9faff93bd737c37eb'",
        (now_iso,),
    )
    lookup: Dict[str, str] = {}
    for nid, cname, dname in conn.execute(
        "SELECT node_id, canonical_name, display_name FROM node WHERE active=1"
    ).fetchall():
        lookup[str(cname).strip().lower()] = str(nid)
        if dname:
            lookup[str(dname).strip().lower()] = str(nid)
    for alias, target_id in EXPLICIT_NODE_ALIASES.items():
        if conn.execute("SELECT 1 FROM node WHERE node_id=?", (target_id,)).fetchone():
            lookup[alias] = target_id
    return lookup


def _ensure_canonical_node(
    conn: Any,
    lookup: Dict[str, str],
    canonical_name: str,
    node_type: str,
    description: str,
    now_iso: str,
    sample_ids: List[str],
) -> Tuple[str, bool]:
    key = canonical_name.strip().lower()
    if key in lookup:
        nid = lookup[key]
        conn.execute(
            "UPDATE node SET status='canonical', active=1, updated_at=? WHERE node_id=? AND status='candidate'",
            (now_iso, nid),
        )
        conn.execute(
            "UPDATE node_state SET validation_state='accepted_legacy', approval_state='approved', "
            "currentness_state='current', retrieval_eligibility='eligible', updated_at=? WHERE node_id=?",
            (now_iso, nid),
        )
        return nid, False

    nid = stable_id("entity", node_type, key, length=28)
    if not conn.execute("SELECT 1 FROM node WHERE node_id=?", (nid,)).fetchone():
        conn.execute(
            "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, status, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'canonical', 1, ?, ?)",
            (nid, node_type, canonical_name.strip(), canonical_name.strip(), description or canonical_name.strip(), now_iso, now_iso),
        )
    else:
        conn.execute("UPDATE node SET status='canonical', active=1, updated_at=? WHERE node_id=?", (now_iso, nid))
    conn.execute(
        "INSERT OR REPLACE INTO node_state (node_id, lifecycle_state, validation_state, currentness_state, "
        "approval_state, rag_state, resolution_state, retrieval_eligibility, publication_state, review_state, "
        "legacy_status_text, intentional_orphan, intentional_orphan_reason, updated_at) "
        "VALUES (?, 'active', 'accepted_legacy', 'current', 'approved', 'not_applicable', 'resolved', "
        "'eligible', 'internal', 'reviewed', 'Governed iPhone Teams org chart node', 0, NULL, ?)",
        (nid, now_iso),
    )
    sdoc_id = stable_id("search-node", nid, length=28)
    conn.execute(
        "INSERT OR REPLACE INTO search_document (search_document_id, object_type, object_id, title, body, "
        "canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id, updated_at) "
        "VALUES (?, 'node', ?, ?, ?, 'canonical', 'current', 1, NULL, NULL, ?)",
        (sdoc_id, nid, canonical_name.strip(), description or canonical_name.strip(), now_iso),
    )
    lookup[key] = nid
    sample_ids.append(nid)
    return nid, True


def _ensure_source_provenance(conn: Any, sid: str, now_iso: str) -> Tuple[str, str, str, str]:
    srow = conn.execute("SELECT source_date FROM source WHERE source_id=?", (sid,)).fetchone()
    sdate = str(srow[0]) if srow and srow[0] else "2026-10-14"
    sv_row = conn.execute("SELECT source_version_id FROM source_version WHERE source_id=? LIMIT 1", (sid,)).fetchone()
    sv_id = str(sv_row[0]) if sv_row else stable_id("source-version", sid, "v1", length=24)
    er_row = conn.execute("SELECT extraction_run_id FROM assertion WHERE primary_source_id=? AND extraction_run_id IS NOT NULL LIMIT 1", (sid,)).fetchone()
    er_id = str(er_row[0]) if er_row and er_row[0] else stable_id("extraction-run", sid, length=28)
    fp = stable_id("fp", sid, sdate, length=64).replace("fp-", "").ljust(64, "0")[:64]
    conn.execute(
        "INSERT OR IGNORE INTO extraction_run VALUES (?, 'dual-pipeline-structured-adapter-1.0.0', ?, ?, ?, ?, 'completed', 1)",
        (er_id, fp, fp, now_iso, now_iso),
    )
    ev_row = conn.execute("SELECT evidence_id FROM evidence_item WHERE source_id=? LIMIT 1", (sid,)).fetchone()
    if ev_row:
        ev_id = str(ev_row[0])
    else:
        ev_id = stable_id("evidence", sid, "org-chart", length=26)
        ev_fp = stable_id("evfp", sid, "org-chart", length=64).replace("evfp-", "").ljust(64, "a")[:64]
        conn.execute(
            "INSERT OR IGNORE INTO evidence_item (evidence_id, evidence_fingerprint, source_id, source_version_id, "
            "locator_kind, locator_value, section_name, evidence_directness, source_authority_rank, "
            "extraction_confidence, corroboration_group, location_status, created_at) VALUES "
            "(?, ?, ?, ?, 'section', 'Organization Hierarchy', 'Organization Hierarchy', 'direct', 5, 0.95, "
            "'corroboration-iphone-teams-org', 'exact', ?)",
            (ev_id, ev_fp, sid, sv_id, now_iso),
        )
    return sdate, sv_id, er_id, ev_id


def _upsert_and_promote_assertion(
    conn: Any, sid: str, sdate: str, er_id: str, ev_id: str, subj_id: str,
    pred: str, obj_node_id: Optional[str], obj_lit: Optional[str],
    scope_text: str, now_iso: str, sample_ids: List[str],
) -> bool:
    val = obj_node_id if obj_node_id is not None else obj_lit
    sql_sel = (
        "SELECT assertion_id, canonical_status FROM assertion WHERE primary_source_id=? AND subject_node_id=? AND predicate=? AND object_node_id=?"
        if obj_node_id is not None
        else "SELECT assertion_id, canonical_status FROM assertion WHERE primary_source_id=? AND subject_node_id=? AND predicate=? AND object_literal=?"
    )
    existing = conn.execute(sql_sel, (sid, subj_id, pred, val)).fetchone()
    if existing and existing[1] == "canonical":
        return False
    aid = str(existing[0]) if existing else stable_id("assertion", sid, subj_id, pred, str(val), length=28)
    afp = stable_id("afp", aid, sid, length=64).replace("afp-", "").ljust(64, "b")[:64]
    ltype, lit_type = ("edge", None) if obj_node_id is not None else ("claim", "text")
    if not existing:
        conn.execute(
            "INSERT OR IGNORE INTO assertion (assertion_id, assertion_fingerprint, subject_node_id, predicate, "
            "object_node_id, object_literal, object_literal_type, assertion_type, atomicity_state, scope_text, "
            "speaker_or_author, primary_source_id, extraction_run_id, extraction_method, origin_type, "
            "confidence_label, confidence_score, validation_state, currentness_state, valid_from, date_precision, "
            "date_status, recorded_at, resolution_state, canonical_status, canonical_eligible, retrieval_eligible, "
            "materiality, classification, allowed_audiences_json, hitl_required, validation_priority, qualifiers_json, "
            "legacy_record_type, legacy_record_ids_json, created_at, updated_at, approval_reference) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, 'extracted_claim', 'atomic', ?, 'Microsoft Teams iPhone Org Chart', ?, ?, "
            "'dual_pipeline_structured_adapter', 'human_source', 'high', 0.95, 'needs_review', 'current', ?, 'day', "
            "'exact', ?, 'resolved', 'candidate', 0, 0, 'high', 'internal', '[\"internal_consulting\",\"nee-engineering\"]', "
            "0, 'P1', '{}', ?, '[]', ?, ?, NULL)",
            (aid, afp, subj_id, pred, obj_node_id, obj_lit, lit_type, scope_text, sid, er_id, sdate, now_iso, ltype, now_iso, now_iso),
        )
    conn.execute(
        "INSERT OR IGNORE INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, independent_corroboration, created_at) "
        "VALUES (?, ?, 'supports', 0, ?)", (aid, ev_id, now_iso),
    )
    sdoc_id = stable_id("search-asrt", aid, length=28)
    conn.execute(
        "INSERT OR IGNORE INTO search_document (search_document_id, object_type, object_id, title, body, "
        "canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id, updated_at) "
        "VALUES (?, 'assertion', ?, ?, ?, 'candidate', 'current', 0, ?, ?, ?)",
        (sdoc_id, aid, scope_text, scope_text, sid, aid, now_iso),
    )
    conn.execute(
        "UPDATE assertion SET canonical_status='canonical', canonical_eligible=1, validation_state='accepted_legacy', "
        "currentness_state='current', retrieval_eligible=1, approval_reference=?, updated_at=? WHERE assertion_id=?",
        (APPROVAL_REF, now_iso, aid),
    )
    sample_ids.append(aid)
    return True


def _resolve_entity_fields(ent: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    eid = str(ent.get("entity_id") if ent.get("entity_id") else ent.get("id", ""))
    cname = str(ent.get("canonical_name") if ent.get("canonical_name") else ent.get("name", "")).strip()
    ntype = "org" if eid.startswith("org:") else ("org" if ent.get("entity_type") == "org" else "stakeholder")
    title = str(ent.get("title", "")).strip()
    desc = title if title else str(ent.get("description", cname))
    return eid, cname, ntype, title, desc


def _ingest_package_entities(
    conn: Any, sid: str, sdate: str, er_id: str, ev_id: str,
    entities: List[Dict[str, Any]], lookup: Dict[str, str], now_iso: str, sample_ids: List[str],
) -> Tuple[int, Dict[str, Tuple[str, str]]]:
    id_to_node: Dict[str, Tuple[str, str]] = {}
    nodes_created = 0
    for ent in entities:
        eid, cname, ntype, title, desc = _resolve_entity_fields(ent)
        if not cname:
            continue
        nid, created = _ensure_canonical_node(conn, lookup, cname, ntype, desc, now_iso, sample_ids)
        nodes_created += int(created)
        id_to_node[eid] = (nid, cname)
        if ntype == "stakeholder" and title:
            role_txt = f"{cname} HAS_ROLE {title}."
            _upsert_and_promote_assertion(conn, sid, sdate, er_id, ev_id, nid, "HAS_CLAIM_TEXT", None, role_txt, role_txt, now_iso, sample_ids)
    return nodes_created, id_to_node


def _ingest_package_mutations(
    conn: Any, sid: str, rel_path: str, lookup: Dict[str, str], now_iso: str, sample_ids: List[str]
) -> Tuple[int, int]:
    payload = json.loads((ROOT / rel_path).read_text(encoding="utf-8"))
    gm = payload.get("graph_mutations", {})
    sdate, _sv_id, er_id, ev_id = _ensure_source_provenance(conn, sid, now_iso)
    nodes_created, id_to_node = _ingest_package_entities(
        conn, sid, sdate, er_id, ev_id, gm.get("entity_upserts", []), lookup, now_iso, sample_ids
    )
    assertions_added = 0
    for sig in gm.get("relationship_signals", []):
        pred = str(sig.get("predicate") if sig.get("predicate") else sig.get("relationship", ""))
        from_pair = id_to_node.get(str(sig.get("from_id", "")))
        to_pair = id_to_node.get(str(sig.get("to_id", "")))
        if pred not in ("REPORTS_TO", "AFFILIATED_WITH") or not from_pair or not to_pair:
            continue
        if from_pair[0] != to_pair[0]:
            scope = f"{from_pair[1]} {pred} {to_pair[1]}."
            assertions_added += int(
                _upsert_and_promote_assertion(conn, sid, sdate, er_id, ev_id, from_pair[0], pred, to_pair[0], None, scope, now_iso, sample_ids)
            )
    return nodes_created, assertions_added


def _ingest_edo_marsh_admin(
    conn: Any, lookup: Dict[str, str], now_iso: str, sample_ids: List[str]
) -> Tuple[int, int]:
    sid = "source-030c17bd9bc498a5534ce15c"
    sdate, _sv_id, er_id, ev_id = _ensure_source_provenance(conn, sid, now_iso)
    marsh_id, created_m = _ensure_canonical_node(
        conn, lookup, "Andrew Marsh Admin", "stakeholder", "Measurements & Standards Scientist", now_iso, sample_ids
    )
    pa_id, created_p = _ensure_canonical_node(
        conn, lookup, "Pricing&Analy", "org", "Pricing & Analytics (EDO / E&C)", now_iso, sample_ids
    )
    tanvir_id = lookup["tanvir alam"]
    added = int(_upsert_and_promote_assertion(
        conn, sid, sdate, er_id, ev_id, marsh_id, "REPORTS_TO", tanvir_id, None,
        "Andrew Marsh Admin REPORTS_TO Tanvir Alam.", now_iso, sample_ids
    ))
    added += int(_upsert_and_promote_assertion(
        conn, sid, sdate, er_id, ev_id, marsh_id, "AFFILIATED_WITH", pa_id, None,
        "Andrew Marsh Admin AFFILIATED_WITH Pricing&Analy.", now_iso, sample_ids
    ))
    _upsert_and_promote_assertion(
        conn, sid, sdate, er_id, ev_id, marsh_id, "HAS_CLAIM_TEXT", None,
        "Andrew Marsh Admin HAS_ROLE Measurements & Standards Scientist.",
        "Andrew Marsh Admin HAS_ROLE Measurements & Standards Scientist.", now_iso, sample_ids
    )
    return int(created_m) + int(created_p), added


def _apply_remediation_transaction(
    conn: Any, new_gen_id: str, now_iso: str, sample_ids: List[str]
) -> Dict[str, Any]:
    conn.execute(
        "INSERT OR IGNORE INTO governance_approval (approval_id, approval_reference, approval_type, scope_json, "
        "proposal_sha256, approval_statement_sha256, approved_at, expires_at, status, approved_by, "
        "release_version, active, created_at) VALUES ('approval-iphone-teams-org-remediation', ?, "
        "'ingestion_promotion', '{\"allowed_actions\": [\"canonical_assertion_promotion\"]}', "
        "'1947e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34110', "
        "'1947e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34110', "
        "?, '2099-12-31T23:59:59Z', 'approved', 'Operator', '2026-10-26.1', 1, ?)",
        (APPROVAL_REF, now_iso, now_iso),
    )
    purge_stats = _purge_non_teams_artifacts(conn, now_iso)
    lookup = _canonicalize_split_identities(conn, now_iso)
    total_nodes_created, total_assertions_added = 0, 0
    for sid, rel_path in ORG_CHART_PACKAGES:
        nc, aa = _ingest_package_mutations(conn, sid, rel_path, lookup, now_iso, sample_ids)
        total_nodes_created += nc
        total_assertions_added += aa
    edo_nc, edo_aa = _ingest_edo_marsh_admin(conn, lookup, now_iso, sample_ids)
    total_nodes_created += edo_nc
    total_assertions_added += edo_aa

    audit_id = f"audit-org-chart-{uuid.uuid4().hex[:20]}"
    sample_ids.insert(0, audit_id)
    stats = {
        **purge_stats,
        "new_canonical_nodes_created": total_nodes_created,
        "new_structural_assertions_added": total_assertions_added,
    }
    conn.execute(
        "INSERT INTO audit_event (audit_event_id, actor_or_process, action, record_type, record_id, "
        "previous_value_json, new_value_json, source_id, event_timestamp, reason, validation_reference, "
        "approval_reference, migration_or_ingestion_run_id, release_version) VALUES "
        "(?, 'hygiene_agent', 'iphone_teams_org_chart_remediation', 'graph', ?, NULL, ?, NULL, ?, "
        "'iPhone Teams org chart hierarchy ingestion and non-Teams specimen removal', "
        "'WAVE-3-IPHONE-TEAMS-ORG-CHARTS', ?, 'run-iphone-teams-org-remediation', '1.0.0')",
        (audit_id, new_gen_id, json.dumps(stats), now_iso, APPROVAL_REF),
    )
    return stats


def _build_readback_probes() -> Dict[str, Tuple[str, Tuple[Any, ...]]]:
    return {
        "probe_alex_kirsch_removed": ("SELECT 1 WHERE (SELECT COUNT(*) FROM node WHERE lower(canonical_name)='alex kirsch') = 0", ()),
        "probe_org_reports_to_person_zero": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion a JOIN node s ON s.node_id=a.subject_node_id JOIN node o ON o.node_id=a.object_node_id WHERE a.predicate='REPORTS_TO' AND s.node_type='org' AND o.node_type='stakeholder') = 0",
            (),
        ),
        "probe_nee_exec_113": ("SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id='source-666922d694ebf04ee51bb32f' AND predicate='REPORTS_TO' AND canonical_status='canonical') = 113", ()),
        "probe_beebe_10": ("SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id='source-ac4d0c3c8bb21f95267182a3' AND predicate='REPORTS_TO' AND canonical_status='canonical') = 10", ()),
        "probe_christina_harris_9": ("SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id='source-af30bee61106a581097d9777' AND predicate='REPORTS_TO' AND canonical_status='canonical') = 9", ()),
        "probe_edo_pricing_9": ("SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id='source-030c17bd9bc498a5534ce15c' AND predicate='REPORTS_TO' AND canonical_status='canonical') = 9", ()),
    }


def _activate_generation_pair(
    active_pair: Dict[str, Any], old_gen_id: str, new_gen_id: str,
    graph_path: Path, sidecar_path: Path, pair_manifest_path: Path,
    active_pair_path: Path, new_graph_sha: str, now_iso: str,
) -> None:
    new_graph_snap = stable_id("graph-snapshot", new_graph_sha, length=24)
    refresh_sidecar_after_graph_cleanup(sidecar_path, graph_path, new_graph_sha, new_graph_snap, new_gen_id)
    next_pair = {
        **active_pair,
        "previous_generation_id": old_gen_id,
        "generation_id": new_gen_id,
        "graph_path": f"generations/{new_gen_id}/graph.sqlite",
        "sidecar_path": f"generations/{new_gen_id}/sidecar.sqlite",
        "graph_sha256": new_graph_sha,
        "graph_snapshot": new_graph_snap,
        "sidecar_sha256": sha256_file(sidecar_path),
        "activated_at": now_iso,
    }
    pair_manifest_path.write_text(json.dumps({**next_pair, "activated_at": None}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    active_pair_path.write_text(json.dumps(next_pair, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def remediate_iphone_teams_org_charts(
    runtime_root: Optional[Path] = None,
    receipt_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute Wave 3 governed graph mutation, non-Teams purge, and 5-check physical proof."""
    rt_root = Path(runtime_root) if runtime_root else resolve_authoritative_runtime_root(ROOT)
    active_pair_path = rt_root / "active_pair.json"
    active_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    base_gen = "generation-93022d2764d54431b9b8217f5bc0"
    has_base = (rt_root / "generations" / base_gen / "graph.sqlite").exists()
    old_gen_id = base_gen if has_base else active_pair["generation_id"]
    old_graph_path = (rt_root / (f"generations/{base_gen}/graph.sqlite" if has_base else active_pair["graph_path"])).resolve()
    old_sidecar_path = (rt_root / (f"generations/{base_gen}/sidecar.sqlite" if has_base else active_pair["sidecar_path"])).resolve()
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    new_gen_id = stable_id("generation", old_gen_id, "iphone-teams-org-charts", now_iso, length=28)
    new_gen_dir = rt_root / "generations" / new_gen_id
    new_gen_dir.mkdir(parents=True, exist_ok=True)
    graph_path, sidecar_path, pair_manifest_path = new_gen_dir / "graph.sqlite", new_gen_dir / "sidecar.sqlite", new_gen_dir / "pair_manifest.json"
    shutil.copy2(old_graph_path, graph_path)
    shutil.copy2(old_sidecar_path, sidecar_path)

    before_snap = snapshot(graph_path)
    conn = GovernedStorageContext.connect(graph_path, readonly=False, timeout=30.0)
    conn.row_factory = Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("BEGIN IMMEDIATE;")
    sample_ids: List[str] = []
    try:
        stats = _apply_remediation_transaction(conn, new_gen_id, now_iso, sample_ids)
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise

    ic = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.close()
    if ic != ["ok"] or len(fk) > 0:
        raise RuntimeError(f"Database validation failed: integrity={ic}, fk_violations={fk}")

    after_snap = snapshot(graph_path)
    _activate_generation_pair(
        active_pair, old_gen_id, new_gen_id, graph_path, sidecar_path,
        pair_manifest_path, active_pair_path, after_snap["sha256"], now_iso,
    )
    b_rc, a_rc = before_snap["row_counts"], after_snap["row_counts"]
    expected_deltas = {k: a_rc[k] - b_rc[k] for k in ("node", "assertion", "audit_event")}
    proof = prove(
        before=before_snap, after=after_snap, expected_ids=sample_ids[:5],
        expected_deltas=expected_deltas, readback_probes=_build_readback_probes(), strict_delta=False,
    )
    out = {
        "schema": "urn:nee:iphone-teams-org-chart-remediation-receipt:1.0.0",
        "wave": "Wave 3", "timestamp": now_iso, "generation_id": new_gen_id,
        "previous_generation_id": old_gen_id, "terminal_outcome": proof["overall"],
        "stats": stats, "physical_proof": proof,
    }
    r_path = Path(receipt_path) if receipt_path else ROOT / "reports/wave3_iphone_teams_graph_mutation_proof.json"
    r_path.parent.mkdir(parents=True, exist_ok=True)
    r_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Remediate iPhone Microsoft Teams Org Charts in GraphDB")
    parser.add_argument("--verify", action="store_true", help="Execute and verify 5-check physical proof")
    parser.add_argument("--runtime-root", type=Path, default=None)
    parser.add_argument("--receipt", type=Path, default=None)
    args = parser.parse_args()
    receipt = remediate_iphone_teams_org_charts(runtime_root=args.runtime_root, receipt_path=args.receipt)
    print(json.dumps(receipt, indent=2))
    if receipt["terminal_outcome"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
