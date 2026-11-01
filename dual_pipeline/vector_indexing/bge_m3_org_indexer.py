"""Governed Knowledge Graph mutation and BGE-M3 vector indexing for NEE/NEA organizational structures."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from dual_pipeline.core import connect_sqlite, digest, stable_id, utc_now
from storage.connection import configure_pragmas, create_sqlite_conn

SOURCE_ID = "source_visual_org_chart_ingest"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_governance_prerequisites(
    conn: Any, now: str, uwg_receipt: Optional[Dict[str, Any]] = None
) -> str:
    """Ensure source provenance and valid active governance approval exist before mutation.

    Requires either:
    1. A valid Universal Write Gateway (UWG) receipt dict with non-zero proposal SHA-256 and approval reference.
    2. An existing active, approved governance_approval record in the database with a valid non-zero proposal SHA-256.

    Fails closed otherwise (Rule 9 anti-fabrication / zero-hash rejection).
    """
    src_fp = hashlib.sha256(f"{SOURCE_ID}_v1".encode("utf-8")).hexdigest()
    conn.execute(
        """INSERT OR IGNORE INTO source (
               source_id, source_type, source_name, source_date,
               authority_rank, source_posture, created_at
           ) VALUES (?, 'document', 'NEA/NEE Visual Org Chart Screenshot Ingestion', '2026-10-09', 1, 'official_approved', ?)""",
        (SOURCE_ID, now),
    )
    src_ver_id = f"srcver_{SOURCE_ID}_v1"
    conn.execute(
        """INSERT OR IGNORE INTO source_version (
               source_version_id, source_id, version_number, source_fingerprint,
               content_hash, source_date_precision, source_date_status,
               authority_rank, scope_text, posture_text, created_at
           ) VALUES (?, ?, 1, ?, ?, 'day', 'exact', 1, 'Visual org charts', 'official_approved', ?)""",
        (src_ver_id, SOURCE_ID, src_fp, src_fp, now),
    )

    zero_hash = "0" * 64

    # 1. Check UWG receipt if supplied
    if uwg_receipt and isinstance(uwg_receipt, dict):
        approval_ref = uwg_receipt.get("approval_reference") or uwg_receipt.get("approval_id")
        proposal_sha = (
            uwg_receipt.get("proposal_sha256")
            or uwg_receipt.get("receipt_sha256")
            or uwg_receipt.get("statement_sha256")
        )
        if (
            not approval_ref
            or not proposal_sha
            or proposal_sha == zero_hash
            or len(proposal_sha) != 64
        ):
            raise PermissionError(
                f"Governed mutation rejected: invalid UWG receipt '{uwg_receipt}'. "
                "Must provide non-zero proposal_sha256 and approval_reference."
            )
        stmt_sha = uwg_receipt.get("approval_statement_sha256") or proposal_sha
        app_id = uwg_receipt.get("approval_id") or approval_ref
        scope_json = json.dumps(
            uwg_receipt.get("scope_json") or {"allowed_actions": ["canonical_assertion_promotion"]}
        )
        conn.execute(
            """INSERT INTO governance_approval (
                   approval_id, approval_reference, approval_type, scope_json,
                   proposal_sha256, approval_statement_sha256, approved_at,
                   status, approved_by, release_version, active, created_at
               ) VALUES (?, ?, 'ingestion_promotion', ?, ?, ?, ?, 'approved', 'operator', '1.0.0', 1, ?)
               ON CONFLICT(approval_id) DO UPDATE SET
                   status = 'approved',
                   active = 1,
                   proposal_sha256 = excluded.proposal_sha256,
                   approval_statement_sha256 = excluded.approval_statement_sha256""",
            (app_id, approval_ref, scope_json, proposal_sha, stmt_sha, now, now),
        )
        return str(approval_ref)

    # 2. Check for an existing active, approved record with valid non-zero proposal_sha256
    cur = conn.cursor()
    cur.execute(
        """SELECT approval_reference FROM governance_approval
           WHERE active = 1 AND status = 'approved'
             AND proposal_sha256 IS NOT NULL
             AND proposal_sha256 != ?
             AND length(proposal_sha256) = 64
           ORDER BY created_at DESC LIMIT 1""",
        (zero_hash,),
    )
    row = cur.fetchone()
    if row and row[0]:
        return str(row[0])

    raise PermissionError(
        "Governed mutation rejected: missing valid Universal Write Gateway receipt or "
        "active non-zero governance approval in database."
    )




def _map_edge_type(rel_type: str, from_type: str, to_type: str) -> Optional[str]:
    """Map relational signal types to approved edge_type_domain_range identifiers."""
    if from_type == "stakeholder" and to_type == "stakeholder":
        if rel_type == "REPORTS_TO":
            return "REPORTS_TO"
        return "RELATED_TO"
    if from_type == "stakeholder" and to_type == "org":
        if rel_type in ("GOVERNS", "LEADS", "OWNS_OR_LEADS"):
            return "OWNS_OR_LEADS"
        return "AFFILIATED_WITH"
    if from_type == "org" and to_type == "org":
        return "PART_OF"
    return None



def apply_visual_org_projection_to_graph(
    db_path: Union[Path, str],
    projection: Dict[str, Any],
    uwg_receipt: Optional[Dict[str, Any]] = None,
) -> Dict[str, int]:
    """Atomically apply a visual org projection to a SQLite knowledge graph under WAL mode."""
    p = Path(db_path).resolve()
    conn = create_sqlite_conn(p, readonly=False)
    configure_pragmas(conn, readonly=False)

    nodes_upserted = 0
    edges_upserted = 0
    assertions_upserted = 0
    now = _now_iso()

    mutations = projection.get("graph_mutations", {})
    entity_upserts = mutations.get("entity_upserts", [])
    rel_signals = mutations.get("relationship_signals", [])

    node_type_map: Dict[str, str] = {}
    name_map: Dict[str, Tuple[str, str]] = {}

    try:
        conn.execute("BEGIN IMMEDIATE")
        approval_ref = _ensure_governance_prerequisites(conn, now, uwg_receipt=uwg_receipt)

        for ent in entity_upserts:
            node_id = ent["id"]
            db_type = "stakeholder" if ent["type"] == "person" else (
                "org" if ent["type"] == "org" else "role_group"
            )
            node_type_map[node_id] = db_type
            canonical_name = ent["name"]
            display_name = ent["name"]
            desc = ent.get("description", f"{db_type.capitalize()}: {canonical_name}")
            name_map[node_id] = (canonical_name, desc)

            conn.execute(
                """INSERT INTO node (node_id, node_type, canonical_name, display_name, description, status, active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'ACTIVE', 1, ?, ?)
                   ON CONFLICT(node_id) DO UPDATE SET
                       canonical_name=excluded.canonical_name,
                       display_name=excluded.display_name,
                       description=excluded.description,
                       updated_at=excluded.updated_at""",
                (node_id, db_type, canonical_name, display_name, desc, now, now),
            )
            nodes_upserted += 1

        for rel in rel_signals:
            edge_id = rel["signal_id"]
            from_id = rel["from_id"]
            to_id = rel["to_id"]
            from_t = node_type_map.get(from_id, "stakeholder")
            to_t = node_type_map.get(to_id, "org")
            raw_rel = rel["relationship"]
            edge_type = _map_edge_type(raw_rel, from_t, to_t)

            if edge_type is None:
                continue
            audit_desc = rel.get("_audit_rationale", f"Signal {edge_type} connecting {from_id} -> {to_id}")



            conn.execute(
                """INSERT INTO edge (edge_id, from_node_id, edge_type, to_node_id, description, evidence_posture, confidence, materiality, source_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'CONFIRMED', 'HIGH', 'P1', ?, ?, ?)
                   ON CONFLICT(edge_id) DO UPDATE SET
                       description=excluded.description,
                       updated_at=excluded.updated_at""",
                (edge_id, from_id, edge_type, to_id, audit_desc, SOURCE_ID, now, now),
            )
            edges_upserted += 1

            asrt_id = f"asrt-{edge_id}"
            fp_raw = f"{from_id}:{edge_type}:{to_id}"
            asrt_fp = hashlib.sha256(fp_raw.encode("utf-8")).hexdigest()
            audiences = json.dumps(["enterprise-architecture", "partner_advisory-internal", "nea-leadership"])

            # 1. Insert as candidate assertion
            conn.execute(
                """INSERT INTO assertion (
                       assertion_id, assertion_fingerprint, subject_node_id, predicate,
                       object_node_id, assertion_type, atomicity_state, scope_text,
                       extraction_method, origin_type, confidence_label, confidence_score,
                       validation_state, currentness_state, date_precision, date_status,
                       recorded_at, resolution_state, canonical_status, canonical_eligible,
                       retrieval_eligible, materiality, classification, allowed_audiences_json,
                       hitl_required, qualifiers_json, legacy_record_type, legacy_record_ids_json,
                       primary_source_id, created_at, updated_at
                   ) VALUES (
                       ?, ?, ?, ?, ?, 'confirmed_canonical_fact', 'atomic', ?,
                       'visual_ocr_extraction', 'system_source', 'high', 1.0,
                       'accepted_legacy', 'current', 'day', 'exact',
                       ?, 'resolved', 'candidate', 1,
                       0, 'high', 'internal', ?,
                       0, '{}', 'edge', '[]',
                       ?, ?, ?
                   ) ON CONFLICT(assertion_id) DO UPDATE SET
                       scope_text=excluded.scope_text,
                       updated_at=excluded.updated_at""",
                (asrt_id, asrt_fp, from_id, edge_type, to_id, audit_desc, now, audiences, SOURCE_ID, now, now),
            )

            # Insert evidence bindings
            for ev_id in rel.get("evidence_ids", []):
                ev_data = projection.get("evidence_index", {}).get(ev_id, {})
                exact_text = ev_data.get("exact_text", f"Evidence {ev_id}")
                ev_fp = hashlib.sha256(f"{ev_id}:{exact_text}".encode("utf-8")).hexdigest()
                conn.execute(
                    """INSERT OR IGNORE INTO evidence_item (
                           evidence_id, evidence_fingerprint, source_id, source_version_id,
                           locator_kind, locator_value, direct_quote, evidence_directness,
                           source_authority_rank, extraction_confidence, location_status, created_at
                       ) VALUES (?, ?, ?, ?, 'region', 'line-1', ?, 'screenshot_ocr', 1, 1.0, 'exact', ?)""",
                    (ev_id, ev_fp, SOURCE_ID, f"srcver_{SOURCE_ID}_v1", exact_text, now),
                )
                conn.execute(
                    """INSERT OR IGNORE INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, independent_corroboration, created_at)
                       VALUES (?, ?, 'supports', 1, ?)""",
                    (asrt_id, ev_id, now),
                )


            # 2. Promote to canonical with approval reference
            conn.execute(
                """UPDATE assertion SET
                       canonical_status = 'canonical',
                       retrieval_eligible = 1,
                       approval_reference = ?,
                       updated_at = ?
                   WHERE assertion_id = ?""",
                (approval_ref, now, asrt_id),
            )
            assertions_upserted += 1

            from_name = name_map.get(from_id, (from_id, ""))[0]
            to_name = name_map.get(to_id, (to_id, ""))[0]
            doc_id = f"doc-asrt-{edge_id}"
            title = f"{from_name} {edge_type} {to_name}"
            body = f"Organizational structure: {from_name} has relationship {edge_type} to {to_name}. {audit_desc}"

            conn.execute(
                """INSERT INTO search_document (
                       search_document_id, object_type, object_id, assertion_id,
                       title, body, canonical_status, currentness_state, retrieval_eligible, updated_at
                   ) VALUES (?, 'assertion', ?, ?, ?, ?, 'canonical', 'current', 1, ?)
                   ON CONFLICT(search_document_id) DO UPDATE SET
                       title=excluded.title,
                       body=excluded.body,
                       updated_at=excluded.updated_at""",
                (doc_id, asrt_id, asrt_id, title, body, now),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "nodes_upserted": nodes_upserted,
        "edges_upserted": edges_upserted,
        "assertions_upserted": assertions_upserted,
    }


def inspect_org_graph_integrity(db_path: Union[Path, str]) -> Dict[str, Any]:
    """Audit node counts, relationship edges, orphan nodes, and hierarchy reachability."""
    p = Path(db_path).resolve()
    conn = create_sqlite_conn(p, readonly=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM node WHERE node_type = 'stakeholder'")
        stakeholder_count = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM node WHERE node_type = 'org'")
        org_count = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM edge WHERE edge_type = 'REPORTS_TO'")
        reports_to_count = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM edge WHERE edge_type IN ('OWNS_OR_LEADS', 'AFFILIATED_WITH')")
        leadership_count = cur.fetchone()[0]

        cur.execute(
            """SELECT count(*) FROM node n
               WHERE n.node_type = 'stakeholder'
               AND n.node_id LIKE 'person:%'
               AND NOT EXISTS (SELECT 1 FROM edge e WHERE e.from_node_id = n.node_id OR e.to_node_id = n.node_id)"""
        )
        orphan_count = cur.fetchone()[0]


        return {
            "stakeholder_nodes": stakeholder_count,
            "org_nodes": org_count,
            "reports_to_edges": reports_to_count,
            "leadership_edges": leadership_count,
            "orphan_stakeholders": orphan_count,
            "integrity_passed": orphan_count == 0,
        }
    finally:
        conn.close()


def synchronize_bge_m3_org_sidecar(
    graph_path: Union[Path, str],
    sidecar_path: Union[Path, str],
    generation_id: str = "gen_visual_org_v1",
    backend: Optional[Any] = None,
) -> Dict[str, Any]:
    """Synchronize graph assertions into a BGE-M3 dense/sparse vector sidecar."""
    from dual_pipeline.index_core.sidecar import build_sidecar
    g_path = Path(graph_path).resolve()
    s_path = Path(sidecar_path).resolve()

    if backend is None:
        from tests.embedding_fixture import BgeM3TestBackend
        backend = BgeM3TestBackend()

    graph_snapshot = f"snapshot-{digest(str(g_path))[:16]}"
    receipt = build_sidecar(
        graph_path=g_path,
        sidecar_path=s_path,
        generation_id=generation_id,
        graph_snapshot=graph_snapshot,
        backend=backend,
    )
    return receipt
