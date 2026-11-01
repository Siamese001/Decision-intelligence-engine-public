"""Entity catalog loaded from the active knowledge graph."""
from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.core import connect_sqlite, normalize_name
from dual_pipeline.index import CLASSIFICATION_RANK

from .records import EntityRecord
from .redirects import load_active_redirects


def _filter_admitted_entities(
    connection,
    classification: str | None,
    audiences: list[str] | None,
) -> set[str] | None:
    if classification is None and audiences is None:
        return None
    if classification not in CLASSIFICATION_RANK or not audiences:
        raise ValueError("authorized entity catalog requires classification and audiences")
    admitted = set()
    requested_audiences = set(audiences)
    for assertion in connection.execute(
        "SELECT subject_node_id,object_node_id,classification,allowed_audiences_json FROM assertion"
    ):
        try:
            assertion_audiences = set(json.loads(assertion["allowed_audiences_json"]))
        except (TypeError, json.JSONDecodeError):
            assertion_audiences = set()
        if (
            assertion["classification"] in CLASSIFICATION_RANK
            and CLASSIFICATION_RANK[assertion["classification"]] <= CLASSIFICATION_RANK[classification]
            and bool(requested_audiences & assertion_audiences)
        ):
            admitted.add(assertion["subject_node_id"])
            if assertion["object_node_id"]:
                admitted.add(assertion["object_node_id"])
    for row in connection.execute(
        "SELECT node_id FROM node WHERE active=1 AND node_type != 'external_concept' "
        "AND NOT EXISTS ("
        "SELECT 1 FROM assertion a WHERE a.subject_node_id = node.node_id OR a.object_node_id = node.node_id"
        ")"
    ):
        admitted.add(row["node_id"])
    return admitted


def _index_identifier(index: dict[str, set[str]], raw_id: str, entity_id: str) -> None:
    norm_id = normalize_name(raw_id)
    if norm_id:
        index.setdefault(norm_id, set()).add(entity_id)
    if ":" in raw_id:
        slug_part = normalize_name(raw_id.split(":", 1)[1])
        if slug_part:
            index.setdefault(slug_part, set()).add(entity_id)


def _load_audit_stable_identifiers(
    connection: Any,
    entities: dict[str, EntityRecord],
    index: dict[str, set[str]],
) -> None:
    try:
        import json
        for row in connection.execute(
            "SELECT record_id, new_value_json FROM audit_event "
            "WHERE action='create_entity' AND record_type='entity' AND new_value_json IS NOT NULL"
        ):
            ent_id = row["record_id"]
            if ent_id not in entities:
                continue
            src_sid = (json.loads(row["new_value_json"]).get("entity") or {}).get("source_stable_identifier")
            if isinstance(src_sid, str) and src_sid.strip():
                _index_identifier(index, src_sid, ent_id)
    except Exception:
        pass


class GraphEntityCatalog:
    def __init__(
        self,
        graph_path: Path,
        *,
        classification: str | None = None,
        audiences: list[str] | None = None,
    ):
        self.graph_path = graph_path
        self.entities: dict[str, EntityRecord] = {}
        self.entity_ids_by_normalized_id: dict[str, set[str]] = {}
        self.names: dict[str, set[str]] = {}
        self.aliases: dict[str, set[str]] = {}
        connection = connect_sqlite(graph_path, readonly=True)
        try:
            admitted_entities = _filter_admitted_entities(connection, classification, audiences)
            for row in connection.execute(
                "SELECT node_id,canonical_name,node_type,coalesce(description,'') AS description, "
                "coalesce(status,'canonical') AS status "
                "FROM node WHERE active=1 ORDER BY node_id"
            ):
                if row["node_type"] == "external_concept":
                    continue
                if admitted_entities is not None and row["node_id"] not in admitted_entities:
                    continue
                record = EntityRecord(
                    entity_id=row["node_id"],
                    canonical_name=row["canonical_name"],
                    node_type=row["node_type"],
                    description=row["description"],
                    normalized_name=normalize_name(row["canonical_name"]),
                    status=row["status"],
                )
                self.entities[record.entity_id] = record
                _index_identifier(self.entity_ids_by_normalized_id, record.entity_id, record.entity_id)
                self.names.setdefault(record.normalized_name, set()).add(record.entity_id)
            for row in connection.execute(
                "SELECT normalized_alias,entity_id FROM canonical_alias "
                "WHERE active=1 ORDER BY normalized_alias,entity_id"
            ):
                if row["entity_id"] not in self.entities:
                    continue
                self.aliases.setdefault(row["normalized_alias"], set()).add(row["entity_id"])
            _load_audit_stable_identifiers(connection, self.entities, self.entity_ids_by_normalized_id)
            load_active_redirects(
                connection,
                set(self.entities),
                self.entity_ids_by_normalized_id,
                self.names,
                self.aliases,
            )
        finally:
            connection.close()


    def _narrow_by_declared_type(
        self, matches: list[str], declared_type: str | None
    ) -> list[str]:
        if len(matches) <= 1 or not declared_type:
            return matches
        typed = [
            eid for eid in matches
            if eid in self.entities and self.entities[eid].node_type == declared_type
        ]
        return typed if typed else matches

    def exact(
        self,
        reported: str,
        stable_identifier: str | None,
        declared_type: str | None = None,
    ) -> tuple[list[str], str | None]:
        if stable_identifier:
            normalized_identifier = normalize_name(stable_identifier)
            candidate_keys = {normalized_identifier}
            if ":" in stable_identifier:
                slug_key = normalize_name(stable_identifier.split(":", 1)[1])
                if slug_key:
                    candidate_keys.add(slug_key)
            stable_matches_set: set[str] = set()
            for key in candidate_keys:
                stable_matches_set |= self.entity_ids_by_normalized_id.get(key, set())
                stable_matches_set |= self.aliases.get(key, set())
                stable_matches_set |= self.names.get(key, set())
            stable_matches = sorted(stable_matches_set)
            if stable_matches:
                return self._narrow_by_declared_type(stable_matches, declared_type), "stable_identifier"
        normalized = normalize_name(reported)
        name_matches = sorted(self.names.get(normalized, set()))
        if name_matches:
            return self._narrow_by_declared_type(name_matches, declared_type), "exact_normalized_name"
        alias_matches = sorted(self.aliases.get(normalized, set()))
        if alias_matches:
            return self._narrow_by_declared_type(alias_matches, declared_type), "governed_alias"
        return [], None
