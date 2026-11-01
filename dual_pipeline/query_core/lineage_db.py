"""dual_pipeline/query_core/lineage_db.py

Database metadata resolution and badge calibration for lineage footers.
Queries assertion, node, evidence_item, and source tables in SQLite snapshots.
"""
from __future__ import annotations

import dataclasses
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from storage.connection import get_db_connection

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class CitationItem:
    index: int
    raw_id: str
    display_label: str
    graph_path: str
    target_uri: str
    confidence: str = "HIGH (90%)"


def humanize_token(tok: str) -> str:
    """Humanizes system tokens like asrt_edge_*, ev_legacy_edge_*, or node_*."""
    cleaned = re.sub(r"^(?:asrt_edge_|asrt_claim_|asrt_|ev_legacy_edge_|ev_|node_|edge_)", "", tok)
    return " ".join(w.capitalize() for w in cleaned.replace("-", "_").split("_") if w)


def humanize_predicate(predicate: str) -> str:
    """Formats predicates into readable relationships (e.g. ALLOCATES_ENGINEERING_CAPACITY_TO)."""
    p_up = predicate.strip().upper()
    mapped = {"ALLOCATES_ENGINEERING_CAPACITY_TO": "Allocates Engineering Capacity To",
              "HAS_CLAIM_TEXT": "Verified Claim", "HAS_CLAIM": "Verified Claim"}
    return mapped.get(p_up) or " ".join(w.capitalize() for w in predicate.replace("-", "_").split("_") if w)


def _is_badge_superseded(cur_state: Optional[str], val_state: Optional[str]) -> bool:
    return bool((cur_state and str(cur_state).lower() not in ("current", "")) or (val_state and str(val_state).lower() == "superseded"))


def _is_badge_candidate(can_status: Optional[str], can_elig: Optional[int]) -> bool:
    return bool((can_status and str(can_status).lower() != "canonical") or (can_elig is not None and not bool(can_elig)))


def _compute_badge_pct(score: Optional[float], tier: str) -> int:
    if isinstance(score, (int, float)):
        val = float(score)
        return max(1, min(100, int(round(val * 100)) if val <= 1.0 else int(round(val))))
    return {"CONFIRMED": 100, "HIGH": 90, "MEDIUM": 70}.get(tier, 45)


def _resolve_tier(label: Optional[str], score: Optional[float]) -> str:
    tier = (label or "").strip().upper()
    if tier in ("CONFIRMED", "HIGH", "MEDIUM", "LOW"):
        return tier
    if isinstance(score, (int, float)):
        pct = int(round(float(score) * 100)) if float(score) <= 1.0 else int(round(float(score)))
        return "HIGH" if pct >= 85 else ("MEDIUM" if pct >= 50 else "LOW")
    return "HIGH"


def format_confidence_badge(
    label: Optional[str],
    score: Optional[float],
    *,
    canonical_status: Optional[str] = None,
    canonical_eligible: Optional[int] = None,
    validation_state: Optional[str] = None,
    currentness_state: Optional[str] = None,
) -> str:
    """Formats a raw confidence label, score, and canonical lifecycle into a calibrated badge."""
    if _is_badge_superseded(currentness_state, validation_state):
        return "SUPERSEDED (0%)"
    if _is_badge_candidate(canonical_status, canonical_eligible):
        return "CANDIDATE (45%)"
    tier = _resolve_tier(label, score)
    return f"{tier} ({_compute_badge_pct(score, tier)}%)"


def _build_graph_path(sub_name: str, pred: str, obj_name: str, obj_lit: Any, scope_txt: Any) -> str:
    pred_label = humanize_predicate(str(pred or ""))
    if obj_name and str(pred or "").upper() != "HAS_CLAIM_TEXT":
        return f"`{sub_name}` $\\rightarrow$ `{pred_label}` $\\rightarrow$ `{obj_name}`"
    claim_summary = str(obj_lit or scope_txt or "Verified Statement").strip()
    if len(claim_summary) > 60:
        claim_summary = claim_summary[:57].rstrip() + "..."
    return f"`{sub_name}` $\\rightarrow$ `{pred_label}` $\\rightarrow$ `{claim_summary}`"


def _resolve_source_link(
    src_name: str, src_id: str, a_id: str, cold_ref: str, default_uri: str
) -> Tuple[str, str]:
    s_label = src_name or (humanize_token(src_id) if src_id else a_id[:20])
    s_uri = default_uri
    if cold_ref and not cold_ref.startswith("http"):
        s_uri = cold_ref if cold_ref.startswith("file://") else default_uri
    return s_label, s_uri


def _process_assertion_row(row: Any, default_uri: str) -> Tuple[str, str, str, str, Tuple[str, str]]:
    a_id = str(row[0])
    badge = format_confidence_badge(
        row[1], row[2], canonical_status=row[3], canonical_eligible=row[4],
        validation_state=row[5], currentness_state=row[6],
    )
    g_path = _build_graph_path(str(row[10] or humanize_token(a_id)), str(row[7] or ""), str(row[11] or ""), row[8], row[9])
    source_pair = _resolve_source_link(str(row[12] or ""), str(row[13] or ""), a_id, str(row[14] or ""), default_uri)
    return a_id, a_id, badge, g_path, source_pair


def _col(cols: Set[str], name: str) -> str:
    return f"a.{name}" if name in cols else "NULL"


def _build_assertion_sql(cols: Set[str], tables: Set[str], where_clause: str) -> str:
    has_node, has_src = "node" in tables, "source" in tables
    sub_join = "LEFT JOIN node sn ON sn.node_id = a.subject_node_id" if has_node else ""
    obj_join = "LEFT JOIN node onode ON onode.node_id = a.object_node_id" if has_node else ""
    src_join = "LEFT JOIN source s ON s.source_id = a.primary_source_id" if has_src else ""
    sub_col = "sn.canonical_name AS subject_name" if has_node else "NULL AS subject_name"
    obj_col = "onode.canonical_name AS object_name" if has_node else "NULL AS object_name"
    src_cols = "s.source_name, s.source_id, s.cold_archive_ref" if has_src else "NULL, NULL, NULL"
    c_stat, c_elig = _col(cols, 'canonical_status'), _col(cols, 'canonical_eligible')
    val_st, cur_st = _col(cols, 'validation_state'), _col(cols, 'currentness_state')
    pred, lit, scp = _col(cols, 'predicate'), _col(cols, 'object_literal'), _col(cols, 'scope_text')
    return f"""SELECT a.assertion_id, a.confidence_label, a.confidence_score, {c_stat}, {c_elig},
                      {val_st}, {cur_st}, {pred}, {lit}, {scp}, {sub_col}, {obj_col}, {src_cols}
               FROM assertion a {sub_join} {obj_join} {src_join} WHERE {where_clause}"""


def _populate_assertions(
    cur: Any, expanded: List[str], default_uri: str,
    confidences: Dict[str, str], graph_paths: Dict[str, str], sources: Dict[str, Tuple[str, str]],
) -> None:
    try:
        cur.execute("PRAGMA table_info(assertion)")
        cols = {r[1] for r in cur.fetchall()}
        if not cols:
            return
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cur.fetchall()}
    except Exception:
        return

    ph = ",".join("?" for _ in expanded)
    where_parts = [f"a.assertion_id IN ({ph})"]
    params: List[Any] = list(expanded)
    if "legacy_claim_id" in cols:
        where_parts.append(f"a.legacy_claim_id IN ({ph})")
        params.extend(expanded)

    asrt_sql = _build_assertion_sql(cols, tables, " OR ".join(where_parts))
    try:
        cur.execute(asrt_sql, params)
        for row in cur.fetchall():
            a_id, leg_id, badge, g_path, src_pair = _process_assertion_row(row, default_uri)
            for key in {a_id, leg_id}:
                if key:
                    confidences[key] = badge
                    graph_paths[key] = g_path
                    sources[key] = src_pair
    except Exception:
        pass


def _node_name_col(cols: Set[str]) -> str:
    if "canonical_name" in cols:
        return "canonical_name"
    return "name" if "name" in cols else "node_id"


def _populate_nodes(
    cur: Any, expanded: List[str], default_uri: str,
    confidences: Dict[str, str], graph_paths: Dict[str, str], sources: Dict[str, Tuple[str, str]],
) -> None:
    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('node', 'canonical_graph_nodes')")
        table_names = [r[0] for r in cur.fetchall()]
        if not table_names:
            return
        ph = ",".join("?" for _ in expanded)
        for tbl in table_names:
            p_sql = f"PRAGMA table_info({tbl})"
            cur.execute(p_sql)
            cols = {r[1] for r in cur.fetchall()}
            name_col = _node_name_col(cols)
            type_col = "node_type" if "node_type" in cols else "NULL"
            n_sql = f"SELECT node_id, {name_col}, {type_col} FROM {tbl} WHERE node_id IN ({ph})"
            cur.execute(n_sql, expanded)
            for row in cur.fetchall():
                n_id = str(row[0])
                n_name = str(row[1] or humanize_token(n_id))
                n_type = str(row[2] or "Entity").capitalize()
                if n_id not in confidences:
                    confidences[n_id] = "CONFIRMED (90%)"
                    graph_paths[n_id] = f"{n_type}: {n_name}"
                    sources[n_id] = (n_name, default_uri)
    except Exception:
        pass


def _populate_legacy_claims(
    cur: Any, expanded: List[str], default_uri: str,
    confidences: Dict[str, str], graph_paths: Dict[str, str], sources: Dict[str, Tuple[str, str]],
) -> None:
    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='claim'")
        if not cur.fetchall():
            return
        cur.execute("PRAGMA table_info(claim)")
        conf_col = "confidence" if "confidence" in {r[1] for r in cur.fetchall()} else "NULL"
        ph = ",".join("?" for _ in expanded)
        c_sql = f"SELECT claim_id, {conf_col} FROM claim WHERE claim_id IN ({ph})"
        cur.execute(c_sql, expanded)
        for row in cur.fetchall():
            c_id = str(row[0])
            if c_id not in confidences:
                score = float(row[1]) if isinstance(row[1], (int, float)) else None
                lbl = str(row[1]) if isinstance(row[1], str) else None
                confidences[c_id] = format_confidence_badge(lbl, score)
                graph_paths[c_id] = f"Claim Lineage ({c_id[:16]}...)"
                sources[c_id] = (humanize_token(c_id), default_uri)
    except Exception:
        pass


def _populate_evidence(
    cur: Any, expanded: List[str], default_uri: str,
    confidences: Dict[str, str], graph_paths: Dict[str, str], sources: Dict[str, Tuple[str, str]],
) -> None:
    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='evidence_item'")
        if not cur.fetchall():
            return
        ph = ",".join("?" for _ in expanded)
        ev_sql = f"""SELECT ei.evidence_id, ae.assertion_id, s.source_name, s.source_id
                     FROM evidence_item ei
                     LEFT JOIN assertion_evidence ae ON ae.evidence_id = ei.evidence_id
                     LEFT JOIN source s ON s.source_id = ei.source_id
                     WHERE ei.evidence_id IN ({ph})"""
        cur.execute(ev_sql, expanded)
        for row in cur.fetchall():
            ev_id, a_id = str(row[0]), str(row[1] or "")
            s_label = str(row[2] or "") or (humanize_token(str(row[3])) if row[3] else ev_id[:20])
            sources[ev_id] = (s_label, default_uri)
            if a_id and a_id in confidences:
                confidences[ev_id] = confidences[a_id]
                graph_paths[ev_id] = graph_paths[a_id]
    except Exception:
        pass


def query_db_lineage_metadata(
    db_file: Path, tokens: List[str]
) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, Tuple[str, str]]]:
    """Queries assertion, node, evidence_item, and source tables for confidence, graph path, and source display."""
    confidences: Dict[str, str] = {}
    graph_paths: Dict[str, str] = {}
    sources: Dict[str, Tuple[str, str]] = {}
    default_uri = f"file://{db_file}"

    conn = get_db_connection(db_file, readonly=True)
    try:
        cur = conn.cursor()
        expanded: List[str] = list(tokens)
        for t in tokens:
            expanded.append(f"asrt_claim_{t}" if t.startswith("claim_") else (t[15:] if t.startswith("ev_legacy_edge_") else t))
        _populate_assertions(cur, expanded, default_uri, confidences, graph_paths, sources)
        _populate_nodes(cur, expanded, default_uri, confidences, graph_paths, sources)
        _populate_legacy_claims(cur, expanded, default_uri, confidences, graph_paths, sources)
        _populate_evidence(cur, expanded, default_uri, confidences, graph_paths, sources)
    finally:
        conn.close()

    return confidences, graph_paths, sources


def _clean_uri(path_str: str) -> Tuple[str, Path]:
    raw = path_str or "storage/graph.sqlite"
    uri = raw if raw.startswith("file://") else f"file://{raw}"
    clean = raw[len("file://"):] if raw.startswith("file://") else raw
    return uri, Path(clean)


def _resolve_active_uri(graph_path: Optional[str]) -> Tuple[str, Path]:
    if graph_path:
        return _clean_uri(graph_path)
    try:
        from dual_pipeline.runtime_root import resolve_active_graph_path
        active_p = resolve_active_graph_path(enforce_integrity=False)
        if active_p and active_p.is_file():
            return _clean_uri(str(active_p))
    except Exception:
        pass
    return _clean_uri("storage/graph.sqlite")


def resolve_graph_metadata(
    tokens: List[str], graph_path: Optional[str] = None
) -> Tuple[str, Dict[str, str], Dict[str, str], Dict[str, Tuple[str, str]]]:
    """Resolves active graph DB URI, calibrated confidences, paths, and sources for tokens."""
    uri, db_file = _resolve_active_uri(graph_path)
    conf: Dict[str, str] = {}
    paths: Dict[str, str] = {}
    srcs: Dict[str, Tuple[str, str]] = {}
    if db_file.is_file():
        try:
            conf, paths, srcs = query_db_lineage_metadata(db_file, tokens)
        except Exception:
            pass
    for tok in tokens:
        conf.setdefault(tok, "UNVERIFIED (0%)")
        paths.setdefault(tok, f"Entity: {humanize_token(tok)}")
        is_a = tok.startswith(("ast_", "asrt_", "assertion-", "claim_", "ev_"))
        srcs.setdefault(tok, ("Knowledge Graph Store" if is_a else humanize_token(tok), uri))
    return uri, conf, paths, srcs


def _aggregate_conf(conf_list: List[str]) -> str:
    if any("SUPERSEDED" in c for c in conf_list):
        return "SUPERSEDED (0%)"
    if conf_list and all("CANDIDATE" in c for c in conf_list):
        return "CANDIDATE (45%)"
    pcts = [int(m.group(1)) for c in conf_list if (m := re.search(r"\((\d+)%\)", c))]
    avg_p = int(round(sum(pcts) / len(pcts))) if pcts else 90
    tier = "CONFIRMED" if avg_p >= 98 else ("HIGH" if avg_p >= 85 else ("MEDIUM" if avg_p >= 50 else "LOW"))
    return f"{tier} ({avg_p}%)"


def _extract_assertion_entities(g_path: str) -> List[str]:
    ignored = {"Verified Claim", "Verified Statement", "Directs", "Allocates Engineering Capacity To"}
    return [e for e in re.findall(r"`([^`]+)`", g_path) if e not in ignored]


def _group_source_data(
    tokens: List[str], srcs: Dict[str, Tuple[str, str]], confs: Dict[str, str], paths: Dict[str, str], uri: str
) -> Tuple[Dict[Tuple[str, str], Dict[str, Any]], Set[str], List[str]]:
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    all_ents: Set[str] = set()
    asrts = [t for t in tokens if t.startswith(("ast_", "asrt_", "assertion-", "claim_", "ev_"))]
    nodes = [humanize_token(t) for t in tokens if t not in asrts]
    all_ents.update(nodes)
    for tok in (asrts or tokens):
        s_lbl, s_uri = srcs.get(tok, ("Knowledge Graph Store" if tok in asrts else humanize_token(tok), uri))
        g = groups.setdefault((s_lbl, s_uri), {"asrts": [], "ents": set(), "confs": []})
        if tok in asrts:
            g["asrts"].append(tok)
            for ent in _extract_assertion_entities(paths.get(tok, "")):
                g["ents"].add(ent)
                all_ents.add(ent)
        else:
            g["ents"].add(humanize_token(tok))
        g["confs"].append(confs.get(tok, "UNVERIFIED (0%)"))
    if asrts and groups:
        for g in groups.values():
            g["ents"].update(nodes)
    return groups, all_ents, asrts


def summarize_graph_traversal_coverage(
    tokens: List[str], graph_path: Optional[str] = None
) -> Tuple[str, List[CitationItem], Dict[str, int]]:
    """Builds the compact Graph Traversal Summary and deduplicated source coverage table."""
    if not tokens:
        return "", [], {}
    uri, confs, paths, srcs = resolve_graph_metadata(tokens, graph_path)
    items = [
        CitationItem(i, t, srcs.get(t, (humanize_token(t), uri))[0],
                     paths.get(t, humanize_token(t)), srcs.get(t, (humanize_token(t), uri))[1],
                     confs.get(t, "UNVERIFIED (0%)"))
        for i, t in enumerate(tokens, start=1)
    ]
    index_map = {t: i for i, t in enumerate(tokens, start=1)}
    groups, all_ents, asrts = _group_source_data(tokens, srcs, confs, paths, uri)

    tot_asrt, tot_ent, tot_src = len(asrts), len(all_ents) or len(tokens), len(groups)
    overall_conf = _aggregate_conf([confs.get(t, "UNVERIFIED (0%)") for t in asrts or tokens])
    callout = f"**Graph Traversal Summary:** {tot_asrt} assertions evaluated across {tot_ent} entities and {tot_src} distinct sources ({overall_conf} overall confidence)."

    table = [
        "---", "", "### Evidence & Graph Lineage", "", callout, "",
        "| Source / Domain | Entities & Workstreams Traversed | Assertions | Confidence |",
        "| :--- | :--- | :--- | :--- |",
    ]
    for (s_name, s_url), data in groups.items():
        ents_fmt = ", ".join(sorted(data["ents"])[:5]) or humanize_token(s_name)
        cnt = len(data["asrts"])
        asrt_lbl = f"{cnt} verified assertion{'s' if cnt != 1 else ''}" if cnt > 0 else "0 assertions"
        table.append(f"| [{s_name}]({s_url}) | {ents_fmt} | {asrt_lbl} | {_aggregate_conf(data['confs'])} |")
    table.append(f"<!-- graph_lineage_tokens: {','.join(tokens)} -->")
    return "\n".join(table), items, index_map

