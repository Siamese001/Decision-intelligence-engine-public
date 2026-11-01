"""Antigravity UI Bridge for Human-in-the-Loop (HITL) Interactive Clicked Options.

Connects Pipelines A–D to the Antigravity UI interactive option selection flow,
enforcing:
1. When confidence difference < 25% (or configured threshold under AMBIGUITY_LE_THRESHOLD),
   presents atomic clickable cards formatted for UI Antigravity modal dialogs.
2. When confidence difference >= 25%, autonomously records the decision in the governed ledger
   (hitl_decision_ledger.json) and persists to store to calibrate priors.
3. Incorporates Bayesian feedback learning from operator clicks to boost future confidence scores.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional

if TYPE_CHECKING:
    from dual_pipeline.decision.agents.hitl_arbiter_agent import HitlArbiterAgent

from dual_pipeline.hitl_governance import (
    DEFAULT_MARGIN_THRESHOLD,
    HitlSurfacingMode,
)

logger = logging.getLogger("dual_pipeline.hitl_ui_bridge")


class AntigravityUIBridge:
    """Bridge coordinating interactive Antigravity UI decision card presentation and ledger recording."""

    def __init__(
        self,
        arbiter: Optional[HitlArbiterAgent] = None,
        ledger_path: Optional[Path | str] = None,
        surfacing_mode: HitlSurfacingMode = HitlSurfacingMode.AMBIGUITY_LE_THRESHOLD,
        margin_threshold: float = DEFAULT_MARGIN_THRESHOLD,
        mock_mode: bool = False,
        interactive_callback: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        if arbiter is not None:
            self.arbiter = arbiter
            self.surfacing_mode = arbiter.surfacing_mode or surfacing_mode
            self.margin_threshold = arbiter.margin_threshold if arbiter.margin_threshold is not None else margin_threshold
        else:
            from dual_pipeline.decision.agents.hitl_arbiter_agent import (
                HitlArbiterAgent,  # noqa: E402
            )
            self.surfacing_mode = surfacing_mode
            self.margin_threshold = margin_threshold
            self.arbiter = HitlArbiterAgent(
                ledger_path=ledger_path,
                surfacing_mode=surfacing_mode,
                margin_threshold=margin_threshold,
            )
        self.mock_mode = mock_mode
        self.interactive_callback = interactive_callback

    def arbitrate_and_present(
        self,
        *,
        pipeline_id: str,
        decision_id: str,
        category: str,
        question: str,
        options: List[Dict[str, Any]],
        margin_threshold: Optional[float] = None,
        surfacing_mode: Optional[HitlSurfacingMode | str] = None,
        auto_record_ledger: bool = True,
        criticality_tier: Optional[Any] = None,
        context: Optional[Mapping[str, Any]] = None,
        entropy_threshold: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Arbitrates a pipeline decision, surfacing to UI under ambiguity or auto-recording under decisive spread.

        Returns:
            Dict containing:
            - status: "HITL_REQUIRED" | "AUTONOMOUS_APPROVED" | "OPERATOR_RESOLVED"
            - should_surface: bool
            - selected_option: Any
            - margin: float
            - atomic_card: Optional[Dict[str, Any]]
            - ledger_record: Optional[Dict[str, Any]]
        """
        mode = surfacing_mode if surfacing_mode is not None else self.surfacing_mode
        thresh = margin_threshold if margin_threshold is not None else self.margin_threshold
        if "speaker" in category.lower():
            from dual_pipeline.speaker_attribution_core.review_context_render import validate_visible_speaker_context
            visible_context = validate_visible_speaker_context((context or {}).get("visible_context"))
            question = f"{visible_context}\n\n{question}"
        arbitration = self.arbiter.arbitrate_decision(
            decision_id=decision_id,
            category=category,
            question=question,
            options=options,
            margin_threshold=thresh,
            surfacing_mode=mode,
            auto_record_ledger=auto_record_ledger,
            criticality_tier=criticality_tier,
            context=context,
            entropy_threshold=entropy_threshold,
        )

        should_surface = arbitration.get("should_surface", False)
        margin = arbitration.get("margin", 0.0)
        atomic_card = arbitration.get("atomic_card")

        if not should_surface:
            # Margin <= threshold: Auto-recorded in ledger to improve HITL criteria
            return {
                "pipeline_id": pipeline_id,
                "decision_id": decision_id,
                "status": "AUTONOMOUS_APPROVED",
                "should_surface": False,
                "margin": margin,
                "selected_option": arbitration.get("selected_option") or arbitration.get("top_option"),
                "top_option": arbitration.get("top_option"),
                "runner_up_option": arbitration.get("runner_up_option"),
                "reason": arbitration.get("reason"),
                "atomic_card": None,
                "ledger_record": arbitration.get("recorded_entry"),
                "audit_receipt": arbitration.get("audit_receipt"),
            }

        # Margin > threshold: Mandatory HITL with clicked options in UI Antigravity
        if self.interactive_callback:
            return self._handle_interactive_ui_response(
                pipeline_id=pipeline_id,
                decision_id=decision_id,
                category=category,
                question=question,
                options=options,
                margin=margin,
                atomic_card=atomic_card,
                arbitration=arbitration,
            )

        # Awaiting authentic operator click / response: return card awaiting operator input
        return {
            "pipeline_id": pipeline_id,
            "decision_id": decision_id,
            "status": "HITL_REQUIRED",
            "should_surface": True,
            "margin": margin,
            "selected_option": None,
            "top_option": arbitration.get("top_option"),
            "runner_up_option": arbitration.get("runner_up_option"),
            "reason": arbitration.get("reason"),
            "atomic_card": atomic_card,
            "ledger_record": None,
        }

    def _handle_interactive_ui_response(
        self,
        *,
        pipeline_id: str,
        decision_id: str,
        category: str,
        question: str,
        options: List[Dict[str, Any]],
        margin: float,
        atomic_card: Optional[Dict[str, Any]],
        arbitration: Dict[str, Any],
    ) -> Dict[str, Any]:
        try:
            ui_response = self.interactive_callback(atomic_card)
        except Exception as exc:
            logger.warning("Interactive UI callback failed or timed out: %s", exc)
            ui_response = None
        if not ui_response:
            return {
                "pipeline_id": pipeline_id,
                "decision_id": decision_id,
                "status": "AWAITING_APPROVAL",
                "should_surface": True,
                "margin": margin,
                "selected_option": None,
                "top_option": arbitration.get("top_option"),
                "runner_up_option": arbitration.get("runner_up_option"),
                "reason": "Interactive callback returned empty response; awaiting valid operator selection.",
                "atomic_card": atomic_card,
                "ledger_record": None,
            }

        if ui_response.get("declined") or ui_response.get("status") in ("DECLINED", "CANCELLED"):
            declined_choice = ui_response.get("selected_option") or "DECLINED"
            feedback_record = self.arbiter.record_feedback(
                decision_id=decision_id,
                category=category,
                prompt=question,
                options=options,
                selected_option=declined_choice,
                rationale=ui_response.get("rationale", "Operator explicitly declined"),
                operator_id=ui_response.get("operator_id", "human_operator"),
                outcome_type="OVERRIDDEN",
            )
            return {
                "pipeline_id": pipeline_id,
                "decision_id": decision_id,
                "status": "DECLINED",
                "should_surface": True,
                "margin": margin,
                "selected_option": None,
                "top_option": arbitration.get("top_option"),
                "runner_up_option": arbitration.get("runner_up_option"),
                "reason": f"Explicitly declined by operator: {declined_choice}",
                "atomic_card": atomic_card,
                "ledger_record": feedback_record,
            }

        selected_choice = ui_response.get("selected_option")
        if not selected_choice:
            return {
                "pipeline_id": pipeline_id,
                "decision_id": decision_id,
                "status": "AWAITING_APPROVAL",
                "should_surface": True,
                "margin": margin,
                "selected_option": None,
                "top_option": arbitration.get("top_option"),
                "runner_up_option": arbitration.get("runner_up_option"),
                "reason": "Operator response contained no selected option; awaiting valid operator selection.",
                "atomic_card": atomic_card,
                "ledger_record": None,
            }

        operator_rationale = ui_response.get("rationale", "Operator clicked option in UI Antigravity")

        feedback_record = self.arbiter.record_feedback(
            decision_id=decision_id,
            category=category,
            prompt=question,
            options=options,
            selected_option=selected_choice,
            rationale=operator_rationale,
            operator_id=ui_response.get("operator_id", "human_operator"),
        )

        return {
            "pipeline_id": pipeline_id,
            "decision_id": decision_id,
            "status": "OPERATOR_RESOLVED",
            "should_surface": True,
            "margin": margin,
            "selected_option": selected_choice,
            "top_option": arbitration.get("top_option"),
            "runner_up_option": arbitration.get("runner_up_option"),
            "reason": f"Resolved by operator via UI Antigravity clicked option: {selected_choice}",
            "atomic_card": atomic_card,
            "ledger_record": feedback_record,
        }
