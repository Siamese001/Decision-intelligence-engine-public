"""Review execution flow for IntakeEngine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dual_pipeline.a2a_finalization import (
    VerifiedA2AFinalization,
    reverify_ingress_finalization_evidence,
)
from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import (
    assert_safe_mutable_path,
    connect_sqlite,
    digest,
    read_json,
    sha256_bytes,
    sha256_file,
    stable_id,
    write_json,
)
from dual_pipeline.intake_core.constants import (
    EXTERNAL_CONCEPT_OUTCOME,
    RETIRE_ASSERTION_KEY,
    SEMANTIC_CORRECTION_CANDIDATE_KEY,
    SUPERSEDES_ASSERTION_KEY,
)
from dual_pipeline.intake_core.hitl import (
    _candidate_governance,
    _validate_a2a_source_span_review_binding,
)
from dual_pipeline.intake_core.proposals import (
    _a2a_guidance_anchor,
    _candidate_resolution_map,
    _correlation,
    _external_concept_proposal,
    _is_a2a_source_bundle,
    _new_internal_entity_proposal,
    _semantic_correction_control,
)
from dual_pipeline.telemetry import RunRecorder


def _check_replay_decisions(
    run_dir: Path, decisions: list[dict[str, Any]], summary: dict[str, Any], review_items: dict[str, Any]
) -> dict[str, Any] | None:
    existing_decisions_path = run_dir / "hitl_decisions.json"
    if existing_decisions_path.is_file():
        existing_decisions = read_json(existing_decisions_path)
        hitl_meta = summary.get("hitl", {})
        if (
            digest(existing_decisions) == digest(decisions)
            and hitl_meta.get("decision_count") == len(decisions)
            and hitl_meta.get("decisions_digest") == digest(decisions)
            and hitl_meta.get("review_applied") is True
        ):
            return {**summary, "idempotent_replay": True}
    if review_items and not decisions:
        raise ValueError("HITL review requires at least one decision for pending review items")
    return None


def _verify_intake_event_and_a2a(
    engine: Any, run_dir: Path, summary: dict[str, Any]
) -> tuple[dict[str, Any], VerifiedA2AFinalization | None, Any]:
    intake_events = []
    for event_path in sorted((run_dir / "learning_events").glob("*.json")):
        event = read_json(event_path)
        validate_artifact(event)
        if event.get("event_type") == "intake_run":
            intake_events.append(event)
    if len(intake_events) != 1:
        raise RuntimeError("intake review requires one sealed source-package event")
    intake_payload = read_json(
        run_dir / "ledger_payloads" / intake_events[0]["payload_reference"].rsplit("/", 1)[-1]
    )
    keys = ("source_kind", "package_file_sha256", "a2a_finalization_binding", "a2a_finalization_reference", "intake_authorization")
    if (
        digest(intake_payload) != intake_events[0]["payload_digest"]
        or any(summary.get(k) != intake_payload.get(k) for k in keys)
        or summary.get("authorization") != intake_payload.get("intake_authorization")
    ):
        raise RuntimeError("intake review source binding differs from the sealed ledger")
    a2a_proof: VerifiedA2AFinalization | None = None
    a2a_ref = summary.get("a2a_finalization_reference")
    if summary.get("a2a_finalization_binding") is not None:
        if summary.get("source_kind") not in {"a2a_fact_vector_packet", "evidence_envelope"}:
            raise RuntimeError("non-A2A intake cannot carry a finalization receipt binding")
        a2a_proof = reverify_ingress_finalization_evidence(
            runtime_root=engine.runtime_root,
            reference=a2a_ref,
            expected_binding=summary["a2a_finalization_binding"],
        )
    return intake_payload, a2a_proof, a2a_ref


def _verify_production_review_policy(
    engine: Any,
    run_dir: Path,
    summary: dict[str, Any],
    intake_payload: dict[str, Any],
    decision_bytes: bytes,
    bundle: dict[str, Any],
) -> dict[str, Any] | None:
    p_flags = (summary.get("production_qualified"), intake_payload.get("production_qualified"))
    if any(not isinstance(v, bool) for v in p_flags) or p_flags[0] != p_flags[1]:
        raise RuntimeError("intake production policy bindings disagree")
    production_run = p_flags[0]
    curr_prod = bool(
        (engine.retriever and engine.retriever.backend.production_qualified)
        or (engine.active_pair_binding and engine.active_pair_binding.get("production_qualified"))
    )
    if curr_prod and not production_run:
        raise PermissionError("a non-production intake run cannot be reviewed in production")
    current_graph_hash = sha256_file(engine.graph_path)
    if current_graph_hash != summary["graph_sha256_before"]:
        raise RuntimeError("intake review is stale because the active graph changed after planning")
    if not production_run:
        return None
    if engine.authorization_context is None:
        raise PermissionError("production intake review requires an authenticated authorization context")
    engine.authorization_context.require("knowledge:intake")
    engine.authorization_context.require_resources({f"{summary['source_kind']}_decisions": sha256_bytes(decision_bytes)})
    production_binding = engine._production_binding(current_graph_hash)
    verified_payload = engine._validate_production_run(run_dir, summary, production_binding)
    if digest(verified_payload) != digest(intake_payload):
        raise RuntimeError("production intake event changed during review")
    engine._require_bundle_access(bundle)
    return production_binding


def _validate_candidate_correction_and_spans(
    bundle: dict[str, Any], decision: dict[str, Any]
) -> None:
    for cid in decision["affected_candidate_ids"]:
        _validate_a2a_source_span_review_binding(_candidate_governance(bundle, cid), decision)
        corr = _semantic_correction_control(bundle, cid)
        if not corr:
            continue
        ext_key = RETIRE_ASSERTION_KEY if corr["retire_only"] else SUPERSEDES_ASSERTION_KEY
        if decision.get("extensions", {}).get(ext_key) != corr["supersedes_assertion_id"]:
            raise ValueError("semantic correction HITL decision does not bind its exact prior assertion")
        if corr["retire_only"] and decision["decision"] != "REJECT":
            raise ValueError("retire-only semantic correction requires a REJECT decision")
        if not corr["retire_only"] and decision["decision"] not in {"CREATE_NEW", "SUPERSEDE"}:
            raise ValueError("semantic correction replacement requires CREATE_NEW or SUPERSEDE")


def _validate_guidance_anchors(
    engine: Any, bundle: dict[str, Any], decision: dict[str, Any], candidate_by_id: dict[str, dict[str, Any]]
) -> None:
    anchors = {
        _a2a_guidance_anchor(bundle, candidate_by_id[cid], decision)
        for cid in decision["affected_candidate_ids"]
    } - {None}
    if not anchors:
        return
    if len(anchors) != 1:
        raise ValueError("one HITL decision cannot bind multiple A2A guidance anchors")
    anchor = next(iter(anchors))
    conn = connect_sqlite(engine.graph_path, readonly=True)
    try:
        row = conn.execute("SELECT node_type,active FROM node WHERE node_id=?", (anchor,)).fetchone()
    finally:
        conn.close()
    if row is None or row["node_type"] != "pattern" or row["active"] != 1:
        raise ValueError("A2A guidance anchor must reference an active governed pattern node")


def _validate_external_concept_resolutions(
    bundle: dict[str, Any],
    decision: dict[str, Any],
    affected_res: list[dict[str, Any]],
    selected_entity: str | None,
) -> None:
    ext_res = [v for v in affected_res if v["outcome"] == EXTERNAL_CONCEPT_OUTCOME]
    if not ext_res:
        return
    if len(ext_res) != len(affected_res):
        raise ValueError("an external-concept decision cannot mix source-scoped and internal identities")
    if selected_entity:
        raise ValueError("source-scoped external concepts cannot select an internal governed entity")
    if decision["decision"] not in {EXTERNAL_CONCEPT_OUTCOME, "HOLD", "REJECT", "QUARANTINE"}:
        raise ValueError("external-concept review requires CREATE_EXTERNAL_CONCEPT, hold, reject, or quarantine")
    for r in ext_res:
        _external_concept_proposal(r, bundle)


def _validate_entity_and_concept_choices(
    bundle: dict[str, Any],
    decision: dict[str, Any],
    candidate_by_id: dict[str, dict[str, Any]],
    resolutions_by_candidate: dict[str, list[dict[str, Any]]],
) -> None:
    selected_entity = decision.get("extensions", {}).get("com.partner_advisory.nee.selected-entity-id")
    affected_res = [v for cid in decision["affected_candidate_ids"] for v in resolutions_by_candidate[cid]]
    if decision["decision"] == "CREATE_NEW":
        proposals = [
            _new_internal_entity_proposal(candidate=candidate_by_id[cid], resolutions=resolutions_by_candidate[cid], decision=decision)
            for cid in decision["affected_candidate_ids"]
        ]
        if len({digest(p) for p in proposals}) != 1:
            raise ValueError("one CREATE_NEW decision cannot bind multiple internal entity proposals")
    if _is_a2a_source_bundle(bundle) and decision["decision"] == "PROMOTE_APPROVED":
        raise ValueError("A2A source HITL may create only a candidate, never an approved fact")
    _validate_external_concept_resolutions(bundle, decision, affected_res, selected_entity)
    if decision["decision"] in {"CONFIRM", "PROMOTE_APPROVED"} and any(v["outcome"] == "HITL_CONFIRM_ENTITY" for v in affected_res) and not selected_entity:
        raise ValueError("identity confirmation requires a selected governed entity")
    if selected_entity:
        allowed = {alt["entity_id"] for cid in decision["affected_candidate_ids"] for r in resolutions_by_candidate[cid] for alt in r["alternatives"]}
        if selected_entity not in allowed:
            raise ValueError("selected HITL entity is not a governed resolution alternative")


def _validate_single_review_decision(
    engine: Any,
    run_id: str,
    bundle: dict[str, Any],
    decision: dict[str, Any],
    candidate_ids: set[str],
    candidate_by_id: dict[str, dict[str, Any]],
    review_items: dict[str, dict[str, Any]],
    resolutions_by_candidate: dict[str, list[dict[str, Any]]],
) -> None:
    validate_artifact(decision)
    if decision["run_id"] != run_id or decision["trace_id"] != bundle["trace_id"]:
        raise ValueError("HITL decision run/trace binding mismatch")
    unknown = set(decision["affected_candidate_ids"]) - candidate_ids
    if unknown:
        raise ValueError(f"HITL decision references unknown candidates: {sorted(unknown)}")
    item = review_items.get(decision["review_item_id"])
    if not item:
        raise ValueError("HITL decision does not bind to an emitted review item")
    if (
        decision["tier"] != item["tier"]
        or set(decision["affected_candidate_ids"]) != set(item["candidate_ids"])
        or decision["before_state_digest"] != item["before_state_digest"]
        or decision["proposed_after_state_digest"] != item["proposed_after_state_digest"]
        or not set(item["evidence_ids"]) <= set(decision["evidence_reviewed"])
    ):
        raise ValueError("HITL decision does not match its review-item evidence and state bindings")
    _validate_candidate_correction_and_spans(bundle, decision)
    _validate_guidance_anchors(engine, bundle, decision, candidate_by_id)
    _validate_entity_and_concept_choices(bundle, decision, candidate_by_id, resolutions_by_candidate)


def _record_review_telemetry(
    engine: Any,
    run_id: str,
    bundle: dict[str, Any],
    graph_hash_before: str,
    production_binding: dict[str, Any] | None,
    cache_observations: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> RunRecorder:
    if engine.active_pair_binding and engine.active_pair_binding.get("graph_sha256") == graph_hash_before:
        graph_snapshot = engine.active_pair_binding["graph_snapshot"]
    else:
        graph_snapshot = stable_id("graph-snapshot", graph_hash_before, length=24)
    recorder = RunRecorder(
        engine.runtime_root, run_id=run_id, trace_id=bundle["trace_id"], use_case="intake",
        graph_snapshot=graph_snapshot,
        model_or_index_generation=production_binding["model_or_index_generation"] if production_binding else None,
    )
    for obs in cache_observations:
        recorder.record(
            stage="L0", event_type="route_decision",
            payload={"semantic_cache_review": obs, "review_applied": True},
            correlation=_correlation(source_id=bundle["source_id"], candidate_id=obs["candidate_id"]),
            route="candidate_comparison_review",
        )
    auth_dig = engine.authorization_context.context_digest if engine.authorization_context else None
    for dec in decisions or [None]:
        recorder.record(
            stage="X2", event_type="hitl_decision" if dec else "pipeline_event",
            payload={"review_event_type": "hitl_decision_set", "decision": dec, "decisions_digest": digest(decisions), "authorization_context_digest": auth_dig},
            correlation=_correlation(
                source_id=bundle["source_id"],
                candidate_id=(dec["affected_candidate_ids"][0] if dec else None),
                hitl_item_id=dec["review_item_id"] if dec else None,
            ),
        )
    return recorder


def execute_review(engine: Any, run_id: str, decisions_path: Path) -> dict[str, Any]:
    run_dir = engine.runtime_root / "runs" / run_id
    assert_safe_mutable_path(engine.runtime_root, run_dir, label="intake review directory")
    summary = read_json(run_dir / "intake_summary.json")
    decision_bytes = decisions_path.resolve().read_bytes()
    intake_payload, a2a_finalization_proof, a2a_finalization_reference = _verify_intake_event_and_a2a(
        engine, run_dir, summary
    )
    bundle = read_json(run_dir / "candidate_bundle.json")
    production_binding = _verify_production_review_policy(
        engine, run_dir, summary, intake_payload, decision_bytes, bundle
    )
    routes = read_json(run_dir / "route_decisions.json")
    resolutions = read_json(run_dir / "entity_resolutions.json")
    comparison = read_json(run_dir / "candidate_comparison.json")
    semantic_cache, cache_bindings, _cached_comparisons, cache_observations = engine._prepare_semantic_cache(bundle)
    hitl = read_json(run_dir / "hitl_batches.json")
    review_items = {item["review_item_id"]: item for batch in hitl["review_batches"] for item in batch["items"]}
    resolutions_by_candidate = _candidate_resolution_map(bundle, resolutions)
    decision_value = json.loads(decision_bytes)
    decisions = decision_value if isinstance(decision_value, list) else decision_value.get("decisions", [])
    replay = _check_replay_decisions(run_dir, decisions, summary, review_items)
    if replay is not None:
        return replay
    candidate_ids = {c["candidate_id"] for c in bundle["candidates"]}
    candidate_by_id = {c["candidate_id"]: c for c in bundle["candidates"]}
    for decision in decisions:
        _validate_single_review_decision(
            engine, run_id, bundle, decision, candidate_ids, candidate_by_id, review_items, resolutions_by_candidate
        )
    correction_ids = set(bundle.get("extensions", {}).get(SEMANTIC_CORRECTION_CANDIDATE_KEY, {}).keys())
    decided_ids = {cid for dec in decisions for cid in dec["affected_candidate_ids"]}
    if correction_ids - decided_ids:
        raise ValueError("semantic correction requires an individual decision for every candidate")
    write_json(run_dir / "hitl_decisions.json", decisions)
    graph_hash_before = sha256_file(engine.graph_path)
    recorder = _record_review_telemetry(
        engine, run_id, bundle, graph_hash_before, production_binding, cache_observations, decisions
    )
    return engine._finish_dispositions(
        bundle=bundle, routes=routes, resolutions=resolutions, recorder=recorder, decisions=decisions,
        graph_hash_before=graph_hash_before, source_kind=summary["source_kind"],
        package_file_sha256=summary["package_file_sha256"],
        a2a_finalization_binding=summary.get("a2a_finalization_binding"),
        a2a_finalization_proof=a2a_finalization_proof,
        a2a_finalization_reference=a2a_finalization_reference,
        production_binding=production_binding,
        batch_admission=intake_payload.get("batch_admission"),
        intake_authorization=intake_payload.get("intake_authorization"),
        review_authorization=engine.authorization_context.audit_record() if engine.authorization_context else None,
        review_applied=True, semantic_cache=semantic_cache, cache_bindings=cache_bindings,
        cache_observations=cache_observations, comparison=comparison,
    )
