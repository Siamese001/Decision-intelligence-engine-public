"""
dual_pipeline/decision_engine_core/evaluation.py

Decision result evaluation, trade-off matrix computation, and confidence scoring.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import digest, stable_id
from dual_pipeline.decision_models import (
    AssessmentState,
    ConfidenceBand,
    DecisionTerminalState,
    validate_decision_frame,
    validate_decision_result,
)

from .assessment import assess_options
from .option_synthesizer import synthesize_constraint_satisfying_options
from .options import build_viable_options


def compute_trade_offs(
    options: Sequence[Mapping[str, Any]], assessments: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    by_option: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in assessments:
        by_option[str(row["option_id"])][str(row["assessment"])] += 1
    return [
        {
            "option_id": option["option_id"],
            "supports": by_option[option["option_id"]][AssessmentState.SUPPORTS.value],
            "opposes": by_option[option["option_id"]][AssessmentState.OPPOSES.value],
            "mixed": by_option[option["option_id"]][AssessmentState.MIXED.value],
            "unknown": by_option[option["option_id"]][AssessmentState.UNKNOWN.value],
        }
        for option in options
    ]


def compute_confidence(
    *,
    terminal: DecisionTerminalState,
    assessments: Sequence[Mapping[str, Any]],
    evidence_pack: Mapping[str, Any],
) -> dict[str, Any]:
    total = len(assessments)
    supported = sum(row["assessment"] == AssessmentState.SUPPORTS.value for row in assessments)
    unknown = sum(row["assessment"] == AssessmentState.UNKNOWN.value for row in assessments)
    criterion_support = supported / total if total else 0.0
    unknown_ratio = unknown / total if total else 1.0
    conflict_count = len(evidence_pack["conflicts"])
    stale_count = sum(
        record["currentness_state"] not in {"current", "unknown"}
        for record in evidence_pack["records"]
    )
    if terminal is not DecisionTerminalState.DECISION_PRODUCED:
        band = ConfidenceBand.INSUFFICIENT
    elif criterion_support == 1.0 and not conflict_count and not stale_count:
        band = ConfidenceBand.HIGH
    elif criterion_support >= 0.7 and unknown_ratio <= 0.3 and not conflict_count:
        band = ConfidenceBand.MEDIUM
    else:
        band = ConfidenceBand.LOW
    return {
        "band": band.value,
        "factors": {
            "criterion_support_ratio": round(criterion_support, 6),
            "unknown_ratio": round(unknown_ratio, 6),
            "conflict_count": conflict_count,
            "stale_record_count": stale_count,
            "visible_evidence_count": len(evidence_pack["source_evidence_ids"]),
        },
        "model_self_report_used": False,
    }


def _find_unique_winner(
    options: Sequence[Mapping[str, Any]],
    trade_offs: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    eligible = [
        row
        for row in trade_offs
        if row["supports"] > 0
        and row["opposes"] == 0
        and row["unknown"] == 0
        and row["mixed"] == 0
    ]
    eligible.sort(key=lambda row: (-row["supports"], row["option_id"]))
    if len(options) >= 2 and eligible:
        if len(eligible) == 1 or eligible[0]["supports"] > eligible[1]["supports"]:
            winner_id = eligible[0]["option_id"]
            return next((opt for opt in options if opt["option_id"] == winner_id), None)
    return None


def _resolve_no_winner_terminal(
    *,
    allow_recommendation: bool,
    evidence_pack: Mapping[str, Any],
    execution: Mapping[str, Any],
    num_options: int,
) -> tuple[DecisionTerminalState, str]:
    if not allow_recommendation and evidence_pack["source_evidence_ids"]:
        failure_code = execution.get("provider_failure_code")
        rationale = (
            f"Evidence is ready, but provider reasoning failed: {failure_code}."
            if failure_code
            else "Evidence is ready, but the reasoning stage did not complete."
        )
        return DecisionTerminalState.EVIDENCE_READY_DECISION_NOT_PRODUCED, rationale
    if not evidence_pack["source_evidence_ids"]:
        return DecisionTerminalState.NO_DECISION, "No source-bound evidence is available."
    if num_options < 2:
        return (
            DecisionTerminalState.EVIDENCE_READY_DECISION_NOT_PRODUCED,
            "Evidence is ready, but fewer than two viable options are evidenced.",
        )
    return DecisionTerminalState.NO_DECISION, "No option uniquely satisfies the evidenced criteria."


def _resolve_winner_material_conclusions(
    winner: Mapping[str, Any] | None,
    terminal: DecisionTerminalState,
    rationale: str,
    assessments: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, DecisionTerminalState, str, list[dict[str, Any]]]:
    if winner is None:
        return None, terminal, rationale, []
    evidence_ids = sorted(
        {
            value
            for row in assessments
            if row["option_id"] == winner["option_id"]
            for value in row["supporting_evidence_ids"]
        }
    )
    if evidence_ids:
        material_conclusions = [
            {
                "conclusion": f"Recommend {winner['label']}",
                "evidence_ids": evidence_ids,
            }
        ]
        return winner, terminal, rationale, material_conclusions
    return (
        None,
        DecisionTerminalState.NO_DECISION,
        "The apparent winner has no source-bound evidence.",
        [],
    )


def formulate_discriminating_question(
    options: Sequence[Mapping[str, Any]],
    criteria: Sequence[Mapping[str, Any]],
    assessments: Sequence[Mapping[str, Any]],
) -> str | None:
    if not options or not criteria:
        return None
    crit_by_id = {str(c["criterion_id"]): c for c in criteria}
    for row in assessments:
        if row.get("assessment") in (AssessmentState.UNKNOWN.value, AssessmentState.MIXED.value):
            c_info = crit_by_id.get(str(row.get("criterion_id")))
            if c_info:
                opt_labels = " and ".join(f"'{o.get('label')}'" for o in options[:2])
                return (
                    f"Which option between {opt_labels} satisfies "
                    f"'{c_info.get('label')}' with verified source-bound evidence?"
                )
    if len(options) >= 2:
        return (
            f"Which option between '{options[0].get('label')}' and '{options[1].get('label')}' "
            "uniquely satisfies all decision criteria with verified source-bound evidence?"
        )
    return None


def _build_what_would_change(
    discriminating_q: str | None,
) -> list[str]:
    items = [
        "New source-bound evidence for an unknown criterion",
        "Resolution of material counterevidence or stale observations",
        "Caller-supplied criteria weights when options remain tied",
    ]
    if discriminating_q:
        items.insert(0, f"Verified evidence answering: {discriminating_q}")
    return items


def _run_discriminating_recovery(
    *,
    validated_frame: Mapping[str, Any],
    evidence_pack: Mapping[str, Any],
    options: list[dict[str, Any]],
    assessments: list[dict[str, Any]],
    trade_offs: list[dict[str, Any]],
    answer: Mapping[str, Any] | None,
    query_type: str | None,
    discriminating_resolver: Any,
) -> tuple[Mapping[str, Any], list[dict[str, Any]], list[dict[str, Any]], Mapping[str, Any] | None, int]:
    """Executes up to 2 bounded discriminating sub-queries when UNKNOWN criteria block a winner."""
    active_pack = evidence_pack
    steps_used = 0
    for _step in range(2):
        has_unknown = any(
            r.get("assessment") in (AssessmentState.UNKNOWN.value, AssessmentState.MIXED.value)
            for r in assessments
        )
        if not has_unknown:
            break
        disc_q = formulate_discriminating_question(
            options=options,
            criteria=validated_frame.get("criteria", []),
            assessments=assessments,
        )
        if not disc_q:
            break
        steps_used += 1
        updated_pack = discriminating_resolver(disc_q, active_pack)
        if not isinstance(updated_pack, Mapping) or updated_pack.get("pack_digest") == active_pack.get("pack_digest"):
            break
        active_pack = updated_pack
        assessments = assess_options(
            frame=validated_frame,
            evidence_pack=active_pack,
            options=options,
            answer=answer,
            query_type=query_type,
        )
        trade_offs = compute_trade_offs(options, assessments)
        winner = _find_unique_winner(options, trade_offs)
        if winner is not None:
            return active_pack, assessments, trade_offs, winner, steps_used
    return active_pack, assessments, trade_offs, _find_unique_winner(options, trade_offs), steps_used


def _build_decision_extensions(
    *,
    discriminating_evidence_q: str | None,
    recovery_steps: int,
    options: list[dict[str, Any]],
    evidence_pack: Mapping[str, Any],
    validated_frame: Mapping[str, Any],
    topology: Any,
    active_pair: Mapping[str, Any] | None,
    assessments: list[dict[str, Any]],
    winner: Mapping[str, Any] | None,
) -> dict[str, Any]:
    extensions: dict[str, Any] = {}
    if discriminating_evidence_q is not None or recovery_steps > 0:
        extensions["com.partner_advisory.nee.decision-support"] = {
            "discriminating_evidence_question": discriminating_evidence_q,
            "discriminating_recovery_steps": recovery_steps,
        }
    if options:
        from .devil_advocate_agent import CounterfactualDevilAdvocateAgent

        da_report = CounterfactualDevilAdvocateAgent().challenge(
            options=options, evidence_records=list(evidence_pack.get("records", []))
        )
        extensions["com.partner_advisory.nee.counterfactual-challenge"] = da_report.to_dict()
    if topology is not None and active_pair is not None:
        try:
            from dual_pipeline.decision_witness import DecisionWitnessEngine

            extensions["com.partner_advisory.nee.decision-witness-subgraph"] = DecisionWitnessEngine.build_witness_subgraph(
                decision_frame=validated_frame, evidence_pack=evidence_pack, topology=topology,
                active_pair=active_pair, options=options, assessments=assessments, recommended_option=winner,
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
    return extensions


def evaluate_decision(
    *,
    frame: Mapping[str, Any],
    evidence_pack: Mapping[str, Any],
    answer: Mapping[str, Any] | None,
    execution: Mapping[str, Any],
    allow_recommendation: bool = True,
    query_type: str | None = None,
    topology: Any = None,
    active_pair: Mapping[str, Any] | None = None,
    discriminating_resolver: Any = None,
) -> dict[str, Any]:
    """Evaluate a decision conservatively and return a closed DecisionResult."""
    validated_frame = validate_decision_frame(frame)
    recovery_steps = 0
    if validated_frame["frame_state"] == "CLARIFICATION_REQUIRED":
        terminal = DecisionTerminalState.CLARIFICATION_REQUIRED
        options, assessments, trade_offs, winner = [], [], [], None
        rationale = "Decision criteria are required before evaluation."
    else:
        options = build_viable_options(frame=validated_frame, evidence_pack=evidence_pack, answer=answer, query_type=query_type)
        options, assessments = synthesize_constraint_satisfying_options(frame=validated_frame, evidence_pack=evidence_pack, options=options, assessments=assess_options(frame=validated_frame, evidence_pack=evidence_pack, options=options, answer=answer, query_type=query_type), query_type=query_type)
        trade_offs = compute_trade_offs(options, assessments)
        candidate_winner = _find_unique_winner(options, trade_offs)
        if candidate_winner is None and discriminating_resolver is not None:
            evidence_pack, assessments, trade_offs, candidate_winner, recovery_steps = _run_discriminating_recovery(
                validated_frame=validated_frame, evidence_pack=evidence_pack, options=options,
                assessments=assessments, trade_offs=trade_offs, answer=answer,
                query_type=query_type, discriminating_resolver=discriminating_resolver,
            )
        if candidate_winner is not None and allow_recommendation:
            terminal, winner = DecisionTerminalState.DECISION_PRODUCED, candidate_winner
            rationale = "The option uniquely satisfies every evidenced criterion without opposition."
        else:
            winner = None
            terminal, rationale = _resolve_no_winner_terminal(
                allow_recommendation=allow_recommendation, evidence_pack=evidence_pack, execution=execution, num_options=len(options)
            )

    winner, terminal, rationale, material_conclusions = _resolve_winner_material_conclusions(winner, terminal, rationale, assessments)
    discriminating_q = (
        formulate_discriminating_question(options=options, criteria=validated_frame.get("criteria", []), assessments=assessments)
        if terminal == DecisionTerminalState.NO_DECISION else None
    )
    unknowns_list = list(dict.fromkeys(evidence_pack["unknowns"] + [u for row in assessments for u in row["material_unknowns"]]))
    result_fields: dict[str, Any] = {
        "terminal_state": terminal.value, "decision_frame": validated_frame, "evidence_pack_digest": evidence_pack["pack_digest"],
        "considered_options": options, "option_assessments": assessments, "trade_off_matrix": trade_offs,
        "recommended_option": winner, "recommendation_rationale": rationale,
        "alternative_options": [o for o in options if winner is None or o["option_id"] != winner["option_id"]],
        "risks": evidence_pack["risks"], "constraints": evidence_pack["constraints"], "assumptions": validated_frame["assumptions"],
        "unknowns": unknowns_list, "confidence": compute_confidence(terminal=terminal, assessments=assessments, evidence_pack=evidence_pack),
        "what_would_change_the_conclusion": _build_what_would_change(discriminating_q),
        "material_conclusions": material_conclusions, "execution": dict(execution), "decision_result_digest": "",
    }
    result_fields["decision_result_digest"] = digest({k: v for k, v in result_fields.items() if k != "decision_result_digest"})
    extensions = _build_decision_extensions(
        discriminating_evidence_q=discriminating_q, recovery_steps=recovery_steps, options=options,
        evidence_pack=evidence_pack, validated_frame=validated_frame, topology=topology,
        active_pair=active_pair, assessments=assessments, winner=winner,
    )
    result = create_artifact(
        "decision-result", stable_id("decision-result", validated_frame["artifact_id"], evidence_pack["pack_digest"], terminal.value, length=26),
        extensions=extensions, **result_fields,
    )
    return validate_decision_result(result)
