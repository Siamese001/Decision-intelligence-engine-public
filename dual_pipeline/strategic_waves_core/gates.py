"""Gate implementations for strategic reasoning shadow verification waves."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import read_json, sha256_file
from dual_pipeline.index import (
    render_retrieval_records,
    resolve_retrieval_mode,
    retrieval_metadata_for_record,
)
from dual_pipeline.normalization import normalize_markdown_source
from dual_pipeline.query import reason_over_packet, validate_answer
from dual_pipeline.strategic_ingestion import STRATEGIC_CANDIDATE_PROFILES_KEY
from dual_pipeline.strategic_reasoning import (
    STRATEGIC_PROFILE_QUALIFIER,
    create_strategic_evidence_artifact,
    create_strategic_reasoning_output,
    validate_strategic_profile,
)

from .specs import _fixture_packet, _write_generated


def _gate_wave_1(root: Path, output_root: Path, state: dict[str, Any]) -> str:
    fixture = read_json(
        root / "contracts" / "dual_pipeline" / "fixtures" / "valid" / "strategic-assertion-profile.json"
    )
    validate_artifact(fixture)
    invalid = {
        "knowledge_role": "target_outcome",
        "modality": "intended",
        "authority_scope": "official_project_intent",
        "applicability_scope": "nee_specific",
        "realization_state": "observed",
    }
    try:
        validate_strategic_profile(invalid)
    except ValueError:
        return "Closed strategic roles validate and intended outcomes cannot claim observation."
    raise AssertionError("intent-to-realization leakage was accepted")


def _gate_wave_2(root: Path, output_root: Path, state: dict[str, Any]) -> str:
    package = read_json(
        root / "tests" / "fixtures" / "sherpa_official_business_case_intake.json"
    )
    bundle = normalize_markdown_source(package, run_context="strategic-wave-receipt")
    profiles = bundle["extensions"].get(STRATEGIC_CANDIDATE_PROFILES_KEY, {})
    if len(profiles) != 3 or any(
        candidate["candidate_type"] != "assertion"
        for candidate in bundle["candidates"]
    ):
        raise AssertionError("Sherpa business case did not produce three atomic profiles")
    state["normalized_bundle"] = bundle
    path = _write_generated(output_root, "wave-02-normalized-sherpa-bundle.json", bundle)
    state.setdefault("generated_evidence", {})[2] = path
    return "Three exact Sherpa spans became profile-bound assertion candidates; no graph write ran."


def _gate_wave_3(root: Path, output_root: Path, state: dict[str, Any]) -> str:
    mode, policy = resolve_retrieval_mode(
        ["approved", "candidate", "external_public"],
        "strategic",
    )
    profile = {
        "knowledge_role": "official_intent",
        "modality": "intended",
        "authority_scope": "official_project_intent",
        "applicability_scope": "nee_specific",
        "realization_state": "intended",
    }
    metadata = retrieval_metadata_for_record(
        "assertion",
        "approved",
        strategic_profile=profile,
    )
    if (
        mode != "strategic"
        or metadata["answer_use"] != "strategic_context"
        or "strategic_context" not in policy["approved"]
        or "candidate_hypothesis" not in policy["candidate"]
    ):
        raise AssertionError("strategic retrieval policy is incomplete")
    return "Strategic mode retrieves role-qualified approved context and labeled candidates."


def _gate_wave_4(root: Path, output_root: Path, state: dict[str, Any]) -> str:
    packet = _fixture_packet(root)
    portfolio = packet["strategic_context"]
    required_roles = {
        "business_problem",
        "official_intent",
        "target_outcome",
        "design_principle",
        "option",
        "external_pattern",
    }
    if not required_roles <= set(portfolio["role_counts"]):
        raise AssertionError("Sherpa strategic portfolio is missing a required role")
    artifact = create_strategic_evidence_artifact(
        query_run_id="query-run-strategic-wave",
        graph_snapshot=packet["graph_snapshot"],
        source_packet_digest=packet["packet_digest"],
        portfolio=portfolio,
    )
    state["packet"] = packet
    state["strategic_evidence_artifact"] = artifact
    path = _write_generated(output_root, "wave-04-strategic-evidence-packet.json", artifact)
    state.setdefault("generated_evidence", {})[4] = path
    return "Evidence is separated into objectives, principles, options, external patterns, and gaps."


def _gate_wave_5(root: Path, output_root: Path, state: dict[str, Any]) -> str:
    packet = state.get("packet") or _fixture_packet(root)
    answer = reason_over_packet({"query_type": "opportunity_nba"}, packet)
    validation = validate_answer(answer, packet)
    if validation["status"] != "PASS" or not answer["next_best_actions"]:
        raise AssertionError(f"strategic answer failed validation: {validation}")
    artifact = create_strategic_reasoning_output(
        query_run_id="query-run-strategic-wave",
        packet=packet,
        answer=answer,
    )
    state["strategic_answer"] = answer
    state["strategic_output_artifact"] = artifact
    path = _write_generated(output_root, "wave-05-strategic-reasoning-output.json", artifact)
    state.setdefault("generated_evidence", {})[5] = path
    return "A citation-bound recommendation and next-best-action trace validates with zero graph mutation."


def _gate_wave_6(root: Path, output_root: Path, state: dict[str, Any]) -> str:
    packet = state.get("packet") or _fixture_packet(root)
    answer = dict(state.get("strategic_answer") or reason_over_packet(
        {"query_type": "opportunity_nba"}, packet
    ))
    target = next(
        item
        for item in packet["approved"]
        if item["record"]["strategic_profile"]["knowledge_role"] == "target_outcome"
    )
    invalid = dict(answer)
    invalid["current_state"] = [
        {
            "statement": target["record"]["retrieval_text"],
            "citations": [target["record"]["record_id"]],
            "evidence_ids": target["record"]["evidence_ids"],
            "lane": "approved",
        }
    ]
    invalid_result = validate_answer(invalid, packet)
    valid_result = validate_answer(answer, packet)
    evaluation = {
        "evaluation_id": "strategic-safety-evaluation-v1",
        "cases": [
            {
                "case_id": "sherpa-strategic-answer-valid",
                "status": valid_result["status"],
            },
            {
                "case_id": "intended-outcome-not-current-state",
                "status": "PASS" if invalid_result["status"] == "FAIL" else "FAIL",
            },
            {
                "case_id": "candidate-remains-hypothesis",
                "status": (
                    "PASS"
                    if answer["hypotheses"]
                    and answer["hypotheses"][0]["hypothesis"].startswith("CANDIDATE:")
                    else "FAIL"
                ),
            },
        ],
        "status": "PASS",
        "provider_call_count": 0,
        "graph_mutation_count": 0,
    }
    if any(case["status"] != "PASS" for case in evaluation["cases"]):
        evaluation["status"] = "FAIL"
        raise AssertionError(f"strategic evaluation failed: {evaluation}")
    path = _write_generated(output_root, "wave-06-strategic-evaluation.json", evaluation)
    state.setdefault("generated_evidence", {})[6] = path
    return "Golden cases pass, including rejection of intended outcome as current state."


def _gate_wave_7(root: Path, output_root: Path, state: dict[str, Any]) -> str:
    graph_path = Path(state["graph_path"])
    graph_hash = state["graph_sha256_before"]
    records = render_retrieval_records(
        graph_path,
        f"graph-snapshot-{graph_hash[:24]}",
    )
    from dual_pipeline.storage.governed_context import GovernedStorageContext
    connection = GovernedStorageContext.connect(graph_path, readonly=True)
    try:
        explicit_count = int(
            connection.execute(
                "SELECT count(*) FROM assertion WHERE qualifiers_json LIKE ?",
                (f"%{STRATEGIC_PROFILE_QUALIFIER}%",),
            ).fetchone()[0]
        )
    finally:
        connection.close()
    explicit_record_count = sum(
        isinstance(record.get("strategic_profile"), dict)
        for record in records
    )
    fallback_eligible_count = sum(
        record.get("record_type") != "entity_facet"
        and not isinstance(record.get("strategic_profile"), dict)
        for record in records
    )
    analysis = {
        "analysis_id": "strategic-profile-migration-analysis-v1",
        "graph_sha256": graph_hash,
        "eligible_retrieval_record_count": len(records),
        "profiled_retrieval_record_count": explicit_record_count,
        "explicit_graph_profile_count": explicit_count,
        "legacy_fallback_profile_count": fallback_eligible_count,
        "proposed_graph_operation_count": 0,
        "automatic_graph_mutation_count": 0,
        "approval_required_for_future_profile_writes": True,
        "recommendation": (
            "Prioritize exact-source strategic profile proposals for official intent, "
            "design principles, constraints, target outcomes, and external patterns."
        ),
    }
    path = _write_generated(output_root, "wave-07-migration-analysis.json", analysis)
    state.setdefault("generated_evidence", {})[7] = path
    return "The existing graph was profiled read-only and a zero-operation migration analysis was sealed."


def _gate_wave_8(root: Path, output_root: Path, state: dict[str, Any]) -> str:
    graph_path = Path(state["graph_path"])
    if sha256_file(graph_path) != state["graph_sha256_before"]:
        raise AssertionError("strategic wave implementation changed the graph")
    active_pair = state.get("active_pair_path")
    boundary = {
        "boundary_id": "strategic-release-boundary-v1",
        "graph_sha256": state["graph_sha256_before"],
        "active_pair_path": str(active_pair) if active_pair else None,
        "active_pair_sha256_before": state.get("active_pair_sha256_before"),
        "active_pair_sha256_after": (
            sha256_file(Path(active_pair))
            if active_pair and Path(active_pair).is_file()
            else None
        ),
        "graph_mutation_count": 0,
        "index_mutation_count": 0,
        "active_pair_mutation_count": 0,
        "activation_state": "READY_NOT_ACTIVATED",
        "separate_release_approval_required": True,
    }
    if boundary["active_pair_sha256_before"] != boundary["active_pair_sha256_after"]:
        raise AssertionError("active pair changed during the strategic wave run")
    path = _write_generated(output_root, "wave-08-release-boundary.json", boundary)
    state.setdefault("generated_evidence", {})[8] = path
    return "Implementation is ready for a separately approved release; no graph, index, or active-pair mutation occurred."


GATES: dict[int, Callable[[Path, Path, dict[str, Any]], str]] = {
    1: _gate_wave_1,
    2: _gate_wave_2,
    3: _gate_wave_3,
    4: _gate_wave_4,
    5: _gate_wave_5,
    6: _gate_wave_6,
    7: _gate_wave_7,
    8: _gate_wave_8,
}
