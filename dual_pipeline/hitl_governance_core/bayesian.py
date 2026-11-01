"""
dual_pipeline/hitl_governance_core/bayesian.py

Bayesian continuous learning and posterior calibration under Beta-binomial conjugate model.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Optional

from dual_pipeline.parameters import (
    get_hitl_autonomous_feedback_weight,
    get_hitl_bayesian_prior_strength,
    get_hitl_human_feedback_weight,
)


@dataclass
class ConfidenceResult:
    point_estimate: float
    lower_bound: float
    upper_bound: float
    interval_width: float
    sample_count: int
    tier: str
    is_precise: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CalibratedScore(float):
    """Bayesian calibrated confidence score with credible interval and sample count metadata."""

    point_estimate: float
    lower_bound: float
    upper_bound: float
    interval_width: float
    sample_count: int
    tier: str
    is_precise: bool

    def __new__(
        cls, val: float, lower: float = 0.05, upper: float = 0.99,
        sample_count: int = 0, tier: str = "MEDIUM",
    ) -> CalibratedScore:
        obj = super().__new__(cls, float(val))
        obj.point_estimate = float(val)
        obj.lower_bound, obj.upper_bound = round(float(lower), 4), round(float(upper), 4)
        obj.interval_width = round(float(upper - lower), 4)
        obj.sample_count, obj.tier = int(sample_count), str(tier)
        obj.is_precise = obj.interval_width < 0.20
        return obj

    def to_confidence_result(self) -> ConfidenceResult:
        return ConfidenceResult(
            self.point_estimate, self.lower_bound, self.upper_bound,
            self.interval_width, self.sample_count, self.tier, self.is_precise,
        )

    def to_dict(self) -> dict[str, Any]:
        return self.to_confidence_result().to_dict()


def compute_beta_credible_interval(
    alpha: float,
    beta: float,
    confidence_level: float = 0.95,
    min_bound: float = 0.05,
    max_bound: float = 0.99,
) -> tuple[float, float]:
    """
    Computes equal-tailed (1 - alpha_level) credible interval for Beta(alpha, beta).
    Uses exact scipy.stats.beta.interval when available, with a closed-form Normal
    approximation fallback. Clamps results to [min_bound, max_bound].
    """
    a, b = max(0.001, float(alpha)), max(0.001, float(beta))
    try:
        import scipy.stats as stats
        lo, hi = stats.beta.interval(confidence_level, a, b)
        return round(max(min_bound, min(max_bound, float(lo))), 4), round(max(min_bound, min(max_bound, float(hi))), 4)
    except Exception:
        pass

    n = a + b
    mu = a / n
    sigma = math.sqrt(max(1e-9, (a * b) / (n * n * (n + 1.0))))
    z = 1.96 if abs(confidence_level - 0.95) < 0.02 else 2.576
    return round(max(min_bound, mu - z * sigma), 4), round(min(max_bound, mu + z * sigma), 4)


def _normalize_key_tokens(key: str) -> list[str]:
    return [t for t in re.split(r"[^a-zA-Z0-9]+", key.lower()) if t]


DISQUALIFYING_ACTION_MODIFIERS = frozenset({
    "delete", "remove", "drop", "destroy", "retire", "purge", "kill", "disable",
    "create", "add", "insert", "new", "modify", "update", "replace", "rewire",
    "deny", "reject", "block", "approve", "allow", "accept",
})

STOP_TOKENS = frozenset({
    "to", "for", "at", "with", "the", "a", "an", "in", "on", "of", "and", "or", "e", "g",
    "mark", "units", "baseline", "etc",
})


def canonicalize_pattern_key(raw: str) -> str:
    """Extract canonical action or entity pattern key.

    Strips presentation prefixes ((Recommended)), confidence indicators
    (including nested parens/percentages), timestamps ([00:05:10]), quotes,
    turn counts, speaker prefixes, and normalizes punctuation to spaces.
    """
    if not raw or not isinstance(raw, str):
        return ""
    text = raw.strip()
    text = re.sub(r"^\s*(?:\(|\[)?\s*recommended\s*(?:\)|\])?\s*[-—–:]*\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?:[-—–]\s*)?(?:\(|\[)?\s*confidence:\s*[^()\[\]]*(?:\([^()]*\)[^()]*|\[[^\[\]]*\][^\[\]]*)*(?:\)|\])?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\(\s*\d{1,3}%\s*\)", "", text)
    text = re.sub(r"\[?\b\d{1,2}:\d{2}(?::\d{2})?\b\]?", "", text)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", text)
    text = re.sub(r"\(\s*\d+\s+turns?\s*\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*speaker[\s_-]*\d+\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[()\[\]{}'\"`\-—–:;,.]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def _extract_pattern_tokens(clean_key: str) -> tuple[list[str], list[str]]:
    all_toks = _normalize_key_tokens(clean_key)
    meaningful = [t for t in all_toks if t not in STOP_TOKENS]
    return all_toks, (meaningful if meaningful else all_toks)


def _match_keys(k1: str, k2: str) -> bool:
    """Robust, collision-free key matching preventing substring pollution.

    Exact match is prioritized. Canonicalizes presentation noise (timestamps, quotes,
    confidence indicators, recommended prefixes). Supports subset containment and
    meaningful action/entity token overlap, provided neither side differs by
    conflicting action modifiers or distinct option identifiers.
    """
    clean1 = canonicalize_pattern_key(k1)
    clean2 = canonicalize_pattern_key(k2)
    if not clean1 or not clean2:
        return False
    if clean1 == clean2:
        return True

    all1, mean1 = _extract_pattern_tokens(clean1)
    all2, mean2 = _extract_pattern_tokens(clean2)
    if not all1 or not all2:
        return False

    set1, set2 = set(all1), set(all2)
    if set1 == set2:
        return True

    mset1, mset2 = set(mean1), set(mean2)
    if mset1 == mset2:
        return True

    sym_diff = set1 ^ set2
    if sym_diff & DISQUALIFYING_ACTION_MODIFIERS:
        return False

    diff1 = set1 - set2
    diff2 = set2 - set1

    mdiff1 = mset1 - mset2
    mdiff2 = mset2 - mset1
    distinct_id_tokens = {"a", "b", "c", "d", "1", "2", "3", "4"}
    if (diff1 & distinct_id_tokens) and (diff2 & distinct_id_tokens):
        return False

    # Subset containment on full or meaningful token sets without conflicting distinct tokens
    if not (mdiff1 and mdiff2):
        common = mset1 & mset2
        if any(len(t) >= 3 for t in common):
            return True

    return False


def _parse_iso_timestamp(ts: Optional[str]):
    if not ts:
        return None
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def compute_bayesian_calibrated_score(
    base_score: float,
    affirmed_weight: float,
    rejected_weight: float,
    prior_strength: float | None = None,
    min_bound: float = 0.05,
    max_bound: float = 0.95,
    sample_count: int | None = None,
    confidence_level: float = 0.95,
) -> CalibratedScore:
    """Computes calibrated posterior confidence under Beta-binomial conjugate model.

    alpha_0 = base_score * prior_strength
    beta_0 = (1.0 - base_score) * prior_strength
    alpha_post = alpha_0 + affirmed_weight
    beta_post = beta_0 + rejected_weight
    posterior_mean = alpha_post / (alpha_post + beta_post)
    """
    eff_prior_strength = (
        prior_strength if prior_strength is not None else get_hitl_bayesian_prior_strength()
    )
    c0 = min(0.99, max(0.01, float(base_score)))
    n0 = max(0.1, float(eff_prior_strength))
    alpha_0 = c0 * n0
    beta_0 = (1.0 - c0) * n0

    total_weight = affirmed_weight + rejected_weight
    effective_n = int(sample_count) if sample_count is not None else int(round(total_weight))

    if total_weight <= 0.0:
        posterior = c0
        alpha_post = alpha_0
        beta_post = beta_0
    else:
        alpha_post = alpha_0 + float(affirmed_weight)
        beta_post = beta_0 + float(rejected_weight)
        posterior = alpha_post / (alpha_post + beta_post)

    score = round(min(max_bound, max(min_bound, posterior)), 4)
    tier = "HIGH" if score >= 0.85 else ("MEDIUM" if score >= 0.50 else "LOW")

    lo, hi = compute_beta_credible_interval(
        alpha=alpha_post,
        beta=beta_post,
        confidence_level=confidence_level,
        min_bound=min_bound,
        max_bound=max_bound,
    )
    if effective_n == 0:
        lo, hi = min_bound, max_bound

    return CalibratedScore(
        val=score,
        lower=lo,
        upper=hi,
        sample_count=effective_n,
        tier=tier,
    )


def _apply_time_decay(
    base_w: float, rec_dt: Optional[datetime], half_life_days: float | None, now: datetime
) -> float:
    if not half_life_days or half_life_days <= 0 or rec_dt is None:
        return base_w
    from datetime import timezone
    ref_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    target_dt = rec_dt if rec_dt.tzinfo is not None else rec_dt.replace(tzinfo=timezone.utc)
    age_seconds = max(0.0, (ref_now - target_dt).total_seconds())
    return base_w * (0.5 ** (age_seconds / (half_life_days * 86400.0)))


def _compute_record_weight(
    rec: dict[str, Any], half_life_days: float | None, now: datetime
) -> float:
    if not isinstance(rec, dict):
        return 0.0
    from .learning_eligibility import extract_independent_outcome, is_autonomous_record, is_test_record
    if is_test_record(rec):
        return 0.0
    meta = rec.get("metadata") if isinstance(rec.get("metadata"), dict) else {}
    auth_type = str(rec.get("authority_type") or meta.get("authority_type") or "").strip().upper()
    if (auth_type == "AUTONOMOUS" or is_autonomous_record(rec)) and extract_independent_outcome(rec) is None:
        return 0.0
    base_w = get_hitl_autonomous_feedback_weight() if is_autonomous_record(rec) else get_hitl_human_feedback_weight()
    return _apply_time_decay(base_w, _parse_iso_timestamp(rec.get("recorded_at")), half_life_days, now)


def _accumulate_record_options(
    rec: dict[str, Any],
    sel: str,
    w: float,
    is_neg: bool,
    aff_w: dict[str, float],
    rej_w: dict[str, float],
    aff_c: dict[str, int],
    rej_c: dict[str, int],
) -> None:
    from .learning_eligibility import extract_override_recommendation, is_override_record
    from .surfacing import _option_key

    if is_neg:
        rej_w[sel] = round(rej_w.get(sel, 0.0) + abs(w), 4)
        rej_c[sel] = rej_c.get(sel, 0) + 1
        return

    aff_w[sel] = round(aff_w.get(sel, 0.0) + w, 4)
    aff_c[sel] = aff_c.get(sel, 0) + 1

    rec_key = extract_override_recommendation(rec) if is_override_record(rec) else None
    if rec_key and not _match_keys(rec_key, sel):
        clean_rec = rec_key.lower().strip()
        rej_w[clean_rec] = round(rej_w.get(clean_rec, 0.0) + w, 4)
        rej_c[clean_rec] = rej_c.get(clean_rec, 0) + 1

    for opt in rec.get("options", []):
        opt_key = _option_key(opt).lower().strip()
        if opt_key and not _match_keys(opt_key, sel) and (not rec_key or not _match_keys(opt_key, rec_key)):
            rej_w[opt_key] = round(rej_w.get(opt_key, 0.0) + w, 4)
            rej_c[opt_key] = rej_c.get(opt_key, 0) + 1


def _matches_scoped_prior(rec: dict[str, Any], clean_proj: str | None, clean_subj: str | None) -> bool:
    if not clean_proj and not clean_subj:
        return True
    meta = rec.get("metadata") if isinstance(rec.get("metadata"), dict) else {}
    if clean_proj and str(rec.get("project") or meta.get("project") or "").strip().lower() != clean_proj:
        return False
    if clean_subj:
        rec_s = str(rec.get("subject_key") or rec.get("subject") or meta.get("subject_key") or meta.get("subject") or "").strip().lower()
        if not rec_s or not _match_keys(rec_s, clean_subj):
            return False
    return True


def _accumulate_historical_weights(
    history: list[dict[str, Any]],
    category: str,
    half_life_days: float | None,
    now: datetime,
    current_decision_id: str | None = None,
    project: str | None = None,
    subject_key: str | None = None,
) -> tuple[dict[str, float], dict[str, float], dict[str, int], dict[str, int]]:
    from .learning_eligibility import (
        deduplicate_records_for_learning,
        filter_frozen_priors,
        is_negative_record,
    )

    aff_w: dict[str, float] = {}
    rej_w: dict[str, float] = {}
    aff_c: dict[str, int] = {}
    rej_c: dict[str, int] = {}

    filtered = filter_frozen_priors(history, current_decision_id)
    deduped = deduplicate_records_for_learning(filtered)
    clean_p = project.strip().lower() if project else None
    clean_s = subject_key.strip().lower() if subject_key else None

    clean_cat = (category or "").strip().lower()
    wild_cat = clean_cat in ("", "general", "default", "all")

    for rec in deduped:
        if not isinstance(rec, dict):
            continue
        rec_cat = str(rec.get("category", "")).strip().lower()
        if not wild_cat and rec_cat != clean_cat:
            continue
        if not _matches_scoped_prior(rec, clean_p, clean_s):
            continue
        w = _compute_record_weight(rec, half_life_days, now)
        sel = str(rec.get("selected_key") or "").lower().strip()
        if w == 0.0 or not sel:
            continue

        _accumulate_record_options(
            rec, sel, w, is_negative_record(rec),
            aff_w, rej_w, aff_c, rej_c
        )

    # Cross-category fallback when a specific category has 0 matched records or legacy 'general' records exist
    if not aff_w and not rej_w and not wild_cat:
        for rec in deduped:
            if not isinstance(rec, dict):
                continue
            rec_cat = str(rec.get("category", "")).strip().lower()
            if rec_cat not in ("general", "default", "tactical", "tactical_execution", clean_cat):
                continue
            if not _matches_scoped_prior(rec, clean_p, clean_s):
                continue
            w = _compute_record_weight(rec, half_life_days, now)
            sel = str(rec.get("selected_key") or "").lower().strip()
            if w == 0.0 or not sel:
                continue
            _accumulate_record_options(
                rec, sel, w, is_negative_record(rec),
                aff_w, rej_w, aff_c, rej_c
            )

    return aff_w, rej_w, aff_c, rej_c


def update_label_confidence(label: str, new_tier: str, new_score: float) -> str:
    """Updates or appends calibrated confidence tier and percentage in the label string."""
    pct = int(round(float(new_score) * 100))
    tier = new_tier.upper()

    p_parens = re.compile(r"\(\s*Confidence:[^()]*(?:\([^()]*\)[^()]*)*\)", re.IGNORECASE)
    if p_parens.search(label):
        return p_parens.sub(f"(Confidence: {tier} ({pct}%))", label)

    p_dash = re.compile(r"([—–-]\s*Confidence:\s*)[^\n—–]+", re.IGNORECASE)
    if p_dash.search(label):
        return p_dash.sub(f"— Confidence: {tier} ({pct}%)", label)

    p_bracket = re.compile(r"\[\s*Confidence:[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\]", re.IGNORECASE)
    if p_bracket.search(label):
        return p_bracket.sub(f"[Confidence: {tier} ({pct}%)]", label)

    p_pct_parens = re.compile(r"\(\s*\d{1,3}%\s*\)")
    if p_pct_parens.search(label):
        return p_pct_parens.sub(f"({pct}%)", label)

    return label


def _matched_sum(weights: dict[str, float], key: str, alt_key: str | None = None) -> float:
    return sum(
        w for k, w in weights.items()
        if _match_keys(k, key) or (alt_key and _match_keys(k, alt_key))
    )


def _matched_count(counts: dict[str, int], key: str, alt_key: str | None = None) -> int:
    return sum(
        c for k, c in counts.items()
        if _match_keys(k, key) or (alt_key and _match_keys(k, alt_key))
    )


def _calibrate_single_option(
    opt: Any,
    aff_weights: dict[str, float],
    rej_weights: dict[str, float],
    aff_counts: dict[str, int],
    rej_counts: dict[str, int],
    eff_prior_strength: float,
    min_bound: float,
    max_bound: float,
    half_life_days: float | None,
) -> dict[str, Any]:
    from .surfacing import _option_key, _standardize_option

    item = _standardize_option(opt)
    base_score = item["confidence_score"]
    key = _option_key(opt).lower().strip()
    alt_key = str(opt.get("label", "") if isinstance(opt, dict) else opt).lower().strip()
    if alt_key == key:
        alt_key = None

    aff_w = _matched_sum(aff_weights, key, alt_key)
    rej_w = _matched_sum(rej_weights, key, alt_key)
    aff_count = _matched_count(aff_counts, key, alt_key)
    rej_count = _matched_count(rej_counts, key, alt_key)

    total_count = aff_count + rej_count
    calibrated_score = compute_bayesian_calibrated_score(
        base_score=base_score,
        affirmed_weight=aff_w,
        rejected_weight=rej_w,
        prior_strength=eff_prior_strength,
        min_bound=min_bound,
        max_bound=max_bound,
        sample_count=total_count,
    )

    tier = calibrated_score.tier
    is_pct = isinstance(opt, dict) and isinstance(opt.get("confidence"), str) and "%" in opt["confidence"]
    adj = round(calibrated_score - base_score, 4) if total_count > 0 else 0.0
    item.update({
        "confidence_score": calibrated_score,
        "confidence": f"{round(float(calibrated_score) * 100.0, 2):g}%" if is_pct else calibrated_score,
        "confidence_level": tier, "historical_adjustment": adj, "prior_adjustment": adj,
        "prior_affirmations": aff_count, "prior_rejections": rej_count,
        "affirmed_weight": round(aff_w, 4), "rejected_weight": round(rej_w, 4),
        "confidence_lower": calibrated_score.lower_bound, "confidence_upper": calibrated_score.upper_bound,
        "confidence_interval": [calibrated_score.lower_bound, calibrated_score.upper_bound],
        "confidence_interval_width": calibrated_score.interval_width, "confidence_sample_count": total_count,
        "confidence_is_precise": calibrated_score.is_precise if total_count > 0 else False,
    })
    if half_life_days is not None:
        item["effective_prior_weight"] = round(aff_w - rej_w, 4)
    if "label" in item and isinstance(item["label"], str):
        item["label"] = update_label_confidence(item["label"], tier, calibrated_score)
    return item
