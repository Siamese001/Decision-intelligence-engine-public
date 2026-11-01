"""Active entity redirect resolution for the GraphEntityCatalog."""
from __future__ import annotations

import re
from typing import Any, Set

from dual_pipeline.core import normalize_name


def _build_redirect_map(
    connection: Any,
    known_entities: set[str],
) -> tuple[dict[str, str], dict[str, set[str]]]:
    """Trace active redirects to active destination entities."""
    redirect_map: dict[str, str] = {}
    normalized_ids: dict[str, set[str]] = {}
    for row in connection.execute(
        "SELECT from_node_id, to_node_id FROM entity_redirect WHERE active=1"
    ):
        from_id = row["from_node_id"]
        curr = row["to_node_id"]
        visited = {from_id}
        while curr not in known_entities:
            visited.add(curr)
            next_row = connection.execute(
                "SELECT to_node_id FROM entity_redirect WHERE from_node_id=? AND active=1",
                (curr,),
            ).fetchone()
            if not next_row or next_row["to_node_id"] in visited:
                break
            curr = next_row["to_node_id"]
        if curr in known_entities:
            redirect_map[from_id] = curr
            normalized_ids.setdefault(normalize_name(from_id), set()).add(curr)
    return redirect_map, normalized_ids


def _extract_acronym(canonical_name: str) -> str | None:
    """Extract acronym from camel-case or space-separated words."""
    words = re.findall(r"[A-Z][a-z]*", canonical_name)
    if len(words) < 2:
        return None
    acronym = "".join(w[0] for w in words).lower()
    return acronym if len(acronym) >= 2 else None


def _map_redirected_names(
    connection: Any,
    redirect_map: dict[str, str],
    names: dict[str, set[str]],
    aliases: dict[str, set[str]],
) -> None:
    """Populate name and alias lookup tables for redirected entities."""
    placeholders = ",".join("?" for _ in redirect_map)
    rows = connection.execute(
        f"SELECT node_id, canonical_name, coalesce(display_name, '') AS display_name FROM node WHERE node_id IN ({placeholders})",
        list(redirect_map.keys()),
    ).fetchall()
    for row in rows:
        dest_id = redirect_map[row["node_id"]]
        cname, dname = row["canonical_name"], row["display_name"]
        if cname:
            names.setdefault(normalize_name(cname), set()).add(dest_id)
            acronym = _extract_acronym(cname)
            if acronym:
                aliases.setdefault(acronym, set()).add(dest_id)
        if dname:
            names.setdefault(normalize_name(dname), set()).add(dest_id)
            aliases.setdefault(normalize_name(dname), set()).add(dest_id)


def load_active_redirects(
    connection: Any,
    known_entities: set[str],
    entity_ids_by_normalized_id: dict[str, set[str]],
    names: dict[str, set[str]],
    aliases: dict[str, set[str]],
) -> None:
    """Load active entity redirects and map merged entities to canonical targets."""
    has_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='entity_redirect'"
    ).fetchone()
    if not has_table:
        return

    redirect_map, normalized_ids = _build_redirect_map(connection, known_entities)
    for norm_id, dest_set in normalized_ids.items():
        entity_ids_by_normalized_id.setdefault(norm_id, set()).update(dest_set)

    if redirect_map:
        _map_redirected_names(connection, redirect_map, names, aliases)
