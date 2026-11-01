"""Agent-B5: Contradiction & Multi-Agent Adjudication Judge Agent.

Enforces Law 5 (Multi-Dimensional Soft-Quarantine).
Evaluates extracted candidate facts from Agents B1–B4, resolves duplicates,
detects factual contradictions, and assigns calibrated soft-quarantine tags.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from config.credentials import CredentialManager
from dual_pipeline.a2a import (
    A2ATask,
    AgentCard,
    ComputePosture,
    ModelBinding,
)

if TYPE_CHECKING:
    from dual_pipeline.decision.agents.hitl_arbiter_agent import HitlArbiterAgent
    from dual_pipeline.hitl_ui_bridge import AntigravityUIBridge
    from dual_pipeline.learning_adapters.live_pipeline_integrator import (
        LivePipelineIntegrator,
    )

from dual_pipeline.hitl_governance import DEFAULT_MARGIN_THRESHOLD, HitlSurfacingMode
from dual_pipeline.learning_adapters.agent_outcome_adapter import AgentOutcomeAdapter
from dual_pipeline.model_registry import ModelTier, get_binding

logger = logging.getLogger("dual_pipeline.agents.adjudication_judge")


def get_adjudication_judge_card(
    model_id: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
) -> AgentCard:
    binding = get_binding(ModelTier.DIALECTICAL_JUDGE)
    eff_model = model_id or binding.model_id
    eff_effort = reasoning_effort or binding.reasoning_effort
    return AgentCard(
        agent_id="urn:nee:agent:pipeline-b:adjudication-judge:1.0.0",
        name="AdjudicationJudgeAgent",
        version="1.0.0",
        role="Quality & Governance Adjudicator",
        domain_focus="Law 5 soft-quarantine tagging, contradiction detection, duplicate reconciliation, and 25% HITL gating",
        compute_posture=ComputePosture.CLIENT_SAFE,
        model_binding=ModelBinding(
            model_id=eff_model,
            provider=binding.provider,
            reasoning_effort=eff_effort,
        ),
        skills=["ScoreSoftQuarantine", "DetectContradictions", "ReconcileCandidateNodes", "ArbitrateQuarantineHitl"],
        allowed_tools=["openai_chat", "hitl_store"],
    )


class AdjudicationJudgeAgent:
    """Agent evaluating candidate facts and enforcing Law 5 soft quarantine with 20% HITL margin gating."""

    def __init__(
        self,
        mock_mode: bool = False,
        hitl_arbiter: Optional[HitlArbiterAgent] = None,
        ui_bridge: Optional[AntigravityUIBridge] = None,
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        use_local_model: bool = False,
        integrator: Optional[Any] = None,
    ) -> None:
        self.mock_mode = mock_mode
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
        binding = get_binding(ModelTier.DIALECTICAL_JUDGE)
        self.model_id = model_id or binding.model_id
        self.reasoning_effort = reasoning_effort or binding.reasoning_effort
        self.use_local_model = use_local_model or CredentialManager.is_local_execution_mode()
        from dual_pipeline.parameters import get_local_mlx_model
        target_model = get_local_mlx_model("reasoning_14b") if self.use_local_model else self.model_id
        self.card = get_adjudication_judge_card(model_id=target_model, reasoning_effort=self.reasoning_effort)
        if self.use_local_model:
            self.card = AgentCard(
                agent_id=self.card.agent_id,
                name=self.card.name,
                version=self.card.version,
                role=self.card.role,
                domain_focus=self.card.domain_focus,
                compute_posture=self.card.compute_posture,
                model_binding=ModelBinding(model_id=target_model, provider="mlx"),
                skills=self.card.skills,
                allowed_tools=["mlx_runtime", "hitl_store"],
            )
        if hitl_arbiter is None:
            from dual_pipeline.decision.agents.hitl_arbiter_agent import (  # noqa: E402
                HitlArbiterAgent,  # noqa: E402
            )
            self.hitl_arbiter = HitlArbiterAgent(
                surfacing_mode=HitlSurfacingMode.DIVERGENCE_GT_THRESHOLD,
                margin_threshold=DEFAULT_MARGIN_THRESHOLD,
                model_id=self.model_id,
                reasoning_effort=self.reasoning_effort,
            )
        else:
            self.hitl_arbiter = hitl_arbiter
        if ui_bridge is None:
            from dual_pipeline.hitl_ui_bridge import AntigravityUIBridge  # noqa: E402
            self.ui_bridge = AntigravityUIBridge(
                arbiter=self.hitl_arbiter,
                mock_mode=self.mock_mode,
            )
        else:
            self.ui_bridge = ui_bridge

    async def handle_task(self, task: A2ATask) -> Dict[str, Any]:
        """Adjudicates candidate facts, applying Law 5 soft-quarantine scores."""
        canonical_nodes = task.input_payload.get("canonical_nodes", [])
        advisory_nodes = task.input_payload.get("advisory_nodes", [])

        logger.info(
            f"AdjudicationJudgeAgent adjudicating {len(canonical_nodes)} canonical and "
            f"{len(advisory_nodes)} advisory nodes"
        )

        adjudicated_canonical = []
        for n in canonical_nodes:
            item = dict(n)
            tacticality = float(item.get("tacticality", 0.0))
            strat_val = float(item.get("strategic_value", 5.0))
            confidence = float(item.get("confidence", 0.9))

            # Law 5 Soft-Quarantine rules
            is_quarantined = False
            quarantine_reason = None

            if tacticality > 7.0 and strat_val < 4.0:
                is_quarantined = True
                quarantine_reason = "TACTICAL_LOW_STRATEGIC_VALUE"
            elif confidence < 0.60:
                is_quarantined = True
                quarantine_reason = "LOW_CONFIDENCE_UNGROUNDED"

            item["is_quarantined"] = is_quarantined
            item["quarantine_reason"] = quarantine_reason
            adjudicated_canonical.append(item)

        # Law 5: Detect contradictory claims on the same title/subject
        seen_titles: Dict[str, List[int]] = {}
        for idx, item in enumerate(adjudicated_canonical):
            t_key = item.get("title") or item.get("subject")
            if t_key:
                seen_titles.setdefault(t_key, []).append(idx)

        for t_key, idxs in seen_titles.items():
            if len(idxs) > 1:
                claims = [adjudicated_canonical[i].get("claim") for i in idxs]
                if len(set(claims)) > 1:
                    # Flag subsequent conflicting claims for soft-quarantine review under Law 5
                    for i in idxs[1:]:
                        adjudicated_canonical[i]["is_quarantined"] = True
                        adjudicated_canonical[i]["quarantine_reason"] = "CONTRADICTORY_CLAIM_FLAGGED"
                    if self.integrator:
                        conflict_ids = [adjudicated_canonical[i].get("id") or str(i) for i in idxs]
                        evt = AgentOutcomeAdapter.build_adjudication_error_event(
                            conflicting_entities=conflict_ids,
                            reason=f"Conflicting claims on title/subject '{t_key}'",
                            source_agent="AdjudicationJudgeAgent",
                            candidate_id=t_key,
                            run_id=task.task_id,
                        )
                        self.integrator.record_outcome(evt)

        quarantined_count = len([n for n in adjudicated_canonical if n.get("is_quarantined")])
        return {
            "task_id": task.task_id,
            "status": "COMPLETED",
            "adjudicated_canonical": adjudicated_canonical,
            "advisory_nodes": advisory_nodes,
            "quarantined_count": quarantined_count,
            "total_canonical": len(adjudicated_canonical),
            "total_advisory": len(advisory_nodes),
            "adjudication_summary": f"Adjudicated {len(adjudicated_canonical)} canonical nodes ({quarantined_count} quarantined) and {len(advisory_nodes)} advisory nodes.",
        }

    def adjudicate(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        """Convenience method to score a single candidate fact under Law 5."""
        item = dict(candidate)
        tacticality = float(item.get("tacticality", 0.0))
        strat_val = float(item.get("strategic_value", 5.0))
        confidence = float(item.get("confidence", 0.9))

        is_quarantined = False
        reasons = []

        if tacticality > 7.0 and strat_val < 4.0:
            is_quarantined = True
            reasons.append("TACTICAL_LOW_STRATEGIC_VALUE")
        if confidence < 0.60:
            is_quarantined = True
            reasons.append("LOW_CONFIDENCE_BELOW_THRESHOLD")

        item["is_quarantined"] = is_quarantined
        item["quarantine_reasons"] = reasons
        item["quarantine_reason"] = reasons[0] if reasons else None
        item["retained_node_id"] = item.get("node_id", "unknown")
        return item

    def arbitrate_disposition(
        self,
        candidate: Dict[str, Any],
        margin_threshold: float = DEFAULT_MARGIN_THRESHOLD,
    ) -> Dict[str, Any]:
        """Arbitrates whether a candidate fact should be CONFIRMED or QUARANTINED under 25% margin rule."""
        item = dict(candidate)
        confidence = float(item.get("confidence", 0.70))
        node_id = item.get("node_id", "cand-fact")
        content = item.get("content") or item.get("name") or "Candidate Fact"

        options = [
            {
                "key": "CONFIRM",
                "label": f"Confirm Fact: {content[:40]}",
                "confidence_score": round(confidence, 2),
                "confidence_level": "HIGH" if confidence >= 0.80 else ("MEDIUM" if confidence >= 0.50 else "LOW"),
                "rationale": "Sufficient provenance and domain grounding",
            },
            {
                "key": "QUARANTINE",
                "label": f"Soft-Quarantine Fact: {content[:40]}",
                "confidence_score": round(1.0 - confidence, 2),
                "confidence_level": "HIGH" if (1.0 - confidence) >= 0.80 else ("MEDIUM" if (1.0 - confidence) >= 0.50 else "LOW"),
                "rationale": "High tacticality or low confidence threshold risk",
            },
        ]

        decision_res = self.ui_bridge.arbitrate_and_present(
            pipeline_id="pipeline-b",
            decision_id=f"dec-judge-{node_id}",
            category="fact_adjudication",
            question=f"Adjudicate candidate fact disposition for '{node_id}'",
            options=options,
            margin_threshold=margin_threshold,
        )

        selected = decision_res.get("selected_option") or options[0]
        selected_key = selected.get("key") if isinstance(selected, dict) else str(selected)
        is_quarantined = (selected_key == "QUARANTINE")

        item["is_quarantined"] = is_quarantined
        item["quarantine_reason"] = "HITL_ADJUDICATION_QUARANTINE" if is_quarantined else None
        item["adjudication_decision"] = decision_res
        return item

    def record_adjudication_override(
        self,
        conflicting_entities: List[str],
        reason: str,
        candidate_id: Optional[str] = None,
        corrected_canonical: Optional[str] = None,
        run_id: Optional[str] = None,
        receipt_id: Optional[str] = None,
    ) -> Any:
        """Records an authentic human adjudication override into live outcome stream."""
        if self.integrator:
            evt = AgentOutcomeAdapter.build_adjudication_error_event(
                conflicting_entities=conflicting_entities,
                reason=reason,
                source_agent="AdjudicationJudgeAgent",
                run_id=run_id,
                candidate_id=candidate_id,
                corrected_canonical=corrected_canonical,
                receipt_id=receipt_id,
            )
            return self.integrator.record_outcome(evt)
        return None

