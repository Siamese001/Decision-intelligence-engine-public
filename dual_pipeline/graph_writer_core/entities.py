"""Entity and external concept creation for GovernedGraphWriter."""
from __future__ import annotations

import json
from storage.database import sqlite3
from typing import Any

from ..core import stable_id
from ..node_type_policy import require_creatable_node_type


def _validate_internal_entity_payload(
    payload: dict[str, Any], operation: dict[str, Any], connection: sqlite3.Connection
) -> dict[str, Any]:
    if set(payload) - {"idempotent_upsert", "allow_existing"} != {"candidate", "disposition", "entity", "entity_kind"}:
        raise RuntimeError("internal entity operation payload has an invalid shape")
    if payload["disposition"] != "CANDIDATE" or payload["entity_kind"] != "internal":
        raise RuntimeError("internal entity must be created only with candidate posture")
    entity = payload["entity"]
    expected_keys = {
        "canonical_name", "description", "entity_id", "node_type", "status",
        "source_id", "source_version_id", "source_stable_identifier",
        "source_profile_digest", "evidence_ids", "affected_candidate_ids", "possible_duplicate_labels",
    }
    if not isinstance(entity, dict) or set(entity) != expected_keys:
        raise RuntimeError("internal entity operation has an invalid proposal")
    required_str_keys = (
        "canonical_name", "description", "entity_id", "node_type", "status",
        "source_id", "source_version_id", "source_stable_identifier", "source_profile_digest",
    )
    if operation["target_id"] != entity["entity_id"] or any(
        not isinstance(entity[key], str) or not entity[key].strip() for key in required_str_keys
    ):
        raise RuntimeError("internal entity proposal is incomplete")
    try:
        require_creatable_node_type(connection, entity["node_type"])
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    candidate = payload["candidate"]
    if not any(
        mention.get("reported_text") == entity["canonical_name"]
        and mention.get("stable_identifier") == entity["source_stable_identifier"]
        for mention in candidate.get("entity_mentions", [])
        if isinstance(mention, dict)
    ):
        raise RuntimeError("internal entity proposal is not anchored to its candidate")
    return entity


def _handle_existing_node_upsert(
    connection: sqlite3.Connection,
    operation: dict[str, Any],
    payload: dict[str, Any],
    existing_node: Any,
    entity: dict[str, Any],
    now: str,
) -> bool:
    is_upsert = operation.get("idempotent_upsert") or operation.get("allow_existing") or payload.get("idempotent_upsert")
    if not (
        is_upsert
        and existing_node["node_type"] == entity["node_type"]
        and existing_node["canonical_name"] == entity["canonical_name"]
        and int(existing_node["active"] or 0) == 1
    ):
        return False
    new_desc = (entity.get("description") or "").strip()
    existing_desc = (existing_node["description"] or "").strip()
    should_update_desc = new_desc and (
        not existing_desc
        or "unresolved" in existing_desc.lower()
        or (len(new_desc) > len(existing_desc) and "unresolved" not in new_desc.lower())
    )
    if should_update_desc:
        connection.execute(
            "UPDATE node SET description=?, updated_at=? WHERE node_id=?",
            (new_desc, now, operation["target_id"]),
        )
    else:
        connection.execute(
            "UPDATE node SET updated_at=? WHERE node_id=?",
            (now, operation["target_id"]),
        )
    return True


class EntityOpsMixin:
    """Provides node creation and external concept provisioning methods."""

    @staticmethod
    def _ensure_external_concept_node_type(
        connection: sqlite3.Connection, now: str
    ) -> None:
        """Provision only the fixed, additive type required by this writer."""
        expected = {
            "node_type_id": "external_concept",
            "canonical_name": "External Concept",
            "definition": (
                "A vendor and source-scoped concept retained without an implied "
                "equivalence to an internal NEE entity."
            ),
            "parent_type_id": None,
            "required_properties_json": json.dumps(
                ["source_id", "source_version_id", "vendor", "normalized_label"],
                separators=(",", ":"),
            ),
            "optional_properties_json": json.dumps(
                ["no_internal_equivalence"], separators=(",", ":")
            ),
            "identity_rule": (
                "Deterministic ID from source_id, vendor, and normalized label; "
                "never auto-merge or infer internal equivalence."
            ),
            "registry_state": "active",
            "replacement_type_id": None,
            "retrieval_use": "Blocked identity anchor; retrieval derives only from source-bound assertions.",
        }
        connection.execute(
            """INSERT OR IGNORE INTO node_type_registry
               (node_type_id,canonical_name,definition,parent_type_id,required_properties_json,
                optional_properties_json,identity_rule,example_node_id,registry_state,
                replacement_type_id,retrieval_use,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                expected["node_type_id"],
                expected["canonical_name"],
                expected["definition"],
                expected["parent_type_id"],
                expected["required_properties_json"],
                expected["optional_properties_json"],
                expected["identity_rule"],
                None,
                expected["registry_state"],
                expected["replacement_type_id"],
                expected["retrieval_use"],
                now,
            ),
        )
        row = connection.execute(
            """SELECT node_type_id,canonical_name,definition,parent_type_id,required_properties_json,
                      optional_properties_json,identity_rule,registry_state,replacement_type_id,
                      retrieval_use
                 FROM node_type_registry WHERE node_type_id='external_concept'"""
        ).fetchone()
        if row is None or any(row[key] != value for key, value in expected.items()):
            raise RuntimeError("external concept node type collides with a different ontology definition")

    @classmethod
    def _create_external_concept(
        cls,
        connection: sqlite3.Connection,
        operation: dict[str, Any],
        payload: dict[str, Any],
        source: dict[str, Any],
        now: str,
    ) -> None:
        if set(payload) - {"idempotent_upsert", "allow_existing"} != {"candidate", "disposition", "entity", "entity_kind"}:
            raise RuntimeError("external concept operation payload has an invalid shape")
        if payload["disposition"] != "CANDIDATE" or payload["entity_kind"] != "external":
            raise RuntimeError("external concept must be created only with candidate posture")
        candidate = payload["candidate"]
        entity = payload["entity"]
        required_entity_keys = {
            "external_concept_id",
            "identity_scope",
            "no_internal_equivalence",
            "source_id",
            "source_version_id",
            "vendor",
            "reported_name",
            "normalized_label",
        }
        if not isinstance(entity, dict) or set(entity) != required_entity_keys:
            raise RuntimeError("external concept operation has an invalid entity proposal")
        if (
            operation["target_id"] != entity["external_concept_id"]
            or entity["identity_scope"] != "source_scoped_external"
            or entity["no_internal_equivalence"] is not True
            or entity["source_id"] != source["source_id"]
            or entity["source_version_id"] != source["source_version_id"]
            or not isinstance(entity["vendor"], str)
            or not entity["vendor"].strip()
            or not isinstance(entity["reported_name"], str)
            or not entity["reported_name"].strip()
            or not isinstance(entity["normalized_label"], str)
            or not entity["normalized_label"].strip()
        ):
            raise RuntimeError("external concept proposal is not source-scoped and immutable")
        expected_id = stable_id(
            "external-concept",
            entity["source_id"],
            entity["vendor"],
            entity["normalized_label"],
            length=28,
        )
        if expected_id != entity["external_concept_id"]:
            raise RuntimeError("external concept proposal has a non-deterministic identity")
        if not any(
            mention.get("reported_text") == entity["reported_name"]
            for mention in candidate.get("entity_mentions", [])
            if isinstance(mention, dict)
        ):
            raise RuntimeError("external concept proposal is not anchored to its candidate mention")
        existing_node = connection.execute(
            "SELECT node_type, canonical_name, active FROM node WHERE node_id=?",
            (operation["target_id"],),
        ).fetchone()
        if existing_node is not None:
            if (
                (operation.get("idempotent_upsert") or operation.get("allow_existing") or payload.get("idempotent_upsert"))
                and existing_node["node_type"] == "external_concept"
                and existing_node["canonical_name"] == entity["reported_name"]
                and int(existing_node["active"] or 0) == 1
            ):
                connection.execute(
                    "UPDATE node SET updated_at=? WHERE node_id=?",
                    (now, operation["target_id"]),
                )
                return
            raise RuntimeError("external concept target already exists")
        cls._ensure_external_concept_node_type(connection, now)
        description = (
            f"Source-scoped {entity['vendor']} concept from {entity['source_id']}. "
            "No internal equivalence is asserted."
        )
        connection.execute(
            """INSERT INTO node
               (node_id,node_type,canonical_name,display_name,description,status,active,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                operation["target_id"],
                "external_concept",
                entity["reported_name"],
                entity["reported_name"],
                description,
                "candidate source-scoped external concept; no internal equivalence",
                1,
                now,
                now,
            ),
        )
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='node_fts'"
        ).fetchone():
            connection.execute(
                "DELETE FROM node_fts WHERE node_id=?",
                (operation["target_id"],),
            )
            connection.execute(
                "INSERT INTO node_fts (node_id,canonical_name,display_name,description) VALUES (?,?,?,?)",
                (
                    operation["target_id"],
                    entity["reported_name"],
                    entity["reported_name"],
                    description,
                ),
            )

        connection.execute(
            """INSERT INTO node_state
               (node_id,lifecycle_state,validation_state,currentness_state,approval_state,rag_state,
                resolution_state,retrieval_eligibility,publication_state,review_state,legacy_status_text,
                intentional_orphan,intentional_orphan_reason,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                operation["target_id"],
                "active",
                "needs_review",
                "current",
                "candidate",
                "amber",
                "unresolved",
                "blocked",
                "internal",
                "pending",
                "source_scoped_external_concept",
                1,
                "No internal equivalence or navigational relationship is asserted.",
                now,
            ),
        )

    @staticmethod
    def _create_internal_entity(
        connection: sqlite3.Connection,
        operation: dict[str, Any],
        payload: dict[str, Any],
        now: str,
    ) -> None:
        """Create an exact HITL-proposed internal entity with candidate posture."""
        entity = _validate_internal_entity_payload(payload, operation, connection)
        existing_node = connection.execute(
            "SELECT node_type, canonical_name, description, active FROM node WHERE node_id=?",
            (operation["target_id"],),
        ).fetchone()
        if existing_node is not None:
            if _handle_existing_node_upsert(connection, operation, payload, existing_node, entity, now):
                return
            raise RuntimeError("internal entity target already exists")
        connection.execute(
            """INSERT INTO node
               (node_id,node_type,canonical_name,display_name,description,status,active,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                entity["entity_id"],
                entity["node_type"],
                entity["canonical_name"],
                entity["canonical_name"],
                entity["description"],
                entity["status"],
                1,
                now,
                now,
            ),
        )
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='node_fts'"
        ).fetchone():
            connection.execute(
                "DELETE FROM node_fts WHERE node_id=?",
                (entity["entity_id"],),
            )
            connection.execute(
                "INSERT INTO node_fts (node_id,canonical_name,display_name,description) VALUES (?,?,?,?)",
                (
                    entity["entity_id"],
                    entity["canonical_name"],
                    entity["canonical_name"],
                    entity["description"],
                ),
            )
        connection.execute(
            """INSERT INTO node_state
               (node_id,lifecycle_state,validation_state,currentness_state,approval_state,rag_state,
                resolution_state,retrieval_eligibility,publication_state,review_state,legacy_status_text,
                intentional_orphan,intentional_orphan_reason,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                entity["entity_id"],
                "active",
                "needs_review",
                "current",
                "candidate",
                "amber",
                "resolved",
                "eligible",
                "internal",
                "reviewed",
                entity["status"],
                0,
                None,
                now,
            ),
        )
