"""Helper functions and validators for query L1 shadow receipts."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..core import artifact_digest, digest
from ..l1_control import L1Action, create_action
from .constants import LANES, SHADOW_MODES
from .models import ShadowReceiptError


def _digest(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ShadowReceiptError("L1_INTERNAL_INVALID", f"{label} must be a lowercase SHA-256 digest")
    return value


def _canonical(value: Mapping[str, Any], label: str) -> str:
    canonical = value.get("canonical_digest")
    if not isinstance(canonical, Mapping):
        raise ShadowReceiptError("L1_INTERNAL_INVALID", f"{label} is not sealed")
    result = _digest(canonical.get("value"), f"{label} canonical digest")
    if artifact_digest(dict(value)) != result:
        raise ShadowReceiptError("L1_INTERNAL_INVALID", f"{label} canonical digest does not verify")
    return result


def _require_mode(mode: str) -> None:
    if mode not in SHADOW_MODES:
        raise ShadowReceiptError("L1_POLICY_BLOCKED", f"unsupported shadow mode: {mode}")


def _pair(active_pair: Mapping[str, Any]) -> dict[str, str]:
    required = {
        "generation_id",
        "graph_sha256",
        "sidecar_sha256",
        "embedding_backend",
        "model_id",
        "model_revision",
    }
    if not required <= set(active_pair):
        raise ShadowReceiptError("L1_INTERNAL_INVALID", "active pair is incomplete")
    result = {key: active_pair.get(key) for key in required}
    for key, value in result.items():
        if not isinstance(value, str) or not value:
            raise ShadowReceiptError("L1_INTERNAL_INVALID", f"active pair field is invalid: {key}")
    for key in ("graph_sha256", "sidecar_sha256"):
        _digest(result[key], f"active pair {key}")
    return {
        "generation_id": result["generation_id"],
        "graph_sha256": result["graph_sha256"],
        "sidecar_sha256": result["sidecar_sha256"],
        "backend": result["embedding_backend"],
        "model_id": result["model_id"],
        "model_revision": result["model_revision"],
    }


def _selected_records(report: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    values = report.get("selected_record_ids_by_lane")
    if not isinstance(values, Mapping):
        raise ShadowReceiptError("L1_INTERNAL_INVALID", "evidence report has no selected record map")
    normalized: dict[str, tuple[str, ...]] = {}
    for lane in LANES:
        ids = values.get(lane, [])
        if not isinstance(ids, Sequence) or isinstance(ids, (str, bytes)):
            raise ShadowReceiptError("L1_INTERNAL_INVALID", f"selected records are invalid for lane {lane}")
        normalized[lane] = tuple(sorted(set(str(value) for value in ids)))
    return normalized


def _requirements(manifest: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    requirements = manifest.get("requirements")
    if not isinstance(requirements, Sequence) or isinstance(requirements, (str, bytes)) or not requirements:
        raise ShadowReceiptError("L1_INTERNAL_INVALID", "coverage manifest requirements are missing")
    result: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for requirement in requirements:
        if not isinstance(requirement, Mapping):
            raise ShadowReceiptError("L1_INTERNAL_INVALID", "coverage requirement is not an object")
        requirement_id = requirement.get("requirement_id")
        if not isinstance(requirement_id, str) or not requirement_id or requirement_id in seen:
            raise ShadowReceiptError("L1_INTERNAL_INVALID", "coverage requirement IDs are not ordered and unique")
        seen.add(requirement_id)
        result.append(requirement)
    return tuple(result)


def _outcome_map(report: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, str]:
    rows = report.get("requirement_outcomes")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ShadowReceiptError("L1_INTERNAL_INVALID", "evidence report outcomes are missing")
    expected = [requirement["requirement_id"] for requirement in _requirements(manifest)]
    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ShadowReceiptError("L1_INTERNAL_INVALID", "evidence report outcome is not an object")
        requirement_id, outcome = row.get("requirement_id"), row.get("outcome")
        if requirement_id not in expected or not isinstance(outcome, str) or requirement_id in result:
            raise ShadowReceiptError("L1_INTERNAL_INVALID", "evidence report outcomes do not bind the manifest")
        result[requirement_id] = outcome
    if list(result) != expected:
        raise ShadowReceiptError("L1_INTERNAL_INVALID", "evidence report outcome order does not bind the manifest")
    return result


def _attempt_action(
    *,
    plan_digest: str,
    manifest_digest: str,
    authorization_digest: str,
    requirement_ids: Sequence[str],
    report_digest: str,
) -> L1Action:
    return create_action(
        parent_plan_digest=plan_digest,
        coverage_manifest_digest=manifest_digest,
        authorization_scope_digest=authorization_digest,
        attempt_ordinal=0,
        action_type="requirement_hybrid_retrieval",
        requirement_ids=requirement_ids,
        input_digest=digest(
            {
                "attempt": "zero",
                "parent_plan_digest": plan_digest,
                "coverage_manifest_digest": manifest_digest,
                "evidence_report_digest": report_digest,
            }
        ),
    )


def _terminal_reason(reason: str) -> str:
    return {
        "L1_COMPLETE": "COMPLETED",
        "L1_EXPLICIT_GAP": "EXPLICIT_GAP",
        "L1_NO_PROGRESS": "NO_PROGRESS",
        "L1_BUDGET_EXHAUSTED": "BUDGET_EXHAUSTED",
        "L1_UNAUTHORIZED": "UNAUTHORIZED",
        "L1_CONFLICT": "CONFLICT",
        "L1_NON_REFINABLE": "HITL_REQUIRED",
        "L1_POLICY_BLOCKED": "HITL_REQUIRED",
        "L1_INTERNAL_INVALID": "HITL_REQUIRED",
    }.get(reason, "HITL_REQUIRED")
