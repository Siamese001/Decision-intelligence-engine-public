"""Validation and extraction of source retirement control packages."""
from __future__ import annotations

from typing import Any

from ..contracts import validate_artifact
from .constants import (
    _RELATED_TEXT_COLUMNS,
    _SUPERSESSION_DETACHMENT_TARGETS,
    _TARGET_KINDS,
    SOURCE_RETIREMENT_CONTROL_KEY,
    _id_list,
    _require_text,
)


def validate_source_retirement_control(control: object) -> dict[str, Any]:
    """Validate and canonicalize the source-bound retirement control extension."""
    required = {
        "schema_version",
        "target_source_id",
        "remove",
        "retain",
        "related_removals",
        "supersession_detachments",
        "source_updates",
        "source_version_updates",
        "reason",
    }
    if not isinstance(control, dict) or set(control) != required:
        raise ValueError("source-retirement control has an invalid shape")
    if control["schema_version"] != "1.0.0":
        raise ValueError("source-retirement control has an unsupported version")
    target_source_id = _require_text(
        control["target_source_id"], label="target_source_id"
    )

    scopes: dict[str, dict[str, list[str]]] = {}
    for scope in ("remove", "retain"):
        value = control[scope]
        if not isinstance(value, dict) or set(value) != set(_TARGET_KINDS):
            raise ValueError(f"source-retirement {scope} has an invalid target shape")
        scopes[scope] = {
            target_kind: _id_list(value[target_kind], label=f"{scope}.{target_kind}")
            for target_kind in _TARGET_KINDS
        }
    for target_kind in _TARGET_KINDS:
        if set(scopes["remove"][target_kind]) & set(scopes["retain"][target_kind]):
            raise ValueError(
                f"source-retirement removes and retains the same {target_kind}"
            )
    if not any(scopes["remove"].values()):
        raise ValueError("source-retirement control has no removal targets")

    raw_related = control["related_removals"]
    if not isinstance(raw_related, list):
        raise ValueError("source-retirement related_removals must be a list")
    related: list[dict[str, str]] = []
    seen_related: set[tuple[str, str]] = set()
    for item in raw_related:
        if not isinstance(item, dict) or set(item) != {
            "object_type",
            "object_id",
            "expected_text_fragment",
        }:
            raise ValueError("source-retirement related removal has an invalid shape")
        object_type = item["object_type"]
        if object_type not in _RELATED_TEXT_COLUMNS:
            raise ValueError("source-retirement related removal has an unsupported object type")
        object_id = _require_text(item["object_id"], label="related_removal.object_id")
        text = _require_text(
            item["expected_text_fragment"],
            label="related_removal.expected_text_fragment",
        )
        key = (object_type, object_id)
        if key in seen_related:
            raise ValueError("source-retirement related removals must be unique")
        seen_related.add(key)
        related.append(
            {
                "object_type": object_type,
                "object_id": object_id,
                "expected_text_fragment": text,
            }
        )
    related.sort(key=lambda item: (item["object_type"], item["object_id"]))

    raw_detachments = control["supersession_detachments"]
    if not isinstance(raw_detachments, list):
        raise ValueError("source-retirement supersession_detachments must be a list")
    detachments: list[dict[str, str]] = []
    seen_detachments: set[tuple[str, str]] = set()
    for item in raw_detachments:
        if not isinstance(item, dict) or set(item) != {
            "object_type",
            "object_id",
            "expected_superseded_by_object_id",
            "expected_text_fragment",
        }:
            raise ValueError("source-retirement supersession detachment has an invalid shape")
        object_type = item["object_type"]
        if object_type not in _SUPERSESSION_DETACHMENT_TARGETS:
            raise ValueError("source-retirement supersession detachment has an unsupported object type")
        object_id = _require_text(
            item["object_id"], label="supersession_detachment.object_id"
        )
        expected_successor = _require_text(
            item["expected_superseded_by_object_id"],
            label="supersession_detachment.expected_superseded_by_object_id",
        )
        if expected_successor not in scopes["remove"][object_type]:
            raise ValueError(
                "source-retirement supersession detachment must target a removed successor"
            )
        if object_id in scopes["remove"][object_type]:
            raise ValueError(
                "source-retirement supersession detachment cannot target a removed object"
            )
        key = (object_type, object_id)
        if key in seen_detachments:
            raise ValueError("source-retirement supersession detachments must be unique")
        seen_detachments.add(key)
        detachments.append(
            {
                "object_type": object_type,
                "object_id": object_id,
                "expected_superseded_by_object_id": expected_successor,
                "expected_text_fragment": _require_text(
                    item["expected_text_fragment"],
                    label="supersession_detachment.expected_text_fragment",
                ),
            }
        )
    detachments.sort(key=lambda item: (item["object_type"], item["object_id"]))

    raw_source_updates = control["source_updates"]
    if not isinstance(raw_source_updates, list) or not raw_source_updates:
        raise ValueError("source-retirement source_updates must be a nonempty list")
    source_updates: list[dict[str, Any]] = []
    seen_source_updates: set[str] = set()
    for item in raw_source_updates:
        required_update = {
            "source_id",
            "expected_source_name",
            "expected_source_posture",
            "expected_notes",
            "source_name",
            "source_posture",
            "notes",
        }
        if not isinstance(item, dict) or set(item) != required_update:
            raise ValueError("source-retirement source update has an invalid shape")
        update = {
            key: _require_text(item[key], label=f"source_update.{key}")
            for key in required_update
            if key != "expected_notes"
        }
        expected_notes = item["expected_notes"]
        if expected_notes is not None and not isinstance(expected_notes, str):
            raise ValueError(
                "source-retirement source_update.expected_notes must be text or null"
            )
        update["expected_notes"] = expected_notes
        if update["source_id"] in seen_source_updates:
            raise ValueError("source-retirement source updates must be unique")
        seen_source_updates.add(update["source_id"])
        source_updates.append(update)
    if target_source_id not in seen_source_updates:
        raise ValueError("source-retirement must update the retained target source posture")
    source_updates.sort(key=lambda item: item["source_id"])

    raw_version_updates = control["source_version_updates"]
    if not isinstance(raw_version_updates, list) or not raw_version_updates:
        raise ValueError("source-retirement source_version_updates must be a nonempty list")
    source_version_updates: list[dict[str, str]] = []
    seen_version_updates: set[str] = set()
    for item in raw_version_updates:
        required_update = {
            "source_version_id",
            "expected_scope_text",
            "expected_posture_text",
            "scope_text",
            "posture_text",
        }
        if not isinstance(item, dict) or set(item) != required_update:
            raise ValueError("source-retirement source-version update has an invalid shape")
        update = {
            key: _require_text(item[key], label=f"source_version_update.{key}")
            for key in required_update
        }
        if update["source_version_id"] in seen_version_updates:
            raise ValueError("source-retirement source-version updates must be unique")
        seen_version_updates.add(update["source_version_id"])
        source_version_updates.append(update)
    source_version_updates.sort(key=lambda item: item["source_version_id"])

    return {
        "schema_version": "1.0.0",
        "target_source_id": target_source_id,
        "remove": scopes["remove"],
        "retain": scopes["retain"],
        "related_removals": related,
        "supersession_detachments": detachments,
        "source_updates": source_updates,
        "source_version_updates": source_version_updates,
        "reason": _require_text(control["reason"], label="reason"),
    }


def source_retirement_control_from_package(package: object) -> dict[str, Any]:
    """Return the exact control extension from one validated Markdown package."""
    if not isinstance(package, dict):
        raise ValueError("source-retirement control package must be an object")
    validate_artifact(package)
    if not str(package.get("contract_id", "")).endswith(":markdown-source-intake:1.0.0"):
        raise ValueError("source-retirement control must use the Markdown source adapter")
    extensions = package.get("extensions")
    if not isinstance(extensions, dict):
        raise ValueError("source-retirement control package has no extensions")
    return validate_source_retirement_control(extensions.get(SOURCE_RETIREMENT_CONTROL_KEY))
