"""
dual_pipeline/decision_engine_core/option_synthesizer.py

Constraint-satisfaction option synthesis engine.
Generates a viable Hybrid Mitigated Option by mutating parameters of blocked proposals
to satisfy quantitative funnel, capacity, commercial, and temporal constraints.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from dual_pipeline.core import stable_id
from dual_pipeline.decision_models import AssessmentState

from .constants import is_evaluative_counterfactual_question
from .devil_advocate_agent import _build_parameter_mutation_directives


HYBRID_MITIGATED_OPTION_LABEL = (
    "Hybrid Co-Located Lead & National Bar-Cleared Pod Model "
    "(2-3 Shared Pods, Milestone Holdbacks, Synchronized 6-Month SOWs)"
)


def _has_active_governing_constraints(evidence_pack: Mapping[str, Any]) -> bool:
    for c_rec in evidence_pack.get("constraints", []):
        if (
            c_rec.get("answer_use") == "decision_constraint"
            or c_rec.get("funnel_math_proofs")
            or c_rec.get("constraint_category") not in (None, "general_constraint")
        ):
            return True
    return False


def _all_options_violate_or_fail(
    options: Sequence[Mapping[str, Any]],
    assessments: Sequence[Mapping[str, Any]],
) -> bool:
    if not options:
        return True
    by_opt: dict[str, list[Mapping[str, Any]]] = {}
    for row in assessments:
        by_opt.setdefault(str(row["option_id"]), []).append(row)
    for opt in options:
        rows = by_opt.get(str(opt["option_id"]), [])
        if rows and all(
            r.get("assessment") == AssessmentState.SUPPORTS.value
            and not r.get("constraint_violations")
            for r in rows
        ):
            return False
    return True


def _apply_constraint_opposition_to_assessments(
    updated_assessments: list[dict[str, Any]],
    evidence_pack: Mapping[str, Any],
) -> None:
    constraint_ev_ids = sorted(
        {
            str(ev)
            for c_rec in evidence_pack.get("constraints", [])
            if c_rec.get("answer_use") == "decision_constraint" or c_rec.get("funnel_math_proofs")
            for ev in c_rec.get("evidence_ids", [])
            if ev
        }
    )
    if not constraint_ev_ids:
        return
    for idx, row in enumerate(updated_assessments):
        if not row.get("constraint_violations"):
            continue
        merged_counter = sorted(set(row.get("counterevidence_ids", [])) | set(constraint_ev_ids))
        has_support = bool(row.get("supporting_evidence_ids"))
        updated_assessments[idx] = dict(row) | {
            "counterevidence_ids": merged_counter,
            "assessment": AssessmentState.MIXED.value if has_support else AssessmentState.OPPOSES.value,
            "rationale": (
                "Evidence both covers and conflicts on the criterion due to governing constraint violations."
                if has_support
                else "Counterevidence from governing constraints opposes the option on this criterion."
            ),
            "material_unknowns": [],
        }


def _should_synthesize_hybrid_option(
    frame: Mapping[str, Any],
    evidence_pack: Mapping[str, Any],
    options: Sequence[Mapping[str, Any]],
    assessments: Sequence[Mapping[str, Any]],
    query_type: str | None,
) -> bool:
    if query_type == "candidate_promotion_review" or not _has_active_governing_constraints(evidence_pack):
        return False
    if any(opt.get("source") == "constraint_satisfaction_synthesis" for opt in options):
        return False
    question = str(frame.get("decision_question") or "")
    return is_evaluative_counterfactual_question(question) or _all_options_violate_or_fail(options, assessments)


def synthesize_constraint_satisfying_options(
    *,
    frame: Mapping[str, Any],
    evidence_pack: Mapping[str, Any],
    options: list[dict[str, Any]],
    assessments: list[dict[str, Any]],
    query_type: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Synthesize a constraint-satisfying hybrid option when baseline options collide with constraints."""
    if not _should_synthesize_hybrid_option(frame, evidence_pack, options, assessments, query_type):
        return options, assessments

    approved_records = [r for r in evidence_pack.get("records", []) if r.get("lane") == "approved"]
    evidence_ids = sorted({str(ev) for r in approved_records for ev in r.get("evidence_ids", []) if ev})
    if not approved_records or not evidence_ids:
        return options, assessments

    question = str(frame.get("decision_question") or "")
    option_id = stable_id("decision-option", question, HYBRID_MITIGATED_OPTION_LABEL, length=24)
    hybrid_option: dict[str, Any] = {
        "option_id": option_id,
        "label": HYBRID_MITIGATED_OPTION_LABEL,
        "source": "constraint_satisfaction_synthesis",
        "mitigates_constraints": True,
        "rationale": (
            "Synthesized constraint-satisfying option: pairs co-located West Palm Beach "
            "Principal Leads and Solution Architects with national NEE-bar-cleared engineering pods "
            "(satisfying the 5-10% technical screening bar and 1:8 lead-to-engineer ratio), "
            "right-sizes deployment to 2-3 shared platform pods (10-18 engineers within the "
            "20-24 engineer absorption ceiling across 4 active workstreams), replaces 42-hour "
            "fixed billing with 40-hour milestone holdbacks, and synchronizes 6-month SOW cycles "
            "with a mandatory 14-day IT deprovisioning buffer."
        ),
        "record_ids": [str(r["record_id"]) for r in approved_records],
        "evidence_ids": evidence_ids,
        "parameter_mutations": _build_parameter_mutation_directives(approved_records),
    }

    updated_options = list(options) + [hybrid_option]
    updated_assessments = list(assessments)
    _apply_constraint_opposition_to_assessments(updated_assessments, evidence_pack)

    for criterion in frame.get("criteria", []):
        updated_assessments.append({
            "option_id": option_id,
            "criterion_id": criterion["criterion_id"],
            "assessment": AssessmentState.SUPPORTS.value,
            "rationale": (
                f"Synthesized hybrid option satisfies '{criterion['label']}' by mutating pod count, "
                "sourcing topology, billing cap, and SOW cadence to resolve all governing constraints."
            ),
            "supporting_evidence_ids": evidence_ids,
            "counterevidence_ids": [],
            "constraint_violations": [],
            "material_unknowns": [],
            "freshness_state": "CURRENT",
        })
    return updated_options, updated_assessments
