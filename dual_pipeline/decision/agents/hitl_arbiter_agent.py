"""Agent-D4: Human-in-the-Loop (HITL) Ambiguity Arbiter.

Enforces HITL Governance Rules:
1. Ambiguity Margin Gate: Surface HITL decision when margin between top two options is within threshold.
2. Autonomous Progression: Decisively qualified options proceed autonomously under an audit receipt.
3. Atomic Presentation: One decision at a time, validated via QuestionBuilder with '(Recommended)' first.
4. Post-Learning Confidence Recalibration: Connects to HITLDecisionStore and HealingDecisionStore.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from dual_pipeline.a2a import A2ATask, AgentCard, ComputePosture, ModelBinding
from dual_pipeline.healing_policy import sanitize_to_layman_text
from dual_pipeline.hitl_criticality import CriticalityTier
from dual_pipeline.hitl_governance import (
    DEFAULT_MARGIN_THRESHOLD,
    HITLDecisionStore,
    HitlSurfacingMode,
    evaluate_hitl_surfacing_gate,
)
from dual_pipeline.hitl_governance_core.bayesian import _match_keys, canonicalize_pattern_key
from dual_pipeline.hitl_governance_core.layman_clarity import contains_jargon
from dual_pipeline.hitl_governance_core.question_builder import QuestionBuilder, format_option_text
from dual_pipeline.storage.healing_decision_store import HealingDecisionStore, record_healing_decision

logger = logging.getLogger("dual_pipeline.agents.hitl_arbiter")


def get_hitl_arbiter_card(
    model_id: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
) -> AgentCard:
    from dual_pipeline.model_registry import ModelTier, get_binding
    binding = get_binding(ModelTier.FRONTIER_HIGH_REASONING)
    return AgentCard(
        agent_id="urn:nee:agent:pipeline-d:hitl-arbiter:1.0.0",
        name="HitlArbiterAgent",
        version="1.0.0",
        role="Human-in-the-Loop Ambiguity Arbiter",
        domain_focus="30% ambiguity margin gating, atomic clickable decision card formulation, Bayesian prior calibration",
        compute_posture=ComputePosture.CLIENT_SAFE,
        model_binding=ModelBinding(
            model_id=model_id or binding.model_id,
            provider="openai",
            reasoning_effort=reasoning_effort or "high",
        ),
        skills=["Evaluate30PercentMarginGate", "CalibrateDecisionPriors", "FormulateAtomicHitlCard"],
        allowed_tools=["hitl_store"],
    )


def _apply_single_healing_posterior(opt: Dict[str, Any], merged: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, Any], bool]:
    item = dict(opt)
    canon_k = canonicalize_pattern_key(str(item.get("key") or item.get("label") or ""))
    base_score = float(item.get("confidence_score", 0.50))
    for hk, hv in merged.items():
        h_total = int(hv.get("total_count") or hv.get("total") or 0)
        if h_total >= 1 and _match_keys(canonicalize_pattern_key(hk), canon_k):
            h_post = float(hv.get("posterior_confidence") or hv.get("posterior_mean") or base_score)
            new_score = max(0.05, min(0.99, round(0.45 * base_score + 0.55 * h_post, 4)))
            item["confidence_score"] = new_score
            item["confidence_level"] = "HIGH" if new_score >= 0.85 else ("MEDIUM" if new_score >= 0.50 else "LOW")
            item["prior_adjustment"] = round(new_score - float(item.get("initial_confidence", base_score)), 4)
            item["confidence_sample_count"] = int(item.get("confidence_sample_count") or 0) + h_total
            return item, True
    return item, False


def _blend_healing_posteriors(
    calibrated_options: List[Dict[str, Any]],
    category: str,
    healing_db_path: Optional[Path | str],
) -> List[Dict[str, Any]]:
    if healing_db_path is None and "healing" not in category.lower():
        return calibrated_options
    try:
        post = HealingDecisionStore(db_path=healing_db_path).compute_empirical_posteriors()
        merged: Dict[str, Dict[str, Any]] = {}
        for raw_k, stats in post.items():
            if isinstance(stats, dict) and raw_k.startswith("repair_class:"):
                merged[raw_k.split("repair_class:", 1)[1]] = stats
        if not merged:
            return calibrated_options
        pairs = [_apply_single_healing_posterior(o, merged) for o in calibrated_options]
        enriched = [p[0] for p in pairs]
        if any(p[1] for p in pairs):
            enriched.sort(key=lambda x: float(x.get("confidence_score", 0.0)), reverse=True)
        return enriched
    except Exception:
        return calibrated_options


def _add_card_option_to_builder(
    builder: QuestionBuilder,
    opt: Any,
    idx: int,
    top_key: str,
    top_option: Any,
) -> Tuple[str, bool]:
    if not isinstance(opt, dict):
        return str(opt), False
    key = str(opt.get("key") or opt.get("action") or opt.get("label") or f"option_{idx + 1}")
    raw_label = str(opt.get("label") or opt.get("title") or key)
    score = float(opt.get("confidence_score", 0.50))
    tier = str(opt.get("confidence_level", "MEDIUM"))
    rationale = str(opt.get("rationale") or opt.get("description") or "Calibrated option.")
    is_rec = key == top_key or opt == top_option or (idx == 0 and not top_key)
    has_jargon = contains_jargon(raw_label) or "_" in raw_label or "/" in raw_label
    raw_str = format_option_text(label=raw_label, confidence_pct=round(score * 100), tier=tier, impact=rationale, recommended=is_rec)

    clean_label = sanitize_to_layman_text(raw_label, fallback=f"Resolution option {idx + 1}")[:80]
    clean_summary = sanitize_to_layman_text(rationale, fallback=f"Select {clean_label.lower()}.")[:110]
    clean_impact = (
        "Ensures the highest confidence resolution is applied and updates future decision priors."
        if is_rec
        else "Preserves this alternative path and ensures the operator preference is recorded."
    )
    builder.add_option(
        label=clean_label,
        plain_english_summary=clean_summary,
        impact_statement=clean_impact,
        confidence_score=score,
        sample_size=int(opt.get("confidence_sample_count") or 0) or None,
        recommended=is_rec,
        prior_adjustment=float(opt.get("prior_adjustment", 0.0)),
    )
    return raw_str, has_jargon


class HitlArbiterAgent:
    """Agent evaluating ambiguity margins and structuring atomic operator decisions."""

    def __init__(
        self,
        ledger_path: Optional[Path | str] = None,
        surfacing_mode: Optional[HitlSurfacingMode] = None,
        margin_threshold: Optional[float] = None,
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        healing_db_path: Optional[Path | str] = None,
    ) -> None:
        from dual_pipeline.model_registry import ModelTier, get_binding
        binding = get_binding(ModelTier.FRONTIER_HIGH_REASONING)
        self.card = get_hitl_arbiter_card(
            model_id=model_id or binding.model_id,
            reasoning_effort=reasoning_effort or binding.reasoning_effort or "high",
        )
        self.store = HITLDecisionStore(ledger_path=ledger_path)
        self.surfacing_mode = surfacing_mode
        self.margin_threshold = margin_threshold
        self.ledger_path = self.store.ledger_path
        self.healing_db_path = healing_db_path

    def _resolve_thresh_and_mode(
        self,
        margin_threshold: Optional[float],
        surfacing_mode: Optional[HitlSurfacingMode | str],
    ) -> Tuple[float, HitlSurfacingMode | str, str]:
        thresh = margin_threshold if margin_threshold is not None else (self.margin_threshold if self.margin_threshold is not None else DEFAULT_MARGIN_THRESHOLD)
        mode = surfacing_mode if surfacing_mode is not None else (self.surfacing_mode if self.surfacing_mode is not None else HitlSurfacingMode.AMBIGUITY_LE_THRESHOLD)
        mode_str = mode.value if isinstance(mode, HitlSurfacingMode) else str(mode)
        return thresh, mode, mode_str

    def arbitrate_decision(
        self,
        decision_id: str,
        category: str,
        question: str,
        options: List[Dict[str, Any]],
        margin_threshold: Optional[float] = None,
        surfacing_mode: Optional[HitlSurfacingMode | str] = None,
        auto_record_ledger: bool = True,
        criticality_tier: Optional[CriticalityTier | int] = None,
        context: Optional[Mapping[str, Any]] = None,
        entropy_threshold: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Arbitrates whether a decision surfaces to HITL or proceeds autonomously."""
        thresh, mode, mode_str = self._resolve_thresh_and_mode(margin_threshold, surfacing_mode)
        if not options:
            return {
                "decision_id": decision_id,
                "status": "HITL_REQUIRED",
                "should_surface": True,
                "margin": 0.0,
                "margin_threshold": thresh,
                "surfacing_mode": mode_str,
                "reason": "No options provided; human guidance required.",
                "atomic_card": None,
            }

        eff_healing_db = (context or {}).get("healing_db_path") or self.healing_db_path
        calibrated = _blend_healing_posteriors(self.store.calibrate_options(options, category=category), category, eff_healing_db)
        gate_res = evaluate_hitl_surfacing_gate(
            calibrated, margin_threshold=thresh, surfacing_mode=mode, criticality_tier=criticality_tier, context=context, entropy_threshold=entropy_threshold
        )
        should_surface = gate_res["should_surface"]
        margin = gate_res["margin"]
        top_opt = gate_res["top_option"]

        if eff_healing_db is not None or "healing" in category.lower():
            record_healing_decision(
                {
                    "run_id": f"run-arbiter-{decision_id}",
                    "subsystem": "hitl_arbiter_agent",
                    "component": "HitlArbiterAgent.arbitrate_decision",
                    "scope": category,
                    "repair_class": str((top_opt or {}).get("key") or (top_opt or {}).get("label") or category),
                    "action_taken": "ESCALATE_HITL" if should_surface else "AUTO_APPLY",
                    "outcome_status": "ESCALATED_HITL" if should_surface else "HEALED",
                    "confidence_top": float(gate_res.get("top_confidence", 0.0)),
                    "confidence_second": float(gate_res.get("runner_up_confidence", 0.0)),
                    "ambiguity_margin": float(margin),
                    "hitl_escalated": bool(should_surface),
                    "invariants_preserved": True,
                    "defect_reasons": [str(gate_res.get("reason", ""))] if should_surface else [],
                    "audit_trail": [str(gate_res.get("reason", ""))],
                },
                db_path=eff_healing_db,
            )

        if should_surface:
            return {
                "decision_id": decision_id,
                "status": "HITL_REQUIRED",
                "should_surface": True,
                "margin": margin,
                "margin_threshold": thresh,
                "surfacing_mode": mode_str,
                "top_option": top_opt,
                "runner_up_option": gate_res["runner_up_option"],
                "reason": gate_res["reason"],
                "atomic_card": self._format_atomic_card(decision_id, category, question, calibrated, top_opt, margin),
            }

        recorded_entry = None
        if auto_record_ledger and top_opt:
            recorded_entry = self.store.record_decision(
                decision_id=decision_id,
                category=category,
                prompt=question,
                options=calibrated,
                selected_option=top_opt,
                rationale=f"Autonomous resolution: confidence margin {margin * 100:.1f}% > {thresh * 100:.0f}%.",
                operator_id="autonomous_engine",
                metadata={"autonomous": True, "margin": margin, "margin_threshold": thresh, "surfacing_mode": mode_str, "model_id": self.card.model_binding.model_id},
            )

        return {
            "decision_id": decision_id,
            "status": "AUTONOMOUS_APPROVED",
            "should_surface": False,
            "margin": margin,
            "margin_threshold": thresh,
            "surfacing_mode": mode_str,
            "selected_option": top_opt,
            "top_option": top_opt,
            "runner_up_option": gate_res["runner_up_option"],
            "reason": gate_res["reason"],
            "recorded_entry": recorded_entry,
            "audit_receipt": {
                "decision_id": decision_id,
                "category": category,
                "autonomous_approval": True,
                "top_confidence": gate_res["top_confidence"],
                "runner_up_confidence": gate_res["runner_up_confidence"],
                "margin": margin,
                "margin_threshold": thresh,
                "surfacing_mode": str(mode),
                "model_id": self.card.model_binding.model_id,
            },
        }

    def _format_atomic_card(
        self,
        decision_id: str,
        category: str,
        question: str,
        options: List[Dict[str, Any]],
        top_option: Any,
        margin: float,
    ) -> Dict[str, Any]:
        """Formats atomic clickable card for ask_user_question tool via QuestionBuilder."""
        is_speaker = "speaker" in category.lower()
        if is_speaker:
            from dual_pipeline.speaker_attribution_core.review_context_render import validate_visible_speaker_context
            question = validate_visible_speaker_context(question)

        top_key = top_option.get("key") if isinstance(top_option, dict) else str(top_option)
        clean_q = sanitize_to_layman_text(
            question.splitlines()[-1] if is_speaker and question.splitlines() else question,
            fallback="Which option should be selected to resolve this ambiguity?",
        )
        if not clean_q.endswith("?"):
            clean_q = clean_q.rstrip(".") + "?"

        raw_keys = [str(o.get("key") or o.get("label") or "") for o in options if isinstance(o, dict)]
        meta = {"decision_id": decision_id, "category": category, "calibrate": True, "already_calibrated": True, "raw_option_keys": raw_keys}
        builder = QuestionBuilder(question=clean_q[:295], metadata=meta)
        pairs = [_add_card_option_to_builder(builder, opt, idx, top_key, top_option) for idx, opt in enumerate(options)]
        raw_formatted, has_raw_jargon = [p[0] for p in pairs], any(p[1] for p in pairs)

        payload = builder.build() if len(builder.options) >= 2 else None
        display_opts = payload.display_options if (payload and not has_raw_jargon) else raw_formatted
        return {
            "decision_id": decision_id,
            "question": question if is_speaker else clean_q,
            "category": category,
            "options": display_opts,
            "structured_options": payload.options if payload else [],
            "question_payload": payload.to_dict() if payload else None,
            "ask_user_question": payload.to_tool_call("ask_user_question") if payload else None,
            "raw_options": options,
            "is_multi_select": False,
            "toolSummary": "HITL decision resolution",
            "toolAction": "Resolving decision ambiguity",
        }

    def record_feedback(
        self,
        decision_id: str,
        category: str,
        prompt: str,
        options: List[Any],
        selected_option: Any,
        rationale: Optional[str] = None,
        operator_id: str = "human_operator",
    ) -> Dict[str, Any]:
        """Records human operator decision into the governed ledger."""
        return self.store.record_decision(
            decision_id=decision_id,
            category=category,
            prompt=prompt,
            options=options,
            selected_option=selected_option,
            rationale=rationale,
            operator_id=operator_id,
        )

    async def handle_task(self, task: A2ATask) -> Dict[str, Any]:
        """Handles A2A task invocation."""
        decision_id = task.input_payload.get("decision_id", "decision-1")
        category = task.input_payload.get("category", "architectural_choice")
        question = task.input_payload.get("question", "Select target path")
        options = task.input_payload.get("options", [])
        margin_threshold = float(task.input_payload.get("margin_threshold", DEFAULT_MARGIN_THRESHOLD))

        operator_feedback = task.input_payload.get("operator_feedback")
        if operator_feedback:
            rec = self.record_feedback(
                decision_id=decision_id,
                category=category,
                prompt=question,
                options=options,
                selected_option=operator_feedback.get("selected_option"),
                rationale=operator_feedback.get("rationale"),
            )
            return {"task_id": task.task_id, "status": "COMPLETED", "action": "RECORD_FEEDBACK", "record": rec}

        result = self.arbitrate_decision(
            decision_id=decision_id, category=category, question=question, options=options, margin_threshold=margin_threshold
        )
        return {"task_id": task.task_id, "status": "COMPLETED", "arbitration_result": result}
