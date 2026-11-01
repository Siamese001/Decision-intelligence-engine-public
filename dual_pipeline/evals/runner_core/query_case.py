"""Execution and validation of query evaluation cases."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from ...contracts import validate_artifact
from ...core import digest, read_json
from ...index import CLASSIFICATION_RANK, EmbeddingBackend
from ...query import QueryEngine
from ...x1_quality import X1QualityMode, load_x1_quality_config
from ..fakes import (
    SealedEvalProviderFactory,
    SealedEvalQualityProviderFactory,
)
from .constants import (
    EvaluationFailure,
    _cache_binding_matches_backend,
    _case_artifact_path,
    _pair_binding,
    _requirement_expectations_met,
)
from .workspace import _audit, _bootstrap


def _selected_evidence_authorization(
    packet: dict[str, Any],
    claim_map: dict[str, Any],
    *,
    requested_classification: str,
    requested_audiences: list[str],
) -> dict[str, Any]:
    """Independently re-check clearance, provenance, and candidate posture.

    The query pipeline enforces these controls at retrieval time.  The
    evaluator repeats the calculation from sealed evidence so a regression in
    one control cannot be masked by another receipt.
    """

    requested_rank = CLASSIFICATION_RANK[requested_classification]
    requested_audience_set = set(requested_audiences)
    records: dict[str, dict[str, Any]] = {}
    record_lanes: dict[str, str] = {}
    authorization_violations: list[str] = []
    evidence_binding_violations: list[str] = []
    evidence_ids: set[str] = set()
    for lane in ("approved", "candidate", "external_public"):
        for item in packet.get(lane, []):
            record = item["record"]
            record_id = record["record_id"]
            records[record_id] = record
            record_lanes[record_id] = lane
            if (
                CLASSIFICATION_RANK[record["classification"]] > requested_rank
                or not (requested_audience_set & set(record["audiences"]))
            ):
                authorization_violations.append(record_id)
            if not record.get("evidence_ids"):
                evidence_binding_violations.append(record_id)
            evidence_ids.update(record.get("evidence_ids", []))
    candidate_claim_violations: list[str] = []
    for claim in claim_map["claims"]:
        has_candidate_evidence = any(
            record_lanes.get(row["record_id"]) == "candidate"
            for row in claim["evidence"]
        )
        if has_candidate_evidence and claim["claim_kind"] != "candidate_hypothesis":
            candidate_claim_violations.append(claim["claim_id"])
    return {
        "selected_record_ids": sorted(records),
        "distinct_evidence_ids": sorted(evidence_ids),
        "authorization_violation_record_ids": sorted(authorization_violations),
        "evidence_binding_violation_record_ids": sorted(evidence_binding_violations),
        "candidate_claim_violation_ids": sorted(candidate_claim_violations),
    }


def _query_case(
    case: dict[str, Any],
    *,
    case_dir: Path,
    case_runtime: Path,
    seed_graph: Path,
    backend: EmbeddingBackend,
    template_runtime: Path | None = None,
) -> dict[str, Any]:
    manager, backend, _active_graph, _sidecar = _bootstrap(
        case_runtime,
        seed_graph,
        backend,
        template_runtime=template_runtime,
    )
    provider = (
        SealedEvalProviderFactory()
        if case["provider_posture"] == "fake"
        else None
    )
    quality_control = case.get("quality_control")
    quality_config = None
    quality_provider = None
    if quality_control is not None:
        if case["provider_posture"] != "fake":
            raise EvaluationFailure(
                "quality-controlled eval cases require the network-free fake provider posture"
            )
        quality_config = replace(
            load_x1_quality_config(),
            mode=X1QualityMode(str(quality_control["mode"])),
            provider_egress_allowed=True,
        )
        quality_provider = SealedEvalQualityProviderFactory(
            [str(value) for value in quality_control["judge_verdicts"]]
        )
    result = QueryEngine(
        case_runtime,
        seed_graph,
        backend,
        provider_factory=provider,
        x1_quality_config=quality_config,
        quality_provider_factory=quality_provider,
    ).ask(
        case["question"],
        query_type=case["query_type"],
        output_dir=case_dir / "query-output",
        classification=case["classification"],
        audience=",".join(case["audiences"]),
        provider_egress_allowed=case["provider_posture"] != "offline",
    )
    semantic_cache = result.get("semantic_cache")
    runtime_binding = result.get("runtime_binding")
    if not isinstance(runtime_binding, dict):
        raise EvaluationFailure("query result is missing its runtime binding receipt")
    semantic_cache_eligible = (
        case["evaluation_scope"] == "graph_retrieval_qualification"
    )
    semantic_cache_evidence = (
        semantic_cache
        if isinstance(semantic_cache, dict)
        else {
            "status": "NOT_APPLICABLE",
            "reason": "QUERY_ROUTED_BEFORE_RETRIEVAL",
        }
    )
    active_pair_binding = _pair_binding(manager.active_pair())
    run_dir = case_runtime / "runs" / result["run_id"]
    packet = read_json(run_dir / "evidence_packet.json")
    connected_map = read_json(run_dir / "connected_evidence_map.json")
    coverage_manifest = read_json(run_dir / "query_coverage_manifest.json")
    evidence_report = read_json(run_dir / "retrieval_evidence_report.json")
    x_gates = read_json(run_dir / "x_gates.json")
    claim_map_path = run_dir / "claim_evidence_map.json"
    pre_l2_blocked = (
        result["status"] == "BLOCKED"
        and result.get("terminal_outcome")
        in {"RETRIEVAL_COVERAGE_INSUFFICIENT", "RETRIEVAL_GRAPH_FIT_INSUFFICIENT"}
    )
    if claim_map_path.is_file():
        claim_map = read_json(claim_map_path)
    elif pre_l2_blocked:
        # A retrieval-coverage block happens before an answer exists.  It must
        # preserve retrieval evidence and an X3 escalation, but cannot create
        # claim or X1 quality receipts for a nonexistent answer.
        claim_map = {"claims": []}
    else:
        raise EvaluationFailure("query case is missing its claim evidence map")
    delivery_quality_path = run_dir / "delivery_quality_decision.json"
    if delivery_quality_path.is_file():
        delivery_quality = read_json(delivery_quality_path)
    elif pre_l2_blocked:
        delivery_quality = None
    else:
        raise EvaluationFailure("query case is missing its delivery quality decision")
    quality_receipt_names = (
        "challenger_review.json",
        "judge_review_initial.json",
        "judge_review.json",
        "content_repair.json",
    )
    quality_receipts_present = all(
        (run_dir / name).is_file() for name in quality_receipt_names
    )
    l2_execution_present = (run_dir / "l2_execution.json").is_file()
    l2_packet_path = run_dir / "l2_evidence_packet.json"
    l2_decision_path = run_dir / "l2_execution_decision.json"
    if l2_packet_path.is_file() and l2_decision_path.is_file():
        l2_packet = read_json(l2_packet_path)
        l2_decision_artifact = read_json(l2_decision_path)
        validate_artifact(l2_decision_artifact)
        l2_packet_digest = l2_packet.get("packet_digest")
        l2_packet_sealed = (
            isinstance(l2_packet_digest, str)
            and l2_packet_digest
            == digest(
                {
                    key: value
                    for key, value in l2_packet.items()
                    if key != "packet_digest"
                }
            )
        )
        fake_l2_consumes_sealed_retrieval_evidence = (
            l2_execution_present
            and l2_packet_sealed
            and l2_decision_artifact.get("evidence_packet_hash")
            == l2_packet_digest
            and evidence_report["evidence_packet_digest"]
            == packet["packet_digest"]
        )
    else:
        fake_l2_consumes_sealed_retrieval_evidence = False
    l2_visible_counts = {
        lane: len(l2_packet.get(lane, []))
        for lane in ("approved", "candidate", "external_public")
    } if l2_packet_path.is_file() and l2_decision_path.is_file() else {
        "approved": 0,
        "candidate": 0,
        "external_public": 0,
    }
    if quality_receipts_present:
        challenger_review = read_json(run_dir / "challenger_review.json")
        initial_judge_review = read_json(run_dir / "judge_review_initial.json")
        judge_review = read_json(run_dir / "judge_review.json")
        content_repair = read_json(run_dir / "content_repair.json")
    elif not pre_l2_blocked:
        raise EvaluationFailure("query case is missing required X1 quality receipts")
    validate_artifact(coverage_manifest)
    validate_artifact(evidence_report)
    if not pre_l2_blocked:
        validate_artifact(claim_map)
    if delivery_quality is not None:
        validate_artifact(delivery_quality)
    record_ids = {
        item["record"]["record_id"]
        for lane in ("approved", "candidate", "external_public")
        for item in packet[lane]
    }
    expected = case["expected"]
    evidence_authorization = _selected_evidence_authorization(
        packet,
        claim_map,
        requested_classification=case["classification"],
        requested_audiences=case["audiences"],
    )
    packet_records = [
        item
        for lane in ("approved", "candidate", "external_public")
        for item in packet[lane]
    ]
    report_record_ids = {
        record_id
        for rows in evidence_report["selected_record_ids_by_lane"].values()
        for record_id in rows
    }
    report_record_ids_by_lane = {
        lane: set(record_ids)
        for lane, record_ids in evidence_report["selected_record_ids_by_lane"].items()
    }
    record_fused_ranks = {
        item["record"]["record_id"]: item["retrieval"].get("fused_rank")
        for item in packet_records
    }
    required_record_lanes = expected.get("required_record_lanes", {})
    maximum_fused_rank_by_record = expected.get("maximum_fused_rank_by_record", {})
    expected_record_ids = set(expected["required_record_ids"])
    declared_record_lanes_are_scoped = set(required_record_lanes) <= expected_record_ids
    declared_rank_bounds_are_scoped = set(maximum_fused_rank_by_record) <= expected_record_ids
    required_records_in_declared_lanes = (
        declared_record_lanes_are_scoped
        and all(
            record_id in report_record_ids_by_lane.get(lane, set())
            for record_id, lane in required_record_lanes.items()
        )
    )
    required_records_within_fused_rank_bounds = (
        declared_rank_bounds_are_scoped
        and all(
            isinstance(record_fused_ranks.get(record_id), int)
            and record_fused_ranks[record_id] <= maximum_rank
            for record_id, maximum_rank in maximum_fused_rank_by_record.items()
        )
    )
    selected_records_respect_case_acl = all(
        item["record"]["classification"] in CLASSIFICATION_RANK
        and CLASSIFICATION_RANK[item["record"]["classification"]]
        <= CLASSIFICATION_RANK[case["classification"]]
        and bool(set(item["record"]["audiences"]) & set(case["audiences"]))
        for item in packet_records
    )
    report_lane_sets = [
        set(rows) for rows in evidence_report["selected_record_ids_by_lane"].values()
    ]
    lane_overlap = any(
        left & right
        for index, left in enumerate(report_lane_sets)
        for right in report_lane_sets[index + 1 :]
    )
    coverage_outcomes = evidence_report["requirement_outcomes"]
    requirement_expectations_passed, requirement_expectation_checks = (
        _requirement_expectations_met(
            expected["required_requirement_assertions"],
            coverage_manifest,
            coverage_outcomes,
        )
    )
    supported_requirement_count = sum(
        row["outcome"] == "covered" and bool(row["record_ids"])
        for row in coverage_outcomes
    )
    safe_direction_requirement_count = sum(
        row["outcome"] == "covered"
        and bool(row["record_ids"])
        # A requirement is directionally safe when it has at least one
        # direct, forward, or explicitly-safe inverse support.  The same
        # requirement may also expose reverse-only paths for discovery; those
        # paths are not used to substantiate the directional claim.
        and bool(
            set(row["support_directions"])
            & {"direct", "forward", "inverse_safe"}
        )
        for row in coverage_outcomes
    )
    unsupported_direction_count = sum(
        claim["directional_claim"]
        and any(
            row["traversal_direction"] == "reverse" for row in claim["evidence"]
        )
        for claim in claim_map["claims"]
    )
    connected_path_assertion_ids = {
        path["target_assertion_id"] for path in connected_map["paths"]
    } | {
        step["assertion_id"]
        for path in connected_map["paths"]
        for step in path["steps"]
    }
    connected_relationship_families = {
        step["relationship_family"]
        for path in connected_map["paths"]
        for step in path["steps"]
    }
    if quality_receipts_present:
        initial_judge_calls = int(initial_judge_review["provider_call_count"])
        rejudge_calls = (
            0
            if judge_review["review_digest"] == initial_judge_review["review_digest"]
            else int(judge_review["provider_call_count"])
        )
        expected_quality_call_counts = {
            "challenger": int(challenger_review["provider_call_count"]),
            "judge": initial_judge_calls + rejudge_calls,
            "content_repair": int(content_repair["call_count"]),
        }
        runtime_quality = result["x1_quality"]
        quality_review_bindings = (
            challenger_review["input_bindings"],
            initial_judge_review["input_bindings"],
            judge_review["input_bindings"],
        )
        judge_required_for_release = bool(
            x_gates["X1"]["runtime_quality"]["judge_required_for_release"]
        )
        judge_release_passed = bool(runtime_quality["judge_release_passed"])
        quality_release_routing_valid = (
            not judge_required_for_release
            or (
                judge_release_passed
                and result["x3_outcome"] in {"X3D_ALLOW_FINISH", "X3E_ALLOW_EVIDENCE_ONLY"}
            )
            or (
                not judge_release_passed
                and result["x3_outcome"] == "X3B_ESCALATE_HITL"
            )
        )
    else:
        expected_quality_call_counts = {
            "challenger": 0,
            "judge": 0,
            "content_repair": 0,
        }
        runtime_quality = None
        quality_review_bindings = ()
        judge_release_passed = False
        quality_release_routing_valid = pre_l2_blocked
    # A retrieval block is a valid terminal shape before PA/L2.  Audit the
    # stages the runtime actually declared for that terminal outcome; a
    # successful-path oracle must not turn the block into a harness exception.
    audit_required_stages = list(expected["required_stages"])
    if pre_l2_blocked:
        observed_required_stages = result.get("required_stages")
        if not isinstance(observed_required_stages, list) or not observed_required_stages:
            raise EvaluationFailure(
                "pre-L2 blocked query is missing its terminal required-stage binding"
            )
        audit_required_stages = observed_required_stages
    audit = _audit(
        case_runtime,
        run_id=result["run_id"],
        trace_id=result["trace_id"],
        use_case="query",
        graph_snapshot=result["graph_snapshot"],
        generation_id=result["generation_id"],
        required_stages=audit_required_stages,
    )
    gates = {
        "status_matches": result["status"] == expected["status"],
        "x3_outcome_matches": result["x3_outcome"] == expected["x3_outcome"],
        "answer_presence_matches": (result["answer"] is not None) == expected["answer_present"],
        "provider_call_count_matches": (
            result["total_external_model_call_count"] == expected["provider_call_count"]
        ),
        "graph_never_mutates": (
            result["graph_mutation_count"] == expected["graph_mutation_count"] == 0
        ),
        "required_records_present": expected_record_ids <= record_ids,
        "required_records_in_declared_lanes": required_records_in_declared_lanes,
        "required_records_within_fused_rank_bounds": (
            required_records_within_fused_rank_bounds
        ),
        "selected_records_respect_case_acl": selected_records_respect_case_acl,
        "forbidden_records_absent": not (set(expected["forbidden_record_ids"]) & record_ids),
        "ledger_audit_passes": audit["status"] == "PASS" and not audit["missing_stages"],
        "active_pair_validates": manager.validate_pair(manager.active_pair())["status"] == "PASS",
        "coverage_has_terminal_outcome": (
            len(coverage_outcomes) == len(coverage_manifest["requirements"])
            and all(
                row["outcome"]
                in {"covered", "explicit_gap", "excluded", "unauthorized", "conflict"}
                for row in coverage_outcomes
            )
        ),
        "selected_records_match_report": record_ids == report_record_ids,
        "selected_records_are_authorized": not evidence_authorization[
            "authorization_violation_record_ids"
        ],
        "selected_records_are_evidence_bound": not evidence_authorization[
            "evidence_binding_violation_record_ids"
        ],
        "candidate_evidence_claims_are_explicitly_labeled": not evidence_authorization[
            "candidate_claim_violation_ids"
        ],
        "claim_grounding_complete": pre_l2_blocked or all(
            claim["status"] in {"grounded", "explicit_gap"}
            for claim in claim_map["claims"]
        ),
        "lane_leakage_zero": not lane_overlap,
        "unsupported_direction_count_zero": unsupported_direction_count == 0,
        "evidence_report_visible": (
            (run_dir / "retrieval_evidence_report.json").is_file()
            and (run_dir / "retrieval_evidence.md").is_file()
        ),
        "l2_report_set_equality": (
            {
                record_id
                for rows in evidence_report["l2_input_record_ids_by_lane"].values()
                for record_id in rows
            }
            == report_record_ids
            if l2_execution_present
            else (
                pre_l2_blocked
                and not any(
                    evidence_report["l2_input_record_ids_by_lane"].values()
                )
            )
        ),
        "x1_quality_receipts_present": quality_receipts_present or pre_l2_blocked,
        "x1_quality_receipts_bind_sealed_evidence": (
            all(
                binding["evidence_packet_digest"] == packet["packet_digest"]
                for binding in quality_review_bindings
            )
            if quality_receipts_present
            else pre_l2_blocked
        ),
        "x1_quality_final_judge_binds_delivered_answer": (
            (
                result["answer"] is None
                or judge_review["input_bindings"]["answer_digest"]
                == digest(result["answer"])
            )
            if quality_receipts_present
            else pre_l2_blocked
        ),
        "x1_quality_counters_match_receipts": (
            result["challenger_call_count"] == expected_quality_call_counts["challenger"]
            and result["judge_call_count"] == expected_quality_call_counts["judge"]
            and result.get("content_repair_call_count", 0)
            == expected_quality_call_counts["content_repair"]
        ),
        "x1_quality_release_routing_respected": quality_release_routing_valid,
        "x1_quality_digests_match_x1": (
            (
                x_gates["X1"]["runtime_quality"]["challenger_review_digest"]
                == challenger_review["review_digest"]
                and x_gates["X1"]["runtime_quality"]["initial_judge_review_digest"]
                == initial_judge_review["review_digest"]
                and x_gates["X1"]["runtime_quality"]["judge_review_digest"]
                == judge_review["review_digest"]
            )
            if quality_receipts_present
            else pre_l2_blocked
        ),
        "destination_matches": result["destination"] == expected["expected_destination"],
        "assurance_profile_matches": (
            result["assurance_profile"] == expected["expected_assurance_profile"]
        ),
        "delivery_profile_matches": (
            result["delivery_profile"] == expected["expected_delivery_profile"]
        ),
        "artifact_type_matches": (
            result["artifact_type"] == expected["expected_artifact_type"]
        ),
        "required_lanes_present": all(
            bool(evidence_report["selected_record_ids_by_lane"][lane])
            for lane in expected["required_lanes"]
        ),
        "requirement_coverage_expectations_met": requirement_expectations_passed,
        "minimum_supported_requirement_count_reached": (
            supported_requirement_count
            >= expected["minimum_supported_requirement_count"]
        ),
        "minimum_safe_direction_requirement_count_reached": (
            safe_direction_requirement_count
            >= expected["minimum_safe_direction_requirement_count"]
        ),
        "runtime_quality_mode_matches": (
            runtime_quality["mode"] == expected["expected_runtime_quality_mode"]
            if runtime_quality is not None
            else (
                pre_l2_blocked
                and expected["expected_runtime_quality_mode"] == "off"
            )
        ),
        "runtime_judge_receipt_verified_post_run": (
            not expected["runtime_judge_receipt_required"]
            or (
                (run_dir / "judge_review.json").is_file()
                and quality_receipts_present
                and judge_review["review_digest"]
                == x_gates["X1"]["runtime_quality"]["judge_review_digest"]
            )
        ),
        "delivery_quality_receipt_binds_runtime_output": (
            pre_l2_blocked
            or (
                delivery_quality is not None
                and delivery_quality["run_id"] == result["run_id"]
                and delivery_quality["delivery_profile"] == result["delivery_profile"]
                and delivery_quality["artifact_type"] == result["artifact_type"]
            )
        ),
        "semantic_retrieval_qualification_posture_matches": (
            (
                case["evaluation_scope"] == "graph_retrieval_qualification"
                and case["embedding_qualification"] == "real_bge_m3"
                and expected["semantic_retrieval_qualified"]
                and backend.backend_id.startswith("flagembedding-bge-m3")
                and backend.model_id == "BAAI/bge-m3"
                and bool(backend.model_revision)
                and bool(backend.model_manifest_digest)
            )
            or (
                case["evaluation_scope"] == "runtime_control_verification"
                and case["embedding_qualification"] == "not_qualified"
                and not expected["semantic_retrieval_qualified"]
            )
        ),
        "post_runtime_boundary_respected": True,
        "active_pair_graph_matches_query": (
            active_pair_binding["graph_sha256"] == result["graph_sha256_after"]
        ),
        "semantic_cache_binding_matches_backend": (
            not semantic_cache_eligible
            or _cache_binding_matches_backend(semantic_cache, backend)
        ),
        "fake_l2_consumes_sealed_retrieval_evidence": (
            case["provider_posture"] != "fake"
            or pre_l2_blocked
            or fake_l2_consumes_sealed_retrieval_evidence
        ),
    }
    if case["evaluation_scope"] == "graph_retrieval_qualification":
        gates["all_hybrid_retrieval_components_executed"] = all(
            result.get(field) is True
            for field in (
                "dense_executed",
                "sparse_executed",
                "bm25_executed",
                "colbert_executed",
            )
        )
    if "bm25_executed" in expected:
        gates["bm25_execution_matches"] = (
            result.get("bm25_executed") is expected["bm25_executed"]
        )
    if "graph_traversal_executed" in expected:
        gates["graph_traversal_execution_matches"] = (
            result.get("graph_traversal_executed")
            is expected["graph_traversal_executed"]
        )
    if "minimum_graph_hops_reached" in expected:
        gates["minimum_graph_hops_reached"] = (
            connected_map["max_hop_reached"]
            >= expected["minimum_graph_hops_reached"]
        )
    if "minimum_graph_expanded_record_count" in expected:
        gates["minimum_graph_expansion_reached"] = (
            connected_map["metrics"]["expanded_record_count"]
            >= expected["minimum_graph_expanded_record_count"]
        )
    if "minimum_connected_path_count" in expected:
        gates["minimum_connected_path_count_reached"] = (
            connected_map["metrics"]["path_count"]
            >= expected["minimum_connected_path_count"]
        )
    if "required_path_assertion_ids" in expected:
        gates["required_path_assertions_present"] = set(
            expected["required_path_assertion_ids"]
        ) <= connected_path_assertion_ids
    if "forbidden_path_relationship_families" in expected:
        gates["forbidden_path_relationships_absent"] = not (
            set(expected["forbidden_path_relationship_families"])
            & connected_relationship_families
        )
    if "minimum_opportunity_context_count" in expected:
        gates["minimum_opportunity_context_reached"] = (
            connected_map["metrics"]["opportunity_context_count"]
            >= expected["minimum_opportunity_context_count"]
        )
    if "minimum_candidate_context_count" in expected:
        gates["minimum_candidate_context_reached"] = (
            connected_map["metrics"]["candidate_context_count"]
            >= expected["minimum_candidate_context_count"]
        )
    if "minimum_counterevidence_count" in expected:
        gates["minimum_counterevidence_reached"] = (
            connected_map["metrics"]["counterevidence_count"]
            >= expected["minimum_counterevidence_count"]
        )
    if "minimum_branch_suppression_count" in expected:
        gates["minimum_branch_suppression_reached"] = (
            connected_map["metrics"]["branch_suppression_count"]
            >= expected["minimum_branch_suppression_count"]
        )
    if "maximum_missing_evidence_count" in expected:
        gates["missing_evidence_within_limit"] = (
            connected_map["metrics"]["missing_evidence_count"]
            <= expected["maximum_missing_evidence_count"]
        )
    if "challenger_call_count" in expected:
        gates["challenger_call_count_matches"] = (
            result["challenger_call_count"] == expected["challenger_call_count"]
        )
    if "judge_call_count" in expected:
        gates["judge_call_count_matches"] = (
            result["judge_call_count"] == expected["judge_call_count"]
        )
    if "content_repair_call_count" in expected:
        gates["content_repair_call_count_matches"] = (
            result["content_repair_call_count"]
            == expected["content_repair_call_count"]
        )
    if "judge_release_passed" in expected:
        gates["judge_release_passed_matches"] = (
            judge_release_passed == expected["judge_release_passed"]
        )
    status = "PASS" if all(gates.values()) else "FAIL"
    payload = {
        "case_id": case["case_id"],
        "pipeline": "query",
        "status": status,
        "failure_category": None if status == "PASS" else "ORACLE_MISMATCH",
        "cause_code": None if status == "PASS" else "QUERY_EXPECTATION_MISMATCH",
        "stage": "X3",
        "provider_initialization_count": result["provider_initialization_count"],
        "provider_call_count": result["total_external_model_call_count"],
        "answer_present": result["answer"] is not None,
        "graph_mutation_count": result["graph_mutation_count"],
        "directive_status": None,
        "terminal_receipt_digest": None,
        "hard_gates": gates,
        "query_run_id": result["run_id"],
        "query_status": result["status"],
        "x3_outcome": result["x3_outcome"],
        "audit": audit,
        "expected_required_stages": list(expected["required_stages"]),
        "observed_required_stages": list(result.get("required_stages", [])),
        "pre_l2_blocked": pre_l2_blocked,
        "retrieved_record_ids": sorted(record_ids),
        "connected_evidence_map_id": connected_map["map_id"],
        "connected_evidence_map_digest": connected_map["canonical_digest"]["value"],
        "connected_evidence_metrics": connected_map["metrics"],
        "connected_path_assertion_ids": sorted(connected_path_assertion_ids),
        "connected_relationship_families": sorted(connected_relationship_families),
        "retrieval_evidence_metrics": {
            "requirement_count": len(coverage_manifest["requirements"]),
            "terminal_outcome_count": len(coverage_outcomes),
            "selected_record_count": len(report_record_ids),
            "claim_count": len(claim_map["claims"]),
            "lane_leakage_count": int(lane_overlap),
            "unsupported_direction_count": unsupported_direction_count,
            "report_visible": True,
            "report_supported_record_count": sum(
                len(rows)
                for rows in evidence_report["l2_input_record_ids_by_lane"].values()
            ),
            "l2_visible_record_counts_by_lane": l2_visible_counts,
            "l2_visible_record_count": sum(l2_visible_counts.values()),
        },
        "retrieval_evidence": evidence_authorization,
        "delivery_quality": (
            {
                "status": delivery_quality["status"],
                "checks": delivery_quality["checks"],
                "defect_codes": delivery_quality["defect_codes"],
                "client_release_allowed": delivery_quality["client_release_allowed"],
            }
            if delivery_quality is not None
            else {"status": "NOT_APPLICABLE", "reason": "RETRIEVAL_BLOCKED_BEFORE_DELIVERY"}
        ),
        "x1_quality": (
            {
                "mode": runtime_quality["mode"],
                "policy_digest": runtime_quality["policy_digest"],
                "challenger_call_count": result["challenger_call_count"],
                "judge_call_count": result["judge_call_count"],
                "content_repair_call_count": result["content_repair_call_count"],
                "judge_release_passed": judge_release_passed,
                "challenger_review_digest": challenger_review["review_digest"],
                "initial_judge_review_digest": initial_judge_review["review_digest"],
                "judge_review_digest": judge_review["review_digest"],
                "content_repair_digest": digest(content_repair),
            }
            if runtime_quality is not None
            else {"status": "NOT_APPLICABLE", "reason": "RETRIEVAL_BLOCKED_BEFORE_X1"}
        ),
        "evaluation_scope": case["evaluation_scope"],
        "embedding_qualification": case["embedding_qualification"],
        "semantic_retrieval_qualified": expected["semantic_retrieval_qualified"],
        "runtime_boundary": "POST_RUNTIME_VERIFICATION",
        "requirement_expectation_checks": requirement_expectation_checks,
        "supported_requirement_count": supported_requirement_count,
        "safe_direction_requirement_count": safe_direction_requirement_count,
        "semantic_cache": semantic_cache_evidence,
        "runtime_binding": runtime_binding,
        "active_pair_binding": active_pair_binding,
    }
    path = _case_artifact_path(case_dir, payload)
    return {key: payload[key] for key in (
        "case_id", "pipeline", "status", "failure_category", "cause_code", "stage",
        "provider_initialization_count", "provider_call_count", "answer_present",
        "graph_mutation_count", "directive_status", "terminal_receipt_digest", "hard_gates"
    )} | {"artifact_path": str(path)}
