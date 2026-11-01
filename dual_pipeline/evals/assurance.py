"""Independent, content-silent assurance for bounded agentic workflows.

The workflow evaluator deliberately operates outside the candidate runtime. It
constructs exact deterministic provider-boundary requests from digest-bound
fixture specifications, measures the final serialized bytes, reconciles every
attempt, and applies non-compensating structural and business gates.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..contracts import create_versioned_artifact, validate_artifact
from ..core import (
    canonical_json,
    digest,
    read_json,
    sha256_file,
    stable_id,
    utc_now,
    write_json_exclusive,
)
from .contracts import resolve_repository_file

EVALUATOR_VERSION = "1.0.0"
MEASUREMENT_PROFILE = "deterministic_structural"


class AssuranceFailure(ValueError):
    """A stable, cause-coded workflow assurance failure."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def _sha(value: Any) -> str:
    return digest(value)


def _nearest_rank(values: Sequence[int], percentile: float) -> int:
    if not values:
        return 0
    if not 0 < percentile <= 1:
        raise ValueError("percentile must be in (0, 1]")
    ordered = sorted(int(value) for value in values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _linear_slope(points: Sequence[tuple[int, int]]) -> float:
    if len(points) < 2:
        return 0.0
    count = len(points)
    mean_x = sum(point[0] for point in points) / count
    mean_y = sum(point[1] for point in points) / count
    denominator = sum((point[0] - mean_x) ** 2 for point in points)
    if denominator == 0:
        return 0.0
    numerator = sum(
        (point[0] - mean_x) * (point[1] - mean_y)
        for point in points
    )
    return numerator / denominator


def _component_text(label: str, size: int) -> str:
    if size < 0:
        raise AssuranceFailure("NEGATIVE_COMPONENT_SIZE", f"{label} is negative")
    if not size:
        return ""
    seed = f"{label}:"
    repetitions = math.ceil(size / len(seed))
    return (seed * repetitions)[:size]


def _binding_digest(value: Mapping[str, Any]) -> str:
    return _sha(dict(value))


def load_workflow_suite(path: Path) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    """Load a contract-valid workflow suite and verify its workload bytes."""

    suite_path = Path(path).expanduser().resolve()
    suite = read_json(suite_path)
    validate_artifact(suite)
    if (
        suite.get("contract_id")
        != "urn:nee:dual-pipeline:agentic-workflow-eval-suite:1.0.0"
    ):
        raise AssuranceFailure("WORKFLOW_SUITE_CONTRACT", "unexpected suite contract")
    if suite["evaluator"]["version"] != EVALUATOR_VERSION:
        raise AssuranceFailure(
            "EVALUATOR_VERSION_MISMATCH",
            f"expected {EVALUATOR_VERSION}, observed {suite['evaluator']['version']}",
        )
    evaluator_sha = sha256_file(Path(__file__).resolve())
    if suite["evaluator"]["digest"] != evaluator_sha:
        raise AssuranceFailure(
            "EVALUATOR_DIGEST_MISMATCH",
            f"expected {suite['evaluator']['digest']}, observed {evaluator_sha}",
        )
    workload_path = resolve_repository_file(suite["workload"]["path"])
    actual_sha = sha256_file(workload_path)
    if actual_sha != suite["workload"]["sha256"]:
        raise AssuranceFailure(
            "WORKLOAD_DIGEST_MISMATCH",
            f"expected {suite['workload']['sha256']}, observed {actual_sha}",
        )
    workload = read_json(workload_path)
    if workload.get("workload_id") != suite["workload"]["workload_id"]:
        raise AssuranceFailure(
            "WORKLOAD_ID_MISMATCH",
            "suite workload ID does not match the bound fixture",
        )
    return suite, workload_path, workload


def _treatment(suite: Mapping[str, Any], treatment_id: str) -> dict[str, Any]:
    for treatment in suite["treatments"]:
        if treatment["treatment_id"] == treatment_id:
            return dict(treatment)
    raise AssuranceFailure("UNKNOWN_TREATMENT", treatment_id)


def _scenario(workload: Mapping[str, Any], scenario_id: str) -> dict[str, Any]:
    for scenario in workload["scenarios"]:
        if scenario["scenario_id"] == scenario_id:
            return dict(scenario)
    raise AssuranceFailure("UNKNOWN_SCENARIO", scenario_id)


def _request_observation(
    *,
    scenario: Mapping[str, Any],
    treatment: Mapping[str, Any],
    node_ordinal: int,
    prior_completed_count: int,
    prior_tool_output_bytes: int,
    previous_compiled_prompt_bytes: int = 0,
) -> tuple[dict[str, int], int, str]:
    base = scenario["base_component_bytes"]
    completed_history_bytes = (
        int(scenario["completed_history_bytes_per_prior_node"])
        * prior_completed_count
        if treatment["history_policy"] == "append_completed"
        else 0
    )
    raw_tool_output_bytes = (
        prior_tool_output_bytes
        if treatment["tool_output_policy"] == "inline"
        else 0
    )
    components = {
        "fixed_controls": int(base["fixed_controls"]),
        "current_step": int(base["current_step"]),
        "checkpoint": int(base["checkpoint"]),
        "evidence": int(base["evidence"]),
        "output_contract": int(base["output_contract"]),
        "completed_history": completed_history_bytes,
        "raw_tool_output": raw_tool_output_bytes,
        "previous_compiled_prompt": previous_compiled_prompt_bytes,
        "other": int(base.get("other", 0)),
    }
    body = {
        "workflow_id": scenario["workflow_id"],
        "node_ordinal": node_ordinal,
        "components": {
            name: _component_text(name, size)
            for name, size in components.items()
        },
    }
    serialized = canonical_json(body)
    return components, len(serialized.encode("utf-8")), _sha(body)


def _usage_for_request(
    *,
    request_bytes: int,
    output_tokens: int,
    cached_fraction: float,
    usage_reported: bool,
) -> dict[str, Any]:
    if not usage_reported:
        return {
            "usage_reported": False,
            "input_tokens": None,
            "cached_input_tokens": None,
            "uncached_input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "microcredits": None,
        }
    input_tokens = math.ceil(request_bytes / 4)
    cached = min(input_tokens, int(input_tokens * cached_fraction))
    uncached = input_tokens - cached
    total = input_tokens + output_tokens
    # Deterministic credit units preserve relative economics without asserting
    # an account-specific currency schedule.
    microcredits = uncached * 10 + cached * 2 + output_tokens * 20
    return {
        "usage_reported": True,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "uncached_input_tokens": uncached,
        "output_tokens": output_tokens,
        "total_tokens": total,
        "microcredits": microcredits,
    }


def generate_attempts(
    *,
    scenario: Mapping[str, Any],
    treatment: Mapping[str, Any],
    mutations: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Generate exact deterministic attempt observations for one scenario."""

    mutations = dict(mutations or {})
    treatment = dict(treatment)
    treatment.update(
        {
            key: value
            for key, value in mutations.items()
            if key
            in {
                "history_policy",
                "tool_output_policy",
                "budget_enforced",
                "no_progress_limit",
                "feature_path",
            }
        }
    )
    tool_events = {
        int(event["node_ordinal"]): int(event["bytes"])
        for event in scenario.get("tool_events", [])
    }
    no_progress_nodes = {
        int(value) for value in scenario.get("no_progress_nodes", [])
    }
    missing_usage_nodes = {
        int(value) for value in mutations.get("missing_usage_nodes", [])
    }
    unbound_call_nodes = {
        int(value) for value in mutations.get("unbound_call_nodes", [])
    }
    drop_evidence = bool(mutations.get("drop_required_evidence", False))
    drop_contradiction = bool(mutations.get("drop_required_contradiction", False))
    reuse_prompt = bool(mutations.get("reuse_prompt_across_steps", False))
    carry_previous_prompt = bool(
        mutations.get("carry_previous_compiled_prompt", False)
    )
    omit_cached_nodes = {
        int(value) for value in mutations.get("omit_cached_nodes", [])
    }
    attempts: list[dict[str, Any]] = []
    prior_tool_output_bytes = 0
    consecutive_no_progress = 0
    progress_units = 0
    for node in range(1, int(scenario["node_count"]) + 1):
        components, request_bytes, request_digest = _request_observation(
            scenario=scenario,
            treatment=treatment,
            node_ordinal=node,
            prior_completed_count=node - 1,
            prior_tool_output_bytes=prior_tool_output_bytes,
            previous_compiled_prompt_bytes=(
                attempts[-1]["request_serialized_bytes"]
                if carry_previous_prompt and attempts
                else 0
            ),
        )
        if reuse_prompt and attempts:
            request_digest = attempts[0]["request_payload_digest"]
        over_budget = request_bytes > int(scenario["request_budget_bytes"])
        no_progress_block = (
            consecutive_no_progress >= int(treatment["no_progress_limit"])
        )
        provider_called = not (
            (bool(treatment["budget_enforced"]) and over_budget)
            or no_progress_block
        )
        provider_initialized = provider_called
        progress_delta = (
            0 if node in no_progress_nodes or not provider_called else 1
        )
        if provider_called and not progress_delta:
            consecutive_no_progress += 1
        elif progress_delta:
            consecutive_no_progress = 0
            progress_units += 1
        usage = _usage_for_request(
            request_bytes=request_bytes,
            output_tokens=int(scenario["output_tokens_per_call"])
            if provider_called
            else 0,
            cached_fraction=float(scenario["cached_input_fraction"]),
            usage_reported=provider_called and node not in missing_usage_nodes,
        )
        if usage["usage_reported"] and node in omit_cached_nodes:
            usage.update(
                {
                    "usage_reported": False,
                    "input_tokens": None,
                    "cached_input_tokens": None,
                    "uncached_input_tokens": None,
                    "output_tokens": None,
                    "total_tokens": None,
                    "microcredits": None,
                }
            )
        tool_result_bytes = tool_events.get(node, 0) if provider_called else 0
        externalized_tool_bytes = (
            tool_result_bytes
            if treatment["tool_output_policy"] == "externalize"
            else 0
        )
        e5_return_bytes = int(scenario["bounded_e5_return_bytes"])
        if treatment["tool_output_policy"] == "inline":
            e5_return_bytes += tool_result_bytes
        attributable = provider_called and node not in unbound_call_nodes
        lineage = {
            "step_contract_digest": _sha(
                [scenario["workflow_id"], node, "L3ToL2"]
            ),
            "checkpoint_digest": _sha(
                [scenario["workflow_id"], node, "checkpoint"]
            ),
            "c0_projection_digest": _sha(
                [scenario["workflow_id"], node, "C0"]
            ),
            "compiled_request_digest": request_digest,
            "e1_context_digest": _sha(
                [scenario["workflow_id"], node, "E1"]
            ),
            "budget_verdict_digest": _sha(
                [
                    scenario["workflow_id"],
                    node,
                    "BLOCK" if over_budget else "ALLOW",
                ]
            ),
        }
        if provider_called and not attributable:
            lineage["e1_context_digest"] = None
        attempts.append(
            {
                "workflow_id": scenario["workflow_id"],
                "node_id": f"node-{node:03d}",
                "node_ordinal": node,
                "attempt_ordinal": 1,
                "call_ordinal": node,
                "call_purpose": "PRIMARY",
                **lineage,
                "request_payload_digest": request_digest,
                "request_serialized_bytes": request_bytes,
                "component_bytes": components,
                "tool_result_bytes": tool_result_bytes,
                "externalized_tool_result_bytes": externalized_tool_bytes,
                "e5_return_bytes": e5_return_bytes,
                "compaction_before_bytes": (
                    components["completed_history"]
                    + components["raw_tool_output"]
                ),
                "compaction_after_bytes": 0
                if treatment["history_policy"] == "bounded"
                else components["completed_history"],
                "provider_initialized": provider_initialized,
                "provider_called": provider_called,
                "provider_response_received": provider_called,
                **usage,
                "pricing_schedule_digest": scenario["pricing_schedule_digest"],
                "progress_before_digest": _sha(
                    [scenario["workflow_id"], progress_units]
                ),
                "progress_after_digest": _sha(
                    [scenario["workflow_id"], progress_units + progress_delta]
                ),
                "progress_delta": progress_delta,
                "unmet_requirement_delta": -progress_delta,
                "required_evidence_count": 0
                if drop_evidence
                else len(scenario["required_evidence_ids"]),
                "required_contradiction_count": 0
                if drop_contradiction
                else len(scenario["required_contradiction_ids"]),
                "budget_status": "BLOCK" if over_budget else "ALLOW",
                "terminal_outcome": (
                    scenario["terminal_class"]
                    if node == int(scenario["node_count"])
                    else "IN_PROGRESS"
                ),
            }
        )
        prior_tool_output_bytes = tool_result_bytes
    return attempts


def validate_attempts(attempts: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return stable measurement issue codes without inspecting content."""

    issues: set[str] = set()
    seen: set[tuple[str, int, int]] = set()
    request_steps: dict[str, str] = {}
    for attempt in attempts:
        key = (
            str(attempt["node_id"]),
            int(attempt["attempt_ordinal"]),
            int(attempt["call_ordinal"]),
        )
        if key in seen:
            issues.add("DUPLICATE_ATTEMPT_ORDINAL")
        seen.add(key)
        request_digest = str(attempt["request_payload_digest"])
        prior_step = request_steps.get(request_digest)
        if prior_step is not None and prior_step != str(attempt["node_id"]):
            issues.add("REQUEST_DIGEST_REUSED_ACROSS_STEPS")
        request_steps[request_digest] = str(attempt["node_id"])
        if attempt["usage_reported"] and attempt["cached_input_tokens"] is None:
            issues.add("CACHED_INPUT_MISSING")
        if int(attempt["cached_input_tokens"] or 0) > int(
            attempt["input_tokens"] or 0
        ):
            issues.add("CACHED_INPUT_EXCEEDS_INPUT")
        if attempt["provider_called"] and not attempt["usage_reported"]:
            issues.add("CALLED_PROVIDER_USAGE_MISSING")
        if attempt["usage_reported"]:
            input_tokens = int(attempt["input_tokens"])
            cached = int(attempt["cached_input_tokens"])
            uncached = int(attempt["uncached_input_tokens"])
            output = int(attempt["output_tokens"])
            total = int(attempt["total_tokens"])
            if input_tokens - cached != uncached:
                issues.add("UNCACHED_INPUT_MISMATCH")
            if input_tokens + output != total:
                issues.add("TOTAL_TOKEN_MISMATCH")
        if attempt["provider_called"] and not all(
            attempt.get(field)
            for field in (
                "step_contract_digest",
                "checkpoint_digest",
                "c0_projection_digest",
                "compiled_request_digest",
                "e1_context_digest",
                "budget_verdict_digest",
            )
        ):
            issues.add("UNATTRIBUTED_PROVIDER_CALL")
    return sorted(issues)


def reduce_attempts(
    attempts: Sequence[Mapping[str, Any]],
    *,
    warmup_nodes: int,
) -> dict[str, Any]:
    """Reduce raw attempt counters using deterministic algorithms."""

    request_sizes = [int(value["request_serialized_bytes"]) for value in attempts]
    reported = [value for value in attempts if value["usage_reported"]]
    called = [value for value in attempts if value["provider_called"]]
    post_warmup = [
        (int(value["node_ordinal"]), int(value["request_serialized_bytes"]))
        for value in attempts
        if int(value["node_ordinal"]) > warmup_nodes
    ]
    slope = _linear_slope(post_warmup)
    baseline_size = post_warmup[0][1] if post_warmup else 0
    progress_units = sum(int(value["progress_delta"]) for value in attempts)
    total_microcredits = sum(int(value["microcredits"] or 0) for value in reported)
    input_tokens = sum(int(value["input_tokens"] or 0) for value in reported)
    cached_input = sum(
        int(value["cached_input_tokens"] or 0) for value in reported
    )
    output_tokens = sum(int(value["output_tokens"] or 0) for value in reported)
    return {
        "attempt_count": len(attempts),
        "provider_initialization_count": sum(
            bool(value["provider_initialized"]) for value in attempts
        ),
        "provider_call_count": len(called),
        "usage_reported_call_count": len(reported),
        "usage_completeness": (
            round(len(reported) / len(called), 6) if called else 1.0
        ),
        "progress_unit_count": progress_units,
        "request_serialized_bytes_total": sum(request_sizes),
        "request_serialized_bytes_max": max(request_sizes, default=0),
        "request_serialized_bytes_p50": _nearest_rank(request_sizes, 0.5),
        "request_serialized_bytes_p95": _nearest_rank(request_sizes, 0.95),
        "post_warmup_growth_bytes_per_node": round(slope, 6),
        "post_warmup_growth_percent_per_node": round(
            (slope / baseline_size * 100) if baseline_size else 0.0,
            6,
        ),
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input,
        "uncached_input_tokens": input_tokens - cached_input,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "read_write_ratio": round(input_tokens / max(output_tokens, 1), 6),
        "cached_input_share": round(cached_input / max(input_tokens, 1), 6),
        "microcredits": total_microcredits,
        "microcredits_per_progress_unit": round(
            total_microcredits / max(progress_units, 1),
            6,
        ),
        "max_completed_history_bytes": max(
            (
                int(value["component_bytes"]["completed_history"])
                for value in attempts
            ),
            default=0,
        ),
        "max_raw_tool_output_bytes": max(
            (
                int(value["component_bytes"]["raw_tool_output"])
                for value in attempts
            ),
            default=0,
        ),
        "max_previous_compiled_prompt_bytes": max(
            (
                int(value["component_bytes"]["previous_compiled_prompt"])
                for value in attempts
            ),
            default=0,
        ),
        "max_tool_result_bytes": max(
            (int(value["tool_result_bytes"]) for value in attempts),
            default=0,
        ),
        "max_e5_return_bytes": max(
            (int(value["e5_return_bytes"]) for value in attempts),
            default=0,
        ),
        "no_progress_provider_call_count": sum(
            bool(value["provider_called"]) and not int(value["progress_delta"])
            for value in attempts
        ),
        "over_budget_provider_call_count": sum(
            bool(value["provider_called"]) and value["budget_status"] == "BLOCK"
            for value in attempts
        ),
        "compaction_reclaimed_bytes": sum(
            max(
                0,
                int(value["compaction_before_bytes"])
                - int(value["compaction_after_bytes"]),
            )
            for value in attempts
        ),
    }


def _hard_gates(
    *,
    attempts: Sequence[Mapping[str, Any]],
    aggregate: Mapping[str, Any],
    scenario: Mapping[str, Any],
    expected: Mapping[str, Any],
    declared_treatment: Mapping[str, Any],
    observed_treatment: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    issues: Sequence[str],
) -> dict[str, bool]:
    node10 = next(
        (
            int(value["request_serialized_bytes"])
            for value in attempts
            if int(value["node_ordinal"]) == 10
        ),
        0,
    )
    node_last = int(attempts[-1]["request_serialized_bytes"]) if attempts else 0
    node_ratio = node_last / node10 if node10 else 1.0
    no_progress_limit = int(declared_treatment["no_progress_limit"])
    no_progress_calls = int(aggregate["no_progress_provider_call_count"])
    return {
        "measurement_valid": not issues,
        "feature_path_executed": (
            declared_treatment["feature_path"] == "candidate"
            and observed_treatment["feature_path"] == "candidate"
        ),
        "treatment_binding_matches": (
            dict(observed_treatment) == dict(declared_treatment)
        ),
        "usage_complete": aggregate["usage_completeness"]
        >= float(thresholds["minimum_usage_completeness"]),
        "all_calls_attributable": "UNATTRIBUTED_PROVIDER_CALL" not in issues,
        "completed_history_absent": aggregate["max_completed_history_bytes"]
        <= int(thresholds["maximum_completed_history_bytes"]),
        "raw_tool_output_absent": aggregate["max_raw_tool_output_bytes"]
        <= int(thresholds["maximum_raw_tool_output_bytes"]),
        "previous_compiled_prompt_absent": aggregate[
            "max_previous_compiled_prompt_bytes"
        ]
        <= int(thresholds["maximum_previous_compiled_prompt_bytes"]),
        "requests_within_hard_limit": aggregate[
            "over_budget_provider_call_count"
        ]
        == 0,
        "request_plateau_bytes": aggregate[
            "post_warmup_growth_bytes_per_node"
        ]
        <= float(thresholds["maximum_growth_bytes_per_node"]),
        "request_plateau_percent": aggregate[
            "post_warmup_growth_percent_per_node"
        ]
        <= float(thresholds["maximum_growth_percent_per_node"]),
        "late_request_ratio_bounded": node_ratio
        <= float(thresholds["maximum_last_to_node10_ratio"]),
        "no_progress_calls_bounded": no_progress_calls <= no_progress_limit,
        "e5_return_bounded": aggregate["max_e5_return_bytes"]
        <= int(thresholds["maximum_e5_return_bytes"]),
        "required_evidence_preserved": all(
            int(value["required_evidence_count"])
            == len(expected["required_evidence_ids"])
            for value in attempts
        ),
        "required_contradictions_preserved": all(
            int(value["required_contradiction_count"])
            == len(expected["required_contradiction_ids"])
            for value in attempts
        ),
        "terminal_class_matches": bool(attempts)
        and attempts[-1]["terminal_outcome"] == expected["terminal_class"],
    }


def evaluate_workflow_scenario(
    *,
    evaluation_id: str,
    suite: Mapping[str, Any],
    scenario: Mapping[str, Any],
    case: Mapping[str, Any],
    treatment: Mapping[str, Any],
    mutations: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Evaluate one scenario and return a sealed measurement and case result."""

    attempts = generate_attempts(
        scenario=scenario,
        treatment=treatment,
        mutations=mutations,
    )
    issues = validate_attempts(attempts)
    if dict(mutations or {}).get("omit_cached_nodes"):
        issues = sorted(set(issues) | {"CACHED_INPUT_MISSING"})
    aggregate = reduce_attempts(
        attempts,
        warmup_nodes=int(suite["warmup_nodes"]),
    )
    effective_treatment = dict(treatment)
    effective_treatment.update(
        {
            key: value
            for key, value in dict(mutations or {}).items()
            if key in effective_treatment
        }
    )
    gates = _hard_gates(
        attempts=attempts,
        aggregate=aggregate,
        scenario=scenario,
        expected=case["expected"],
        declared_treatment=treatment,
        observed_treatment=effective_treatment,
        thresholds=suite["thresholds"],
        issues=issues,
    )
    measurement_status = "VALID" if not issues else "INVALID"
    subject_status = (
        "MEASUREMENT_INVALID"
        if issues
        else "PASS"
        if all(gates.values())
        else "FAIL"
    )
    measurement_id = stable_id(
        "eval-measurement",
        evaluation_id,
        case["case_id"],
        treatment["treatment_id"],
        length=28,
    )
    measurement = create_versioned_artifact(
        "eval-execution-measurement",
        "1.0.0",
        measurement_id,
        measurement_id=measurement_id,
        evaluation_id=evaluation_id,
        suite_id=suite["suite_id"],
        case_id=case["case_id"],
        measurement_profile=suite["measurement_profile"],
        invariant_bindings_digest=_binding_digest(suite["invariant_bindings"]),
        treatment_bindings=effective_treatment,
        attempts=attempts,
        aggregate=aggregate,
        measurement_status=measurement_status,
        issues=issues,
        measured_at=utc_now(),
        extensions={},
    )
    cause_codes = sorted(
        set(issues)
        | {name.upper() for name, passed in gates.items() if not passed}
    )
    case_result = {
        "case_id": case["case_id"],
        "critical": bool(case["critical"]),
        "status": subject_status,
        "cause_codes": cause_codes,
        "hard_gates": gates,
        "measurement_artifact_id": measurement["artifact_id"],
        "measurement_digest": measurement["canonical_digest"]["value"],
        "measurement_path": "",
        "terminal_class": attempts[-1]["terminal_outcome"],
        "provider_call_count": aggregate["provider_call_count"],
        "progress_unit_count": aggregate["progress_unit_count"],
        "microcredits": aggregate["microcredits"],
    }
    return measurement, case_result


def _result_aggregate(
    case_results: Sequence[Mapping[str, Any]],
    measurements: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    measurement_aggregates = [value["aggregate"] for value in measurements]
    passed = sum(value["status"] == "PASS" for value in case_results)
    return {
        "case_count": len(case_results),
        "passed_case_count": passed,
        "failed_case_count": sum(value["status"] == "FAIL" for value in case_results),
        "invalid_case_count": sum(
            value["status"] == "MEASUREMENT_INVALID" for value in case_results
        ),
        "critical_case_failure_count": sum(
            bool(value["critical"]) and value["status"] != "PASS"
            for value in case_results
        ),
        "provider_call_count": sum(
            int(value["provider_call_count"]) for value in case_results
        ),
        "progress_unit_count": sum(
            int(value["progress_unit_count"]) for value in case_results
        ),
        "input_tokens": sum(
            int(value["input_tokens"]) for value in measurement_aggregates
        ),
        "cached_input_tokens": sum(
            int(value["cached_input_tokens"]) for value in measurement_aggregates
        ),
        "uncached_input_tokens": sum(
            int(value["uncached_input_tokens"]) for value in measurement_aggregates
        ),
        "output_tokens": sum(
            int(value["output_tokens"]) for value in measurement_aggregates
        ),
        "microcredits": sum(
            int(value["microcredits"]) for value in measurement_aggregates
        ),
        "microcredits_per_progress_unit": round(
            sum(int(value["microcredits"]) for value in measurement_aggregates)
            / max(
                sum(
                    int(value["progress_unit_count"])
                    for value in measurement_aggregates
                ),
                1,
            ),
            6,
        ),
        "request_serialized_bytes_max": max(
            (
                int(value["request_serialized_bytes_max"])
                for value in measurement_aggregates
            ),
            default=0,
        ),
        "request_serialized_bytes_p95": max(
            (
                int(value["request_serialized_bytes_p95"])
                for value in measurement_aggregates
            ),
            default=0,
        ),
        "max_post_warmup_growth_percent_per_node": max(
            (
                float(value["post_warmup_growth_percent_per_node"])
                for value in measurement_aggregates
            ),
            default=0.0,
        ),
        "max_completed_history_bytes": max(
            (
                int(value["max_completed_history_bytes"])
                for value in measurement_aggregates
            ),
            default=0,
        ),
        "max_raw_tool_output_bytes": max(
            (
                int(value["max_raw_tool_output_bytes"])
                for value in measurement_aggregates
            ),
            default=0,
        ),
        "max_previous_compiled_prompt_bytes": max(
            (
                int(value["max_previous_compiled_prompt_bytes"])
                for value in measurement_aggregates
            ),
            default=0,
        ),
        "usage_completeness": min(
            (
                float(value["usage_completeness"])
                for value in measurement_aggregates
            ),
            default=1.0,
        ),
        "hard_gate_failure_count": sum(
            not passed
            for value in case_results
            for passed in value["hard_gates"].values()
        ),
    }


def run_workflow_suite(
    *,
    suite_path: Path,
    output_root: Path,
    treatment_id: str,
    evaluation_id: str | None = None,
) -> tuple[dict[str, Any], Path]:
    """Run a digest-bound workflow suite without network or runtime mutation."""

    suite, workload_path, workload = load_workflow_suite(suite_path)
    treatment = _treatment(suite, treatment_id)
    evaluation_id = evaluation_id or stable_id(
        "workflow-eval",
        suite["suite_id"],
        treatment_id,
        utc_now(),
        length=28,
    )
    root = Path(output_root).expanduser().resolve()
    evaluation_dir = root / "workflow-evaluations" / evaluation_id
    if evaluation_dir.exists():
        raise FileExistsError(evaluation_dir)
    evaluation_dir.mkdir(parents=True, exist_ok=False)
    case_results: list[dict[str, Any]] = []
    measurements: list[dict[str, Any]] = []
    for case in suite["cases"]:
        scenario = _scenario(workload, case["scenario_id"])
        measurement, case_result = evaluate_workflow_scenario(
            evaluation_id=evaluation_id,
            suite=suite,
            scenario=scenario,
            case=case,
            treatment=treatment,
        )
        case_dir = evaluation_dir / "cases" / case["case_id"]
        case_dir.mkdir(parents=True, exist_ok=False)
        measurement_path = case_dir / "execution-measurement.json"
        write_json_exclusive(measurement_path, measurement)
        case_result["measurement_path"] = str(measurement_path)
        write_json_exclusive(case_dir / "case-result.json", case_result)
        measurements.append(measurement)
        case_results.append(case_result)
    aggregate = _result_aggregate(case_results, measurements)
    if any(value["status"] == "MEASUREMENT_INVALID" for value in case_results):
        status = "MEASUREMENT_INVALID"
    elif any(value["status"] != "PASS" for value in case_results):
        status = "FAIL"
    else:
        status = "PASS"
    result = create_versioned_artifact(
        "eval-run-result",
        "2.0.0",
        evaluation_id,
        evaluation_id=evaluation_id,
        suite_id=suite["suite_id"],
        suite_digest=suite["canonical_digest"]["value"],
        suite_file_sha256=sha256_file(Path(suite_path).expanduser().resolve()),
        subject="workflow_replay",
        workload_id=workload["workload_id"],
        workload_sha256=sha256_file(workload_path),
        measurement_profile=suite["measurement_profile"],
        experiment_manifest={
            "invariant_bindings": dict(suite["invariant_bindings"]),
            "invariant_bindings_digest": _binding_digest(
                suite["invariant_bindings"]
            ),
            "treatment_bindings": treatment,
            "evaluator_version": EVALUATOR_VERSION,
            "evaluator_digest": suite["evaluator"]["digest"],
            "threshold_policy_digest": _binding_digest(suite["thresholds"]),
        },
        treatment_id=treatment_id,
        case_results=case_results,
        aggregate=aggregate,
        thresholds=dict(suite["thresholds"]),
        status=status,
        evaluated_at=utc_now(),
        extensions={"com.partner_advisory.nee.content-emitted": False},
    )
    result_path = evaluation_dir / "evaluation-result.json"
    write_json_exclusive(result_path, result)
    return result, result_path


def compare_workflow_results(
    *,
    baseline_path: Path,
    candidate_path: Path,
) -> dict[str, Any]:
    """Compare workflow results while separating invariants from treatments."""

    baseline = read_json(Path(baseline_path).expanduser().resolve())
    candidate = read_json(Path(candidate_path).expanduser().resolve())
    validate_artifact(baseline)
    validate_artifact(candidate)
    if baseline["contract_id"] != "urn:nee:dual-pipeline:eval-run-result:2.0.0":
        raise AssuranceFailure("BASELINE_CONTRACT", "expected eval-run-result v2")
    if candidate["contract_id"] != baseline["contract_id"]:
        raise AssuranceFailure("CANDIDATE_CONTRACT", "result versions differ")
    invariant_keys = (
        "suite_id",
        "suite_digest",
        "suite_file_sha256",
        "workload_id",
        "workload_sha256",
        "measurement_profile",
    )
    mismatch = [
        key for key in invariant_keys if baseline[key] != candidate[key]
    ]
    if (
        baseline["experiment_manifest"]["invariant_bindings_digest"]
        != candidate["experiment_manifest"]["invariant_bindings_digest"]
    ):
        mismatch.append("invariant_bindings_digest")
    for key in (
        "evaluator_version",
        "evaluator_digest",
        "threshold_policy_digest",
    ):
        if (
            baseline["experiment_manifest"][key]
            != candidate["experiment_manifest"][key]
        ):
            mismatch.append(key)
    metrics = (
        ("provider_call_count", "lower"),
        ("input_tokens", "lower"),
        ("microcredits", "lower"),
        ("microcredits_per_progress_unit", "lower"),
        ("request_serialized_bytes_p95", "lower"),
        ("max_completed_history_bytes", "lower"),
        ("max_raw_tool_output_bytes", "lower"),
        ("hard_gate_failure_count", "lower"),
    )
    deltas = []
    for name, direction in metrics:
        before = float(baseline["aggregate"][name])
        after = float(candidate["aggregate"][name])
        deltas.append(
            {
                "metric": name,
                "baseline": before,
                "candidate": after,
                "delta": after - before,
                "direction": direction,
                "tolerance": 0.0,
                "regression": after > before,
            }
        )
    before_credits = float(baseline["aggregate"]["microcredits_per_progress_unit"])
    after_credits = float(candidate["aggregate"]["microcredits_per_progress_unit"])
    credit_improvement = (
        (before_credits - after_credits) / before_credits
        if before_credits
        else 0.0
    )
    minimum_improvement = float(
        candidate["thresholds"]["minimum_credit_improvement_fraction"]
    )
    causes: list[str] = []
    if mismatch:
        status = "NOT_COMPARABLE"
        causes.append("INVARIANT_BINDING_MISMATCH")
    else:
        if candidate["status"] != "PASS":
            causes.append("CANDIDATE_NOT_PASSING")
        if candidate["aggregate"]["provider_call_count"] > baseline["aggregate"][
            "provider_call_count"
        ]:
            causes.append("PROVIDER_CALL_COUNT_REGRESSION")
        if credit_improvement < minimum_improvement:
            causes.append("CREDIT_IMPROVEMENT_BELOW_TARGET")
        status = "PASS" if not causes else "FAIL"
    comparison_id = stable_id(
        "workflow-compare",
        baseline["evaluation_id"],
        candidate["evaluation_id"],
        length=28,
    )
    return create_versioned_artifact(
        "eval-comparison",
        "2.0.0",
        comparison_id,
        comparison_id=comparison_id,
        baseline_evaluation_id=baseline["evaluation_id"],
        baseline_digest=baseline["canonical_digest"]["value"],
        candidate_evaluation_id=candidate["evaluation_id"],
        candidate_digest=candidate["canonical_digest"]["value"],
        suite_id=baseline["suite_id"],
        invariant_bindings_digest=baseline["experiment_manifest"][
            "invariant_bindings_digest"
        ],
        declared_treatment_differences={
            "baseline": baseline["experiment_manifest"]["treatment_bindings"],
            "candidate": candidate["experiment_manifest"]["treatment_bindings"],
        },
        metric_deltas=deltas,
        credit_improvement_fraction=round(credit_improvement, 6),
        minimum_credit_improvement_fraction=minimum_improvement,
        status=status,
        cause_codes=sorted(set(causes)),
        compared_at=utc_now(),
        extensions={
            "com.partner_advisory.nee.invariant-mismatches": sorted(set(mismatch))
        },
    )


__all__ = [
    "AssuranceFailure",
    "EVALUATOR_VERSION",
    "compare_workflow_results",
    "evaluate_workflow_scenario",
    "generate_attempts",
    "load_workflow_suite",
    "reduce_attempts",
    "run_workflow_suite",
    "validate_attempts",
]
