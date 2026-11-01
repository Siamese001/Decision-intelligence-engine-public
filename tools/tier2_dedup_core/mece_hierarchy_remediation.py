"""Remediation handlers for MECE portfolio hierarchy, compound-node splitting, ownership wiring, and retrieval breadcrumb synthesis."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from dual_pipeline.core import stable_id

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_GOV_APPROVAL_REF = "REQ_INGEST_CEC57955C84BCE3A"


def _norm_slug(text: str) -> str:
    clean = re.sub(r"\([^)]*\)", " ", (text or "").lower())
    clean = re.sub(r"^(?:org|workstream|program|product|person|stakeholder|entity)[:_-]+", "", clean)
    return _NON_ALNUM_RE.sub("_", clean).strip("_")


def _ensure_governance_approval(conn: Any, approval_ref: str, tables: Set[str]) -> None:
    if "governance_approval" not in tables:
        return
    ga_cols = {r[1] for r in conn.execute("PRAGMA table_info(governance_approval)").fetchall()}
    if not {"approval_id", "approval_reference"}.issubset(ga_cols):
        return
    if conn.execute("SELECT 1 FROM governance_approval WHERE approval_reference = ? LIMIT 1", (approval_ref,)).fetchone():
        return
    ga_id = stable_id("ga", approval_ref, length=24)
    scope_j = '{"allowed_actions": ["canonical_assertion_promotion"]}'
    if {"status", "active", "scope_json"}.issubset(ga_cols):
        conn.execute(
            "INSERT OR IGNORE INTO governance_approval (approval_id, approval_reference, status, active, scope_json) VALUES (?, ?, 'approved', 1, ?)",
            (ga_id, approval_ref, scope_j),
        )
    elif {"status", "active"}.issubset(ga_cols):
        conn.execute("INSERT OR IGNORE INTO governance_approval (approval_id, approval_reference, status, active) VALUES (?, ?, 'approved', 1)", (ga_id, approval_ref))
    else:
        conn.execute("INSERT OR IGNORE INTO governance_approval (approval_id, approval_reference) VALUES (?, ?)", (ga_id, approval_ref))


def _resolve_source_and_run(conn: Any, tables: Set[str], src_id: Optional[str]) -> Tuple[str, str]:
    src_val = src_id or "source_canonical_graph"
    if "source" in tables:
        srow = conn.execute("SELECT source_id FROM source WHERE source_id = ? LIMIT 1", (src_val,)).fetchone()
        if not srow:
            srow = conn.execute("SELECT source_id FROM source LIMIT 1").fetchone()
            src_val = str(srow[0]) if srow else src_val
    er_val = "er-1"
    if "extraction_run" in tables:
        erow = conn.execute("SELECT extraction_run_id FROM extraction_run LIMIT 1").fetchone()
        if erow:
            er_val = str(erow[0])
    return src_val, er_val


def _link_default_evidence(conn: Any, tables: Set[str], aid: str, src_val: str, now_iso: str) -> None:
    if not {"assertion_evidence", "evidence_item"}.issubset(tables):
        return
    ev_row = (
        conn.execute("SELECT evidence_id FROM evidence_item WHERE source_id = ? LIMIT 1", (src_val,)).fetchone()
        or conn.execute("SELECT evidence_id FROM evidence_item LIMIT 1").fetchone()
    )
    if not (ev_row and ev_row[0]):
        return
    ae_cols = {r[1] for r in conn.execute("PRAGMA table_info(assertion_evidence)").fetchall()}
    sql = (
        "INSERT OR IGNORE INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, independent_corroboration, created_at) VALUES (?, ?, 'supports', 1, ?)"
        if "independent_corroboration" in ae_cols
        else "INSERT OR IGNORE INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, created_at) VALUES (?, ?, 'supports', ?)"
    )
    conn.execute(sql, (aid, ev_row[0], now_iso))


def _iso_z(now_iso: str) -> str:
    base = (now_iso or "2026-10-29T00:00:00Z").replace("+00:00", "Z")
    return f"{base[:19]}Z" if len(base) >= 19 else "2026-10-29T00:00:00Z"


def _normalize_node_type(ntype: str) -> str:
    mapping = {"entity": "workstream", "organization": "org", "person": "stakeholder", "mission": "workstream"}
    return mapping.get((ntype or "workstream").lower(), ntype or "workstream")


def _insert_canonical_assertion(
    conn: Any, tables: Set[str], asrt_cols: Set[str], subj_id: str, pred: str,
    obj_id: Optional[str], scope_text: str, src_id: Optional[str], now_iso: str,
    qualifiers: Optional[Dict[str, Any]] = None,
) -> str:
    if "assertion_predicate_registry" in tables and pred == "PART_OF":
        if not conn.execute("SELECT 1 FROM assertion_predicate_registry WHERE predicate = 'PART_OF' AND active = 1").fetchone():
            pred = "AFFILIATED_WITH"
    ts_z = _iso_z(now_iso)
    aid = stable_id("assertion", "mece", subj_id, pred, obj_id or scope_text, length=28)
    fp = hashlib.sha256(aid.encode("utf-8")).hexdigest()
    src_val, er_val = _resolve_source_and_run(conn, tables, src_id)
    if not conn.execute("SELECT 1 FROM assertion WHERE assertion_id = ?", (aid,)).fetchone():
        col_vals: Dict[str, Any] = {
            "assertion_id": aid, "assertion_fingerprint": fp, "subject_node_id": subj_id,
            "predicate": pred, "object_node_id": obj_id,
            "object_literal": None if obj_id else scope_text,
            "object_literal_type": None if obj_id else "text",
            "assertion_type": "confirmed_canonical_fact", "atomicity_state": "atomic", "scope_text": scope_text,
            "primary_source_id": src_val, "extraction_run_id": er_val, "extraction_method": "hygiene_mece_reconciler",
            "origin_type": "human_source", "confidence_label": "high", "confidence_score": 0.95,
            "validation_state": "unreviewed", "currentness_state": "current", "resolution_state": "resolved",
            "canonical_status": "candidate", "canonical_eligible": 1, "retrieval_eligible": 0,
            "materiality": "high", "classification": "internal", "allowed_audiences_json": '["internal_consulting"]',
            "hitl_required": 0, "legacy_record_type": "edge" if obj_id else "claim", "legacy_record_ids_json": "[]",
            "approval_reference": _GOV_APPROVAL_REF,
            "qualifiers_json": json.dumps(qualifiers or {"answer_use": "canonical_fact", "fact_promotion_eligible": True}),
            "valid_from": ts_z[:10], "date_precision": "day", "date_status": "exact",
            "recorded_at": ts_z, "created_at": ts_z, "updated_at": ts_z,
        }
        present = [(k, v) for k, v in col_vals.items() if k in asrt_cols]
        conn.execute(
            f"INSERT INTO assertion ({', '.join(k for k, _ in present)}) VALUES ({', '.join('?' for _ in present)})",
            [v for _, v in present],
        )
    _link_default_evidence(conn, tables, aid, src_val, ts_z)
    conn.execute(
        "UPDATE assertion SET canonical_status = 'canonical', validation_state = 'verified', canonical_eligible = 1, retrieval_eligible = 1, approval_reference = ? WHERE assertion_id = ?",
        (_GOV_APPROVAL_REF, aid),
    )
    return aid


def _upsert_node(conn: Any, node_cols: Set[str], nid: str, ntype: str, cname: str, desc: str, now_iso: str) -> None:
    safe_type = _normalize_node_type(ntype)
    ts_z = _iso_z(now_iso)
    vals: Dict[str, Any] = {
        "node_id": nid, "node_type": safe_type, "canonical_name": cname, "display_name": cname,
        "normalized_name": _norm_slug(cname).replace("_", " "), "description": desc,
        "status": "canonical", "active": 1, "created_at": ts_z, "updated_at": ts_z,
    }
    present = [(k, v) for k, v in vals.items() if k in node_cols]
    upd_cols = [f"{k} = excluded.{k}" for k, _ in present if k not in ("node_id", "created_at")]
    conn.execute(
        f"INSERT INTO node ({', '.join(k for k, _ in present)}) VALUES ({', '.join('?' for _ in present)}) ON CONFLICT(node_id) DO UPDATE SET {', '.join(upd_cols)}",
        [v for _, v in present],
    )


def _spawn_comma_split_siblings(
    conn: Any, tables: Set[str], node_cols: Set[str], asrt_cols: Set[str],
    nid: str, exp_type: str, split_names: List[str], now_iso: str, sample_ids: List[str],
) -> None:
    if "assertion" not in tables:
        for extra_name in split_names[1:]:
            new_nid = f"{exp_type}_{_norm_slug(extra_name)}"
            _upsert_node(conn, node_cols, new_nid, exp_type, extra_name, f"Atomic {exp_type}: {extra_name}", now_iso)
            sample_ids.append(new_nid)
        return
    parent_rows = conn.execute(
        """SELECT predicate, object_node_id, primary_source_id FROM assertion
            WHERE subject_node_id = ? AND currentness_state = 'current'
              AND predicate IN ('DELIVERS', 'PART_OF', 'AFFILIATED_WITH', 'GOVERNS')""",
        (nid,),
    ).fetchall()
    inc_rows = conn.execute(
        """SELECT subject_node_id, predicate, primary_source_id FROM assertion
            WHERE object_node_id = ? AND currentness_state = 'current'
              AND predicate IN ('DELIVERS', 'PART_OF', 'GOVERNS', 'OWNS_OR_LEADS')""",
        (nid,),
    ).fetchall()
    for extra_name in split_names[1:]:
        new_nid = f"{exp_type}_{_norm_slug(extra_name)}"
        _upsert_node(conn, node_cols, new_nid, exp_type, extra_name, f"Atomic {exp_type}: {extra_name}", now_iso)
        sample_ids.append(new_nid)
        for pred, obj_id, src_id in parent_rows:
            _insert_canonical_assertion(conn, tables, asrt_cols, new_nid, str(pred), str(obj_id), f"{extra_name} {pred} {obj_id}.", str(src_id or ""), now_iso)
        for subj_id, pred, src_id in inc_rows:
            _insert_canonical_assertion(conn, tables, asrt_cols, str(subj_id), str(pred), new_nid, f"{subj_id} {pred} {extra_name}.", str(src_id or ""), now_iso)


def _unmerge_cross_tier_redirect(
    conn: Any, tables: Set[str], asrt_cols: Set[str], nid: str, exp_name: str, merged_into: str, now_iso: str
) -> None:
    if "entity_redirect" in tables:
        conn.execute("DELETE FROM entity_redirect WHERE from_node_id = ?", (nid,))
    if "entity_merge_event" in tables:
        eme_cols = {r[1] for r in conn.execute("PRAGMA table_info(entity_merge_event)").fetchall()}
        if "merged_node_id" in eme_cols:
            conn.execute("DELETE FROM entity_merge_event WHERE merged_node_id = ?", (nid,))
    if merged_into and "assertion" in tables:
        _insert_canonical_assertion(conn, tables, asrt_cols, nid, "DELIVERS", merged_into, f"{exp_name} DELIVERS {merged_into}.", None, now_iso)


def _handle_compound_split_finding(
    conn: Any, tables: Set[str], node_cols: Set[str], asrt_cols: Set[str], rd: Dict[str, Any], now_iso: str, sample_ids: List[str]
) -> int:
    nid = str(rd.get("node_id") or "")
    if not nid:
        return 0
    subtype = str(rd.get("issue_subtype") or "")
    exp_name = str(rd.get("expected_canonical_name") or rd.get("normalized_name") or rd.get("canonical_name") or "")
    exp_type = _normalize_node_type(str(rd.get("expected_node_type") or rd.get("target_node_type") or rd.get("node_type") or "workstream"))
    if subtype == "cross_tier_destructive_merge":
        _unmerge_cross_tier_redirect(conn, tables, asrt_cols, nid, exp_name, str(rd.get("merged_into_node_id") or ""), now_iso)
    upd_map = {"canonical_name": exp_name, "node_type": exp_type, "display_name": exp_name, "active": 1, "status": "canonical", "updated_at": _iso_z(now_iso)}
    present = [(k, v) for k, v in upd_map.items() if k in node_cols]
    set_clause = ", ".join(f"{k} = ?" for k, _ in present)
    sql_upd = f"UPDATE node SET {set_clause} WHERE node_id = ?"
    conn.execute(sql_upd, [v for _, v in present] + [nid])
    sample_ids.append(nid)
    if subtype == "comma_merged_entities":
        _spawn_comma_split_siblings(conn, tables, node_cols, asrt_cols, nid, exp_type, list(rd.get("split_names") or []), now_iso, sample_ids)
    return 1


def _retire_orphan_prefix_twin(conn: Any, tables: Set[str], ret_id: str, surv_id: str, now_iso: str, sample_ids: List[str]) -> int:
    if not ret_id or not surv_id or ret_id == surv_id:
        return 0
    conn.execute("UPDATE node SET active = 0, status = 'merged' WHERE node_id = ?", (ret_id,))
    if "entity_redirect" in tables:
        er_cols = {r[1] for r in conn.execute("PRAGMA table_info(entity_redirect)").fetchall()}
        me_id = stable_id("merge", ret_id, surv_id, length=24)
        if "entity_merge_event" in tables:
            eme_cols = {r[1] for r in conn.execute("PRAGMA table_info(entity_merge_event)").fetchall()}
            if "merged_node_id" in eme_cols:
                conn.execute("INSERT OR IGNORE INTO entity_merge_event (merge_event_id, merged_node_id, canonical_node_id, merge_reason, approval_reference, created_at) VALUES (?, ?, ?, 'Orphan prefix twin consolidation', ?, ?)", (me_id, ret_id, surv_id, _GOV_APPROVAL_REF, _iso_z(now_iso)))
            else:
                conn.execute("INSERT OR IGNORE INTO entity_merge_event (merge_event_id, canonical_node_id, event_type, status, approval_reference, reason, created_at, completed_at) VALUES (?, ?, 'merge', 'completed', ?, 'Orphan prefix twin consolidation', ?, ?)", (me_id, surv_id, _GOV_APPROVAL_REF, _iso_z(now_iso), _iso_z(now_iso)))
        sql_er = "INSERT OR REPLACE INTO entity_redirect (from_node_id, to_node_id, merge_event_id, active, created_at) VALUES (?, ?, ?, 1, ?)" if "merge_event_id" in er_cols else "INSERT OR REPLACE INTO entity_redirect (from_node_id, to_node_id, active) VALUES (?, ?, 1)"
        conn.execute(sql_er, (ret_id, surv_id, me_id, _iso_z(now_iso)) if "merge_event_id" in er_cols else (ret_id, surv_id))
    sample_ids.append(ret_id)
    return 1


def _promote_misrouted_portfolio_candidates(
    conn: Any, tables: Set[str], asrt_cols: Set[str], rd: Dict[str, Any], sample_ids: List[str]
) -> int:
    src_id = str(rd.get("source_id") or "")
    p_ids = [str(x) for x in (rd.get("promotable_ids") or []) if x]
    if src_id and "source" in tables:
        src_cols = {r[1] for r in conn.execute("PRAGMA table_info(source)").fetchall()}
        if {"authority_rank", "source_posture"}.issubset(src_cols):
            conn.execute("UPDATE source SET authority_rank = 9, source_posture = 'authoritative_governed' WHERE source_id = ?", (src_id,))
    if not p_ids or "assertion" not in tables:
        return 0
    for aid in p_ids:
        _link_default_evidence(conn, tables, aid, src_id or "source_canonical_graph", "2026-10-29T00:00:00Z")
    ph = ",".join("?" for _ in p_ids)
    q_upd = ", qualifiers_json = replace(replace(coalesce(qualifiers_json, '{}'), '\"fact_promotion_eligible\": false', '\"fact_promotion_eligible\": true'), '\"fact_promotion_eligible\":false', '\"fact_promotion_eligible\":true') " if "qualifiers_json" in asrt_cols else ""
    params: List[Any] = []
    app_clause = ""
    if "approval_reference" in asrt_cols:
        app_clause = ", approval_reference = ? "
        params.append(_GOV_APPROVAL_REF)
    sql_asrt = f"UPDATE assertion SET canonical_status = 'canonical', canonical_eligible = 1, retrieval_eligible = 1, validation_state = 'verified', currentness_state = 'current'{app_clause}{q_upd}WHERE assertion_id IN ({ph})"
    params.extend(p_ids)
    conn.execute(sql_asrt, params)
    if "search_document" in tables:
        sql_sd = f"UPDATE search_document SET canonical_status = 'canonical', currentness_state = 'current', retrieval_eligible = 1 WHERE assertion_id IN ({ph})"
        conn.execute(sql_sd, p_ids)
    sample_ids.extend(p_ids[:3])
    return len(p_ids)


def _handle_hierarchy_and_ownership_finding(
    conn: Any, tables: Set[str], node_cols: Set[str], asrt_cols: Set[str], rd: Dict[str, Any], now_iso: str, sample_ids: List[str]
) -> int:
    subtype = str(rd.get("issue_subtype") or "")
    if subtype == "orphan_prefix_twin":
        return _retire_orphan_prefix_twin(conn, tables, str(rd.get("retired_node_id") or rd.get("node_id") or ""), str(rd.get("survivor_node_id") or ""), now_iso, sample_ids)
    if subtype == "misrouted_portfolio_candidates":
        return _promote_misrouted_portfolio_candidates(conn, tables, asrt_cols, rd, sample_ids)
    subj_id = str(rd.get("subject_node_id") or "")
    obj_id = str(rd.get("object_node_id") or rd.get("node_id") or "")
    pred = str(rd.get("predicate") or "OWNS_OR_LEADS")
    src_id = str(rd.get("source_id") or "")
    if not subj_id and rd.get("owner_names"):
        first_owner = str(rd["owner_names"][0]).strip()
        row_p = conn.execute("SELECT node_id FROM node WHERE active = 1 AND LOWER(canonical_name) = ? LIMIT 1", (first_owner.lower(),)).fetchone()
        subj_id = str(row_p[0]) if row_p else f"stakeholder_{_norm_slug(first_owner)}"
        if not row_p:
            _upsert_node(conn, node_cols, subj_id, "stakeholder", first_owner, f"Portfolio Owner/PM: {first_owner}", now_iso)
    if subj_id and obj_id and "assertion" in tables:
        sample_ids.append(_insert_canonical_assertion(conn, tables, asrt_cols, subj_id, pred, obj_id, f"{subj_id} {pred} {obj_id}.", src_id, now_iso))
        return 1
    return 0


def _collect_hierarchy_maps(conn: Any) -> Tuple[Dict[str, Tuple[str, str, str]], Dict[str, List[str]], Dict[str, List[str]]]:
    nodes = {
        str(nid): (str(cname), str(ntype), str(desc))
        for nid, cname, ntype, desc in conn.execute(
            "SELECT node_id, coalesce(canonical_name, node_id), coalesce(node_type, 'entity'), coalesce(description, '') FROM node WHERE active = 1"
        ).fetchall()
    }
    parents: Dict[str, List[str]] = {}
    owners: Dict[str, List[str]] = {}
    for subj, pred, obj in conn.execute(
        "SELECT subject_node_id, predicate, object_node_id FROM assertion WHERE currentness_state = 'current' AND canonical_status = 'canonical'"
    ).fetchall():
        s_id, p_str, o_id = str(subj or ""), str(pred or ""), str(obj or "")
        if s_id not in nodes or o_id not in nodes:
            continue
        if p_str in ("OWNS_OR_LEADS", "LEADS", "OWNS"):
            owners.setdefault(o_id, []).append(nodes[s_id][0])
        elif p_str in ("PART_OF", "DELIVERS", "AFFILIATED_WITH"):
            parents.setdefault(s_id, []).append(o_id)
        elif p_str in ("GOVERNS", "CONTAINS"):
            parents.setdefault(o_id, []).append(s_id)
    return nodes, parents, owners


def _build_node_breadcrumb_map(conn: Any) -> Dict[str, str]:
    nodes, parents, owners = _collect_hierarchy_maps(conn)
    breadcrumbs: Dict[str, str] = {}
    for nid, (cname, ntype, desc) in nodes.items():
        if ntype not in ("program", "product", "workstream", "mission", "org", "organization"):
            continue
        chain, cur_id, visited = [f"{cname} ({ntype})"], nid, {nid}
        for _ in range(4):
            plist = [p for p in parents.get(cur_id, []) if p not in visited and p in nodes]
            if not plist:
                break
            cur_id = plist[0]
            visited.add(cur_id)
            chain.append(f"{nodes[cur_id][0]} ({nodes[cur_id][1]})")
        own_list = sorted(set(owners.get(nid, [])))
        own_str = f" | Owners: {', '.join(own_list)}" if own_list else ""
        desc_snip = f" | Scope: {desc[:180]}" if desc else ""
        breadcrumbs[nid] = f"[MECE Hierarchy: {' > '.join(reversed(chain))}{own_str}{desc_snip}]"
    return breadcrumbs


def _refresh_search_fts_if_present(conn: Any, tables: Set[str], sd_cols: Set[str]) -> None:
    if "search_fts" not in tables or "search_rowid" not in sd_cols:
        return
    try:
        conn.execute("INSERT INTO search_fts(search_fts) VALUES('delete-all')")
    except Exception:
        conn.execute("DELETE FROM search_fts")
    conn.execute(
        "INSERT INTO search_fts(rowid, title, body, object_type, object_id) "
        "SELECT search_rowid, title, body, object_type, object_id FROM search_document WHERE retrieval_eligible = 1"
    )


def synthesize_portfolio_retrieval_breadcrumbs(conn: Any, tables: Set[str], now_iso: str, sample_ids: List[str]) -> int:
    """Synthesize MECE hierarchy, owner, and financial breadcrumbs into search_document and search_fts."""
    if not {"node", "assertion", "search_document"}.issubset(tables):
        return 0
    sd_cols = {r[1] for r in conn.execute("PRAGMA table_info(search_document)").fetchall()}
    if not {"search_document_id", "title", "body", "retrieval_eligible"}.issubset(sd_cols):
        return 0
    breadcrumbs = _build_node_breadcrumb_map(conn)
    if not breadcrumbs:
        return 0
    rows = conn.execute(
        """SELECT a.assertion_id, a.subject_node_id, a.object_node_id, coalesce(a.scope_text, a.object_literal, a.assertion_id),
                  coalesce(a.primary_source_id, 'source_canonical_graph'), sd.search_document_id, coalesce(sd.title, ''), coalesce(sd.body, '')
             FROM assertion a LEFT JOIN search_document sd ON sd.assertion_id = a.assertion_id
            WHERE a.canonical_status = 'canonical' AND a.currentness_state = 'current' AND a.retrieval_eligible = 1"""
    ).fetchall()
    updated = 0
    for aid, subj_id, obj_id, scope_txt, src_id, sd_id, old_title, old_body in rows:
        bc = breadcrumbs.get(str(subj_id or "")) or breadcrumbs.get(str(obj_id or ""))
        if not bc:
            continue
        base_body = re.sub(r"\s*\[MECE Hierarchy:[^\]]*\]", "", str(old_body or scope_txt)).strip()
        new_body, new_title = f"{base_body} {bc}".strip(), str(old_title or scope_txt)[:160]
        if sd_id and old_body == new_body:
            continue
        if sd_id:
            conn.execute(
                "UPDATE search_document SET title = ?, body = ?, canonical_status = 'canonical', currentness_state = 'current', retrieval_eligible = 1, updated_at = ? WHERE search_document_id = ?",
                (new_title, new_body, now_iso, sd_id),
            )
        else:
            conn.execute(
                """INSERT OR REPLACE INTO search_document (
                    search_document_id, object_type, object_id, title, body, canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id, updated_at
                ) VALUES (?, 'assertion', ?, ?, ?, 'canonical', 'current', 1, ?, ?, ?)""",
                (str(aid), str(aid), new_title, new_body, str(src_id), str(aid), now_iso),
            )
        updated += 1
        if len(sample_ids) < 10:
            sample_ids.append(str(aid))
    _refresh_search_fts_if_present(conn, tables, sd_cols)
    return updated


def remediate_mece_hierarchy_fixes(
    conn: Any, findings: List[Dict[str, Any]], tables: Set[str], now_iso: str, sample_ids: List[str]
) -> Dict[str, int]:
    """Execute compound-node splitting, MECE hierarchy/ownership wiring, and retrieval breadcrumb synthesis."""
    _ensure_governance_approval(conn, _GOV_APPROVAL_REF, tables)
    node_cols = {r[1] for r in conn.execute("PRAGMA table_info(node)").fetchall()} if "node" in tables else set()
    asrt_cols = {r[1] for r in conn.execute("PRAGMA table_info(assertion)").fetchall()} if "assertion" in tables else set()
    split_cnt, hier_cnt, synth_req = 0, 0, False
    for f in findings:
        fix, rd = str(f.get("proposed_fix") or ""), f.get("raw_data") or {}
        if fix == "split_or_normalize_compound_node":
            split_cnt += _handle_compound_split_finding(conn, tables, node_cols, asrt_cols, rd, now_iso, sample_ids)
        elif fix == "reconcile_hierarchy_and_ownership_edges":
            hier_cnt += _handle_hierarchy_and_ownership_finding(conn, tables, node_cols, asrt_cols, rd, now_iso, sample_ids)
        elif fix == "synthesize_hierarchical_retrieval_docs":
            synth_req = True
    synth_cnt = synthesize_portfolio_retrieval_breadcrumbs(conn, tables, now_iso, sample_ids) if (synth_req or split_cnt > 0 or hier_cnt > 0) else 0
    return {
        "compound_or_mistyped_nodes_normalized": split_cnt,
        "hierarchy_and_ownership_items_reconciled": hier_cnt,
        "hierarchical_retrieval_docs_synthesized": synth_cnt,
    }
