"""Provider-free validation for the decision-intelligence semantic spine v2."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
from .exceptions import DualPipelineValueError

CONTRACT_PATH = (
    ROOT / "governance" / "decision_intelligence" / "W13_SEMANTIC_SPINE_V2_CONTRACT.json"
)


class SemanticSpineV2Error(DualPipelineValueError):
    """Raised when a target semantic edge is underspecified or invalid."""


def _contract() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def validate_relation(
    *,
    relation: str,
    source_type: str,
    target_type: str,
    qualifiers: dict[str, Any],
) -> dict[str, Any]:
    """Validate and normalize one target-vocabulary relationship."""

    contract = _contract()
    definition = contract["relations"].get(relation)
    if definition is None:
        raise SemanticSpineV2Error(f"unsupported v2 relation: {relation}")
    if source_type not in definition["source"]:
        raise SemanticSpineV2Error(f"{relation} does not allow source type {source_type}")
    if target_type not in definition["target"]:
        raise SemanticSpineV2Error(f"{relation} does not allow target type {target_type}")
    if not isinstance(qualifiers, dict):
        raise SemanticSpineV2Error("relation qualifiers must be an object")
    source_assertion_ids = qualifiers.get("source_assertion_ids")
    if (
        not isinstance(source_assertion_ids, list)
        or not source_assertion_ids
        or not all(
        isinstance(value, str) and value.strip() for value in source_assertion_ids
        )
    ):
        raise SemanticSpineV2Error("source_assertion_ids must contain one or more IDs")
    missing = [key for key in definition["qualifiers"] if key not in qualifiers]
    if missing:
        raise SemanticSpineV2Error(f"{relation} is missing qualifiers: {', '.join(missing)}")
    if relation == "supports" and not isinstance(qualifiers.get("support_scope_id"), str):
        raise SemanticSpineV2Error("supports requires a named support_scope_id")
    if relation == "supports" and qualifiers["support_scope_id"].strip().upper() in {
        "",
        "UNSPECIFIED",
        "ALL",
    }:
        raise SemanticSpineV2Error("supports cannot use an unspecified or blanket scope")
    return {
        "relation": relation,
        "source_type": source_type,
        "target_type": target_type,
        "qualifiers": {key: qualifiers[key] for key in sorted(qualifiers)},
    }
