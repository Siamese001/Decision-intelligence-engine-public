"""Ingestion decision ledger, explanation artifact, and Markdown reporting."""

from __future__ import annotations

import json
from typing import Any

from dual_pipeline.contracts import create_artifact, validate_artifact
from dual_pipeline.core import digest, stable_id

from .constants import (
    GRAPH_ADMISSION_EXTENSION_KEY,
    IDENTITY_REVIEW_EXTENSION_KEY,
    INGESTION_DECISION_LEDGER_JSON,
)
from .proposals import _candidate_resolution_map


def _markdown_table_cell(value: object) -> str:
    """Render a single review value safely into a compact Markdown cell."""

    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _decision_gate_status(value: bool, *, required: bool = True) -> str:
    if not required:
        return "NOT_REQUIRED"
    return "PASS" if value else "BLOCKED"


def build_ingestion_decision_ledger(
    bundle: dict[str, Any],
    routes: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
    dispositions: list[dict[str, Any]],
    change_set: dict[str, Any] | None,
    graph_delta: dict[str, Any],
) -> dict[str, Any]:
    """Build the required, source-bound decision record for an intake run.

    This record is deliberately separate from both candidate extraction and the
    GraphDB delta.  It joins the actual score values, every graph-admission
    gate, available human labels, and the proposed operation so an analyst can
    distinguish "not a known identity" from "not eligible to write."  It is
    computed before release and cannot authorize a graph mutation itself.
    """

    routes_by_candidate = {value["subject_id"]: value for value in routes}
    resolutions_by_candidate = _candidate_resolution_map(bundle, resolutions)
    dispositions_by_candidate = {value["candidate_id"]: value for value in dispositions}
    operations_by_candidate: dict[str, list[dict[str, Any]]] = {
        candidate["candidate_id"]: [] for candidate in bundle["candidates"]
    }
    for operation in (change_set or {}).get("operations", []):
        operations_by_candidate.setdefault(operation["candidate_id"], []).append(
            {
                "operation_id": operation["operation_id"],
                "operation": operation["operation"],
                "target_type": operation["target_type"],
                "target_id": operation["target_id"],
                "sequence": operation["sequence"],
            }
        )

    rows: list[dict[str, Any]] = []
    for candidate in bundle["candidates"]:
        candidate_id = candidate["candidate_id"]
        route = routes_by_candidate[candidate_id]
        disposition = dispositions_by_candidate[candidate_id]
        admission = disposition["extensions"][GRAPH_ADMISSION_EXTENSION_KEY]
        inputs = admission["decision_inputs"]
        identities: list[dict[str, Any]] = []
        label_options: list[dict[str, Any]] = []
        for resolution in resolutions_by_candidate[candidate_id]:
            review = resolution["extensions"][IDENTITY_REVIEW_EXTENSION_KEY]
            identities.append(
                {
                    "resolution_id": resolution["resolution_id"],
                    "reported_label": resolution["reported_name"],
                    "automated_outcome": review["automated_outcome"],
                    "selected_entity_id": resolution["selected_entity_id"],
                    "decision_basis": review["decision_basis"],
                    "confidence": review["confidence"],
                    "possible_duplicates": resolution["alternatives"],
                    "new_node_assessment": resolution.get("extensions", {}).get(
                        "com.partner_advisory.nee.new-node-assessment"
                    ),
                }
            )
            label_options.extend(review["label_options"])
        gate_checks = {
            "identity": _decision_gate_status(inputs["identity_resolved"]),
            "evidence": _decision_gate_status(inputs["evidence_count"] > 0),
            "classification": _decision_gate_status(inputs["classification_valid"]),
            "audience": _decision_gate_status(inputs["audience_valid"]),
            "contradiction": _decision_gate_status(
                inputs["material_contradiction_resolved"]
            ),
            "hitl": _decision_gate_status(
                inputs["hitl_complete"], required=inputs["hitl_required"]
            ),
            "approved_fact_authority": _decision_gate_status(
                inputs["source_authority_sufficient_for_approved_fact"],
                required=admission["current_disposition"] == "APPROVED",
            ),
        }
        operations = operations_by_candidate[candidate_id]
        rows.append(
            {
                "candidate_id": candidate_id,
                "source": {
                    "locator": candidate["source_locator"],
                    "evidence_ids": candidate["evidence_ids"],
                    "candidate_type": candidate["candidate_type"],
                    "assertion_type": candidate["assertion_type"],
                    "extraction_confidence": candidate["extraction_confidence"],
                    "classification": candidate["classification"],
                    "audiences": candidate["audiences"],
                },
                "identity": identities,
                "route": {
                    "intake_route": route["intake_route"],
                    "reason_codes": route["reason_codes"],
                },
                "graph_admission": {
                    "disposition": admission["current_disposition"],
                    "graph_action": admission["current_graph_action"],
                    "graph_write_eligible": admission["current_graph_write_allowed"],
                    "decision_reasons": admission["decision_reasons"],
                    "gate_checks": gate_checks,
                    "authority_rank": inputs["source_authority_rank"],
                    "approved_fact_authority_threshold": inputs[
                        "approved_fact_authority_threshold"
                    ],
                },
                "label_options": label_options,
                "planned_graph_operations": operations,
                "release_state": (
                    "PROPOSED_OPERATION_REQUIRES_RELEASE_APPROVAL"
                    if operations
                    else "NO_GRAPH_OPERATION_PROPOSED"
                ),
                "next_actions": admission["next_actions"],
            }
        )
    if len(rows) != len(bundle["candidates"]):
        raise RuntimeError("decision ledger must account for every intake candidate")
    return {
        "schema_version": "1.0.0",
        "ledger_type": "ingestion_decision_ledger",
        "run_id": bundle["run_id"],
        "trace_id": bundle["trace_id"],
        "source_id": bundle["source_id"],
        "source_version_id": bundle["source_version_id"],
        "candidate_count": len(rows),
        "planned_graph_delta_digest": digest(graph_delta),
        "planned_graph_operation_count": graph_delta["operation_count"],
        "planned_graph_operation_counts": graph_delta["operation_counts"],
        "decision_model": {
            "identity": (
                "A label may auto-link only for a unique exact stable identifier, "
                "canonical name, or governed alias. Similarity ranks possible duplicates "
                "for review and never selects or merges an identity."
            ),
            "graph_admission": (
                "An identity result is only one gate. Evidence, classification, audience, "
                "contradiction, authority, and required HITL controls independently affect "
                "whether a candidate can be staged."
            ),
            "release": (
                "A planned operation is not a graph mutation. It remains proposed until the "
                "separately governed release is approved and activated."
            ),
        },
        "rows": rows,
    }


_PASSED_LABELS = {
    "identity": "exact existing identity",
    "evidence": "source evidence is present",
    "classification": "security classification is valid",
    "audience": "an allowed audience is present",
    "contradiction": "no unresolved contradiction blocks the item",
    "hitl": "required human review is complete",
    "approved_fact_authority": "approved-fact authority is sufficient",
}


def _default_no_entity_identity(ledger: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    return {
        "resolution_id": None,
        "reported_label": row["source"]["candidate_type"].replace("_", " "),
        "automated_outcome": "NO_ENTITY_MENTION",
        "selected_entity_id": None,
        "decision_basis": ["This source candidate contains no entity mention that requires identity resolution"],
        "confidence": {},
        "possible_duplicates": [],
        "new_node_assessment": {
            "group_id": stable_id("identity-decision-group", ledger["source_id"], row["candidate_id"], "no-entity-mention", length=26),
            "status": "NO_ENTITY_MENTION",
            "declared_node_type": None,
            "proposal_digest": None,
            "reasons": ["No existing-node link or new-node assessment was needed"],
        },
    }


def _collect_decision_groups_map(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in ledger["rows"]:
        identities = row["identity"] or [_default_no_entity_identity(ledger, row)]
        for identity in identities:
            assessment = identity.get("new_node_assessment") or {}
            group_id = assessment.get("group_id") or stable_id(
                "identity-decision-group", ledger["source_id"], identity["resolution_id"], length=26
            )
            group = groups.setdefault(
                group_id,
                {"candidate_ids": set(), "locations": {}, "resolutions": [], "label_options": [], "gate_checks": [], "operations": []},
            )
            group["candidate_ids"].add(row["candidate_id"])
            locator = row["source"]["locator"]
            group["locations"][json.dumps(locator, sort_keys=True)] = locator
            group["resolutions"].append(identity)
            group["label_options"].extend(row["label_options"])
            group["gate_checks"].append(row["graph_admission"]["gate_checks"])
            group["operations"].extend(row["planned_graph_operations"])
    return groups


def _compute_recognition_text(
    source_only: bool, exact_found: bool, assessment_status: str | None, new_recommended: bool
) -> str:
    if source_only:
        return "The source explicitly says this anchor is evidence-only. The pipeline did not search for or propose a knowledge node."
    if exact_found:
        return "The pipeline found one exact governed identity using a stable identifier, canonical name, or governed alias."
    if assessment_status == "NO_ENTITY_MENTION":
        return "This source candidate contains no entity mention, so the pipeline did not perform an identity or new-node decision."
    if new_recommended:
        return "The pipeline found no exact existing identity and built one complete, source-bound candidate-node proposal for review."
    return "The pipeline did not find one exact existing identity. It stopped for review instead of using name similarity as identity authority."


def _compute_missing_info(
    exact_found: bool, source_only: bool, assessment_status: str | None, gate_checks: list[dict[str, str]]
) -> list[str]:
    missing: list[str] = []
    if not exact_found and not source_only:
        missing.append("one exact existing identity")
    if assessment_status == "TYPE_REVIEW_REQUIRED":
        missing.append("a valid active node type declared by a new source-bound package")
    if any(checks.get("hitl") == "BLOCKED" for checks in gate_checks):
        missing.append("the grouped human review decision")
    return sorted(set(missing))


def _build_reviewer_choices(
    label_options: list[dict[str, Any]],
    candidate_count: int,
    possible_duplicates: dict[str, dict[str, Any]],
    exact_found: bool,
    source_only: bool,
    new_recommended: bool,
    proposal_digest: str | None,
) -> list[dict[str, Any]]:
    option_map: dict[str, dict[str, Any]] = {}
    for option in label_options:
        dec = option.get("decision")
        if dec in {"CONFIRM", "CREATE_NEW", "HOLD", "REJECT"} and (dec not in option_map or option.get("available") is True):
            option_map[dec] = option
    default_effects = {
        "CONFIRM": "Link the grouped source candidates to the selected existing node, then propose eligible candidate assertions; release approval and activation are still required.",
        "CREATE_NEW": f"Propose exactly one candidate entity plus up to {candidate_count} bound candidate assertions; the active graph stays unchanged until release activation.",
        "HOLD": "Retain the review evidence and propose no graph operation.",
        "REJECT": "Reject the grouped candidates and propose no graph operation.",
    }
    avail = {
        "CONFIRM": bool(possible_duplicates) and not exact_found and not source_only,
        "CREATE_NEW": new_recommended and isinstance(proposal_digest, str),
        "HOLD": not exact_found and not source_only,
        "REJECT": not exact_found and not source_only,
    }
    return [
        {
            "decision": dec,
            "available": avail[dec],
            "effect": default_effects[dec],
            "reason": option_map.get(dec, {}).get("reason") or ("This choice is not applicable to the deterministic outcome." if not avail[dec] else "This choice is available for the grouped review item."),
        }
        for dec in ("CONFIRM", "CREATE_NEW", "HOLD", "REJECT")
    ]


def _build_single_decision_group_entry(group_id: str, values: dict[str, Any]) -> dict[str, Any]:
    resolutions = values["resolutions"]
    first = resolutions[0]
    assessment = first.get("new_node_assessment") or {}
    statuses = [(r.get("new_node_assessment") or {}).get("status") for r in resolutions]
    exact_found = all(r["automated_outcome"] == "AUTO_LINK_EXACT" for r in resolutions)
    new_recommended = "PROPOSE_NEW_CANDIDATE" in statuses
    source_only = all(s == "SOURCE_ONLY" for s in statuses)
    recognition = _compute_recognition_text(source_only, exact_found, assessment.get("status"), new_recommended)
    possible_duplicates = {
        alt["entity_id"]: {"entity_id": alt["entity_id"], "label": alt["canonical_name"], "similarity_percent": round(alt["match_score"] * 100, 1), "evidence_label": "possible duplicate evidence only"}
        for r in resolutions for alt in r.get("possible_duplicates", [])
    }
    checks_passed = sorted({_PASSED_LABELS[k] for checks in values["gate_checks"] for k, st in checks.items() if st == "PASS"})
    missing_info = _compute_missing_info(exact_found, source_only, assessment.get("status"), values["gate_checks"])
    proposal_digest = next((d for r in resolutions if isinstance(d := (r.get("new_node_assessment") or {}).get("proposal_digest"), str)), assessment.get("proposal_digest"))
    reviewer_choices = _build_reviewer_choices(
        values["label_options"], len(values["candidate_ids"]), possible_duplicates,
        exact_found, source_only, new_recommended, proposal_digest,
    )
    operations = values["operations"]
    graph_effect = (
        "No graph operation is proposed before the required review decision."
        if not operations
        else "Proposed operations are sealed for release review but have not changed the active graph: "
        + ", ".join(sorted({v["operation"] for v in operations})) + "."
    )
    return {
        "group_id": group_id,
        "candidate_ids": sorted(values["candidate_ids"]),
        "affected_source_locations": sorted(values["locations"].values(), key=lambda v: (v.get("kind", ""), v.get("value", ""))),
        "technical_resolution_ids": sorted({v["resolution_id"] for v in resolutions if isinstance(v.get("resolution_id"), str)}),
        "reported_label": first["reported_label"],
        "pipeline_recognition": recognition,
        "exact_identity_found": exact_found,
        "exact_identity_explanation": f"Yes. The exact governed node is {first['selected_entity_id']}." if exact_found else "No. Similar-looking labels are not exact identity evidence.",
        "new_node_recommended": new_recommended,
        "new_node_explanation": f"Yes. The declared type is active, the source identifier is present, and no exact existing identity was found. Human CREATE_NEW approval must bind the unchanged proposal digest {proposal_digest}." if new_recommended else "No. " + "; ".join(assessment.get("reasons", first["decision_basis"])),
        "declared_node_type": assessment.get("declared_node_type"),
        "proposal_digest": proposal_digest,
        "possible_duplicates": sorted(possible_duplicates.values(), key=lambda v: (-v["similarity_percent"], v["label"])),
        "checks_passed": checks_passed,
        "missing_information": missing_info,
        "reviewer_choices": reviewer_choices,
        "graph_effect": graph_effect,
    }


def build_ingestion_decision_explanation(
    ledger: dict[str, Any],
    *,
    technical_ledger_sha256: str,
    graph_delta: dict[str, Any],
) -> dict[str, Any]:
    """Derive the contract-valid English artifact only from technical evidence."""
    groups = _collect_decision_groups_map(ledger)
    decision_groups = [_build_single_decision_group_entry(gid, vals) for gid, vals in sorted(groups.items())]

    summary = {
        "candidate_count": ledger["candidate_count"],
        "decision_group_count": len(decision_groups),
        "exact_link_count": sum(value["exact_identity_found"] for value in decision_groups),
        "proposed_new_node_count": sum(
            value["new_node_recommended"] for value in decision_groups
        ),
        "source_only_count": sum(
            "evidence-only" in value["pipeline_recognition"] for value in decision_groups
        ),
        "review_required_count": sum(
            any(choice["available"] for choice in value["reviewer_choices"])
            for value in decision_groups
        ),
        "proposed_graph_operation_count": ledger["planned_graph_operation_count"],
    }
    explanation_id = stable_id(
        "ingestion-decision-explanation",
        ledger["run_id"],
        technical_ledger_sha256,
        length=28,
    )
    return create_artifact(
        "ingestion-decision-explanation",
        explanation_id,
        explanation_id=explanation_id,
        run_id=ledger["run_id"],
        trace_id=ledger["trace_id"],
        source_id=ledger["source_id"],
        source_version_id=ledger["source_version_id"],
        authority="derived_explanation_only",
        mutation_authority=False,
        bindings={
            "technical_ledger_path": INGESTION_DECISION_LEDGER_JSON,
            "technical_ledger_sha256": technical_ledger_sha256,
            "technical_ledger_digest": digest(ledger),
            "graph_delta_path": "graph_delta_summary.json",
            "graph_delta_digest": digest(graph_delta),
            "graph_sha256_before": graph_delta["graph_sha256_before"],
        },
        summary=summary,
        decision_groups=decision_groups,
        extensions={},
    )


def render_ingestion_decision_ledger_markdown(explanation: dict[str, Any]) -> str:
    """Render Markdown exclusively from the validated English JSON artifact."""

    validate_artifact(explanation)
    if not explanation["contract_id"].endswith(
        ":ingestion-decision-explanation:1.0.0"
    ):
        raise ValueError("Markdown rendering requires the English explanation artifact")
    summary = explanation["summary"]
    lines = [
        "# Ingestion decision ledger",
        "",
        f"Run: `{explanation['run_id']}`  ",
        f"Source: `{explanation['source_id']}`  ",
        f"Source version: `{explanation['source_version_id']}`",
        "",
        "This table explains the deterministic decision. Similarity is possible duplicate evidence only; it never identifies or merges a graph node.",
        "",
        "| Source decision | What was recognized | Exact existing identity? | New node? | Possible duplicates | Checks passed / missing | Reviewer choices and exact graph effect | Current graph effect |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for group in explanation["decision_groups"]:
        locations = "; ".join(
            f"{value['kind']}: {value['value']}"
            for value in group["affected_source_locations"]
        )
        duplicates = "; ".join(
            f"{value['label']} ({value['similarity_percent']:.1f}% possible duplicate evidence)"
            for value in group["possible_duplicates"]
        ) or "None ranked"
        checks = (
            f"Passed: {', '.join(group['checks_passed']) or 'none'}. "
            f"Missing: {', '.join(group['missing_information']) or 'nothing'}."
        )
        choices = "; ".join(
            f"{value['decision']} ({'available' if value['available'] else 'not available'}): {value['effect']}"
            for value in group["reviewer_choices"]
        )
        lines.append(
            "| "
            + " | ".join(
                _markdown_table_cell(value)
                for value in (
                    f"{group['reported_label']}<br>{len(group['candidate_ids'])} candidate(s)<br>{locations}",
                    group["pipeline_recognition"],
                    group["exact_identity_explanation"],
                    group["new_node_explanation"],
                    duplicates,
                    checks,
                    choices,
                    group["graph_effect"],
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            (
                f"Totals: {summary['candidate_count']} candidates in "
                f"{summary['decision_group_count']} grouped decisions; "
                f"{summary['proposed_graph_operation_count']} proposed graph operations."
            ),
            "",
            "Authority: explanation only. This file cannot authorize a graph mutation.",
            "",
        ]
    )
    return "\n".join(lines)
