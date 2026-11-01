"""Governance Policy: HITL 20% Ambiguity Margin Arbiter & Healing Policy Engine.

Implements the repository-wide Human-in-the-Loop (HITL) ambiguity gating rule:
Delta > 20% (0.20) and c_top >= 65% (0.65) -> Autonomous execution; otherwise escalate to HITL.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

from config.credentials import CredentialManager
from dual_pipeline.hitl_governance import DEFAULT_CONFIDENCE_FLOOR, DEFAULT_MARGIN_THRESHOLD, HITLDecisionStore
from dual_pipeline.hitl_governance_core.bayesian import _match_keys, canonicalize_pattern_key
from dual_pipeline.hitl_governance_core.layman_clarity import contains_jargon
from dual_pipeline.hitl_governance_core.question_builder import QuestionBuilder
from dual_pipeline.storage.healing_decision_store import HealingDecisionStore, record_healing_decision

AMBIGUITY_MARGIN_THRESHOLD = float(DEFAULT_MARGIN_THRESHOLD)  # 0.20 SSOT margin threshold
MIN_TOP_CONFIDENCE_FLOOR = float(DEFAULT_CONFIDENCE_FLOOR)    # 0.65 SSOT confidence floor


def load_healing_policy_config(policy_path: Optional[Path | str] = None) -> Dict[str, Any]:
    """Load config/governance/healing_policy.yaml."""
    default_p = Path(__file__).resolve().parent.parent / "config" / "governance" / "healing_policy.yaml"
    target = Path(policy_path) if policy_path else default_p
    if target.is_file():
        try:
            data = yaml.safe_load(target.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {
        "version": "1.0.0",
        "healing_governance": {
            "max_retries": 3, "ambiguity_margin_threshold": AMBIGUITY_MARGIN_THRESHOLD,
            "direct_mutation_permitted": False,
        },
    }


class HealingActionClassification(str, Enum):
    """Action classification for proposed repairs."""
    AUTO_APPLY, RETRY_WITH_BACKOFF = "AUTO_APPLY", "RETRY_WITH_BACKOFF"
    ESCALATE_HITL, QUARANTINE_AND_PROCEED = "ESCALATE_HITL", "QUARANTINE_AND_PROCEED"


@dataclass
class HitlArbitrationDecision:
    """Outcome of evaluating candidate options under the 20% SSOT margin rule."""
    decision_id: str
    action: HealingActionClassification
    top_option_key: str
    top_confidence: float
    second_confidence: float
    confidence_margin: float
    hitl_required: bool
    rationale: str
    timestamp: float = field(default_factory=time.time)
    question_payload: Optional[Dict[str, Any]] = None
    ask_user_question_tool_call: Optional[Dict[str, Any]] = None
    calibrated_options: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "action": self.action.value if isinstance(self.action, Enum) else self.action,
            "top_option_key": self.top_option_key, "top_confidence": self.top_confidence,
            "second_confidence": self.second_confidence, "confidence_margin": self.confidence_margin,
            "hitl_required": self.hitl_required, "rationale": self.rationale, "timestamp": self.timestamp,
            "question_payload": self.question_payload,
            "ask_user_question_tool_call": self.ask_user_question_tool_call,
            "calibrated_options": list(self.calibrated_options),
        }


_LAYMAN_TOKEN_MAP = {
    "db": "database", "sql": "database", "ast": "syntax",
    "json": "structured", "yaml": "configuration", "uuid": "identifier",
}


def sanitize_to_layman_text(raw_text: str, fallback: str = "Select recommended option") -> str:
    """Convert internal identifiers or snake_case tokens into clean layman English (>= 5 chars)."""
    cleaned = re.sub(r"[`_*:]+", " ", str(raw_text or ""))
    cleaned = re.sub(r"([a-z])([A-Z])", r"\1 \2", cleaned)
    cleaned = re.sub(r"[-/\\|]+", " ", cleaned)
    for tech_tok, plain_word in _LAYMAN_TOKEN_MAP.items():
        cleaned = re.sub(rf"\b{tech_tok}\b", plain_word, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(urn|nee|stmt|dec|sha256)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned or contains_jargon(cleaned):
        return fallback
    if len(cleaned) < 5:
        cleaned = f"Select {cleaned}"
    return cleaned[0].upper() + cleaned[1:]


def _load_repair_class_posteriors(
    category: str, healing_db_path: Optional[Path | str]
) -> Dict[str, Dict[str, Any]]:
    has_env_db = bool(
        CredentialManager.get_credential("NEE_LEARNING_SQLITE_PATH")
        or CredentialManager.get_credential("NEE_TEST_LEARNING_SQLITE")
    )
    if healing_db_path is None and not has_env_db and "healing" not in category.lower():
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    try:
        post = HealingDecisionStore(db_path=healing_db_path).compute_empirical_posteriors()
        for raw_k, stats in post.items():
            if isinstance(stats, dict) and raw_k.startswith("repair_class:"):
                out[raw_k.split("repair_class:", 1)[1]] = stats
    except Exception:
        pass
    return out


def _apply_healing_posterior_blend(
    orig_key: str,
    orig_score: float,
    c_dict: Mapping[str, Any],
    healing_posteriors: Mapping[str, Dict[str, Any]],
) -> Tuple[float, Dict[str, Any]]:
    score = float(c_dict.get("confidence_score", orig_score))
    adj = float(c_dict.get("prior_adjustment", 0.0))
    sample_cnt = int(c_dict.get("confidence_sample_count") or 0)
    canon_k = canonicalize_pattern_key(orig_key)
    for hk, hv in healing_posteriors.items():
        h_total = int(hv.get("total_count") or hv.get("total") or 0)
        if h_total >= 1 and _match_keys(canonicalize_pattern_key(hk), canon_k):
            h_post = float(hv.get("posterior_confidence") or hv.get("posterior_mean") or score)
            score = max(0.05, min(0.99, round(0.45 * score + 0.55 * h_post, 4)))
            adj = round(score - orig_score, 4)
            sample_cnt += h_total
            break
    return score, {
        "key": orig_key,
        "label": orig_key,
        "initial_confidence": float(orig_score),
        "confidence_score": score,
        "prior_adjustment": adj,
        "sample_count": sample_cnt,
    }


def _recalibrate_candidate_scores(
    options_with_scores: List[Tuple[str, float]],
    category: str,
    ledger_path: Optional[Path | str],
    healing_db_path: Optional[Path | str],
    enable_calibration: bool,
) -> Tuple[List[Tuple[str, float]], List[Dict[str, Any]]]:
    if not enable_calibration:
        raw_dicts = [{"key": k, "label": k, "confidence_score": float(s), "prior_adjustment": 0.0} for k, s in options_with_scores]
        return list(options_with_scores), raw_dicts

    opt_dicts = [{"key": k, "label": k, "confidence": float(s)} for k, s in options_with_scores]
    try:
        calibrated_dicts = HITLDecisionStore(ledger_path=ledger_path).calibrate_options(opt_dicts, category=category)
    except Exception:
        calibrated_dicts = [dict(d, confidence_score=d["confidence"], prior_adjustment=0.0) for d in opt_dicts]

    healing_posteriors = _load_repair_class_posteriors(category, healing_db_path)
    recalibrated_pairs: List[Tuple[str, float]] = []
    enriched_dicts: List[Dict[str, Any]] = []
    for (orig_key, orig_score), c_dict in zip(options_with_scores, calibrated_dicts):
        score, enriched = _apply_healing_posterior_blend(orig_key, orig_score, c_dict, healing_posteriors)
        recalibrated_pairs.append((orig_key, score))
        enriched_dicts.append(enriched)
    return recalibrated_pairs, enriched_dicts


def _build_escalation_question_payload(
    decision_id: str,
    sorted_opts: List[Tuple[str, float]],
    calibrated_dicts: List[Dict[str, Any]],
    question: Optional[str],
    category: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    if len(sorted_opts) < 2:
        return None, None
    clean_q = sanitize_to_layman_text(
        question or "",
        fallback="Which resolution option should be applied to resolve this quality check?",
    )
    if not clean_q.endswith("?"):
        clean_q = clean_q.rstrip(".") + "?"

    cal_by_key = {d["key"]: d for d in calibrated_dicts}
    builder = QuestionBuilder(
        question=clean_q,
        metadata={
            "decision_id": decision_id,
            "category": category,
            "calibrate": True,
            "already_calibrated": True,
            "raw_option_keys": [k for k, _ in sorted_opts],
            "post_learning_recalibrated": any(abs(float(d.get("prior_adjustment", 0.0))) > 1e-4 for d in calibrated_dicts),
        },
    )
    for idx, (opt_key, opt_score) in enumerate(sorted_opts):
        c_info = cal_by_key.get(opt_key, {})
        label = sanitize_to_layman_text(opt_key, fallback=f"Resolution option {idx + 1}")
        summary = f"Apply {label.lower()} based on verified historical evidence."
        impact = (
            "Updates the target section using the highest confidence path while preserving existing records."
            if idx == 0
            else "Preserves the alternative resolution path for this section and ensures operator oversight."
        )
        builder.add_option(
            label=label,
            plain_english_summary=summary,
            impact_statement=impact,
            confidence_score=float(opt_score),
            sample_size=int(c_info.get("sample_count") or 0) or None,
            recommended=(idx == 0),
            prior_adjustment=float(c_info.get("prior_adjustment", 0.0)),
        )
    payload = builder.build()
    return payload.to_dict(), payload.to_tool_call("ask_user_question")


def _record_policy_arbitration(
    decision: HitlArbitrationDecision,
    category: str,
    healing_db_path: Optional[Path | str],
) -> None:
    record_healing_decision(
        {
            "run_id": f"run-policy-{decision.decision_id}",
            "subsystem": "healing_policy",
            "component": "arbitrate_decision_margin",
            "scope": category,
            "repair_class": decision.top_option_key or "EMPTY_OPTIONS",
            "action_taken": decision.action.value,
            "outcome_status": "HEALED" if not decision.hitl_required else "ESCALATED_HITL",
            "confidence_top": decision.top_confidence,
            "confidence_second": decision.second_confidence,
            "ambiguity_margin": decision.confidence_margin,
            "hitl_escalated": decision.hitl_required,
            "invariants_preserved": True,
            "defect_reasons": [decision.rationale] if decision.hitl_required else [],
            "audit_trail": [decision.rationale],
            "metadata": {"decision_id": decision.decision_id, "calibrated_options": decision.calibrated_options},
        },
        db_path=healing_db_path,
    )


def _build_single_option_decision(
    decision_id: str,
    top_key: str,
    top_conf: float,
    calibrated_dicts: List[Dict[str, Any]],
) -> HitlArbitrationDecision:
    is_decisive = top_conf >= 0.50
    return HitlArbitrationDecision(
        decision_id=decision_id,
        action=HealingActionClassification.AUTO_APPLY if is_decisive else HealingActionClassification.ESCALATE_HITL,
        top_option_key=top_key,
        top_confidence=top_conf,
        second_confidence=0.0,
        confidence_margin=top_conf if is_decisive else 0.0,
        hitl_required=not is_decisive,
        rationale=(
            "Single decisive candidate option with adequate confidence."
            if is_decisive
            else "Single candidate option confidence below 50% threshold."
        ),
        calibrated_options=calibrated_dicts,
    )


def _evaluate_multi_option_margin(
    decision_id: str,
    sorted_opts: List[Tuple[str, float]],
    calibrated_dicts: List[Dict[str, Any]],
    margin_threshold: float,
    question: Optional[str],
    category: str,
) -> HitlArbitrationDecision:
    top_key, top_conf = sorted_opts[0]
    second_key, second_conf = sorted_opts[1]
    margin = round(top_conf - second_conf, 4)
    floor_met = top_conf >= MIN_TOP_CONFIDENCE_FLOOR
    margin_met = margin > margin_threshold if abs(margin_threshold - DEFAULT_AMBIGUITY_MARGIN) < 1e-6 else margin >= margin_threshold
    if margin_met and floor_met:
        return HitlArbitrationDecision(
            decision_id=decision_id,
            action=HealingActionClassification.AUTO_APPLY,
            top_option_key=top_key,
            top_confidence=top_conf,
            second_confidence=second_conf,
            confidence_margin=margin,
            hitl_required=False,
            rationale=(
                f"Decisive confidence margin ({margin * 100:.1f}% >= {margin_threshold * 100:.0f}%). "
                f"Proceeding autonomously with '{top_key}'."
            ),
            calibrated_options=calibrated_dicts,
        )
    q_payload, tool_call = _build_escalation_question_payload(
        decision_id, sorted_opts, calibrated_dicts, question, category
    )
    return HitlArbitrationDecision(
        decision_id=decision_id,
        action=HealingActionClassification.ESCALATE_HITL,
        top_option_key=top_key,
        top_confidence=top_conf,
        second_confidence=second_conf,
        confidence_margin=margin,
        hitl_required=True,
        rationale=(
            f"Ambiguity alert: Confidence margin ({margin * 100:.1f}%) is below {margin_threshold * 100:.0f}% "
            f"or top confidence ({top_conf * 100:.1f}%) is below {MIN_TOP_CONFIDENCE_FLOOR * 100:.0f}% floor. "
            f"Competing candidates '{top_key}' ({top_conf:.2f}) and '{second_key}' ({second_conf:.2f}) "
            f"require human operator arbitration."
        ),
        question_payload=q_payload,
        ask_user_question_tool_call=tool_call,
        calibrated_options=calibrated_dicts,
    )


def arbitrate_decision_margin(
    decision_id: str,
    options_with_scores: List[Tuple[str, float]],
    margin_threshold: float = AMBIGUITY_MARGIN_THRESHOLD,
    *,
    question: Optional[str] = None,
    category: str = "healing_policy",
    ledger_path: Optional[Path | str] = None,
    healing_db_path: Optional[Path | str] = None,
    calibrate_from_learning: Optional[bool] = None,
    persist_decision: bool = True,
) -> HitlArbitrationDecision:
    """Evaluates candidate choices against the 20% SSOT confidence margin rule with post-learning calibration."""
    if not options_with_scores:
        dec = HitlArbitrationDecision(
            decision_id=decision_id,
            action=HealingActionClassification.ESCALATE_HITL,
            top_option_key="",
            top_confidence=0.0,
            second_confidence=0.0,
            confidence_margin=0.0,
            hitl_required=True,
            rationale="No candidate options provided; requires human intervention.",
        )
    else:
        has_env_stores = bool(
            CredentialManager.get_credential("NEE_HITL_LEDGER_JSON")
            or CredentialManager.get_credential("NEE_LEARNING_SQLITE_PATH")
            or CredentialManager.get_credential("NEE_TEST_LEARNING_SQLITE")
            or CredentialManager.get_credential("NEE_LEARNED_HITL_OPTION_STORE")
        )
        should_calibrate = (
            calibrate_from_learning
            if calibrate_from_learning is not None
            else (ledger_path is not None or healing_db_path is not None or has_env_stores)
        )
        recalibrated_opts, calibrated_dicts = _recalibrate_candidate_scores(
            options_with_scores, category, ledger_path, healing_db_path, should_calibrate
        )
        sorted_opts = sorted(recalibrated_opts, key=lambda x: x[1], reverse=True)
        if len(sorted_opts) == 1:
            dec = _build_single_option_decision(decision_id, sorted_opts[0][0], sorted_opts[0][1], calibrated_dicts)
        else:
            dec = _evaluate_multi_option_margin(
                decision_id, sorted_opts, calibrated_dicts, margin_threshold, question, category
            )

    if persist_decision and healing_db_path is not None:
        _record_policy_arbitration(dec, category, healing_db_path)
    return dec


__all__ = [
    "AMBIGUITY_MARGIN_THRESHOLD",
    "MIN_TOP_CONFIDENCE_FLOOR",
    "HealingActionClassification",
    "HitlArbitrationDecision",
    "arbitrate_decision_margin",
    "load_healing_policy_config",
    "sanitize_to_layman_text",
]
