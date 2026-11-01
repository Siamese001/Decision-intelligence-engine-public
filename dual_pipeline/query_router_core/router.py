"""Deterministic query routing engine."""
from __future__ import annotations

from typing import Any, Tuple

from ..l2 import canonical_query_type
from .constants import (
    _STATUS_WRITE_REQUEST,
    ARTIFACT_TYPES,
    DELIVERY_PROFILES,
    EVIDENCE_QUERY_TYPES,
    MAX_QUESTION_LENGTH,
    NEE_QUERY_TYPES,
)
from .intent import (
    _evidence_has_independent_material_action,
    _has_joined_semantic_and_handoff,
    _joined_independent_actions,  # noqa: F401
    _materiality,
    _query_type,
)
from .models import DestinationDecision, QueryDestination, QueryRoutingPolicy
from .policy import _lexicon_matches, load_query_routing_policy
from .project_router import determine_query_project
from .text import _routing_text

_RouteResolution = Tuple[QueryDestination, bool, tuple[str, ...], tuple[str, ...]]
import threading

_GRAPH_TERMS_LOCK = threading.RLock()
_CACHED_GRAPH_TERMS: tuple[str, tuple[str, ...]] | None = None


def _load_active_graph_entity_terms() -> tuple[str, ...]:
    global _CACHED_GRAPH_TERMS
    try:
        from ..runtime_root import resolve_active_graph_path
        from storage.connection import create_sqlite_conn

        graph_path = resolve_active_graph_path(enforce_integrity=False)
        st = graph_path.stat()
        cache_key = f"{graph_path}:{st.st_mtime_ns}:{st.st_size}"
        with _GRAPH_TERMS_LOCK:
            if _CACHED_GRAPH_TERMS is not None and _CACHED_GRAPH_TERMS[0] == cache_key:
                return _CACHED_GRAPH_TERMS[1]
            conn = create_sqlite_conn(graph_path, readonly=True)
            try:
                terms = {
                    str(val or "").strip().lower()
                    for row in conn.execute("SELECT canonical_name, display_name FROM node WHERE active = 1")
                    for val in (row[0], row[1])
                    if len(str(val or "").strip()) >= 4
                }
                try:
                    for (al,) in conn.execute("SELECT display_alias FROM canonical_alias WHERE active = 1"):
                        if len(str(al or "").strip()) >= 4:
                            terms.add(str(al).strip().lower())
                except Exception:
                    pass
                sorted_terms = tuple(sorted(terms, key=len, reverse=True))
                _CACHED_GRAPH_TERMS = (cache_key, sorted_terms)
                return sorted_terms
            finally:
                conn.close()
    except Exception:
        return ()


def _has_canonical_entity_or_project(instruction: str) -> bool:
    from ..system_directory import find_systems_in_text
    if determine_query_project(instruction) is not None or find_systems_in_text(instruction):
        return True
    import re
    low = instruction.lower()
    return any(
        term in low and re.search(rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])", low)
        for term in _load_active_graph_entity_terms()
    )


def _validate_route_inputs(
    question: str, authorized: bool, delivery_profile: str | None, artifact_type: str | None, policy: QueryRoutingPolicy
) -> Tuple[Any, str, str]:
    if not isinstance(question, str):
        raise ValueError("question must be a string")
    if len(question) > MAX_QUESTION_LENGTH:
        raise ValueError(f"question must not exceed {MAX_QUESTION_LENGTH} characters")
    if not isinstance(authorized, bool):
        raise ValueError("authorized must be a boolean")
    routing_text = _routing_text(question)
    if not routing_text.normalized:
        raise ValueError("question is required")
    if not routing_text.instruction:
        raise ValueError("question must contain an instruction outside quoted or negated content")
    delivery, artifact = delivery_profile or policy.default_delivery_profile, artifact_type or policy.default_artifact_type
    if delivery not in DELIVERY_PROFILES:
        raise ValueError("unsupported delivery profile")
    if artifact not in ARTIFACT_TYPES:
        raise ValueError("unsupported artifact type")
    return routing_text, delivery, artifact


def _resolve_handoff_or_conflict(
    policy: QueryRoutingPolicy, lexicon_key: str, instruction: str, has_semantic: bool,
    fallback_dest: QueryDestination, handoff_dest: QueryDestination, option_label: str,
) -> _RouteResolution:
    if has_semantic and _has_joined_semantic_and_handoff(policy, lexicon_key, instruction):
        return (
            QueryDestination.ROUTE_AMBIGUOUS, True,
            (QueryDestination.NEE_DECISION_INTELLIGENCE.value, handoff_dest.value), ("decision", option_label),
        )
    return fallback_dest, False, (), ()


def _resolve_evidence_route(
    policy: QueryRoutingPolicy, instruction: str, general: bool, operational: bool, has_semantic: bool
) -> _RouteResolution:
    if not (general or operational or _evidence_has_independent_material_action(policy, instruction)):
        return QueryDestination.GRAPH_EVIDENCE_UTILITY, False, (), ()
    peer_dest = QueryDestination.NEE_DECISION_INTELLIGENCE.value if has_semantic else QueryDestination.GENERAL_ASSISTANT_HANDOFF.value
    return (
        QueryDestination.ROUTE_AMBIGUOUS, True,
        (QueryDestination.GRAPH_EVIDENCE_UTILITY.value, peer_dest), ("evidence", "decision" if has_semantic else "general"),
    )


def _is_multi_record_nee_decision(
    policy: QueryRoutingPolicy, instruction: str, material: bool, multi_record: bool, nee: bool, graph: bool = False
) -> bool:
    return bool(nee or (material and (multi_record or graph) and _query_type(policy, instruction, None) in NEE_QUERY_TYPES))


def _has_graph_routing_signal(
    policy: QueryRoutingPolicy, instruction: str, material: bool, bounded: bool, project_status: bool
) -> bool:
    if _lexicon_matches(policy, "graph", instruction):
        return True
    return (material or bounded or project_status) and _has_canonical_entity_or_project(instruction)


def _resolve_instruction_destination(policy: QueryRoutingPolicy, instruction: str) -> _RouteResolution:
    general = _lexicon_matches(policy, "general", instruction)
    general_decisive = _lexicon_matches(policy, "general_decisive", instruction)
    operational = _lexicon_matches(policy, "operational", instruction)
    bounded = _lexicon_matches(policy, "bounded", instruction)
    material = _lexicon_matches(policy, "material", instruction)
    multi_record = _lexicon_matches(policy, "multi_record", instruction)
    project_status = _lexicon_matches(policy, "project_status", instruction)
    graph = _has_graph_routing_signal(policy, instruction, material, bounded, project_status)
    semantic_status = project_status and material
    evidence = bounded and graph
    nee = graph and material and (multi_record or semantic_status)
    has_semantic = nee or (graph and material)

    if general and operational:
        return (
            QueryDestination.ROUTE_AMBIGUOUS, True,
            (QueryDestination.GENERAL_ASSISTANT_HANDOFF.value, QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF.value),
            ("general", "workflow"),
        )
    if general_decisive:
        return _resolve_handoff_or_conflict(
            policy, "general_decisive", instruction, has_semantic,
            QueryDestination.GENERAL_ASSISTANT_HANDOFF, QueryDestination.GENERAL_ASSISTANT_HANDOFF, "general",
        )
    if project_status and bool(_STATUS_WRITE_REQUEST.search(instruction)):
        return QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, False, (), ()
    if project_status and not semantic_status and not general and not operational:
        return QueryDestination.GRAPH_EVIDENCE_UTILITY, False, (), ()
    if evidence:
        return _resolve_evidence_route(policy, instruction, general, operational, has_semantic)
    if operational:
        return _resolve_handoff_or_conflict(
            policy, "operational", instruction, has_semantic,
            QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, "workflow",
        )
    if general:
        fallback = QueryDestination.NEE_DECISION_INTELLIGENCE if nee else QueryDestination.GENERAL_ASSISTANT_HANDOFF
        return _resolve_handoff_or_conflict(
            policy, "general", instruction, has_semantic, fallback, QueryDestination.GENERAL_ASSISTANT_HANDOFF, "general",
        )
    if _is_multi_record_nee_decision(policy, instruction, material, multi_record, nee, graph=graph):
        return QueryDestination.NEE_DECISION_INTELLIGENCE, False, (), ()

    from ..system_directory import find_systems_in_text
    if find_systems_in_text(instruction):
        return QueryDestination.GRAPH_EVIDENCE_UTILITY, False, (), ()
    return QueryDestination.ROUTE_AMBIGUOUS, False, (QueryDestination.ROUTE_AMBIGUOUS.value,), ("clarification_required",)


def _resolve_initial_destination(
    policy: QueryRoutingPolicy,
    instruction: str,
    destination_override: str | None,
    query_type_override: str | None,
) -> _RouteResolution:
    if destination_override is not None:
        if not policy.operator_override_allowed:
            raise ValueError("operator destination override is disabled")
        try:
            dest = QueryDestination(str(destination_override))
        except ValueError as exc:
            raise ValueError("unsupported destination override") from exc
        if dest is QueryDestination.ROUTE_AMBIGUOUS:
            raise ValueError("ROUTE_AMBIGUOUS cannot be selected as an operator override")
        return dest, False, (), ()
    if query_type_override is not None:
        selected_override = canonical_query_type(query_type_override)
        dest = (
            QueryDestination.GRAPH_EVIDENCE_UTILITY
            if selected_override in EVIDENCE_QUERY_TYPES
            else QueryDestination.NEE_DECISION_INTELLIGENCE
        )
        return dest, False, (), ()
    return _resolve_instruction_destination(policy, instruction)


def _set_all_eligibility_checks(checks: dict[str, str], value: str) -> None:
    checks.update(
        material_outcome=value,
        graph_essential=value,
        multi_record_reasoning=value,
        l2_reasoning_required=value,
    )


def _evaluate_nee_destination(
    selected: str | None,
    operator_override: bool,
    query_type_override: str | None,
    authorized: bool,
    checks: dict[str, str],
    reason_codes: list[str],
) -> Tuple[QueryDestination, str | None, str]:
    if selected in EVIDENCE_QUERY_TYPES and not operator_override:
        reason_codes.extend(("BOUNDED_GRAPH_EVIDENCE", "L2_NOT_REQUIRED"))
        return QueryDestination.GRAPH_EVIDENCE_UTILITY, selected, "NOT_APPLICABLE"
    if selected not in NEE_QUERY_TYPES:
        if operator_override or query_type_override is not None:
            raise ValueError("NEE decision intelligence requires a reasoning query type")
        _set_all_eligibility_checks(checks, "FAIL")
        reason_codes.append("INCOMPATIBLE_DECISION_INTENT")
        return QueryDestination.ROUTE_AMBIGUOUS, None, "AMBIGUOUS"
    _set_all_eligibility_checks(checks, "PASS")
    if not authorized:
        reason_codes.append("QUERY_NOT_AUTHORIZED")
        return QueryDestination.ROUTE_AMBIGUOUS, None, "AMBIGUOUS"
    reason_codes.append("MATERIAL_GRAPH_DECISION")
    return QueryDestination.NEE_DECISION_INTELLIGENCE, selected, "ELIGIBLE_PRE_RETRIEVAL"


def _evaluate_destination_eligibility(
    destination: QueryDestination,
    policy: QueryRoutingPolicy,
    instruction: str,
    query_type_override: str | None,
    operator_override: bool,
    authorized: bool,
    mixed_destination_conflict: bool,
    checks: dict[str, str],
    reason_codes: list[str],
) -> Tuple[QueryDestination, str | None, str, bool]:
    if destination in {
        QueryDestination.GENERAL_ASSISTANT_HANDOFF,
        QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF,
    }:
        if query_type_override is not None:
            raise ValueError("handoff destination is incompatible with a query-type override")
        _set_all_eligibility_checks(checks, "FAIL")
        task_code = (
            "GENERAL_ASSISTANT_TASK"
            if destination is QueryDestination.GENERAL_ASSISTANT_HANDOFF
            else "OPERATIONAL_WORKFLOW_TASK"
        )
        reason_codes.extend((task_code, "NOT_GRAPH_ESSENTIAL"))
        return destination, None, "NOT_ELIGIBLE", True

    if destination is QueryDestination.GRAPH_EVIDENCE_UTILITY:
        selected = _query_type(policy, instruction, query_type_override)
        if selected not in EVIDENCE_QUERY_TYPES:
            if operator_override or query_type_override is not None:
                raise ValueError("graph evidence utility requires a bounded query type")
            reason_codes.append("INCOMPATIBLE_GRAPH_EVIDENCE_INTENT")
            return QueryDestination.ROUTE_AMBIGUOUS, None, "AMBIGUOUS", False
        reason_codes.extend(("BOUNDED_GRAPH_EVIDENCE", "L2_NOT_REQUIRED"))
        if selected == "project_status_current":
            reason_codes.append("PROJECT_STATUS_SNAPSHOT_CURRENT")
        elif selected == "project_status_history":
            reason_codes.append("PROJECT_STATUS_SNAPSHOT_HISTORY")
        return destination, selected, "NOT_APPLICABLE", False

    if destination is QueryDestination.NEE_DECISION_INTELLIGENCE:
        selected = _query_type(policy, instruction, query_type_override)
        dest, qtype, status = _evaluate_nee_destination(
            selected, operator_override, query_type_override, authorized, checks, reason_codes
        )
        return dest, qtype, status, False

    _set_all_eligibility_checks(checks, "FAIL")
    reason_codes.append("MULTI_DESTINATION_CONFLICT" if mixed_destination_conflict else "ROUTE_AMBIGUOUS")
    return destination, None, "AMBIGUOUS", False


def _collect_initial_reason_codes(routing_text: Any) -> list[str]:
    codes: list[str] = []
    if routing_text.ignored_context:
        codes.append("NON_INSTRUCTION_CONTEXT_IGNORED")
    if routing_text.removed_negation:
        codes.append("NEGATED_ROUTING_SIGNAL_IGNORED")
    if routing_text.delegated_request:
        codes.append("EXPLICIT_REQUEST_ENVELOPE_UNWRAPPED")
    if routing_text.outer_wrapper_unwrapped:
        codes.append("OUTER_INSTRUCTION_WRAPPER_UNWRAPPED")
    return codes


def route_query(
    question: str,
    *,
    query_type_override: str | None = None,
    destination_override: str | None = None,
    requested_materiality: str | None = None,
    delivery_profile: str | None = None,
    artifact_type: str | None = None,
    authorized: bool = True,
    policy: QueryRoutingPolicy | None = None,
) -> DestinationDecision:
    policy = policy or load_query_routing_policy()
    routing_text, delivery, artifact = _validate_route_inputs(
        question, authorized, delivery_profile, artifact_type, policy
    )
    operator_override = destination_override is not None
    destination, mixed_conflict, conflicting_destinations, resolution_options = _resolve_initial_destination(
        policy, routing_text.instruction, destination_override, query_type_override
    )

    checks = {
        "material_outcome": "NOT_APPLICABLE",
        "graph_essential": "NOT_APPLICABLE",
        "multi_record_reasoning": "NOT_APPLICABLE",
        "l2_reasoning_required": "NOT_APPLICABLE",
        "authorized": "PASS" if authorized else "FAIL",
    }
    reason_codes = _collect_initial_reason_codes(routing_text)
    destination, query_type, eligibility_status, handoff = _evaluate_destination_eligibility(
        destination,
        policy,
        routing_text.instruction,
        query_type_override,
        operator_override,
        authorized,
        mixed_conflict,
        checks,
        reason_codes,
    )
    if destination is QueryDestination.ROUTE_AMBIGUOUS and not conflicting_destinations:
        conflicting_destinations = (destination.value,)
        resolution_options = ("clarification_required",)

    return DestinationDecision(
        destination=destination,
        eligibility_status=eligibility_status,
        eligibility_checks=checks,
        query_type=query_type,
        materiality=_materiality(policy, routing_text.instruction, requested_materiality, destination),
        delivery_profile=delivery,
        artifact_type=artifact,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        handoff_permitted=handoff,
        operator_override=operator_override,
        policy_digest=policy.policy_digest,
        conflicting_destinations=conflicting_destinations,
        resolution_options=resolution_options,
    )
