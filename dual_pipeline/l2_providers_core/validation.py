"""Deterministic validation and semantic quality evaluation for L2 answers."""

from __future__ import annotations

import json
from typing import Any, Mapping

from dual_pipeline.core import canonical_json
from dual_pipeline.l2_output_contract import STRATEGIC_RECOMMENDATION_ANSWER_USES
from dual_pipeline.l2_policy import (
    QualityFailureCode,
    SchemaFailureCode,
    analyze_request_intent,
    canonical_query_type,
)

from .constants import _REPAIR_ROW_VALUE_FIELDS, DECISION_MODEL_OUTPUT_SCHEMA
from .types import QualityDeficiency, SchemaIssue, SchemaValidationResult


def _packet_identifiers(
    packet: Mapping[str, Any],
) -> tuple[dict[str, str], dict[str, str], dict[str, set[str]], set[str]]:
    record_lanes: dict[str, str] = {}
    record_answer_uses: dict[str, str] = {}
    evidence_to_records: dict[str, set[str]] = {}
    evidence_ids: set[str] = set()
    for lane in ("approved", "candidate", "external_public"):
        values = packet.get(lane, [])
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, Mapping):
                continue
            record = item.get("record")
            if not isinstance(record, Mapping) or not isinstance(record.get("record_id"), str):
                continue
            record_id = str(record["record_id"])
            record_lanes[record_id] = lane
            metadata = item.get("retrieval_metadata")
            answer_use = (
                metadata.get("answer_use")
                if isinstance(metadata, Mapping)
                else record.get("answer_use")
            )
            if not isinstance(answer_use, str):
                answer_use = {
                    "action_item": "guidance",
                    "source_chunk": "citation_only",
                    "architecture_component": "navigation_only",
                }.get(
                    str(record.get("record_type", "")),
                    "candidate_hypothesis" if lane == "candidate" else "factual_answer",
                )
            record_answer_uses[record_id] = answer_use
            raw_evidence = record.get("evidence_ids", [])
            if isinstance(raw_evidence, list):
                for evidence_id in raw_evidence:
                    if isinstance(evidence_id, str):
                        evidence_ids.add(evidence_id)
                        evidence_to_records.setdefault(evidence_id, set()).add(record_id)
            hydrated_evidence = item.get("evidence", [])
            if isinstance(hydrated_evidence, list):
                for evidence in hydrated_evidence:
                    if isinstance(evidence, Mapping) and isinstance(
                        evidence.get("evidence_id"), str
                    ):
                        evidence_id = str(evidence["evidence_id"])
                        evidence_ids.add(evidence_id)
                        evidence_to_records.setdefault(evidence_id, set()).add(record_id)
    return record_lanes, record_answer_uses, evidence_to_records, evidence_ids


def _recover_truncated_json(value: str) -> Any | None:
    """Recover only JSON whose sole defect is missing final object/array delimiters."""

    stack: list[str] = []
    in_string = False
    escaped = False
    pairs = {"}": "{", "]": "["}
    for character in value:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "{[":
            stack.append(character)
        elif character in "}]":
            if not stack or stack.pop() != pairs[character]:
                return None
    if in_string or not stack:
        return None
    suffix = "".join("}" if opener == "{" else "]" for opener in reversed(stack))
    try:
        return json.loads(value + suffix)
    except json.JSONDecodeError:
        return None


def validate_decision_model_output(
    output: Any,
    *,
    evidence_packet: Mapping[str, Any],
    expected_query_type: str,
) -> SchemaValidationResult:
    """Validate the closed output contract and every record/evidence identifier."""

    issues: list[SchemaIssue] = []
    if isinstance(output, str):
        try:
            decoded = json.loads(output)
        except json.JSONDecodeError:
            recoverable = isinstance(_recover_truncated_json(output), Mapping)
            return SchemaValidationResult(
                value=None,
                issues=(
                    SchemaIssue(
                        SchemaFailureCode.MALFORMED_STRUCTURED_OUTPUT,
                        "$",
                        "output is not valid JSON",
                        recoverable,
                    ),
                ),
            )
    else:
        decoded = output
    if not isinstance(decoded, Mapping):
        return SchemaValidationResult(
            value=None,
            issues=(
                SchemaIssue(
                    SchemaFailureCode.SERIALIZATION_DEFECT,
                    "$",
                    "structured output must be an object",
                    True,
                ),
            ),
        )
    value = dict(decoded)
    required = set(DECISION_MODEL_OUTPUT_SCHEMA["required"])
    allowed = set(DECISION_MODEL_OUTPUT_SCHEMA["properties"])
    for name in sorted(required - set(value)):
        issues.append(
            SchemaIssue(
                SchemaFailureCode.MISSING_REQUIRED_OUTPUT_FIELD,
                f"$.{name}",
                "required output field is missing",
                True,
            )
        )
    for name in sorted(set(value) - allowed):
        issues.append(
            SchemaIssue(
                SchemaFailureCode.SERIALIZATION_DEFECT,
                f"$.{name}",
                "unknown output field is not permitted",
                True,
            )
        )
    if value.get("schema_version") != "nee-governed-answer-1.0.0":
        issues.append(
            SchemaIssue(
                SchemaFailureCode.INVALID_ENUM_VALUE,
                "$.schema_version",
                "schema_version must be nee-governed-answer-1.0.0",
                True,
            )
        )
    expected = canonical_query_type(expected_query_type)
    strategic = expected == "opportunity_nba"
    if value.get("query_type") != expected:
        issues.append(
            SchemaIssue(
                SchemaFailureCode.INVALID_ENUM_VALUE,
                "$.query_type",
                f"query_type must be {expected}",
                True,
            )
        )

    array_fields = (
        "current_state",
        "patterns",
        "pain_points",
        "opportunities",
        "guidance",
        "hypotheses",
        "source_evidence",
        "next_best_actions",
        "counterevidence",
        "missing_information",
        "assumptions",
        "what_would_change_the_conclusion",
        "proposed_assertions",
    )
    for name in array_fields:
        if name in value and not isinstance(value[name], list):
            issues.append(
                SchemaIssue(
                    SchemaFailureCode.SERIALIZATION_DEFECT,
                    f"$.{name}",
                    "field must be an array",
                    True,
                )
            )
    if "rationale" in value and not isinstance(value["rationale"], str):
        issues.append(
            SchemaIssue(
                SchemaFailureCode.SERIALIZATION_DEFECT,
                "$.rationale",
                "rationale must be a string",
                True,
            )
        )
    mutation_count = value.get("automatic_graph_mutation_count")
    if "automatic_graph_mutation_count" in value and (
        isinstance(mutation_count, bool)
        or not isinstance(mutation_count, int)
        or mutation_count != 0
    ):
        issues.append(
            SchemaIssue(
                SchemaFailureCode.GOVERNANCE_VIOLATION,
                "$.automatic_graph_mutation_count",
                "automatic graph mutation count must be zero",
                False,
            )
        )

    string_array_fields = (
        "patterns",
        "missing_information",
        "assumptions",
        "what_would_change_the_conclusion",
    )
    for name in string_array_fields:
        rows = value.get(name)
        if not isinstance(rows, list):
            continue
        for index, item in enumerate(rows):
            if not isinstance(item, str):
                issues.append(
                    SchemaIssue(
                        SchemaFailureCode.SERIALIZATION_DEFECT,
                        f"$.{name}[{index}]",
                        "array item must be a string",
                        True,
                    )
                )
            elif name in {"patterns", "missing_information"} and not item.strip():
                issues.append(
                    SchemaIssue(
                        SchemaFailureCode.GOVERNANCE_VIOLATION,
                        f"$.{name}[{index}]",
                        "material text cannot be blank",
                        False,
                    )
                )

    (
        record_lanes,
        record_answer_uses,
        evidence_to_records,
        valid_evidence_ids,
    ) = _packet_identifiers(evidence_packet)
    def object_rows(field_name: str) -> list[Mapping[str, Any]]:
        raw = value.get(field_name)
        if not isinstance(raw, list):
            return []
        rows: list[Mapping[str, Any]] = []
        for index, row in enumerate(raw):
            if not isinstance(row, Mapping):
                issues.append(
                    SchemaIssue(
                        SchemaFailureCode.SERIALIZATION_DEFECT,
                        f"$.{field_name}[{index}]",
                        "array item must be an object",
                        True,
                    )
                )
            else:
                rows.append(row)
        return rows

    row_contracts: Mapping[str, tuple[set[str], set[str], str | None]] = {
        "current_state": (
            {"statement", "citations", "evidence_ids", "lane"},
            {"statement", "citations", "evidence_ids", "lane"},
            None,
        ),
        "pain_points": (
            {"pain_point", "citations", "evidence_ids", "lane"},
            {"pain_point", "citations", "evidence_ids", "lane"},
            "approved",
        ),
        "opportunities": (
            {"opportunity", "why_prioritize", "addresses", "citations", "evidence_ids", "lane"},
            {"opportunity", "why_prioritize", "addresses", "citations", "evidence_ids", "lane"},
            None if strategic else "approved",
        ),
        "guidance": (
            {"label", "guidance", "citations", "evidence_ids", "lane"},
            {"label", "guidance", "citations", "evidence_ids", "lane"},
            "approved",
        ),
        "hypotheses": (
            {"hypothesis", "citations", "evidence_ids", "lane"},
            {"hypothesis", "citations", "evidence_ids", "lane"},
            "candidate",
        ),
        "source_evidence": (
            {"label", "excerpt", "citations", "evidence_ids", "lane"},
            {"label", "excerpt", "citations", "evidence_ids", "lane"},
            "candidate",
        ),
        "counterevidence": (
            {"statement", "citations", "evidence_ids", "lane"},
            {"statement", "citations", "evidence_ids", "lane"},
            None,
        ),
        "proposed_assertions": (
            {"proposal", "citations", "evidence_ids", "lane", "proposal_only"},
            {"proposal", "citations", "evidence_ids", "lane", "proposal_only"},
            "candidate",
        ),
    }
    row_text_fields: Mapping[str, tuple[str, ...]] = {
        "current_state": ("statement",),
        "pain_points": ("pain_point",),
        "opportunities": ("opportunity", "why_prioritize"),
        "guidance": ("guidance",),
        "hypotheses": ("hypothesis",),
        "source_evidence": ("excerpt",),
        "counterevidence": ("statement",),
        "proposed_assertions": ("proposal",),
    }
    required_answer_use = {
        "current_state": {"factual_answer"},
        "pain_points": (
            set(STRATEGIC_RECOMMENDATION_ANSWER_USES) - {"external_analogy"}
            if strategic
            else {"factual_answer"}
        ),
        "opportunities": (
            set(STRATEGIC_RECOMMENDATION_ANSWER_USES)
            if strategic
            else {"factual_answer"}
        ),
        "guidance": {"guidance"},
        "hypotheses": {"candidate_hypothesis"},
        "source_evidence": {"citation_only"},
        "counterevidence": {"factual_answer"},
        "proposed_assertions": {"candidate_hypothesis"},
    }
    for field_name, (required_keys, allowed_keys, required_lane) in row_contracts.items():
        rows = object_rows(field_name)
        for index, row in enumerate(rows):
            for name in sorted(required_keys - set(row)):
                issues.append(
                    SchemaIssue(
                        SchemaFailureCode.MISSING_REQUIRED_OUTPUT_FIELD,
                        f"$.{field_name}[{index}].{name}",
                        "required row field is missing",
                        True,
                    )
                )
            for name in sorted(set(row) - allowed_keys):
                issues.append(
                    SchemaIssue(
                        SchemaFailureCode.SERIALIZATION_DEFECT,
                        f"$.{field_name}[{index}].{name}",
                        "unknown row field is not permitted",
                        True,
                    )
                )
            for name in row_text_fields[field_name]:
                if name in row and not isinstance(row[name], str):
                    issues.append(
                        SchemaIssue(
                            SchemaFailureCode.SERIALIZATION_DEFECT,
                            f"$.{field_name}[{index}].{name}",
                            "material text must be a string",
                            True,
                        )
                    )
                elif isinstance(row.get(name), str) and not row[name].strip():
                    issues.append(
                        SchemaIssue(
                            SchemaFailureCode.GOVERNANCE_VIOLATION,
                            f"$.{field_name}[{index}].{name}",
                            "material text cannot be blank",
                            False,
                        )
                    )
            if field_name == "opportunities" and "addresses" in row:
                addresses = row["addresses"]
                if not isinstance(addresses, list) or any(
                    not isinstance(address, str) for address in addresses
                ):
                    issues.append(
                        SchemaIssue(
                            SchemaFailureCode.SERIALIZATION_DEFECT,
                            f"$.{field_name}[{index}].addresses",
                            "addresses must be an array of strings",
                            True,
                        )
                    )
                elif not addresses or any(not address.strip() for address in addresses):
                    issues.append(
                        SchemaIssue(
                            SchemaFailureCode.GOVERNANCE_VIOLATION,
                            f"$.{field_name}[{index}].addresses",
                            "opportunity addresses must contain nonblank material text",
                            False,
                        )
                    )
            if field_name == "guidance" and row.get("label") != "GUIDANCE_NOT_FACT":
                issues.append(
                    SchemaIssue(
                        SchemaFailureCode.GOVERNANCE_VIOLATION,
                        f"$.{field_name}[{index}].label",
                        "guidance must be explicitly labeled GUIDANCE_NOT_FACT",
                        False,
                    )
                )
            if (
                field_name == "source_evidence"
                and row.get("label") != "CANDIDATE_SOURCE_EVIDENCE"
            ):
                issues.append(
                    SchemaIssue(
                        SchemaFailureCode.GOVERNANCE_VIOLATION,
                        f"$.{field_name}[{index}].label",
                        "citation-only source evidence must be labeled CANDIDATE_SOURCE_EVIDENCE",
                        False,
                    )
                )
            lane = row.get("lane")
            if lane not in {"approved", "candidate", "external_public"} or (
                required_lane is not None and lane != required_lane
            ) or (
                strategic
                and field_name == "opportunities"
                and lane not in {"approved", "external_public"}
            ):
                issues.append(
                    SchemaIssue(
                        SchemaFailureCode.INVALID_ENUM_VALUE,
                        f"$.{field_name}[{index}].lane",
                        "row evidence lane is invalid",
                        True,
                    )
                )
            citations = row.get("citations")
            evidence_ids = row.get("evidence_ids")
            if not isinstance(citations, list) or any(
                not isinstance(citation, str) for citation in citations
            ):
                issues.append(
                    SchemaIssue(
                        SchemaFailureCode.SERIALIZATION_DEFECT,
                        f"$.{field_name}[{index}].citations",
                        "citations must be an array of strings",
                        True,
                    )
                )
                citations = []
            if not isinstance(evidence_ids, list) or any(
                not isinstance(evidence_id, str) for evidence_id in evidence_ids
            ):
                issues.append(
                    SchemaIssue(
                        SchemaFailureCode.SERIALIZATION_DEFECT,
                        f"$.{field_name}[{index}].evidence_ids",
                        "evidence_ids must be an array of strings",
                        True,
                    )
                )
                evidence_ids = []
            valid_row_evidence_ids = {
                evidence_id
                for evidence_id in evidence_ids
                if evidence_id in valid_evidence_ids
            }
            intended_records = {
                record_id
                for evidence_id in valid_row_evidence_ids
                for record_id in evidence_to_records.get(evidence_id, set())
            }
            citations_derivable = bool(valid_row_evidence_ids) and all(
                len(
                    {
                        record_id
                        for record_id in evidence_to_records.get(evidence_id, set())
                        if (
                            not isinstance(lane, str)
                            or (strategic and field_name == "opportunities")
                            or record_lanes.get(record_id) == lane
                        )
                    }
                )
                == 1
                for evidence_id in valid_row_evidence_ids
            )
            allowed_record_text = ",".join(sorted(intended_records))
            if not citations:
                issues.append(
                    SchemaIssue(
                        SchemaFailureCode.INVALID_CITATION_WIRING,
                        f"$.{field_name}[{index}].citations",
                        "a material output row must cite a sealed record"
                        + (
                            f"; allowed_record_ids={allowed_record_text}"
                            if citations_derivable
                            else ""
                        ),
                        citations_derivable,
                    )
                )
            if not evidence_ids:
                issues.append(
                    SchemaIssue(
                        SchemaFailureCode.INVALID_CITATION_WIRING,
                        f"$.{field_name}[{index}].evidence_ids",
                        "a material output row must include a sealed evidence identifier",
                        False,
                    )
                )
            for citation_index, citation in enumerate(citations):
                citation_lane = record_lanes.get(citation)
                if citation_lane is None or (
                    isinstance(lane, str)
                    and citation_lane != lane
                    and not (strategic and field_name == "opportunities")
                ):
                    issues.append(
                        SchemaIssue(
                            SchemaFailureCode.INVALID_CITATION_WIRING,
                            f"$.{field_name}[{index}].citations[{citation_index}]",
                            "citation does not resolve to the sealed packet and declared lane"
                            + (
                                f"; allowed_record_ids={allowed_record_text}"
                                if intended_records
                                else ""
                            ),
                            bool(intended_records),
                        )
                    )
                elif citation not in intended_records:
                    issues.append(
                        SchemaIssue(
                            SchemaFailureCode.INVALID_CITATION_WIRING,
                            f"$.{field_name}[{index}].citations[{citation_index}]",
                            "citation is not supported by this row's sealed evidence identifiers",
                            False,
                        )
                    )
                elif (
                    record_answer_uses.get(citation)
                    not in required_answer_use[field_name]
                ):
                    issues.append(
                        SchemaIssue(
                            SchemaFailureCode.GOVERNANCE_VIOLATION,
                            f"$.{field_name}[{index}].citations[{citation_index}]",
                            "citation answer-use does not match the output field",
                            False,
                        )
                    )
            for evidence_index, evidence_id in enumerate(evidence_ids):
                if evidence_id not in valid_evidence_ids:
                    issues.append(
                        SchemaIssue(
                            SchemaFailureCode.INVALID_CITATION_WIRING,
                            f"$.{field_name}[{index}].evidence_ids[{evidence_index}]",
                            "evidence identifier does not resolve to the sealed packet",
                            bool(valid_row_evidence_ids),
                        )
                    )
                elif not (evidence_to_records.get(evidence_id, set()) & set(citations)):
                    issues.append(
                        SchemaIssue(
                            SchemaFailureCode.INVALID_CITATION_WIRING,
                            f"$.{field_name}[{index}].evidence_ids[{evidence_index}]",
                            "evidence identifier does not support a cited record in this row"
                            + (
                                f"; allowed_record_ids={allowed_record_text}"
                                if citations_derivable
                                else ""
                            ),
                            citations_derivable,
                        )
                    )
            if (
                field_name == "proposed_assertions"
                and "proposal_only" in row
                and row.get("proposal_only") is not True
            ):
                issues.append(
                    SchemaIssue(
                        SchemaFailureCode.GOVERNANCE_VIOLATION,
                        f"$.{field_name}[{index}].proposal_only",
                        "candidate promotion output must remain proposal-only",
                        False,
                    )
                )

    actions = object_rows("next_best_actions")
    for index, row in enumerate(actions):
        required_action = {"action", "basis_citations"}
        for name in sorted(required_action - set(row)):
            issues.append(
                SchemaIssue(
                    SchemaFailureCode.MISSING_REQUIRED_OUTPUT_FIELD,
                    f"$.next_best_actions[{index}].{name}",
                    "required action field is missing",
                    True,
                )
            )
        for name in sorted(set(row) - required_action):
            issues.append(
                SchemaIssue(
                    SchemaFailureCode.SERIALIZATION_DEFECT,
                    f"$.next_best_actions[{index}].{name}",
                    "unknown action field is not permitted",
                    True,
                )
            )
        if "action" in row and not isinstance(row["action"], str):
            issues.append(
                SchemaIssue(
                    SchemaFailureCode.SERIALIZATION_DEFECT,
                    f"$.next_best_actions[{index}].action",
                    "action must be a string",
                    True,
                )
            )
        elif isinstance(row.get("action"), str) and not row["action"].strip():
            issues.append(
                SchemaIssue(
                    SchemaFailureCode.GOVERNANCE_VIOLATION,
                    f"$.next_best_actions[{index}].action",
                    "action cannot be blank",
                    False,
                )
            )
        citations = row.get("basis_citations")
        if not isinstance(citations, list) or any(
            not isinstance(citation, str) for citation in citations
        ):
            issues.append(
                SchemaIssue(
                    SchemaFailureCode.SERIALIZATION_DEFECT,
                    f"$.next_best_actions[{index}].basis_citations",
                    "basis_citations must be an array of strings",
                    True,
                )
            )
        else:
            if not citations:
                issues.append(
                    SchemaIssue(
                        SchemaFailureCode.INVALID_CITATION_WIRING,
                        f"$.next_best_actions[{index}].basis_citations",
                        "a proposed action must cite a sealed record",
                        False,
                    )
                )
            for citation_index, citation in enumerate(citations):
                if citation not in record_lanes:
                    issues.append(
                        SchemaIssue(
                            SchemaFailureCode.INVALID_CITATION_WIRING,
                            f"$.next_best_actions[{index}].basis_citations[{citation_index}]",
                            "action citation does not resolve to the sealed packet",
                            False,
                        )
                    )
                elif (
                    record_lanes[citation]
                    not in (
                        {"approved", "external_public"}
                        if strategic
                        else {"approved"}
                    )
                    or record_answer_uses.get(citation)
                    not in (
                        STRATEGIC_RECOMMENDATION_ANSWER_USES
                        if strategic
                        else {"factual_answer"}
                    )
                ):
                    issues.append(
                        SchemaIssue(
                            SchemaFailureCode.GOVERNANCE_VIOLATION,
                            f"$.next_best_actions[{index}].basis_citations[{citation_index}]",
                            "next-best action citation has an ineligible lane or answer-use",
                            False,
                        )
                    )
    return SchemaValidationResult(value=value, issues=tuple(issues))



def _repair_atom(value: Any) -> str | None:
    if isinstance(value, str):
        return value if value else None
    if value is not None and not isinstance(value, (bool, Mapping, list)):
        return canonical_json(value)
    return None


def _repair_role_values(value: Any) -> dict[str, set[str]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = _recover_truncated_json(value)
    if not isinstance(value, Mapping):
        return {}
    roles: dict[str, set[str]] = {}

    def add(role: str, raw: Any) -> None:
        values = raw if isinstance(raw, list) else [raw]
        for item in values:
            atom = _repair_atom(item)
            if atom is not None:
                roles.setdefault(role, set()).add(atom)

    add("rationale", value.get("rationale"))
    for field_name in (
        "patterns",
        "missing_information",
        "assumptions",
        "what_would_change_the_conclusion",
    ):
        add(field_name, value.get(field_name, []))
    for section, fields in _REPAIR_ROW_VALUE_FIELDS.items():
        rows = value.get(section, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            for field_name in fields:
                add(f"{section}.{field_name}", row.get(field_name, []))
    return roles


def _schema_repair_preserves_facts(
    *,
    original_response: Any,
    repaired_output: Mapping[str, Any],
    evidence_packet: Mapping[str, Any],
) -> bool:
    original_roles = _repair_role_values(original_response)
    repaired_roles = _repair_role_values(repaired_output)
    _, _, evidence_to_records, valid_evidence_ids = _packet_identifiers(evidence_packet)
    for role, repaired_values in repaired_roles.items():
        allowed = set(original_roles.get(role, set()))
        if role.endswith(".citations"):
            evidence_role = role.removesuffix(".citations") + ".evidence_ids"
            for evidence_id in original_roles.get(evidence_role, set()) & valid_evidence_ids:
                allowed.update(evidence_to_records.get(evidence_id, set()))
        if not repaired_values <= allowed:
            return False
    return True



def _required_material_record_ids_by_lane(
    evidence_packet: Mapping[str, Any],
) -> dict[str, tuple[str, ...]]:
    raw = evidence_packet.get("required_material_record_ids_by_lane", {})
    if not isinstance(raw, Mapping):
        return {"approved": (), "candidate": (), "external_public": ()}
    return {
        lane: tuple(
            dict.fromkeys(
                value
                for value in raw.get(lane, [])
                if isinstance(value, str) and value
            )
        )
        if isinstance(raw.get(lane, []), list)
        else ()
        for lane in ("approved", "candidate", "external_public")
    }


def evaluate_semantic_quality(
    *,
    question: str,
    plan: Mapping[str, Any],
    evidence_packet: Mapping[str, Any],
    answer: Mapping[str, Any],
) -> tuple[QualityDeficiency, ...]:
    """Apply typed deterministic obligations; no model evaluates another model."""

    deficiencies: list[QualityDeficiency] = []
    query_type = canonical_query_type(str(plan["query_type"]))
    intent = analyze_request_intent(question)
    approved_count = len(evidence_packet.get("approved", []))
    candidate_count = len(evidence_packet.get("candidate", []))
    _record_lanes, record_answer_uses, _evidence_to_records, _evidence_ids = (
        _packet_identifiers(evidence_packet)
    )
    candidate_record_ids = [
        str(item["record"]["record_id"])
        for item in evidence_packet.get("candidate", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("record"), Mapping)
        and isinstance(item["record"].get("record_id"), str)
    ]
    candidate_source_evidence_count = sum(
        record_answer_uses.get(record_id) == "citation_only"
        for record_id in candidate_record_ids
    )
    candidate_hypothesis_count = sum(
        record_answer_uses.get(record_id) == "candidate_hypothesis"
        for record_id in candidate_record_ids
    )
    conflicts = evidence_packet.get("counterevidence", evidence_packet.get("conflicts", []))
    conflict_count = len(conflicts) if isinstance(conflicts, list) else 0
    graph_paths = evidence_packet.get("graph_paths", [])
    graph_path_count = len(graph_paths) if isinstance(graph_paths, list) else 0

    current_state = answer.get("current_state", [])
    patterns = answer.get("patterns", [])
    pain_points = answer.get("pain_points", [])
    opportunities = answer.get("opportunities", [])
    hypotheses = answer.get("hypotheses", [])
    source_evidence = answer.get("source_evidence", [])
    next_actions = answer.get("next_best_actions", [])
    counterevidence = answer.get("counterevidence", [])
    assumptions = answer.get("assumptions", [])
    what_changes = answer.get("what_would_change_the_conclusion", [])
    proposed = answer.get("proposed_assertions", [])
    rationale = str(answer.get("rationale", "")).strip()

    def meaningful_strings(values: Any) -> bool:
        return isinstance(values, list) and any(
            isinstance(value, str) and bool(value.strip()) for value in values
        )

    def meaningful_rows(values: Any, *text_fields: str) -> list[Mapping[str, Any]]:
        if not isinstance(values, list):
            return []
        return [
            row
            for row in values
            if isinstance(row, Mapping)
            and all(
                isinstance(row.get(field), str) and bool(str(row[field]).strip())
                for field in text_fields
            )
            and isinstance(row.get("citations"), list)
            and bool(row["citations"])
            and isinstance(row.get("evidence_ids"), list)
            and bool(row["evidence_ids"])
        ]

    meaningful_current = meaningful_rows(current_state, "statement")
    meaningful_patterns = meaningful_strings(patterns)
    meaningful_pain = meaningful_rows(pain_points, "pain_point")
    meaningful_opportunities = [
        row
        for row in meaningful_rows(opportunities, "opportunity", "why_prioritize")
        if meaningful_strings(row.get("addresses"))
    ]
    meaningful_hypotheses = meaningful_rows(hypotheses, "hypothesis")
    meaningful_source_evidence = meaningful_rows(source_evidence, "excerpt")
    meaningful_counterevidence = meaningful_rows(counterevidence, "statement")
    meaningful_proposed = meaningful_rows(proposed, "proposal")
    meaningful_actions = [
        row
        for row in next_actions
        if isinstance(row, Mapping)
        and isinstance(row.get("action"), str)
        and bool(str(row["action"]).strip())
        and isinstance(row.get("basis_citations"), list)
        and bool(row["basis_citations"])
    ] if isinstance(next_actions, list) else []
    evidence_rows = (
        meaningful_current
        + meaningful_pain
        + meaningful_opportunities
        + meaningful_hypotheses
        + meaningful_source_evidence
        + meaningful_counterevidence
        + meaningful_proposed
    )
    cited_record_ids = {
        citation
        for row in evidence_rows
        for citation in row.get("citations", [])
        if isinstance(citation, str)
    }

    route_answered = {
        "current_state_corroboration": bool(meaningful_current) or approved_count == 0,
        "project_status_current": bool(meaningful_source_evidence) or candidate_count == 0,
        "project_status_history": bool(meaningful_source_evidence) or candidate_count == 0,
        "relationship_explanation": bool(meaningful_current),
        "cross_workstream_synthesis": bool(meaningful_current) and meaningful_patterns,
        "gap_risk_inconsistency": bool(meaningful_pain or meaningful_counterevidence),
        "opportunity_nba": bool(meaningful_opportunities),
        "candidate_promotion_review": bool(
            meaningful_hypotheses or meaningful_source_evidence or meaningful_proposed
        )
        or (candidate_count == 0 and bool(meaningful_current)),
        "exact_lookup": True,
    }[query_type]
    if not route_answered:
        deficiencies.append(
            QualityDeficiency(
                QualityFailureCode.MATERIAL_QUERY_COMPONENT_NOT_ANSWERED,
                "route_output",
                "the route-specific material answer component is empty",
            )
        )
    if query_type != "exact_lookup" and not rationale:
        deficiencies.append(
            QualityDeficiency(
                QualityFailureCode.MATERIAL_QUERY_COMPONENT_NOT_ANSWERED,
                "rationale",
                "required semantic synthesis rationale is empty",
            )
        )
    required_material_by_lane = _required_material_record_ids_by_lane(evidence_packet)
    required_material_ids = {
        record_id
        for record_ids in required_material_by_lane.values()
        for record_id in record_ids
    }
    missing_material_ids = sorted(required_material_ids - cited_record_ids)
    if missing_material_ids:
        deficiencies.append(
            QualityDeficiency(
                QualityFailureCode.MATERIAL_QUERY_COMPONENT_NOT_ANSWERED,
                "evidence_coverage",
                "required route-specific material evidence is not covered: "
                + ", ".join(missing_material_ids),
                required_record_ids=tuple(sorted(required_material_ids)),
                missing_record_ids=tuple(missing_material_ids),
            )
        )
    if conflict_count > 0 and not meaningful_counterevidence:
        deficiencies.append(
            QualityDeficiency(
                QualityFailureCode.MATERIAL_COUNTEREVIDENCE_NOT_ADDRESSED,
                "counterevidence",
                "sealed material counterevidence is not addressed",
            )
        )
    requires_recommendation_action = (
        (intent.recommendation or intent.prioritization or query_type == "opportunity_nba")
        and query_type != "candidate_promotion_review"
    )
    requires_opportunity = query_type == "opportunity_nba"
    if requires_recommendation_action and (
        not meaningful_actions
        or not rationale
        or (requires_opportunity and not meaningful_opportunities)
    ):
        deficiencies.append(
            QualityDeficiency(
                QualityFailureCode.RECOMMENDATION_RATIONALE_INCOMPLETE,
                "rationale",
                "a requested recommendation lacks a complete rationale or next action",
            )
        )
    if conflict_count > 0 and (
        not meaningful_counterevidence or not meaningful_strings(what_changes)
    ):
        deficiencies.append(
            QualityDeficiency(
                QualityFailureCode.MATERIAL_CONFLICT_NOT_RECONCILED,
                "counterevidence",
                "material conflict posture and what-would-change conditions are incomplete",
            )
        )
    material = str(plan.get("materiality", "medium")) in {"medium", "high"}
    if material and not meaningful_strings(assumptions):
        deficiencies.append(
            QualityDeficiency(
                QualityFailureCode.REQUIRED_ASSUMPTIONS_NOT_DISCLOSED,
                "assumptions",
                "material synthesis must disclose assumptions",
            )
        )
    if (
        material or query_type in {"gap_risk_inconsistency", "opportunity_nba"}
    ) and not meaningful_strings(what_changes):
        deficiencies.append(
            QualityDeficiency(
                QualityFailureCode.REQUIRED_WHAT_WOULD_CHANGE_MISSING,
                "what_would_change_the_conclusion",
                "material synthesis must state what would change the conclusion",
            )
        )
    candidate_material = query_type == "candidate_promotion_review" or bool(
        plan.get("candidate_synthesis_required", False)
    )
    if candidate_hypothesis_count > 0 and candidate_material:
        candidate_rows = [
            (row, "hypothesis") for row in meaningful_hypotheses
        ] + [(row, "proposal") for row in meaningful_proposed]
        labeled = bool(candidate_rows) and all(
            str(row.get(text_field, "")).startswith("CANDIDATE:")
            and row.get("lane") == "candidate"
            and (text_field != "proposal" or row.get("proposal_only") is True)
            for row, text_field in candidate_rows
        )
        if not labeled:
            deficiencies.append(
                QualityDeficiency(
                    QualityFailureCode.MATERIAL_CANDIDATE_POSTURE_NOT_EXPLICIT,
                    "hypotheses_or_proposed_assertions",
                    "material candidate evidence is not explicitly labeled as candidate-only",
                )
            )
    if candidate_source_evidence_count > 0 and candidate_material:
        source_labeled = all(
            isinstance(row, Mapping)
            and row.get("label") == "CANDIDATE_SOURCE_EVIDENCE"
            and row.get("lane") == "candidate"
            for row in meaningful_source_evidence
        )
        if not meaningful_source_evidence or not source_labeled:
            deficiencies.append(
                QualityDeficiency(
                    QualityFailureCode.MATERIAL_CANDIDATE_POSTURE_NOT_EXPLICIT,
                    "source_evidence",
                    "citation-only candidate source evidence must be explicitly labeled",
                )
            )
    if query_type == "relationship_explanation" and graph_path_count > 0:
        path_record_ids = [
            str(path.get("record_id"))
            for path in graph_paths
            if isinstance(path, Mapping)
            and path.get("lane") == "approved"
            and path.get("record_id")
        ][:2]
        cited = {
            citation
            for row in meaningful_current
            if isinstance(row, Mapping)
            for citation in row.get("citations", [])
            if isinstance(citation, str)
        }
        if path_record_ids and not set(path_record_ids) <= cited:
            deficiencies.append(
                QualityDeficiency(
                    QualityFailureCode.MATERIAL_GRAPH_PATH_NOT_EXPLAINED,
                    "current_state",
                    "no material governed graph path is cited or explained",
                )
            )
    if isinstance(current_state, list) and current_state:
        uncited_statements = [
            row
            for row in current_state
            if isinstance(row, Mapping)
            and isinstance(row.get("statement"), str)
            and bool(str(row["statement"]).strip())
            and not (
                isinstance(row.get("citations"), list)
                and any(isinstance(c, str) and bool(c.strip()) for c in row["citations"])
            )
        ]
        if uncited_statements:
            deficiencies.append(
                QualityDeficiency(
                    QualityFailureCode.CITATION_DENSITY_INSUFFICIENT,
                    "current_state",
                    "substantive current-state claims must cite approved evidence records",
                )
            )
    if conflict_count > 0 and meaningful_counterevidence:
        falsification_addressed = meaningful_strings(what_changes) and any(
            any(
                kw in str(wc).casefold()
                for kw in ("conflict", "contradict", "counter", "falsif", "dispute", "differ", "reconcil")
            )
            or any(
                str(conf.get("record_id", "")) in str(wc)
                for conf in conflicts
                if isinstance(conf, Mapping) and conf.get("record_id")
            )
            for wc in what_changes
        )
        if not falsification_addressed:
            deficiencies.append(
                QualityDeficiency(
                    QualityFailureCode.UNRESOLVED_CONTRADICTION_POSTURE,
                    "what_would_change_the_conclusion",
                    "what-would-change conditions must address the falsification criteria for sealed counterevidence",
                )
            )
    if isinstance(next_actions, list) and next_actions:
        infeasible_actions = [
            row
            for row in next_actions
            if isinstance(row, Mapping)
            and (
                not isinstance(row.get("action"), str)
                or not bool(str(row["action"]).strip())
                or not (
                    isinstance(row.get("basis_citations"), list)
                    and any(isinstance(c, str) and bool(c.strip()) for c in row["basis_citations"])
                )
            )
        ]
        if infeasible_actions:
            deficiencies.append(
                QualityDeficiency(
                    QualityFailureCode.ACTION_FEASIBILITY_INCOMPLETE,
                    "next_best_actions",
                    "proposed next-best actions must carry actionable text and valid basis citations",
                )
            )
    return tuple(deficiencies)

