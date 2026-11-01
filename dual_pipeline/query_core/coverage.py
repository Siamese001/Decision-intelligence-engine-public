"""Query coverage manifest compilation, supplemental retrieval, and evidence reporting."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import digest, normalize_space, stable_id
from dual_pipeline.graph_retrieval import expand_connected_retrieval
from dual_pipeline.index import HybridRetriever
from dual_pipeline.l2 import canonical_query_type
from dual_pipeline.query_l1_controller import merge_retrieval_results
from dual_pipeline.query_router import route_query

from .constants import (
    _COVERAGE_CONCLUSION, _COVERAGE_OUTPUT_ONLY, MAX_L2_INPUT_RECORDS_PER_LANE,
    MAX_QUERY_COVERAGE_REQUIREMENTS, MAX_REQUIRED_MATERIAL_RECORDS,
    MAX_RETRIEVAL_EVIDENCE_REPORT_BYTES, _entities,
)
from .decision_coverage import (
    extract_frame_coverage_requirements, is_record_relevant_to_requirement,
    match_decision_support_obligation, rank_missing_gap_requirements,
)
from .hydration import build_evidence_packet, exact_retrieval, hydrate_results


def infer_query_type(question: str) -> str:
    """Return the provider-free destination router's governed query type."""

    decision = route_query(question)
    if decision.query_type is None:
        raise ValueError(
            f"query does not bind a graph query type: {decision.destination.value}"
        )
    return canonical_query_type(decision.query_type)


def _explicit_coverage_items(question: str) -> list[str]:
    """Return ordered material list items without treating output formatting as evidence."""

    values: list[str] = []
    for raw_line in question.splitlines():
        match = re.match(r"^\s*(?:\d{1,3}[.)]|[-*•])\s+(.+?)\s*$", raw_line)
        if not match:
            continue
        value = normalize_space(match.group(1))
        if value and not _COVERAGE_OUTPUT_ONLY.search(value):
            values.append(value)
    return list(dict.fromkeys(values))


def _named_coverage_subjects(
    question: str,
    plan: dict[str, Any],
    active: dict[str, Any] | None = None,
) -> list[str]:
    """Capture deterministic named subjects without using generic plan questions."""
    from .dynamic_entities import resolve_named_coverage_subjects

    return resolve_named_coverage_subjects(question, plan, active)


def _semantic_coverage_facets(question: str, plan: dict[str, Any]) -> list[str]:
    """Compile deterministic graph facets implied by the complete requested outcome."""

    query_type = plan["query_type"]
    facets_by_type = {
        "exact_lookup": ["Exact governed facts and source locators"],
        "current_state_corroboration": [
            "Current governed state and effective dates",
            "Historical, superseded, or excluded evidence",
        ],
        "project_status_current": [
            "Latest dated project-status snapshot and its as-of date",
            "Citation-only source evidence; no durable fact is asserted",
        ],
        "project_status_history": [
            "Dated project-status snapshots requested by the user",
            "Citation-only source evidence; no durable fact is asserted",
        ],
        "relationship_explanation": [
            "Governed relationship paths and direction",
            "Evidence supporting or contradicting each relationship",
        ],
        "cross_workstream_synthesis": [
            "Cross-workstream recurring patterns",
            "Material differences, constraints, and unresolved gaps",
        ],
        "gap_risk_inconsistency": [
            "Observed gaps, risks, and conflicts",
            "Counterevidence and evidence needed for resolution",
        ],
        "opportunity_nba": [
            "Business needs, opportunities, and expected outcomes",
            "Dependencies, constraints, risks, and prioritization rationale",
        ],
        "candidate_promotion_review": [
            "Candidate assertions and proposal-only posture",
            "Approved corroboration, contradiction, and unresolved validation",
        ],
    }
    facets = list(facets_by_type.get(query_type, []))
    normalized = question.casefold()
    if re.search(r"\ba2a\b|agent[- ]to[- ]agent", normalized):
        facets.extend(
            [
                "A2A protocols, communication patterns, and interaction boundaries",
                "Evidence-packet inputs, handoffs, and durable write-back responsibilities",
            ]
        )
    if re.search(r"\barchitecture\b|future[- ]state|target[- ]state", normalized):
        facets.extend(
            [
                "Architecture boundaries, component responsibilities, and shared services",
                "Identity, security, observability, state, and operational controls",
            ]
        )
    return list(dict.fromkeys(facets))


def _append_frame_requirements(
    plan: dict[str, Any], lanes: list[str], requirements: list[dict[str, Any]], seen_labels: set[str]
) -> None:
    for freq in extract_frame_coverage_requirements(plan, lanes):
        lbl_key = freq["requirement_label"].casefold()
        if lbl_key not in seen_labels:
            seen_labels.add(lbl_key)
            requirements.append({
                "requirement_id": freq["requirement_id"], "requirement_label": freq["requirement_label"],
                "requirement_kind": "policy_requirement", "allowed_lanes": freq["allowed_lanes"],
                "missing_evidence_disposition": freq["missing_evidence_disposition"], "materiality": freq["materiality"],
            })


def _fallback_facet_matches(
    kind: str, allowed: set[str], record_rows: dict[str, tuple[str, dict[str, Any]]], support_direction_fn: Any
) -> list[tuple[str, str]]:
    if kind != "graph_facet" or not record_rows:
        return []
    matches: list[tuple[str, str]] = []
    for record_id, (lane, row) in record_rows.items():
        if lane not in allowed:
            continue
        rec = row.get("record") if isinstance(row.get("record"), dict) else row
        if bool(row.get("evidence")) or bool(rec.get("evidence_ids")):
            matches.append((record_id, support_direction_fn(row)))
    return matches


def compile_query_coverage_manifest(
    *,
    question: str,
    validated_request_digest: str,
    plan: dict[str, Any],
    active: dict[str, Any],
    authorization_scope_digest: str,
) -> dict[str, Any]:
    """Compile replayable material retrieval requirements before any retrieval."""

    normalized = normalize_space(question)
    if not normalized:
        raise ValueError("QUERY_COVERAGE_REQUIREMENT_AMBIGUOUS: empty request")
    if not re.fullmatch(r"[a-f0-9]{64}", validated_request_digest):
        raise ValueError("QUERY_COVERAGE_PLAN_INCOMPLETE: invalid request digest")
    requirements: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    lanes = list(plan["allowed_lanes"])

    def add(label: str, kind: str, allowed_lanes: list[str] | None = None) -> None:
        label = normalize_space(label)
        label_key = label.casefold()
        if not label or label_key in seen_labels:
            return
        seen_labels.add(label_key)
        requirements.append({
            "requirement_id": stable_id("coverage-requirement", plan["run_id"], len(requirements) + 1, label, length=26),
            "requirement_label": label, "requirement_kind": kind, "allowed_lanes": allowed_lanes or lanes,
            "missing_evidence_disposition": "explicit_gap", "materiality": plan["materiality"],
        })

    for value in _explicit_coverage_items(question):
        kind = "comparison" if re.search(r"\b(?:vs\.?|versus|compare)\b", value, re.I) else "evidence"
        add(value, kind)
    for subject in _named_coverage_subjects(question, plan, active):
        add(subject, "named_subject")
    for requirement in plan.get("evidence_requirements", []):
        add(str(requirement), "policy_requirement")
    _append_frame_requirements(plan, lanes, requirements, seen_labels)
    for facet in _semantic_coverage_facets(question, plan):
        facet_lanes = (
            ["candidate"]
            if (plan["query_type"] == "candidate_promotion_review" and facet == "Candidate assertions and proposal-only posture")
            else None
        )
        add(facet, "graph_facet", facet_lanes)
    if _COVERAGE_CONCLUSION.search(normalized):
        add("Requested evidence-based conclusion", "conclusion")
    if plan["query_type"] == "candidate_promotion_review":
        add("Candidate assertions under review", "evidence", ["candidate"])
        add("Approved corroboration or contradiction", "evidence", ["approved"])
    if not requirements:
        raise ValueError("QUERY_COVERAGE_PLAN_INCOMPLETE: no material requirements")
    if len(requirements) > MAX_QUERY_COVERAGE_REQUIREMENTS:
        raise ValueError(f"QUERY_COVERAGE_REQUIREMENT_LIMIT_EXCEEDED: {len(requirements)} exceeds {MAX_QUERY_COVERAGE_REQUIREMENTS}")

    artifact_id = stable_id("query-coverage-manifest", plan["run_id"], length=26)
    return create_artifact(
        "query-coverage-manifest", artifact_id, manifest_id=artifact_id, run_id=plan["run_id"],
        validated_request_digest=validated_request_digest, query_plan_digest=plan["canonical_digest"]["value"],
        graph_snapshot=active["graph_snapshot"], graph_sha256=active["graph_sha256"],
        authorization_scope_digest=authorization_scope_digest, requirements=requirements,
    )


def _support_direction_for_row(row: dict[str, Any], inverse_safe: set[str]) -> str:
    path = row.get("graph_path") if isinstance(row.get("graph_path"), dict) else {}
    steps = path.get("steps") if isinstance(path.get("steps"), list) else []
    if not steps:
        return "direct"
    reverse_steps = [step for step in steps if step.get("traversal_direction") == "reverse"]
    if not reverse_steps:
        return "forward"
    if all(str(step.get("edge_type") or step.get("relationship_family")) in inverse_safe for step in reverse_steps):
        return "inverse_safe"
    return "reverse_only"


def _is_directional_requirement(requirement: dict[str, Any]) -> bool:
    if requirement["requirement_kind"] == "graph_facet":
        return False
    if requirement["requirement_kind"] in ("relationship", "named_subject"):
        return True
    return bool(re.search(
        r"\b(?:depends?|causes?|drives?|flows?|writes?|feeds?|owns?|routes?|direction|handoffs?|responsibilit)",
        requirement["requirement_label"], re.IGNORECASE,
    ))


def _stem_coverage_token(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("es") and not word.endswith(("ses", "xes", "zes")):
        return word[:-1]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _exclusion_reason_code(kind: str, label: str, allowed: set[str], selected: dict[str, list[str]]) -> str | None:
    if kind == "policy_requirement" and re.search(r"candidate material is excluded", label, re.I):
        return "CANDIDATE_LANE_EXCLUDED_BY_POLICY"
    if allowed == {"candidate"} and not selected["candidate"] and selected["approved"]:
        return "NO_CANDIDATE_RECORDS_IN_GENERATION"
    return None


def _row_text_for_coverage(row: dict[str, Any], packet: dict[str, Any]) -> str:
    record = row.get("record") if isinstance(row.get("record"), dict) else row
    graph_path = row.get("graph_path") if isinstance(row.get("graph_path"), dict) else {}
    is_dc = record.get("answer_use") == "decision_constraint"
    own_paths = [p for p in packet.get("graph_paths", []) if isinstance(p, Mapping) and p.get("record_id") == record.get("record_id")] if is_dc else []
    values = [
        record.get("title"), record.get("retrieval_text"), record.get("strategic_profile"), "decision_constraint" if is_dc else None,
        record.get("record_type"), graph_path.get("subject"), graph_path.get("predicate"), graph_path.get("steps"),
        graph_path.get("object"), graph_path.get("evidence_ids"), row.get("evidence"), own_paths,
    ]
    return json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)


def _is_l2_ordered_candidate(record_id: Any, record: Any, supported_lane: set[str], required_ids: list[str]) -> bool:
    if not isinstance(record_id, str) or record_id in required_ids:
        return False
    if record_id in supported_lane:
        return True
    return bool(supported_lane and isinstance(record, Mapping) and record.get("answer_use") == "decision_constraint")


def _is_eligible_material_row(
    row: Any,
    lane: str,
    outcome_record_ids: set[str],
    lane_l2_ids: set[str],
    existing_lane_ids: list[str],
) -> str | None:
    record = row.get("record") if isinstance(row, dict) and isinstance(row.get("record"), dict) else row
    record_id = record.get("record_id") if isinstance(record, dict) else None
    if not isinstance(record_id, str) or record_id not in outcome_record_ids or record_id not in lane_l2_ids or record_id in existing_lane_ids:
        return None
    if lane == "approved":
        meta_use = (row.get("retrieval_metadata") or {}).get("answer_use") if isinstance(row, dict) else None
        ans_use = meta_use or (record.get("answer_use") if isinstance(record, dict) else None) or "factual_answer"
        if ans_use != "factual_answer":
            return None
    return record_id


def build_retrieval_evidence_report(
    *,
    coverage_manifest: dict[str, Any],
    packet: dict[str, Any],
    authorization_scope_digest: str,
    graph_sha256: str,
    view_authorized: bool,
) -> dict[str, Any]:
    """Create the caller-visible, sealed projection from existing retrieval only."""

    selected = {
        lane: sorted({
            record["record_id"] for row in packet.get(lane, [])
            if isinstance(row, dict) and isinstance(record := (row.get("record") if isinstance(row.get("record"), dict) else row), dict) and isinstance(record.get("record_id"), str)
        })
        for lane in ("approved", "candidate", "external_public")
    }
    record_rows: dict[str, tuple[str, dict[str, Any]]] = {}
    for lane in ("approved", "candidate", "external_public"):
        for row in packet.get(lane, []):
            if not isinstance(row, dict):
                continue
            record = row.get("record") if isinstance(row.get("record"), dict) else row
            if isinstance(record, dict) and isinstance(record.get("record_id"), str):
                record_rows[record["record_id"]] = (lane, row)

    stopwords = {
        "a", "an", "and", "are", "as", "at", "be", "by", "each", "for", "from",
        "in", "is", "of", "on", "or", "the", "to", "under", "with", "within",
        "which", "what", "requested", "material", "governed",
    }
    synonym_groups = (
        {"a2a", "agent", "agents", "protocol", "protocols", "orchestration", "handoff", "handoffs", "communication"},
        {"architecture", "platform", "runtime", "component", "components", "service", "services", "boundary", "boundaries", "design"},
        {"writeback", "postgres", "postgresql", "state", "persistence", "durable", "store"},
        {"identity", "authentication", "authorization", "auth", "security", "cognito", "control", "controls"},
        {"evidence", "source", "sources", "citation", "citations", "locator", "locators", "provenance", "support", "corroboration", "contradiction", "validation"},
        {"risk", "risks", "gap", "gaps", "constraint", "constraints", "conflict", "conflicts", "issue", "issues", "unresolved", "blocker", "blockers", "pain", "impediment", "observed", "counterevidence", "resolution"},
        {"dependency", "dependencies", "depends", "requires", "prerequisite", "retains", "relationship", "relationships", "path", "paths", "direction"},
        {"opportunity", "opportunities", "nba", "strategic", "need", "needs", "stance", "frame", "options", "option", "evaluated", "augmentation", "partnership"},
        {"difference", "differences", "deviation", "variance", "divergence", "inconsistency", "recurring"},
        {"prioritization", "priority", "priorities", "rationale", "justification", "ranking", "tradeoff", "tradeoffs"},
        {"pattern", "patterns", "practice", "practices", "approach", "standard", "standards"},
        {"workstream", "workstreams", "capability", "capabilities", "initiative", "initiatives", "domain", "business", "project", "program"},
        {"outcome", "outcomes", "value", "benefit", "benefits", "decision", "decisions", "recommendation", "recommendations", "expected"},
        {"current", "effective", "date", "dates", "dated", "snapshot", "snapshots", "latest", "status", "history", "historical", "superseded"},
        {"candidate", "proposal", "posture", "review", "proposed"},
    )

    def tokens(value: Any) -> set[str]:
        raw = set(re.findall(r"[a-z0-9]+", str(value).casefold())) - stopwords
        expanded = set(raw) | {_stem_coverage_token(w) for w in raw}
        for group in synonym_groups:
            if expanded & group:
                expanded.update(group)
        return expanded

    row_text = lambda row: _row_text_for_coverage(row, packet)  # noqa: E731
    inverse_safe = {"RELATED_TO", "CONTRADICTS", "ALIGNED_WITH", "SIMILAR_TO", "SHARES_CAPABILITY_DOMAIN"}
    support_direction = lambda row: _support_direction_for_row(row, inverse_safe)  # noqa: E731
    directional = _is_directional_requirement

    counterevidence_ids = {
        value.get("record_id")
        for value in packet.get("counterevidence", [])
        if isinstance(value, dict) and isinstance(value.get("record_id"), str)
    }
    outcomes: list[dict[str, Any]] = []
    used_record_ids: set[str] = set()
    for requirement in coverage_manifest["requirements"]:
        label = requirement["requirement_label"]
        kind = requirement["requirement_kind"]
        allowed = set(requirement["allowed_lanes"])
        available_lanes = set(packet.get("allowed_lanes", selected))
        if not allowed & available_lanes:
            outcomes.append({"requirement_id": requirement["requirement_id"], "outcome": "unauthorized", "record_ids": [], "reason_code": "RETRIEVAL_LANE_NOT_AUTHORIZED", "support_directions": []})
            continue
        if rc := _exclusion_reason_code(kind, label, allowed, selected):
            outcomes.append({"requirement_id": requirement["requirement_id"], "outcome": "excluded", "record_ids": [], "reason_code": rc, "support_directions": ["not_applicable"]})
            continue
        requirement_tokens = tokens(label)
        matches: list[tuple[str, str]] = []
        reverse_match = False
        for record_id, (lane, row) in record_rows.items():
            if lane not in allowed:
                continue
            record_tokens = tokens(row_text(row))
            relevant, explicit_direction = is_record_relevant_to_requirement(
                kind, label, lane, row, requirement, requirement_tokens, record_tokens
            )
            direction = explicit_direction or support_direction(row)
            if relevant and directional(requirement) and direction == "reverse_only":
                reverse_match = True
                continue
            if relevant:
                matches.append((record_id, direction))
        if not matches:
            matches = _fallback_facet_matches(kind, allowed, record_rows, support_direction)
        matched_ids = sorted({record_id for record_id, _ in matches})
        directions = sorted({direction for _, direction in matches})
        if matched_ids:
            outcome, reason_code = "covered", None
            used_record_ids.update(matched_ids)
        else:
            outcome = "explicit_gap"
            reason_code = "REVERSE_ONLY_DIRECTIONAL_SUPPORT" if directional(requirement) and reverse_match else "RETRIEVAL_COVERAGE_MISSING"
            directions = ["reverse_only"] if reverse_match else []
        outcomes.append({
            "requirement_id": requirement["requirement_id"], "outcome": outcome,
            "record_ids": matched_ids, "reason_code": reason_code, "support_directions": directions,
        })
    l2_input = {lane: sorted(record_id for record_id in ids if record_id in used_record_ids) for lane, ids in selected.items()}
    if any(row.get("reason_code") == "CANDIDATE_LANE_EXCLUDED_BY_POLICY" for row in outcomes) or "candidate" not in available_lanes:
        l2_input["candidate"] = []
    if any(row["outcome"] not in {"covered", "excluded"} for row in outcomes):
        l2_input = {lane: [] for lane in l2_input}

    material_requirements = [
        requirement for requirement in coverage_manifest["requirements"]
        if requirement["requirement_kind"] in {"comparison", "evidence", "relationship", "graph_facet"}
    ]
    required_material = {"approved": [], "candidate": [], "external_public": []}
    l2_ids_by_lane = {lane: set(values) for lane, values in l2_input.items()}
    selected_requirement_ids = {requirement["requirement_id"] for requirement in material_requirements}
    for requirement, outcome in zip(coverage_manifest["requirements"], outcomes, strict=False):
        if requirement["requirement_id"] not in selected_requirement_ids or outcome["outcome"] != "covered":
            continue
        outcome_record_ids = set(outcome["record_ids"])
        allowed_lanes = set(requirement["allowed_lanes"])
        for lane in ("approved", "candidate", "external_public"):
            if lane not in allowed_lanes:
                continue
            for row in packet.get(lane, []):
                record_id = _is_eligible_material_row(row, lane, outcome_record_ids, l2_ids_by_lane[lane], required_material[lane])
                if record_id is None:
                    continue
                required_material[lane].append(record_id)
                if sum(len(values) for values in required_material.values()) >= MAX_REQUIRED_MATERIAL_RECORDS:
                    break
            if sum(len(values) for values in required_material.values()) >= MAX_REQUIRED_MATERIAL_RECORDS:
                break
        if sum(len(values) for values in required_material.values()) >= MAX_REQUIRED_MATERIAL_RECORDS:
            break

    artifact_id = stable_id("retrieval-evidence-report", packet["graph_snapshot"], coverage_manifest["run_id"], length=26)
    report = create_artifact(
        "retrieval-evidence-report", artifact_id, report_id=artifact_id, run_id=coverage_manifest["run_id"],
        validated_request_digest=coverage_manifest["validated_request_digest"], query_plan_digest=coverage_manifest["query_plan_digest"],
        coverage_manifest_digest=coverage_manifest["canonical_digest"]["value"], graph_snapshot=packet["graph_snapshot"],
        graph_sha256=graph_sha256, authorization_scope_digest=authorization_scope_digest, evidence_packet_digest=packet["packet_digest"],
        selected_record_ids_by_lane=selected, l2_input_record_ids_by_lane=l2_input, required_material_record_ids_by_lane=required_material,
        requirement_outcomes=outcomes, view_authorized=view_authorized,
    )
    report_size = len(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if report_size > MAX_RETRIEVAL_EVIDENCE_REPORT_BYTES:
        raise ValueError(f"RETRIEVAL_EVIDENCE_REPORT_SIZE_LIMIT_EXCEEDED: {report_size} exceeds {MAX_RETRIEVAL_EVIDENCE_REPORT_BYTES}")
    return report


def coverage_complete(report: dict[str, Any]) -> bool:
    """Return whether every material requirement has an acceptable disposition."""

    return all(
        row["outcome"] in {"covered", "excluded"}
        for row in report["requirement_outcomes"]
    )


def coverage_supplemental_retrieval(
    *,
    retrieval_evidence_report: dict[str, Any],
    coverage_manifest: dict[str, Any],
    retriever: "HybridRetriever",
    retrieval: dict[str, Any],
    question: str,
    graph_path: Path,
    sidecar_path: Path,
    run_id: str,
    graph_snapshot: str,
    query_plan_digest: str,
    requested_hops: int,
    relationships_to_inspect: list[str],
    classification: str,
    audiences: list[str],
    retrieval_mode: str,
    allowed_assertion_ids: Iterable[str] | None,
    exact: dict[str, Any],
    card_scope: dict[str, Any] | None,
    active: dict[str, Any],
    authorization_scope_digest: str,
    max_supplemental_queries: int = 5,
    comparison_mode: bool = False,
    request_context: Any | None = None,
) -> tuple[
    dict[str, Any] | None, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], int,
]:
    """Single-pass coverage-gap supplemental retrieval with bounded recovery."""
    if request_context is not None and getattr(request_context, "is_expired", lambda: False)():
        return None, {}, {}, {}, {}, 0

    gap_requirements = [
        row
        for row in retrieval_evidence_report["requirement_outcomes"]
        if row["outcome"] == "explicit_gap"
        and row.get("reason_code") == "RETRIEVAL_COVERAGE_MISSING"
    ]
    if not gap_requirements:
        return None, {}, {}, {}, {}, 0

    requirement_by_id = {
        req["requirement_id"]: req for req in coverage_manifest["requirements"]
    }
    sorted_gaps = rank_missing_gap_requirements(gap_requirements, requirement_by_id)
    allowed_lanes = list(retrieval["allowed_lanes"])
    supplemental_queries: list[str] = []
    for gap in sorted_gaps[:max_supplemental_queries]:
        req = requirement_by_id.get(gap["requirement_id"])
        if req is not None:
            label = req["requirement_label"]
            kind = req.get("requirement_kind", "")
            if kind in ("policy_requirement", "graph_facet") and question:
                supplemental_queries.append(f"{question} - {label}")
            else:
                supplemental_queries.append(label)

    if not supplemental_queries:
        return None, {}, {}, {}, {}, 0

    # Run supplemental retrieval for each gap query and collect records
    supplemental_retrievals: list[dict[str, Any]] = []
    for label_query in supplemental_queries:
        supp_bm25_ranks_by_lane = None
        if exact.get("bm25_executed"):
            supp_exact = exact_retrieval(
                label_query,
                graph_path,
                classification=classification,
                audiences=audiences,
                retrieval_mode=retrieval_mode,
                allowed_assertion_ids=list(allowed_assertion_ids) if allowed_assertion_ids is not None else None,
            )
            supp_bm25_ranks_by_lane = supp_exact.get("bm25_ranks_by_lane")

        supplemental = retriever.retrieve(
            label_query,
            allowed_lanes,
            classification=classification,
            audiences=audiences,
            retrieval_mode=retrieval_mode,
            allowed_assertion_ids=allowed_assertion_ids,
            bm25_assertion_ranks_by_lane=supp_bm25_ranks_by_lane,
        )
        supplemental_retrievals.append(supplemental)

    # Merge supplemental results into the initial retrieval
    augmented_retrieval = merge_retrieval_results(
        initial_retrieval=retrieval,
        action_retrievals=supplemental_retrievals,
        allowed_lanes=allowed_lanes,
        max_records=60,
    )

    # Re-run the full pipeline: expand → hydrate → packet → report
    aug_graph_retrieval, aug_connected_evidence_map = expand_connected_retrieval(
        retrieval=augmented_retrieval, question=question, graph_path=graph_path, sidecar_path=sidecar_path,
        run_id=run_id, graph_snapshot=graph_snapshot, query_plan_digest=query_plan_digest, requested_hops=requested_hops,
        relationships_to_inspect=relationships_to_inspect, classification=classification, audiences=audiences,
        retrieval_mode=retrieval_mode, allowed_assertion_ids=allowed_assertion_ids,
    )
    aug_hydrated = hydrate_results(
        aug_graph_retrieval, graph_path, allowed_assertion_ids=allowed_assertion_ids, connected_evidence_map=aug_connected_evidence_map,
    )
    aug_packet = build_evidence_packet(
        hydrated=aug_hydrated, graph_snapshot=graph_snapshot, exact=exact, retrieval_mode=retrieval_mode,
        card_scope=card_scope, connected_evidence_map=aug_connected_evidence_map,
    )
    aug_report = build_retrieval_evidence_report(
        coverage_manifest=coverage_manifest, packet=aug_packet, authorization_scope_digest=authorization_scope_digest,
        graph_sha256=active["graph_sha256"], view_authorized=True,
    )

    original_gap_count = len(gap_requirements)
    augmented_gap_count = sum(
        1 for row in aug_report["requirement_outcomes"]
        if row["outcome"] == "explicit_gap" and row.get("reason_code") == "RETRIEVAL_COVERAGE_MISSING"
    )
    if comparison_mode or augmented_gap_count >= original_gap_count:
        return None, {}, {}, {}, {}, len(supplemental_queries)
    return aug_report, aug_packet, aug_graph_retrieval, aug_connected_evidence_map, aug_hydrated, len(supplemental_queries)


def filter_packet_for_l2(packet: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """Return the exact requirement-supported evidence subset visible to L2."""
    filtered = deepcopy(packet)
    supported_ids = {lane: set(ids) for lane, ids in report["l2_input_record_ids_by_lane"].items()}
    required_material = report.get("required_material_record_ids_by_lane", {})
    bounded_ids: dict[str, list[str]] = {}
    for lane in ("approved", "candidate", "external_public"):
        required_ids = required_material.get(lane, []) if isinstance(required_material, Mapping) else []
        required_ids = [record_id for record_id in required_ids if isinstance(record_id, str) and record_id in supported_ids[lane]]
        ordered_ids: list[str] = []
        for row in packet.get(lane, []):
            record = row.get("record") if isinstance(row, Mapping) and isinstance(row.get("record"), Mapping) else row
            record_id = record.get("record_id") if isinstance(record, Mapping) else None
            if _is_l2_ordered_candidate(record_id, record, supported_ids[lane], required_ids):
                ordered_ids.append(record_id)
        bounded_ids[lane] = list(dict.fromkeys(required_ids + ordered_ids))[:MAX_L2_INPUT_RECORDS_PER_LANE]
    if any(row.get("reason_code") == "CANDIDATE_LANE_EXCLUDED_BY_POLICY" for row in report.get("requirement_outcomes", []) if isinstance(row, Mapping)):
        bounded_ids["candidate"] = []
    allowed_ids = {lane: set(ids) for lane, ids in bounded_ids.items()}
    for lane in ("approved", "candidate", "external_public"):
        filtered[lane] = [
            row for row in packet.get(lane, [])
            if ((row.get("record") if isinstance(row, Mapping) and isinstance(row.get("record"), Mapping) else row).get("record_id") in allowed_ids[lane])
        ]
    all_ids = set().union(*allowed_ids.values())
    filtered["graph_paths"] = [row for row in packet.get("graph_paths", []) if row.get("record_id") in all_ids]
    filtered["counterevidence"] = [row for row in packet.get("counterevidence", []) if row.get("record_id") in all_ids]
    filtered["required_material_record_ids_by_lane"] = (
        {lane: [record_id for record_id in required_material.get(lane, []) if record_id in allowed_ids[lane]] for lane in ("approved", "candidate", "external_public")}
        if isinstance(required_material, Mapping)
        else {"approved": [], "candidate": [], "external_public": []}
    )
    filtered["packet_digest"] = digest({key: value for key, value in filtered.items() if key != "packet_digest"})
    return filtered


def render_retrieval_evidence_markdown(
    coverage_manifest: dict[str, Any], report: dict[str, Any]
) -> str:
    outcomes = {row["requirement_id"]: row for row in report["requirement_outcomes"]}
    lines = [
        "# Retrieval evidence", "",
        "| Requirement | Outcome | Records | Direction | Reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    for requirement in coverage_manifest["requirements"]:
        outcome = outcomes[requirement["requirement_id"]]
        records = ", ".join(outcome["record_ids"]) or "None"
        directions = ", ".join(outcome["support_directions"]) or "None"
        reason = outcome["reason_code"] or "None"
        lines.append(
            f"| {requirement['requirement_label']} | {outcome['outcome']} | "
            f"{records} | {directions} | {reason} |"
        )
    return "\n".join(lines) + "\n"


def build_claim_evidence_map(*, answer: dict[str, Any] | None, report: dict[str, Any], coverage_manifest: dict[str, Any]) -> dict[str, Any]:
    record_lanes = {
        record_id: lane
        for lane, rows in report["selected_record_ids_by_lane"].items()
        for record_id in rows
    }
    citations: list[list[str]] = []
    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                # Citation-only source excerpts are evidence attachments, not
                # answer claims.  They are validated directly by X1 but must
                # not be represented as factual or candidate-hypothesis
                # claims in the sealed claim/evidence map.
                if key == "source_evidence":
                    continue
                if key in {"citations", "basis_citations"} and isinstance(item, list):
                    citations.append([str(record_id) for record_id in item])
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(answer or {})
    claims = []
    for ordinal, cited in enumerate(citations, 1):
        evidence = [
            {"record_id": record_id, "lane": record_lanes[record_id], "answer_use": "factual_answer" if record_lanes[record_id] == "approved" else "candidate_hypothesis", "traversal_direction": "not_applicable"}
            for record_id in cited if record_id in record_lanes
        ]
        claims.append({"claim_id": stable_id("answer-claim", report["run_id"], ordinal, length=26), "claim_kind": ("factual" if all(row["lane"] == "approved" for row in evidence) else "candidate_hypothesis") if evidence else "gap", "materiality": "high", "status": "grounded" if evidence else "explicit_gap", "directional_claim": False, "evidence": evidence})
    artifact_id = stable_id("claim-evidence-map", report["run_id"], length=26)
    return create_artifact("claim-evidence-map", artifact_id, map_id=artifact_id, run_id=report["run_id"], validated_request_digest=report["validated_request_digest"], query_plan_digest=report["query_plan_digest"], coverage_manifest_digest=coverage_manifest["canonical_digest"]["value"], evidence_report_digest=report["canonical_digest"]["value"], evidence_packet_digest=report["evidence_packet_digest"], graph_snapshot=report["graph_snapshot"], graph_sha256=report["graph_sha256"], authorization_scope_digest=report["authorization_scope_digest"], answer_digest=digest(answer or {}), claims=claims)


__all__ = [
    "_entities",
    "_explicit_coverage_items",
    "_named_coverage_subjects",
    "_semantic_coverage_facets",
    "build_claim_evidence_map",
    "build_retrieval_evidence_report",
    "compile_query_coverage_manifest",
    "coverage_complete",
    "coverage_supplemental_retrieval",
    "filter_packet_for_l2",
    "infer_query_type",
    "render_retrieval_evidence_markdown",
]


