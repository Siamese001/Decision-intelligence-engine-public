"""
dual_pipeline/transcript_metadata_core/entity_resolver_graph.py

L1/L2 Knowledge Graph database path discovery and entity/assertion extraction.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EntityCandidate:
    """Represents a candidate title/role assertion from GraphDB or authoritative sources."""
    role: str
    confidence: float = 1.0
    currentness_state: str = "current"
    validation_state: str = "needs_review"
    valid_from: Optional[str] = None
    assertion_id: Optional[str] = None
    source_node_id: Optional[str] = None

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)

    def as_dict(self) -> Dict[str, Any]:
        return {
            k: getattr(self, k)
            for k in ("role", "confidence", "currentness_state", "validation_state", "valid_from", "assertion_id", "source_node_id")
        }


def _find_graph_in_base(base: Path) -> Optional[Path]:
    ap_file = base / "active_pair.json"
    if ap_file.is_file():
        try:
            from dual_pipeline.runtime_root import resolve_active_graph_path as _resolve_rt_graph
            return _resolve_rt_graph(base, enforce_integrity=False)
        except Exception:
            try:
                rel = json.loads(ap_file.read_text(encoding="utf-8")).get("graph_path")
                if rel and (p := (base / rel).resolve()).exists():
                    return p
            except Exception as e:
                logger.debug(f"Could not read active_pair.json at {ap_file}: {e}")
    gen_dir = base / "generations"
    if gen_dir.exists() and (cands := sorted(gen_dir.glob("*/graph.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)):
        return cands[0]
    for cand in ("CANONICAL_GRAPH.db", "canonical_graph.sqlite", "graph.sqlite", "nee_graph.sqlite", "graph/nee_graph.sqlite"):
        if (base / cand).is_file():
            return base / cand
    return None


def resolve_active_graph_path(
    root: Optional[Path] = None,
    runtime_root: Optional[Path] = None,
) -> Optional[Path]:
    """Resolve active graph SQLite database from active_pair.json or repository locations."""
    resolved_root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    if runtime_root:
        return _find_graph_in_base(Path(runtime_root))

    from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
    auth_rt = resolve_authoritative_runtime_root(resolved_root)
    for base in (resolved_root / ".local" / "dual_pipeline", auth_rt, resolved_root / "dual_pipeline" / "retrieval", resolved_root):
        found = _find_graph_in_base(base)
        if found:
            return found

    fallbacks = (
        "graph/nee_graph.sqlite", "rollback/baseline/nee_graph_v53.sqlite",
        "production.sqlite", "decision_knowledge.sqlite",
        "CANONICAL_GRAPH.db", "storage/CANONICAL_GRAPH.db",
    )
    return next((resolved_root / db for db in fallbacks if (resolved_root / db).is_file()), None)


def resolve_active_generation_id(
    root: Optional[Path] = None,
    runtime_root: Optional[Path] = None,
    fallback_generation_id: str = "unknown",
) -> Optional[str]:
    resolved_root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    from dual_pipeline.runtime_root import resolve_active_pair, resolve_authoritative_runtime_root
    for rt_cand in filter(None, [runtime_root, resolved_root / ".local" / "dual_pipeline", resolve_authoritative_runtime_root(resolved_root), resolved_root]):
        try:
            ap_data, _ = resolve_active_pair(rt_cand, enforce_integrity=False)
            gen_id = str(ap_data.get("generation_id") or "")
            if gen_id:
                return gen_id
        except Exception:
            ap_file = Path(rt_cand) / "active_pair.json"
            if ap_file.is_file():
                try:
                    gen_id = str(json.loads(ap_file.read_text(encoding="utf-8")).get("generation_id") or "")
                    if gen_id:
                        return gen_id
                except Exception:
                    pass
    return fallback_generation_id or None


_ROLE_KEYWORDS_RE = re.compile(
    r"\b(?:Director|Manager|Lead|Specialist|Architect|Engineer|Officer|President|VP|"
    r"Head|Consultant|Practitioner|Partner|Strategist|Analyst|Developer|Designer|"
    r"Coordinator|Supervisor|Executive|Scientist|Researcher|Principal|"
    r"Administrator|Controller|Counsel|Advisor|Fellow|Chief|CTO|CIO|CFO|CEO|COO|"
    r"CISO|SVP|EVP|AVP|PM|SME|Scrum\s+Master|Product\s+Owner|Business\s+Owner|Leader)\b",
    re.IGNORECASE,
)

_ROLE_PREFIX_TOKEN = (
    r"(?:Senior|Sr\.?|Lead|Principal|Chief|Global|Associate|Managing|Staff|Executive|Delivery|"
    r"Assistant|Junior|Jr\.?|VP|Group|Practice|Enterprise|Technical|Client|Engagement|"
    r"National|Regional|Head|Acting|Interim|Solutions?|Data|Cloud|AI|ML|AI/ML|Software|"
    r"Platform|Operations?|Product|Program|Project|Security|Infrastructure|UX|UI|Business|"
    r"Strategic|Strategy|Creative|Digital|Engineering|Customer|Account|Commercial)"
)
_ROLE_PREFIXES = r"(?:" + _ROLE_PREFIX_TOKEN + r"[,\s/&]+)*"

_ROLE_TITLE_RE = re.compile(
    r"^" + _ROLE_PREFIXES + r"(?:Leader|Director|Manager|Lead|Specialist|Architect|Engineer|Officer|President|VP|"
    r"Head|Consultant|Practitioner|Partner|Strategist|Analyst|Developer|Designer|"
    r"Coordinator|Supervisor|Executive|Scientist|Researcher|Principal|"
    r"Administrator|Controller|Counsel|Advisor|Fellow|Chief|CTO|CIO|CFO|CEO|COO|"
    r"CISO|SVP|EVP|AVP|PM|SME|Scrum\s+Master|Product\s+Owner|Business\s+Owner)\b"
    r"(?:[,\s/&]+[A-Za-z0-9\s/&,-]+)?",
    re.IGNORECASE,
)

_NON_ROLE_CLAUSES_RE = re.compile(
    r"^(?:Affiliation|Source\s+affiliation|Canonical\s+|Active\s+in|Participated|"
    r"Attended|Mentioned|Referenced|Discussed|Reports\s+to|Works\s+with|"
    r"Collaborates|Responsible\s+for|Focuses\s+on|Involved\s+in|Member\s+of|"
    r"Part\s+of|Assigned\s+to|Supporting|Key\s+stakeholder|Stakeholder\s+in|"
    r"Primary\s+contact|Point\s+of\s+contact|"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}|"
    r"Notes\s+|Context\s+|Visibility\s+|Pursuit\s+|Meeting\s+|Session\s+|Call\s+)",
    re.IGNORECASE,
)


def _sanitize_extracted_role(raw_role: str) -> str:
    cleaned = re.sub(
        r"\s*&\s*VP\s*/\s*Executive\s+Sponsor\b",
        "",
        raw_role.strip(),
        flags=re.IGNORECASE,
    ).strip(" ,;.-")
    return cleaned or "Participant"


def _extract_role_from_description(desc_str: str) -> Optional[str]:
    if not desc_str:
        return None
    m_role = re.search(
        r"(?:Source roles|Role|Title|Position):\s*([^;\.\n]+)",
        desc_str,
        re.IGNORECASE,
    )
    if m_role:
        cand = _sanitize_extracted_role(m_role.group(1))
        if cand.lower() not in ("unknown", "none", "n/a", "tbd", "participant"):
            return cand

    m_merged = re.search(r"Merged into\s+[^(\n]+\([^,]+,\s*([^)]+)\)", desc_str, re.IGNORECASE)
    if m_merged and _ROLE_KEYWORDS_RE.search(m_merged.group(1)):
        return _sanitize_extracted_role(m_merged.group(1))

    for clause in re.split(r"[;\n]|\.\s+", desc_str):
        c_str = re.sub(r"\s*\([^)]*\)", "", clause).strip(" ,.-")
        if not c_str or _NON_ROLE_CLAUSES_RE.match(c_str):
            continue
        c_low = c_str.lower()
        if any(w in c_low for w in ("notes support", "context", "visibility", "pursuit", "attended meeting", "participated in")):
            continue
        m_is = re.search(
            r"\b(?:is\s+(?:the\s+|a\s+|an\s+)?|serves\s+as\s+(?:the\s+)?|acting\s+as\s+|role\s+of\s+)([A-Z][^.;\n]{2,80})",
            c_str,
        )
        if m_is and _ROLE_KEYWORDS_RE.search(m_is.group(1)):
            return _sanitize_extracted_role(m_is.group(1))
        m_title = _ROLE_TITLE_RE.match(c_str)
        if m_title:
            cleaned_title = re.sub(
                r"\s+(?:at|for|in|of)\s+(?:PartnerAdvisory|Apex Global|NEA|Apex Global Enterprises|APEX|Client).*$",
                "",
                m_title.group(0),
                flags=re.IGNORECASE,
            ).strip(" ,.-")
            if _ROLE_KEYWORDS_RE.search(cleaned_title):
                return _sanitize_extracted_role(cleaned_title)
    return None


def _parse_entity_org_and_role(desc: Optional[str], props: Dict[str, Any]) -> Tuple[str, str]:
    desc_str = desc or ""
    org = props.get("org") or props.get("organization") or "Client / Partner"
    if "Apex Global Analytics" in desc_str:
        org = "Apex Global Analytics"
    elif "Affiliation label(s): PartnerAdvisory" in desc_str or desc_str.strip().lower().startswith("partner_advisory") or "partner_advisory lead" in desc_str.lower() or "partner_advisory team" in desc_str.lower() or "partner_advisory leadership" in desc_str.lower() or "partner_advisory contributor" in desc_str.lower():
        org = "PartnerAdvisory"
    elif "Affiliation label(s): NEA" in desc_str or "Apex Global" in desc_str or "NEA" in desc_str:
        org = "Apex Global Enterprises (NEA)"
    elif "PartnerAdvisory" in desc_str:
        org = "PartnerAdvisory"

    prop_role = str(props.get("role") or "").strip()
    role = _sanitize_extracted_role(prop_role) if prop_role else "Participant"
    if role == "Participant" or not role:
        extracted = _extract_role_from_description(desc_str)
        if extracted:
            role = extracted
    return org, role


def _parse_ts(ts_str: Optional[str]) -> Optional[datetime]:
    if not ts_str or not isinstance(ts_str, str):
        return None
    cleaned = ts_str.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except Exception:
        try:
            parts = cleaned.split("T")[0].split(" ")[0]
            return datetime.strptime(parts, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            return None


def _is_temporally_active(
    obs: Optional[str],
    stale: Optional[str],
    status: Optional[str],
    cur_state: Optional[str],
    sup_id: Optional[str],
    as_of: Optional[str],
) -> bool:
    if sup_id and str(sup_id).strip():
        return False
    bad = {"SUPERSEDED", "RETRACTED", "RETIRED", "REJECTED", "INVALIDATED", "EXPIRED"}
    if str(status or "").upper() in bad or str(cur_state or "").upper() in bad:
        return False
    dt_as_of = _parse_ts(as_of) or datetime.now(timezone.utc)
    if as_of and (dt_obs := _parse_ts(obs)) and dt_obs > dt_as_of:
        return False
    return not ((dt_stale := _parse_ts(stale)) and dt_stale <= dt_as_of)


def _clean_node_names(
    canon_name: Optional[str], disp_name: Optional[str]
) -> Tuple[str, str, str, str, List[str]]:
    c_raw = canon_name or disp_name or ""
    nicknames = re.findall(r"\(([A-Za-z]+)\)", c_raw)
    clean_canon = re.sub(r"\s*\([^)]*\)\s*", " ", c_raw).strip()
    clean_disp = re.sub(r"\s*\([^)]*\)\s*", " ", disp_name).strip() if disp_name else ""
    if clean_canon and len(clean_canon.split()) >= 2:
        c_name = clean_canon
    elif clean_disp and len(clean_disp.split()) >= 2:
        c_name = clean_disp
    elif clean_canon and len(clean_canon.split()) >= 2:
        c_name = clean_canon
    elif nicknames and len(clean_canon.split()) >= 1:
        c_name = f"{nicknames[0]} {clean_canon.split()[-1]}"
    elif clean_canon:
        c_name = clean_canon
    else:
        c_name = c_raw
    return c_raw, c_name, clean_canon, clean_disp, nicknames


def _parse_props_dict(props_val: Any) -> Dict[str, Any]:
    if isinstance(props_val, dict):
        return props_val
    if props_val:
        try:
            return json.loads(props_val)
        except Exception:
            return {}
    return {}


def _refine_org_from_directory(c_name: str, org: str) -> str:
    if org and org not in ("Client / Partner", "Unknown"):
        return org
    try:
        from .entity_resolver import _CANONICAL_ENTERPRISE_DIRECTORY

        dir_ent = _CANONICAL_ENTERPRISE_DIRECTORY.get(c_name.strip().lower())
        if dir_ent and dir_ent[1] and dir_ent[1] not in ("Client / Partner", "Unknown"):
            return dir_ent[1]
    except Exception:
        pass
    return org


def _should_replace_cache_entry(existing: Any, new_entity: Any, nid: Any, k: str, c_name: str) -> bool:
    ex_id = str(getattr(existing, "provenance_id", "") or "")
    curr_id = str(nid or "")
    if curr_id.startswith("unresolved_") and not ex_id.startswith("unresolved_"):
        return False
    if ex_id.startswith("unresolved_") and not curr_id.startswith("unresolved_"):
        return True

    ex_role = str(getattr(existing, "role", "") or "").strip().lower()
    new_role = str(getattr(new_entity, "role", "") or "").strip().lower()
    if ex_role and ex_role != "participant" and new_role in ("", "participant"):
        return False
    if ex_role in ("", "participant") and new_role and new_role != "participant":
        return True
    if curr_id.startswith("person_") and not ex_id.startswith("person_"):
        return True
    return False


def _cache_entity_alias(
    kg_cache: Dict[str, Any], entity: Any, nid: Any, c_name: str, k: str
) -> None:
    if not k:
        return
    if k not in kg_cache or _should_replace_cache_entry(kg_cache[k], entity, nid, k, c_name):
        kg_cache[k] = entity


def _cache_node_aliases(
    kg_cache: Dict[str, Any],
    entity: Any,
    nid: Any,
    c_raw: str,
    c_name: str,
    clean_canon: str,
    disp_name: Optional[str],
    clean_disp: str,
    nicknames: List[str],
) -> None:
    for k in {c_raw.lower(), c_name.lower(), clean_canon.lower()}:
        _cache_entity_alias(kg_cache, entity, nid, c_name, k)
    if disp_name:
        _cache_entity_alias(kg_cache, entity, nid, c_name, disp_name.strip().lower())
    if clean_disp:
        _cache_entity_alias(kg_cache, entity, nid, c_name, clean_disp.lower())

    p_parts = c_name.split()
    surname = p_parts[-1] if len(p_parts) >= 2 else ""
    for nick in nicknames:
        n_low = nick.lower()
        if surname:
            _cache_entity_alias(kg_cache, entity, nid, c_name, f"{n_low} {surname.lower()}")
            _cache_entity_alias(kg_cache, entity, nid, c_name, f"{n_low} {surname[0].lower()}")
        nick_cand = replace(
            entity,
            status="UNRESOLVED_CANDIDATE_ONLY",
            candidate_name=c_name,
            requires_operator_confirmation=True,
            source=f"{getattr(entity, 'source', 'knowledge_graph')}_nickname_candidate",
        )
        _cache_entity_alias(kg_cache, nick_cand, nid, c_name, n_low)

    if len(p_parts) >= 2 and not str(nid or "").startswith("unresolved_"):
        f_low = p_parts[0].lower()
        _cache_entity_alias(kg_cache, entity, nid, c_name, f"{f_low} {p_parts[-1][0].lower()}")
        new_status = entity.status if getattr(entity, "status", "") in ("AMBIGUOUS_CONFLICT", "UNRESOLVED_GRAPH_ATTENDEE") else "UNRESOLVED_CANDIDATE_ONLY"
        single_cand = replace(
            entity,
            status=new_status,
            candidate_name=c_name,
            requires_operator_confirmation=True,
            source=f"{getattr(entity, 'source', 'knowledge_graph')}_single_token_candidate",
        )
        _cache_entity_alias(kg_cache, single_cand, nid, c_name, f_low)


def _process_node_row(
    row: Tuple[Any, ...],
    gen_id: Optional[str],
    entity_factory: Callable[..., Any],
    kg_cache: Dict[str, Any],
    node_id_to_canon: Dict[str, str],
) -> None:
    nid, canon_name, disp_name, desc = row[0], row[1], row[2], row[3]
    props_val = row[4] if len(row) > 4 else None
    c_raw, c_name, clean_canon, clean_disp, nicknames = _clean_node_names(canon_name, disp_name)
    if not c_raw:
        return

    if nid:
        node_id_to_canon[str(nid)] = c_name
        node_id_to_canon[str(nid).lower()] = c_name

    props = _parse_props_dict(props_val)
    org, role = _parse_entity_org_and_role(desc, props)
    org = _refine_org_from_directory(c_name, org)

    entity = entity_factory(
        canonical_name=c_name,
        display_name=disp_name or c_name,
        org=org,
        role=role,
        source="knowledge_graph",
        generation_id=gen_id,
        provenance_id=nid,
    )
    _cache_node_aliases(
        kg_cache, entity, nid, c_raw, c_name, clean_canon, disp_name, clean_disp, nicknames
    )



def _get_node_query(tbl: str, cols: set[str]) -> str:
    if tbl == "node":
        prop_c = ", properties_json" if "properties_json" in cols else ""
        return f"SELECT node_id, canonical_name, display_name, description{prop_c} FROM node WHERE node_type IN ('stakeholder', 'person', 'internal_person') AND active = 1"
    prop_c = ", properties" if "properties" in cols else ""
    name_c = "name" if "name" in cols else "canonical_name"
    desc_c = ", description" if "description" in cols else ", '' as description"
    active_f = "WHERE (is_quarantined IS NULL OR is_quarantined = 0)" if "is_quarantined" in cols else "WHERE 1=1"
    type_f = "AND type IN ('stakeholder', 'person', 'internal_person', 'STAKEHOLDER', 'PERSON')" if "type" in cols else ""
    return f"SELECT node_id, {name_c}, {name_c}{desc_c}{prop_c} FROM canonical_graph_nodes {active_f} {type_f}"


def _load_nodes(
    cur: Any,
    gen_id: Optional[str],
    entity_factory: Callable[..., Any],
) -> Tuple[Dict[str, Any], Dict[str, str], str]:
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name IN ('node', 'canonical_graph_nodes')"
    )
    tables = {r[0] for r in cur.fetchall()}
    if not tables:
        return {}, {}, ""
    if "node" in tables:
        tbl = "node"
        cur.execute("PRAGMA table_info(node)")
    else:
        tbl = "canonical_graph_nodes"
        cur.execute("PRAGMA table_info(canonical_graph_nodes)")
    cols = {row[1] for row in cur.fetchall()}
    cur.execute(_get_node_query(tbl, cols))

    kg_cache: Dict[str, Any] = {}
    node_id_to_canon: Dict[str, str] = {}
    for row in cur.fetchall():
        _process_node_row(row, gen_id, entity_factory, kg_cache, node_id_to_canon)
    return kg_cache, node_id_to_canon, tbl


def _col_expr(cols: set[str], name: str, fallback: str, prefix: str = "a.") -> str:
    return f"{prefix}{name}" if name in cols else fallback


def _build_assertion_query(cols: set[str], node_tbl: str = "node") -> str:
    text_c = _col_expr(cols, "object_literal", _col_expr(cols, "literal_value", "''"))
    scope_c = _col_expr(cols, "scope_text", _col_expr(cols, "claim_text", "''"))
    conf_c = _col_expr(cols, "confidence_score", _col_expr(cols, "confidence", "1.0"))
    status_c = _col_expr(cols, "status", "'CONFIRMED'")
    obs_c = _col_expr(cols, "observed_at", "NULL")
    stale_c = _col_expr(cols, "stale_after", "NULL")
    cur_c = _col_expr(cols, "currentness_state", "NULL")
    sup_c = _col_expr(cols, "superseded_by_assertion_id", "NULL")
    val_c = _col_expr(cols, "validation_state", "'needs_review'")
    from_c = _col_expr(cols, "valid_from", obs_c)
    obj_id_c = _col_expr(cols, "object_node_id", "NULL")

    q_filter = " AND (a.is_quarantined IS NULL OR a.is_quarantined = 0)" if "is_quarantined" in cols else ""
    if "validation_state" in cols:
        q_filter += " AND (a.validation_state IS NULL OR a.validation_state != 'QUARANTINED')"

    if "object_node_id" in cols and node_tbl:
        name_col = "o.canonical_name" if node_tbl == "node" else "o.name"
        type_col = "o.node_type" if node_tbl == "node" else "o.type"
        join_clause = f"LEFT JOIN {node_tbl} o ON a.object_node_id = o.node_id"
        where_cond = f"(a.predicate IN ('HAS_ROLE', 'HAS_ROLE_IN') OR {type_col} = 'role_group' OR {scope_c} LIKE '%HAS_ROLE%')"
    else:
        name_col, type_col, join_clause = "''", "''", ""
        where_cond = f"(a.predicate IN ('HAS_ROLE', 'HAS_ROLE_IN') OR {scope_c} LIKE '%HAS_ROLE%')"

    return (
        f"SELECT a.assertion_id, a.subject_node_id, a.predicate, {obj_id_c} AS object_node_id, "
        f"{text_c} AS object_literal, {scope_c} AS scope_text, {conf_c} AS confidence, "
        f"{status_c} AS status, {obs_c} AS observed_at, {stale_c} AS stale_after, "
        f"{cur_c} AS currentness_state, {sup_c} AS superseded_by_assertion_id, "
        f"{val_c} AS validation_state, {from_c} AS valid_from, "
        f"{type_col} AS object_node_type, {name_col} AS object_canonical_name "
        f"FROM assertion a {join_clause} WHERE {where_cond}{q_filter}"
    )


_NON_ROLE_OBJECT_TYPES = frozenset({
    "deliverable", "pattern", "technical_anchor", "org", "workstream",
    "product_lane", "program", "project", "sow", "card", "system",
    "metric", "policy", "capability", "application", "initiative",
    "platform", "tool", "dataset", "pipeline", "domain",
})


def _extract_role_from_scope(scope: str) -> Optional[str]:
    m = re.search(r"(?:HAS_ROLE|HAS_ROLE_IN)\s+([^\.]+)", scope)
    if m:
        return _sanitize_extracted_role(m.group(1))
    m_serves = re.search(
        r"(?:serves as|role as|acting as|works as)\s+(?:PartnerAdvisory\s+|NEA\s+)?([^\.\(]+?)(?:\s+on\s+|\s+for\s+|\s+in\s+|$)",
        scope,
        re.IGNORECASE,
    )
    if m_serves:
        return _sanitize_extracted_role(m_serves.group(1))
    if "legacy canonical graph relationship" not in scope.lower() and len(scope) <= 60:
        return _sanitize_extracted_role(scope)
    return None


def _extract_role_text(
    obj_lit: Optional[str],
    scope: Optional[str],
    o_type: Optional[str],
    o_name: Optional[str],
) -> Optional[str]:
    if o_type == "role_group" and o_name and str(o_name).strip():
        return _sanitize_extracted_role(str(o_name))
    if (o_type or "").lower() in _NON_ROLE_OBJECT_TYPES:
        if scope and "HAS_ROLE " in scope:
            return _extract_role_from_scope(scope)
        return None
    raw = (obj_lit or "").strip()
    if not raw and scope:
        raw = _extract_role_from_scope(scope) or ""
    if not raw or "legacy canonical graph relationship" in raw.lower():
        return None
    cleaned = _sanitize_extracted_role(raw)
    if cleaned.lower() in ("unknown", "none", "n/a", "tbd", "participant"):
        return None
    return cleaned


def _handle_assertion_conflict(
    curr: Any,
    role_text: str,
    ass_id: Any,
    sub_id: Any,
    conf: float,
    gen_id: Optional[str],
    entity_factory: Callable[..., Any],
) -> Optional[Any]:
    if not curr or not getattr(curr, "role", None) or curr.role == "Participant":
        return None
    if curr.role.strip().lower() == role_text.strip().lower():
        return None
    curr_conf = float(getattr(curr, "confidence", None) or 1.0)
    if abs(curr_conf - conf) > get_hitl_ambiguity_margin_threshold():
        return None
    return entity_factory(
        canonical_name=curr.canonical_name,
        display_name=curr.display_name,
        org=curr.org,
        role=curr.role,
        source="knowledge_graph_assertion_conflict",
        generation_id=gen_id,
        assertion_id=f"{getattr(curr, 'assertion_id', '')},{ass_id}",
        provenance_id=str(sub_id),
        confidence=min(curr_conf, conf),
        status="AMBIGUOUS_CONFLICT",
        candidate_name=f"{curr.role} vs {role_text}",
        requires_operator_confirmation=True,
    )


def _store_in_cache(kg_cache: Dict[str, Any], low: str, disp_name: Optional[str], ent: Any) -> None:
    kg_cache[low] = ent
    if disp_name:
        kg_cache[disp_name.strip().lower()] = ent


def _rank_role_candidates(candidates: List[EntityCandidate]) -> List[EntityCandidate]:
    def sort_key(c: EntityCandidate) -> Tuple[int, int, float, str]:
        c_state = str(c.currentness_state or "").lower()
        is_cur = 1 if c_state in ("current", "active", "confirmed") and c_state not in ("historical", "superseded", "retired", "invalidated", "expired", "retracted") else 0
        v_state = str(c.validation_state or "").lower()
        is_ver = 1 if v_state in ("verified", "confirmed", "approved", "human_verified", "operator_verified") else 0
        return (is_cur, is_ver, float(c.confidence), str(c.valid_from or ""))

    return sorted(candidates, key=sort_key, reverse=True)


def _dedup_role_candidates(cands: List[EntityCandidate]) -> List[EntityCandidate]:
    def cand_key(c: EntityCandidate) -> Tuple[int, int, float, str]:
        c_state = str(c.currentness_state or "").lower()
        is_cur = 1 if c_state in ("current", "active", "confirmed") and c_state not in ("historical", "superseded", "retired", "invalidated", "expired", "retracted") else 0
        v_state = str(c.validation_state or "").lower()
        is_ver = 1 if v_state in ("verified", "confirmed", "approved", "human_verified", "operator_verified") else 0
        return (is_cur, is_ver, float(c.confidence), str(c.valid_from or ""))

    deduped: Dict[str, EntityCandidate] = {}
    for c in cands:
        r_key = re.sub(r"[^a-z0-9]+", " ", c.role.strip().lower()).strip()
        prev = deduped.get(r_key)
        if not prev or cand_key(c) > cand_key(prev):
            deduped[r_key] = c
    return list(deduped.values())



def _resolve_person_role_assertions(
    sub_id: str,
    cands: List[EntityCandidate],
    kg_cache: Dict[str, Any],
    node_id_to_canon: Dict[str, str],
    gen_id: Optional[str],
    entity_factory: Callable[..., Any],
) -> None:
    ranked = _rank_role_candidates(_dedup_role_candidates(cands))
    if not ranked:
        return

    c_name = node_id_to_canon.get(str(sub_id)) or node_id_to_canon.get(str(sub_id).lower())
    if not c_name:
        c_name = re.sub(r"^person_", "", str(sub_id), flags=re.I).replace("_", " ").strip().title()

    low = c_name.strip().lower()
    curr = kg_cache.get(low)
    disp = curr.display_name if curr else c_name
    org = curr.org if curr else "Client / Partner"
    gen = curr.generation_id if curr else gen_id

    threshold = get_hitl_ambiguity_margin_threshold()
    top = ranked[0]
    has_conflict = False
    runner_up = None

    if len(ranked) >= 2:
        runner_up = ranked[1]
        top_c_state = str(top.currentness_state or "").lower()
        top_cur = 1 if top_c_state in ("current", "active", "confirmed") and top_c_state not in ("historical", "superseded", "retired", "invalidated", "expired", "retracted") else 0
        top_v_state = str(top.validation_state or "").lower()
        top_ver = 1 if top_v_state in ("verified", "confirmed", "approved", "human_verified", "operator_verified") else 0

        run_c_state = str(runner_up.currentness_state or "").lower()
        run_cur = 1 if run_c_state in ("current", "active", "confirmed") and run_c_state not in ("historical", "superseded", "retired", "invalidated", "expired", "retracted") else 0
        run_v_state = str(runner_up.validation_state or "").lower()
        run_ver = 1 if run_v_state in ("verified", "confirmed", "approved", "human_verified", "operator_verified") else 0

        # Competing assertion must share the exact same currentness and validation tier to cause conflict
        if (top_cur, top_ver) == (run_cur, run_ver):
            margin = abs(float(top.confidence) - float(runner_up.confidence))
            if margin <= threshold and top.role.strip().lower() != runner_up.role.strip().lower():
                has_conflict = True

    if has_conflict and runner_up:
        ent = entity_factory(
            canonical_name=c_name,
            display_name=disp,
            org=org,
            role=top.role,
            source="knowledge_graph_assertion_conflict",
            generation_id=gen,
            assertion_id=f"{top.assertion_id},{runner_up.assertion_id}",
            provenance_id=str(sub_id),
            confidence=min(float(top.confidence), float(runner_up.confidence)),
            status="AMBIGUOUS_CONFLICT",
            candidate_name=f"{top.role} vs {runner_up.role}",
            requires_operator_confirmation=True,
            candidates=tuple(ranked),
        )
    else:
        ent = entity_factory(
            canonical_name=c_name,
            display_name=disp,
            org=org,
            role=top.role,
            source="knowledge_graph_assertion",
            generation_id=gen,
            assertion_id=top.assertion_id,
            provenance_id=str(sub_id),
            confidence=float(top.confidence),
            status="RESOLVED",
            requires_operator_confirmation=False,
            candidates=tuple(ranked),
        )

    _store_in_cache(kg_cache, low, disp, ent)
    p_parts = c_name.split()
    if len(p_parts) >= 2:
        f_low = p_parts[0].lower()
        if f_low in kg_cache and getattr(kg_cache[f_low], "candidate_name", None) == c_name:
            new_status = ent.status if getattr(ent, "status", "") in ("AMBIGUOUS_CONFLICT", "UNRESOLVED_GRAPH_ATTENDEE") else "UNRESOLVED_CANDIDATE_ONLY"
            kg_cache[f_low] = replace(
                ent,
                status=new_status,
                candidate_name=c_name,
                requires_operator_confirmation=True,
                source=f"{getattr(ent, 'source', 'knowledge_graph')}_single_token_candidate",
            )


def _enrich_from_assertions(
    cur: Any,
    kg_cache: Dict[str, Any],
    node_id_to_canon: Dict[str, str],
    gen_id: Optional[str],
    entity_factory: Callable[..., Any],
    as_of: Optional[str] = None,
    node_tbl: str = "node",
) -> None:
    cur.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name='assertion'")
    if not cur.fetchone():
        return
    cur.execute("PRAGMA table_info(assertion)")
    cols = {row[1] for row in cur.fetchall()}
    query = _build_assertion_query(cols, node_tbl=node_tbl)
    cur.execute(query)

    person_candidates: Dict[str, List[EntityCandidate]] = {}
    for row in cur.fetchall():
        (
            ass_id, sub_id, _pred, _obj_id,
            obj_lit, scope, conf_raw, status,
            obs, stale, cur_state, sup_id,
            val_state, val_from,
            o_type, o_name,
        ) = row[:16]

        if not _is_temporally_active(obs, stale, status, cur_state, sup_id, as_of):
            continue

        role_text = _extract_role_text(obj_lit, scope, o_type, o_name)
        if not role_text:
            continue

        try:
            conf = float(conf_raw) if conf_raw is not None else 1.0
        except Exception:
            conf = 1.0

        cand = EntityCandidate(
            role=role_text,
            confidence=conf,
            currentness_state=str(cur_state or status or "current"),
            validation_state=str(val_state or "needs_review"),
            valid_from=str(val_from or obs or ""),
            assertion_id=str(ass_id),
            source_node_id=str(sub_id),
        )
        person_candidates.setdefault(str(sub_id), []).append(cand)

    for sub_id, cands in person_candidates.items():
        _resolve_person_role_assertions(
            sub_id, cands, kg_cache, node_id_to_canon, gen_id, entity_factory
        )


def _load_aliases_by_name(cur: Any, cols: set[str], kg_cache: Dict[str, Any]) -> None:
    if "active" in cols:
        query = "SELECT alias_name, canonical_name FROM alias WHERE active = 1"
    else:
        query = "SELECT alias_name, canonical_name FROM alias"
    cur.execute(query)
    for al_name, can_name in cur.fetchall():
        if al_name and can_name:
            can_low = can_name.strip().lower()
            if can_low in kg_cache:
                target_ent = kg_cache[can_low]
                al_low = al_name.strip().lower()
                c_name = getattr(target_ent, "canonical_name", can_name)
                if len(al_low.split()) == 1 and len(c_name.split()) > 1:
                    new_status = target_ent.status if getattr(target_ent, "status", "") in ("AMBIGUOUS_CONFLICT", "UNRESOLVED_GRAPH_ATTENDEE") else "UNRESOLVED_CANDIDATE_ONLY"
                    kg_cache[al_low] = replace(
                        target_ent,
                        status=new_status,
                        candidate_name=c_name,
                        requires_operator_confirmation=True,
                        source=f"{getattr(target_ent, 'source', 'knowledge_graph')}_alias_candidate",
                    )
                else:
                    kg_cache[al_low] = target_ent


def _load_aliases_by_node_id(
    cur: Any, kg_cache: Dict[str, Any], node_id_to_canon: Dict[str, str]
) -> None:
    cur.execute("SELECT alias, node_id FROM alias")
    for al_name, nid in cur.fetchall():
        if al_name and nid:
            c_name = node_id_to_canon.get(nid)
            if c_name:
                can_low = c_name.strip().lower()
                if can_low in kg_cache:
                    target_ent = kg_cache[can_low]
                    al_low = al_name.strip().lower()
                    if len(al_low.split()) == 1 and len(c_name.split()) > 1:
                        new_status = target_ent.status if getattr(target_ent, "status", "") in ("AMBIGUOUS_CONFLICT", "UNRESOLVED_GRAPH_ATTENDEE") else "UNRESOLVED_CANDIDATE_ONLY"
                        kg_cache[al_low] = replace(
                            target_ent,
                            status=new_status,
                            candidate_name=c_name,
                            requires_operator_confirmation=True,
                            source=f"{getattr(target_ent, 'source', 'knowledge_graph')}_alias_candidate",
                        )
                    else:
                        kg_cache[al_low] = target_ent


def _load_aliases(cur: Any, kg_cache: Dict[str, Any], node_id_to_canon: Dict[str, str]) -> None:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='alias'")
    if not cur.fetchone():
        return
    cur.execute("PRAGMA table_info(alias)")
    cols = {row[1] for row in cur.fetchall()}
    if "alias_name" in cols and "canonical_name" in cols:
        _load_aliases_by_name(cur, cols, kg_cache)
    elif "alias" in cols and "node_id" in cols and node_id_to_canon:
        _load_aliases_by_node_id(cur, kg_cache, node_id_to_canon)



def _extract_project_members_from_cursor(cur: Any, node_id_to_canon: Dict[str, str], project_token: str) -> set[str]:
    cur.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name='assertion'")
    if not cur.fetchone():
        return set()
    cur.execute("PRAGMA table_info(assertion)")
    cols = {row[1] for row in cur.fetchall()}
    text_c = "scope_text" if "scope_text" in cols else ("claim_text" if "claim_text" in cols else "''")
    obj_c = "object_node_id" if "object_node_id" in cols else "''"
    pred_c = "predicate" if "predicate" in cols else "''"
    status_clause = " AND canonical_status != 'rejected'" if "canonical_status" in cols else ""
    p_sql = (
        f"SELECT subject_node_id, {obj_c}, {text_c}, {pred_c} FROM assertion "
        f"WHERE {obj_c} LIKE ?{status_clause}"
    )
    like_pat = f"%{project_token}%"
    cur.execute(p_sql, (like_pat,))
    structural_preds = {"OWNS_OR_LEADS", "CONTRIBUTES_TO", "ASSIGNED_TO", "HAS_ROLE_IN", "HAS_ROLE", "SHAPES", "FEEDBACK_SOURCE_FOR"}
    members: set[str] = set()
    for sub_id, _, s_txt, pred in cur.fetchall():
        if pred and str(pred).upper() not in structural_preds:
            continue
        if s_txt and "is an identified stakeholder in" in str(s_txt).lower():
            continue
        if sub_id and (c_name := (node_id_to_canon.get(str(sub_id)) or node_id_to_canon.get(str(sub_id).lower()))):
            members.add(c_name.strip().lower())
    return members


def _partition_roster_members(
    members: set[str], kg_cache: Dict[str, Any], entity_factory: Optional[Callable[..., Any]] = None
) -> Dict[str, List[Any]]:
    roster: Dict[str, List[Any]] = {"partner_advisory": [], "apex_global": [], "all": []}
    seen = set()

    for m in members:
        ent = kg_cache.get(m)
        if ent and ent.canonical_name not in seen:
            seen.add(ent.canonical_name)
            roster["all"].append(ent)
            org_lower = ent.org.lower()
            if "partner_advisory" in org_lower:
                roster["partner_advisory"].append(ent)
            else:
                roster["apex_global"].append(ent)
    return roster


def _augment_sherpa_roster(
    all_rosters: Dict[str, Dict[str, List[Any]]],
    sherpa_members: set[str],
    kg_cache: Dict[str, Any],
    entity_factory: Optional[Callable[..., Any]],
) -> None:
    existing = all_rosters.setdefault("project_beacon", {"partner_advisory": [], "apex_global": [], "all": []})
    existing_names = {e.canonical_name.lower() for e in existing["all"]}

    for s_name in sherpa_members:
        if s_name in existing_names:
            continue
        ent = kg_cache.get(s_name)
        if ent and ent.canonical_name.lower() not in existing_names:
            existing_names.add(ent.canonical_name.lower())
            existing["all"].append(ent)
            target = existing["partner_advisory"] if "partner_advisory" in ent.org.lower() else existing["apex_global"]
            target.append(ent)
    all_rosters["sherpa"] = existing


def _load_project_rosters(
    cur: Any,
    kg_cache: Dict[str, Any],
    node_id_to_canon: Dict[str, str],
    entity_factory: Optional[Callable[..., Any]] = None,
) -> Dict[str, Dict[str, List[Any]]]:
    project_tokens = {
        "project_lens": "lens",
        "project_beacon": "sherpa",
        "project_nexus": "nexus",
        "project_jarvis": "jarvis",
        "apex_trans": "apex_trans",
        "project_atlas": "rewire",
    }
    all_rosters: Dict[str, Dict[str, List[Any]]] = {}
    for proj_key, token in project_tokens.items():
        members = _extract_project_members_from_cursor(cur, node_id_to_canon, token)
        roster = _partition_roster_members(members, kg_cache, entity_factory=entity_factory)
        if roster["all"]:
            all_rosters[proj_key] = roster
            all_rosters[token] = roster

    sherpa_names = [
        "David Childers", "Speaker_Alpha Muschett", "Marcus Balk", "Brandon Que",
        "Catalina Orostegui", "Jeffrey Glade", "Egle Tamasevice",
        "Andy Kilinskis", "Lillian Le", "Speaker_Beta Tharakan", "Cansu Miller"
    ]
    sherpa_members = {n.strip().lower() for n in sherpa_names}
    if "project_beacon" not in all_rosters:
        sherpa_roster = _partition_roster_members(sherpa_members, kg_cache, entity_factory=entity_factory)
        if sherpa_roster["all"]:
            all_rosters["project_beacon"] = sherpa_roster
            all_rosters["sherpa"] = sherpa_roster
    _augment_sherpa_roster(all_rosters, sherpa_members, kg_cache, entity_factory)
    return all_rosters



def load_knowledge_graph_data(
    db_path: Path,
    root: Path,
    runtime_root: Optional[Path],
    fallback_generation_id: str,
    entity_factory: Callable[..., Any],
    as_of_timestamp: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Loads entities, assertions, and aliases from graph SQLite database."""
    gen_id = resolve_active_generation_id(root, runtime_root, fallback_generation_id)

    try:
        from dual_pipeline.storage.governed_context import GovernedStorageContext
        conn = GovernedStorageContext.connect(db_path, readonly=True)
        cur = conn.cursor()
        kg_cache, node_id_to_canon, tbl = _load_nodes(cur, gen_id, entity_factory)
        if kg_cache or node_id_to_canon:
            _enrich_from_assertions(
                cur, kg_cache, node_id_to_canon, gen_id, entity_factory, as_of_timestamp, node_tbl=tbl
            )
            _load_aliases(cur, kg_cache, node_id_to_canon)
            kg_cache["__project_rosters__"] = _load_project_rosters(
                cur, kg_cache, node_id_to_canon, entity_factory=entity_factory
            )
        conn.close()
        return kg_cache, gen_id
    except Exception as e:
        logger.error("PreSynthesisEntityResolver: Knowledge graph load failed: %s", e)
        raise RuntimeError(f"Fail-Fast: Knowledge graph load failed: {e}") from e
