"""
dual_pipeline/hitl_core/envelope.py

Mandatory HITL Requirement Envelope & Adversarial Execution Guard.
Ensures ask_user_question interactive prompts cannot be called unbundled,
uncalibrated, or unpersisted. Enforces cryptographic linking, entropy checks,
and automatic post-tool persistence into hitl_decision_ledger.json.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.hitl_governance_core.surfacing import (
    DEFAULT_MARGIN_THRESHOLD,
    HitlConfidenceMissingError,
    parse_confidence_score,
    validate_approval_origin,
)
from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold

logger = logging.getLogger(__name__)


def compute_normalized_entropy(probabilities: List[float]) -> float:
    """Calculate normalized Shannon entropy H(p) in [0.0, 1.0]."""
    valid = [p for p in probabilities if p > 0.0]
    total = sum(valid)
    if total <= 0.0 or len(valid) <= 1:
        return 0.0
    probs = [p / total for p in valid]
    h = -sum(p * math.log2(p) for p in probs)
    max_h = math.log2(len(valid))
    return round(h / max_h, 4) if max_h > 0 else 0.0


def _format_option_confidence(opt: Dict[str, Any], strict: bool = True) -> Tuple[int, str]:
    try:
        val = parse_confidence_score(opt, require_confidence=strict)
        conf_pct = int(round(val * 100)) if val <= 1.0 else int(round(val))
    except HitlConfidenceMissingError:
        if strict:
            raise
        conf_pct = 50
    except Exception:
        if strict:
            raise HitlConfidenceMissingError(f"Invalid confidence score in option: {opt!r}") from None
        conf_pct = 50
    tier = "HIGH" if conf_pct >= 85 else ("MEDIUM" if conf_pct >= 50 else "LOW")
    return conf_pct, tier


def _format_option_interval(opt: Dict[str, Any]) -> str:
    lo = opt.get("confidence_lower")
    hi = opt.get("confidence_upper")
    ci = opt.get("confidence_interval")
    n = opt.get("confidence_sample_count")
    if ci and isinstance(ci, (list, tuple)) and len(ci) >= 2:
        lo, hi = ci[0], ci[1]
    if lo is not None and hi is not None:
        lo_pct = int(round(float(lo) * 100))
        hi_pct = int(round(float(hi) * 100))
        n_str = f", n={n}" if n is not None else ""
        return f" [{lo_pct}%–{hi_pct}%{n_str}]"
    return ""


def _format_ask_option(i: int, opt: Dict[str, Any]) -> str:
    conf_pct, tier = _format_option_confidence(opt)
    rec_prefix = "(Recommended) " if i == 0 else ""
    desc = opt.get("description") or opt.get("label") or opt.get("text") or opt.get("key", f"Option {i+1}")
    interval_str = _format_option_interval(opt)
    return f"{rec_prefix}(Confidence: {conf_pct}% - {tier}){interval_str} {desc}"


def _match_string_selection(clean_sel: str, options: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for i, opt in enumerate(options):
        key = str(opt.get("key", "")).lower()
        label = str(opt.get("label", opt.get("description", ""))).lower()
        if clean_sel == key or clean_sel in label or (key and key in clean_sel) or f"option {i+1}" in clean_sel:
            return opt
    return options[0] if options else None


def _match_selection(
    selection: Union[str, int, Dict[str, Any]], options: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    if isinstance(selection, dict):
        return selection
    if isinstance(selection, int) and 0 <= selection < len(options):
        return options[selection]
    if isinstance(selection, str):
        return _match_string_selection(selection.strip().lower(), options)
    return None


@dataclass
class HitlRequirementEnvelope:
    """Governed requirement envelope bundling decision metadata with execution audit."""

    decision_id: str
    category: str
    domain: str
    question: str
    raw_options: List[Dict[str, Any]]
    calibrated_options: List[Dict[str, Any]]
    margin_delta: float
    entropy_score: float
    should_surface: bool
    rationale: str
    selected_option: Optional[Dict[str, Any]] = None
    operator_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved_at: Optional[str] = None
    resolution_receipt: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_ask_user_question_payload(self) -> Dict[str, Any]:
        """Format interactive options payload for ask_user_question tool."""
        formatted_options = [
            _format_ask_option(i, opt) for i, opt in enumerate(self.calibrated_options)
        ]
        qb_options: List[str] = []
        try:
            from dual_pipeline.hitl_governance_core.question_builder import format_option_text

            for i, opt in enumerate(self.calibrated_options):
                conf_pct, tier = _format_option_confidence(opt, strict=False)
                desc = str(opt.get("description") or opt.get("label") or opt.get("text") or opt.get("key", f"Option {i+1}"))
                qb_options.append(
                    format_option_text(
                        label=desc,
                        confidence_pct=conf_pct,
                        tier=tier,
                        impact=str(opt.get("impact_statement") or ""),
                        summary=opt.get("plain_english_summary"),
                        recommended=(i == 0),
                    )
                )
        except Exception:
            qb_options = list(formatted_options)
        return {
            "questions": [
                {
                    "question": self.question,
                    "is_multi_select": False,
                    "options": formatted_options,
                }
            ],
            "question_builder_options": qb_options,
        }

    # Deprecated backward compatibility alias
    to_ask_question_payload = to_ask_user_question_payload

    def resolve_and_persist(
        self,
        selection: Union[str, int, Dict[str, Any]],
        operator_id: str = "human_operator",
        store: Optional[HITLDecisionStore] = None,
        source_message: Optional[Dict[str, Any]] = None,
        persist_autonomous: bool = False,
    ) -> Dict[str, Any]:
        """Resolve envelope with operator response and persist to ledger."""
        if source_message is not None:
            is_valid_origin, origin_reason = validate_approval_origin(source_message)
            if not is_valid_origin:
                raise PermissionError(f"SYNTHETIC_APPROVAL_REJECTED: {origin_reason}")

        chosen_option = _match_selection(selection, self.calibrated_options)
        if chosen_option is None:
            raise ValueError(f"Unable to match selection '{selection}' to available options.")

        self.selected_option = chosen_option
        self.operator_id = operator_id
        self.resolved_at = datetime.now(timezone.utc).isoformat()

        # Build cryptographic receipt
        receipt_content = json.dumps(
            {
                "decision_id": self.decision_id,
                "category": self.category,
                "domain": self.domain,
                "selected_option": chosen_option,
                "margin_delta": self.margin_delta,
                "entropy_score": self.entropy_score,
                "operator_id": operator_id,
                "resolved_at": self.resolved_at,
            },
            sort_keys=True,
        )
        receipt_digest = hashlib.sha256(receipt_content.encode("utf-8")).hexdigest()
        self.resolution_receipt = {
            "receipt_digest": receipt_digest,
            "decision_id": self.decision_id,
            "status": "RESOLVED",
            "resolved_at": self.resolved_at,
            "operator_id": operator_id,
        }

        # Synchronously persist into governed ledger only if surfaced or human or explicit
        from dual_pipeline.hitl_governance_core.learning_eligibility import is_autonomous_record
        is_auto = (not self.should_surface) and is_autonomous_record({"operator_id": operator_id})
        if not is_auto or persist_autonomous:
            decision_store = store or HITLDecisionStore()
            decision_store.record_decision(
                decision_id=self.decision_id,
                category=self.category,
                prompt=self.question,
                options=self.calibrated_options,
                selected_option=chosen_option,
                rationale=self.rationale,
                operator_id=operator_id,
                metadata={"domain": self.domain},
            )
            try:
                decision_store.export_learned_priors()
            except Exception as e:
                logger.warning("Failed to refresh hitl_prior_calibration.json: %s", e)

            logger.info(
                "Persisted HITL decision '%s' [%s] with digest %s",
                self.decision_id,
                self.category,
                receipt_digest[:12],
            )
        else:
            logger.debug(
                "Quarantined non-surfaced autonomous decision '%s' [%s] with digest %s from human ledger",
                self.decision_id,
                self.category,
                receipt_digest[:12],
            )
        return self.resolution_receipt


def create_hitl_envelope(
    category: str,
    domain: str,
    question: str,
    options: List[Dict[str, Any]],
    store: Optional[HITLDecisionStore] = None,
    threshold: Optional[float] = None,
    entropy_threshold: float = 0.85,
) -> HitlRequirementEnvelope:
    """Create and evaluate a validated HitlRequirementEnvelope.

    Applies Bayesian calibration, calculates margin spread Delta and Shannon entropy H(p),
    and sets should_surface according to governance rules.
    """
    if not options or len(options) < 2:
        raise ValueError(f"HITL requires at least 2 options for evaluation; received {len(options) if options else 0}")

    eff_threshold = threshold if threshold is not None else get_hitl_ambiguity_margin_threshold()
    decision_store = store or HITLDecisionStore()
    calibrated = decision_store.calibrate_options(options, category=category)
    sorted_calibrated = sorted(
        calibrated,
        key=lambda o: float(o.get("confidence_score", o.get("confidence", 0.0))),
        reverse=True,
    )

    top_conf = float(sorted_calibrated[0].get("confidence_score", sorted_calibrated[0].get("confidence", 0.0)))
    runner_up_conf = float(sorted_calibrated[1].get("confidence_score", sorted_calibrated[1].get("confidence", 0.0)))
    margin_delta = round(top_conf - runner_up_conf, 4)

    confs = [float(o.get("confidence_score", o.get("confidence", 0.0))) for o in sorted_calibrated]
    entropy_score = compute_normalized_entropy(confs)

    # Surfacing criteria: margin < eff_threshold OR normalized entropy > entropy_threshold
    if margin_delta < eff_threshold:
        should_surface = True
        rationale = (
            f"Confidence margin ({margin_delta:.1%}) between top ({top_conf:.1%}) and "
            f"runner-up ({runner_up_conf:.1%}) is under {eff_threshold:.1%} threshold; mandatory HITL required."
        )
    elif entropy_score > entropy_threshold:
        should_surface = True
        rationale = (
            f"Normalized entropy ({entropy_score:.3f}) exceeds {entropy_threshold:.3f}; "
            "high distributional uncertainty forces mandatory HITL review."
        )
    else:
        should_surface = False
        rationale = (
            f"Confidence margin ({margin_delta:.1%}) meets or exceeds {eff_threshold:.1%} threshold "
            f"and entropy ({entropy_score:.3f} <= {entropy_threshold:.3f}) is bounded; resolving autonomously."
        )

    return HitlRequirementEnvelope(
        decision_id=f"hitl_{category}_{uuid.uuid4().hex[:12]}",
        category=category,
        domain=domain,
        question=question,
        raw_options=options,
        calibrated_options=sorted_calibrated,
        margin_delta=margin_delta,
        entropy_score=entropy_score,
        should_surface=should_surface,
        rationale=rationale,
    )
