"""Dynamic entity resolution against SQLite active nodes on word boundaries."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Sequence, Set

from .constants import _entities
from .thought_leadership import extract_audience_stakeholders, strip_audience_clause

_COMMON_NAME_STOPWORDS = {
    "the", "and", "for", "with", "project", "workstream", "status", "update",
    "meeting", "phase", "team", "lead", "view", "role", "data", "test",
    "debrief", "pause", "review", "sprint", "power", "light", "initiative",
    "charter", "roadmap", "plan", "notes", "action", "item", "alignment",
    "functional", "technical", "architecture", "design", "governance", "security",
    "observability",
}


def _append_unseen_nodes(
    matched_nodes: list[Any], seen_ids: set[str], matches: list[dict[str, Any]]
) -> None:
    for r in matched_nodes:
        nid = r["node_id"]
        if nid not in seen_ids:
            seen_ids.add(nid)
            matches.append({
                "node_id": nid, "canonical_name": r["canonical_name"],
                "display_name": r["display_name"], "node_type": r["node_type"],
            })


def _add_matched_token_nodes(
    token_to_nodes: dict[str, list[Any]], q_low: str, seen_ids: set[str], matches: list[dict[str, Any]]
) -> None:
    for token, matched_nodes in token_to_nodes.items():
        if len(token) >= 3 and re.search(r"\b" + re.escape(token) + r"\b", q_low):
            if 1 <= len({nr["node_id"] for nr in matched_nodes}) <= 5:
                _append_unseen_nodes(matched_nodes, seen_ids, matches)


def _load_alias_rows(connection: Any) -> list[dict[str, Any]]:
    alias_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name IN ('alias', 'canonical_alias')"
    ).fetchone()
    if not alias_table:
        return []
    for sql in (
        "SELECT node_id, alias FROM alias",
        "SELECT entity_id AS node_id, display_alias AS alias FROM canonical_alias WHERE active=1",
    ):
        try:
            cur = connection.execute(sql)
            acols = [d[0] for d in cur.description]
            return [dict(zip(acols, r)) if not isinstance(r, dict) and not hasattr(r, "keys") else dict(r) for r in cur.fetchall()]
        except Exception:
            continue
    return []


def _load_active_node_rows(connection: Any) -> list[dict[str, Any]]:
    cursor = connection.execute(
        "SELECT node_id, canonical_name, display_name, node_type FROM node WHERE active = 1"
    )
    cols = [d[0] for d in cursor.description]
    return [
        dict(zip(cols, r)) if not isinstance(r, dict) and not hasattr(r, "keys") else dict(r)
        for r in cursor.fetchall()
    ]


def _match_exact_node_names(
    node_rows: list[dict[str, Any]],
    q_low: str,
    auth_set: set[str] | None,
    seen_ids: set[str],
    matches: list[dict[str, Any]],
) -> None:
    for r in node_rows:
        nid = r["node_id"]
        if auth_set is not None and nid not in auth_set:
            continue
        cname = (r["canonical_name"] or "").strip().lower()
        dname = (r["display_name"] or "").strip().lower()
        if (
            len(cname) >= 3
            and cname not in _COMMON_NAME_STOPWORDS
            and re.search(r"\b" + re.escape(cname) + r"\b", q_low)
        ) or (
            len(dname) >= 3
            and dname not in _COMMON_NAME_STOPWORDS
            and re.search(r"\b" + re.escape(dname) + r"\b", q_low)
        ):
            _append_unseen_nodes([r], seen_ids, matches)


def _extract_person_tokens(r: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for name in ((r["canonical_name"] or "").strip(), (r["display_name"] or "").strip()):
        words = re.findall(r"[A-Za-z0-9]+", name)
        if words and len(words[0]) >= 3 and words[0].lower() not in _COMMON_NAME_STOPWORDS:
            tokens.add(words[0].lower())
        for w in words:
            wl = w.lower()
            if len(wl) >= 4 and wl not in _COMMON_NAME_STOPWORDS:
                tokens.add(wl)
    return tokens


def _add_alias_tokens(
    connection: Any,
    node_by_id: dict[str, dict[str, Any]],
    auth_set: set[str] | None,
    token_to_nodes: dict[str, list[Any]],
) -> None:
    try:
        for ar in _load_alias_rows(connection):
            anid = ar["node_id"]
            if auth_set is not None and anid not in auth_set:
                continue
            alias_str = (ar["alias"] or "").strip().lower()
            if len(alias_str) >= 3 and alias_str not in _COMMON_NAME_STOPWORDS and anid in node_by_id:
                token_to_nodes.setdefault(alias_str, []).append(node_by_id[anid])
    except Exception:
        pass


def _build_person_and_alias_tokens(
    connection: Any,
    node_rows: list[dict[str, Any]],
    auth_set: set[str] | None,
) -> dict[str, list[Any]]:
    token_to_nodes: dict[str, list[Any]] = {}
    node_by_id = {r["node_id"]: r for r in node_rows}
    for r in node_rows:
        nid = r["node_id"]
        if (auth_set is not None and nid not in auth_set) or "person" not in str(r.get("node_type") or "").lower():
            continue
        for token in _extract_person_tokens(r):
            token_to_nodes.setdefault(token, []).append(r)

    _add_alias_tokens(connection, node_by_id, auth_set, token_to_nodes)
    return token_to_nodes


def _append_static_entity_nodes(
    connection: Any,
    question: str,
    auth_set: set[str] | None,
    seen_ids: set[str],
    matches: list[dict[str, Any]],
) -> None:
    static_entities = _entities(question, default_to_nee=not bool(matches))
    if not static_entities:
        return
    cur = connection.execute(
        """SELECT node_id, canonical_name, display_name, node_type FROM node
           WHERE active=1 AND lower(canonical_name) IN ({})
           ORDER BY node_id""".format(",".join("?" for _ in static_entities)),
        [value.casefold() for value in static_entities],
    )
    scols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        rdict = dict(zip(scols, row)) if not isinstance(row, dict) and not hasattr(row, "keys") else dict(row)
        nid = rdict["node_id"]
        if (auth_set is None or nid in auth_set) and nid not in seen_ids:
            seen_ids.add(nid)
            matches.append(rdict)


def resolve_dynamic_entity_matches(
    connection: Any,
    question: str,
    authorized_nodes: Set[str] | Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Resolves matching entities from SQLite active nodes using word boundaries."""
    auth_set = set(authorized_nodes) if authorized_nodes else None
    seen_ids: set[str] = set()
    matches: list[dict[str, Any]] = []
    q_low = question.lower()
    try:
        node_rows = _load_active_node_rows(connection)
        _match_exact_node_names(node_rows, q_low, auth_set, seen_ids, matches)
        token_to_nodes = _build_person_and_alias_tokens(connection, node_rows, auth_set)
        _add_matched_token_nodes(token_to_nodes, q_low, seen_ids, matches)
    except Exception:
        pass

    try:
        _append_static_entity_nodes(connection, question, auth_set, seen_ids, matches)
    except Exception:
        pass
    return matches


def _resolve_dynamic_for_coverage(question: str, active: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    if not active or not active.get("graph_path"):
        return None
    try:
        from dual_pipeline.core import connect_sqlite
        from dual_pipeline.runtime_root import resolve_active_graph_path

        gpath = Path(str(active["graph_path"]))
        if not gpath.is_file():
            resolved = resolve_active_graph_path(enforce_integrity=False)
            if resolved is not None and resolved.is_file():
                gpath = resolved
        conn = connect_sqlite(gpath, readonly=True)
        try:
            return resolve_dynamic_entity_matches(conn, question, authorized_nodes=None)
        finally:
            conn.close()
    except Exception:
        return None


def _dedup_casefold(values: list[str]) -> list[str]:
    unique_values: list[str] = []
    seen_cf: set[str] = set()
    for v in values:
        if v and v.casefold() not in seen_cf:
            seen_cf.add(v.casefold())
            unique_values.append(v)
    return unique_values


def _is_audience_only_entity(
    name: str,
    audience_tokens: set[str],
    subject_question: str,
) -> bool:
    if not audience_tokens:
        return False
    ent_toks = {t.casefold() for t in re.findall(r"[A-Za-z0-9]+", str(name)) if len(t) >= 3}
    if not (ent_toks & audience_tokens):
        return False
    sub_cf = subject_question.casefold()
    return not any(re.search(r"\b" + re.escape(t) + r"\b", sub_cf) for t in ent_toks)


def resolve_named_coverage_subjects(
    question: str,
    plan: dict[str, Any],
    active: dict[str, Any] | None = None,
) -> list[str]:
    """Capture deterministic named subjects without using generic plan questions."""
    subject_question = strip_audience_clause(question)
    audience_tokens = {
        tok.casefold()
        for name in extract_audience_stakeholders(question)
        for tok in re.findall(r"[A-Za-z0-9]+", name)
        if len(tok) >= 3
    }
    values: list[str] = []
    dynamic_entities = plan.get("dynamic_entities") or _resolve_dynamic_for_coverage(subject_question, active)
    for ent in dynamic_entities or ():
        cname = ent.get("canonical_name") or ent.get("display_name")
        if cname and not _is_audience_only_entity(cname, audience_tokens, subject_question):
            values.append(cname)
    for ent in plan.get("primary_entities", []):
        if ent and not _is_audience_only_entity(str(ent), audience_tokens, subject_question):
            values.append(ent)
    cleaned_subject_q = re.sub(r"\s+[A-Z]\d?\s*[.?]?$", "", subject_question)
    values.extend(re.findall(r"\b[A-Z][A-Z0-9]{1,15}\b", cleaned_subject_q))
    unique_values = _dedup_casefold(values)
    if len(unique_values) > 1 and "NEE" in unique_values and not re.search(r"\bNEE\b", subject_question):
        unique_values.remove("NEE")
    return unique_values


def _collect_audience_tokens(audience_entities: Sequence[str] | None) -> set[str]:
    return {
        t.casefold()
        for name in (audience_entities or ())
        for t in re.findall(r"[A-Za-z0-9]+", str(name))
        if len(t) >= 3
    }


def _record_missing_entity_gap_if_needed(
    packet: dict[str, Any] | None,
    ent: str,
    ent_tokens: set[str],
    audience_toks: set[str],
) -> None:
    if packet is None or (audience_toks and ent_tokens.issubset(audience_toks)):
        return
    gap_msg = f"No approved factual current-state evidence found for {ent}."
    if gap_msg not in packet.setdefault("gaps", []):
        packet["gaps"].append(gap_msg)


def select_effective_factual_records(
    factual_records: list[dict[str, Any]],
    primary_entities: list[str],
    max_per_entity: int = 3,
    packet: dict[str, Any] | None = None,
    audience_entities: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Select entity-balanced factual records for multi-entity or single-entity queries."""
    audience_toks = _collect_audience_tokens(audience_entities)
    def _record_tokens(item: dict[str, Any]) -> set[str]:
        record = item["record"]
        return set(re.findall(r"[a-z0-9]+", f"{record['title']} {record['retrieval_text']}".casefold()))

    if len(primary_entities) > 1:
        effective: list[dict[str, Any]] = []
        for ent in primary_entities:
            ent_tokens = {
                t for t in re.findall(r"[a-z0-9]+", ent.casefold())
                if t not in {"the", "and", "or", "in", "on", "at", "to", "for", "of"} and len(t) >= 3
            }
            if not ent_tokens:
                continue
            ent_records = [it for it in factual_records if _record_tokens(it) & ent_tokens]
            if ent_records:
                for item in ent_records[:max_per_entity]:
                    if item not in effective:
                        effective.append(item)
            else:
                _record_missing_entity_gap_if_needed(packet, ent, ent_tokens, audience_toks)
        return effective or factual_records

    query_entity_tokens = {
        t for ent in primary_entities
        for t in re.findall(r"[a-z0-9]+", ent.casefold())
        if t not in {"the", "and", "or", "in", "on", "at", "to", "for", "of"}
    }
    matching = [it for it in factual_records if _record_tokens(it) & query_entity_tokens] if query_entity_tokens else factual_records
    return matching or factual_records

