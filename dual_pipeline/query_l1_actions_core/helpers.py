"""Helper functions, validation, and attempt builders for query L1 actions."""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from ..contracts import create_versioned_artifact
from ..core import artifact_digest, digest
from ..l1_control import BudgetState, L1Action
from ..query_planning import CompiledRequirementQuery, compile_requirement_query
from .constants import ATTEMPT_CONTRACT, CARD_SCOPE_EXTENSION, LANE_ORDER
from .models import ActionExecutionResult, ActionLimits, L1ActionExecutionError


def _digest(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", f"{label} must be a lowercase SHA-256 digest")
    return value


def _sealed(value: Mapping[str, Any], label: str) -> str:
    canonical = value.get("canonical_digest")
    if not isinstance(canonical, Mapping):
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", f"{label} is not sealed")
    expected = _digest(canonical.get("value"), f"{label} canonical digest")
    if artifact_digest(dict(value)) != expected:
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", f"{label} canonical digest does not verify")
    return expected


def _pair(active_pair: Mapping[str, Any]) -> dict[str, str]:
    required = {
        "generation_id",
        "graph_sha256",
        "sidecar_sha256",
        "model_id",
        "model_revision",
    }
    backend = active_pair.get("embedding_backend", active_pair.get("backend"))
    if not required <= set(active_pair) or not isinstance(backend, str):
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", "active pair is incomplete")
    values = {
        "generation_id": active_pair.get("generation_id"),
        "graph_sha256": active_pair.get("graph_sha256"),
        "sidecar_sha256": active_pair.get("sidecar_sha256"),
        "backend": backend,
        "model_id": active_pair.get("model_id"),
        "model_revision": active_pair.get("model_revision"),
    }
    for key, value in values.items():
        if not isinstance(value, str) or not value:
            raise L1ActionExecutionError("L1_INTERNAL_INVALID", f"active pair field is invalid: {key}")
    _digest(values["graph_sha256"], "active pair graph_sha256")
    _digest(values["sidecar_sha256"], "active pair sidecar_sha256")
    return values


def _normalize_lanes(value: Sequence[str], label: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise L1ActionExecutionError("L1_POLICY_BLOCKED", f"{label} must be an ordered lane list")
    lanes = tuple(value)
    if not lanes or any(lane not in LANE_ORDER for lane in lanes) or len(set(lanes)) != len(lanes):
        raise L1ActionExecutionError("L1_POLICY_BLOCKED", f"{label} contains an unauthorized lane")
    if tuple(sorted(lanes, key=LANE_ORDER.index)) != lanes:
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", f"{label} is not in canonical lane order")
    return lanes


def _backend_snapshot(backend: Any, pair: Mapping[str, str], label: str) -> dict[str, Any]:
    verifier = getattr(backend, "verify_snapshot", None)
    if not callable(verifier):
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", "embedding backend cannot verify its immutable snapshot")
    snapshot = verifier()
    if not isinstance(snapshot, Mapping):
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", f"{label} backend snapshot is not an object")
    checks = {
        "model_id": pair["model_id"],
        "model_revision": pair["model_revision"],
    }
    manifest = getattr(backend, "model_manifest_digest", None)
    if manifest is not None:
        checks["manifest_digest"] = str(manifest)
    for key, expected in checks.items():
        if key in snapshot and snapshot.get(key) != expected:
            raise L1ActionExecutionError("L1_INTERNAL_INVALID", f"{label} backend snapshot drifted: {key}")
    return {
        key: snapshot.get(key)
        for key in ("model_id", "model_revision", "manifest_digest", "file_count", "snapshot_size_bytes")
        if key in snapshot
    }


def _requirements(
    manifest: Mapping[str, Any], action: L1Action
) -> dict[str, Mapping[str, Any]]:
    values = manifest.get("requirements")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", "coverage manifest requirements are missing")
    by_id: dict[str, Mapping[str, Any]] = {}
    for requirement in values:
        if not isinstance(requirement, Mapping) or not isinstance(requirement.get("requirement_id"), str):
            raise L1ActionExecutionError("L1_INTERNAL_INVALID", "coverage requirement is malformed")
        requirement_id = str(requirement["requirement_id"])
        if requirement_id in by_id:
            raise L1ActionExecutionError("L1_INTERNAL_INVALID", "coverage requirement IDs are duplicated")
        by_id[requirement_id] = requirement
    if not action.requirement_ids or any(requirement_id not in by_id for requirement_id in action.requirement_ids):
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", "action requirements are outside the manifest")
    return by_id


def _query_bindings(
    action: L1Action,
    plan: Mapping[str, Any],
    requirements: Mapping[str, Mapping[str, Any]],
    supplied: Mapping[str, CompiledRequirementQuery | Mapping[str, Any]],
) -> str:
    if not isinstance(supplied, Mapping):
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", "sealed requirement queries are missing")
    queries: list[str] = []
    for requirement_id in action.requirement_ids:
        value = supplied.get(requirement_id)
        if isinstance(value, CompiledRequirementQuery):
            query, input_digest = value.query, value.input_digest
        elif isinstance(value, Mapping):
            query, input_digest = value.get("query"), value.get("input_digest")
        else:
            raise L1ActionExecutionError("L1_INTERNAL_INVALID", f"sealed requirement query is missing: {requirement_id}")
        if not isinstance(query, str) or not query or not isinstance(input_digest, str):
            raise L1ActionExecutionError("L1_INTERNAL_INVALID", f"sealed requirement query is malformed: {requirement_id}")
        compiled = compile_requirement_query(requirements[requirement_id], plan)
        if query != compiled.query or input_digest != compiled.input_digest:
            raise L1ActionExecutionError("L1_INTERNAL_INVALID", f"sealed requirement query drifted: {requirement_id}")
        queries.append(query)
    if len(set(queries)) != 1:
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", "one action cannot execute multiple query texts")
    if action.input_digest != next(
        value.input_digest if isinstance(value, CompiledRequirementQuery) else value.get("input_digest")
        for value in (supplied[action.requirement_ids[0]],)
    ):
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", "action input digest is not bound to the sealed query")
    return queries[0]


def _scope_bindings(
    *,
    plan: Mapping[str, Any],
    requirements: Mapping[str, Mapping[str, Any]],
    action: L1Action,
    lanes: tuple[str, ...],
    authorization_scope_digest: str,
    allowed_assertion_ids: Iterable[str] | None,
    card_scope: Mapping[str, Any] | None,
) -> None:
    _digest(authorization_scope_digest, "authorization scope digest")
    if action.authorization_scope_digest != authorization_scope_digest:
        raise L1ActionExecutionError("L1_UNAUTHORIZED", "action authorization scope drifted")
    if plan.get("allowed_lanes") != list(lanes):
        raise L1ActionExecutionError("L1_UNAUTHORIZED", "action lanes do not match the sealed plan")
    planned_scope = (plan.get("extensions") or {}).get(CARD_SCOPE_EXTENSION)
    if planned_scope is not None:
        if not isinstance(planned_scope, Mapping) or not isinstance(card_scope, Mapping):
            raise L1ActionExecutionError("L1_UNAUTHORIZED", "card scope binding is missing")
        expected = {key: planned_scope.get(key) for key in ("card_id", "card_view_id", "view_version", "selector_digest", "membership_digest")}
        actual = {key: card_scope.get(key) for key in expected}
        if expected != actual:
            raise L1ActionExecutionError("L1_UNAUTHORIZED", "card scope drifted")
    elif card_scope is not None:
        raise L1ActionExecutionError("L1_UNAUTHORIZED", "unplanned card scope cannot be added")
    assertion_ids = None if allowed_assertion_ids is None else tuple(allowed_assertion_ids)
    if assertion_ids is not None and (len(set(assertion_ids)) != len(assertion_ids) or any(not isinstance(value, str) or not value for value in assertion_ids)):
        raise L1ActionExecutionError("L1_UNAUTHORIZED", "assertion scope is not normalized")
    for requirement_id in action.requirement_ids:
        requirement_lanes = _normalize_lanes(requirements[requirement_id].get("allowed_lanes", []), f"requirement {requirement_id} lanes")
        if not set(lanes) <= set(requirement_lanes):
            raise L1ActionExecutionError("L1_UNAUTHORIZED", "action widens a requirement lane scope")
    if card_scope is not None:
        card_lanes = card_scope.get("lanes")
        if card_lanes is not None:
            if not isinstance(card_lanes, Mapping):
                raise L1ActionExecutionError("L1_UNAUTHORIZED", "card assertion lanes are malformed")
            card_ids = {str(value) for values in card_lanes.values() for value in (values if isinstance(values, Sequence) and not isinstance(values, (str, bytes)) else ())}
            if assertion_ids is None or set(assertion_ids) != card_ids:
                raise L1ActionExecutionError("L1_UNAUTHORIZED", "assertion scope does not bind the card scope")


def _public_diagnostics(lanes: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, int]]]:
    ranks: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, dict[str, int]] = {}
    for lane, result in lanes.items():
        rows = result.get("results", [])
        ranks[lane] = [
            {
                "record_id": row.get("record_id"),
                "lane": row.get("lane"),
                "fused_rank": row.get("fused_rank"),
                "dense_rank": row.get("dense_rank"),
                "sparse_rank": row.get("sparse_rank"),
                "bm25_rank": row.get("bm25_rank"),
                "colbert_rank": row.get("colbert_rank"),
            }
            for row in rows
        ]
        counts[lane] = {
            key: int(result.get(key, 0))
            for key in (
                "dense_candidate_count",
                "sparse_candidate_count",
                "bm25_candidate_count",
                "colbert_candidate_count",
                "candidate_union_count",
                "authorized_record_count",
            )
        }
    return ranks, counts


def _attempt_artifact(
    *,
    action: L1Action,
    pair: Mapping[str, str],
    budget_before: BudgetState,
    budget_after: BudgetState,
    status: str,
    reason_code: str,
    record_ids_by_lane: Mapping[str, Sequence[str]],
    result_digest: str,
    elapsed_ms: int,
    result_bytes: int,
    graph_sha256: str,
    extensions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return create_versioned_artifact(
        ATTEMPT_CONTRACT,
        "1.0.0",
        f"l1-action-attempt-{action.attempt_id}",
        attempt_id=action.attempt_id,
        parent_plan_digest=action.parent_plan_digest,
        coverage_manifest_digest=action.coverage_manifest_digest,
        attempt_ordinal=action.attempt_ordinal,
        action_id=action.action_id,
        action_type=action.action_type,
        requirement_ids=list(action.requirement_ids),
        authorization_scope_digest=action.authorization_scope_digest,
        active_pair=dict(pair),
        input_digest=action.input_digest,
        budget_before={
            "actions_used": budget_before.actions_used,
            "records_used": budget_before.unique_records,
            "bytes_used": budget_before.result_bytes,
            "elapsed_ms": budget_before.elapsed_ms,
        },
        result={
            "status": status,
            "record_ids_by_lane": {lane: list(ids) for lane, ids in record_ids_by_lane.items()},
            "result_digest": result_digest,
            "elapsed_ms": elapsed_ms,
            "bytes": result_bytes,
            "provider_initialization_count": 0,
            "provider_call_count": 0,
        },
        budget_after={
            "actions_used": budget_after.actions_used,
            "records_used": budget_after.unique_records,
            "bytes_used": budget_after.result_bytes,
            "elapsed_ms": budget_after.elapsed_ms,
        },
        graph_sha256_before=graph_sha256,
        graph_sha256_after=graph_sha256,
        provider_initialization_count=0,
        provider_call_count=0,
        extensions={
            "wave": "W5",
            "executed": status == "completed",
            "reason_code": reason_code,
            **dict(extensions or {}),
        },
    )


def _blocked(
    *,
    action: L1Action,
    pair: Mapping[str, str],
    budget: BudgetState,
    reason_code: str,
    graph_sha256: str,
    limits: ActionLimits,
) -> ActionExecutionResult:
    result_digest = digest({"action_id": action.action_id, "status": "blocked", "reason_code": reason_code})
    artifact = _attempt_artifact(
        action=action,
        pair=pair,
        budget_before=budget,
        budget_after=budget,
        status="blocked",
        reason_code=reason_code,
        record_ids_by_lane={},
        result_digest=result_digest,
        elapsed_ms=0,
        result_bytes=0,
        graph_sha256=graph_sha256,
        extensions={"limits": limits.__dict__},
    )
    return ActionExecutionResult("blocked", reason_code, action, artifact, None, budget, budget, 0)
