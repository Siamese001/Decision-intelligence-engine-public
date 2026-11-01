"""Provider-free front-door routing for governed query work.

The router decides where work belongs. It does not retrieve, embed, initialize
an external provider, authorize egress, or answer the request.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from .core import ROOT
from .l2 import canonical_query_type
from .query_router_core import (
    _INDEPENDENT_GENERAL_TARGET,
    _META_ROUTING_TOKEN,
    _META_WRAPPER,
    _NEGATED_CLAUSE,
    _NEGATED_PREPOSITIONAL_CLAUSE,
    _OUTER_INSTRUCTION_WRAPPERS,
    _QUOTED_OR_CODE,
    _REQUEST_TEXT_ENVELOPE,
    _STATUS_WRITE_REQUEST,
    _TOP_LEVEL_JOINER,
    ARTIFACT_TYPES,
    DELIVERY_PROFILES,
    EVIDENCE_QUERY_TYPES,
    LEXICON_RULE_KEYS,
    MAX_QUESTION_LENGTH,
    NEE_QUERY_TYPES,
    ROUTING_POLICY_PATH,
    DestinationDecision,
    QueryDestination,
    QueryRoutingPolicy,
    RoutingText,
    _compiled_lexicon_rule,
    _evidence_has_independent_material_action,
    _has_joined_semantic_and_handoff,
    _joined_independent_actions,
    _lexicon_match,
    _lexicon_matches,
    _load_routing_lexicon,
    _materiality,
    _query_type,
    _routing_text,
    _strict_keys,
    _unwrap_single_outer_instruction_wrapper,
    determine_query_project,
    load_query_routing_policy,
)
from .query_router_core import (
    route_query as _core_route_query,
)
from .query_router_core.temporal_gate import (
    extract_meeting_topic_tokens,
    extract_temporal_target_date,
    resolve_deliverable_aliases,
    scan_unpromoted_dlq_and_worktrees,
    verify_temporal_source_existence as _core_verify_temporal_source_existence,
)


import time

_GRAPHDB_CHECK_TTL_SECONDS = 5.0
_LAST_GRAPHDB_CHECK_TIME: float = 0.0
_LAST_GRAPHDB_CHECK_RESULT: bool = False


def _check_graphdb_available(force_refresh: bool = False) -> bool:
    global _LAST_GRAPHDB_CHECK_TIME, _LAST_GRAPHDB_CHECK_RESULT
    now = time.monotonic()
    if not force_refresh and (now - _LAST_GRAPHDB_CHECK_TIME < _GRAPHDB_CHECK_TTL_SECONDS):
        return _LAST_GRAPHDB_CHECK_RESULT
    try:
        from .runtime_root import resolve_active_graph_path

        graph_path = resolve_active_graph_path(enforce_integrity=True)
        if graph_path and graph_path.is_file():
            _LAST_GRAPHDB_CHECK_RESULT = True
            _LAST_GRAPHDB_CHECK_TIME = time.monotonic()
            return True
    except Exception:
        _LAST_GRAPHDB_CHECK_RESULT = False
        _LAST_GRAPHDB_CHECK_TIME = time.monotonic()
        return False
    from .runtime_evidence import discover_inventory
    inv = discover_inventory(ROOT)
    _LAST_GRAPHDB_CHECK_RESULT = any(p.get("parse_status") == "PASS" and p.get("generation_id") for p in inv.get("runtime_pairs", []))
    _LAST_GRAPHDB_CHECK_TIME = time.monotonic()
    return _LAST_GRAPHDB_CHECK_RESULT


def _clear_graphdb_available_cache() -> None:
    global _LAST_GRAPHDB_CHECK_TIME, _LAST_GRAPHDB_CHECK_RESULT
    _LAST_GRAPHDB_CHECK_TIME = 0.0
    _LAST_GRAPHDB_CHECK_RESULT = False


_check_graphdb_available.cache_clear = _clear_graphdb_available_cache  # type: ignore[attr-defined]


def _should_force_entity_advisory_override(
    inst_text: str, decision: DestinationDecision, policy: Any
) -> tuple[bool, bool]:
    if _lexicon_matches(policy, "general_decisive", inst_text):
        return False, False
    import re
    from .query_router_core.router import _has_canonical_entity_or_project

    advisory = bool(re.search(
        r"\b(?:executive\s+agenda|executive\s+question|leadership\s+question|stakeholder\s+prep|workstream\s+summary|talking[- ]points?|all[- ]hands)\b",
        inst_text, re.IGNORECASE,
    ))
    has_named = bool(_has_canonical_entity_or_project(inst_text))
    synthesis_override = bool(
        re.search(r"\b(?:draft|generate|compose|write|prepare|formulate|introduce|recommend|propose|send|share|staffing|incorporate|review\s+gaps|should\s+we\s+add)\b", inst_text, re.IGNORECASE)
        and has_named
        and decision.destination in {QueryDestination.GENERAL_ASSISTANT_HANDOFF, QueryDestination.ROUTE_AMBIGUOUS}
    )
    return advisory, synthesis_override


def route_query(*args: Any, **kwargs: Any) -> DestinationDecision:
    if not _check_graphdb_available():
        raise RuntimeError("GraphDB unavailable: runtime_evidence inventory check failed. Active generation and active pair graph bindings are required.")
    decision = _core_route_query(*args, **kwargs)
    if (
        kwargs.get("destination_override") is not None
        or "MULTI_DESTINATION_CONFLICT" in decision.reason_codes
        or "QUERY_NOT_AUTHORIZED" in decision.reason_codes
        or decision.eligibility_checks.get("authorized") == "FAIL"
    ):
        return decision
    q_text = str(args[0]) if args else str(kwargs.get("question", ""))
    inst_text = _routing_text(q_text).instruction if q_text else ""
    if not inst_text:
        return decision
    policy = kwargs.get("policy") or load_query_routing_policy()
    advisory_pattern, entity_synthesis_override = _should_force_entity_advisory_override(inst_text, decision, policy)
    if advisory_pattern or entity_synthesis_override:
        from dataclasses import replace

        inferred_qtype = _query_type(policy, inst_text, kwargs.get("query_type_override"))
        resolved_qtype = "cross_workstream_synthesis" if advisory_pattern or inferred_qtype not in NEE_QUERY_TYPES else inferred_qtype
        filtered_reasons = [
            rc for rc in decision.reason_codes
            if rc not in {"GENERAL_ASSISTANT_TASK", "NOT_GRAPH_ESSENTIAL", "BOUNDED_GRAPH_EVIDENCE", "L2_NOT_REQUIRED"}
        ]
        return replace(
            decision,
            destination=QueryDestination.NEE_DECISION_INTELLIGENCE,
            eligibility_status="ELIGIBLE",
            eligibility_checks={
                "material_outcome": "PASS", "graph_essential": "PASS",
                "multi_record_reasoning": "PASS", "l2_reasoning_required": "PASS",
                "authorized": decision.eligibility_checks.get("authorized", "PASS"),
            },
            query_type=resolved_qtype,
            materiality="high" if advisory_pattern else _materiality(policy, inst_text, kwargs.get("requested_materiality"), QueryDestination.NEE_DECISION_INTELLIGENCE),
            handoff_permitted=False, conflicting_destinations=(), resolution_options=(),
            reason_codes=tuple(dict.fromkeys(filtered_reasons + ["EXECUTIVE_SYNTHESIS_FORCED", "ENTITY_ADVISORY_TRAVERSAL_OVERRIDE"])),
        )
    return decision


def _validate_not_memory_conn(conn: Any) -> None:
    """Reject :memory: SQLite connections."""
    from dual_pipeline.query_core.firewall import GraphDBRequiredError

    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA database_list")
        for _, name, file in cursor.fetchall():
            if name == "main" and (not file or file == ":memory:"):
                raise GraphDBRequiredError(
                    "In-memory SQLite connections (:memory:) are strictly prohibited for executive briefings; "
                    "active GraphDB is required."
                )
    except GraphDBRequiredError:
        raise
    except Exception:
        pass


def _resolve_executive_graph_conn() -> tuple[Any, bool]:
    """Resolve active GraphDB connection for executive briefings."""
    from dual_pipeline.query_core.firewall import GraphDBRequiredError

    if not _check_graphdb_available():
        raise GraphDBRequiredError(
            "GraphDB unavailable: runtime_evidence inventory check failed. "
            "Active generation and active pair graph bindings are required for executive briefings; "
            "in-memory fallback is strictly prohibited."
        )
    from dual_pipeline.runtime_root import resolve_active_graph_path
    from storage.connection import create_sqlite_conn

    try:
        graph_file = resolve_active_graph_path(enforce_integrity=False)
        return create_sqlite_conn(graph_file, readonly=True), True
    except Exception as exc:
        raise GraphDBRequiredError(f"Active GraphDB connection could not be established: {exc}") from exc


def route_executive_briefing(
    stakeholder_name: str,
    conn: Any = None,
    suppress_l4_delivery_noise: bool = True,
) -> dict[str, Any]:
    """Route an executive query directly through Cross-BU White-Space & Tension Engine.

    Guarantees >=30% forward-looking strategic foresight and suppresses low-level L4 noise.
    """
    from .canonical_pipeline.cross_bu_whitespace_engine import CrossBUWhiteSpaceEngine

    close_after = False
    if conn is None:
        conn, close_after = _resolve_executive_graph_conn()
    else:
        _validate_not_memory_conn(conn)

    try:
        engine = CrossBUWhiteSpaceEngine(conn)
        return engine.generate_strategic_executive_briefing(
            stakeholder_name=stakeholder_name,
            suppress_l4_delivery_noise=suppress_l4_delivery_noise,
        )
    finally:
        if close_after and conn is not None:
            conn.close()


def verify_temporal_source_existence(
    question: str,
    conn: Any = None,
    reference_date: str | None = None,
    repo_root: Any = None,
    runtime_root: Any = None,
) -> dict[str, Any]:
    """Verify temporal meeting existence against active GraphDB, failing closed when absent."""
    close_after = False
    active_conn = conn
    if active_conn is None and extract_temporal_target_date(question, reference_date=reference_date) is not None:
        try:
            active_conn, close_after = _resolve_executive_graph_conn()
        except Exception:
            active_conn = None
    try:
        return _core_verify_temporal_source_existence(
            question,
            conn=active_conn,
            reference_date=reference_date,
            repo_root=repo_root,
            runtime_root=runtime_root,
        )
    finally:
        if close_after and active_conn is not None:
            active_conn.close()


__all__ = [
    "ARTIFACT_TYPES",
    "DELIVERY_PROFILES",
    "DestinationDecision",
    "EVIDENCE_QUERY_TYPES",
    "LEXICON_RULE_KEYS",
    "MAX_QUESTION_LENGTH",
    "NEE_QUERY_TYPES",
    "QueryDestination",
    "QueryRoutingPolicy",
    "ROUTING_POLICY_PATH",
    "RoutingText",
    "_INDEPENDENT_GENERAL_TARGET",
    "_META_ROUTING_TOKEN",
    "_META_WRAPPER",
    "_NEGATED_CLAUSE",
    "_NEGATED_PREPOSITIONAL_CLAUSE",
    "_OUTER_INSTRUCTION_WRAPPERS",
    "_QUOTED_OR_CODE",
    "_REQUEST_TEXT_ENVELOPE",
    "_STATUS_WRITE_REQUEST",
    "_TOP_LEVEL_JOINER",
    "_compiled_lexicon_rule",
    "_evidence_has_independent_material_action",
    "_has_joined_semantic_and_handoff",
    "_joined_independent_actions",
    "_lexicon_match",
    "_lexicon_matches",
    "_load_routing_lexicon",
    "_materiality",
    "_query_type",
    "_routing_text",
    "_strict_keys",
    "_unwrap_single_outer_instruction_wrapper",
    "canonical_query_type",
    "determine_query_project",
    "extract_meeting_topic_tokens",
    "extract_temporal_target_date",
    "load_query_routing_policy",
    "resolve_deliverable_aliases",
    "route_executive_briefing",
    "route_query",
    "scan_unpromoted_dlq_and_worktrees",
    "verify_temporal_source_existence",
]

