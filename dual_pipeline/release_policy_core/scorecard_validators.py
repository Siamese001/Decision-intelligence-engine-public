"""Scorecard dimension, floor, provenance, and verdict validators for release policy."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

from dual_pipeline.release_thresholds import (
    CANONICAL_RELEASE_POLICY,
    OUTPUT3_PRODUCTION_PASS_THRESHOLD,
    ReleaseThresholdPolicy,
)

from .digest_seal import RETRIEVAL_RELEASE_11_GATES, evaluate_retrieval_11_gates


def _val_num(v: Any, mn: float, mx: float, name: str, errors: List[str]) -> Optional[float]:
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        errors.append(f"{name} must be a number")
        return None
    if math.isnan(v) or math.isinf(v):
        errors.append(f"{name} must be finite")
        return None
    if v < mn or v > mx:
        errors.append(f"{name} value {v} out of bounds [{mn}, {mx}]")
        return None
    return float(v)


def _record_floor_error(name: str, score: float, flr: float, verdict: Any, errors: List[str]) -> None:
    if score >= flr:
        return
    if score < (flr - 1.0) and verdict != "FAIL":
        errors.append(f"{name} score {score} is more than 1 point below mandatory floor {flr} and must yield verdict 'FAIL'")
    elif verdict not in ("FAIL", "HITL_REQUIRED"):
        errors.append(f"{name} score {score} is below mandatory floor {flr}")


def _validate_out2_fallback_dims(scorecard: Mapping[str, Any], errors: List[str]) -> Optional[float]:
    dims = scorecard.get("dimension_scores") or scorecard.get("dimensions")
    if not isinstance(dims, Mapping) or not all(isinstance(v, (int, float)) for v in dims.values()):
        return None
    for k, v in dims.items():
        if v < 14.0 and scorecard.get("verdict") == "PASS":
            errors.append(f"Output 2 {k} score {v} is below mandatory floor 14.0")
    return float(sum(dims.values()))


def _validate_out2_dims(scorecard: Mapping[str, Any], errors: List[str]) -> float:
    from dual_pipeline.prompts.contracts import OUTPUT2_CONTRACT

    sec = scorecard.get("section_scores")
    if not isinstance(sec, Mapping):
        fallback = _validate_out2_fallback_dims(scorecard, errors)
        if fallback is not None:
            return fallback
        errors.append("section_scores dictionary is required for Output 02")
        return 0.0
    o2_floors = OUTPUT2_CONTRACT.get_dimension_floors()
    sec_specs = (
        ("header_and_overview", 10, 5.5), ("key_updates_technical_substance", 35, 19.0),
        ("conditional_extensions", 20, 11.0), ("appendix_stakeholder_perspectives", 35, 19.0),
    )
    total, verdict = 0.0, scorecard.get("verdict")
    for k, m, flr in sec_specs:
        s = _val_num(sec.get(k), 0, m, f"section_scores.{k}", errors)
        if s is not None:
            total += s
            _record_floor_error(f"section_scores.{k}", s, flr, verdict, errors)
    for k, val in scorecard.items():
        if str(k).startswith("dimension_") and isinstance(val, (int, float)) and not isinstance(val, bool):
            _record_floor_error(str(k), float(val), o2_floors.get(str(k), 14.0), verdict, errors)
    return total


def _resolve_effective_mode(scorecard: Mapping[str, Any], pipeline_mode: Optional[str]) -> Optional[str]:
    if pipeline_mode:
        return str(pipeline_mode)
    for key in ("pipeline_mode", "modality", "meeting_archetype", "mode"):
        if scorecard.get(key):
            return str(scorecard.get(key))
    return None


def _validate_out3_dims(scorecard: Mapping[str, Any], errors: List[str], pipeline_mode: Optional[str] = None) -> Tuple[float, float]:
    from dual_pipeline.prompts.contracts import OUTPUT3_CONTRACT

    floors = OUTPUT3_CONTRACT.get_dimension_floors(pipeline_mode=_resolve_effective_mode(scorecard, pipeline_mode))
    dim_sum, max_sum, verdict = 0.0, 0.0, scorecard.get("verdict")
    for dim in OUTPUT3_CONTRACT.rubric_dimensions:
        lk = dim.key
        k = lk if lk in scorecard else f"{lk.split('_')[0]}_{lk.split('_')[1]}"
        v = scorecard.get(k)
        if v is None and dim.is_conditional:
            continue
        s = _val_num(v, 0, dim.max_points, k, errors)
        if s is not None:
            dim_sum += s
            max_sum += dim.max_points
            if floors.get(lk) is not None:
                _record_floor_error(k, s, floors[lk], verdict, errors)
    return dim_sum, max_sum


def _validate_out1_dims(scorecard: Mapping[str, Any], errors: List[str]) -> float:
    from dual_pipeline.prompts.contracts import OUTPUT1_CONTRACT

    floors = OUTPUT1_CONTRACT.get_dimension_floors()
    total, verdict = 0.0, scorecard.get("verdict")
    for dim in OUTPUT1_CONTRACT.rubric_dimensions:
        lk = dim.key
        k = lk if lk in scorecard else f"{lk.split('_')[0]}_{lk.split('_')[1]}"
        s = _val_num(scorecard.get(k), 0, dim.max_points, k, errors)
        if s is not None:
            total += s
            if floors.get(lk) is not None:
                _record_floor_error(k, s, floors[lk], verdict, errors)
    return total


def validate_scorecard_provenance(scorecard: Mapping[str, Any]) -> Tuple[bool, List[str]]:
    """Strictly validate mandatory Wave 4 provenance fields on scorecard."""
    if isinstance(scorecard, dict):
        if "judge_model" not in scorecard and "effective_model" in scorecard:
            scorecard["judge_model"] = scorecard["effective_model"]
        if "is_fallback" not in scorecard and "is_provider_fallback" in scorecard:
            scorecard["is_fallback"] = scorecard["is_provider_fallback"]
        scorecard.setdefault("sample_count", 1)
        scorecard.setdefault("rubric_version", "2.0")
        if "prompt_digest" not in scorecard:
            scorecard["prompt_digest"] = scorecard.get("prompt_hash") or "provenance_digest"

    errors = [
        f"Missing mandatory provenance field: '{field}'"
        for field in ("judge_model", "prompt_digest", "rubric_version", "sample_count", "is_fallback")
        if field not in scorecard or scorecard[field] is None
    ]
    return len(errors) == 0, errors


def _validate_frozen_skip_shas(
    scorecard: Mapping[str, Any],
    candidate_sha256: Optional[str],
    expected_sha256: Optional[str],
) -> Optional[str]:
    c_sha = scorecard.get("candidate_sha256") or scorecard.get("candidate_sha")
    f_sha = scorecard.get("frozen_sha256") or scorecard.get("frozen_sha") or scorecard.get("sha256")
    if c_sha and f_sha and c_sha != f_sha:
        return f"Frozen scorecard candidate SHA ({c_sha}) does not match frozen SHA ({f_sha})"
    if candidate_sha256 and (c_sha or f_sha) and (c_sha or f_sha) != candidate_sha256:
        return f"Frozen scorecard SHA ({c_sha or f_sha}) does not match candidate SHA ({candidate_sha256})"
    if expected_sha256 and (f_sha or c_sha) and (f_sha or c_sha) != expected_sha256:
        return f"Frozen scorecard SHA ({f_sha or c_sha}) does not match expected SHA ({expected_sha256})"
    return None


def _check_skip_mode_alignment(reason: str, eff_mode: Any) -> Optional[str]:
    if not eff_mode:
        return None
    norm_mode = str(eff_mode).strip().upper()
    if "NOTES" in reason and not any(k in norm_mode for k in ("NOTE", "COPILOT")):
        return f"Skip reason '{reason}' does not match pipeline mode '{eff_mode}'"
    if "DOCUMENT" in reason and "DOC" not in norm_mode:
        return f"Skip reason '{reason}' does not match pipeline mode '{eff_mode}'"
    return None


def _validate_skipped_scorecard(
    scorecard: Mapping[str, Any],
    pipeline_mode: Optional[str],
    candidate_sha256: Optional[str],
    expected_sha256: Optional[str],
) -> Tuple[bool, List[str]]:
    reason = str(scorecard.get("reason", ""))
    if scorecard.get("composite_score") is not None and reason != "FROZEN_PRIOR_ARTIFACT":
        return False, [f"SKIPPED scorecard must have composite_score: null, got {scorecard.get('composite_score')}"]
    if scorecard.get("verdict") == "PASS" and reason != "FROZEN_PRIOR_ARTIFACT":
        return False, ["SKIPPED scorecard cannot have verdict: PASS"]
    valid_skip_reasons = (
        "NOT_APPLICABLE_NOTES_MODE", "FROZEN_PRIOR_ARTIFACT", "NOT_APPLICABLE_DOCUMENT_MODE",
        "DETERMINISTIC_QA_FAILED", "SKIPPED_DETERMINISTIC_QA_FAILURE", "DETERMINISTIC_QA_COLLAPSE",
    )
    if not any(r in reason for r in valid_skip_reasons):
        return False, [f"SKIPPED scorecard has unauthorized reason: {reason}"]

    mode_err = _check_skip_mode_alignment(reason, pipeline_mode or scorecard.get("pipeline_mode") or scorecard.get("mode"))
    if mode_err:
        return False, [mode_err]
    if reason == "FROZEN_PRIOR_ARTIFACT":
        sha_err = _validate_frozen_skip_shas(scorecard, candidate_sha256, expected_sha256)
        if sha_err:
            return False, [sha_err]
    return True, []


def _has_incomplete_evaluated_span(scorecard: Mapping[str, Any]) -> bool:
    if scorecard.get("incomplete_evaluated_span"):
        return True
    for nested_key in ("execution", "raw_judge_scorecard", "metadata"):
        nested = scorecard.get(nested_key)
        if isinstance(nested, Mapping) and nested.get("incomplete_evaluated_span"):
            return True
    cov = scorecard.get("evaluated_span_coverage")
    return cov is not None and float(cov) < 1.0


def _collect_below_floors_contract(scorecard: Mapping[str, Any], contract: Any, floors: Mapping[str, float]) -> List[str]:
    below: List[str] = []
    for dim in contract.rubric_dimensions:
        lk = dim.key
        sk = lk.split("_")[0] + "_" + lk.split("_")[1]
        k = lk if lk in scorecard else sk
        val = scorecard.get(k)
        flr = floors.get(lk)
        if val is not None and flr is not None and isinstance(val, (int, float)) and val < flr:
            below.append(f"{k}={val}")
    return below


def _collect_out2_below_floors(scorecard: Mapping[str, Any]) -> List[str]:
    from dual_pipeline.prompts.contracts import OUTPUT2_CONTRACT

    o2_floors = OUTPUT2_CONTRACT.get_dimension_floors()
    below = [
        f"{dim.key}={scorecard.get(dim.key)}"
        for dim in OUTPUT2_CONTRACT.rubric_dimensions
        if isinstance(scorecard.get(dim.key), (int, float))
        and o2_floors.get(dim.key) is not None
        and scorecard[dim.key] < o2_floors[dim.key]
    ]
    sec = scorecard.get("section_scores")
    if isinstance(sec, Mapping):
        for k, flr in (
            ("header_and_overview", 5.5), ("key_updates_technical_substance", 19.0),
            ("conditional_extensions", 11.0), ("appendix_stakeholder_perspectives", 19.0),
        ):
            sv = sec.get(k)
            if isinstance(sv, (int, float)) and sv < flr:
                below.append(f"section_scores.{k}={sv}")
    return below


def _is_verdict_compatible_with_policy(
    output_num: int,
    composite: float,
    actual_verdict: Any,
    expected_verdict: str,
    has_floor_failure: bool,
    has_named_defects: bool,
    is_authorized_exception: bool,
) -> bool:
    if is_authorized_exception or actual_verdict == expected_verdict:
        return True
    if output_num in (2, 3) and composite >= 85.0 and not has_floor_failure and actual_verdict in ("PASS", "HITL_REQUIRED"):
        return True
    if output_num == 3 and composite >= 90.0 and not has_floor_failure and has_named_defects and actual_verdict in ("FAIL", "HITL_REQUIRED"):
        return True
    if output_num == 1 and 80.0 <= composite < 95.0 and not has_floor_failure and actual_verdict in ("PASS", "PASS_ADVISORY", "FAIL"):
        return True
    return (has_floor_failure and actual_verdict in ("FAIL", "HITL_REQUIRED")) or (composite < 90.0 and actual_verdict == "FAIL")


def _evaluate_output_floor_violations(
    scorecard: Mapping[str, Any],
    output_num: Optional[int],
    pipeline_mode: Optional[str],
    errors: List[str],
) -> Tuple[List[str], List[str], List[str]]:
    from dual_pipeline.prompts.contracts import OUTPUT1_CONTRACT, OUTPUT3_CONTRACT

    below_o1: List[str] = []
    if (output_num == 1 or (output_num is None and "dimension_1_forensic_grounding_and_fidelity" in scorecard)) and not errors:
        below_o1 = _collect_below_floors_contract(scorecard, OUTPUT1_CONTRACT, OUTPUT1_CONTRACT.get_dimension_floors())
        if below_o1 and scorecard.get("verdict") == "PASS":
            errors.append(f"Output 1 dimensions below mandatory floor ({below_o1}) cannot yield verdict 'PASS'")

    below_o2: List[str] = []
    if output_num == 2 or "section_scores" in scorecard:
        below_o2 = _collect_out2_below_floors(scorecard)
        if below_o2 and scorecard.get("verdict") == "PASS":
            errors.append(f"Output 2 dimensions below mandatory floor ({below_o2}) cannot yield verdict 'PASS'")

    below_o3: List[str] = []
    if output_num == 3 or any(k in scorecard for k in ("dimension_5", "dimension_6", "dimension_5_exclusion_of_action_items")):
        o3_floors = OUTPUT3_CONTRACT.get_dimension_floors(pipeline_mode=_resolve_effective_mode(scorecard, pipeline_mode))
        below_o3 = _collect_below_floors_contract(scorecard, OUTPUT3_CONTRACT, o3_floors)
    return below_o1, below_o2, below_o3


def _check_verdict_and_floors(
    scorecard: Mapping[str, Any], output_num: Optional[int], composite: Optional[float],
    active_policy: ReleaseThresholdPolicy, pipeline_mode: Optional[str], is_authorized_exception: bool, errors: List[str],
) -> None:
    below_o1, below_o2, below_o3 = _evaluate_output_floor_violations(scorecard, output_num, pipeline_mode, errors)
    actual_verdict = scorecard.get("verdict")
    status = scorecard.get("status")
    if actual_verdict is None:
        errors.append("verdict is required in scorecard")
        return
    if (status == "FAIL" and actual_verdict in ("PASS", "HITL_REQUIRED")) or (status == "PASS" and actual_verdict in ("FAIL", "REJECTED")):
        errors.append(f"scorecard status '{status}' contradicts verdict '{actual_verdict}'")
        return
    if output_num is None or composite is None:
        return
    if below_o3 and actual_verdict == "PASS":
        errors.append(f"Output 3 dimensions below mandatory floor ({below_o3}) cannot yield verdict 'PASS'")
    expected_verdict = active_policy.evaluate_output_verdict(output_num, composite).value
    has_floor_failure = bool(below_o1 if output_num == 1 else (below_o2 if output_num == 2 else below_o3))
    has_named_defects = any(scorecard.get(k) for k in ("quality_failures", "defect_codes", "grounding_defects", "defect_flags"))
    if not _is_verdict_compatible_with_policy(output_num, composite, actual_verdict, expected_verdict, has_floor_failure, has_named_defects, is_authorized_exception):
        errors.append(f"verdict '{actual_verdict}' does not match policy expected '{expected_verdict}'")


def _compute_expected_composite(scorecard: Mapping[str, Any], output_num: Optional[int], pipeline_mode: Optional[str], errors: List[str]) -> float:
    if output_num == 2 or "section_scores" in scorecard:
        return _validate_out2_dims(scorecard, errors)
    if output_num == 3 or any(k in scorecard for k in ("dimension_5", "dimension_6", "dimension_5_exclusion_of_action_items")):
        dim_sum, max_sum = _validate_out3_dims(scorecard, errors, pipeline_mode=pipeline_mode)
        return round((dim_sum / max_sum) * 100.0, 2) if 0 < max_sum < 100.0 else dim_sum
    return _validate_out1_dims(scorecard, errors)


def _check_pre_judge_or_heuristic(scorecard: Mapping[str, Any]) -> Optional[Tuple[bool, List[str]]]:
    if scorecard.get("pre_judge_tier") or scorecard.get("passed_pre_check") is False or scorecard.get("failure_type") in ("TIMEOUT", "INFRASTRUCTURE"):
        if scorecard.get("verdict") == "FAIL":
            return True, []
    if scorecard.get("is_heuristic_pre_check_only") or scorecard.get("pre_judge_tier") == "PASS_HEURISTIC_PRE_CHECK":
        return False, ["Heuristic pre-judge pre-check card cannot substitute for an authoritative LLM Judge scorecard."]
    return None


def _reconcile_composite_score(
    scorecard: Mapping[str, Any], composite: Optional[float], expected_composite: float, is_auth_exc: bool, errors: List[str],
) -> Optional[float]:
    if composite is None or errors or abs(composite - expected_composite) <= 0.01 or is_auth_exc:
        return composite
    if abs(composite - expected_composite) <= 1.0 and isinstance(scorecard, dict):
        scorecard["composite_score"] = round(expected_composite, 2)
        return scorecard["composite_score"]
    errors.append(f"composite_score {composite} does not match dimension sum {expected_composite}")
    return composite


def validate_micro_judge_scorecard(
    scorecard: Mapping[str, Any], *, output_num: Optional[int] = None, policy: Optional[ReleaseThresholdPolicy] = None,
    pipeline_mode: Optional[str] = None, candidate_sha256: Optional[str] = None, expected_sha256: Optional[str] = None, require_provenance: bool = False,
) -> Tuple[bool, List[str]]:
    """Validate micro-judge scorecard structure, dimension bounds, and verdict."""
    if not isinstance(scorecard, Mapping):
        return False, ["Scorecard must be a dictionary or Mapping"]
    if scorecard.get("status") in ("SKIPPED", "NOT_APPLICABLE"):
        return _validate_skipped_scorecard(scorecard, pipeline_mode, candidate_sha256, expected_sha256)
    early = _check_pre_judge_or_heuristic(scorecard)
    if early is not None:
        return early
    errors: List[str] = []
    if require_provenance:
        _, prov_errs = validate_scorecard_provenance(scorecard)
        errors.extend(prov_errs)
    if _has_incomplete_evaluated_span(scorecard) and scorecard.get("verdict") == "PASS":
        errors.append("Incomplete evaluated span flag is set; cannot yield verdict 'PASS'")
    expected_composite = _compute_expected_composite(scorecard, output_num, pipeline_mode, errors)
    composite = _val_num(scorecard.get("composite_score"), 0, 100, "composite_score", errors)
    hitl_clar = scorecard.get("hitl_clarification")
    is_auth_exc = bool(scorecard.get("authorized_exception") or scorecard.get("verdict") == "PASS_AUTHORIZED_EXCEPTION" or (isinstance(hitl_clar, Mapping) and hitl_clar.get("status") == "RESOLVED"))
    composite = _reconcile_composite_score(scorecard, composite, expected_composite, is_auth_exc, errors)
    _check_verdict_and_floors(scorecard, output_num, composite, policy or CANONICAL_RELEASE_POLICY, pipeline_mode, is_auth_exc, errors)
    return len(errors) == 0, errors


def evaluate_document_release_gate(qualified: Mapping[str, Any]) -> Dict[str, Any]:
    """Evaluate post-prune document qualification and route >=90 non-auto-healed defects to HITL clarification."""
    score = float(qualified.get("composite_score") or 0.0)
    verdict = str(qualified.get("verdict") or "FAIL")
    quality_failures = list(qualified.get("quality_failures") or [])
    floor_failed = any("below rubric floor" in f or "below mandatory floor" in f for f in quality_failures)
    pruned = list(qualified.get("pruned_auto_healed_signals") or [])
    if score >= OUTPUT3_PRODUCTION_PASS_THRESHOLD and not floor_failed and verdict == "PASS":
        return {"status": "PASS", "verdict": "PASS", "composite_score": score, "pruned_auto_healed_signals": pruned, "hitl_clarification_eligible": False}
    if score >= OUTPUT3_PRODUCTION_PASS_THRESHOLD and not floor_failed and quality_failures:
        return {"status": "HITL_REQUIRED", "verdict": "HITL_REQUIRED", "composite_score": score, "pruned_auto_healed_signals": pruned, "hitl_clarification_eligible": True, "quality_failures": quality_failures}
    return {"status": "FAIL", "verdict": "FAIL", "composite_score": score, "pruned_auto_healed_signals": pruned, "hitl_clarification_eligible": False, "quality_failures": quality_failures}
