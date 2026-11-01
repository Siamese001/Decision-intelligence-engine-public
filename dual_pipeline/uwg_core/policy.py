"""Policy and validation helpers for Universal Write Gateway."""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

from .._validators import table_exists
from ..core import digest, read_json
from ..index import EmbeddingBackend
from .constants import (
    PAIR_FIELDS,
    UWG_POLICY_THRESHOLD_FIELDS,
    UWG_PROMOTION_POLICY_PATH,
    UWG_RELEASE_KINDS,
)


def _table_exists(connection: Any, table: str) -> bool:
    return table_exists(connection, table)


def _replayed_a2a_payload_governance(
    governance: object, reference: dict[str, Any]
) -> dict[str, Any]:
    """Render normalized A2A governance exactly as persisted on an assertion."""
    if not isinstance(governance, dict):
        raise RuntimeError("replayed A2A candidate has no retrieval governance")
    required = {
        "source_item_id",
        "retrieval_kind",
        "answer_use",
        "fact_promotion_eligible",
        "promotion_status",
    }
    result = {key: governance[key] for key in required}
    if governance.get("source_selection_bindings") is not None:
        result["a2a_source_span_bindings"] = governance["source_selection_bindings"]
    if governance.get("finalization_binding") is not None:
        result["a2a_finalization_binding"] = governance["finalization_binding"]
    result["a2a_finalization_reference"] = reference
    result["source_kind"] = "a2a_fact_vector_packet"
    return result


def _policy_error(message: str) -> ValueError:
    return ValueError(f"UWG promotion policy is invalid: {message}")


def _nonempty_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _policy_error(f"{label} must be a nonempty string")
    return value


def _number(value: Any, *, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _policy_error(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise _policy_error(f"{label} must be finite")
    if not minimum <= number <= maximum:
        raise _policy_error(f"{label} must be between {minimum} and {maximum}")
    return number


def _load_uwg_promotion_policy() -> dict[str, Any]:
    """Load the repository-owned qualification policy, fail closed on drift."""
    policy = read_json(UWG_PROMOTION_POLICY_PATH)
    if not isinstance(policy, dict):
        raise _policy_error("document must be an object")
    required_keys = {
        "schema_version",
        "policy_id",
        "policy_version",
        "owner",
        "thresholds",
    }
    if set(policy) != required_keys:
        raise _policy_error(
            "document keys must be exactly " + ", ".join(sorted(required_keys))
        )
    if policy["schema_version"] != "1.0.0":
        raise _policy_error("schema_version must be 1.0.0")
    for name in ("policy_id", "policy_version", "owner"):
        _nonempty_text(policy[name], label=name)
    thresholds = policy["thresholds"]
    if not isinstance(thresholds, dict) or set(thresholds) != set(UWG_POLICY_THRESHOLD_FIELDS):
        raise _policy_error(
            "thresholds must contain exactly " + ", ".join(UWG_POLICY_THRESHOLD_FIELDS)
        )
    if isinstance(thresholds["top_k"], bool) or not isinstance(thresholds["top_k"], int):
        raise _policy_error("thresholds.top_k must be an integer")
    if not 1 <= thresholds["top_k"] <= 100:
        raise _policy_error("thresholds.top_k must be between 1 and 100")
    for name in ("min_recall_at_k", "min_mrr"):
        if _number(thresholds[name], label=f"thresholds.{name}", minimum=0, maximum=1) <= 0:
            raise _policy_error(f"thresholds.{name} must be non-zero")
    _number(
        thresholds["max_latency_ms"],
        label="thresholds.max_latency_ms",
        minimum=1,
        maximum=float("inf"),
    )
    for name in (
        "max_negative_leakage_rate",
        "max_lane_leakage_rate",
        "max_acl_leakage_rate",
    ):
        _number(thresholds[name], label=f"thresholds.{name}", minimum=0, maximum=1)
    return policy


def _policy_binding(policy: dict[str, Any]) -> dict[str, str]:
    return {
        "policy_id": str(policy["policy_id"]),
        "policy_version": str(policy["policy_version"]),
        "policy_digest": digest(policy),
        "owner": str(policy["owner"]),
    }


def _utc_timestamp(value: Any, *, label: str) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty ISO-8601 timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a UTC offset")
    return parsed.astimezone(dt.timezone.utc)


def _iso_date(value: Any, *, label: str) -> dt.date:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty ISO-8601 date")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 date") from exc


def _correlation(*, source_id: str | None = None) -> dict[str, str | None]:
    return {
        "source_id": source_id,
        "candidate_id": None,
        "query_id": None,
        "graph_operation_id": None,
        "hitl_item_id": None,
    }


def _seal_receipt(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result.pop("receipt_digest", None)
    result["receipt_digest"] = digest(result)
    return result


def _verify_sealed_receipt(value: dict[str, Any], *, status: str) -> None:
    declared = value.get("receipt_digest")
    actual = digest({key: item for key, item in value.items() if key != "receipt_digest"})
    if declared != actual:
        raise ValueError("UWG release receipt digest mismatch")
    if value.get("status") != status:
        raise ValueError(f"UWG receipt must have status {status}")
    if value.get("release_kind") not in UWG_RELEASE_KINDS:
        raise ValueError("receipt is not a supported UWG release")


def _pair_binding(pair: dict[str, Any]) -> dict[str, Any]:
    missing = [name for name in PAIR_FIELDS if name not in pair]
    if missing:
        raise ValueError(f"graph/index pair is missing UWG binding fields: {missing}")
    return {name: pair[name] for name in PAIR_FIELDS}


def _same_pair(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return _pair_binding(left) == _pair_binding(right)


def _assert_backend_matches(pair: dict[str, Any], backend: EmbeddingBackend) -> None:
    expected = {
        "embedding_backend": backend.backend_id,
        "model_id": backend.model_id,
        "model_revision": backend.model_revision,
        "model_manifest_digest": backend.model_manifest_digest,
        "production_qualified": backend.production_qualified,
    }
    mismatches = {
        name: {"active": pair.get(name), "backend": value}
        for name, value in expected.items()
        if pair.get(name) != value
    }
    if mismatches:
        raise RuntimeError(
            "UWG backend must exactly match the active candidate generation: "
            f"{mismatches}"
        )
