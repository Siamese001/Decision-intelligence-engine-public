"""NEE governance semantic validations and domain rules."""
from __future__ import annotations

import hashlib
from typing import Any

from .issues import fail
from .registry import SENSITIVE_OTEL_KEY


def require_unique_ids(values: list[dict[str, Any]], field: str, path: str) -> None:
    ids = [item.get(field) for item in values]
    if len(ids) != len(set(ids)):
        fail("SEMANTIC_DUPLICATE_ID", path, f"duplicate {field}")


VALID_ENTITY_PREFIXES = (
    "person:", "org:", "system:", "workstream:", "project:", "metric:", "role:", "policy:", "stakeholder:", "document:",
    "deliverable:", "milestone:", "forum:", "model:", "initiative:", "capability:", "pattern:", "decision:", "proposal:",
)


def validate_canonical_signal_projection_extension(projection: Any, path: str) -> None:
    """Validate canonical signal projection in extensions against mutation schema invariants."""
    if not isinstance(projection, dict):
        fail("SEMANTIC_CANONICAL_SIGNAL_PROJECTION", path, "projection must be a JSON object")

    mutations = projection.get("graph_mutations", projection)
    if not isinstance(mutations, dict):
        fail("SEMANTIC_CANONICAL_SIGNAL_PROJECTION", f"{path}.graph_mutations", "mutations must be a JSON object")

    allowed_categories = {
        "entity_upserts",
        "relationship_signals",
        "decision_updates",
        "proposal_updates",
        "action_updates",
        "risk_updates",
        "strategic_intent_signals",
    }

    for key, items in mutations.items():
        if key not in allowed_categories:
            continue
        if not isinstance(items, list):
            fail("SEMANTIC_CANONICAL_SIGNAL_PROJECTION", f"{path}.{key}", f"{key} must be an array")

        if key == "entity_upserts":
            for idx, item in enumerate(items):
                if not isinstance(item, dict):
                    fail("SEMANTIC_CANONICAL_SIGNAL_PROJECTION", f"{path}.{key}[{idx}]", "entity must be an object")
                ent_id = item.get("id")
                if not isinstance(ent_id, str) or not any(ent_id.startswith(p) for p in VALID_ENTITY_PREFIXES):
                    fail("SEMANTIC_INVALID_ENTITY_PREFIX", f"{path}.{key}[{idx}].id", f"invalid entity prefix: {ent_id}")

        elif key == "relationship_signals":
            for idx, item in enumerate(items):
                if not isinstance(item, dict):
                    fail("SEMANTIC_CANONICAL_SIGNAL_PROJECTION", f"{path}.{key}[{idx}]", "relationship must be an object")
                from_id = item.get("from_id")
                to_id = item.get("to_id")
                if not isinstance(from_id, str) or not any(from_id.startswith(p) for p in VALID_ENTITY_PREFIXES):
                    fail("SEMANTIC_INVALID_ENDPOINT_PREFIX", f"{path}.{key}[{idx}].from_id", f"invalid from_id prefix: {from_id}")
                if not isinstance(to_id, str) or not any(to_id.startswith(p) for p in VALID_ENTITY_PREFIXES):
                    fail("SEMANTIC_INVALID_ENDPOINT_PREFIX", f"{path}.{key}[{idx}].to_id", f"invalid to_id prefix: {to_id}")

        elif key == "decision_updates":
            for idx, item in enumerate(items):
                if not isinstance(item, dict):
                    fail("SEMANTIC_CANONICAL_SIGNAL_PROJECTION", f"{path}.{key}[{idx}]", "decision must be an object")
                did = item.get("decision_id")
                if not isinstance(did, str) or not did.startswith("decision:"):
                    fail("SEMANTIC_INVALID_DECISION_ID", f"{path}.{key}[{idx}].decision_id", f"decision_id must start with 'decision:': {did}")

        elif key == "proposal_updates":
            for idx, item in enumerate(items):
                if not isinstance(item, dict):
                    fail("SEMANTIC_CANONICAL_SIGNAL_PROJECTION", f"{path}.{key}[{idx}]", "proposal must be an object")
                pid = item.get("proposal_id")
                if not isinstance(pid, str) or not pid.startswith("proposal:"):
                    fail("SEMANTIC_INVALID_PROPOSAL_ID", f"{path}.{key}[{idx}].proposal_id", f"proposal_id must start with 'proposal:': {pid}")

        elif key == "risk_updates":
            for idx, item in enumerate(items):
                if not isinstance(item, dict):
                    fail("SEMANTIC_CANONICAL_SIGNAL_PROJECTION", f"{path}.{key}[{idx}]", "risk must be an object")
                rid = item.get("risk_id")
                if not isinstance(rid, str) or not rid.startswith("risk:"):
                    fail("SEMANTIC_INVALID_RISK_ID", f"{path}.{key}[{idx}].risk_id", f"risk_id must start with 'risk:': {rid}")


def validate_strategic_profile_semantics(
    profile: dict[str, Any],
    path: str,
) -> None:
    """Enforce cross-field strategic meaning independent of schema-if support."""

    role = profile["knowledge_role"]
    modality = profile["modality"]
    authority = profile["authority_scope"]
    applicability = profile["applicability_scope"]
    realization = profile["realization_state"]
    if role in {"official_intent", "target_outcome"} and (
        authority != "official_project_intent"
        or modality not in {"intended", "normative"}
        or realization not in {"intended", "planned"}
    ):
        fail(
            "SEMANTIC_INTENT_REALIZATION_LEAKAGE",
            path,
            "official intent and target outcomes require official-intent scope without realized-outcome posture",
        )
    if role == "external_pattern" and (
        authority != "external_reference"
        or applicability not in {"generalizable", "analogous_only"}
        or realization != "not_applicable"
    ):
        fail(
            "SEMANTIC_EXTERNAL_INTERNAL_LEAKAGE",
            path,
            "external patterns require external-reference authority and non-realization posture",
        )
    if role == "observed_outcome" and (
        modality != "observed" or realization not in {"observed", "measured"}
    ):
        fail(
            "SEMANTIC_OUTCOME_POSTURE",
            path,
            "observed outcomes require observed or measured realization",
        )
    if realization in {"observed", "measured"} and modality in {
        "intended",
        "proposed",
    }:
        fail(
            "SEMANTIC_INTENT_REALIZATION_LEAKAGE",
            path,
            "intended or proposed material cannot claim realized outcomes",
        )


def validate_eval_measurement_semantics(artifact: dict[str, Any]) -> None:
    attempts = artifact["attempts"]
    attempt_keys = [
        (
            attempt["node_id"],
            attempt["attempt_ordinal"],
            attempt["call_ordinal"],
        )
        for attempt in attempts
    ]
    if len(attempt_keys) != len(set(attempt_keys)):
        fail(
            "SEMANTIC_ATTEMPT_ORDINALS",
            "$.attempts",
            "node, attempt, and call ordinals must identify a unique attempt",
        )

    token_fields = (
        "input_tokens",
        "cached_input_tokens",
        "uncached_input_tokens",
        "output_tokens",
        "total_tokens",
    )
    for index, attempt in enumerate(attempts):
        path = f"$.attempts[{index}]"
        if sum(attempt["component_bytes"].values()) > attempt["request_serialized_bytes"]:
            fail(
                "SEMANTIC_REQUEST_COMPONENT_TOTAL",
                f"{path}.component_bytes",
                "request components cannot exceed the final serialized request",
            )
        if attempt["compaction_after_bytes"] > attempt["compaction_before_bytes"]:
            fail(
                "SEMANTIC_COMPACTION_GROWTH",
                f"{path}.compaction_after_bytes",
                "compaction may not increase the measured packet",
            )
        if attempt["externalized_tool_result_bytes"] > attempt["tool_result_bytes"]:
            fail(
                "SEMANTIC_TOOL_EXTERNALIZATION_TOTAL",
                f"{path}.externalized_tool_result_bytes",
                "externalized tool bytes cannot exceed tool result bytes",
            )

        values = [attempt[field] for field in token_fields]
        all_present = all(value is not None for value in values)
        all_absent = all(value is None for value in values)
        if not (all_present or all_absent):
            fail(
                "SEMANTIC_PARTIAL_TOKEN_USAGE",
                path,
                "token usage must be wholly present or wholly unavailable",
            )
        if attempt["usage_reported"] != all_present:
            fail(
                "SEMANTIC_USAGE_REPORTING_STATUS",
                f"{path}.usage_reported",
                "usage_reported must match complete token counters",
            )
        if all_present:
            if attempt["microcredits"] is None:
                fail(
                    "SEMANTIC_PARTIAL_TOKEN_USAGE",
                    f"{path}.microcredits",
                    "reported usage requires microcredits",
                )
            if attempt["cached_input_tokens"] > attempt["input_tokens"]:
                fail(
                    "SEMANTIC_CACHED_INPUT_EXCEEDS_INPUT",
                    f"{path}.cached_input_tokens",
                    "cached input cannot exceed total input",
                )
            if attempt["uncached_input_tokens"] != (
                attempt["input_tokens"] - attempt["cached_input_tokens"]
            ):
                fail(
                    "SEMANTIC_UNCACHED_INPUT_TOTAL",
                    f"{path}.uncached_input_tokens",
                    "uncached input must equal input minus cached input",
                )
            if attempt["total_tokens"] != (
                attempt["input_tokens"] + attempt["output_tokens"]
            ):
                fail(
                    "SEMANTIC_TOKEN_TOTAL",
                    f"{path}.total_tokens",
                    "total tokens must equal input plus output",
                )
        elif attempt["microcredits"] is not None:
            fail(
                "SEMANTIC_PARTIAL_TOKEN_USAGE",
                f"{path}.microcredits",
                "unreported usage cannot carry microcredits",
            )

        if attempt["provider_response_received"] and not attempt["provider_called"]:
            fail(
                "SEMANTIC_PROVIDER_EVENT_ORDER",
                f"{path}.provider_response_received",
                "a provider response requires a provider call",
            )
        if attempt["provider_called"] and not attempt["provider_initialized"]:
            fail(
                "SEMANTIC_PROVIDER_EVENT_ORDER",
                f"{path}.provider_called",
                "a provider call requires provider initialization",
            )
        if attempt["provider_called"] and artifact["measurement_status"] == "VALID":
            if not all_present:
                fail(
                    "SEMANTIC_MISSING_PROVIDER_USAGE",
                    path,
                    "valid measurements require usage for every provider call",
                )
            lineage_fields = (
                "step_contract_digest",
                "checkpoint_digest",
                "c0_projection_digest",
                "compiled_request_digest",
                "e1_context_digest",
                "budget_verdict_digest",
            )
            if any(attempt[field] is None for field in lineage_fields):
                fail(
                    "SEMANTIC_UNATTRIBUTED_PROVIDER_CALL",
                    path,
                    "valid measurements require complete call lineage",
                )

    if (artifact["measurement_status"] == "VALID") != (not artifact["issues"]):
        fail(
            "SEMANTIC_MEASUREMENT_ISSUES",
            "$.issues",
            "valid measurements have no issues and invalid measurements have at least one",
        )

    aggregate = artifact["aggregate"]
    expected_counts = {
        "attempt_count": len(attempts),
        "provider_initialization_count": sum(
            attempt["provider_initialized"] for attempt in attempts
        ),
        "provider_call_count": sum(attempt["provider_called"] for attempt in attempts),
        "usage_reported_call_count": sum(
            attempt["usage_reported"] for attempt in attempts
        ),
        "progress_unit_count": sum(attempt["progress_delta"] for attempt in attempts),
        "request_serialized_bytes_total": sum(
            attempt["request_serialized_bytes"] for attempt in attempts
        ),
        "request_serialized_bytes_max": max(
            attempt["request_serialized_bytes"] for attempt in attempts
        ),
        "max_completed_history_bytes": max(
            attempt["component_bytes"]["completed_history"] for attempt in attempts
        ),
        "max_raw_tool_output_bytes": max(
            attempt["component_bytes"]["raw_tool_output"] for attempt in attempts
        ),
        "max_previous_compiled_prompt_bytes": max(
            attempt["component_bytes"]["previous_compiled_prompt"]
            for attempt in attempts
        ),
        "max_tool_result_bytes": max(attempt["tool_result_bytes"] for attempt in attempts),
        "max_e5_return_bytes": max(attempt["e5_return_bytes"] for attempt in attempts),
        "no_progress_provider_call_count": sum(
            attempt["provider_called"] and attempt["progress_delta"] == 0
            for attempt in attempts
        ),
        "over_budget_provider_call_count": sum(
            attempt["provider_called"] and attempt["budget_status"] == "BLOCK"
            for attempt in attempts
        ),
        "compaction_reclaimed_bytes": sum(
            max(
                0,
                attempt["compaction_before_bytes"]
                - attempt["compaction_after_bytes"],
            )
            for attempt in attempts
        ),
    }
    for field, expected in expected_counts.items():
        if aggregate[field] != expected:
            fail(
                "SEMANTIC_MEASUREMENT_AGGREGATE",
                f"$.aggregate.{field}",
                f"expected aggregate value {expected}",
            )

    reported_attempts = [attempt for attempt in attempts if attempt["usage_reported"]]
    for field in token_fields:
        expected = sum(attempt[field] for attempt in reported_attempts)
        if aggregate[field] != expected:
            fail(
                "SEMANTIC_MEASUREMENT_AGGREGATE",
                f"$.aggregate.{field}",
                f"expected reconciled token aggregate {expected}",
            )
    expected_credits = sum(attempt["microcredits"] for attempt in reported_attempts)
    if aggregate["microcredits"] != expected_credits:
        fail(
            "SEMANTIC_MEASUREMENT_AGGREGATE",
            "$.aggregate.microcredits",
            f"expected reconciled microcredit aggregate {expected_credits}",
        )
    expected_completeness = (
        round(
            aggregate["usage_reported_call_count"]
            / aggregate["provider_call_count"],
            6,
        )
        if aggregate["provider_call_count"]
        else 1.0
    )
    if aggregate["usage_completeness"] != expected_completeness:
        fail(
            "SEMANTIC_MEASUREMENT_AGGREGATE",
            "$.aggregate.usage_completeness",
            f"expected usage completeness {expected_completeness}",
        )
    if aggregate["uncached_input_tokens"] != (
        aggregate["input_tokens"] - aggregate["cached_input_tokens"]
    ):
        fail(
            "SEMANTIC_UNCACHED_INPUT_TOTAL",
            "$.aggregate.uncached_input_tokens",
            "aggregate uncached input must equal input minus cached input",
        )
    if aggregate["total_tokens"] != (
        aggregate["input_tokens"] + aggregate["output_tokens"]
    ):
        fail(
            "SEMANTIC_TOKEN_TOTAL",
            "$.aggregate.total_tokens",
            "aggregate total tokens must equal input plus output",
                )


def validate_query_route_eval_suite_semantics(artifact: dict[str, Any]) -> None:
    case_ids = [case["case_id"] for case in artifact["cases"]]
    if len(case_ids) != len(set(case_ids)):
        fail("SEMANTIC_DUPLICATE_ROUTE_CASE", "$.cases", "route case IDs must be unique")
    destinations = {case["expected"]["destination"] for case in artifact["cases"]}
    if not destinations & {"GRAPH_EVIDENCE_UTILITY", "NEE_DECISION_INTELLIGENCE"}:
        fail(
            "SEMANTIC_ROUTE_CORPUS_NO_GRAPH_POSITIVE",
            "$.cases",
            "route corpus must contain at least one graph-entering positive",
        )
    if destinations <= {"GRAPH_EVIDENCE_UTILITY", "NEE_DECISION_INTELLIGENCE"}:
        fail(
            "SEMANTIC_ROUTE_CORPUS_NO_NEGATIVE",
            "$.cases",
            "route corpus must contain at least one non-graph negative",
        )
    if not any(case["critical"] for case in artifact["cases"]):
        fail(
            "SEMANTIC_ROUTE_CORPUS_NO_CRITICAL_CASE",
            "$.cases",
            "route corpus must label at least one critical case",
        )


def validate_query_route_eval_result_semantics(artifact: dict[str, Any]) -> None:
    rows = artifact["case_results"]
    metrics = artifact["metrics"]
    expected_case_count = len(rows)
    correct = sum(row["status"] == "PASS" for row in rows)
    graph_tp = sum(
        row["expected_enters_query_runtime"] and row["predicted_enters_query_runtime"]
        for row in rows
    )
    graph_fp = sum(
        not row["expected_enters_query_runtime"] and row["predicted_enters_query_runtime"]
        for row in rows
    )
    graph_fn = sum(
        row["expected_enters_query_runtime"] and not row["predicted_enters_query_runtime"]
        for row in rows
    )
    nee_expected = sum(
        row["expected_destination"] == "NEE_DECISION_INTELLIGENCE" for row in rows
    )
    nee_tp = sum(
        row["expected_destination"] == "NEE_DECISION_INTELLIGENCE"
        and row["predicted_destination"] == "NEE_DECISION_INTELLIGENCE"
        for row in rows
    )
    critical_fn = sum(
        row["critical"]
        and row["expected_enters_query_runtime"]
        and not row["predicted_enters_query_runtime"]
        for row in rows
    )

    def ratio(numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator else 1.0

    expected_metrics = {
        "case_count": expected_case_count,
        "correct_case_count": correct,
        "destination_accuracy": ratio(correct, expected_case_count),
        "graph_entry_true_positive_count": graph_tp,
        "graph_entry_false_positive_count": graph_fp,
        "graph_entry_false_negative_count": graph_fn,
        "graph_entry_precision": ratio(graph_tp, graph_tp + graph_fp),
        "graph_entry_recall": ratio(graph_tp, graph_tp + graph_fn),
        "nee_decision_expected_count": nee_expected,
        "nee_decision_true_positive_count": nee_tp,
        "nee_decision_false_negative_count": nee_expected - nee_tp,
        "nee_decision_recall": ratio(nee_tp, nee_expected),
        "critical_false_negative_count": critical_fn,
    }
    if metrics != expected_metrics:
        fail(
            "SEMANTIC_ROUTE_RESULT_METRICS",
            "$.metrics",
            "route metrics must exactly reconcile to case results",
        )
    expected_confusion: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row["expected_destination"], row["predicted_destination"])
        expected_confusion[key] = expected_confusion.get(key, 0) + 1
    actual_confusion = {
        (row["expected_destination"], row["predicted_destination"]): row["count"]
        for row in artifact["confusion_cells"]
    }
    if actual_confusion != expected_confusion:
        fail(
            "SEMANTIC_ROUTE_RESULT_CONFUSION",
            "$.confusion_cells",
            "confusion cells must exactly reconcile to case results",
        )
    if (artifact["status"] == "PASS") != all(artifact["gates"].values()):
        fail(
            "SEMANTIC_ROUTE_RESULT_STATUS",
            "$.status",
            "route result status must match all declared gates",
        )


def validate_eval_suite_v3_semantics(artifact: dict[str, Any]) -> None:
    case_ids = [case["case_id"] for case in artifact["cases"]]
    if len(case_ids) != len(set(case_ids)):
        fail("SEMANTIC_DUPLICATE_EVAL_CASE", "$.cases", "evaluation case IDs must be unique")
    for index, case in enumerate(artifact["cases"]):
        if case["pipeline"] != "query":
            continue
        path = f"$.cases[{index}]"
        expected = case["expected"]
        if not expected["required_record_ids"]:
            fail(
                "SEMANTIC_VACUOUS_RETRIEVAL_ORACLE",
                f"{path}.expected.required_record_ids",
                "every graph-entering query case must name at least one required record",
            )
        if not expected["required_requirement_assertions"]:
            fail(
                "SEMANTIC_VACUOUS_COVERAGE_ORACLE",
                f"{path}.expected.required_requirement_assertions",
                "every graph-entering query case must assert requirement-specific coverage",
            )
        if not expected["required_lanes"]:
            fail(
                "SEMANTIC_VACUOUS_LANE_ORACLE",
                f"{path}.expected.required_lanes",
                "every graph-entering query case must assert at least one evidence lane",
            )
        if expected["minimum_connected_path_count"] < 1:
            fail(
                "SEMANTIC_VACUOUS_PATH_ORACLE",
                f"{path}.expected.minimum_connected_path_count",
                "every graph-entering query case must require a connected evidence path",
            )
        pre_l2_block = (
            expected["status"] == "BLOCKED"
            and expected["x3_outcome"] == "X3B_ESCALATE_HITL"
            and "PA" not in expected["required_stages"]
            and "L2" not in expected["required_stages"]
        )
        if not expected["runtime_judge_receipt_required"] and not pre_l2_block:
            fail(
                "SEMANTIC_RUNTIME_JUDGE_RECEIPT_NOT_REQUIRED",
                f"{path}.expected.runtime_judge_receipt_required",
                "post-runtime evaluation must verify the runtime X1 receipt unless the case is an explicit pre-L2 HITL block",
            )
        graph_scope = case["evaluation_scope"] == "graph_retrieval_qualification"
        if graph_scope != expected["semantic_retrieval_qualified"]:
            fail(
                "SEMANTIC_RETRIEVAL_QUALIFICATION_SCOPE",
                path,
                "semantic qualification must be true only for graph-retrieval qualification cases",
            )
        required_embedding_posture = "real_bge_m3" if graph_scope else "not_qualified"
        if case["embedding_qualification"] != required_embedding_posture:
            fail(
                "SEMANTIC_RETRIEVAL_BACKEND_POSTURE",
                f"{path}.embedding_qualification",
                f"expected {required_embedding_posture}",
            )
        graph_destination = case["query_type"] in {
            "exact_lookup",
            "current_state",
            "current_state_corroboration",
            # Weekly project status is deliberately a citation-only evidence
            # route.  Treating it as decision intelligence would permit a
            # durable-fact or provider-synthesis posture that the runtime
            # explicitly forbids.
            "project_status_current",
            "project_status_history",
        }
        expected_destination = (
            "GRAPH_EVIDENCE_UTILITY"
            if graph_destination
            else "NEE_DECISION_INTELLIGENCE"
        )
        if expected["expected_destination"] != expected_destination:
            fail(
                "SEMANTIC_QUERY_DESTINATION_ORACLE",
                f"{path}.expected.expected_destination",
                f"query type requires {expected_destination}",
            )
        if graph_destination != (expected["expected_assurance_profile"] is None):
            fail(
                "SEMANTIC_QUERY_ASSURANCE_ORACLE",
                f"{path}.expected.expected_assurance_profile",
                "graph utility requires null assurance; NEE reasoning requires a profile",
            )
        quality = case.get("quality_control")
        expected_mode = quality["mode"] if quality else "off"
        if expected["expected_runtime_quality_mode"] != expected_mode:
            fail(
                "SEMANTIC_RUNTIME_QUALITY_MODE_ORACLE",
                f"{path}.expected.expected_runtime_quality_mode",
                f"expected {expected_mode}",
            )
    if artifact["thresholds"]["maximum_unqualified_retrieval_case_count"] != 0:
        fail(
            "SEMANTIC_UNQUALIFIED_RETRIEVAL_THRESHOLD",
            "$.thresholds.maximum_unqualified_retrieval_case_count",
            "unqualified retrieval cases may not be accepted",
        )


def validate_semantics(artifact: dict[str, Any]) -> None:
    contract_id = artifact["contract_id"]

    if contract_id.endswith(":query-coverage-manifest:1.0.0"):
        require_unique_ids(artifact["requirements"], "requirement_id", "$.requirements")

    elif contract_id.endswith(":graph-label-adjudication-packet:1.0.0"):
        queues = artifact["queues"]
        require_unique_ids(queues, "queue_id", "$.queues")
        if (
            artifact["review_plan"]["base_graph_sha256"]
            != artifact["pair_binding"]["graph_sha256"]
        ):
            fail(
                "SEMANTIC_ADJUDICATION_PAIR_BINDING",
                "$.review_plan.base_graph_sha256",
                "review plan must bind the exact active-pair graph",
            )
        expected_summary = {
            "label_mapping_record_count": sum(
                item["record_count"]
                for item in queues
                if item["category"] == "label_mapping"
            ),
            "label_mapping_queue_count": sum(
                item["category"] == "label_mapping" for item in queues
            ),
            "lineage_record_count": sum(
                item["record_count"]
                for item in queues
                if item["category"] == "lineage"
            ),
            "lineage_queue_count": sum(
                item["category"] == "lineage" for item in queues
            ),
            "identity_record_count": sum(
                item["record_count"]
                for item in queues
                if item["category"] == "identity_resolution"
            ),
            "identity_queue_count": sum(
                item["category"] == "identity_resolution" for item in queues
            ),
            "total_queue_count": len(queues),
        }
        for field, expected in expected_summary.items():
            if artifact["summary"][field] != expected:
                fail(
                    "SEMANTIC_ADJUDICATION_SUMMARY",
                    f"$.summary.{field}",
                    f"expected {expected}",
                )
        required_fields = artifact["review_protocol"]["required_decision_fields"]
        for index, queue in enumerate(queues):
            path = f"$.queues[{index}]"
            if queue["record_count"] != len(queue["record_ids"]):
                fail(
                    "SEMANTIC_ADJUDICATION_RECORD_COUNT",
                    f"{path}.record_count",
                    "record_count must equal the exact record-id list length",
                )
            if queue["required_decision_fields"] != required_fields:
                fail(
                    "SEMANTIC_ADJUDICATION_DECISION_FIELDS",
                    f"{path}.required_decision_fields",
                    "every queue must require the packet decision fields",
                )
            if queue["dual_review_required"] != (queue["review_tier"] == 2):
                fail(
                    "SEMANTIC_ADJUDICATION_REVIEW_TIER",
                    f"{path}.dual_review_required",
                    "tier 2 requires dual independent review and tier 1 does not",
                )
            source_context_ids = {
                item["source_id"] for item in queue["source_context"]
            }
            if source_context_ids != set(queue["source_ids"]):
                fail(
                    "SEMANTIC_ADJUDICATION_SOURCE_CONTEXT",
                    f"{path}.source_context",
                    "source context must bind exactly the queue source IDs",
                )

    elif contract_id.endswith(":retrieval-evidence-report:1.0.0"):
        outcomes = artifact["requirement_outcomes"]
        require_unique_ids(outcomes, "requirement_id", "$.requirement_outcomes")
        selected = {
            record_id
            for rows in artifact["selected_record_ids_by_lane"].values()
            for record_id in rows
        }
        l2_records = {
            record_id
            for rows in artifact["l2_input_record_ids_by_lane"].values()
            for record_id in rows
        }
        if not l2_records <= selected:
            fail(
                "SEMANTIC_L2_EVIDENCE_BINDING",
                "$.l2_input_record_ids_by_lane",
                "L2 input records must be a subset of selected records",
            )
        for index, outcome in enumerate(outcomes):
            unknown = sorted(set(outcome["record_ids"]) - selected)
            if unknown:
                fail(
                    "SEMANTIC_RETRIEVAL_RECORD_UNBOUND",
                    f"$.requirement_outcomes[{index}].record_ids",
                    f"records are not in the selected evidence packet: {unknown}",
                )
            if outcome["outcome"] == "covered" and not outcome["record_ids"]:
                fail(
                    "SEMANTIC_RETRIEVAL_COVERAGE_MISSING",
                    f"$.requirement_outcomes[{index}].record_ids",
                    "covered requirement must name selected evidence",
                )
        supported = {
            record_id
            for outcome in outcomes
            if outcome["outcome"] == "covered"
            for record_id in outcome["record_ids"]
        }
        coverage_complete = all(
            outcome["outcome"] in {"covered", "excluded"} for outcome in outcomes
        )
        expected_l2_records = supported if coverage_complete else set()
        if l2_records != expected_l2_records:
            fail(
                "SEMANTIC_L2_REQUIREMENT_BINDING",
                "$.l2_input_record_ids_by_lane",
                "L2 input must exactly match requirement-supported records after complete coverage",
            )

    elif contract_id.endswith(":claim-evidence-map:1.0.0"):
        claims = artifact["claims"]
        require_unique_ids(claims, "claim_id", "$.claims")
        for index, claim in enumerate(claims):
            path = f"$.claims[{index}]"
            evidence = claim["evidence"]
            if claim["status"] == "grounded" and not evidence:
                fail(
                    "SEMANTIC_ANSWER_CLAIM_UNGROUNDED",
                    f"{path}.evidence",
                    "grounded claim requires evidence",
                )
            if claim["status"] == "explicit_gap" and claim["claim_kind"] != "gap":
                fail(
                    "SEMANTIC_ANSWER_CLAIM_UNGROUNDED",
                    f"{path}.claim_kind",
                    "explicit gap status requires a gap claim",
                )
            for evidence_index, row in enumerate(evidence):
                evidence_path = f"{path}.evidence[{evidence_index}]"
                if claim["claim_kind"] == "factual" and (
                    row["lane"] != "approved"
                    or row["answer_use"] != "factual_answer"
                ):
                    fail(
                        "SEMANTIC_CANDIDATE_FACTUAL_ANSWER",
                        evidence_path,
                        "factual claims require approved factual-answer evidence",
                    )
                if claim["directional_claim"] and row["traversal_direction"] == "reverse":
                    fail(
                        "SEMANTIC_GRAPH_DIRECTION_UNSUPPORTED",
                        evidence_path,
                        "directional claims cannot rely on reverse traversal",
                    )

    elif contract_id.endswith(":strategic-assertion-profile:1.0.0"):
        validate_strategic_profile_semantics(artifact, "$")

    elif contract_id.endswith(":strategic-evidence-packet:1.0.0"):
        for channel_name, rows in artifact["channels"].items():
            for index, row in enumerate(rows):
                validate_strategic_profile_semantics(
                    row,
                    f"$.channels.{channel_name}[{index}]",
                )

    elif contract_id.endswith(":strategic-wave-receipt:1.0.0"):
        wave_number = artifact["wave_number"]
        prior_receipt_digest = artifact["prior_receipt_digest"]
        if (wave_number == 1) != (prior_receipt_digest is None):
            fail(
                "SEMANTIC_RECEIPT_CHAIN",
                "$.prior_receipt_digest",
                "only wave 1 may omit the prior receipt digest",
            )
        if artifact["status"] == "PASS":
            non_passing_gates = [
                gate["gate_id"]
                for gate in artifact["gates"]
                if gate["status"] != "PASS"
            ]
            if non_passing_gates:
                fail(
                    "SEMANTIC_RECEIPT_GATE_STATUS",
                    "$.gates",
                    f"PASS receipt contains non-passing gates: {non_passing_gates}",
                )
        if (
            artifact["graph_mutation_count"] == 0
            and artifact["graph_sha256_before"] != artifact["graph_sha256_after"]
        ):
            fail(
                "SEMANTIC_RECEIPT_GRAPH_BOUNDARY",
                "$.graph_sha256_after",
                "zero graph mutations require an unchanged graph digest",
            )
        if wave_number == 8 and artifact["activation_state"] != "READY_NOT_ACTIVATED":
            fail(
                "SEMANTIC_RECEIPT_ACTIVATION_BOUNDARY",
                "$.activation_state",
                "wave 8 must stop at READY_NOT_ACTIVATED",
            )

    elif contract_id.endswith((
        ":copilot-intake:1.0.0",
        ":google-meet-gemini-intake:1.0.0",
        ":evidence-envelope-intake:1.0.0",
    )):
        require_unique_ids(artifact["speakers"], "speaker_ref", "$.speakers")
        statements = [statement for speaker in artifact["speakers"] for statement in speaker["statements"]]
        require_unique_ids(statements, "statement_id", "$.speakers[*].statements")
        require_unique_ids(artifact["actions"], "action_id", "$.actions")

    elif contract_id.endswith(":architecture-diagram-intake:1.0.0"):
        require_unique_ids(artifact["components"], "component_ref", "$.components")
        require_unique_ids(artifact["relationships"], "relationship_ref", "$.relationships")
        component_ids = {item["component_ref"] for item in artifact["components"]}
        relationship_ids = {item["relationship_ref"] for item in artifact["relationships"]}
        for index, item in enumerate(artifact["relationships"]):
            for key in ("from_component_ref", "to_component_ref"):
                if item[key] not in component_ids:
                    fail("SEMANTIC_DANGLING_REFERENCE", f"$.relationships[{index}].{key}", "component does not exist")
        valid_refs = component_ids | relationship_ids
        for index, item in enumerate(artifact["open_ambiguities"]):
            unknown = sorted(set(item["affected_refs"]) - valid_refs)
            if unknown:
                fail("SEMANTIC_DANGLING_REFERENCE", f"$.open_ambiguities[{index}].affected_refs", f"unknown refs: {unknown}")

    elif contract_id.endswith(":normalized-candidate-bundle:1.0.0"):
        require_unique_ids(artifact["evidence"], "evidence_id", "$.evidence")
        require_unique_ids(artifact["candidates"], "candidate_id", "$.candidates")
        evidence_ids = {item["evidence_id"] for item in artifact["evidence"]}
        mention_ids: list[str] = []
        for index, candidate in enumerate(artifact["candidates"]):
            unknown = sorted(set(candidate["evidence_ids"]) - evidence_ids)
            if unknown:
                fail("SEMANTIC_DANGLING_REFERENCE", f"$.candidates[{index}].evidence_ids", f"unknown evidence: {unknown}")
            mention_ids.extend(item["mention_id"] for item in candidate["entity_mentions"])
        if len(mention_ids) != len(set(mention_ids)):
            fail("SEMANTIC_DUPLICATE_ID", "$.candidates[*].entity_mentions", "duplicate mention_id")

    elif contract_id.endswith(":ingestion-plan:1.0.0"):
        require_unique_ids(artifact["candidates"], "candidate_id", "$.candidates")

    elif contract_id.endswith(":route-decision:1.0.0"):
        if artifact["use_case"] == "intake":
            if artifact["intake_route"] is None or artifact["query_route"] is not None:
                fail("SEMANTIC_ROUTE_CARDINALITY", "$", "intake requires exactly one intake route")
        elif artifact["query_route"] is None or artifact["intake_route"] is not None:
            fail("SEMANTIC_ROUTE_CARDINALITY", "$", "query requires exactly one query route")

    elif contract_id.endswith(":entity-resolution:1.0.0"):
        require_unique_ids(artifact["alternatives"], "entity_id", "$.alternatives")
        outcome = artifact["outcome"]
        selected = artifact["selected_entity_id"]
        hitl_outcomes = {
            "HITL_CONFIRM_ENTITY",
            "CREATE_NEW_ENTITY_PROPOSAL",
            "CREATE_EXTERNAL_CONCEPT",
            "ADD_ALIAS_PROPOSAL",
        }
        linked_outcomes = {"AUTO_LINK_EXACT", "AUTO_LINK_HIGH_CONFIDENCE"}
        if outcome in linked_outcomes and selected is None:
            fail("SEMANTIC_ENTITY_SELECTION", "$.selected_entity_id", "linked outcome requires a selected entity")
        if outcome in {
            "CREATE_NEW_ENTITY_PROPOSAL",
            "CREATE_EXTERNAL_CONCEPT",
            "REJECT_MATCH",
            "QUARANTINE_IDENTITY",
            "SOURCE_ONLY",
        } and selected is not None:
            fail("SEMANTIC_ENTITY_SELECTION", "$.selected_entity_id", "outcome forbids a selected entity")
        if artifact["requires_hitl"] != (outcome in hitl_outcomes):
            fail("SEMANTIC_HITL_POSTURE", "$.requires_hitl", "HITL flag does not match outcome")
        if outcome == "AUTO_LINK_HIGH_CONFIDENCE" and not artifact["stable_identifier"]["value"]:
            alternatives = artifact["alternatives"]
            if not alternatives or alternatives[0]["match_score"] < 0.9 or not alternatives[0]["supporting_context"]:
                fail("SEMANTIC_NAKED_FUZZY_MATCH", "$.alternatives[0]", "high-confidence link requires context and score >= 0.9")

    elif contract_id.endswith(":assertion-disposition:1.0.0"):
        disposition = artifact["disposition"]
        expected = {
            "APPROVED": ("X3D_ALLOW_APPROVED_WRITE", "approved"),
            "CANDIDATE": ("X3D_ALLOW_CANDIDATE_STAGE", "candidate"),
            "QUARANTINE": ("X3Q_QUARANTINE", "none"),
            "REJECTED_NOOP": ("X3R_REJECT_NOOP", "none"),
        }[disposition]
        if (artifact["x3_outcome"], artifact["persistent_lane"]) != expected:
            fail("SEMANTIC_LANE_CONTAMINATION", "$", f"{disposition} requires {expected}")
        if disposition == "APPROVED":
            gates = (
                artifact["identity_resolved"],
                bool(artifact["evidence_ids"]),
                artifact["source_authority_sufficient"],
                artifact["currentness_valid"],
                artifact["classification_valid"],
                artifact["audience_valid"],
                artifact["material_contradiction_resolved"],
                not artifact["hitl_required"] or artifact["hitl_complete"],
            )
            if not all(gates):
                fail("SEMANTIC_UNSAFE_APPROVAL", "$", "approved disposition has an incomplete approval gate")

    elif contract_id.endswith(":graph-change-set:1.0.0"):
        require_unique_ids(artifact["operations"], "operation_id", "$.operations")
        sequence = [item["sequence"] for item in artifact["operations"]]
        if sequence != list(range(1, len(sequence) + 1)):
            fail("SEMANTIC_OPERATION_SEQUENCE", "$.operations", "operation sequence must be contiguous and ordered")
        if artifact["status"] in {"approved", "staged", "applied"} and not artifact["approval_id"]:
            fail("SEMANTIC_APPROVAL_REQUIRED", "$.approval_id", "status requires an approval binding")

    elif contract_id.endswith(":retrieval-record:1.0.0"):
        profile = artifact.get("strategic_profile")
        if profile is not None:
            validate_strategic_profile_semantics(profile, "$.strategic_profile")
        actual_text_hash = hashlib.sha256(artifact["retrieval_text"].encode("utf-8")).hexdigest()
        if artifact["retrieval_text_hash"] != actual_text_hash:
            fail("SEMANTIC_RETRIEVAL_TEXT_HASH", "$.retrieval_text_hash", f"expected {actual_text_hash}")
        if artifact["lane"] == "candidate" and artifact["lifecycle"] != "candidate":
            fail("SEMANTIC_LANE_CONTAMINATION", "$.lifecycle", "candidate lane requires candidate lifecycle")
        if artifact["lane"] == "approved" and artifact["lifecycle"] == "candidate":
            fail("SEMANTIC_LANE_CONTAMINATION", "$.lifecycle", "approved lane cannot contain candidate lifecycle")
        if artifact["record_type"] == "action_item" and artifact["action_status"] is None:
            fail("SEMANTIC_ACTION_STATUS", "$.action_status", "action items require explicit status")
        if artifact["record_type"] != "action_item" and artifact["action_status"] is not None:
            fail("SEMANTIC_ACTION_STATUS", "$.action_status", "non-action records cannot carry action status")
        retrieval_metadata_fields = (
            "retrieval_kind",
            "epistemic_status",
            "provenance_kind",
            "answer_use",
        )
        metadata_presence = [field in artifact for field in retrieval_metadata_fields]
        # Existing immutable retrieval artifacts predate these fields.  They
        # remain valid, but a newly emitted metadata projection must be whole.
        if any(metadata_presence) and not all(metadata_presence):
            fail(
                "SEMANTIC_RETRIEVAL_METADATA_INCOMPLETE",
                "$.retrieval_kind",
                "retrieval metadata must be present as one complete projection",
            )
        if artifact["record_type"] == "entity_facet" and not all(metadata_presence):
            fail(
                "SEMANTIC_ENTITY_FACET_METADATA_REQUIRED",
                "$.retrieval_kind",
                "entity facets require a complete navigation-only metadata projection",
            )
        if all(metadata_presence):
            if artifact["lane"] == "candidate" and artifact["epistemic_status"] != "candidate":
                fail(
                    "SEMANTIC_RETRIEVAL_METADATA_LANE",
                    "$.epistemic_status",
                    "candidate lane requires candidate epistemic status",
                )
            if artifact["lane"] == "approved" and artifact["epistemic_status"] != "approved":
                fail(
                    "SEMANTIC_RETRIEVAL_METADATA_LANE",
                    "$.epistemic_status",
                    "approved lane requires approved epistemic status",
                )
            if (
                artifact["answer_use"] == "factual_answer"
                and artifact["epistemic_status"] != "approved"
            ):
                fail(
                    "SEMANTIC_RETRIEVAL_ANSWER_USE",
                    "$.answer_use",
                    "factual answer use requires approved epistemic status",
                )
            if (
                artifact["answer_use"] == "candidate_hypothesis"
                and artifact["epistemic_status"] != "candidate"
            ):
                fail(
                    "SEMANTIC_RETRIEVAL_ANSWER_USE",
                    "$.answer_use",
                    "candidate hypothesis use requires candidate epistemic status",
                )
            if (
                artifact["answer_use"] == "citation_only"
                and artifact["retrieval_kind"] != "evidence_span"
            ):
                fail(
                    "SEMANTIC_RETRIEVAL_ANSWER_USE",
                    "$.answer_use",
                    "citation-only use requires an evidence span",
                )
            if (
                artifact["answer_use"] == "routing_only"
                and artifact["retrieval_kind"] != "routing_intent"
            ):
                fail(
                    "SEMANTIC_RETRIEVAL_ANSWER_USE",
                    "$.answer_use",
                    "routing-only use requires a routing intent",
                )
            if artifact["record_type"] == "entity_facet":
                if (
                    artifact["retrieval_kind"] != "entity_facet"
                    or artifact["answer_use"] != "navigation_only"
                    or artifact["provenance_kind"] != "source_bound"
                    or len(artifact["canonical_entities"]) != 1
                ):
                    fail(
                        "SEMANTIC_ENTITY_FACET_POLICY",
                        "$.record_type",
                        "entity facets are source-bound, navigation-only, and singularly anchored",
                    )
                required_extension_lists = (
                    "com.partner_advisory.nee.graph-entity-ids",
                    "com.partner_advisory.nee.graph-assertion-ids",
                    "com.partner_advisory.nee.graph-evidence-ids",
                    "com.partner_advisory.nee.entity-facet-source-record-ids",
                    "com.partner_advisory.nee.entity-facet-profile-record-ids",
                )
                for extension_key in required_extension_lists:
                    value = artifact["extensions"].get(extension_key)
                    if (
                        not isinstance(value, list)
                        or not value
                        or not all(isinstance(item, str) and item for item in value)
                    ):
                        fail(
                            "SEMANTIC_ENTITY_FACET_BINDING",
                            f"$.extensions.{extension_key}",
                            "entity facets require nonempty graph-bound source lists",
                        )

    elif contract_id.endswith(":otel-event-envelope:1.0.0"):
        for key in artifact["attributes"]:
            if SENSITIVE_OTEL_KEY.search(key):
                fail("PRIVACY_SENSITIVE_OTEL_ATTRIBUTE", f"$.attributes.{key}", "sensitive/raw content key is forbidden")

    elif contract_id.endswith(":learning-event:1.0.0"):
        correlation = artifact["correlation"]
        if not any(value is not None for value in correlation.values()):
            fail("SEMANTIC_MISSING_CORRELATION", "$.correlation", "at least one domain correlation ID is required")
        event_type = artifact["event_type"]
        l2_event_types = {
            "l2_execution_decision",
            "l2_primary_call",
            "l2_schema_repair",
            "l2_quality_escalation",
            "l2_provider_failure",
            "l2_output_selection",
        }
        required_correlation = {
            "candidate_record": "candidate_id",
            "entity_resolution_candidate": "candidate_id",
            "entity_resolution_decision": "candidate_id",
            "assertion_disposition": "candidate_id",
            "query_plan": "query_id",
            "graph_change_operation": "graph_operation_id",
            "hitl_decision": "hitl_item_id",
        }.get(event_type)
        if event_type in l2_event_types:
            required_correlation = (
                "source_id" if artifact["use_case"] == "intake" else "query_id"
            )
        if required_correlation and correlation[required_correlation] is None:
            fail("SEMANTIC_MISSING_CORRELATION", f"$.correlation.{required_correlation}", f"{event_type} requires this correlation")

    elif contract_id.endswith(":query-route-eval-suite:1.0.0"):
        validate_query_route_eval_suite_semantics(artifact)

    elif contract_id.endswith(":query-route-eval-result:1.0.0"):
        validate_query_route_eval_result_semantics(artifact)

    elif contract_id.endswith(":eval-suite:3.0.0"):
        validate_eval_suite_v3_semantics(artifact)

    elif contract_id.endswith(":eval-execution-measurement:1.0.0"):
        validate_eval_measurement_semantics(artifact)

    elif contract_id.endswith(":agentic-workflow-eval-suite:1.0.0"):
        require_unique_ids(artifact["treatments"], "treatment_id", "$.treatments")
        require_unique_ids(artifact["cases"], "case_id", "$.cases")

    elif contract_id.endswith(":eval-run-result:2.0.0"):
        require_unique_ids(artifact["case_results"], "case_id", "$.case_results")
        aggregate = artifact["aggregate"]
        case_results = artifact["case_results"]
        expected = {
            "case_count": len(case_results),
            "passed_case_count": sum(
                item["status"] == "PASS" for item in case_results
            ),
            "failed_case_count": sum(
                item["status"] == "FAIL" for item in case_results
            ),
            "invalid_case_count": sum(
                item["status"] == "MEASUREMENT_INVALID" for item in case_results
            ),
        }
        for field, value in expected.items():
            if aggregate[field] != value:
                fail(
                    "SEMANTIC_EVAL_RESULT_AGGREGATE",
                    f"$.aggregate.{field}",
                    f"expected aggregate value {value}",
                )
        expected_status = (
            "MEASUREMENT_INVALID"
            if aggregate["invalid_case_count"]
            else "PASS"
            if aggregate["failed_case_count"] == 0
            else "FAIL"
        )
        if artifact["status"] != expected_status:
            fail(
                "SEMANTIC_EVAL_RESULT_STATUS",
                "$.status",
                f"expected {expected_status}",
            )

    elif contract_id.endswith(":eval-comparison:2.0.0"):
        invariant_mismatch = (
            "INVARIANT_BINDING_MISMATCH" in artifact["cause_codes"]
        )
        if (artifact["status"] == "NOT_COMPARABLE") != invariant_mismatch:
            fail(
                "SEMANTIC_COMPARISON_STATUS",
                "$.status",
                "NOT_COMPARABLE requires an invariant binding mismatch",
            )
        for index, delta in enumerate(artifact["metric_deltas"]):
            if delta["delta"] != delta["candidate"] - delta["baseline"]:
                fail(
                    "SEMANTIC_METRIC_DELTA",
                    f"$.metric_deltas[{index}].delta",
                    "metric delta must equal candidate minus baseline",
                )

    elif contract_id.endswith(":query-plan:3.0.0"):
        controller = artifact["controller"]
        if controller["mode"] == "off" and controller["action_allowlist"] != [
            "requirement_hybrid_retrieval",
            "stop",
        ]:
            fail(
                "SEMANTIC_L1_ACTION_ALLOWLIST",
                "$.controller.action_allowlist",
                "mode off requires the closed v1 action allowlist",
            )
        if artifact["destination"] == "GRAPH_EVIDENCE_UTILITY" and artifact["provider_egress_allowed"]:
            fail(
                "SEMANTIC_L1_PROVIDER_EGRESS",
                "$.provider_egress_allowed",
                "graph evidence utility plans cannot authorize provider egress",
            )

    elif contract_id.endswith(":query-retrieval-attempt:1.0.0"):
        if artifact["graph_sha256_before"] != artifact["graph_sha256_after"]:
            fail(
                "SEMANTIC_L1_GRAPH_MUTATION",
                "$.graph_sha256_after",
                "an L1 retrieval attempt must remain graph read-only",
            )
        if artifact["provider_initialization_count"] != 0 or artifact["provider_call_count"] != 0:
            fail(
                "SEMANTIC_L1_PROVIDER_CALL",
                "$.provider_call_count",
                "provider initialization and calls must remain zero before L2D",
            )
        if artifact["budget_after"]["actions_used"] < artifact["budget_before"]["actions_used"]:
            fail(
                "SEMANTIC_L1_BUDGET_ACCOUNTING",
                "$.budget_after.actions_used",
                "budget counters cannot decrease",
            )
        result = artifact["result"]
        if result["provider_initialization_count"] != 0 or result["provider_call_count"] != 0:
            fail(
                "SEMANTIC_L1_PROVIDER_CALL",
                "$.result.provider_call_count",
                "attempt results cannot contain provider work",
            )

    elif contract_id.endswith(":query-plan-observation:1.0.0"):
        if artifact["graph_sha256_before"] != artifact["graph_sha256_after"]:
            fail(
                "SEMANTIC_L1_GRAPH_MUTATION",
                "$.graph_sha256_after",
                "observations must preserve the graph hash",
            )
        covered = artifact["newly_covered_requirement_ids"]
        if artifact["progress"] in {"progress", "complete"} and not covered:
            fail(
                "SEMANTIC_L1_FALSE_PROGRESS",
                "$.newly_covered_requirement_ids",
                "progress requires newly covered material requirements",
            )
        if artifact["progress"] == "no_progress" and covered:
            fail(
                "SEMANTIC_L1_FALSE_PROGRESS",
                "$.progress",
                "no_progress cannot report newly covered requirements",
            )

    elif contract_id.endswith(":query-plan-terminal:1.0.0"):
        if artifact["graph_sha256_before"] != artifact["graph_sha256_after"]:
            fail(
                "SEMANTIC_L1_GRAPH_MUTATION",
                "$.graph_sha256_after",
                "terminal receipts must preserve the graph hash",
            )
        if artifact["terminal_reason"] == "COMPLETED" and not artifact["coverage_complete"]:
            fail(
                "SEMANTIC_L1_INCOMPLETE_TERMINAL",
                "$.coverage_complete",
                "COMPLETED requires complete coverage",
            )
        if artifact["terminal_reason"] != "COMPLETED" and artifact["coverage_complete"]:
            fail(
                "SEMANTIC_L1_TERMINAL_COVERAGE",
                "$.coverage_complete",
                "blocked terminal reasons cannot claim complete coverage",
            )

    elif contract_id.endswith(":query-l1-control-policy:1.0.0"):
        if artifact["mode"] != "off":
            fail(
                "SEMANTIC_L1_POLICY_MODE",
                "$.mode",
                "Wave 1 checked-in policy must remain off",
            )
        if artifact["action_allowlist"] != ["requirement_hybrid_retrieval", "stop"]:
            fail(
                "SEMANTIC_L1_ACTION_ALLOWLIST",
                "$.action_allowlist",
                "policy must expose only the two v1 actions",
            )
        if artifact["provider_calls_before_l2d"] != 0 or artifact["graph_mutations_before_l2d"] != 0:
            fail(
                "SEMANTIC_L1_BOUNDARY",
                "$.provider_calls_before_l2d",
                "the protected pre-L2 boundary must remain zero-work",
            )

    elif contract_id.endswith(":ingestion-l1-control-policy:1.0.0"):
        if artifact["mode"] != "off" or artifact["runtime_default"] != "off" or not artifact["proposal_only"]:
            fail("SEMANTIC_INGESTION_L1_POLICY", "$.mode", "Wave 10 ingestion planning must remain default-off and proposal-only")
        if artifact["provider_calls_before_l2d"] != 0 or artifact["graph_mutations_before_l2d"] != 0:
            fail("SEMANTIC_INGESTION_L1_BOUNDARY", "$.provider_calls_before_l2d", "the protected ingestion pre-L2D boundary must remain zero-work")

    elif contract_id.endswith(":ingestion-plan-v2:1.0.0"):
        prior_ordinal = 0
        seen_step_ids: set[str] = set()
        seen_idempotency_keys: set[str] = set()
        for index, step in enumerate(artifact["steps"]):
            if step["ordinal"] <= prior_ordinal or step["step_id"] in seen_step_ids or step["idempotency_key"] in seen_idempotency_keys:
                fail("SEMANTIC_INGESTION_PLAN_ORDER", f"$.steps[{index}]", "step IDs and idempotency keys must be unique and ordinals strictly increasing")
            if any(dependency not in seen_step_ids for dependency in step["dependencies"]):
                fail("SEMANTIC_INGESTION_PLAN_DEPENDENCY", f"$.steps[{index}].dependencies", "dependencies must be earlier plan steps")
            if step["provider_call_count"] != 0 or step["graph_mutation_count"] != 0:
                fail("SEMANTIC_INGESTION_PLAN_BOUNDARY", f"$.steps[{index}]", "planning steps must remain provider-free and graph read-only")
            prior_ordinal = step["ordinal"]
            seen_step_ids.add(step["step_id"])
            seen_idempotency_keys.add(step["idempotency_key"])

    elif contract_id.endswith(":ingestion-plan-step-receipt:1.0.0"):
        if not artifact["proposal_only"] or artifact["provider_call_count"] != 0 or artifact["graph_mutation_count"] != 0:
            fail("SEMANTIC_INGESTION_STEP_RECEIPT_BOUNDARY", "$", "step receipts cannot evidence execution, provider calls, or graph mutation")
        if artifact["status"] == "PLANNED" and artifact["output_digest"] is not None:
            fail("SEMANTIC_INGESTION_STEP_RECEIPT_OUTPUT", "$.output_digest", "planned receipts cannot claim an output")

    elif contract_id.endswith(":ingestion-plan-checkpoint:1.0.0"):
        if not artifact["completed_step_receipt_digests"]:
            fail("SEMANTIC_INGESTION_CHECKPOINT_RECEIPTS", "$.completed_step_receipt_digests", "terminal checkpoints require step receipt bindings")

    elif contract_id.endswith(":ingestion-l1-ledger-policy:1.0.0"):
        if artifact["runtime_default"] != "off" or set(artifact["allowed_modes"]) != {"off", "shadow", "enforced"}:
            fail("SEMANTIC_INGESTION_LEDGER_POLICY", "$.runtime_default", "ledger policy must keep default-off and the closed mode set")
        if artifact["provider_calls_before_l2d"] != 0 or artifact["graph_mutations_before_l2d"] != 0:
            fail("SEMANTIC_INGESTION_LEDGER_BOUNDARY", "$.provider_calls_before_l2d", "ledger policy cannot authorize provider or graph work")

    elif contract_id.endswith(":ingestion-shadow-step-receipt:1.0.0"):
        if artifact["provider_call_count"] != 0 or artifact["graph_mutation_count"] != 0 or not artifact["deterministic_equivalent"]:
            fail("SEMANTIC_INGESTION_SHADOW_RECEIPT_BOUNDARY", "$", "shadow receipts must remain equivalent, provider-free, and graph read-only")
        if artifact["status"] == "OBSERVED" and artifact["output_digest"] is None:
            fail("SEMANTIC_INGESTION_SHADOW_RECEIPT_OUTPUT", "$.output_digest", "an observed v1 output requires its digest")

    elif contract_id.endswith(":ingestion-shadow-checkpoint:1.0.0"):
        if len(artifact["completed_step_receipt_digests"]) != len(artifact["completed_step_ids"]):
            fail("SEMANTIC_INGESTION_SHADOW_CHECKPOINT_CHAIN", "$.completed_step_ids", "receipt and step chains must have equal lengths")
        is_open = artifact["terminal_state"] == "OPEN"
        if is_open != (artifact["next_step_id"] is not None):
            fail("SEMANTIC_INGESTION_SHADOW_CHECKPOINT_TERMINAL", "$.next_step_id", "only open checkpoints may name a next step")
        if artifact["provider_call_count"] != 0 or artifact["graph_mutation_count"] != 0:
            fail("SEMANTIC_INGESTION_SHADOW_CHECKPOINT_BOUNDARY", "$", "shadow checkpoints cannot authorize execution")

    elif contract_id.endswith(":shadow-learning-policy:1.0.0"):
        if artifact["runtime_default"] != "off" or artifact["automatic_policy_change_count"] != 0 or artifact["raw_content_registry_allowed"]:
            fail("SEMANTIC_SHADOW_LEARNING_POLICY", "$", "shadow learning must remain default-off, non-mutating, and privacy-safe")

    elif contract_id.endswith(":l1-shadow-observation:1.0.0"):
        if artifact["raw_content_present"] or artifact["status"] != "ADMITTED":
            fail("SEMANTIC_SHADOW_LEARNING_OBSERVATION", "$", "only privacy-safe admitted observations may enter the registry")

    elif contract_id.endswith(":shadow-finding:1.0.0"):
        if artifact["observation_count"] != len(artifact["observation_digests"]) or artifact["automatic_policy_change_count"] != 0:
            fail("SEMANTIC_SHADOW_LEARNING_FINDING", "$", "findings require one digest per observation and cannot alter policy")

    elif contract_id.endswith(":query-l1-controller-eval-result:1.0.0"):
        results = artifact["case_results"]
        aggregate = artifact["aggregate"]
        expected = {
            "case_count": len(results),
            "passed_case_count": sum(row["status"] == "PASS" for row in results),
            "failed_case_count": sum(row["status"] == "FAIL" for row in results),
            "invalid_case_count": sum(row["status"] == "MEASUREMENT_INVALID" for row in results),
        }
        for key, value in expected.items():
            if aggregate[key] != value:
                fail("SEMANTIC_L1_EVAL_AGGREGATE", f"$.aggregate.{key}", f"expected {value}")
        if artifact["provider_call_count"] != 0 or artifact["graph_mutation_count"] != 0:
            fail("SEMANTIC_L1_EVAL_BOUNDARY", "$.provider_call_count", "evaluation must remain provider-free and read-only")

    elif contract_id.endswith(":query-l1-controller-eval-comparison:1.0.0"):
        if artifact["status"] == "NOT_COMPARABLE" and "INVARIANT_BINDING_MISMATCH" not in artifact["cause_codes"]:
            fail("SEMANTIC_L1_COMPARISON_INVARIANT", "$.cause_codes", "NOT_COMPARABLE requires an invariant mismatch cause")
        for index, delta in enumerate(artifact["metric_deltas"]):
            if delta["delta"] != delta["candidate"] - delta["baseline"]:
                fail("SEMANTIC_L1_METRIC_DELTA", f"$.metric_deltas[{index}].delta", "delta must equal candidate minus baseline")

    elif contract_id.endswith(":retrieval-release-evaluation:2.0.0"):
        if artifact["final_coverage"]["covered"] < artifact["initial_coverage"]["covered"]:
            fail("SEMANTIC_L1_RELEASE_COVERAGE", "$.final_coverage.covered", "final coverage cannot regress")
        if artifact["status"] == "PASS" and artifact["final_coverage"]["unresolved"] != 0:
            fail("SEMANTIC_L1_RELEASE_STATUS", "$.status", "PASS requires zero unresolved requirements")

    elif contract_id.endswith(":l1-wave-receipt:1.0.0"):
        if any(command["status"] != "PASS" or command["skipped"] != 0 or command["timed_out"] for command in artifact["commands"]):
            fail("SEMANTIC_L1_WAVE_COMMAND", "$.commands", "a wave cannot pass with failed, skipped, or timed-out commands")
        if artifact["next_wave_authorized"] and (
            artifact["provider_call_count"] != 0
            or artifact["graph_mutation_count"] != 0
        ):
            fail("SEMANTIC_L1_WAVE_BOUNDARY", "$.next_wave_authorized", "promotion requires zero provider calls and graph mutations")

    elif contract_id.endswith(":learning-change-proposal:1.0.0"):
        if artifact["auto_apply"] or not artifact["approval_required"]:
            fail("SEMANTIC_AUTOMATIC_SELF_MODIFICATION", "$", "learning changes must remain approval-required proposals")
        binding = artifact.get("extensions", {}).get("com.nee.l1-shadow-proposal-binding")
        if binding is not None:
            required = {"finding_digest", "target_field", "expected_improvement", "guardrails", "isolated_replay_suite_digest", "rollback", "expires_at", "requires_new_implementation_branch"}
            if not isinstance(binding, dict) or set(binding) != required or binding["target_field"] not in {"vocabulary", "refinability_rule", "action_ordering", "budget"} or binding["requires_new_implementation_branch"] is not True:
                fail("SEMANTIC_SHADOW_LEARNING_PROPOSAL_BINDING", "$.extensions.com.nee.l1-shadow-proposal-binding", "shadow proposals require closed replay, expiry, rollback, and branch bindings")

    if "extensions" in artifact and isinstance(artifact["extensions"], dict):
        if "com.partner_advisory.nee.canonical-signal-projection" in artifact["extensions"]:
            validate_canonical_signal_projection_extension(
                artifact["extensions"]["com.partner_advisory.nee.canonical-signal-projection"],
                "$.extensions['com.partner_advisory.nee.canonical-signal-projection']",
            )

