"""Request-specific structured-output constraints for governed L2 providers.

The static decision schema protects output shape.  This module projects that
schema against one sealed evidence packet so a strict-output provider cannot
select record identifiers that are invalid for an output field's governed
lane or semantic ``answer_use``.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

from .core import digest

OUTPUT_CONTRACT_VERSION = "nee-l2-record-bound-output-1.2.0"
PAIRING_INVARIANT_DESCRIPTION = (
    "Citations and evidence IDs must form a permitted pair from prompt slot C0M."
)

FIELD_ELIGIBILITY: Mapping[str, tuple[tuple[str, ...], str]] = {
    "current_state": (
        ("approved",),
        "factual_answer",
    ),
    "counterevidence": (
        ("approved",),
        "factual_answer",
    ),
    "pain_points": (("approved",), "factual_answer"),
    "opportunities": (("approved",), "factual_answer"),
    "guidance": (("approved",), "guidance"),
    "hypotheses": (("candidate",), "candidate_hypothesis"),
    "source_evidence": (("candidate",), "citation_only"),
    "proposed_assertions": (("candidate",), "candidate_hypothesis"),
}
STRATEGIC_RECOMMENDATION_ANSWER_USES = frozenset(
    {
        "factual_answer",
        "strategic_context",
        "decision_constraint",
        "external_analogy",
    }
)

SYSTEM_INSTRUCTION_EXTENSION = (
    " Field eligibility is mandatory: current_state, pain_points, opportunities, "
    "and counterevidence may use only records whose answer_use is factual_answer, "
    "except opportunity_nba pain points, opportunities, and actions may use "
    "approved strategic_context or decision_constraint and may use external_analogy "
    "without restating them as current-state facts; "
    "guidance may use only answer_use guidance; hypotheses and proposed_assertions "
    "may use only answer_use candidate_hypothesis; source_evidence may use only "
    "answer_use citation_only and must be labeled CANDIDATE_SOURCE_EVIDENCE; "
    "for material synthesis, assumptions must include at least one explicit "
    "assumption about the active governed snapshot or the limits of the supplied "
    "evidence; "
    "keep the structured response compact and within the schema item limits: emit "
    "only distinct material claims, do not restate every retrieved record, keep "
    "rationale concise, and finish the JSON object before the output limit; "
    "next_best_actions may use only eligible governed records. If no eligible records exist for a field, "
    "return an empty array for that field. For each material row, every evidence_id "
    "must belong to at least one cited record in that same row, and every cited "
    "record must be supported by an evidence_id in that same row. Meet every "
    "governed_prompt.slots.R0 quality requirement. Cite every record listed in "
    "required_approved_citation_ids at least once. When candidate_hypothesis_required "
    "is true, emit at least one hypotheses or proposed_assertions row whose "
    "hypothesis or proposal begins exactly CANDIDATE:. The governed prompt "
    "field G0.required_material_record_ids_by_lane is the bounded, route-specific "
    "material citation obligation; cite every listed record in an applicable "
    "material answer row."
)


def _fallback_answer_use(record: Mapping[str, Any], lane: str) -> str:
    return {
        "action_item": "guidance",
        "source_chunk": "citation_only",
        "architecture_component": "navigation_only",
    }.get(
        str(record.get("record_type", "")),
        "candidate_hypothesis" if lane == "candidate" else "factual_answer",
    )


def _packet_bindings(
    evidence_packet: Mapping[str, Any],
) -> tuple[
    dict[str, list[str]],
    dict[str, str],
    dict[str, list[str]],
]:
    records_by_lane = {
        "approved": [],
        "candidate": [],
        "external_public": [],
    }
    answer_use_by_record: dict[str, str] = {}
    evidence_by_record: dict[str, list[str]] = {}
    for lane in records_by_lane:
        rows = evidence_packet.get(lane, [])
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, Mapping):
                continue
            record = item.get("record")
            if not isinstance(record, Mapping):
                continue
            record_id = record.get("record_id")
            if not isinstance(record_id, str):
                continue
            records_by_lane[lane].append(record_id)

            metadata = item.get("retrieval_metadata")
            answer_use = (
                metadata.get("answer_use")
                if isinstance(metadata, Mapping)
                else record.get("answer_use")
            )
            answer_use_by_record[record_id] = (
                answer_use if isinstance(answer_use, str) else _fallback_answer_use(record, lane)
            )

            governed_evidence = {
                value for value in record.get("evidence_ids", []) if isinstance(value, str)
            }
            hydrated = item.get("evidence", [])
            if isinstance(hydrated, list):
                governed_evidence.update(
                    str(value["evidence_id"])
                    for value in hydrated
                    if isinstance(value, Mapping) and isinstance(value.get("evidence_id"), str)
                )
            evidence_by_record[record_id] = sorted(governed_evidence)
    return records_by_lane, answer_use_by_record, evidence_by_record


def record_evidence_binding_map(
    evidence_packet: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Return one compact prompt-level record/evidence binding map."""

    _, _, evidence_by_record = _packet_bindings(evidence_packet)
    return {
        record_id: list(evidence_ids)
        for record_id, evidence_ids in sorted(evidence_by_record.items())
        if evidence_ids
    }


def material_citation_obligations(
    evidence_packet: Mapping[str, Any],
    *,
    query_type: str,
) -> list[dict[str, Any]]:
    """Describe bounded material records and their eligible answer fields.

    This is prompt guidance only. Deterministic output validation remains the
    authority, but providers need an explicit record-to-field binding when a
    required record is not among the first retrieved rows.
    """

    required = evidence_packet.get("required_material_record_ids_by_lane", {})
    if not isinstance(required, Mapping):
        return []

    records: dict[tuple[str, str], Mapping[str, Any]] = {}
    answer_uses: dict[tuple[str, str], str] = {}
    for lane in ("approved", "candidate", "external_public"):
        rows = evidence_packet.get(lane, [])
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, Mapping) or not isinstance(item.get("record"), Mapping):
                continue
            record = item["record"]
            record_id = record.get("record_id")
            if not isinstance(record_id, str):
                continue
            metadata = item.get("retrieval_metadata")
            answer_use = (
                metadata.get("answer_use")
                if isinstance(metadata, Mapping)
                else record.get("answer_use")
            )
            records[(lane, record_id)] = record
            answer_uses[(lane, record_id)] = (
                answer_use
                if isinstance(answer_use, str)
                else _fallback_answer_use(record, lane)
            )

    obligations: list[dict[str, Any]] = []
    for lane in ("approved", "candidate", "external_public"):
        record_ids = required.get(lane, [])
        if not isinstance(record_ids, list):
            continue
        for record_id in record_ids:
            if not isinstance(record_id, str):
                continue
            record = records.get((lane, record_id))
            answer_use = answer_uses.get((lane, record_id))
            eligible_fields: list[str] = []
            for field_name, (allowed_lanes, allowed_use) in FIELD_ELIGIBILITY.items():
                field_uses = {allowed_use}
                field_lanes = allowed_lanes
                if query_type == "opportunity_nba" and field_name in {
                    "pain_points",
                    "opportunities",
                }:
                    field_uses = set(STRATEGIC_RECOMMENDATION_ANSWER_USES)
                    if field_name == "pain_points":
                        field_uses.discard("external_analogy")
                    else:
                        field_lanes = ("approved", "external_public")
                if lane in field_lanes and answer_use in field_uses:
                    eligible_fields.append(field_name)
            obligations.append(
                {
                    "lane": lane,
                    "record_id": record_id,
                    "evidence_ids": (
                        sorted(
                            {
                                value
                                for value in record.get("evidence_ids", [])
                                if isinstance(value, str)
                            }
                            | {
                                value["evidence_id"]
                                for item in evidence_packet.get(lane, [])
                                if isinstance(item, Mapping)
                                and isinstance(item.get("record"), Mapping)
                                and item["record"].get("record_id") == record_id
                                for value in item.get("evidence", [])
                                if isinstance(value, Mapping)
                                and isinstance(value.get("evidence_id"), str)
                            }
                        )
                        if record is not None
                        else []
                    ),
                    "answer_use": answer_use,
                    "eligible_answer_fields": eligible_fields,
                    "record_present_in_packet": record is not None,
                }
            )
    return obligations


def _resolve_action_uses(
    expected_query_type: str | None,
    records_by_lane: Mapping[str, Sequence[str]],
    answer_use_by_record: Mapping[str, str],
) -> frozenset[str]:
    if expected_query_type != "opportunity_nba":
        return frozenset({"factual_answer"})
    approved_ids = records_by_lane["approved"]
    if len(approved_ids) == 1 and answer_use_by_record.get(approved_ids[0]) == "guidance":
        return STRATEGIC_RECOMMENDATION_ANSWER_USES | frozenset({"guidance"})
    return STRATEGIC_RECOMMENDATION_ANSWER_USES


def record_bound_decision_schema(
    base_schema: Mapping[str, Any],
    evidence_packet: Mapping[str, Any] | None,
    *,
    expected_query_type: str | None = None,
    canonicalize_query_type: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Project the static answer schema onto one sealed evidence packet."""

    schema = deepcopy(dict(base_schema))
    if expected_query_type is not None:
        canonical = canonicalize_query_type(expected_query_type) if canonicalize_query_type is not None else expected_query_type
        schema["properties"]["query_type"] = {"type": "string", "const": canonical}
    if evidence_packet is None:
        return schema

    records_by_lane, answer_use_by_record, evidence_by_record = _packet_bindings(evidence_packet)

    for field_name, (lanes, answer_use) in FIELD_ELIGIBILITY.items():
        field_schema = schema["properties"][field_name]
        properties = field_schema["items"]["properties"]
        allowed_lanes = lanes
        allowed_answer_uses = {answer_use}
        if expected_query_type == "opportunity_nba" and field_name in {"pain_points", "opportunities"}:
            allowed_answer_uses = set(STRATEGIC_RECOMMENDATION_ANSWER_USES)
            if field_name == "pain_points":
                allowed_answer_uses.discard("external_analogy")
            else:
                allowed_lanes = ("approved", "external_public")
        record_ids = sorted({
            record_id for lane in allowed_lanes for record_id in records_by_lane[lane]
            if answer_use_by_record.get(record_id) in allowed_answer_uses
        })
        evidence_ids = sorted({
            evidence_id for record_id in record_ids for evidence_id in evidence_by_record.get(record_id, [])
        })
        if not record_ids or not evidence_ids:
            field_schema["maxItems"] = 0
            continue
        properties["citations"]["items"]["enum"] = record_ids
        properties["evidence_ids"]["items"]["enum"] = evidence_ids
        properties["citations"]["description"] = PAIRING_INVARIANT_DESCRIPTION
        properties["evidence_ids"]["description"] = PAIRING_INVARIANT_DESCRIPTION

    action_schema = schema["properties"]["next_best_actions"]
    action_properties = action_schema["items"]["properties"]
    action_lanes = ("approved", "external_public") if expected_query_type == "opportunity_nba" else ("approved",)
    action_uses = _resolve_action_uses(expected_query_type, records_by_lane, answer_use_by_record)
    eligible_action_records = sorted(
        record_id for lane in action_lanes for record_id in records_by_lane[lane]
        if answer_use_by_record.get(record_id) in action_uses
    )
    if eligible_action_records:
        action_properties["basis_citations"]["items"]["enum"] = eligible_action_records
    else:
        action_schema["maxItems"] = 0
    return schema


def output_contract_digest(base_schema: Mapping[str, Any]) -> str:
    """Return the versioned digest that must participate in query run identity."""

    return digest(
        {
            "version": OUTPUT_CONTRACT_VERSION,
            "base_schema": base_schema,
            "field_eligibility": {
                field: {"lanes": list(lanes), "answer_use": answer_use}
                for field, (lanes, answer_use) in FIELD_ELIGIBILITY.items()
            },
            "system_instruction_extension": SYSTEM_INSTRUCTION_EXTENSION,
            "pairing_invariant_description": PAIRING_INVARIANT_DESCRIPTION,
            "pairing_map_slot": "C0M",
            "strategic_recommendation_answer_uses": sorted(
                STRATEGIC_RECOMMENDATION_ANSWER_USES
            ),
        }
    )


__all__ = [
    "FIELD_ELIGIBILITY",
    "OUTPUT_CONTRACT_VERSION",
    "PAIRING_INVARIANT_DESCRIPTION",
    "SYSTEM_INSTRUCTION_EXTENSION",
    "STRATEGIC_RECOMMENDATION_ANSWER_USES",
    "material_citation_obligations",
    "output_contract_digest",
    "record_evidence_binding_map",
    "record_bound_decision_schema",
]
