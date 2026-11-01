"""Agent-D0: Dialectical Decision Orchestrator managing debate, fast-path sync, and HITL."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from dual_pipeline.a2a import A2ATask, AgentCard, ComputePosture, ModelBinding
from dual_pipeline.l0.runtime_gate_preservation import validate_replan_clearance
from dual_pipeline.model_registry import ModelTier, resolve_tier_model
from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold

from .briefing_synthesis_agent import BriefingSynthesisAgent
from .dialectical_perspective_agent import DialecticalPerspectiveAgent, PerspectiveType
from .fast_path_sync_agent import FastPathSyncAgent
from .l1_decision_cognition import L1DecisionCognition, L1DecisionPlanContract
from .roles import CANONICAL_18_ROLES

logger = logging.getLogger("dual_pipeline.agents.decision_orchestrator")

DEFAULT_MODEL, DEFAULT_EFFORT = resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)


def get_decision_orchestrator_card(
    model_id: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
) -> AgentCard:
    effective_model = model_id or DEFAULT_MODEL
    effective_effort = reasoning_effort or DEFAULT_EFFORT or "high"
    return AgentCard(
        agent_id="urn:nee:agent:pipeline-d:decision-orchestrator:1.0.0",
        name="DecisionOrchestrator",
        version="1.0.0",
        role="Dialectical Decision Coordinator",
        domain_focus="L1 decision cognition, multi-agent dialectical deliberation, fast-path fusion, HITL gating, terminal state determination",
        compute_posture=ComputePosture.CLIENT_SAFE,
        model_binding=ModelBinding(model_id=effective_model, provider="openai", reasoning_effort=effective_effort),
        skills=[
            "PlanL1DecisionCognition", "OrchestrateDialecticalDebate", "SynthesizeTradeOffMatrix",
            "EnforceAmbiguityMarginGate", "ProduceTerminalDecisionState", "ExecuteDialecticalRollout",
        ],
        allowed_tools=["openai_chat_completion", "a2a_router"],
    )


class DecisionOrchestrator:
    """Master orchestrator for Pipeline D Dialectical Decision Intelligence."""

    def __init__(
        self,
        red_team_agent: Optional[Any] = None,
        feasibility_agent: Optional[Any] = None,
        fast_path_agent: Optional[FastPathSyncAgent] = None,
        hitl_arbiter: Optional[Any] = None,
        briefing_agent: Optional[BriefingSynthesisAgent] = None,
        ui_bridge: Optional[Any] = None,
        post_debate_reviewer: Optional[Any] = None,
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        integrator: Optional[Any] = None,
    ) -> None:
        self.model_id = model_id or DEFAULT_MODEL
        self.reasoning_effort = reasoning_effort or DEFAULT_EFFORT or "high"
        self.card = get_decision_orchestrator_card(model_id=self.model_id, reasoning_effort=self.reasoning_effort)
        if integrator is not None:
            self.integrator = integrator
        else:
            try:
                from dual_pipeline.learning_adapters.live_pipeline_integrator import (
                    get_live_pipeline_integrator,
                )
                self.integrator = get_live_pipeline_integrator()
            except ImportError:
                self.integrator = None

        # L1 Decision Cognition planner
        self.l1_cognition = L1DecisionCognition(default_model=self.model_id, reasoning_effort=self.reasoning_effort)

        self.red_team = red_team_agent or DialecticalPerspectiveAgent(
            perspective=PerspectiveType.RED_TEAM,
            model_id=self.model_id,
            reasoning_effort=self.reasoning_effort,
        )
        self.feasibility = feasibility_agent or DialecticalPerspectiveAgent(
            perspective=PerspectiveType.STRATEGIC_FEASIBILITY,
            model_id=self.model_id,
            reasoning_effort=self.reasoning_effort,
        )
        self.fast_path = fast_path_agent or FastPathSyncAgent()

        # L5 Arbiter: optionally injected to preserve strict L4 -> L5 layer decoupling
        if hitl_arbiter is not None:
            self.hitl_arbiter = hitl_arbiter
        else:
            try:
                from .hitl_arbiter_agent import HitlArbiterAgent
                self.hitl_arbiter = HitlArbiterAgent(model_id=self.model_id, reasoning_effort=self.reasoning_effort)
            except (ImportError, Exception):
                self.hitl_arbiter = None

        if ui_bridge is not None:
            self.ui_bridge = ui_bridge
        else:
            try:
                from dual_pipeline.hitl_ui_bridge import AntigravityUIBridge
                self.ui_bridge = AntigravityUIBridge(arbiter=self.hitl_arbiter)
            except Exception:
                self.ui_bridge = None

        self.briefing = briefing_agent or BriefingSynthesisAgent(model_id=self.model_id, reasoning_effort=self.reasoning_effort)
        from .post_debate_reviewer import DecisionPostDebateReviewer
        self.post_debate_reviewer = post_debate_reviewer or DecisionPostDebateReviewer()

    def _validate_l1_plan(
        self,
        decision_id: str,
        question: str,
        options: List[Dict[str, Any]],
        canonical_evidence: List[Dict[str, Any]],
        live_turns: List[Dict[str, Any]],
        category: str,
    ) -> L1DecisionPlanContract:
        l1_plan: L1DecisionPlanContract = self.l1_cognition.plan(
            decision_id=decision_id,
            question=question,
            options=options,
            canonical_evidence=canonical_evidence,
            live_turns=live_turns,
            ledger_path=getattr(self.hitl_arbiter, "ledger_path", None),
            metadata={"category": category},
        )
        logger.info(
            f"DecisionOrchestrator L1 Plan: type={l1_plan.decision_type}, valid={l1_plan.is_valid_decision_frame}, flaws={l1_plan.fatal_flaws_detected}"
        )
        if not l1_plan.sanitization_passed:
            raise ValueError(f"L0 ROUTING BLOCKED: L1 decision sanitization failed: {l1_plan.quarantine_reasons}")
        if not l1_plan.is_valid_decision_frame:
            flaws_str = ", ".join(l1_plan.fatal_flaws_detected) if l1_plan.fatal_flaws_detected else "Redundant or non-orthogonal options"
            raise ValueError(f"L0 ROUTING BLOCKED: Invalid decision frame: {flaws_str}")
        return l1_plan

    def _score_options(
        self,
        options: List[Dict[str, Any]],
        risk_map: Dict[str, Dict[str, Any]],
        feasibility_map: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        scored: List[Dict[str, Any]] = []
        for opt in options:
            opt_key = opt.get("key") or opt.get("id") or opt.get("label") or str(opt)
            risk_info = risk_map.get(opt_key, {"risk_score": 0.5, "has_fatal_flaw": False, "risks": []})
            feas_info = feasibility_map.get(opt_key, {"feasibility_score": 0.5, "benefits": []})

            feas_score = feas_info["feasibility_score"]
            risk_score = risk_info["risk_score"]
            has_fatal_flaw = risk_info["has_fatal_flaw"]

            if has_fatal_flaw:
                dialectical_score = 0.05
            else:
                dialectical_score = round(feas_score * (1.0 - 0.7 * risk_score), 3)

            scored.append({
                "key": opt_key,
                "label": opt.get("label") or opt.get("title") or opt_key,
                "confidence_score": dialectical_score,
                "feasibility_score": feas_score,
                "risk_score": risk_score,
                "has_fatal_flaw": has_fatal_flaw,
                "risks": risk_info["risks"],
                "benefits": feas_info.get("benefits", []),
                "description": opt.get("description", ""),
            })
        return scored

    def _arbitrate_scored_options(
        self,
        decision_id: str,
        category: str,
        question: str,
        scored_options: List[Dict[str, Any]],
        margin_threshold: float,
        surfacing_mode: Optional[Any],
    ) -> Dict[str, Any]:
        if self.ui_bridge is not None:
            return self.ui_bridge.arbitrate_and_present(
                pipeline_id="pipeline-d",
                decision_id=decision_id,
                category=category,
                question=question,
                options=scored_options,
                margin_threshold=margin_threshold,
                surfacing_mode=surfacing_mode,
            )
        return self.hitl_arbiter.arbitrate_decision(
            decision_id=decision_id,
            category=category,
            question=question,
            options=scored_options,
            margin_threshold=margin_threshold,
            surfacing_mode=surfacing_mode,
        )

    def _resolve_execution_outcome(
        self,
        should_surface: bool,
        top_opt: Optional[Dict[str, Any]],
        question: str,
        margin: float,
        canonical_evidence: List[Dict[str, Any]],
        canonical_graph_delta: Optional[Dict[str, Any]],
    ) -> tuple[str, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if should_surface:
            return "ESCALATED_HITL", None, None

        if top_opt and top_opt.get("has_fatal_flaw"):
            terminal_state = "BLOCKED_HIGH_RISK"
        elif top_opt and top_opt.get("risk_score", 0.0) >= 0.30:
            terminal_state = "APPROVED_WITH_CONDITIONS"
        else:
            terminal_state = "APPROVED_UNANIMOUS"

        recap = None
        figma_spec = None
        if canonical_evidence and canonical_graph_delta and top_opt:
            try:
                top_label = top_opt.get("label", "Selected Option")
                recap = self.briefing.synthesize_recap(
                    meeting_name=f"Decision Deliberation: {question}", date_str="09/12/2026",
                    start_time_et="10:00 AM", end_time_et="11:00 AM",
                    attendees_by_company={"Apex Global Enterprises": ["Executive Sponsor"], "PartnerAdvisory": ["Chief Architect"]},
                    meeting_objective=f"Evaluate dialectical options for: {question}",
                    decisions=[{"title": f"Selection of {top_label}", "rationale": f"Feasibility {top_opt.get('feasibility_score')}, Risk {top_opt.get('risk_score')}"}],
                    next_steps=[{"item": f"Implement {top_label}", "owner": "Architecture Lead", "due_date": "09/19/2026", "status": "APPROVED"}],
                    evidence=canonical_evidence, canonical_graph_delta=canonical_graph_delta,
                )
                figma_spec = self.briefing.generate_figma_4k_spec(
                    title=f"Executive Decision: {question}", conclusion=f"Execute {top_label}",
                    evidence_points=[f"Confidence margin {margin * 100:.1f}%", f"Feasibility {top_opt.get('feasibility_score')}"],
                    implications=["Commence delivery wave immediately"],
                )
            except Exception as exc:
                logger.warning(f"Executive briefing generation deferred: {exc}")

        return terminal_state, recap, figma_spec

    def record_decision_override(
        self,
        decision_id: str,
        category: str,
        recommended_option: str,
        operator_chosen_option: str,
        run_id: Optional[str] = None,
    ) -> Optional[Any]:
        """Emits DECISION_OVERRIDE outcome event to LivePipelineIntegrator."""
        if not self.integrator:
            return None
        from dual_pipeline.learning_adapters.agent_outcome_adapter import AgentOutcomeAdapter
        evt = AgentOutcomeAdapter.build_decision_override_event(
            decision_id=decision_id,
            category=category,
            recommended_option=recommended_option,
            operator_chosen_option=operator_chosen_option,
            source_agent=self.card.name,
            run_id=run_id,
        )
        return self.integrator.record_outcome(evt)

    def orchestrate_decision(
        self,
        decision_id: str,
        question: str,
        options: List[Dict[str, Any]],
        category: str = "strategic_architecture",
        canonical_evidence: Optional[List[Dict[str, Any]]] = None,
        canonical_graph_delta: Optional[Dict[str, Any]] = None,
        live_turns: Optional[List[Dict[str, Any]]] = None,
        margin_threshold: Optional[float] = None,
        surfacing_mode: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Runs the complete dialectical decision pipeline across specialized agents."""
        eff_threshold = (
            margin_threshold
            if margin_threshold is not None
            else get_hitl_ambiguity_margin_threshold()
        )
        logger.info(f"Orchestrating decision '{decision_id}': {question} ({len(options)} options)")
        canonical_evidence = canonical_evidence or []
        live_turns = live_turns or []

        l1_plan = self._validate_l1_plan(decision_id, question, options, canonical_evidence, live_turns, category)

        # 1. Law 3: Synchronize untrusted fast-path context if present
        override_count = int((l1_plan.historical_prior_biases or {}).get("override_count", 0))
        if override_count > 0:
            self.fast_path.tighten_conviction_threshold(delta=0.05 * override_count)

        for t in live_turns:
            self.fast_path.ingest_live_turn(
                speaker=t.get("speaker", "Unknown"), text=t.get("text", ""),
                timestamp=t.get("timestamp"), confidence=float(t.get("confidence", 0.85)),
            )
        fused_evidence = self.fast_path.fuse_with_canonical_evidence(canonical_evidence)

        # Apply calibrated role weights from L1 plan
        pw = getattr(l1_plan, "perspective_weights", {}) or {}
        if "RED_TEAM" in pw and hasattr(self.red_team, "set_role_weight"):
            self.red_team.set_role_weight(pw["RED_TEAM"])
        if "STRATEGIC_FEASIBILITY" in pw and hasattr(self.feasibility, "set_role_weight"):
            self.feasibility.set_role_weight(pw["STRATEGIC_FEASIBILITY"])

        # 2. Dialectical Tension: Run Red Team (D1) and Strategic Feasibility (D2)
        risk_assessments = self.red_team.analyze_risks(options=options, evidence=fused_evidence)
        feasibility_assessments = self.feasibility.evaluate_feasibility(options=options)

        # L1 Post-Debate Review (Feedback Verification)
        review_contract = self.post_debate_reviewer.review_debate(
            decision_id=decision_id,
            options=options,
            risk_assessments=risk_assessments,
            feasibility_assessments=feasibility_assessments,
            l1_plan=l1_plan,
        )

        if not validate_replan_clearance(review_contract):
            logger.warning("L1 Post-Debate Review returned INSUFFICIENT_REPLAN for %s. Halting.", decision_id)
            return {
                "decision_id": decision_id, "question": question, "terminal_state": "BLOCKED_REPLAN_REQUIRED",
                "top_option": None, "margin": 0.0, "margin_threshold": eff_threshold,
                "should_surface_hitl": False, "arbitration": {"blocked": True, "reason": "L1 review verdict INSUFFICIENT_REPLAN"},
                "atomic_card": None, "ledger_record": None, "scored_options": [],
                "risk_assessments": risk_assessments, "feasibility_assessments": feasibility_assessments,
                "executive_recap": None, "figma_4k_spec": None, "ephemeral_turns_count": len(live_turns),
                "canonical_evidence_count": len(canonical_evidence), "l1_plan": l1_plan.model_dump(),
                "l1_post_debate_review": review_contract.to_dict(), "replan_directive": getattr(review_contract, "replan_directive", None),
            }

        risk_map = {r["option_key"]: r for r in risk_assessments}
        feasibility_map = {f["option_key"]: f for f in feasibility_assessments}

        # 3. Trade-off Matrix & Composite Scoring
        scored_options = self._score_options(options, risk_map, feasibility_map)

        # 4. HITL Ambiguity Margin Gate (Agent-D4 / UI Bridge)
        arbitration = self._arbitrate_scored_options(
            decision_id, category, question, scored_options, eff_threshold, surfacing_mode
        )

        should_surface = arbitration["should_surface"]
        margin = arbitration["margin"]
        top_opt = arbitration.get("top_option")

        # 5 & 6. Determine Terminal State & Synthesize Briefing
        terminal_state, recap, figma_spec = self._resolve_execution_outcome(
            should_surface, top_opt, question, margin, canonical_evidence, canonical_graph_delta
        )

        return {
            "decision_id": decision_id, "question": question, "terminal_state": terminal_state,
            "top_option": top_opt, "margin": margin, "margin_threshold": eff_threshold,
            "should_surface_hitl": should_surface, "arbitration": arbitration,
            "atomic_card": arbitration.get("atomic_card"), "ledger_record": arbitration.get("ledger_record"),
            "scored_options": scored_options, "risk_assessments": risk_assessments,
            "feasibility_assessments": feasibility_assessments, "executive_recap": recap,
            "figma_4k_spec": figma_spec, "ephemeral_turns_count": len(live_turns),
            "canonical_evidence_count": len(canonical_evidence),
            "l1_plan": l1_plan.model_dump(), "l1_post_debate_review": review_contract.to_dict(),
        }

    async def handle_task(self, task: A2ATask) -> Dict[str, Any]:
        """Handles A2A task invocation."""
        payload = task.input_payload
        result = self.orchestrate_decision(
            decision_id=payload.get("decision_id", "decision-1"),
            question=payload.get("question", "Strategic path selection"),
            options=payload.get("options", []),
            category=payload.get("category", "strategic_architecture"),
            canonical_evidence=payload.get("canonical_evidence"),
            canonical_graph_delta=payload.get("canonical_graph_delta"),
            live_turns=payload.get("live_turns"),
            margin_threshold=float(payload.get("margin_threshold", get_hitl_ambiguity_margin_threshold())),
            surfacing_mode=payload.get("surfacing_mode"),
        )
        return {"task_id": task.task_id, "status": "COMPLETED", "result": result}

    def execute_dialectical_rollout(
        self,
        decision_id: str,
        question: str,
        options: List[Dict[str, Any]],
        evidence: Optional[List[Dict[str, Any]]] = None,
        canonical_graph_delta: Optional[Dict[str, Any]] = None,
        live_turns: Optional[List[Dict[str, Any]]] = None,
        margin_threshold: Optional[float] = None,
        surfacing_mode: Optional[Any] = None,
        roles: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Executes a single-turn 18-role dialectical decision rollout."""
        roles = list(roles or CANONICAL_18_ROLES)
        eff_threshold = margin_threshold if margin_threshold is not None else get_hitl_ambiguity_margin_threshold()
        decision_res = self.orchestrate_decision(
            decision_id=decision_id, question=question, options=options,
            category="dialectical_architecture", canonical_evidence=evidence,
            canonical_graph_delta=canonical_graph_delta, live_turns=live_turns,
            margin_threshold=eff_threshold, surfacing_mode=surfacing_mode,
        )
        return {
            "rollout_id": f"rollout-{decision_id}", "model_id": self.model_id,
            "reasoning_effort": self.reasoning_effort, "simulated_roles_count": len(roles),
            "simulated_roles": roles, "question": question,
            "terminal_state": decision_res["terminal_state"], "top_option": decision_res.get("top_option"),
            "margin": decision_res.get("margin", 0.0), "margin_threshold": margin_threshold,
            "should_surface_hitl": decision_res["should_surface_hitl"],
            "arbitration": decision_res["arbitration"], "scored_options": decision_res["scored_options"],
            "risk_assessments": decision_res["risk_assessments"],
            "feasibility_assessments": decision_res["feasibility_assessments"],
            "executive_recap": decision_res["executive_recap"], "figma_4k_spec": decision_res["figma_4k_spec"],
            "l1_plan": decision_res["l1_plan"],
        }

    def to_antigravity_agent_config(self) -> Any:
        """Bridge to formulate an Antigravity SDK LocalAgentConfig for this orchestrator."""
        from dual_pipeline.agent_sdk.orchestration import NeeAgentHierarchy
        return NeeAgentHierarchy.build_supervisor_config()
