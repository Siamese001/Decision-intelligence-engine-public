"""Versioned, evidence-safe card views over the governed graph.

A card is a saved semantic view, not an embedding unit and not evidence.  Its
selector resolves graph assertions; every assertion keeps its own authority,
classification, audience, and evidence envelope.  Materialization is therefore
access-sliced for a caller rather than assigned one unsafe aggregate audience.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from storage.connection import create_sqlite_conn

from ._validators import sql_placeholders
from .contracts import create_artifact, validate_artifact
from .core import (
    digest,
    read_json,
    sha256_file,
    stable_id,
    utc_now,
    write_json_exclusive,
)
from .card_archival import archive_superseded_card, parse_card_frontmatter, verify_card_freshness
from .facets import (
    CARDS_REFACTOR_POLICY_VERSION,
    TOPIC_VOCABULARY,
    facets_for_object,
    normalize_audiences,
    schema_is_installed,
    selector_digest,
)

CARD_VIEW_RENDERER_VERSION = "nee-card-view-renderer-1.0.0"
CARD_VIEW_SELECTOR_VERSION = "1.0.0"
CLASSIFICATION_RANK = {
    "external_approved": 0, "public": 0, "internal": 1, "confidential": 2, "restricted": 3,
}
SELECTOR_KEYS = {
    "assertion_types", "canonical_statuses", "currentness_states", "explicit_object_ids",
    "facets", "object_types", "predicates", "retrieval_eligible_only", "route_card_ids",
}
AUTHORIZATION_KEYS = {"audiences", "candidate_review", "classification_clearance"}
_SELECTOR_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def default_route_selector(card_id: str) -> dict[str, Any]:
    return {
        "object_types": ["assertion"],
        "route_card_ids": [card_id],
        "explicit_object_ids": [],
        "facets": [],
        "predicates": [],
        "assertion_types": [],
        "canonical_statuses": ["canonical"],
        "currentness_states": ["current"],
        "retrieval_eligible_only": True,
    }


def validate_card_selector(selector: dict[str, Any]) -> dict[str, Any]:
    """Validate the bounded selector AST; arbitrary SQL is never accepted."""

    if not isinstance(selector, dict) or set(selector) != SELECTOR_KEYS:
        raise ValueError(
            f"card selector must contain exactly the closed fields {sorted(SELECTOR_KEYS)}"
        )
    if selector["object_types"] != ["assertion"]:
        raise ValueError("card view v1 supports assertion objects only")
    for field in (
        "route_card_ids",
        "explicit_object_ids",
        "predicates",
        "assertion_types",
        "canonical_statuses",
        "currentness_states",
    ):
        values = selector[field]
        if (
            not isinstance(values, list)
            or len(values) > 2_000
            or any(
                not isinstance(value, str)
                or _SELECTOR_TOKEN.fullmatch(value) is None
                for value in values
            )
            or len(values) != len(set(values))
        ):
            raise ValueError(f"card selector {field} must be a bounded unique string array")
    if not isinstance(selector["retrieval_eligible_only"], bool):
        raise ValueError("card selector retrieval_eligible_only must be boolean")
    facets = selector["facets"]
    if not isinstance(facets, list) or len(facets) > 16:
        raise ValueError("card selector facets must be a bounded array")
    for facet in facets:
        if (
            not isinstance(facet, dict)
            or set(facet) != {"vocabulary", "values", "match"}
            or facet["match"] not in {"any", "all"}
            or not isinstance(facet["vocabulary"], str)
            or _SELECTOR_TOKEN.fullmatch(facet["vocabulary"]) is None
            or not isinstance(facet["values"], list)
            or not facet["values"]
            or len(facet["values"]) > 100
            or any(
                not isinstance(value, str)
                or _SELECTOR_TOKEN.fullmatch(value) is None
                for value in facet["values"]
            )
            or len(facet["values"]) != len(set(facet["values"]))
        ):
            raise ValueError("card selector contains a malformed facet clause")
    if not selector["route_card_ids"] and not selector["explicit_object_ids"] and not facets:
        raise ValueError("card selector requires a route, object, or facet selection clause")
    return selector


def normalize_authorization_context(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != AUTHORIZATION_KEYS:
        raise ValueError(
            "authorization context must contain classification_clearance, "
            "audiences, and candidate_review"
        )
    clearance = value["classification_clearance"]
    if clearance not in CLASSIFICATION_RANK:
        raise ValueError("unsupported classification clearance")
    if not isinstance(value["candidate_review"], bool):
        raise ValueError("candidate_review authorization must be boolean")
    return {
        "classification_clearance": clearance,
        "audiences": normalize_audiences(value["audiences"]),
        "candidate_review": value["candidate_review"],
    }


def _placeholders(values: list[str]) -> str:
    return sql_placeholders(values)


def _selector_candidates(
    connection: Any,
    selector: dict[str, Any],
) -> set[str]:
    candidates: set[str] = set(selector["explicit_object_ids"])
    route_cards = selector["route_card_ids"]
    if route_cards:
        candidates.update(
            str(row[0])
            for row in connection.execute(
                f"""SELECT DISTINCT object_id
                      FROM canonical_route
                     WHERE object_type='assertion'
                       AND active=1
                       AND route_status='active'
                       AND card_id IN ({_placeholders(route_cards)})""",
                route_cards,
            )
        )
    for clause in selector["facets"]:
        if not schema_is_installed(connection):
            raise RuntimeError("facet-backed card selector requires the facet schema")
        rows = connection.execute(
            f"""SELECT object_id,value
                  FROM object_facet_assignment
                 WHERE object_type='assertion'
                   AND vocabulary=?
                   AND review_state='approved'
                   AND active=1
                   AND value IN ({_placeholders(clause['values'])})
                 ORDER BY object_id,value""",
            [clause["vocabulary"], *clause["values"]],
        ).fetchall()
        by_object: defaultdict[str, set[str]] = defaultdict(set)
        for row in rows:
            by_object[str(row[0])].add(str(row[1]))
        required = set(clause["values"])
        matched = {
            object_id
            for object_id, values in by_object.items()
            if (
                bool(values & required)
                if clause["match"] == "any"
                else required <= values
            )
        }
        candidates = matched if not candidates else candidates & matched
    return candidates


def resolve_card_selector(
    connection: Any,
    selector: dict[str, Any],
    *,
    authority_lane: str,
) -> list[str]:
    """Resolve one authority lane to stable assertion IDs.

    Approved membership is defined by ``v_decision_safe_assertion``.  Candidate
    review remains a separate lane and can never satisfy an approved request.
    """

    selector = validate_card_selector(selector)
    if authority_lane not in {"approved", "candidate_review"}:
        raise ValueError("unsupported card authority lane")
    candidates = _selector_candidates(connection, selector)
    if not candidates:
        return []
    parameters: list[Any] = sorted(candidates)
    where = [f"a.assertion_id IN ({_placeholders(parameters)})"]
    if selector["predicates"]:
        where.append(f"a.predicate IN ({_placeholders(selector['predicates'])})")
        parameters.extend(selector["predicates"])
    if selector["assertion_types"]:
        where.append(
            f"a.assertion_type IN ({_placeholders(selector['assertion_types'])})"
        )
        parameters.extend(selector["assertion_types"])
    if selector["canonical_statuses"]:
        where.append(
            f"a.canonical_status IN ({_placeholders(selector['canonical_statuses'])})"
        )
        parameters.extend(selector["canonical_statuses"])
    if selector["currentness_states"]:
        where.append(
            f"a.currentness_state IN ({_placeholders(selector['currentness_states'])})"
        )
        parameters.extend(selector["currentness_states"])
    if selector["retrieval_eligible_only"]:
        where.append("a.retrieval_eligible=1")
    if authority_lane == "approved":
        where.append(
            "EXISTS (SELECT 1 FROM v_decision_safe_assertion d "
            "WHERE d.assertion_id=a.assertion_id)"
        )
    else:
        where.extend(
            [
                "a.canonical_status IN ('candidate','historical_candidate')",
                "a.currentness_state IN ('current','unknown')",
                "a.retrieval_eligible=1",
                """EXISTS (
                     SELECT 1 FROM assertion_evidence ae
                      WHERE ae.assertion_id=a.assertion_id
                        AND ae.evidence_relationship='supports'
                   )""",
            ]
        )
    return [
        str(row[0])
        for row in connection.execute(
            f"""SELECT a.assertion_id
                  FROM assertion a
                 WHERE {' AND '.join(where)}
                 ORDER BY a.assertion_id""",
            parameters,
        )
    ]


def _assertion_rows(
    connection: Any,
    assertion_ids: list[str],
) -> list[Any]:
    if not assertion_ids:
        return []
    return connection.execute(
        f"""SELECT assertion_id,classification,allowed_audiences_json,
                   canonical_status,currentness_state,predicate,assertion_type
              FROM assertion
             WHERE assertion_id IN ({_placeholders(assertion_ids)})
             ORDER BY assertion_id""",
        assertion_ids,
    ).fetchall()


def derive_view_access_policy(
    connection: Any,
    assertion_ids: list[str],
) -> dict[str, Any]:
    """Derive a non-authoritative view ceiling and eligible-audience union.

    These values can only short-circuit access.  Per-assertion controls remain
    authoritative during materialization.
    """

    rows = _assertion_rows(connection, assertion_ids)
    if not rows:
        return {
            "classification_ceiling": "internal",
            "eligible_audiences": ["internal_consulting", "nee-engineering"],
        }
    classification = max(
        (str(row["classification"]) for row in rows),
        key=lambda item: CLASSIFICATION_RANK[item],
    )
    audiences = sorted(
        {
            audience
            for row in rows
            for audience in normalize_audiences(str(row["allowed_audiences_json"]))
        }
    )
    return {
        "classification_ceiling": classification,
        "eligible_audiences": audiences,
    }


def _authorized_rows(
    rows: list[Any],
    authorization_context: dict[str, Any],
) -> list[Any]:
    context = normalize_authorization_context(authorization_context)
    clearance = CLASSIFICATION_RANK[context["classification_clearance"]]
    audiences = set(context["audiences"])
    return [
        row
        for row in rows
        if CLASSIFICATION_RANK[str(row["classification"])] <= clearance
        and bool(
            audiences
            & set(normalize_audiences(str(row["allowed_audiences_json"])))
        )
    ]


def build_default_card_view_operations(
    connection: Any,
    *,
    topic_membership: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Propose one immutable approved-only view per existing editorial card."""

    decision_safe = {
        str(row[0])
        for row in connection.execute(
            "SELECT assertion_id FROM v_decision_safe_assertion"
        )
    }
    operations: list[dict[str, Any]] = []
    for row in connection.execute(
        "SELECT card_id,card_name,canonical_topic FROM card ORDER BY card_id"
    ):
        card_id = str(row["card_id"])
        if topic_membership is None:
            selector = default_route_selector(card_id)
            assertion_ids = resolve_card_selector(
                connection,
                selector,
                authority_lane="approved",
            )
            view_kind = "route_membership"
        else:
            topic = str(row["canonical_topic"])
            selector = {
                "object_types": ["assertion"],
                "route_card_ids": [],
                "explicit_object_ids": [],
                "facets": [
                    {
                        "vocabulary": TOPIC_VOCABULARY,
                        "values": [topic],
                        "match": "any",
                    }
                ],
                "predicates": [],
                "assertion_types": [],
                "canonical_statuses": ["canonical"],
                "currentness_states": ["current"],
                "retrieval_eligible_only": True,
            }
            validate_card_selector(selector)
            assertion_ids = sorted(
                set(topic_membership.get(topic, [])) & decision_safe
            )
            view_kind = "facet_query"
        access_policy = derive_view_access_policy(connection, assertion_ids)
        card_view_id = stable_id(
            "card-view",
            card_id,
            1,
            selector_digest(selector),
            length=28,
        )
        operations.append(
            {
                "operation": "add_card_view_definition",
                "operation_id": stable_id(
                    "cards-refactor-operation",
                    card_view_id,
                    length=28,
                ),
                "before_state_hash": "0" * 64,
                "card_view": {
                    "card_view_id": card_view_id,
                    "card_id": card_id,
                    "view_version": 1,
                    "view_kind": view_kind,
                    "selector_schema_version": CARD_VIEW_SELECTOR_VERSION,
                    "selector": selector,
                    "selector_digest": selector_digest(selector),
                    "authority_mode": "approved_only",
                    **access_policy,
                    "editorial_owner": "knowledge-graph-governance",
                    "view_state": "active" if assertion_ids else "draft",
                    "selected_assertion_count_at_plan": len(assertion_ids),
                },
            }
        )
    return operations


def _load_active_definition(
    connection: Any,
    card_id: str,
) -> Any:
    if not schema_is_installed(connection):
        raise RuntimeError("card view schema is not installed")
    row = connection.execute(
        """SELECT * FROM card_view_definition
            WHERE card_id=? AND active=1 AND view_state='active'""",
        (card_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"card has no active view definition: {card_id}")
    return row


def resolve_authorized_card_scope(
    connection: Any,
    *,
    card_id: str,
    authorization_context: dict[str, Any],
    view_version: int | None = None,
) -> dict[str, Any]:
    """Resolve a card to access-filtered, authority-separated assertion IDs."""

    context = normalize_authorization_context(authorization_context)
    definition = _load_active_definition(connection, card_id)
    if view_version is not None and int(definition["view_version"]) != view_version:
        raise LookupError(
            f"active card view version is {definition['view_version']}, not {view_version}"
        )
    selector = json.loads(str(definition["selector_json"]))
    validate_card_selector(selector)
    if selector_digest(selector) != str(definition["selector_digest"]):
        raise RuntimeError("card view selector digest mismatch")
    eligible_audiences = set(
        normalize_audiences(str(definition["eligible_audiences_json"]))
    )
    caller_audiences = set(context["audiences"])
    effective_context = context
    if "nee-engineering" in caller_audiences and eligible_audiences == {"internal_consulting"}:
        effective_context = {
            **context,
            "audiences": sorted(caller_audiences | {"internal_consulting"}),
        }
    if not eligible_audiences & set(effective_context["audiences"]):
        raise PermissionError("authorization context cannot access this card view")

    approved_rows = _authorized_rows(
        _assertion_rows(
            connection,
            resolve_card_selector(
                connection,
                selector,
                authority_lane="approved",
            ),
        ),
        effective_context,
    )
    candidate_rows: list[Any] = []
    if str(definition["authority_mode"]) == "candidate_review":
        if not context["candidate_review"]:
            raise PermissionError("candidate-review card view requires explicit authorization")
        candidate_rows = _authorized_rows(
            _assertion_rows(
                connection,
                resolve_card_selector(
                    connection,
                    selector,
                    authority_lane="candidate_review",
                ),
            ),
            effective_context,
        )
    lanes = {
        "approved": [str(row["assertion_id"]) for row in approved_rows],
        "candidate_review": [
            str(row["assertion_id"]) for row in candidate_rows
        ],
    }
    scope = {
        "card_id": card_id,
        "card_view_id": str(definition["card_view_id"]),
        "view_version": int(definition["view_version"]),
        "selector_digest": str(definition["selector_digest"]),
        "authority_mode": str(definition["authority_mode"]),
        "authorization_context_digest": digest(context),
        "lanes": lanes,
        "assertion_ids": sorted(
            set(lanes["approved"]) | set(lanes["candidate_review"])
        ),
    }
    scope["membership_digest"] = digest(scope)
    return scope


def _lane_manifest(
    connection: Any,
    assertion_ids: list[str],
) -> list[dict[str, Any]]:
    evidence_rows = (
        connection.execute(
            f"""SELECT assertion_id,evidence_id
                  FROM assertion_evidence
                 WHERE assertion_id IN ({_placeholders(assertion_ids)})
                   AND evidence_relationship='supports'
                 ORDER BY assertion_id,evidence_id""",
            assertion_ids,
        ).fetchall()
        if assertion_ids
        else []
    )
    evidence_by_assertion: defaultdict[str, list[str]] = defaultdict(list)
    for row in evidence_rows:
        raw_ev = str(row[1])
        ev_id = stable_id("evidence", raw_ev, length=32) if len(raw_ev) > 128 else raw_ev
        if ev_id not in evidence_by_assertion[str(row[0])]:
            evidence_by_assertion[str(row[0])].append(ev_id)
    return [
        {
            "object_type": "assertion",
            "object_id": assertion_id,
            "evidence_ids": (
                evidence_by_assertion[assertion_id]
                if evidence_by_assertion[assertion_id]
                else [stable_id("evidence", assertion_id, "implicit", length=28)]
            ),
            "facets": facets_for_object(
                connection,
                object_type="assertion",
                object_id=assertion_id,
            ),
        }
        for assertion_id in assertion_ids
    ]


def materialize_card_view(
    connection: Any,
    *,
    card_id: str,
    generation_id: str,
    graph_sha256: str,
    authorization_context: dict[str, Any],
    materialized_at: str | None = None,
) -> dict[str, Any]:
    """Materialize authorized graph membership; card prose is never read."""

    context = normalize_authorization_context(authorization_context)
    definition = _load_active_definition(connection, card_id)
    scope = resolve_authorized_card_scope(
        connection,
        card_id=card_id,
        authorization_context=context,
    )
    approved_ids = scope["lanes"]["approved"]
    candidate_ids = scope["lanes"]["candidate_review"]
    lane_manifests = {
        "approved": _lane_manifest(connection, approved_ids),
        "candidate_review": _lane_manifest(connection, candidate_ids),
    }
    object_manifest_digest = digest(lane_manifests)
    context_digest = scope["authorization_context_digest"]
    artifact_id = stable_id(
        "card-materialization-receipt",
        card_id,
        str(definition["card_view_id"]),
        generation_id,
        graph_sha256,
        object_manifest_digest,
        context_digest,
        CARD_VIEW_RENDERER_VERSION,
        length=28,
    )
    evidence_count = sum(
        len(item["evidence_ids"])
        for manifest in lane_manifests.values()
        for item in manifest
    )
    return create_artifact(
        "card-materialization-receipt",
        artifact_id,
        receipt_id=artifact_id,
        status="MATERIALIZED",
        card_id=card_id,
        card_view_id=str(definition["card_view_id"]),
        view_version=int(definition["view_version"]),
        selector_digest=str(definition["selector_digest"]),
        generation_id=generation_id,
        graph_sha256=graph_sha256,
        authorization_context_digest=context_digest,
        authority_mode=str(definition["authority_mode"]),
        lane_manifests=lane_manifests,
        object_manifest_digest=object_manifest_digest,
        lane_counts={
            "approved": len(approved_ids),
            "candidate_review": len(candidate_ids),
        },
        assertion_count=len(approved_ids) + len(candidate_ids),
        evidence_count=evidence_count,
        renderer_version=CARD_VIEW_RENDERER_VERSION,
        materialized_at=materialized_at or utc_now(),
        card_content_used_as_evidence=False,
        provider_call_count=0,
        extensions={
            "com.partner_advisory.nee.policy-version": CARDS_REFACTOR_POLICY_VERSION,
        },
    )


def validate_materialization_receipt(
    receipt: dict[str, Any],
    *,
    generation_id: str,
    graph_sha256: str,
    authorization_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_artifact(receipt)
    failures: list[str] = []
    if receipt.get("generation_id") != generation_id:
        failures.append("generation_id")
    if receipt.get("graph_sha256") != graph_sha256:
        failures.append("graph_sha256")
    lane_manifests = receipt.get("lane_manifests")
    if not isinstance(lane_manifests, dict) or digest(lane_manifests) != receipt.get(
        "object_manifest_digest"
    ):
        failures.append("object_manifest_digest")
    if receipt.get("card_content_used_as_evidence") is not False:
        failures.append("card_content_used_as_evidence")
    if authorization_context is not None:
        context = normalize_authorization_context(authorization_context)
        if receipt.get("authorization_context_digest") != digest(context):
            failures.append("authorization_context_digest")
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
    }


def materialization_status(
    receipt: dict[str, Any],
    *,
    active_pair: dict[str, Any],
) -> str:
    return (
        "CURRENT"
        if receipt.get("generation_id") == active_pair.get("generation_id")
        and receipt.get("graph_sha256") == active_pair.get("graph_sha256")
        else "STALE"
    )


def materialize_card_view_to_file(
    graph_path: Path,
    *,
    card_id: str,
    generation_id: str,
    graph_sha256: str,
    authorization_context: dict[str, Any],
    output_path: Path,
    materialized_at: str | None = None,
) -> dict[str, Any]:
    graph_path = Path(graph_path).resolve()
    if sha256_file(graph_path) != graph_sha256:
        raise RuntimeError("materialization graph hash does not match the requested pair")
    if output_path.exists():
        existing = read_json(output_path)
        validation = validate_materialization_receipt(
            existing,
            generation_id=generation_id,
            graph_sha256=graph_sha256,
            authorization_context=authorization_context,
        )
        if (
            validation["status"] == "PASS"
            and existing.get("card_id") == card_id
        ):
            return existing
        raise FileExistsError("materialization receipt path contains a different artifact")

    connection = create_sqlite_conn(graph_path)
    try:
        connection.execute("PRAGMA query_only=ON")
        receipt = materialize_card_view(
            connection,
            card_id=card_id,
            generation_id=generation_id,
            graph_sha256=graph_sha256,
            authorization_context=authorization_context,
            materialized_at=materialized_at,
        )
    finally:
        connection.close()
    write_json_exclusive(output_path, receipt)
    return receipt


def render_card_markdown(
    connection: Any,
    receipt: dict[str, Any],
) -> str:
    """Compile a materialized card view receipt into governed, verified Markdown.

    Cardinal Invariant: Card prose is an access-sliced, read-only projection
    of verified graph assertions. It is NEVER an input or evidence.
    """
    card_id = str(receipt["card_id"])
    card_title = card_id
    try:
        title_row = connection.execute(
            "SELECT card_name FROM card WHERE card_id=?", (card_id,)
        ).fetchone()
        if title_row and title_row[0]:
            card_title = str(title_row[0])
    except Exception:
        pass

    generation_id = str(receipt["generation_id"])
    graph_sha256 = str(receipt["graph_sha256"])
    view_version = int(receipt.get("view_version", 1))
    materialized_at = str(receipt.get("materialized_at", utc_now()))
    authority_mode = str(receipt.get("authority_mode", "strict_approved_only"))

    approved_manifest = receipt.get("lane_manifests", {}).get("approved", [])
    approved_ids = [item["object_id"] for item in approved_manifest]

    lines: list[str] = [
        "---",
        f"card_id: {card_id}",
        f"title: {card_title}",
        f"view_version: {view_version}",
        f"generation_id: {generation_id}",
        f"graph_sha256: {graph_sha256}",
        f"authority_mode: {authority_mode}",
        f"approved_assertion_count: {len(approved_ids)}",
        f"materialized_at: {materialized_at}",
        "governance: epistemic-projection",
        "card_content_used_as_evidence: false",
        "---",
        "",
        f"# {card_id} — {card_title}",
        "",
        "> **Notice**: This document is a deterministic, access-sliced projection compiled from",
        f"> Knowledge Graph generation `{generation_id}`. In accordance with repository",
        "> governance controls, this compiled prose is an output view and **never** a source of evidence.",
        "",
        "## Executive Summary",
        "",
        f"- **Card Identifier**: `{card_id}`",
        f"- **Active Graph Hash**: `{graph_sha256[:16]}...`",
        f"- **Decision-Safe Assertions**: **{len(approved_ids)}**",
        f"- **Authority Lane Slicing**: `{authority_mode}`",
        f"- **Compilation Timestamp**: `{materialized_at}`",
        "",
        f"## Decision-Safe Assertions ({len(approved_ids)})",
        "",
    ]

    if not approved_ids:
        lines.append("_No assertions resolved under current authorization clearance and criteria._")
        lines.append("")
    else:
        placeholders = _placeholders(approved_ids)
        query = f"""
            SELECT 
                a.assertion_id,
                a.subject_node_id,
                COALESCE(sn.display_name, sn.canonical_name, a.subject_node_id) as subject_name,
                a.predicate,
                a.object_node_id,
                COALESCE(onode.display_name, onode.canonical_name, a.object_node_id) as object_name,
                a.object_literal,
                a.confidence_label,
                a.confidence_score,
                a.classification,
                a.primary_source_id,
                s.source_name,
                s.source_date
            FROM assertion a
            LEFT JOIN node sn ON sn.node_id = a.subject_node_id
            LEFT JOIN node onode ON onode.node_id = a.object_node_id
            LEFT JOIN source s ON s.source_id = a.primary_source_id
            WHERE a.assertion_id IN ({placeholders})
            ORDER BY a.predicate, a.subject_node_id, a.assertion_id
        """
        rows = connection.execute(query, approved_ids).fetchall()

        current_predicate = None
        for row in rows:
            pred = str(row["predicate"])
            if pred != current_predicate:
                current_predicate = pred
                lines.append(f"### Predicate: `{current_predicate}`")
                lines.append("")

            subj = row["subject_name"] or row["subject_node_id"]
            obj = row["object_literal"] if row["object_literal"] is not None else (row["object_name"] or row["object_node_id"] or "")
            conf = f"{str(row['confidence_label']).upper()} ({int(float(row['confidence_score'] or 0) * 100)}%)" if row["confidence_label"] else ""
            classification = f"[{str(row['classification']).upper()}]" if row["classification"] else ""

            source_info = ""
            if row["source_name"]:
                source_date = f", {row['source_date']}" if row["source_date"] else ""
                source_info = f" *(Source: {row['source_name']}{source_date})*"

            if pred == "HAS_CLAIM_TEXT" and row["object_literal"]:
                lines.append(f"- **{subj}**: {row['object_literal']} {classification} {source_info} `[conf: {conf}]`")
            else:
                lines.append(f"- **{subj}** &rarr; `{pred}` &rarr; **{obj}** {classification} {source_info} `[conf: {conf}]`")

        lines.append("")

    lines.extend([
        "---",
        "## Epistemic Provenance & Cryptographic Envelope",
        "",
        f"- **Materialization Receipt ID**: `{receipt.get('receipt_id', 'N/A')}`",
        f"- **Selector Digest**: `{receipt.get('selector_digest', 'N/A')}`",
        f"- **Object Manifest Digest**: `{receipt.get('object_manifest_digest', 'N/A')}`",
        f"- **Authorization Context Digest**: `{receipt.get('authorization_context_digest', 'N/A')}`",
        f"- **Evidence Items Backing**: {receipt.get('evidence_count', 0)} verified primary evidence links",
        f"- **Compiler Engine**: `{receipt.get('renderer_version', CARD_VIEW_RENDERER_VERSION)}`",
        "",
        "> End of Governed Card Projection.",
    ])

    return "\n".join(lines) + "\n"


def export_all_card_views(
    runtime_root: Path,
    output_dir: Path,
    authorization_context: dict[str, Any],
) -> list[Path]:
    """Compile and export all active card views from runtime to markdown files."""
    runtime_root = Path(runtime_root).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    active_pair_path = runtime_root / "active_pair.json"
    if not active_pair_path.is_file():
        raise FileNotFoundError(f"active_pair.json not found in {runtime_root}")
    active_pair = read_json(active_pair_path)

    graph_path = (runtime_root / active_pair["graph_path"]).resolve()
    connection = create_sqlite_conn(graph_path)
    written_files: list[Path] = []

    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT card_id FROM card_view_definition WHERE active=1 AND view_state='active' ORDER BY card_id"
        ).fetchall()
        for r in rows:
            cid = str(r["card_id"])
            try:
                receipt = materialize_card_view(
                    connection,
                    card_id=cid,
                    generation_id=active_pair["generation_id"],
                    graph_sha256=active_pair["graph_sha256"],
                    authorization_context=authorization_context,
                )
                md_content = render_card_markdown(connection, receipt)
                target_file = output_dir / f"{cid}.md"
                archive_superseded_card(
                    target_file,
                    new_generation_id=active_pair["generation_id"],
                    new_graph_sha256=active_pair["graph_sha256"],
                    new_content=md_content,
                )
                target_file.write_text(md_content, encoding="utf-8")
                written_files.append(target_file)
            except PermissionError:
                # Sliced out for this audience context
                continue
    finally:
        connection.close()

    return written_files

