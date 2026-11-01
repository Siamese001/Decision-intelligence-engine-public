"""
dual_pipeline/decision_engine_core/assessment.py

Evidence assessment across decision options and criteria.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dual_pipeline.decision_models import AssessmentState
from dual_pipeline.l0.decision_support_contracts import (
    DecisionSupportState,
    evaluate_evidence_support,
)

from .constants import (
    PROMOTION_OPTION_TERMS,
    RETENTION_OPTION_TERMS,
    extract_tokens,
)
from .quantitative_math import evaluate_temporal_constraint_breaches


def candidate_option_posture(label: object) -> str | None:
    """Return a conservative caller-option posture for candidate review."""

    label_tokens = extract_tokens(label)
    if label_tokens & PROMOTION_OPTION_TERMS:
        return "PROMOTE"
    if label_tokens & RETENTION_OPTION_TERMS:
        return "RETAIN"
    return None


def candidate_retention_evidence(
    *, records: Sequence[Mapping[str, Any]]
) -> list[str] | None:
    """Return source-bound candidate evidence for the non-mutating retain option."""

    candidate_records = [record for record in records if record["lane"] == "candidate"]
    candidate_evidence_ids = {
        str(evidence_id)
        for record in candidate_records
        for evidence_id in record["evidence_ids"]
    }
    if not candidate_evidence_ids:
        return None
    return sorted(candidate_evidence_ids)


def candidate_review_assessments(
    *,
    frame: Mapping[str, Any],
    evidence_pack: Mapping[str, Any],
    options: Sequence[Mapping[str, Any]],
    answer: Mapping[str, Any] | None,
) -> list[dict[str, Any]] | None:
    """Assess candidate review without promoting the candidate fact."""

    del answer
    retention_evidence = candidate_retention_evidence(records=evidence_pack["records"])
    if retention_evidence is None:
        return None
    evidence_ids = retention_evidence
    postures = [candidate_option_posture(option["label"]) for option in options]
    if postures.count("RETAIN") != 1 or postures.count("PROMOTE") != 1:
        return None

    counterevidence_ids = sorted(
        {
            citation
            for item in evidence_pack["conflicts"]
            for citation in item.get("citations", [])
            if isinstance(citation, str)
        }
    )
    assessments: list[dict[str, Any]] = []
    for option, posture in zip(options, postures, strict=True):
        for criterion in frame["criteria"]:
            retains_candidate = posture == "RETAIN"
            assessments.append(
                {
                    "option_id": option["option_id"],
                    "criterion_id": criterion["criterion_id"],
                    "assessment": (
                        AssessmentState.SUPPORTS.value
                        if retains_candidate
                        else AssessmentState.OPPOSES.value
                    ),
                    "rationale": (
                        "Candidate-lane evidence establishes only the existing candidate "
                        "state; retaining it is a non-mutating outcome."
                        if retains_candidate
                        else "Candidate-lane evidence and a proposal-only retention "
                        "disposition cannot establish an approved fact for promotion."
                    ),
                    "supporting_evidence_ids": evidence_ids,
                    "counterevidence_ids": counterevidence_ids,
                    "constraint_violations": [],
                    "material_unknowns": [],
                    "freshness_state": "CURRENT",
                }
            )
    return assessments


def _resolve_bound_records(
    option: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    record_by_id: Mapping[str, Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    option_record_ids = set(option.get("record_ids", []))
    option_evidence_ids = set(option.get("evidence_ids", []))
    bound = [
        record
        for record in records
        if record["record_id"] in option_record_ids
        or option_evidence_ids.intersection(record["evidence_ids"])
    ]
    if not bound:
        bound = [
            record_by_id[record_id]
            for record_id in option_record_ids
            if record_id in record_by_id
        ]
    return bound


def _evaluate_record_support(
    bound_records: Sequence[Mapping[str, Any]],
    option_label: str,
    criterion_label: str,
    conflict_texts: Sequence[str],
) -> tuple[set[str], set[str]]:
    supporting_ids: set[str] = set()
    counter_ids: set[str] = set()
    for record in bound_records:
        support_state, _ = evaluate_evidence_support(
            record,
            option_label,
            criterion_label,
            known_conflicts=conflict_texts,
        )
        if support_state == DecisionSupportState.SUPPORTED:
            supporting_ids.update(str(v) for v in record.get("evidence_ids", []))
        elif support_state == DecisionSupportState.CONTRADICTED:
            counter_ids.update(str(v) for v in record.get("evidence_ids", []))
    return supporting_ids, counter_ids


def _collect_conflict_citations(
    conflicts_raw: Sequence[Any],
    option_label: str,
) -> set[str]:
    citations: set[str] = set()
    opt_tokens = extract_tokens(option_label)
    for item in conflicts_raw:
        item_text = str(item.get("description", item)) if isinstance(item, Mapping) else str(item)
        if extract_tokens(item_text).intersection(opt_tokens) and isinstance(item, Mapping):
            citations.update(c for c in item.get("citations", []) if isinstance(c, str))
    return citations


def _determine_assessment_state(
    supporting_ids: set[str],
    counter_ids: set[str],
) -> AssessmentState:
    if supporting_ids and counter_ids:
        return AssessmentState.MIXED
    if supporting_ids:
        return AssessmentState.SUPPORTS
    if counter_ids:
        return AssessmentState.OPPOSES
    return AssessmentState.UNKNOWN


_TEMPORAL_CRITERION_TOKENS = (
    "governance", "risk", "execution", "schedule", "lifecycle", "notice", "operating", "model", "timeline"
)


def _compute_temporal_and_constraint_violations(
    option: Mapping[str, Any],
    criterion: Mapping[str, Any],
    bound_records: Sequence[Mapping[str, Any]],
    evidence_pack: Mapping[str, Any],
    time_horizon: str | None,
    counter_ids: set[str],
) -> list[str]:
    if option.get("source") == "constraint_satisfaction_synthesis" or option.get("mitigates_constraints"):
        return []
    breaches = evaluate_temporal_constraint_breaches(
        str(option["label"]), bound_records, evidence_pack.get("constraints", []), time_horizon=time_horizon
    )
    crit_lower = str(criterion["label"]).casefold()
    if breaches and any(k in crit_lower for k in _TEMPORAL_CRITERION_TOKENS):
        counter_ids.update(str(b["evidence_id"]) for b in breaches if b.get("evidence_id"))
    opt_tokens = extract_tokens(option["label"])
    matched_ids = [
        r["record_id"]
        for r in evidence_pack["constraints"]
        if extract_tokens(r["text"]).intersection(opt_tokens) or r.get("answer_use") == "decision_constraint"
    ]
    return list(dict.fromkeys(matched_ids + [str(b["constraint_record_id"]) for b in breaches]))


def _assess_single_criterion(
    *,
    option: Mapping[str, Any],
    criterion: Mapping[str, Any],
    evidence_tokens: set[str],
    bound_records: Sequence[Mapping[str, Any]],
    evidence_pack: Mapping[str, Any],
    time_horizon: str | None = None,
) -> dict[str, Any]:
    conflicts_raw = evidence_pack.get("conflicts", [])
    conflict_texts = [
        str(item.get("description", item)) if isinstance(item, Mapping) else str(item)
        for item in conflicts_raw
    ]
    supporting_ids, counter_ids = _evaluate_record_support(
        bound_records, str(option["label"]), str(criterion["label"]), conflict_texts
    )
    counter_ids.update(_collect_conflict_citations(conflicts_raw, str(option["label"])))
    constraint_violations = _compute_temporal_and_constraint_violations(
        option, criterion, bound_records, evidence_pack, time_horizon, counter_ids
    )
    state = _determine_assessment_state(supporting_ids, counter_ids)
    rationale_map = {
        AssessmentState.SUPPORTS: "Evidence explicitly covers the criterion.",
        AssessmentState.MIXED: "Evidence both covers and conflicts on the criterion.",
        AssessmentState.OPPOSES: "Counterevidence opposes the option on this criterion.",
        AssessmentState.UNKNOWN: "No visible evidence binds this option to the criterion.",
    }
    freshness_state = (
        "CURRENT"
        if bound_records and all(r["currentness_state"] in {"current", "unknown"} for r in bound_records)
        else "STALE_OR_UNKNOWN"
    )
    material_unknowns = (
        [] if state is not AssessmentState.UNKNOWN else [f"No evidence for criterion: {criterion['label']}"]
    )
    return {
        "option_id": option["option_id"],
        "criterion_id": criterion["criterion_id"],
        "assessment": state.value,
        "rationale": rationale_map[state],
        "supporting_evidence_ids": sorted(supporting_ids),
        "counterevidence_ids": sorted(counter_ids),
        "constraint_violations": constraint_violations,
        "material_unknowns": material_unknowns,
        "freshness_state": freshness_state,
    }


def assess_options(
    *,
    frame: Mapping[str, Any],
    evidence_pack: Mapping[str, Any],
    options: Sequence[Mapping[str, Any]],
    answer: Mapping[str, Any] | None = None,
    query_type: str | None = None,
) -> list[dict[str, Any]]:
    if query_type == "candidate_promotion_review":
        cand_assessments = candidate_review_assessments(
            frame=frame,
            evidence_pack=evidence_pack,
            options=options,
            answer=answer,
        )
        if cand_assessments is not None:
            return cand_assessments
    records = evidence_pack["records"]
    record_by_id = {record["record_id"]: record for record in records}
    assessments: list[dict[str, Any]] = []
    for option in options:
        bound_records = _resolve_bound_records(option, records, record_by_id)
        evidence_text = " ".join(
            [str(option.get("rationale") or "")]
            + [f"{record['title']} {record['text']}" for record in bound_records]
        )
        evidence_tokens = extract_tokens(evidence_text)
        for criterion in frame["criteria"]:
            assessments.append(
                _assess_single_criterion(
                    option=option,
                    criterion=criterion,
                    evidence_tokens=evidence_tokens,
                    bound_records=bound_records,
                    evidence_pack=evidence_pack,
                    time_horizon=str(frame.get("time_horizon") or "unspecified"),
                )
            )
    return assessments

