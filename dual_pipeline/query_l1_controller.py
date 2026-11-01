"""Wave 6 deterministic helpers for one bounded L1 retrieval feedback edge.

The helpers here deliberately do not route, retrieve, expand a graph, or call
a provider.  They decide eligibility, fuse already-authorized result rows, and
seal the observation/terminal portions of a bounded controller chain.  Query
runtime owns the single retrieval and final evidence rebuild.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .contracts import create_versioned_artifact
from .core import artifact_digest, stable_id
from .l1_control import BudgetState, ProgressStatus, observe_progress

LANES = ("approved", "candidate", "external_public")
BOUNDED_TELEMETRY_KEYS = frozenset(
    {
        "event_type", "mode", "plan_digest", "coverage_manifest_digest",
        "attempt_count", "action_count", "target_count", "newly_covered_count",
        "terminal_digest", "terminal_reason", "coverage_complete", "budget",
        "provider_call_count", "graph_mutation_count", "graph_sha256_before",
        "graph_sha256_after", "final_evidence_report_digest", "reason_code",
    }
)


def refinable_requirement_ids(report: Mapping[str, Any]) -> tuple[str, ...]:
    """Return only the explicit, policy-approved retrieval coverage gaps."""

    rows = report.get("requirement_outcomes")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError("retrieval evidence report outcomes are missing")
    values: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("retrieval evidence report outcome is malformed")
        if row.get("outcome") == "explicit_gap" and row.get("reason_code") == "RETRIEVAL_COVERAGE_MISSING":
            requirement_id = row.get("requirement_id")
            if not isinstance(requirement_id, str) or not requirement_id:
                raise ValueError("retrieval evidence report requirement ID is malformed")
            values.append(requirement_id)
    return tuple(values)


def feedback_eligible(
    *,
    mode: str,
    destination: str,
    graph_fit_decision: Mapping[str, Any],
    evidence_report: Mapping[str, Any],
    budget_state: Any | None = None,
    policy: Any | None = None,
) -> bool:
    """Apply the closed Wave 6 entry predicate before an action is selected."""

    if mode != "bounded" or destination != "NEE_DECISION_INTELLIGENCE":
        return False
    if budget_state is not None and policy is not None:
        if getattr(budget_state, "elapsed_ms", 0) >= getattr(policy, "max_elapsed_ms", 10000):
            return False
        if getattr(budget_state, "additional_attempts_used", 0) >= getattr(policy, "max_additional_attempts", 1):
            return False
    # A graph with no selected record can still be recoverable through a
    # requirement-scoped retrieval.  Existing conflict/authorization failures
    # are not recoverable by further retrieval and stop the edge.
    reasons = graph_fit_decision.get("reason_codes", [])
    if not isinstance(reasons, Sequence) or isinstance(reasons, (str, bytes)):
        return False
    if any(reason in {"UNAUTHORIZED", "CONFLICT", "INTERNAL_INVALID"} for reason in reasons):
        return False
    outcomes = evidence_report.get("requirement_outcomes", [])
    if any(
        isinstance(row, Mapping) and row.get("outcome") in {"unauthorized", "conflict"}
        for row in outcomes
    ):
        return False
    return bool(refinable_requirement_ids(evidence_report))


def _row_fusion_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Stable within-lane selection tuple; it never changes authority."""

    exact_anchor = bool(row.get("exact_anchor_injected"))
    target_count = int(row.get("l1_target_requirement_count", 0))
    fused_rank = row.get("fused_rank")
    rank = int(fused_rank) if isinstance(fused_rank, int) and fused_rank > 0 else 1_000_000
    reciprocal_rank = 1.0 / rank
    component = tuple(
        -float(row.get(name) or 0.0)
        for name in ("dense_score", "sparse_score", "colbert_score", "fused_score")
    )
    action_ordinal = int(row.get("l1_action_ordinal", 0))
    record_id = str(row.get("record_id") or "")
    return (not exact_anchor, -target_count, -reciprocal_rank, component, action_ordinal, record_id)


def merge_retrieval_results(
    *,
    initial_retrieval: Mapping[str, Any],
    action_retrievals: Sequence[Mapping[str, Any]],
    allowed_lanes: Sequence[str],
    max_records: int,
) -> dict[str, Any]:
    """Union, provenance-bind, deduplicate, and cap results per lane.

    Input records must already have passed the lane, clearance, audience, and
    assertion filters in ``HybridRetriever``.  This function does not inspect
    or alter authority fields; it only selects a stable subset within the same
    lane.
    """

    lanes = tuple(allowed_lanes)
    if not lanes or any(lane not in LANES for lane in lanes) or len(set(lanes)) != len(lanes):
        raise ValueError("allowed lanes are invalid")
    if isinstance(max_records, bool) or not isinstance(max_records, int) or max_records < 1:
        raise ValueError("max_records must be a positive integer")
    base = deepcopy(dict(initial_retrieval))
    if base.get("allowed_lanes") != list(lanes):
        raise ValueError("initial retrieval lanes do not bind the controller")
    merged_lanes: dict[str, dict[str, Any]] = {}
    per_lane_cap = max(1, max_records // len(lanes))
    for lane in lanes:
        candidates: dict[str, dict[str, Any]] = {}
        sources = [base.get("lanes", {}).get(lane)] + [value.get("lanes", {}).get(lane) for value in action_retrievals]
        for source_ordinal, source in enumerate(sources):
            if not isinstance(source, Mapping):
                continue
            rows = source.get("results", [])
            if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
                raise ValueError("retrieval lane result is malformed")
            for row in rows:
                if not isinstance(row, Mapping) or row.get("lane") != lane:
                    raise ValueError("retrieval lane contamination detected")
                record_id = row.get("record_id")
                if not isinstance(record_id, str) or not record_id:
                    raise ValueError("retrieval record ID is malformed")
                candidate = deepcopy(dict(row))
                candidate["l1_action_ordinal"] = max(0, source_ordinal - 1)
                candidate["l1_target_requirement_count"] = int(candidate.get("l1_target_requirement_count", 0))
                provenance = candidate.setdefault("l1_provenance", [])
                if not isinstance(provenance, list):
                    raise ValueError("retrieval provenance is malformed")
                provenance.append({"source_ordinal": source_ordinal, "lane": lane})
                existing = candidates.get(record_id)
                if existing is None:
                    candidates[record_id] = candidate
                else:
                    existing.setdefault("l1_provenance", []).extend(provenance)
                    if _row_fusion_key(candidate) < _row_fusion_key(existing):
                        candidate["l1_provenance"] = existing["l1_provenance"]
                        candidates[record_id] = candidate
        ordered = sorted(candidates.values(), key=_row_fusion_key)[:per_lane_cap]
        lane_base = deepcopy(dict(base.get("lanes", {}).get(lane) or {}))
        lane_base["results"] = ordered
        lane_base["l1_fusion"] = {
            "policy": "exact_anchor,target_count,reciprocal_rank,component_scores,action_ordinal,record_id",
            "candidate_count": len(candidates),
            "selected_count": len(ordered),
            "per_lane_cap": per_lane_cap,
        }
        merged_lanes[lane] = lane_base
    record_ids = [row["record_id"] for lane in lanes for row in merged_lanes[lane]["results"]]
    if len(record_ids) != len(set(record_ids)) or len(record_ids) > max_records:
        raise ValueError("fused retrieval exceeds aggregate record cap")
    base["lanes"] = merged_lanes
    base["final_record_count"] = len(record_ids)
    base["l1_fusion"] = {
        "enabled": True,
        "aggregate_cap": max_records,
        "selected_record_ids_by_lane": {
            lane: [row["record_id"] for row in merged_lanes[lane]["results"]]
            for lane in lanes
        },
    }
    return base


def build_observation_one(
    *,
    plan: Mapping[str, Any],
    coverage_manifest: Mapping[str, Any],
    evidence_report: Mapping[str, Any],
    action_attempt: Mapping[str, Any],
    prior_report: Mapping[str, Any],
    graph_sha256: str,
) -> dict[str, Any]:
    """Seal the final post-action coverage observation."""

    plan_digest = plan["canonical_digest"]["value"]
    manifest_digest = coverage_manifest["canonical_digest"]["value"]
    report_digest = evidence_report["canonical_digest"]["value"]
    attempt_digest = action_attempt["canonical_digest"]["value"]
    previous = {row["requirement_id"]: row["outcome"] for row in prior_report["requirement_outcomes"]}
    current = {row["requirement_id"]: row["outcome"] for row in evidence_report["requirement_outcomes"]}
    progress = observe_progress(previous, current)
    status = (
        "complete" if progress.status is ProgressStatus.COMPLETE else
        "progress" if progress.status is ProgressStatus.PROGRESS else
        "no_progress" if progress.status is ProgressStatus.NO_PROGRESS else "blocked"
    )
    observation_id = stable_id("query-plan-observation", attempt_digest, report_digest, length=26)
    return create_versioned_artifact(
        "query-plan-observation", "1.0.0", observation_id,
        observation_id=observation_id,
        parent_plan_digest=plan_digest,
        attempt_digest=attempt_digest,
        evidence_report_digest=report_digest,
        coverage_manifest_digest=manifest_digest,
        requirement_outcomes=[{"requirement_id": key, "outcome": current[key]} for key in current],
        newly_covered_requirement_ids=list(progress.newly_covered_requirement_ids),
        unresolved_requirement_ids=list(progress.unresolved_requirement_ids),
        progress=status,
        progress_reason=("coverage complete" if status == "complete" else "newly covered material requirements" if status == "progress" else "no newly covered material requirements"),
        graph_sha256_before=graph_sha256,
        graph_sha256_after=graph_sha256,
        provider_call_count=0,
        extensions={"wave": "W6", "prior_evidence_report_digest": prior_report["canonical_digest"]["value"]},
    )


def build_bounded_terminal(
    *,
    plan: Mapping[str, Any],
    coverage_manifest: Mapping[str, Any],
    final_evidence_report: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
    observation_one: Mapping[str, Any],
    budget: BudgetState,
    graph_sha256: str,
) -> dict[str, Any]:
    """Seal a one-replan terminal; incomplete coverage is never passed to L2D."""

    complete = not observation_one["unresolved_requirement_ids"]
    reason = "COMPLETED" if complete else "NO_PROGRESS"
    terminal_id = stable_id("query-plan-terminal", plan["canonical_digest"]["value"], observation_one["canonical_digest"]["value"], length=26)
    return create_versioned_artifact(
        "query-plan-terminal", "1.0.0", terminal_id,
        terminal_id=terminal_id,
        parent_plan_digest=plan["canonical_digest"]["value"],
        coverage_manifest_digest=coverage_manifest["canonical_digest"]["value"],
        attempt_chain=[artifact_digest(dict(value)) for value in attempts],
        final_evidence_report_digest=final_evidence_report["canonical_digest"]["value"],
        total_work={"attempts": len(attempts), "actions": budget.actions_used, "unique_records": budget.unique_records, "bytes": budget.result_bytes, "elapsed_ms": budget.elapsed_ms},
        final_provider_call_count=0,
        graph_sha256_before=graph_sha256,
        graph_sha256_after=graph_sha256,
        terminal_reason=reason,
        coverage_complete=complete,
        permitted_next_stage="L2D" if complete else "NONE",
        extensions={"wave": "W6", "observation_one_digest": observation_one["canonical_digest"]["value"], "one_replan_only": True},
    )


def build_complete_first_pass_terminal(
    *,
    controller_plan: Mapping[str, Any],
    coverage_manifest: Mapping[str, Any],
    evidence_report: Mapping[str, Any],
    attempt_zero: Mapping[str, Any],
    graph_sha256: str,
) -> dict[str, Any]:
    """Seal bounded mode when attempt zero already has full coverage."""

    terminal_id = stable_id("query-plan-terminal", controller_plan["canonical_digest"]["value"], attempt_zero["canonical_digest"]["value"], length=26)
    return create_versioned_artifact(
        "query-plan-terminal", "1.0.0", terminal_id,
        terminal_id=terminal_id,
        parent_plan_digest=controller_plan["canonical_digest"]["value"],
        coverage_manifest_digest=coverage_manifest["canonical_digest"]["value"],
        attempt_chain=[attempt_zero["canonical_digest"]["value"]],
        final_evidence_report_digest=evidence_report["canonical_digest"]["value"],
        total_work={
            "attempts": 1,
            "actions": 1,
            "unique_records": sum(len(values) for values in evidence_report["selected_record_ids_by_lane"].values()),
            "bytes": 0,
            "elapsed_ms": 0,
        },
        final_provider_call_count=0,
        graph_sha256_before=graph_sha256,
        graph_sha256_after=graph_sha256,
        terminal_reason="COMPLETED",
        coverage_complete=True,
        permitted_next_stage="L2D",
        extensions={"wave": "W6", "executed_replans": 0, "one_replan_only": True},
    )


def bounded_telemetry_payload(
    *,
    plan_digest: str,
    coverage_manifest_digest: str,
    terminal: Mapping[str, Any],
    budget: BudgetState,
    target_count: int,
    newly_covered_count: int,
) -> dict[str, Any]:
    """Return the strict content-safe telemetry projection for bounded L1."""

    payload = {
        "event_type": "l1_bounded",
        "mode": "bounded",
        "plan_digest": plan_digest,
        "coverage_manifest_digest": coverage_manifest_digest,
        "attempt_count": int(terminal["total_work"]["attempts"]),
        "action_count": int(terminal["total_work"]["actions"]),
        "target_count": target_count,
        "newly_covered_count": newly_covered_count,
        "terminal_digest": terminal["canonical_digest"]["value"],
        "terminal_reason": terminal["terminal_reason"],
        "coverage_complete": bool(terminal["coverage_complete"]),
        "budget": budget.to_dict(),
        "provider_call_count": 0,
        "graph_mutation_count": 0,
        "graph_sha256_before": terminal["graph_sha256_before"],
        "graph_sha256_after": terminal["graph_sha256_after"],
        "final_evidence_report_digest": terminal["final_evidence_report_digest"],
        "reason_code": terminal["terminal_reason"],
    }
    validate_bounded_telemetry(payload)
    return payload


def validate_bounded_telemetry(payload: Mapping[str, Any]) -> None:
    """Reject unknown or content-bearing telemetry attributes."""

    forbidden = ("question", "label", "prompt", "response", "excerpt", "raw", "record")
    for key in payload:
        if any(token in key.casefold() for token in forbidden):
            raise ValueError(f"content-bearing bounded telemetry key: {key}")
    unknown = set(payload) - BOUNDED_TELEMETRY_KEYS
    if unknown:
        raise ValueError(f"unsupported bounded telemetry keys: {sorted(unknown)}")
