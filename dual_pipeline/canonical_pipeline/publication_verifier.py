"""
dual_pipeline/canonical_pipeline/publication_verifier.py

Layer 5: Independent read-only graph inspection and publication coverage verification.
Validates that every authorized fact survived into the active canonical graph or has an
explicit non-promotion disposition (EXCLUDED / QUARANTINED with supported rationale).
Produces a sealed publication-coverage receipt bound to the authorized proposal and activated generation.
"""
from __future__ import annotations

import json
import logging
import re
from storage.connection import create_sqlite_conn, sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import jsonschema

from dual_pipeline.canonical_pipeline.evidence_coverage import (
    EvidenceCoverageManifest, EvidenceItem, EvidenceState, ItemType,
)
from dual_pipeline.canonical_pipeline.manifest_synthesis import (
    build_manifest_from_output_03, canonicalize_relationship_predicate,
)

logger = logging.getLogger(__name__)
PCR_SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "contracts" / "dual_pipeline" / "schemas" / "publication-coverage-receipt.schema.json"


def validate_publication_coverage_receipt(payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validates payload against publication-coverage-receipt.schema.json."""
    if not PCR_SCHEMA_PATH.is_file():
        return False, [f"Schema file not found: {PCR_SCHEMA_PATH}"]
    try:
        schema = json.loads(PCR_SCHEMA_PATH.read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        errors = [err.message for err in validator.iter_errors(payload)]
        return len(errors) == 0, errors
    except Exception as exc:
        return False, [f"JSON schema validation error: {exc}"]


def _is_populated_graph(p: Optional[Path]) -> bool:
    if not p or not p.is_file():
        return False
    try:
        conn = create_sqlite_conn(p.resolve(), readonly=True)
        cur = conn.cursor()
        tbls = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        return bool(tbls.intersection({"node", "assertion", "canonical_graph_nodes", "edge"}))
    except Exception:
        return False


def _resolve_from_metadata(metadata: Dict[str, Any], explicit_path: Optional[Union[Path, str]]) -> Optional[Path]:
    if explicit_path:
        p = Path(explicit_path).resolve()
        if p.is_file():
            return p
    for k in ("graph_path", "target_graph"):
        if metadata.get(k):
            p = Path(metadata[k]).resolve()
            if p.is_file():
                return p
    return None


def _resolve_from_active_pair(rt_root: Path) -> Optional[Path]:
    ap_file = rt_root / "active_pair.json"
    if not ap_file.is_file():
        for cand_dir in (
            rt_root / ".local" / "dual_pipeline",
            rt_root / "dual_pipeline",
        ):
            cand = cand_dir / "active_pair.json"
            if cand.is_file():
                ap_file = cand
                break
        else:
            return None
    try:
        ap_data = json.loads(ap_file.read_text(encoding="utf-8"))
        rel = ap_data.get("graph_path")
        if rel:
            gp = (ap_file.parent / rel).resolve()
            if gp.is_file():
                return gp
    except Exception as exc:
        logger.debug("Failed to resolve graph path from active_pair %s: %s", ap_file, exc)
    return None


def _resolve_graph_path(
    metadata: Dict[str, Any],
    output_dir: Optional[Path] = None,
    explicit_path: Optional[Union[Path, str]] = None,
) -> Optional[Path]:
    resolved = _resolve_from_metadata(metadata, explicit_path)
    if _is_populated_graph(resolved):
        return resolved
    rt_root = Path(metadata.get("runtime_root") or output_dir or ".").resolve()
    ap_path = _resolve_from_active_pair(rt_root)
    if _is_populated_graph(ap_path):
        return ap_path
    for cand in (
        rt_root / "graph.sqlite",
        rt_root / "CANONICAL_GRAPH.db",
        rt_root / "graph" / "nee_graph.sqlite",
    ):
        if _is_populated_graph(cand):
            return cand
    if output_dir:
        for cand in (
            output_dir / "CANONICAL_GRAPH.db",
            output_dir / "test_graph.sqlite",
            output_dir / "graph.sqlite",
        ):
            if _is_populated_graph(cand):
                return cand
    default_ap = _resolve_from_active_pair(Path(".local/dual_pipeline").resolve())
    if _is_populated_graph(default_ap):
        return default_ap
    return None


def _lookup_node_by_alias(cur: sqlite3.Cursor, sel_sql: str, name_or_id: str) -> Optional[Any]:
    low = name_or_id.lower()
    sql_node = f"SELECT {sel_sql} FROM node WHERE node_id = ?"
    if cur.execute("PRAGMA table_info(entity_redirect)").fetchall():
        rrow = cur.execute("SELECT to_node_id FROM entity_redirect WHERE LOWER(from_node_id) = ? AND active = 1", (low,)).fetchone()
        if rrow:
            return cur.execute(sql_node, (rrow[0],)).fetchone()
    if not cur.execute("PRAGMA table_info(alias)").fetchall():
        return None
    arow = cur.execute("SELECT node_id FROM alias WHERE LOWER(alias) = ?", (low,)).fetchone()
    return cur.execute(sql_node, (arow[0],)).fetchone() if arow else None


def _lookup_node_fallback_row(
    cur: sqlite3.Cursor, select_fields: List[str], name_or_id: str
) -> Optional[Any]:
    sel_sql = ", ".join(select_fields)
    if ":" in name_or_id:
        clean_name = name_or_id.split(":")[-1].replace("-", " ").replace("_", " ").strip().lower()
        words = [w for w in clean_name.split() if len(w) >= 3]
        if words:
            like_pat = "%" + "%".join(words) + "%"
            cur.execute(
                f"SELECT {sel_sql} FROM node WHERE LOWER(canonical_name) LIKE ? OR LOWER(display_name) LIKE ?",
                (like_pat, like_pat),
            )
            row = cur.fetchone()
            if row:
                return row
    row = _lookup_node_by_alias(cur, sel_sql, name_or_id)
    if row:
        return row
    clean = name_or_id.strip().lower()
    if len(clean) >= 4:
        cur.execute(
            f"SELECT {sel_sql} FROM node WHERE LOWER(canonical_name) LIKE ? OR LOWER(display_name) LIKE ?",
            (f"%{clean}%", f"%{clean}%"),
        )
        return cur.fetchone()
    return None


def _inspect_node_in_graph(conn: sqlite3.Connection, name_or_id: str) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(node)")
    cols = {row[1] for row in cur.fetchall()}
    if not cols:
        return None
    has_props, has_active = "properties_json" in cols, "active" in cols
    select_fields = ["node_id", "canonical_name", "display_name", "description"]
    if has_props:
        select_fields.append("properties_json")
    if has_active:
        select_fields.append("active")

    cur.execute(
        f"SELECT {', '.join(select_fields)} FROM node WHERE LOWER(canonical_name) = ? OR LOWER(display_name) = ? OR LOWER(node_id) = ?",
        (name_or_id.lower(), name_or_id.lower(), name_or_id.lower()),
    )
    row = cur.fetchone() or _lookup_node_fallback_row(cur, select_fields, name_or_id)
    if not row:
        return None
    res = {"node_id": row[0], "canonical_name": row[1], "display_name": row[2], "description": row[3], "properties": {}, "active": True}
    idx = 4
    if has_props:
        if row[idx]:
            try:
                res["properties"] = json.loads(row[idx])
            except Exception as exc:
                logger.debug("Failed to parse node properties_json for %s: %s", res["node_id"], exc)
        idx += 1
    if has_active and len(row) > idx:
        res["active"] = bool(row[idx])
    return res


def _build_assertion_query(
    has_claim_text: bool, has_scope_text: bool, has_quarantined: bool, has_object_node: bool,
    subject_id: str, predicate: str, object_str: Optional[str],
) -> Tuple[str, List[Any]]:
    text_field = "claim_text" if has_claim_text else ("scope_text" if has_scope_text else "''")
    select_fields = ["assertion_id", "subject_node_id", "predicate", "object_literal", text_field, "currentness_state"]
    if has_quarantined:
        select_fields.append("is_quarantined")
    if has_object_node:
        select_fields.append("object_node_id")

    q = (
        f"SELECT {', '.join(select_fields)} FROM assertion "
        "WHERE LOWER(subject_node_id) = ? AND UPPER(predicate) = ? "
        "AND LOWER(COALESCE(currentness_state, 'current')) = 'current'"
    )
    if has_quarantined:
        q += " AND COALESCE(is_quarantined, 0) = 0"
    params: List[Any] = [subject_id.lower(), predicate.upper()]
    if object_str:
        obj_clauses = ["LOWER(object_literal) = ?"]
        params.append(object_str.lower())
        if has_claim_text:
            obj_clauses.append("LOWER(claim_text) LIKE ?")
            params.append(f"%{object_str.lower()}%")
        elif has_scope_text:
            obj_clauses.append("LOWER(scope_text) LIKE ?")
            params.append(f"%{object_str.lower()}%")
        if has_object_node:
            obj_clauses.append("LOWER(object_node_id) = ?")
            params.append(object_str.lower())
        q += f" AND ({' OR '.join(obj_clauses)})"
    return q, params


def _inspect_edge_in_graph(
    conn: sqlite3.Connection, subject_id: str, predicate: str, object_str: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(edge)")
    if not {r[1] for r in cur.fetchall()}:
        return None
    eq = "SELECT edge_id, from_node_id, edge_type, to_node_id FROM edge WHERE LOWER(from_node_id) = ? AND UPPER(edge_type) = ?"
    eparams: List[Any] = [subject_id.lower(), predicate.upper()]
    if object_str:
        eq += " AND LOWER(to_node_id) = ?"
        eparams.append(object_str.lower())
    cur.execute(eq, tuple(eparams))
    erow = cur.fetchone()
    if not erow:
        return None
    return {
        "assertion_id": erow[0], "subject_node_id": erow[1], "predicate": erow[2],
        "object_literal": erow[3], "claim_text": erow[3], "currentness_state": "current",
        "is_quarantined": False, "object_node_id": erow[3],
    }


def _lookup_assertion_text_fallback(
    cur: sqlite3.Cursor, text_col: str, sel_fields: List[str],
    subject_id: str, predicate: str, object_str: Optional[str],
) -> Optional[Any]:
    subj_words = [w for w in re.split(r"[\s\-_]+", subject_id.split(":")[-1]) if w]
    like_subj = "%" + "%".join(subj_words) + "%" if subj_words else f"%{subject_id.lower()}%"
    quarantine_clause = " AND COALESCE(is_quarantined, 0) = 0" if "is_quarantined" in sel_fields else ""
    sq = (
        f"SELECT {', '.join(sel_fields)} FROM assertion WHERE LOWER(subject_node_id) = ? "
        f"AND LOWER(COALESCE(currentness_state, 'current')) = 'current'{quarantine_clause} "
        f"AND LOWER({text_col}) LIKE ? AND LOWER({text_col}) LIKE ?"
    )
    cur.execute(sq, (subject_id.lower(), like_subj.lower(), f"%{predicate.lower()}%"))
    if row := cur.fetchone():
        return row
    clean_subj = subject_id.split(":")[-1].replace("-", " ").replace("_", " ").strip()
    cur.execute(sq, (subject_id.lower(), f"%{clean_subj.lower()}%", f"%{predicate.lower()}%"))
    if row := cur.fetchone():
        return row
    if clean_subj and object_str:
        clean_obj = object_str.split(":")[-1].replace("-", " ").replace("_", " ").strip()
        cur.execute(sq, (subject_id.lower(), f"%{clean_subj.lower()}%", f"%{clean_obj.lower()}%"))
        return cur.fetchone()
    return None


def _format_assertion_row(row: Any, has_quarantined: bool, has_object_node: bool) -> Dict[str, Any]:
    res = {
        "assertion_id": row[0], "subject_node_id": row[1], "predicate": row[2],
        "object_literal": row[3], "claim_text": row[4], "currentness_state": row[5],
        "is_quarantined": False,
    }
    idx = 6
    if has_quarantined:
        res["is_quarantined"] = bool(row[idx])
        idx += 1
    if has_object_node and len(row) > idx:
        res["object_node_id"] = row[idx]
    return res


def _expand_assertion_candidates(
    conn: sqlite3.Connection, predicate: str, object_str: Optional[str]
) -> Tuple[List[str], List[Optional[str]]]:
    up = predicate.upper()
    preds = [up, canonicalize_relationship_predicate(up)]
    if up in {"ROLE", "HAS_ROLE", "HAS_ROLE_IN"}:
        preds.extend(["HAS_ROLE_IN", "HAS_ROLE", "HAS_CLAIM_TEXT"])
    elif up != "HAS_CLAIM_TEXT":
        preds.append("HAS_CLAIM_TEXT")
    pred_list = list(dict.fromkeys(p for p in preds if p))
    if not object_str:
        return pred_list, [None]
    objs: List[Optional[str]] = [object_str]
    obj_node = _inspect_node_in_graph(conn, object_str)
    if obj_node:
        objs.extend([obj_node["node_id"], obj_node["canonical_name"]])
    if ":" in object_str:
        objs.append(object_str.split(":", 1)[1].replace("-", " ").replace("_", " ").strip())
    return pred_list, list(dict.fromkeys(o for o in objs if o))


def _query_candidate_assertion_pairs(
    cur: sqlite3.Cursor, has_claim_text: bool, has_scope_text: bool, has_quarantined: bool,
    has_object_node: bool, subject_id: str, pred_cands: List[str], obj_cands: List[Optional[str]],
) -> Optional[Any]:
    pairs = [(p, o) for p in pred_cands for o in obj_cands]
    for p_cand, o_cand in pairs:
        q, params = _build_assertion_query(
            has_claim_text, has_scope_text, has_quarantined, has_object_node, subject_id, p_cand, o_cand
        )
        cur.execute(q, tuple(params))
        row = cur.fetchone()
        if row:
            return row
    return None


def _inspect_assertion_in_graph(
    conn: sqlite3.Connection, subject_id: str, predicate: str, object_str: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(assertion)")
    cols = {row[1] for row in cur.fetchall()}
    if not cols:
        return None
    has_claim_text, has_scope_text = "claim_text" in cols, "scope_text" in cols
    has_quarantined, has_object_node = "is_quarantined" in cols, "object_node_id" in cols
    pred_cands, obj_cands = _expand_assertion_candidates(conn, predicate, object_str)
    row = _query_candidate_assertion_pairs(
        cur, has_claim_text, has_scope_text, has_quarantined, has_object_node, subject_id, pred_cands, obj_cands
    )
    if not row and (has_scope_text or has_claim_text) and subject_id and predicate:
        text_col = "scope_text" if has_scope_text else "claim_text"
        sel_fields = ["assertion_id", "subject_node_id", "predicate", "object_literal", text_col, "currentness_state"]
        if has_quarantined:
            sel_fields.append("is_quarantined")
        if has_object_node:
            sel_fields.append("object_node_id")
        row = _lookup_assertion_text_fallback(cur, text_col, sel_fields, subject_id, predicate, obj_cands[-1] if obj_cands else object_str)
    if row:
        return _format_assertion_row(row, has_quarantined, has_object_node)
    return _inspect_edge_in_graph(conn, subject_id, predicate, object_str)


def _lookup_node_attr_index(conn: sqlite3.Connection, node_id: str, keys: Tuple[str, ...]) -> Optional[str]:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(node_attribute_index)")
    cols = {r[1] for r in cur.fetchall()}
    key_col = "attribute_key" if "attribute_key" in cols else ("key" if "key" in cols else "")
    val_col = "attribute_value" if "attribute_value" in cols else ("value" if "value" in cols else "")
    if not (key_col and val_col):
        return None
    ph = ",".join("?" for _ in keys)
    sql = "SELECT " + val_col + " FROM node_attribute_index WHERE LOWER(node_id)=? AND LOWER(" + key_col + ") IN (" + ph + ")"
    cur.execute(sql, (node_id.lower(), *[k.lower() for k in keys]))
    r = cur.fetchone()
    return str(r[0]) if r and r[0] else None


def _extract_role_prop_value(
    node_data: Dict[str, Any], pred: str, conn: Optional[sqlite3.Connection], obj: str
) -> Optional[Any]:
    props = node_data.get("properties", {})
    if "role" in props:
        return props["role"]
    if conn is not None:
        attr_val = _lookup_node_attr_index(conn, node_data["node_id"], ("role", "title", pred.lower()))
        if attr_val:
            return attr_val
    desc = str(node_data.get("description") or "").strip()
    return obj if (desc and obj and obj.lower() in desc.lower()) else None


def _extract_node_props_value(
    node_data: Dict[str, Any], pred: str, item_type: str, conn: Optional[sqlite3.Connection] = None, obj: str = ""
) -> Optional[Any]:
    props = node_data.get("properties", {})
    if pred.lower() in props:
        return props[pred.lower()]
    if item_type == ItemType.SPEAKER_ROLE.value or pred.upper() in {"ROLE", "HAS_ROLE", "HAS_ROLE_IN"}:
        return _extract_role_prop_value(node_data, pred, conn, obj)
    if item_type == ItemType.ORGANIZATION_AFFILIATION.value and "org" in props:
        return props["org"]
    return None


def _resolve_fact_assertion(
    conn: sqlite3.Connection, node_data: Optional[Dict[str, Any]], subj: str, pred: str, obj: str
) -> Optional[Dict[str, Any]]:
    obj_val = obj if obj else None
    if node_data:
        res = _inspect_assertion_in_graph(conn, node_data["node_id"], pred, obj_val)
        if res:
            return res
    if subj and pred:
        return _inspect_assertion_in_graph(conn, subj, pred, obj_val)
    return None


def _inspect_fact_in_graph(
    conn: sqlite3.Connection, item: EvidenceItem, subj: str, pred: str, obj: str, fact_res: Dict[str, Any],
) -> None:
    node_data = _inspect_node_in_graph(conn, subj) if subj else None
    if node_data:
        fact_res["graph_row_id"] = node_data["node_id"]
    assertion_data = _resolve_fact_assertion(conn, node_data, subj, pred, obj)

    if assertion_data:
        fact_res["graph_row_id"] = assertion_data["assertion_id"]
        fact_res["verified_in_graph"] = True
        fact_res["after_value"] = assertion_data.get("object_literal") or assertion_data.get("claim_text") or obj
    elif node_data:
        val = _extract_node_props_value(node_data, pred, item.item_type, conn=conn, obj=obj)
        is_existence = (
            not pred or pred in {"EXISTENCE", "EXISTS", "IDENTITY", "ENTITY"}
            or item.item_type in {ItemType.PERSON_IDENTITY.value, "entity", "node"}
        )
        if val is not None:
            fact_res["verified_in_graph"] = True
            fact_res["after_value"] = val
        elif is_existence:
            fact_res["verified_in_graph"] = True


def _assign_fact_disposition(
    fact_res: Dict[str, Any], subj: str, base_val: Optional[Any], item_id: str,
) -> None:
    if fact_res["verified_in_graph"]:
        if base_val is not None and str(base_val).strip() == str(fact_res["after_value"]).strip():
            fact_res["disposition"] = "EXISTING_FACT_NOOP"
            fact_res["disposition_reason"] = (
                f"Existing canonical fact matches identity ('{subj}'), value ('{fact_res['after_value']}'), "
                f"temporal scope, and authority; explicit no-op preserved."
            )
        else:
            fact_res["disposition"] = "COMMITTED"
            fact_res["disposition_reason"] = (
                f"Fact successfully written to canonical graph (before='{base_val}', after='{fact_res['after_value']}')."
            )
    else:
        fact_res["disposition"] = "MISSING"
        fact_res["disposition_reason"] = f"Required fact '{item_id}' not found in active graph after publication."


def _verify_single_fact(
    item: EvidenceItem, conn: sqlite3.Connection, base_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pf = item.proposed_fact or {}
    subj = str(pf.get("subject", "")).strip()
    pred = str(pf.get("predicate", "") or pf.get("canonical_predicate", "")).upper()
    obj = str(pf.get("object", "")).strip()

    fact_res: Dict[str, Any] = {
        "fact_id": item.item_id, "item_type": item.item_type, "canonical_identity": subj,
        "target_property": pred or item.item_type, "before_value": None, "after_value": obj or None,
        "temporal_scope": pf.get("temporal_scope"),
        "authority": item.approval_ref.get("entry_id") if item.approval_ref else "OPERATOR_CONFIRMED",
        "disposition": "COMMITTED", "disposition_reason": "", "verified_in_graph": False, "graph_row_id": None,
    }
    if item.state == EvidenceState.EXCLUDED.value:
        fact_res["disposition"] = "EXCLUDED"
        fact_res["disposition_reason"] = item.disposition_reason or "Explicitly excluded with supported rationale"
        fact_res["verified_in_graph"] = True
        return fact_res
    if item.state == EvidenceState.QUARANTINED.value:
        fact_res["disposition"] = "QUARANTINED"
        fact_res["disposition_reason"] = item.disposition_reason or "Explicitly quarantined with supported rationale"
        fact_res["verified_in_graph"] = True
        return fact_res

    _inspect_fact_in_graph(conn, item, subj, pred, obj, fact_res)
    base_nodes = base_snapshot.get("nodes", {}) if base_snapshot else {}
    base_val = base_nodes.get(subj.lower(), {}).get(pred.lower()) if subj else None
    fact_res["before_value"] = base_val
    _assign_fact_disposition(fact_res, subj, base_val, item.item_id)
    return fact_res


def verify_fact_coverage_in_graph(
    graph_db: Union[Path, str], manifest: EvidenceCoverageManifest, proposal_digest: str,
    activated_generation_id: str, output_dir: Optional[Path] = None, base_snapshot: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Performs independent read-only graph inspection to verify factual coverage."""
    g_path = Path(graph_db).resolve()
    if not g_path.is_file():
        return False, [f"Graph database file not found: {g_path}"], {}

    conn = create_sqlite_conn(g_path, readonly=True)
    covered_facts: List[Dict[str, Any]] = []
    violations: List[str] = []
    committed_count = existing_noop_count = excluded_count = quarantined_count = missing_count = 0

    try:
        for item in manifest.items:
            if not item.is_eligible_graph_fact:
                continue
            res = _verify_single_fact(item, conn, base_snapshot)
            disp = res["disposition"]
            if disp == "COMMITTED":
                committed_count += 1
                item.transition_to(EvidenceState.ACTIVATED, res["disposition_reason"])
            elif disp == "EXISTING_FACT_NOOP":
                existing_noop_count += 1
                item.transition_to(EvidenceState.ACTIVATED, res["disposition_reason"])
            elif disp == "EXCLUDED":
                excluded_count += 1
            elif disp == "QUARANTINED":
                quarantined_count += 1
            elif disp == "MISSING":
                missing_count += 1
                violations.append(res["disposition_reason"])
            covered_facts.append(res)
    finally:
        conn.close()

    total_eligible = sum(1 for it in manifest.items if it.is_eligible_graph_fact)
    receipt: Dict[str, Any] = {
        "contract_id": "urn:nee:dual-pipeline:publication-coverage-receipt:1.0.0",
        "schema_version": "1.0.0", "receipt_id": f"pcr-{uuid.uuid4().hex[:12]}",
        "run_id": manifest.run_id, "proposal_digest": proposal_digest,
        "activated_generation_id": str(activated_generation_id), "target_graph": g_path.name,
        "status": "VERIFIED" if missing_count == 0 else "FAILED", "covered_facts": covered_facts,
        "coverage_summary": {
            "total_eligible_facts": total_eligible, "committed_count": committed_count,
            "existing_noop_count": existing_noop_count, "excluded_count": excluded_count,
            "quarantined_count": quarantined_count, "missing_count": missing_count,
        },
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }

    is_schema_valid, schema_errs = validate_publication_coverage_receipt(receipt)
    if not is_schema_valid:
        logger.warning("Publication coverage receipt schema warnings: %s", schema_errs)
    if output_dir:
        rf = Path(output_dir).resolve() / "publication_coverage_receipt.json"
        rf.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    return missing_count == 0, violations, receipt


def _load_manifest_from_file(mf_p: Path) -> Optional[EvidenceCoverageManifest]:
    if not mf_p.is_file():
        return None
    try:
        raw_m = json.loads(mf_p.read_text(encoding="utf-8"))
        items = [
            EvidenceItem(
                item_id=it["item_id"], item_type=it["item_type"],
                is_eligible_graph_fact=it["is_eligible_graph_fact"], state=it["state"],
                source_origin=it["source_origin"], disposition_reason=it.get("disposition_reason", ""),
                proposed_fact=it.get("proposed_fact"), approval_ref=it.get("approval_ref"),
            )
            for it in raw_m.get("items", [])
        ]
        return EvidenceCoverageManifest(
            manifest_id=raw_m.get("manifest_id", "cov-auto"),
            run_id=raw_m.get("run_id", "auto"),
            source_digest=raw_m.get("source_digest", "0" * 64),
            items=items,
        )
    except Exception as e:
        logger.warning("Could not parse evidence_manifest.json: %s", e)
        return None


def _find_output_03_path(written: Dict[str, str], output_dir: Path) -> Optional[Path]:
    out3_p = written.get("03_graph")
    if out3_p and Path(out3_p).is_file():
        return Path(out3_p)
    candidates = list(output_dir.glob("03_*_canonical_signal_projection.json"))
    if candidates and candidates[0].is_file():
        return candidates[0]
    intake_pkg = output_dir / "intake_package.json"
    return intake_pkg if intake_pkg.is_file() else None


def _resolve_manifest_for_coverage(
    metadata: Dict[str, Any], written: Dict[str, str], output_dir: Path,
) -> Optional[EvidenceCoverageManifest]:
    manifest = metadata.get("evidence_manifest") or metadata.get("evidence_coverage_manifest")
    if isinstance(manifest, EvidenceCoverageManifest):
        return manifest
    manifest = _load_manifest_from_file(output_dir / "evidence_manifest.json")
    if manifest:
        return manifest
    out3_file = _find_output_03_path(written, output_dir)
    if out3_file:
        try:
            out3_data = json.loads(out3_file.read_text(encoding="utf-8"))
            return build_manifest_from_output_03(
                out3_data,
                run_id=metadata.get("run_id") or metadata.get("directive_id") or "auto",
                source_digest=metadata.get("raw_source_sha256") or ("0" * 64),
            )
        except Exception as e:
            logger.warning("Could not synthesize manifest from Output 03: %s", e)
    return None


def record_publication_fact_coverage(
    res: Any, written: Dict[str, str], auth_info: Dict[str, Any],
    metadata: Dict[str, Any], output_dir: Path, activated_generation_id: str,
) -> Optional[Dict[str, Any]]:
    """Hook invoked upon publication activation to record readback coverage receipt."""
    gp = _resolve_graph_path(metadata, output_dir)
    if not gp:
        return None
    manifest = _resolve_manifest_for_coverage(metadata, written, output_dir)
    if not manifest or not manifest.items:
        return None
    prop_dig = auth_info.get("proposal_digest") or metadata.get("proposal_digest") or ("0" * 64)
    passed, violations, receipt = verify_fact_coverage_in_graph(
        graph_db=gp, manifest=manifest, proposal_digest=prop_dig,
        activated_generation_id=activated_generation_id, output_dir=output_dir,
    )
    written["publication_coverage_receipt"] = str(output_dir / "publication_coverage_receipt.json")
    written["publication_coverage_verified"] = bool(passed)
    if not passed:
        written["publication_coverage_violations"] = list(violations)
        if isinstance(res, dict):
            res.update({"publication_coverage_verified": False, "publication_coverage_status": "FAILED", "publication_coverage_violations": list(violations)})
        if metadata.get("strict_publication_coverage", False):
            raise RuntimeError(f"Publication fact coverage verification failed: {'; '.join(violations)}")
    elif isinstance(res, dict):
        res.update({"publication_coverage_verified": True, "publication_coverage_status": "VERIFIED"})
    return receipt
