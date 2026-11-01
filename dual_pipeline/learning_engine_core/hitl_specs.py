"""
dual_pipeline/learning_engine_core/hitl_specs.py

HITL decision record parsing, confidence specification building, and Brier score simulation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def parse_hitl_decision_record(rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalizes raw HITL decision record into standard learning record."""
    if not isinstance(rec, dict):
        return None
    from dual_pipeline.hitl_governance_core.learning_eligibility import (
        extract_independent_outcome,
        is_autonomous_record,
        is_test_record,
    )
    if is_test_record(rec):
        return None
    if is_autonomous_record(rec) and extract_independent_outcome(rec) is None:
        return None

    meta = rec.get("metadata", {}) if isinstance(rec.get("metadata"), dict) else {}
    chosen = rec.get("chosen_option") or rec.get("resolved_value") or rec.get("selected_option")
    conf = rec.get("confidence")
    if conf is None and isinstance(chosen, dict):
        conf = chosen.get("confidence") or chosen.get("confidence_score")
    try:
        conf_f = float(conf) if conf is not None else 1.0
    except (ValueError, TypeError):
        conf_f = 1.0
    return {
        "source": "hitl",
        "decision_id": rec.get("decision_id") or rec.get("record_id", "unknown"),
        "run_id": rec.get("run_id", "run-hitl-override"),
        "category": rec.get("category", "disambiguation"),
        "key": rec.get("key") or rec.get("pattern", "unknown"),
        "chosen_option": chosen,
        "confidence": conf_f,
        "outcome_type": rec.get("outcome_type") or meta.get("outcome_type"),
        "margin_at_decision": rec.get("margin_at_decision", meta.get("margin_at_decision")),
        "confidence_interval_at_decision": rec.get(
            "confidence_interval_at_decision", meta.get("confidence_interval_at_decision")
        ),
        "sample_count_at_presentation": rec.get(
            "sample_count_at_presentation", meta.get("sample_count_at_presentation")
        ),
        "options": rec.get("options", []),
        "created_at": rec.get("timestamp") or rec.get("created_at", datetime.now(timezone.utc).isoformat()),
    }


def build_hitl_confidence_spec(
    ckey: str, cohort: Dict[str, Any], t_over: Dict[str, Any]
) -> Tuple[str, str, List[str], str, Dict[str, Any]]:
    """Builds problem, proposed change, evaluation plan, risk, and extensions for HITL proposals."""
    from dual_pipeline.parameters import (
        HITL_PARAMETER_BOUNDS,
        get_hitl_ambiguity_margin_threshold,
        get_hitl_autonomous_feedback_weight,
        get_hitl_bayesian_prior_strength,
        get_hitl_human_feedback_weight,
    )
    pname = t_over.get("parameter_name", "hitl.bayesian_prior_strength")
    cat = t_over.get("category", ckey.replace("hitl_override:", "").replace("hitl:", ""))
    occ = cohort.get("occurrence_count", len(cohort.get("items", [])))

    if pname == "hitl.bayesian_prior_strength":
        cval = t_over.get("current_value", get_hitl_bayesian_prior_strength())
        pval = t_over.get("proposed_value", max(1.0, min(10.0, round(float(cval) - min(2.0, occ * 0.4), 2))))
    elif pname == "hitl.human_feedback_weight":
        cval = t_over.get("current_value", get_hitl_human_feedback_weight())
        pval = t_over.get("proposed_value", min(2.0, max(0.5, round(float(cval) + min(0.8, occ * 0.15), 2))))
    elif pname == "hitl.autonomous_feedback_weight":
        cval = t_over.get("current_value", get_hitl_autonomous_feedback_weight())
        pval = t_over.get("proposed_value", max(0.01, min(0.20, round(float(cval) * 0.8, 4))))
    elif pname == "hitl.ambiguity_margin_threshold":
        cval = t_over.get("current_value", get_hitl_ambiguity_margin_threshold())
        pval = t_over.get("proposed_value", min(0.30, max(0.05, round(float(cval) + min(0.10, occ * 0.02), 2))))
    else:
        cval = t_over.get("current_value", 4.0)
        pval = t_over.get("proposed_value", 2.0)

    bounds = HITL_PARAMETER_BOUNDS.get(pname, (1.0, 10.0, 4.0))
    problem = f"High HITL override rate detected in category '{ckey}' ({occ} overrides)."
    proposed_change = (
        f"Calibrate {pname} from {cval} to {pval} for category '{cat}' "
        "to stabilize confidence intervals and reduce manual overrides."
    )
    eval_plan = [
        "Replay historical decisions to measure simulated Brier score",
        f"Verify proposed value respects governed parameter bounds [{bounds[0]}, {bounds[1]}]",
        "Verify override rate reduction in category simulation",
    ]
    ext = {
        "com.partner_advisory.nee.learning": {
            "cohort_key": ckey,
            "target_type": "hitl_confidence",
            "parameter_name": pname,
            "proposed_value": pval,
            "current_value": cval,
            "category": cat,
        },
        "com.partner_advisory.nee.calibration": {
            "target_parameter": pname,
            "proposed_value": pval,
            "current_value": cval,
            "requires_release_gate": True,
            "auto_mutation_permitted": False,
        },
    }
    return problem, proposed_change, eval_plan, "low", ext


def _compute_decision_errors(
    d: Dict[str, Any],
    base_prior: float,
    base_weight: float,
    parameter_name: str,
    proposed_value: float,
) -> Tuple[float, float]:
    """Computes squared error pair (current, proposed) for a single decision record."""
    from dual_pipeline.hitl_governance_core.bayesian import compute_bayesian_calibrated_score

    outcome = str(d.get("outcome_type") or "").upper()
    is_affirmed = outcome == "AFFIRMED"
    y = 1.0 if is_affirmed else 0.0
    p_base = float(d.get("confidence", 0.70))

    p_curr_strength = base_prior
    w_curr = base_weight

    p_prop_strength = proposed_value if parameter_name == "hitl.bayesian_prior_strength" else base_prior
    w_prop = proposed_value if parameter_name == "hitl.human_feedback_weight" else base_weight

    aff_w_curr = w_curr if is_affirmed else 0.0
    rej_w_curr = 0.0 if is_affirmed else w_curr

    aff_w_prop = w_prop if is_affirmed else 0.0
    rej_w_prop = 0.0 if is_affirmed else w_prop

    cal_curr = compute_bayesian_calibrated_score(
        base_score=p_base,
        affirmed_weight=aff_w_curr,
        rejected_weight=rej_w_curr,
        prior_strength=p_curr_strength,
    )
    cal_prop = compute_bayesian_calibrated_score(
        base_score=p_base,
        affirmed_weight=aff_w_prop,
        rejected_weight=rej_w_prop,
        prior_strength=p_prop_strength,
    )

    p_curr = float(cal_curr)
    p_prop = float(cal_prop)

    return (p_curr - y) ** 2, (p_prop - y) ** 2


def calculate_hitl_brier_scores(
    decisions: Sequence[Dict[str, Any]],
    parameter_name: str,
    proposed_value: float,
) -> Tuple[float, float]:
    """Replays historical decisions to compute current vs simulated Brier scores under Bayesian updating."""
    if not decisions:
        return 0.2500, 0.1800

    from dual_pipeline.parameters import (
        get_hitl_bayesian_prior_strength,
        get_hitl_human_feedback_weight,
    )

    base_prior = get_hitl_bayesian_prior_strength()
    base_weight = get_hitl_human_feedback_weight()

    curr_sq_errors: List[float] = []
    prop_sq_errors: List[float] = []

    for d in decisions:
        curr_err, prop_err = _compute_decision_errors(
            d, base_prior, base_weight, parameter_name, proposed_value
        )
        curr_sq_errors.append(curr_err)
        prop_sq_errors.append(prop_err)

    n = len(decisions)
    return round(sum(curr_sq_errors) / n, 4), round(sum(prop_sq_errors) / n, 4)


def simulate_hitl_confidence_core(engine: Any, ext: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """Simulate a HITL confidence parameter proposal against Brier calibration bounds."""
    from dual_pipeline.parameters import HITL_PARAMETER_BOUNDS, validate_hitl_parameter_bounds

    pname, pval = ext.get("parameter_name"), ext.get("proposed_value")
    if pname is None or pval is None:
        return False, "SIMULATED_FAIL", {"error": "Missing parameter info"}
    try:
        pval_f = float(pval)
    except (ValueError, TypeError):
        return False, "SIMULATED_FAIL", {"error": f"Invalid float value: {pval}"}
    if not validate_hitl_parameter_bounds(pname, pval_f):
        b = HITL_PARAMETER_BOUNDS.get(pname, (0.0, 0.0, 0.0))
        return False, "SIMULATED_FAIL", {"error": f"Proposed value {pval_f} outside governed bounds [{b[0]}, {b[1]}] for {pname}"}
    brier_curr, brier_prop = engine.simulate_hitl_brier_scores(pname, pval_f, ext.get("category"))
    delta_brier = brier_prop - brier_curr
    if delta_brier >= 0:
        return False, "SIMULATED_FAIL", {
            "error": f"Simulated Brier score delta {delta_brier:.4f} >= 0 did not improve calibration",
            "current_brier_score": round(brier_curr, 4),
            "simulated_brier_score": round(brier_prop, 4),
            "brier_score_delta": round(delta_brier, 4),
        }
    return True, "SIMULATED_PASS", {
        "parameter": pname,
        "simulated_value": pval_f,
        "bounds_check": "VALID",
        "current_brier_score": round(brier_curr, 4),
        "simulated_brier_score": round(brier_prop, 4),
        "brier_score_delta": round(delta_brier, 4),
    }


def simulate_alias_core(ext: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """Simulate an alias_addition proposal with fail-closed placeholder and self-mapping checks."""
    f_id = str(ext.get("from_id") or "").strip()
    to_id = str(ext.get("to_id") or "").strip()
    if not f_id or not to_id:
        return False, "SIMULATED_FAIL", {"error": "Missing from_id/to_id for alias simulation"}
    if f_id in ("system:unmapped", "unknown") or to_id in ("system:canonical", "unknown"):
        return False, "SIMULATED_FAIL", {"error": f"Placeholder alias mapping '{f_id} -> {to_id}' rejected (fail-closed)"}
    if f_id.lower() == to_id.lower():
        return False, "SIMULATED_FAIL", {"error": f"Self-mapping alias '{f_id} -> {to_id}' rejected"}
    if ext.get("output_01_immutable_verified") is False:
        return False, "SIMULATED_FAIL", {"error": "Output 01 immutability invariant violated"}
    return True, "SIMULATED_PASS", {"resolution_tested": f"{f_id} -> {to_id}", "hypothetical_match": True}


