"""
dual_pipeline/hitl_governance_core/priors.py

Export and programmatic sifting operations for learned Bayesian priors.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from dual_pipeline.core import digest, stable_id, utc_now, write_json
from .surfacing import _option_key


def _record_prior_adjustment(
    priors_summary: dict[str, dict[str, Any]],
    cat: str,
    key: str,
    *,
    affirmed: bool,
    weight: float,
) -> None:
    entry = priors_summary.setdefault(cat, {}).setdefault(
        key,
        {
            "pattern_key": key,
            "category": cat,
            "affirmations": 0,
            "rejections": 0,
            "affirmed_weight": 0.0,
            "rejected_weight": 0.0,
        },
    )
    if affirmed:
        entry["affirmations"] += 1
        entry["affirmed_weight"] = round(entry["affirmed_weight"] + weight, 4)
    else:
        entry["rejections"] += 1
        entry["rejected_weight"] = round(entry["rejected_weight"] + weight, 4)


def _process_export_record(
    rec: dict[str, Any],
    priors_summary: dict[str, dict[str, Any]],
    auto_w: float,
    human_w: float,
) -> None:
    from .learning_eligibility import (
        extract_override_recommendation,
        is_autonomous_record,
        is_negative_record,
        is_override_record,
        is_test_record,
    )

    if is_test_record(rec):
        return
    sel = rec.get("selected_key", "")
    if not sel:
        return

    w = auto_w if is_autonomous_record(rec) else human_w
    cat = rec.get("category", "default")
    is_neg = is_negative_record(rec)

    if is_neg:
        _record_prior_adjustment(priors_summary, cat, sel, affirmed=False, weight=w)
        return

    _record_prior_adjustment(priors_summary, cat, sel, affirmed=True, weight=w)
    rec_key = extract_override_recommendation(rec) if is_override_record(rec) else None
    if rec_key and rec_key != sel:
        _record_prior_adjustment(priors_summary, cat, rec_key, affirmed=False, weight=w)

    for opt in rec.get("options", []):
        opt_key = _option_key(opt)
        if opt_key and opt_key != sel and (not rec_key or opt_key != rec_key):
            _record_prior_adjustment(priors_summary, cat, opt_key, affirmed=False, weight=w)


def export_learned_priors(store: Any, output_path: Path | str | None = None) -> dict[str, Any]:
    """Exports an indexed dictionary of learned priors and posterior adjustments for auditing."""
    from .learning_eligibility import deduplicate_records_for_learning
    from dual_pipeline.parameters import get_hitl_autonomous_feedback_weight, get_hitl_human_feedback_weight

    if output_path is None:
        output_path = store.ledger_path.parent / "hitl_prior_calibration.json"
    out_p = Path(output_path).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)

    raw_history = store.get_history()
    history = deduplicate_records_for_learning(raw_history)
    priors_summary: dict[str, dict[str, Any]] = {}
    auto_w = get_hitl_autonomous_feedback_weight()
    human_w = get_hitl_human_feedback_weight()

    for rec in history:
        _process_export_record(rec, priors_summary, auto_w, human_w)

    payload = {
        "schema_version": "1.0.0",
        "exported_at": utc_now(),
        "total_records_analyzed": len(history),
        "categories_count": len(priors_summary),
        "learned_priors": priors_summary,
    }
    payload["priors_digest"] = digest(payload)
    write_json(out_p, payload)
    return payload


def _update_priors_for_record(rec: dict[str, Any], priors: dict[str, float]) -> None:
    from .bayesian import _match_keys
    from .learning_eligibility import (
        extract_override_recommendation,
        is_negative_record,
        is_override_record,
        is_test_record,
    )
    if is_test_record(rec):
        return
    sel = rec.get("selected_key", "").lower().strip()
    if not sel:
        return
    if is_negative_record(rec):
        priors[sel] = round(priors.get(sel, 1.0) * 0.85, 4)
        return

    priors[sel] = round(priors.get(sel, 1.0) * 1.15, 4)
    rec_key = extract_override_recommendation(rec) if is_override_record(rec) else None
    if rec_key and not _match_keys(rec_key, sel):
        clean_rec = rec_key.lower().strip()
        priors[clean_rec] = round(priors.get(clean_rec, 1.0) * 0.85, 4)
    for opt in rec.get("options", []):
        opt_key = _option_key(opt).lower().strip()
        if opt_key and not _match_keys(opt_key, sel) and (not rec_key or not _match_keys(opt_key, rec_key)):
            priors[opt_key] = round(priors.get(opt_key, 1.0) * 0.90, 4)


def _build_priors_map_from_history(history: Sequence[dict[str, Any]]) -> dict[str, float]:
    priors: dict[str, float] = {}
    for rec in history:
        _update_priors_for_record(rec, priors)
    return priors


def _convert_sifter_candidate(c: Mapping[str, Any], default_category: str | None) -> Any:
    from dual_pipeline.ptc.hitl_sifter import PTCHITLCandidate, PTCHITLOption

    c_dict = dict(c)
    opts = [
        PTCHITLOption(
            option_id=str(o.get("option_id", f"opt_{idx}")),
            title=str(o.get("title", f"Option {idx}")),
            raw_confidence=float(o.get("confidence", o.get("confidence_score", 0.50))),
            pattern_key=str(o.get("pattern_key", o.get("option_id", ""))),
            evidence_basis=str(o.get("evidence_basis", "")),
            risk_assessment=str(o.get("risk_assessment", "")),
        )
        for idx, o in enumerate(c_dict.get("options", []))
    ]
    return PTCHITLCandidate(
        candidate_id=str(c_dict.get("candidate_id", stable_id("cand", c_dict))),
        question=str(c_dict.get("question", "")),
        context=str(c_dict.get("context", "")),
        criticality=str(c_dict.get("criticality", "P1")),
        options=opts,
        category=default_category or str(c_dict.get("category", "entity_disambiguation")),
        metadata=dict(c_dict.get("metadata", {})),
    )


def sift_candidates_programmatic(
    store: Any,
    candidates: Sequence[Mapping[str, Any]],
    category: str | None = None,
    margin_threshold: float | None = None,
) -> dict[str, Any]:
    """Programmatic entrypoint for ambiguity sifting and prior-weighted auto-resolution (PASQM)."""
    from .learning_eligibility import deduplicate_records_for_learning
    from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold
    from dual_pipeline.ptc.hitl_sifter import PTCAmbiguitySifter

    eff_threshold = (
        margin_threshold
        if margin_threshold is not None
        else get_hitl_ambiguity_margin_threshold()
    )

    raw_history = store.get_history(category)
    history = deduplicate_records_for_learning(raw_history)
    priors = _build_priors_map_from_history(history)
    cand_objs = [_convert_sifter_candidate(c, category) for c in candidates]

    sifter = PTCAmbiguitySifter(priors_map=priors)
    result = sifter.sift(cand_objs, margin_threshold=eff_threshold)
    return result.to_dict()


def load_learned_priors(priors_path: Path | str | None = None) -> dict[str, Any]:
    """Load exported learned priors from disk, falling back to canonical ledger directory."""
    if priors_path is None:
        from dual_pipeline.hitl_governance_core.decision_record_writer import resolve_ledger_paths

        _, j_path = resolve_ledger_paths()
        priors_path = j_path.parent / "hitl_prior_calibration.json"
    p_path = Path(priors_path).resolve()
    if p_path.is_file():
        try:
            from dual_pipeline.core import read_json

            return read_json(p_path)
        except Exception:
            pass
    return {}
