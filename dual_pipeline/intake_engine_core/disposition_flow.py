"""Disposition processing and ledger finalization for IntakeEngine."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from dual_pipeline.a2a_finalization import VerifiedA2AFinalization
from dual_pipeline.core import digest, read_json, sha256_file, write_json
from dual_pipeline.graph_delta import build_planned_graph_delta
from dual_pipeline.intake_core.change_set import build_graph_change_set
from dual_pipeline.intake_core.constants import (
    INGESTION_DECISION_LEDGER_ENGLISH_JSON,
    INGESTION_DECISION_LEDGER_JSON,
    INGESTION_DECISION_LEDGER_MARKDOWN,
    INTAKE_STAGES,
)
from dual_pipeline.intake_core.decision_ledger import (
    build_ingestion_decision_explanation,
    build_ingestion_decision_ledger,
    render_ingestion_decision_ledger_markdown,
)
from dual_pipeline.intake_core.dispositions import build_dispositions
from dual_pipeline.intake_core.hitl import build_hitl_batches
from dual_pipeline.intake_core.proposals import _correlation
from dual_pipeline.semantic_cache import SemanticCache
from dual_pipeline.telemetry import RunRecorder


def _check_candidate_x1(
    disposition: dict[str, Any],
    candidate: Any,
    graph_delta: dict[str, Any],
) -> dict[str, bool]:
    candidate_id = disposition["candidate_id"]
    return {
        "schema_valid": isinstance(candidate, dict) and candidate.get("candidate_id") == candidate_id,
        "evidence_linked": bool(disposition["evidence_ids"]) or disposition["persistent_lane"] == "none",
        "classification_valid": disposition["classification_valid"],
        "audience_valid": disposition["audience_valid"],
        "unauthorized_side_effect_count_zero": (
            graph_delta["validation"]["unauthorized_side_effect_count"] == 0
        ),
    }


def _check_candidate_x2(
    disposition: dict[str, Any],
    candidate: Any,
) -> dict[str, bool]:
    return {
        "materiality_checked": (
            isinstance(candidate, dict)
            and isinstance(candidate.get("materiality"), str)
            and bool(candidate["materiality"])
        ),
        "approval_authority_valid": (
            disposition["disposition"] != "APPROVED"
            or disposition["source_authority_sufficient"]
        ),
        "hitl_boundary_valid": (
            not disposition["hitl_required"]
            or disposition["hitl_complete"]
            or disposition["persistent_lane"] != "approved"
        ),
    }


def _evaluate_single_candidate_exit_gate(
    disposition: dict[str, Any],
    candidate: Any,
    bundle: dict[str, Any],
    graph_delta: dict[str, Any],
    recorder: RunRecorder,
) -> None:
    candidate_id = disposition["candidate_id"]
    corr = _correlation(source_id=bundle["source_id"], candidate_id=candidate_id)

    x1_checks = _check_candidate_x1(disposition, candidate, graph_delta)
    x1_status = "PASS" if all(x1_checks.values()) else "FAIL"
    recorder.record(
        stage="X1",
        event_type="exit_gate_event",
        payload={"candidate_id": candidate_id, "status": x1_status, "checks": x1_checks},
        correlation=corr,
        status=x1_status,
    )
    if x1_status != "PASS":
        raise RuntimeError(f"Fail-Fast: Candidate X1 exit gate failed for '{candidate_id}'. Checks: {x1_checks}")

    x2_checks = _check_candidate_x2(disposition, candidate)
    x2_status = "PASS" if all(x2_checks.values()) else "FAIL"
    recorder.record(
        stage="X2",
        event_type="exit_gate_event",
        payload={
            "candidate_id": candidate_id,
            "status": x2_status,
            "checks": x2_checks,
            "authority_sufficient_for_approval": disposition["source_authority_sufficient"],
            "hitl_required": disposition["hitl_required"],
            "hitl_complete": disposition["hitl_complete"],
        },
        correlation=corr,
        status=x2_status,
    )
    if x2_status != "PASS":
        raise RuntimeError(f"Fail-Fast: Candidate X2 exit gate failed for '{candidate_id}'. Checks: {x2_checks}")

    recorder.record(
        stage="X3",
        event_type="assertion_disposition",
        payload=disposition,
        correlation=corr,
        lane=(disposition["persistent_lane"] if disposition["persistent_lane"] != "none" else None),
    )


def _evaluate_candidate_exit_gates(
    dispositions: list[dict[str, Any]],
    bundle: dict[str, Any],
    graph_delta: dict[str, Any],
    recorder: RunRecorder,
) -> dict[str, Any]:
    candidate_by_id = {
        candidate["candidate_id"]: candidate for candidate in bundle["candidates"]
    }
    for disposition in dispositions:
        candidate = candidate_by_id.get(disposition["candidate_id"])
        _evaluate_single_candidate_exit_gate(disposition, candidate, bundle, graph_delta, recorder)
    return candidate_by_id


def _evaluate_graph_exit_gates(
    bundle: dict[str, Any],
    graph_delta: dict[str, Any],
    graph_delta_digest: str,
    graph_delta_path_name: str,
    schema_and_lineage_valid: bool,
    lane_separation_valid: bool,
    all_operations_accounted_for: bool,
    recorder: RunRecorder,
) -> None:
    for stage, payload in (
        (
            "X1",
            {
                "exit_gate": "graph_delta_summary",
                "graph_delta_summary_digest": graph_delta_digest,
                "graph_delta_summary_path": graph_delta_path_name,
                "table": graph_delta["table"],
                "schema_and_lineage_valid": schema_and_lineage_valid,
                "unauthorized_side_effect_count": graph_delta["validation"][
                    "unauthorized_side_effect_count"
                ],
            },
        ),
        (
            "X2",
            {
                "exit_gate": "graph_delta_summary",
                "graph_delta_summary_digest": graph_delta_digest,
                "candidate_lane_operations": graph_delta["validation"][
                    "candidate_lane_operations"
                ],
                "canonical_lane_operations": graph_delta["validation"][
                    "canonical_lane_operations"
                ],
                "lane_separation_valid": lane_separation_valid,
            },
        ),
        (
            "X3",
            {
                "exit_gate": "graph_delta_summary",
                "graph_delta_summary_digest": graph_delta_digest,
                "change_set_id": graph_delta["change_set_id"],
                "operation_count": graph_delta["operation_count"],
                "all_operations_accounted_for": all_operations_accounted_for,
            },
        ),
    ):
        status = (
            "PASS"
            if (
                stage == "X1"
                and payload["schema_and_lineage_valid"]
                and payload["unauthorized_side_effect_count"] == 0
                or stage == "X2"
                and payload["lane_separation_valid"]
                or stage == "X3"
                and payload["all_operations_accounted_for"]
            )
            else "FAIL"
        )
        recorder.record(
            stage=stage,
            event_type="exit_gate_event",
            payload=payload,
            correlation=_correlation(source_id=bundle["source_id"]),
            record_count=graph_delta["operation_count"],
            status=status,
        )
        if status != "PASS":
            raise RuntimeError(
                f"Fail-Fast: Stage {stage} exit gate failed with status '{status}'. Payload: {payload}"
            )


def _stage_candidate_cache_writeback(
    candidate_id: str,
    binding: dict[str, Any],
    comparison_values: dict[str, Any],
    bundle: dict[str, Any],
    semantic_cache: SemanticCache,
) -> tuple[dict[str, Any], Any]:
    value = comparison_values.get(candidate_id)
    if not isinstance(value, dict):
        return {
            "candidate_id": candidate_id,
            "status": "SKIPPED",
            "reason": "CACHE_COMPARISON_OUTPUT_MISSING",
            "entry_id": None,
        }, None

    write_result = semantic_cache.stage(
        context=binding["context"],
        intent_digest=binding["intent_digest"],
        intent_vector=binding["intent_vector"],
        output={"comparison": value},
        outcome="C0.2_COMPARISON_COMPLETE",
        source_run_id=bundle["run_id"],
        source_trace_id=bundle["trace_id"],
        replay_eligible=True,
    )
    writeback = {
        "candidate_id": candidate_id,
        "status": write_result.status,
        "reason": write_result.reason,
        "entry_id": (write_result.entry.entry_id if write_result.entry else None),
        "replay_eligible": (write_result.entry.replay_eligible if write_result.entry else False),
    }
    return writeback, write_result.entry


def _record_cache_writeback_telemetry(
    writeback: dict[str, Any],
    bundle: dict[str, Any],
    cache_bindings: dict[str, dict[str, Any]],
    semantic_cache: SemanticCache,
    recorder: RunRecorder,
    review_applied: bool,
) -> None:
    candidate_id = writeback["candidate_id"]
    binding = cache_bindings.get(candidate_id) if candidate_id else None
    context_digest = (
        digest(semantic_cache.compatibility(binding["context"]))
        if binding
        else None
    )
    recorder.record(
        stage="L4",
        event_type="pipeline_event",
        payload={
            "semantic_cache_writeback": writeback,
            "context_digest": context_digest,
            "intent_digest": binding["intent_digest"] if binding else None,
            "policy_digest": semantic_cache.policy_digest,
            "embedding_binding": semantic_cache.last_binding_result,
            "review_applied": review_applied,
        },
        correlation=_correlation(
            source_id=bundle["source_id"],
            candidate_id=candidate_id,
        ),
        route="candidate_comparison",
        status="PASS",
    )


def _process_cache_writebacks(
    bundle: dict[str, Any],
    cache_bindings: dict[str, dict[str, Any]],
    cache_observations: list[dict[str, Any]],
    comparison: dict[str, Any],
    semantic_cache: SemanticCache,
    recorder: RunRecorder,
    review_applied: bool,
) -> tuple[list[dict[str, Any]], list[tuple[Any, dict[str, Any]]]]:
    cache_writebacks: list[dict[str, Any]] = []
    pending_cache_entries = []
    comparison_values = comparison.get("comparisons", {})

    for candidate_id, binding in sorted(cache_bindings.items()):
        wb, entry = _stage_candidate_cache_writeback(candidate_id, binding, comparison_values, bundle, semantic_cache)
        cache_writebacks.append(wb)
        if entry is not None:
            pending_cache_entries.append((entry, wb))

    if not cache_writebacks:
        reason = cache_observations[0]["reason"] if cache_observations else "CACHE_NO_ELIGIBLE_CANDIDATES"
        cache_writebacks.append({
            "candidate_id": None,
            "status": "SKIPPED",
            "reason": reason,
            "entry_id": None,
            "replay_eligible": False,
        })

    for writeback in cache_writebacks:
        _record_cache_writeback_telemetry(
            writeback=writeback,
            bundle=bundle,
            cache_bindings=cache_bindings,
            semantic_cache=semantic_cache,
            recorder=recorder,
            review_applied=review_applied,
        )

    return cache_writebacks, pending_cache_entries


def _resolve_ingestion_reasoning(
    run_dir: Path,
    ingestion_reasoning: dict[str, Any] | None,
) -> dict[str, Any]:
    if ingestion_reasoning is not None:
        return ingestion_reasoning
    reasoning_path = run_dir / "ingestion_reasoning_execution.json"
    if reasoning_path.is_file():
        return read_json(reasoning_path)
    return {
        "status": "DISABLED",
        "mode": "off",
        "disposition": "DETERMINISTIC_ONLY",
        "decision_id": None,
        "decision_digest": None,
        "decision_reason_code": "FEATURE_DISABLED",
        "initial_complexity": None,
        "final_complexity": None,
        "selected_model_tier": None,
        "selected_model": None,
        "reasoning_effort": None,
        "provider_egress_allowed": False,
        "packet_digest": None,
        "proposal_id": None,
        "proposal_digest": None,
        "provider_call_count": 0,
        "primary_call_count": 0,
        "repair_call_count": 0,
        "escalation_call_count": 0,
        "judge_call_count": 0,
        "challenger_call_count": 0,
        "failure_code": None,
        "graph_mutation_count": 0,
        "canonical_approval_count": 0,
    }


def _record_hitl_batch_items(
    bundle: dict[str, Any],
    hitl: dict[str, Any],
    recorder: RunRecorder,
) -> None:
    for batch in hitl["review_batches"]:
        for item in batch["items"]:
            recorder.record(
                stage="X2",
                event_type="hitl_review_item",
                payload=item,
                correlation=_correlation(
                    source_id=bundle["source_id"],
                    candidate_id=item["candidate_ids"][0],
                    hitl_item_id=item["review_item_id"],
                ),
            )


def _write_and_record_decision_ledger(
    run_dir: Path,
    bundle: dict[str, Any],
    routes: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
    dispositions: list[dict[str, Any]],
    change_set: dict[str, Any] | None,
    graph_delta: dict[str, Any],
    recorder: RunRecorder,
) -> dict[str, Any]:
    decision_ledger = build_ingestion_decision_ledger(
        bundle, routes, resolutions, dispositions, change_set, graph_delta
    )
    ledger_path = run_dir / INGESTION_DECISION_LEDGER_JSON
    english_path = run_dir / INGESTION_DECISION_LEDGER_ENGLISH_JSON
    markdown_path = run_dir / INGESTION_DECISION_LEDGER_MARKDOWN

    write_json(ledger_path, decision_ledger)
    ledger_sha = sha256_file(ledger_path)

    explanation = build_ingestion_decision_explanation(
        decision_ledger,
        technical_ledger_sha256=ledger_sha,
        graph_delta=graph_delta,
    )
    write_json(english_path, explanation)
    markdown_path.write_text(
        render_ingestion_decision_ledger_markdown(explanation),
        encoding="utf-8",
        newline="\n",
    )
    english_sha = sha256_file(english_path)
    markdown_sha = sha256_file(markdown_path)

    recorder.record(
        stage="L4",
        event_type="pipeline_event",
        payload={
            "review_event_type": "ingestion_decision_ledger",
            "ledger_type": decision_ledger["ledger_type"],
            "ledger_schema_version": decision_ledger["schema_version"],
            "ledger_path": ledger_path.name,
            "ledger_sha256": ledger_sha,
            "english_path": english_path.name,
            "english_sha256": english_sha,
            "english_canonical_digest": explanation["canonical_digest"]["value"],
            "table_path": markdown_path.name,
            "table_sha256": markdown_sha,
            "candidate_count": decision_ledger["candidate_count"],
            "planned_graph_delta_digest": decision_ledger["planned_graph_delta_digest"],
        },
        correlation=_correlation(source_id=bundle["source_id"]),
        record_count=decision_ledger["candidate_count"],
        status="PASS",
    )
    return {
        "ledger_path_name": ledger_path.name, "ledger_sha256": ledger_sha,
        "english_path_name": english_path.name, "english_sha256": english_sha,
        "canonical_digest_value": explanation["canonical_digest"]["value"],
        "markdown_path_name": markdown_path.name, "markdown_sha256": markdown_sha,
    }


def _is_operation_payload_valid(operation: dict[str, Any], payload: dict[str, Any]) -> bool:
    op, disp = operation["operation"], payload.get("disposition")
    if op == "add_approved_assertion":
        return disp == "APPROVED"
    if op == "supersede_assertion":
        return disp in {"CANDIDATE", "REJECTED_NOOP"}
    if op in {"add_relationship", "add_action_item", "add_evidence"}:
        return disp in {"APPROVED", "CANDIDATE"}
    return disp == "CANDIDATE"


def _check_lane_separation(operations: list[dict[str, Any]], operation_payloads: dict[str, Any]) -> bool:
    for op in operations:
        payload = operation_payloads.get(op["operation_id"])
        if not isinstance(payload, dict) or not _is_operation_payload_valid(op, payload):
            return False
    return True


def _check_operations_accounted_for(graph_delta: dict[str, Any], operations: list[dict[str, Any]]) -> bool:
    if not graph_delta["validation"]["all_operations_accounted_for"] or graph_delta["operation_count"] != len(operations):
        return False
    if len({op["operation_id"] for op in operations}) != len(operations):
        return False
    return [op["sequence"] for op in operations] == list(range(1, len(operations) + 1))


def _validate_and_evaluate_graph_exit_gates(
    bundle: dict[str, Any], graph_delta: dict[str, Any], graph_delta_digest: str, graph_delta_path: Path,
    change_set: dict[str, Any] | None, candidate_by_id: dict[str, Any], dispositions: list[dict[str, Any]], recorder: RunRecorder,
) -> None:
    operations = change_set["operations"] if change_set else []
    operation_payloads = change_set["extensions"]["com.partner_advisory.nee.operation-payloads"] if change_set else {}
    schema_and_lineage_valid = (
        graph_delta["validation"]["status"] == "PASS"
        and graph_delta["run_id"] == bundle["run_id"]
        and graph_delta["trace_id"] == bundle["trace_id"]
        and graph_delta["source_id"] == bundle["source_id"]
        and graph_delta["source_version_id"] == bundle["source_version_id"]
        and len(candidate_by_id) == len(dispositions)
    )
    lane_separation_valid = _check_lane_separation(operations, operation_payloads)
    all_operations_accounted_for = _check_operations_accounted_for(graph_delta, operations)
    _evaluate_graph_exit_gates(
        bundle=bundle, graph_delta=graph_delta, graph_delta_digest=graph_delta_digest,
        graph_delta_path_name=graph_delta_path.name, schema_and_lineage_valid=schema_and_lineage_valid,
        lane_separation_valid=lane_separation_valid, all_operations_accounted_for=all_operations_accounted_for,
        recorder=recorder,
    )


def _record_change_set_execution(
    run_dir: Path,
    bundle: dict[str, Any],
    change_set: dict[str, Any] | None,
    previous_change_set: dict[str, Any] | None,
    recorder: RunRecorder,
) -> None:
    change_set_path = run_dir / "graph_change_set.json"
    if change_set:
        write_json(change_set_path, change_set)
        recorder.record(
            stage="X3",
            event_type="graph_change_set",
            payload=change_set,
            correlation=_correlation(source_id=bundle["source_id"]),
            record_count=len(change_set["operations"]),
        )
    elif previous_change_set is not None:
        recorder.record(
            stage="X3",
            event_type="graph_change_set",
            payload={
                "status": "SUPERSEDED_NO_OPERATIONS",
                "run_id": bundle["run_id"],
                "trace_id": bundle["trace_id"],
                "previous_change_set_digest": digest(previous_change_set),
                "current_change_set_digest": None,
            },
            correlation=_correlation(source_id=bundle["source_id"]),
            record_count=0,
        )
        change_set_path.unlink()


def _seal_and_activate_cache(
    bundle: dict[str, Any],
    ingestion_reasoning: dict[str, Any],
    pending_cache_entries: list[tuple[Any, dict[str, Any]]],
    cache_writebacks: list[dict[str, Any]],
    semantic_cache: SemanticCache,
    recorder: RunRecorder,
) -> dict[str, Any]:
    required_stages = list(INTAKE_STAGES)
    if ingestion_reasoning["decision_id"] is not None:
        required_stages.append("L2D")
    if ingestion_reasoning["packet_digest"] is not None:
        required_stages.extend(["PA", "L2"])
    seal = recorder.seal(
        required_stages,
        _correlation(source_id=bundle["source_id"]),
    )
    if not seal or not seal.get("event_chain_digest"):
        raise RuntimeError("Fail-Fast: Recorder seal failed or missing event_chain_digest.")
    for entry, writeback in pending_cache_entries:
        activation = semantic_cache.activate(
            entry,
            source_event_count=seal["event_count"],
            source_event_chain_digest=seal["event_chain_digest"],
        )
        writeback["activation_status"] = activation.status
        writeback["activation_reason"] = activation.reason
    for writeback in cache_writebacks:
        writeback.setdefault("activation_status", "SKIPPED")
        writeback.setdefault("activation_reason", writeback["reason"])
    return seal


def _build_semantic_cache_summary(
    semantic_cache: SemanticCache,
    cache_observations: list[dict[str, Any]],
    cache_writebacks: list[dict[str, Any]],
) -> dict[str, Any]:
    lookup_count = sum(1 for item in cache_observations if item.get("candidate_id") is not None)
    lookup_hit_count = sum(1 for item in cache_observations if item.get("hit") is True)
    writeback_count = sum(1 for item in cache_writebacks if item.get("entry_id") is not None)
    return {
        "mode": semantic_cache.mode,
        "config_source": semantic_cache.config_source,
        "policy_digest": semantic_cache.policy_digest,
        "embedding_binding": semantic_cache.last_binding_result,
        "lookup_count": lookup_count,
        "lookup_hit_count": lookup_hit_count,
        "lookup_observations": cache_observations,
        "writeback_count": writeback_count,
        "writebacks": cache_writebacks,
    }


def finish_dispositions(
    engine: Any,
    *,
    bundle: dict[str, Any],
    routes: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
    recorder: RunRecorder,
    decisions: list[dict[str, Any]],
    graph_hash_before: str,
    source_kind: str,
    package_file_sha256: str,
    a2a_finalization_binding: dict[str, Any] | None,
    a2a_finalization_proof: VerifiedA2AFinalization | None,
    a2a_finalization_reference: dict[str, Any] | None,
    production_binding: dict[str, Any] | None,
    batch_admission: dict[str, str] | None,
    intake_authorization: dict[str, Any] | None,
    review_authorization: dict[str, Any] | None,
    review_applied: bool,
    semantic_cache: SemanticCache,
    cache_bindings: dict[str, dict[str, Any]],
    cache_observations: list[dict[str, Any]],
    comparison: dict[str, Any],
    ingestion_reasoning: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_dir = engine.runtime_root / "runs" / bundle["run_id"]
    ingestion_reasoning = _resolve_ingestion_reasoning(run_dir, ingestion_reasoning)

    dispositions = build_dispositions(bundle, routes, resolutions, decisions)
    hitl = build_hitl_batches(bundle, routes, resolutions, dispositions)
    write_json(run_dir / "hitl_batches.json", hitl)
    write_json(run_dir / "assertion_dispositions.json", dispositions)
    _record_hitl_batch_items(bundle, hitl, recorder)

    change_set_path = run_dir / "graph_change_set.json"
    previous_change_set = read_json(change_set_path) if change_set_path.is_file() else None
    base_snapshot = (
        engine.active_pair_binding["graph_snapshot"]
        if engine.active_pair_binding and engine.active_pair_binding.get("graph_sha256") == sha256_file(engine.graph_path)
        else None
    )
    change_set = build_graph_change_set(
        bundle,
        dispositions,
        resolutions,
        engine.graph_path,
        decisions,
        a2a_finalization_proof=a2a_finalization_proof,
        a2a_finalization_reference=a2a_finalization_reference,
        a2a_finalization_runtime_root=engine.runtime_root,
        base_graph_snapshot=base_snapshot,
    )
    graph_delta = build_planned_graph_delta(
        bundle=bundle,
        change_set=change_set,
        graph_path=engine.graph_path,
    )
    graph_delta_path = run_dir / "graph_delta_summary.json"
    write_json(graph_delta_path, graph_delta)
    graph_delta_digest = digest(graph_delta)

    candidate_by_id = _evaluate_candidate_exit_gates(dispositions, bundle, graph_delta, recorder)
    _validate_and_evaluate_graph_exit_gates(
        bundle=bundle,
        graph_delta=graph_delta,
        graph_delta_digest=graph_delta_digest,
        graph_delta_path=graph_delta_path,
        change_set=change_set,
        candidate_by_id=candidate_by_id,
        dispositions=dispositions,
        recorder=recorder,
    )

    ledger_info = _write_and_record_decision_ledger(
        run_dir=run_dir,
        bundle=bundle,
        routes=routes,
        resolutions=resolutions,
        dispositions=dispositions,
        change_set=change_set,
        graph_delta=graph_delta,
        recorder=recorder,
    )

    _record_change_set_execution(run_dir, bundle, change_set, previous_change_set, recorder)

    cache_writebacks, pending_cache_entries = _process_cache_writebacks(
        bundle=bundle, cache_bindings=cache_bindings, cache_observations=cache_observations,
        comparison=comparison, semantic_cache=semantic_cache, recorder=recorder, review_applied=review_applied,
    )
    _invalidate_superseded_in_cache(change_set, semantic_cache, cache_writebacks)
    seal = _seal_and_activate_cache(
        bundle=bundle, ingestion_reasoning=ingestion_reasoning, pending_cache_entries=pending_cache_entries,
        cache_writebacks=cache_writebacks, semantic_cache=semantic_cache, recorder=recorder,
    )

    graph_hash_after = sha256_file(engine.graph_path)
    if graph_hash_after != graph_hash_before:
        raise RuntimeError("intake planning mutated the immutable graph seed")

    counts = Counter(value["disposition"] for value in dispositions)
    summary = {
        "status": "COMPLETED",
        "milestone": "wave_3_disposition_complete",
        "source_kind": source_kind,
        "package_file_sha256": package_file_sha256,
        "batch_admission": batch_admission,
        "a2a_finalization_binding": a2a_finalization_binding,
        "a2a_finalization_reference": a2a_finalization_reference,
        "intake_id": bundle["intake_id"],
        "run_id": bundle["run_id"],
        "trace_id": bundle["trace_id"],
        "source_id": bundle["source_id"],
        "authorization": intake_authorization,
        "intake_authorization": intake_authorization,
        "review_authorization": review_authorization,
        **(
            production_binding
            if production_binding
            else {
                "production_qualified": False,
                "embedding_backend": None,
                "model_id": None,
                "model_revision": None,
                "model_manifest_digest": None,
                "model_or_index_generation": None,
            }
        ),
        "candidate_count": len(bundle["candidates"]),
        "entity_mention_count": sum(
            len(candidate["entity_mentions"]) for candidate in bundle["candidates"]
        ),
        "entity_resolution_count": len(resolutions),
        "route_count": len(routes),
        "disposition_count": len(dispositions),
        "disposition_counts": dict(sorted(counts.items())),
        "ingestion_decision_ledger_path": ledger_info["ledger_path_name"],
        "ingestion_decision_ledger_sha256": ledger_info["ledger_sha256"],
        "ingestion_decision_english_path": ledger_info["english_path_name"],
        "ingestion_decision_english_sha256": ledger_info["english_sha256"],
        "ingestion_decision_english_canonical_digest": ledger_info["canonical_digest_value"],
        "ingestion_decision_table_path": ledger_info["markdown_path_name"],
        "ingestion_decision_table_sha256": ledger_info["markdown_sha256"],
        "hitl": {
            "tier_0_count": hitl["tier_0_count"],
            "tier_1_count": hitl["tier_1_count"],
            "tier_2_count": hitl["tier_2_count"],
            "decision_count": len(decisions),
            "decisions_digest": digest(decisions),
            "review_applied": review_applied,
            "review_event_count": (max(1, len(decisions)) if review_applied else 0),
        },
        "graph_change_set_id": change_set["change_set_id"] if change_set else None,
        "graph_operation_count": len(change_set["operations"]) if change_set else 0,
        "graph_delta_summary_path": graph_delta_path.name,
        "graph_delta_summary_digest": graph_delta_digest,
        "graph_change_table": graph_delta["table"],
        "graph_sha256_before": graph_hash_before,
        "graph_sha256_after": graph_hash_after,
        "graph_mutation_count": 0,
        "persistent_embedding_write_count": 0,
        "ingestion_reasoning": ingestion_reasoning,
        "provider_call_count": ingestion_reasoning["provider_call_count"],
        "external_l2_call_count": ingestion_reasoning["provider_call_count"],
        "primary_call_count": ingestion_reasoning["primary_call_count"],
        "repair_call_count": ingestion_reasoning["repair_call_count"],
        "escalation_call_count": ingestion_reasoning["escalation_call_count"],
        "judge_call_count": ingestion_reasoning["judge_call_count"],
        "challenger_call_count": ingestion_reasoning["challenger_call_count"],
        "learning_event_count": seal["event_count"],
        "learning_event_chain_digest": seal["event_chain_digest"],
        "semantic_cache": _build_semantic_cache_summary(
            semantic_cache, cache_observations, cache_writebacks
        ),
        "idempotent_replay": False,
    }
    write_json(run_dir / "intake_summary.json", summary)
    return summary


def _invalidate_superseded_in_cache(
    change_set: dict[str, Any] | None, semantic_cache: Any, cache_writebacks: list[dict[str, Any]]
) -> None:
    if not change_set or not isinstance(change_set.get("operations"), list):
        return
    superseded_ids = [
        str(op.get("target_id"))
        for op in change_set["operations"]
        if isinstance(op, dict) and op.get("operation") == "supersede_assertion" and op.get("target_id")
    ]
    if not superseded_ids:
        return
    if hasattr(semantic_cache, "invalidate_assertions"):
        semantic_cache.invalidate_assertions(superseded_ids)
    for sup_id in superseded_ids:
        cache_writebacks.append({
            "entry_id": None,
            "superseded_assertion_id": sup_id,
            "activation_status": "INVALIDATED",
            "activation_reason": "Superseded assertion invalidated in semantic cache.",
            "reason": "Superseded assertion invalidated in semantic cache.",
        })
