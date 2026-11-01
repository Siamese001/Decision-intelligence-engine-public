"""
dual_pipeline/query_core/traversal_helper.py

Dynamic graph traversal and transcript entity spotting helpers for OfflineGraphContextRetriever.
Traverses project/workstream topologies to retrieve all resource names and profiles,
and spots in-dialogue entity mentions against graph stakeholders.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, List, Optional, Sequence, Set, Tuple

from .offline_models import GraphEntityReference

logger = logging.getLogger(__name__)

COMMON_STOP_WORDS = {
    "will", "may", "can", "an", "as", "at", "be", "by", "do", "go",
    "if", "in", "is", "it", "no", "of", "on", "or", "so", "to", "up",
    "us", "we", "he", "me", "my", "just", "well", "see", "get", "got",
    "let", "say", "said", "one", "two", "good", "bad", "now", "then",
    "here", "there", "what", "who", "why", "how", "when", "all", "any",
    "both", "each", "few", "more", "most", "other", "some", "such",
    "than", "too", "very", "mark", "bill", "rob", "art", "bob",
    "grant", "chase", "price", "case", "major", "dean", "lane",
    "gene", "don", "guy", "ray", "roy", "dale", "glen",
}


def _read_pair_fallback(runtime_root: Path) -> Optional[Path]:
    active_pair_path = runtime_root / "active_pair.json"
    if not active_pair_path.is_file():
        return None
    try:
        pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
        rel = pair.get("graph_path")
        if not rel:
            return None
        bases = (Path("."), runtime_root, *runtime_root.parents)
        for base in bases:
            cand = Path(rel) if base == Path(".") else (base / rel)
            if cand.is_file():
                return cand
    except Exception as e:
        logger.debug("Failed reading active_pair.json: %s", e)
    return None


def read_active_pair_graph(runtime_root: Optional[Path]) -> Optional[Path]:
    """Resolves active graph SQLite path from active_pair.json via resolve_active_pair."""
    if not runtime_root:
        return None
    try:
        from dual_pipeline.runtime_root import resolve_active_pair
        data, actual_pair_path = resolve_active_pair(runtime_root, enforce_integrity=False)
        rel = data.get("graph_path")
        if rel:
            cand = Path(rel) if Path(rel).is_absolute() else (actual_pair_path.parent / rel).resolve()
            if cand.is_file():
                return cand
    except Exception:
        pass
    return _read_pair_fallback(runtime_root)


def _is_valid_sqlite_db(path: Optional[Path]) -> bool:
    """Validates that a path points to an existing non-empty database file."""
    if not path:
        return False
    try:
        p = Path(path)
        return p.is_file() and p.stat().st_size > 1024
    except Exception:
        return False


def _find_generation_sqlite(bases: Sequence[Path]) -> Optional[Path]:
    for gen_base in bases:
        gen_dir = gen_base / "generations"
        if gen_dir.is_dir():
            gen_sqlite = sorted(gen_dir.glob("*/graph.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
            if gen_sqlite and _is_valid_sqlite_db(gen_sqlite[0]):
                return gen_sqlite[0]
    return None


def _find_fallback_sqlite(repo_root: Path) -> Optional[Path]:
    for db_rel in (
        "graph/nee_graph.sqlite",
        "rollback/baseline/nee_graph_v53.sqlite",
        "rollback/baseline/nee_graph_v52.sqlite",
        "storage/CANONICAL_GRAPH.db",
    ):
        cand = repo_root / db_rel
        if _is_valid_sqlite_db(cand):
            return cand
    return None


def resolve_active_graph_path(
    repo_root: Path,
    runtime_root: Optional[Path],
    default_graph_path: Optional[Path],
    override_path: Optional[Path] = None,
) -> Optional[Path]:
    """Resolves target graph database path across active pair, generations, and fallbacks."""
    if override_path is not None:
        p = Path(override_path)
        return p if _is_valid_sqlite_db(p) else None

    runtime_cand = read_active_pair_graph(runtime_root)
    if runtime_cand and _is_valid_sqlite_db(runtime_cand):
        return runtime_cand

    if default_graph_path and Path(default_graph_path).name != "nee_graph.sqlite" and _is_valid_sqlite_db(default_graph_path):
        return Path(default_graph_path)

    from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
    auth_rt = resolve_authoritative_runtime_root(repo_root)

    for ap_dir in (repo_root / ".local" / "dual_pipeline", auth_rt, repo_root / "dual_pipeline" / "retrieval"):
        cand = read_active_pair_graph(ap_dir)
        if cand and _is_valid_sqlite_db(cand):
            return cand

    gen_cand = _find_generation_sqlite((repo_root / ".local" / "dual_pipeline", auth_rt))
    if gen_cand:
        return gen_cand

    if default_graph_path and _is_valid_sqlite_db(default_graph_path):
        return default_graph_path

    return _find_fallback_sqlite(repo_root)


def _build_assertion_resource_query(
    cursor: Any,
    placeholders: str,
    tables: Set[str],
    proj_tokens: Optional[Sequence[str]] = None,
) -> Tuple[str, List[Any]]:
    ass_cols = {r[1] for r in cursor.execute("PRAGMA table_info(assertion)").fetchall()}
    desc_expr = (
        "coalesce(a.scope_text, a.object_literal, '')"
        if "scope_text" in ass_cols
        else ("coalesce(a.claim_text, '')" if "claim_text" in ass_cols else "''")
    )
    cur_filter = ""
    for col in ("canonical_status", "canonical_eligible", "currentness_state"):
        if col in ass_cols:
            val = "'canonical'" if col == "canonical_status" else ("1" if col == "canonical_eligible" else "'current'")
            cur_filter += f" AND a.{col} = {val}"
    if "superseded_by_assertion_id" in ass_cols:
        cur_filter += " AND (a.superseded_by_assertion_id IS NULL OR a.superseded_by_assertion_id = '')"
    if "is_quarantined" in ass_cols:
        cur_filter += " AND (a.is_quarantined IS NULL OR a.is_quarantined = 0)"

    source_clause = ""
    extra_params: List[Any] = []
    if proj_tokens and "source" in tables:
        source_conds = " OR ".join("s.source_name LIKE ?" for _ in proj_tokens)
        source_clause = f"OR ({source_conds})"
        extra_params = [f"%{tok}%" for tok in proj_tokens]

    q = f"""
    SELECT DISTINCT n.node_id, n.canonical_name, n.display_name, n.node_type,
           coalesce(n.description, '') as description,
           a.predicate as edge_type, {desc_expr} as edge_desc,
           a.subject_node_id as from_node_id, a.object_node_id as to_node_id
    FROM assertion a
    JOIN node n ON (n.node_id = a.subject_node_id OR n.node_id = a.object_node_id)
    LEFT JOIN source s ON a.primary_source_id = s.source_id
    WHERE ((a.subject_node_id IN ({placeholders}) OR a.object_node_id IN ({placeholders})) {source_clause})
      AND n.node_id NOT IN ({placeholders})
      AND n.node_type IN ('stakeholder', 'person', 'internal_person')
      {cur_filter}
    """
    return q, extra_params


def _build_edge_resource_query(placeholders: str) -> Tuple[str, List[Any]]:
    q = f"""
    SELECT DISTINCT n.node_id, n.canonical_name, n.display_name, n.node_type,
           coalesce(n.description, '') as description,
           e.edge_type, coalesce(e.description, '') as edge_desc,
           e.from_node_id, e.to_node_id
    FROM edge e
    JOIN node n ON (n.node_id = e.from_node_id OR n.node_id = e.to_node_id)
    WHERE (e.from_node_id IN ({placeholders}) OR e.to_node_id IN ({placeholders}))
      AND n.node_id NOT IN ({placeholders})
      AND n.node_type IN ('stakeholder', 'person', 'internal_person')
    """
    return q, []


def _build_resource_traversal_query(
    cursor: Any,
    placeholders: str,
    has_quarantine: bool,
    tables: Set[str],
    proj_tokens: Optional[Sequence[str]] = None,
) -> Tuple[Optional[str], List[Any]]:
    if "assertion" in tables:
        q, extra_params = _build_assertion_resource_query(cursor, placeholders, tables, proj_tokens)
    elif "edge" in tables:
        q, extra_params = _build_edge_resource_query(placeholders)
    else:
        return None, []
    if has_quarantine:
        q += " AND (n.is_quarantined IS NULL OR n.is_quarantined = 0)"
    return q + " LIMIT 50", extra_params


def _extract_project_tokens(
    cursor: Any, placeholders: str, p_ids: Sequence[str], tables: Set[str]
) -> List[str]:
    if "node" not in tables:
        return []
    proj_tokens: List[str] = []
    try:
        cnames = [
            r[0]
            for r in cursor.execute(
                f"SELECT canonical_name FROM node WHERE node_id IN ({placeholders})", list(p_ids)
            ).fetchall()
            if r[0]
        ]
        for cn in cnames:
            for word in re.split(r"[\s/_\-]+", cn):
                if len(word) >= 4 and word.lower() not in COMMON_STOP_WORDS:
                    proj_tokens.append(word)
    except Exception:
        pass
    return list(dict.fromkeys(proj_tokens))


def _resolve_2hop_role(cursor: Any, nid: str, tables: Set[str]) -> Optional[str]:
    if "assertion" not in tables:
        return None
    try:
        role_row = cursor.execute(
            """
            SELECT COALESCE(n_target.canonical_name, a.object_literal, a.scope_text, '') AS role_target
            FROM assertion a
            LEFT JOIN node n_target ON a.object_node_id = n_target.node_id
            WHERE a.subject_node_id = ?
              AND a.predicate IN ('HAS_ROLE_IN', 'HAS_ROLE', 'OWNS_OR_LEADS')
              AND (a.currentness_state IS NULL OR a.currentness_state = 'current')
              AND (a.superseded_by_assertion_id IS NULL OR a.superseded_by_assertion_id = '')
            ORDER BY CASE WHEN a.predicate = 'HAS_ROLE_IN' THEN 0 WHEN a.predicate = 'HAS_ROLE' THEN 1 ELSE 2 END, a.assertion_id DESC
            LIMIT 1
            """,
            (nid,),
        ).fetchone()
        if role_row and role_row[0] and len(role_row[0].strip()) > 1:
            return role_row[0].strip()
    except Exception:
        pass
    return None


def _resolve_2hop_manager(cursor: Any, nid: str, tables: Set[str]) -> Optional[str]:
    if "assertion" not in tables:
        return None
    try:
        mgr_row = cursor.execute(
            """
            SELECT COALESCE(n_mgr.canonical_name, a.object_literal, '') AS manager_name
            FROM assertion a
            LEFT JOIN node n_mgr ON a.object_node_id = n_mgr.node_id
            WHERE a.subject_node_id = ?
              AND a.predicate = 'REPORTS_TO'
              AND (a.currentness_state IS NULL OR a.currentness_state = 'current')
              AND (a.superseded_by_assertion_id IS NULL OR a.superseded_by_assertion_id = '')
            ORDER BY a.assertion_id DESC
            LIMIT 1
            """,
            (nid,),
        ).fetchone()
        if mgr_row and mgr_row[0] and len(mgr_row[0].strip()) > 1:
            return mgr_row[0].strip()
    except Exception:
        pass
    return None


def _compute_stakeholder_role(
    d: Dict[str, Any], resolved_role: Optional[str], reports_to: Optional[str]
) -> str:
    desc = d.get("description", "")
    edge_desc = d.get("edge_desc", "")
    if resolved_role:
        return f"{resolved_role} (Reports to {reports_to})" if reports_to else resolved_role
    if reports_to:
        base_role = edge_desc or desc or d.get("edge_type", "Team Resource")
        return f"{base_role} (Reports to {reports_to})"
    return edge_desc or desc or d.get("edge_type", "Team Resource")


def traverse_project_resources(
    cursor: Any,
    project_node_ids: Sequence[str],
    has_quarantine: bool,
    tables: Set[str],
) -> Tuple[List[GraphEntityReference], Set[str]]:
    """Traverses assertions connected to project/workstream nodes to find associated stakeholders."""
    if not project_node_ids:
        return [], set()

    p_ids = list(project_node_ids)
    placeholders = ",".join("?" for _ in p_ids)
    proj_tokens = _extract_project_tokens(cursor, placeholders, p_ids, tables)

    q, extra_params = _build_resource_traversal_query(cursor, placeholders, has_quarantine, tables, proj_tokens)
    if not q:
        return [], set()
    params = p_ids + p_ids + extra_params + p_ids

    entities: List[GraphEntityReference] = []
    terms: Set[str] = set()
    for r in cursor.execute(q, params).fetchall():
        d = dict(r)
        nid = d.get("node_id", "")
        comb_desc = f"{d.get('description', '')} {d.get('edge_desc', '')}"
        org = "PartnerAdvisory" if "PartnerAdvisory" in comb_desc else ("NEA" if "NEA" in comb_desc else None)

        role = _compute_stakeholder_role(
            d,
            _resolve_2hop_role(cursor, nid, tables),
            _resolve_2hop_manager(cursor, nid, tables),
        )
        matched_ws = [pid for pid in p_ids if pid in (d.get("from_node_id"), d.get("to_node_id"))]
        ref = GraphEntityReference(
            node_id=nid,
            canonical_name=d.get("canonical_name", ""),
            display_name=d.get("display_name", d.get("canonical_name", "")),
            node_type="stakeholder",
            organization=org,
            role=role,
            associated_workstreams=tuple(matched_ws),
        )
        if not any(x.node_id == ref.node_id for x in entities):
            entities.append(ref)
        if ref.canonical_name:
            terms.add(ref.canonical_name)

    return entities, terms


def _find_associated_workstreams(
    cursor: Any, nid: str, has_assertion: bool, has_edge: bool
) -> Tuple[str, ...]:
    if has_assertion:
        ass_cols = {r[1] for r in cursor.execute("PRAGMA table_info(assertion)").fetchall()}
        cur_cond = ""
        if "canonical_status" in ass_cols:
            cur_cond += " AND canonical_status = 'canonical'"
        if "canonical_eligible" in ass_cols:
            cur_cond += " AND canonical_eligible = 1"
        if "currentness_state" in ass_cols:
            cur_cond += " AND currentness_state = 'current'"
        ws_rows = cursor.execute(
            f"SELECT DISTINCT object_node_id FROM assertion WHERE subject_node_id = ? AND object_node_id LIKE '%workstream%'{cur_cond} "
            f"UNION "
            f"SELECT DISTINCT subject_node_id FROM assertion WHERE object_node_id = ? AND subject_node_id LIKE '%workstream%'{cur_cond}",
            (nid, nid),
        ).fetchall()
        return tuple(sorted(list({row[0] for row in ws_rows if row[0]})))
    if has_edge:
        ws_rows = cursor.execute(
            "SELECT DISTINCT to_node_id FROM edge WHERE from_node_id = ? AND to_node_id LIKE '%workstream%' "
            "UNION "
            "SELECT DISTINCT from_node_id FROM edge WHERE to_node_id = ? AND from_node_id LIKE '%workstream%'",
            (nid, nid),
        ).fetchall()
        return tuple(sorted(list({row[0] for row in ws_rows if row[0]})))
    return ()


def _is_name_in_transcript(
    c_name: str, transcript_text: str, words_lower: Set[str], words_case: Set[str]
) -> bool:
    if len(c_name) > 4 and c_name.lower() in transcript_text.lower():
        return True
    first = c_name.split()[0].lower() if c_name else ""
    if first and len(first) >= 3 and first not in COMMON_STOP_WORDS and first in words_lower and first in words_case:
        prefix_re = rf"\b(?:st\.?|saint|san|fort|mount|lake|port)\s+{re.escape(first)}\b"
        return not bool(re.search(prefix_re, transcript_text, re.IGNORECASE))
    return False


def _is_workstream_excluded(nid: str, scoped_workstreams: Optional[Sequence[str]]) -> bool:
    if not scoped_workstreams:
        return False
    nid_lower = nid.lower()
    if "workstream_lens" in scoped_workstreams and ("sherpa" in nid_lower or "clay_palumbo" in nid_lower):
        return True
    if "workstream_project_beacon" in scoped_workstreams and ("lens" in nid_lower or "jen_pan" in nid_lower):
        return True
    return False


def _extract_org(desc: str) -> Optional[str]:
    if "PartnerAdvisory" in desc:
        return "PartnerAdvisory"
    if "NEA" in desc:
        return "NEA"
    return None


def _build_spotted_ref(
    d: Dict[str, Any], associated_ws: Tuple[str, ...]
) -> GraphEntityReference:
    c_name = d.get("canonical_name", "")
    desc = d.get("description", "")
    return GraphEntityReference(
        node_id=d.get("node_id", ""),
        canonical_name=c_name,
        display_name=d.get("display_name", c_name),
        node_type="stakeholder",
        organization=_extract_org(desc),
        role=desc or "Identified Graph Stakeholder",
        associated_workstreams=associated_ws,
    )


def spot_transcript_entities(
    cursor: Any,
    transcript_text: Optional[str],
    has_quarantine: bool,
    max_spotted: int = 15,
    scoped_workstreams: Optional[Sequence[str]] = None,
) -> Tuple[List[GraphEntityReference], Set[str]]:
    """Scans transcript text for mentions of any known graph stakeholders."""
    if not transcript_text:
        return [], set()

    tables = {
        r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
    }
    has_assertion = "assertion" in tables
    has_edge = "edge" in tables

    q = (
        "SELECT node_id, canonical_name, display_name, node_type, "
        "coalesce(description, '') as description "
        "FROM node WHERE node_type IN ('stakeholder', 'person', 'internal_person')"
    )
    if has_quarantine:
        q += " AND (is_quarantined IS NULL OR is_quarantined = 0)"
    rows = cursor.execute(q).fetchall()

    words_in_transcript = set(re.findall(r"\b[A-Za-z0-9_-]+\b", transcript_text))
    words_lower = {w.lower() for w in words_in_transcript}
    words_case = {w.lower() for w in re.findall(r"\b[A-Z][a-z]+\b", transcript_text)}

    entities: List[GraphEntityReference] = []
    terms: Set[str] = set()

    for r in rows:
        d = dict(r)
        c_name = d.get("canonical_name", "")
        if not c_name or not _is_name_in_transcript(c_name, transcript_text, words_lower, words_case):
            continue
        nid = d.get("node_id", "")
        if _is_workstream_excluded(nid, scoped_workstreams):
            continue

        associated_ws = _find_associated_workstreams(cursor, nid, has_assertion, has_edge)
        ref = _build_spotted_ref(d, associated_ws)
        if not any(x.node_id == ref.node_id for x in entities):
            entities.append(ref)
        terms.add(c_name)
        if len(entities) >= max_spotted:
            break

    return entities, terms


def clean_attendee_names(attendees: Optional[Sequence[Any]]) -> List[str]:
    """Extract clean string names from attendee roster entries."""
    cleaned: List[str] = []
    if not attendees:
        return cleaned
    for att in attendees:
        if isinstance(att, dict):
            n = att.get("name")
            if n:
                cleaned.append(str(n).strip())
        elif isinstance(att, str):
            m = re.match(r"^([^(]+)", att)
            cleaned.append(m.group(1).strip() if m else att.strip())
    return cleaned


def extract_domain_terms(cursor: Any, has_quarantine: bool) -> Set[str]:
    """Retrieve key vocabulary and domain terms from active graph."""
    q_terms = (
        "SELECT canonical_name, display_name FROM node "
        "WHERE node_type IN ('system', 'workstream', 'policy', 'metric')"
    )
    if has_quarantine:
        q_terms += " AND (is_quarantined IS NULL OR is_quarantined = 0)"
    q_terms += " ORDER BY node_id DESC LIMIT 15"
    terms: Set[str] = set()
    for r in cursor.execute(q_terms).fetchall():
        d = dict(r)
        c_name = d.get("canonical_name") or d.get("display_name")
        if c_name and len(c_name) >= 3:
            terms.add(c_name)
    return terms


def parse_raw_claim(raw: Any) -> str:
    """Safely parse claim text from string or JSON structure."""
    if isinstance(raw, str) and raw.startswith("{"):
        try:
            parsed = json.loads(raw)
            return str(parsed.get("text") or parsed.get("claim") or raw)
        except Exception:
            return str(raw)
    return str(raw) if raw else ""


def extract_project_nodes(
    cursor: Any, project_name: Optional[str], has_quarantine: bool
) -> Tuple[List[GraphEntityReference], Set[str]]:
    """Resolve project and workstream entity nodes for a project query."""
    if not project_name:
        return [], set()
    clean_proj = re.sub(r"[^\w\s-]", "", project_name).strip()
    core_proj = re.sub(r"^project_", "", clean_proj.lower())
    terms = {clean_proj, core_proj}
    q = (
        "SELECT * FROM node WHERE ("
        "canonical_name LIKE ? OR display_name LIKE ? OR node_id LIKE ? "
        "OR canonical_name LIKE ? OR display_name LIKE ? OR node_id LIKE ?"
        ")"
    )
    params = [
        f"%{clean_proj}%", f"%{clean_proj}%", f"%{clean_proj}%",
        f"%{core_proj}%", f"%{core_proj}%", f"%{core_proj}%",
    ]
    if has_quarantine:
        q += " AND (is_quarantined IS NULL OR is_quarantined = 0)"
    q += " AND (active IS NULL OR active = 1)"
    q += " LIMIT 10"
    entities: List[GraphEntityReference] = []
    for r in cursor.execute(q, params).fetchall():
        d = dict(r)
        ref = GraphEntityReference(
            node_id=d.get("node_id", ""),
            canonical_name=d.get("canonical_name", ""),
            display_name=d.get("display_name", d.get("canonical_name", "")),
            node_type=d.get("node_type", "project"),
            organization=d.get("organization"),
            role=d.get("role"),
        )
        entities.append(ref)
        if ref.canonical_name:
            terms.add(ref.canonical_name)
    return entities, terms


def extract_attendee_nodes(
    cursor: Any,
    cleaned_names: List[str],
    has_quarantine: bool,
    max_entities: int,
) -> Tuple[List[GraphEntityReference], Set[str]]:
    """Lookup attendee nodes from canonical graph by name."""
    entities: List[GraphEntityReference] = []
    terms: Set[str] = set()
    for name in cleaned_names[:max_entities]:
        if not name:
            continue
        q = "SELECT * FROM node WHERE (canonical_name LIKE ? OR display_name LIKE ?)"
        p = [f"%{name}%", f"%{name}%"]
        if has_quarantine:
            q += " AND (is_quarantined IS NULL OR is_quarantined = 0)"
        q += " AND (active IS NULL OR active = 1)"
        q += " LIMIT 2"
        for r in cursor.execute(q, p).fetchall():
            d = dict(r)
            ref = GraphEntityReference(
                node_id=d.get("node_id", ""),
                canonical_name=d.get("canonical_name", ""),
                display_name=d.get("display_name", d.get("canonical_name", "")),
                node_type=d.get("node_type", "person"),
                organization=d.get("organization"),
                role=d.get("role"),
            )
            if not any(x.node_id == ref.node_id for x in entities):
                entities.append(ref)
            if ref.canonical_name:
                terms.add(ref.canonical_name)
    return entities, terms
