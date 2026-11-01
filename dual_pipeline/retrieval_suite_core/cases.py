"""Case generation helpers and graded QREL builders for retrieval release suite."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from ..index import CLASSIFICATION_RANK, RETRIEVAL_MODE_POLICY
from .constants import _CLASSIFICATIONS, _MAX_QUERY_CHARACTERS, _RELATIONSHIP_RECORD_TYPES
from .exceptions import AutomaticSuiteBuildError
from .qrel_grades import (
    build_graded_qrels_for_group,
    compute_content_word_jaccard,
    mine_sibling_hard_negatives,
    synthesize_low_overlap_paraphrase,
)


def _record(value: str) -> dict[str, Any]:
    return json.loads(value)["retrieval_record"]


_APPROVED_MODE_MAP = {
    "factual_answer": "factual",
    "guidance": "guidance",
    "strategic_context": "strategic",
    "decision_constraint": "strategic",
    "external_analogy": "strategic",
}
_NAV_MODE_MAP = {
    "approved": "entity_navigation",
    "candidate": "candidate_entity_navigation",
}


def _retrieval_mode(record: dict[str, Any]) -> str:
    lane, answer_use = record["lane"], record["answer_use"]
    if answer_use == "navigation_only":
        return _NAV_MODE_MAP.get(lane, "strategic")
    if lane == "approved":
        return _APPROVED_MODE_MAP.get(answer_use, "factual")
    if lane == "candidate":
        return "candidate_guidance" if answer_use == "guidance" else "candidate_evaluation"
    if lane == "external_public":
        return "strategic"
    raise AutomaticSuiteBuildError(
        "RETRIEVAL_PROFILE_REQUIRED",
        f"record {record['record_id']} has no governed retrieval lane",
    )


def _retrieval_expected(record: dict[str, Any]) -> bool:
    mode = _retrieval_mode(record)
    return record["answer_use"] in RETRIEVAL_MODE_POLICY[mode][record["lane"]]


def _lane_isolation(record: dict[str, Any]) -> tuple[str, str]:
    if record["answer_use"] == "navigation_only":
        return (
            ("candidate", "candidate_entity_navigation")
            if record["lane"] == "approved"
            else ("approved", "entity_navigation")
        )
    if record["lane"] == "approved":
        return "candidate", "strategic"
    return "approved", "strategic"


def _scope_ids(record: dict[str, Any]) -> list[str]:
    extensions = record.get("extensions", {})
    values = set(extensions.get("com.partner_advisory.nee.graph-assertion-ids", []))
    object_id = extensions.get("com.partner_advisory.nee.graph-object-id")
    if object_id:
        values.add(object_id)
    return sorted(values)


def _bounded(value: str, *, limit: int = _MAX_QUERY_CHARACTERS) -> str:
    normalized = " ".join(str(value).split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1].rstrip() + "…"


def _template_values(records: list[dict[str, Any]]) -> dict[str, str]:
    titles = "; ".join(record["title"] for record in records)
    entities = "; ".join(
        dict.fromkeys(
            entity
            for record in records
            for entity in (
                [record["title"]]
                if record.get("record_type") == "entity_facet"
                else record.get("canonical_entities", [])
            )
        )
    )
    retrieval_text = " | ".join(record["retrieval_text"] for record in records)
    return {
        "titles": _bounded(titles, limit=1200),
        "entities": _bounded(entities or titles, limit=1200),
        "retrieval_text": _bounded(retrieval_text, limit=2200),
    }


def _render(template: str, values: dict[str, str]) -> str:
    return _bounded(template.format(**values))


def _case(
    *,
    case_id: str,
    query_kind: str,
    query: str,
    record_ids: list[str],
    lane: str,
    retrieval_mode: str,
    classification: str,
    audiences: list[str],
    top_k: int,
    expected: bool,
    scope_ids: list[str] | None = None,
    scope: str = "delta",
    critical: bool = False,
    qrel_grades: dict[str, int] | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "case_id": case_id,
        "scope": scope,
        "query_kind": query_kind,
        "critical": critical,
        "query": query,
        "classification": classification,
        "audiences": audiences,
        "lane": lane,
        "retrieval_mode": retrieval_mode,
        "top_k": top_k,
        "expected_record_ids": record_ids if expected else [],
        "forbidden_record_ids": [] if expected else record_ids,
    }
    if scope_ids:
        value["allowed_assertion_ids"] = scope_ids
    if qrel_grades is not None:
        value["qrel_grades"] = dict(qrel_grades)
    return value


def _acl_boundary(
    records: list[dict[str, Any]],
    policy: dict[str, Any],
) -> tuple[str, list[str]]:
    classification = records[0]["classification"]
    rank = CLASSIFICATION_RANK[classification]
    if rank > 0:
        return _CLASSIFICATIONS[rank - 1], list(records[0]["audiences"])
    record_audiences = set(records[0]["audiences"])
    audience = next(
        (
            item
            for item in policy["acl_boundary_audiences"]
            if item not in record_audiences
        ),
        None,
    )
    if audience is None:
        raise AutomaticSuiteBuildError(
            "ACL_BOUNDARY_AUDIENCE_REQUIRED",
            (
                f"public records in group {records[0]['record_id']} cover every "
                "configured ACL boundary audience"
            ),
        )
    return classification, [audience]


def _group_key(record: dict[str, Any]) -> tuple[Any, ...]:
    scope_ids = _scope_ids(record)
    entities = tuple(record.get("canonical_entities", []))
    subject = (entities or (record["record_id"],)) if (record.get("record_type") == "entity_facet" or not scope_ids) else ()
    return (
        record["lane"],
        _retrieval_mode(record),
        record["classification"],
        tuple(record["audiences"]),
        record["record_type"],
        subject,
        record["record_type"] in _RELATIONSHIP_RECORD_TYPES,
        _retrieval_expected(record),
        bool(scope_ids),
    )


def _groups(
    records: list[dict[str, Any]],
    *,
    max_size: int,
) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in sorted(records, key=lambda item: item["record_id"]):
        grouped[_group_key(record)].append(record)
    result: list[list[dict[str, Any]]] = []
    for key in sorted(grouped, key=str):
        values = grouped[key]
        # Without assertion/object scope, keep the group record-specific so a
        # broad top-K query cannot accidentally stand in for exact coverage.
        size = max_size if key[-1] else 1
        result.extend(values[index : index + size] for index in range(0, len(values), size))
    return result


def _delta_group_cases(
    records: list[dict[str, Any]],
    *,
    ordinal: int,
    generation_policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], bool]:
    record_ids = sorted(record["record_id"] for record in records)
    scope_ids = sorted({item for record in records for item in _scope_ids(record)})
    mode = _retrieval_mode(records[0])
    retrieval_expected = _retrieval_expected(records[0])
    values = _template_values(records)
    prefix = f"auto-delta-{ordinal:04d}"
    semantic_ids = [f"{prefix}-semantic-{index}" for index in range(1, 4)]
    alias_id = f"{prefix}-alias"
    hard_negative_id = f"{prefix}-hard-negative"
    lane_id = f"{prefix}-lane-isolation"
    acl_id = f"{prefix}-acl-boundary"
    semantic_cases = [
        _case(
            case_id=case_id,
            query_kind="semantic_paraphrase",
            query=_render(template, values),
            record_ids=record_ids,
            lane=records[0]["lane"],
            retrieval_mode=mode,
            classification=records[0]["classification"],
            audiences=list(records[0]["audiences"]),
            top_k=int(generation_policy["delta_top_k"]),
            expected=retrieval_expected,
            scope_ids=scope_ids,
        )
        for case_id, template in zip(
            semantic_ids,
            generation_policy["semantic_templates"][:3],
            strict=True,
        )
    ]
    lane, lane_mode = _lane_isolation(records[0])
    acl_classification, acl_audiences = _acl_boundary(records, generation_policy)
    cases = [
        *semantic_cases,
        _case(
            case_id=alias_id,
            query_kind="alias",
            query=_render(generation_policy["alias_template"], values),
            record_ids=record_ids,
            lane=records[0]["lane"],
            retrieval_mode=mode,
            classification=records[0]["classification"],
            audiences=list(records[0]["audiences"]),
            top_k=int(generation_policy["delta_top_k"]),
            expected=retrieval_expected,
            scope_ids=scope_ids,
        ),
        _case(
            case_id=hard_negative_id,
            query_kind="hard_negative",
            query=generation_policy["hard_negative_query"],
            record_ids=record_ids,
            lane=records[0]["lane"],
            retrieval_mode=mode,
            classification=records[0]["classification"],
            audiences=list(records[0]["audiences"]),
            top_k=int(generation_policy["delta_top_k"]),
            expected=False,
            scope_ids=["assertion-intentionally-absent-release-negative"],
        ),
        _case(
            case_id=lane_id,
            query_kind="lane_isolation",
            query=semantic_cases[0]["query"],
            record_ids=record_ids,
            lane=lane,
            retrieval_mode=lane_mode,
            classification=records[0]["classification"],
            audiences=list(records[0]["audiences"]),
            top_k=int(generation_policy["delta_top_k"]),
            expected=False,
            scope_ids=scope_ids,
        ),
        _case(
            case_id=acl_id,
            query_kind="acl_boundary",
            query=semantic_cases[0]["query"],
            record_ids=record_ids,
            lane=records[0]["lane"],
            retrieval_mode=mode,
            classification=acl_classification,
            audiences=acl_audiences,
            top_k=int(generation_policy["delta_top_k"]),
            expected=False,
            scope_ids=scope_ids,
        ),
    ]
    case_ids = [*semantic_ids, alias_id, hard_negative_id, lane_id, acl_id]
    relationship = records[0]["record_type"] in _RELATIONSHIP_RECORD_TYPES
    if relationship:
        relationship_id = f"{prefix}-relationship"
        cases.append(
            _case(
                case_id=relationship_id,
                query_kind="relationship",
                query=_render(generation_policy["relationship_template"], values),
                record_ids=record_ids,
                lane=records[0]["lane"],
                retrieval_mode=mode,
                classification=records[0]["classification"],
                audiences=list(records[0]["audiences"]),
                top_k=int(generation_policy["delta_top_k"]),
                expected=retrieval_expected,
                scope_ids=scope_ids,
            )
        )
        case_ids.append(relationship_id)
    return cases, case_ids, relationship


def _removed_group_cases(
    records: list[dict[str, Any]],
    *,
    ordinal: int,
    generation_policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], bool]:
    values = _template_values(records)
    stale_case_id = f"auto-removed-{ordinal:04d}-stale"
    scope_ids = sorted({item for record in records for item in _scope_ids(record)})
    cases = [
        _case(
            case_id=stale_case_id,
            query_kind="stale_or_superseded",
            query=_render(generation_policy["semantic_templates"][0], values),
            record_ids=sorted(record["record_id"] for record in records),
            lane=records[0]["lane"],
            retrieval_mode=_retrieval_mode(records[0]),
            classification=records[0]["classification"],
            audiences=list(records[0]["audiences"]),
            top_k=int(generation_policy["delta_top_k"]),
            expected=False,
            scope_ids=scope_ids,
        )
    ]
    case_ids = [stale_case_id]
    relationship = records[0]["record_type"] in _RELATIONSHIP_RECORD_TYPES
    if relationship:
        relationship_case_id = f"auto-removed-{ordinal:04d}-relationship"
        cases.append(
            _case(
                case_id=relationship_case_id,
                query_kind="relationship",
                query=_render(generation_policy["relationship_template"], values),
                record_ids=sorted(record["record_id"] for record in records),
                lane=records[0]["lane"],
                retrieval_mode=_retrieval_mode(records[0]),
                classification=records[0]["classification"],
                audiences=list(records[0]["audiences"]),
                top_k=int(generation_policy["delta_top_k"]),
                expected=False,
                scope_ids=scope_ids,
            )
        )
        case_ids.append(relationship_case_id)
    return cases, case_ids, relationship


def _core_case(
    baseline_records: list[dict[str, Any]],
    generation_policy: dict[str, Any],
    candidate_record_ids: set[str] | None = None,
) -> dict[str, Any]:
    supported: list[dict[str, Any]] = []
    for record in sorted(baseline_records, key=lambda item: item["record_id"]):
        if candidate_record_ids is not None and record["record_id"] not in candidate_record_ids:
            continue
        try:
            _retrieval_mode(record)
        except AutomaticSuiteBuildError:
            continue
        if record["lane"] == "approved" and record["answer_use"] == "factual_answer":
            supported.append(record)
    if not supported:
        raise AutomaticSuiteBuildError(
            "CORE_CASE_REQUIRED",
            "the active baseline has no supported approved factual retrieval record",
        )
    record = supported[0]
    return _case(
        case_id="auto-core-approved-factual",
        query_kind="semantic_paraphrase",
        query=_bounded(record["retrieval_text"]),
        record_ids=[record["record_id"]],
        lane=record["lane"],
        retrieval_mode=_retrieval_mode(record),
        classification=record["classification"],
        audiences=list(record["audiences"]),
        top_k=int(generation_policy["core_top_k"]),
        expected=True,
        scope_ids=_scope_ids(record),
        scope="core",
        critical=True,
    )
