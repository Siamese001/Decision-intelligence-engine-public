"""Execution engine and action registry for query L1 actions."""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from ..core import (
    artifact_digest,
    canonical_bytes,
    digest,
    seal_artifact,
    sha256_file,
    write_json,
)
from ..index import HybridRetriever, validate_sidecar
from ..l1_control import (
    BudgetState,
    L1Action,
    L1ControlError,
    L1ControlPolicy,
    apply_action_result,
    guard_action,
)
from ..query_planning import CompiledRequirementQuery
from .helpers import (
    _attempt_artifact,
    _backend_snapshot,
    _blocked,
    _digest,
    _normalize_lanes,
    _pair,
    _public_diagnostics,
    _query_bindings,
    _requirements,
    _scope_bindings,
    _sealed,
)
from .models import ActionExecutionResult, ActionLimits, L1ActionExecutionError


def _get_retriever_and_validator() -> tuple[type, Any]:
    mod = sys.modules.get("dual_pipeline.query_l1_actions")
    if mod is not None:
        return getattr(mod, "HybridRetriever", HybridRetriever), getattr(mod, "validate_sidecar", validate_sidecar)
    return HybridRetriever, validate_sidecar


def _execute_stop(
    action: L1Action,
    *,
    pair: Mapping[str, str],
    budget: BudgetState,
    limits: ActionLimits,
) -> ActionExecutionResult:
    result_digest = digest({"action_id": action.action_id, "status": "skipped", "reason_code": "STOP"})
    artifact = _attempt_artifact(
        action=action,
        pair=pair,
        budget_before=budget,
        budget_after=budget,
        status="skipped",
        reason_code="STOP",
        record_ids_by_lane={},
        result_digest=result_digest,
        elapsed_ms=0,
        result_bytes=0,
        graph_sha256=pair["graph_sha256"],
        extensions={"limits": limits.__dict__},
    )
    return ActionExecutionResult("skipped", "STOP", action, artifact, None, budget, budget, 0)


def _execute_retrieval(
    action: L1Action,
    *,
    plan: Mapping[str, Any],
    coverage_manifest: Mapping[str, Any],
    requirement_queries: Mapping[str, CompiledRequirementQuery | Mapping[str, Any]],
    active_pair: Mapping[str, Any],
    graph_path: Path,
    sidecar_path: Path,
    backend: Any,
    policy: L1ControlPolicy,
    budget: BudgetState,
    classification: str,
    audiences: Sequence[str],
    allowed_lanes: Sequence[str],
    retrieval_mode: str,
    authorization_scope_digest: str,
    allowed_assertion_ids: Iterable[str] | None,
    card_scope: Mapping[str, Any] | None,
    facet_filters: Mapping[str, Iterable[str]] | None,
    bm25_assertion_ranks_by_lane: Mapping[str, Mapping[str, int]] | None,
    exact_anchor_assertion_ids_by_lane: Mapping[str, Iterable[str]] | None,
    limits: ActionLimits,
) -> ActionExecutionResult:
    pair = _pair(active_pair)
    normalized_assertion_ids = (
        None if allowed_assertion_ids is None else tuple(allowed_assertion_ids)
    )
    plan_digest = _sealed(plan, "query plan")
    manifest_digest = _sealed(coverage_manifest, "coverage manifest")
    source_plan_digest = plan_digest
    if plan.get("schema_version") == "3.0.0":
        source_plan_digest = (
            (plan.get("extensions") or {})
            .get("com.partner_advisory.nee.l1-agentic", {})
            .get("source_plan_digest", plan_digest)
        )
        _digest(source_plan_digest, "v3 source plan digest")
    if action.parent_plan_digest != plan_digest or action.coverage_manifest_digest != manifest_digest:
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", "action parent binding drifted")
    if coverage_manifest.get("query_plan_digest") != source_plan_digest:
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", "coverage manifest is not bound to the plan")
    requirements = _requirements(coverage_manifest, action)
    _scope_bindings(
        plan=plan,
        requirements=requirements,
        action=action,
        lanes=_normalize_lanes(allowed_lanes, "allowed lanes"),
        authorization_scope_digest=authorization_scope_digest,
        allowed_assertion_ids=normalized_assertion_ids,
        card_scope=card_scope,
    )
    if plan.get("retrieval_mode") != retrieval_mode:
        raise L1ActionExecutionError("L1_POLICY_BLOCKED", "retrieval mode drifted")
    controller = plan.get("controller")
    if not isinstance(controller, Mapping) or controller.get("policy_digest") != policy.policy_digest:
        raise L1ActionExecutionError("L1_POLICY_BLOCKED", "controller policy binding drifted")
    query = _query_bindings(action, plan, requirements, requirement_queries)
    if not graph_path.is_file() or not sidecar_path.is_file():
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", "graph or sidecar path is unavailable")
    if sha256_file(graph_path) != pair["graph_sha256"] or sha256_file(sidecar_path) != pair["sidecar_sha256"]:
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", "active graph/index pair drifted before action")
    if getattr(backend, "backend_id", None) != pair["backend"] or getattr(backend, "model_id", None) != pair["model_id"] or getattr(backend, "model_revision", None) != pair["model_revision"]:
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", "embedding backend binding drifted")
    _backend_snapshot(backend, pair, "before")
    retriever_cls, validator_fn = _get_retriever_and_validator()
    validator_fn(
        sidecar_path,
        expected_generation_id=pair["generation_id"],
        expected_graph_sha256=pair["graph_sha256"],
    )
    effective_action = replace(
        action,
        estimated_records=limits.max_records_per_action,
        estimated_result_bytes=limits.max_result_bytes_per_action,
        estimated_elapsed_ms=limits.max_elapsed_ms_per_action,
    )
    guard = guard_action(policy, budget, effective_action)
    if guard.status.value != "ALLOW":
        return _blocked(
            action=action,
            pair=pair,
            budget=budget,
            reason_code=str(guard.reason),
            graph_sha256=pair["graph_sha256"],
            limits=limits,
        )
    start = perf_counter()
    encoded = backend.encode(query)
    encoding_count = 1
    retriever = retriever_cls(sidecar_path, backend)
    lane_results: dict[str, dict[str, Any]] = {}
    lanes = _normalize_lanes(allowed_lanes, "allowed lanes")
    for lane in lanes:
        lane_results[lane] = retriever.retrieve_lane(
            query,
            lane,
            classification=classification,
            audiences=list(audiences),
            dense_limit=limits.max_candidates_per_lane,
            sparse_limit=limits.max_candidates_per_lane,
            bm25_limit=limits.max_candidates_per_lane,
            union_limit=limits.max_candidates_per_lane,
            colbert_limit=limits.max_candidates_per_lane,
            top_k=limits.max_selected_per_lane,
            retrieval_mode=retrieval_mode,
            encoded_query=encoded,
            facet_filters=facet_filters,
            allowed_assertion_ids=normalized_assertion_ids,
            bm25_assertion_ranks=(None if bm25_assertion_ranks_by_lane is None else bm25_assertion_ranks_by_lane.get(lane, {})),
            exact_anchor_assertion_ids=(() if exact_anchor_assertion_ids_by_lane is None else exact_anchor_assertion_ids_by_lane.get(lane, ())),
        )
    elapsed_ms = max(0, int((perf_counter() - start) * 1000))
    graph_after = sha256_file(graph_path)
    sidecar_after = sha256_file(sidecar_path)
    _backend_snapshot(backend, pair, "after")
    if graph_after != pair["graph_sha256"] or sidecar_after != pair["sidecar_sha256"]:
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", "graph/index pair changed during action")
    record_ids_by_lane: dict[str, list[str]] = {}
    for lane, result in lane_results.items():
        rows = result.get("results")
        if not isinstance(rows, list) or len(rows) > limits.max_selected_per_lane:
            raise L1ActionExecutionError("L1_BUDGET_EXHAUSTED", "selected-record cap exceeded")
        candidate_count = int(result.get("candidate_union_count", 0))
        if candidate_count > limits.max_candidates_per_lane:
            raise L1ActionExecutionError("L1_BUDGET_EXHAUSTED", "candidate cap exceeded")
        ids = [row.get("record_id") for row in rows if isinstance(row, Mapping)]
        if len(ids) != len(rows) or any(not isinstance(record_id, str) or not record_id for record_id in ids) or len(set(ids)) != len(ids):
            raise L1ActionExecutionError("L1_INTERNAL_INVALID", "retrieval returned duplicate or malformed record IDs")
        if any(row.get("lane") != lane for row in rows):
            raise L1ActionExecutionError("L1_UNAUTHORIZED", "retrieval lane contamination detected")
        record_ids_by_lane[lane] = ids
    all_ids = [record_id for ids in record_ids_by_lane.values() for record_id in ids]
    if len(all_ids) != len(set(all_ids)) or len(set(all_ids)) > limits.max_records_per_action:
        raise L1ActionExecutionError("L1_BUDGET_EXHAUSTED", "aggregate selected-record cap exceeded")
    ranks, component_counts = _public_diagnostics(lane_results)
    controlled = seal_artifact(
        {
            "artifact_type": "l1-controlled-retrieval",
            "schema_version": "1.0.0",
            "action_id": action.action_id,
            "attempt_id": action.attempt_id,
            "parent_plan_digest": plan_digest,
            "coverage_manifest_digest": manifest_digest,
            "authorization_scope_digest": authorization_scope_digest,
            "query": query,
            "query_digest": digest(query),
            "retrieval_mode": retrieval_mode,
            "allowed_lanes": list(lanes),
            "classification": classification,
            "audiences": list(audiences),
            "active_pair": dict(pair),
            "lanes": lane_results,
            "encoding_count": encoding_count,
            "elapsed_ms": elapsed_ms,
            "graph_sha256_before": pair["graph_sha256"],
            "graph_sha256_after": graph_after,
            "sidecar_sha256_before": pair["sidecar_sha256"],
            "sidecar_sha256_after": sidecar_after,
        }
    )
    result_bytes = len(canonical_bytes(controlled))
    if result_bytes > limits.max_result_bytes_per_action or elapsed_ms > limits.max_elapsed_ms_per_action:
        raise L1ActionExecutionError("L1_BUDGET_EXHAUSTED", "action work cap exceeded")
    controlled_digest = artifact_digest(controlled)
    result_digest = digest(
        {
            "action_id": action.action_id,
            "record_ids_by_lane": record_ids_by_lane,
            "ranks_by_lane": ranks,
            "component_counts_by_lane": component_counts,
            "controlled_retrieval_artifact_digest": controlled_digest,
            "encoding_count": encoding_count,
        }
    )
    after_budget = apply_action_result(
        budget,
        effective_action,
        policy,
        record_ids_by_lane=record_ids_by_lane,
        result_bytes=result_bytes,
        elapsed_ms=elapsed_ms,
        graph_sha256_before=pair["graph_sha256"],
        graph_sha256_after=graph_after,
    )
    attempt = _attempt_artifact(
        action=action,
        pair=pair,
        budget_before=budget,
        budget_after=after_budget,
        status="completed",
        reason_code="COMPLETED",
        record_ids_by_lane=record_ids_by_lane,
        result_digest=result_digest,
        elapsed_ms=elapsed_ms,
        result_bytes=result_bytes,
        graph_sha256=pair["graph_sha256"],
        extensions={
            "controlled_retrieval_artifact_digest": controlled_digest,
            "encoding_count": encoding_count,
            "ranks_by_lane": ranks,
            "component_counts_by_lane": component_counts,
            "limits": limits.__dict__,
        },
    )
    return ActionExecutionResult("completed", "COMPLETED", action, attempt, controlled, budget, after_budget, encoding_count)


def execute_l1_action(
    action: L1Action,
    *,
    plan: Mapping[str, Any],
    coverage_manifest: Mapping[str, Any],
    requirement_queries: Mapping[str, CompiledRequirementQuery | Mapping[str, Any]],
    active_pair: Mapping[str, Any],
    graph_path: Path,
    sidecar_path: Path,
    backend: Any,
    policy: L1ControlPolicy,
    budget: BudgetState,
    classification: str,
    audiences: Sequence[str],
    allowed_lanes: Sequence[str],
    retrieval_mode: str,
    authorization_scope_digest: str,
    allowed_assertion_ids: Iterable[str] | None = None,
    card_scope: Mapping[str, Any] | None = None,
    facet_filters: Mapping[str, Iterable[str]] | None = None,
    bm25_assertion_ranks_by_lane: Mapping[str, Mapping[str, int]] | None = None,
    exact_anchor_assertion_ids_by_lane: Mapping[str, Iterable[str]] | None = None,
) -> ActionExecutionResult:
    """Execute exactly one closed L1 action, or return one failed result."""

    pair = _pair(active_pair)
    lanes = _normalize_lanes(allowed_lanes, "allowed lanes")
    limits = ActionLimits.from_policy(policy, len(lanes))
    if action.action_type not in ACTION_REGISTRY:
        return _blocked(
            action=action,
            pair=pair,
            budget=budget,
            reason_code="L1_POLICY_BLOCKED",
            graph_sha256=pair["graph_sha256"],
            limits=limits,
        )
    if action.action_type == "stop":
        return _execute_stop(action, pair=pair, budget=budget, limits=limits)
    try:
        return ACTION_REGISTRY[action.action_type](
            action,
            plan=plan,
            coverage_manifest=coverage_manifest,
            requirement_queries=requirement_queries,
            active_pair=active_pair,
            graph_path=Path(graph_path).resolve(),
            sidecar_path=Path(sidecar_path).resolve(),
            backend=backend,
            policy=policy,
            budget=budget,
            classification=classification,
            audiences=audiences,
            allowed_lanes=lanes,
            retrieval_mode=retrieval_mode,
            authorization_scope_digest=authorization_scope_digest,
            allowed_assertion_ids=allowed_assertion_ids,
            card_scope=card_scope,
            facet_filters=facet_filters,
            bm25_assertion_ranks_by_lane=bm25_assertion_ranks_by_lane,
            exact_anchor_assertion_ids_by_lane=exact_anchor_assertion_ids_by_lane,
            limits=limits,
        )
    except L1ControlError as exc:
        return _blocked(
            action=action,
            pair=pair,
            budget=budget,
            reason_code=exc.reason,
            graph_sha256=pair["graph_sha256"],
            limits=limits,
        )
    except Exception:
        # An encoder/retriever failure is terminal for this action. It is
        # deliberately not retried, substituted, widened, or converted into a
        # provider request.
        return _blocked(
            action=action,
            pair=pair,
            budget=budget,
            reason_code="L1_INTERNAL_INVALID",
            graph_sha256=pair["graph_sha256"],
            limits=limits,
        )


def write_controlled_retrieval_artifact(path: Path, result: ActionExecutionResult) -> None:
    """Persist only the controlled full-record artifact, never the receipt."""

    if result.retrieval_artifact is None or result.status != "completed":
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", "only a completed action has a controlled retrieval artifact")
    value = result.retrieval_artifact
    if artifact_digest(value) != value.get("canonical_digest", {}).get("value"):
        raise L1ActionExecutionError("L1_INTERNAL_INVALID", "controlled retrieval artifact is not sealed")
    write_json(Path(path), value)


ACTION_REGISTRY = MappingProxyType(
    {
        "requirement_hybrid_retrieval": _execute_retrieval,
        "stop": _execute_stop,
    }
)
