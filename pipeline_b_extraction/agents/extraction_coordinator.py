"""Agent-B0: Extraction Coordinator Agent.

Orchestrates the Law 1 Compute Fork and multi-agent extraction across
Agents B1 through B5 using the A2A protocol and event broker.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from dual_pipeline.a2a import (
    A2AEventBroker,
    A2ATask,
    A2ATaskStatus,
    AgentCard,
    ComputePosture,
    DelegationBudget,
    ModelBinding,
)
from dual_pipeline.model_registry import ModelTier, get_binding
from storage.database import DatabaseManager

from .friction_blocker_agent import FrictionBlockerAgent
from .l1_extraction_cognition import L1ExtractionCognition, L1ExtractionPlanContract
from .stakeholder_dynamics_agent import StakeholderDynamicsAgent
from .strategic_commitments_agent import (
    StrategicCommitmentExtractor,
)
from .tech_arch_agent import TechArchExtractor

logger = logging.getLogger("dual_pipeline.agents.extraction_coordinator")


def get_extraction_coordinator_card(
    model_id: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
) -> AgentCard:
    binding = get_binding(ModelTier.FRONTIER_HIGH_REASONING)
    eff_model = model_id or binding.model_id
    eff_effort = reasoning_effort or binding.reasoning_effort
    return AgentCard(
        agent_id="urn:nee:agent:pipeline-b:coordinator:1.0.0",
        name="ExtractionCoordinator",
        version="1.0.0",
        role="Extraction Orchestrator",
        domain_focus="Law 1 compute fork management, L1 extraction cognition, and multi-agent extraction DAG",
        compute_posture=ComputePosture.CLIENT_SAFE,
        model_binding=ModelBinding(
            model_id=eff_model,
            provider=binding.provider,
            reasoning_effort=eff_effort,
        ),
        skills=["PlanL1ExtractionCognition", "EnforceComputeFork", "FanOutExtraction", "HealCanonicalProjection"],
        allowed_tools=["a2a_event_broker"],
    )


class ExtractionCoordinator:
    """Orchestrator enforcing Law 1 and managing Agents B1–B5."""

    def __init__(
        self,
        broker: Optional[A2AEventBroker] = None,
        db_manager: Optional[DatabaseManager] = None,
        mock_mode: bool = False,
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        judge_agent: Optional[Any] = None,
        healer_agent: Optional[Any] = None,
        priors_path: Optional[Any] = None,
        integrator: Optional[Any] = None,
    ) -> None:
        self.broker = broker or A2AEventBroker()
        self.db_manager = db_manager
        self.mock_mode = mock_mode
        self.priors_path = priors_path
        self.integrator = integrator
        binding = get_binding(ModelTier.FRONTIER_HIGH_REASONING)
        self.model_id = model_id or binding.model_id
        self.reasoning_effort = reasoning_effort or binding.reasoning_effort
        self.card = get_extraction_coordinator_card(
            model_id=self.model_id,
            reasoning_effort=self.reasoning_effort,
        )

        # L1 Cognition planner
        self.l1_cognition = L1ExtractionCognition(
            default_model=self.model_id,
            reasoning_effort=self.reasoning_effort,
            priors_path=self.priors_path,
        )

        # Instantiate specialized extraction agents
        self.tech_arch_agent = TechArchExtractor(mock_mode=mock_mode)
        self.commitments_agent = StrategicCommitmentExtractor(mock_mode=mock_mode)
        self.stakeholder_agent = StakeholderDynamicsAgent(mock_mode=mock_mode)
        self.friction_agent = FrictionBlockerAgent(mock_mode=mock_mode)

        # L5 Judge & Healer: optionally injected to preserve strict L4 -> L5 layer decoupling
        if judge_agent is not None:
            self.judge_agent = judge_agent
        else:
            try:
                from .adjudication_judge_agent import AdjudicationJudgeAgent
                self.judge_agent = AdjudicationJudgeAgent(mock_mode=mock_mode, integrator=self.integrator)
            except (ImportError, Exception):
                self.judge_agent = None

        if healer_agent is not None:
            self.healer_agent = healer_agent
        else:
            try:
                from .canonical_projection_healer import CanonicalProjectionHealerAgent
                self.healer_agent = CanonicalProjectionHealerAgent(mock_mode=mock_mode, integrator=self.integrator)
            except (ImportError, Exception):
                self.healer_agent = None

        # Register agents in A2A broker
        self.broker.register_agent(self.card, self.handle_task)
        self.broker.register_agent(self.tech_arch_agent.card, self.tech_arch_agent.handle_task)
        self.broker.register_agent(self.commitments_agent.card, self.commitments_agent.handle_task)
        self.broker.register_agent(self.stakeholder_agent.card, self.stakeholder_agent.handle_task)
        self.broker.register_agent(self.friction_agent.card, self.friction_agent.handle_task)
        if self.judge_agent is not None and hasattr(self.judge_agent, "card"):
            self.broker.register_agent(self.judge_agent.card, self.judge_agent.handle_task)
        if self.healer_agent is not None and hasattr(self.healer_agent, "card"):
            self.broker.register_agent(self.healer_agent.card, self.healer_agent.handle_task)


    async def handle_task(self, task: A2ATask) -> Dict[str, Any]:
        """Orchestrates parallel multi-agent extraction across the Law 1 compute fork."""
        evidence_id = task.input_payload.get("evidence_id", "ev-001")
        evidence_text = task.input_payload.get("text", "")
        speaker = task.input_payload.get("speaker", "Speaker")

        logger.info(f"ExtractionCoordinator orchestrating extraction for evidence '{evidence_id}'")

        # Step 0: L1 Extraction Cognition & Pre-Fork Planning (before L0 dispatch)
        l1_plan: L1ExtractionPlanContract = self.l1_cognition.plan(
            evidence_id=evidence_id,
            text=evidence_text,
            speaker=speaker,
            metadata=task.input_payload,
        )
        logger.info(f"ExtractionCoordinator L1 Plan: posture={l1_plan.compute_posture}, active={l1_plan.active_agents}")

        if not l1_plan.sanitization_passed:
            raise ValueError(
                f"L0 ROUTING BLOCKED: L1 extraction sanitization failed: {l1_plan.quarantine_reasons}"
            )

        # Step 1: Fan-out to specialized agents in parallel
        # 2 Client-Safe agents (TechArch, Commitments) + 2 Internal-Advisory agents (Stakeholder, Friction)
        t_tech = A2ATask(
            task_id=f"{task.task_id}-tech",
            parent_task_id=task.task_id,
            sender_agent_id=self.card.agent_id,
            recipient_agent_id=self.tech_arch_agent.card.agent_id,
            objective="Extract technical architecture",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"evidence_id": evidence_id, "text": evidence_text, "speaker": speaker},
            budget=DelegationBudget(timeout_seconds=task.budget.timeout_seconds),
        )

        t_comm = A2ATask(
            task_id=f"{task.task_id}-comm",
            parent_task_id=task.task_id,
            sender_agent_id=self.card.agent_id,
            recipient_agent_id=self.commitments_agent.card.agent_id,
            objective="Extract delivery milestones and commitments",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"evidence_id": evidence_id, "text": evidence_text, "speaker": speaker},
            budget=DelegationBudget(timeout_seconds=task.budget.timeout_seconds),
        )

        t_stk = A2ATask(
            task_id=f"{task.task_id}-stk",
            parent_task_id=task.task_id,
            sender_agent_id=self.card.agent_id,
            recipient_agent_id=self.stakeholder_agent.card.agent_id,
            objective="Analyze stakeholder power dynamics locally on Metal",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"evidence_id": evidence_id, "text": evidence_text, "speaker": speaker},
            budget=DelegationBudget(timeout_seconds=task.budget.timeout_seconds),
        )

        t_fric = A2ATask(
            task_id=f"{task.task_id}-fric",
            parent_task_id=task.task_id,
            sender_agent_id=self.card.agent_id,
            recipient_agent_id=self.friction_agent.card.agent_id,
            objective="Detect blockers and adoption friction locally on Metal",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"evidence_id": evidence_id, "text": evidence_text, "speaker": speaker},
            budget=DelegationBudget(timeout_seconds=task.budget.timeout_seconds),
        )

        results = await self.broker.fan_out_and_wait([t_tech, t_comm, t_stk, t_fric])

        canonical_nodes = (results[0].output_payload or {}).get("nodes", []) + (
            results[1].output_payload or {}
        ).get("nodes", [])
        advisory_nodes = (results[2].output_payload or {}).get("nodes", []) + (
            results[3].output_payload or {}
        ).get("nodes", [])

        # Step 2: Adjudication Judge (Law 5 soft quarantine)
        t_judge = A2ATask(
            task_id=f"{task.task_id}-judge",
            parent_task_id=task.task_id,
            sender_agent_id=self.card.agent_id,
            recipient_agent_id=self.judge_agent.card.agent_id,
            objective="Adjudicate candidate facts and score soft quarantine",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"canonical_nodes": canonical_nodes, "advisory_nodes": advisory_nodes},
            budget=DelegationBudget(timeout_seconds=task.budget.timeout_seconds),
        )
        judge_res_task = await self.broker.dispatch_task(t_judge)
        judge_res = judge_res_task.output_payload or {}

        final_canonical = judge_res.get("adjudicated_canonical", canonical_nodes)
        final_advisory = judge_res.get("advisory_nodes", advisory_nodes)

        # Step 3: Extraction Coordinator returns candidate payloads upstream.
        # Direct database mutations are prohibited; all writes must flow through the release lifecycle.
        if self.db_manager:
            logger.warning(
                "Direct database writes from ExtractionCoordinator are deprecated and disabled to enforce the "
                "governed release lifecycle. Projected nodes are returned in the task payload."
            )

        return {
            "task_id": task.task_id,
            "status": "COMPLETED",
            "l1_plan": l1_plan,
            "canonical_nodes": final_canonical,
            "advisory_nodes": final_advisory,
            "canonical_count": len(final_canonical),
            "advisory_count": len(final_advisory),
            "quarantined_count": judge_res.get("quarantined_count", 0),
        }

    def coordinate_extraction(
        self,
        source_id: str = "src-default",
        evidence_id: str = "ev-default",
        content_digest: str = "",
        clean_transcript_text: str = "",
        speaker: str = "Speaker",
        stealth_mode: bool = True,
    ) -> Dict[str, Any]:
        """Convenience method for synchronous extraction across compute fork."""
        from dual_pipeline.a2a import A2ATask, A2ATaskStatus

        task = A2ATask(
            task_id="extract-sync-1",
            parent_task_id=None,
            sender_agent_id="urn:nee:agent:caller",
            recipient_agent_id=self.card.agent_id,
            objective="Coordinate extraction across Law 1 compute fork",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "evidence_id": evidence_id,
                "text": clean_transcript_text,
                "speaker": speaker,
                "source_id": source_id,
                "content_digest": content_digest,
                "stealth_mode": stealth_mode,
            },
        )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            res = asyncio.run(self.handle_task(task))
        else:
            import concurrent.futures
            from config.request_deadline import remaining_timeout
            t_budget = remaining_timeout(180.0)
            with concurrent.futures.ThreadPoolExecutor() as pool:
                res = pool.submit(asyncio.run, self.handle_task(task)).result(timeout=t_budget)

        canonical_nodes = res.get("canonical_nodes", [])
        advisory_nodes = res.get("advisory_nodes", [])

        client_safe_facts = []
        for cn in canonical_nodes:
            item = dict(cn) if isinstance(cn, dict) else cn.to_dict()
            item.setdefault("namespace", "CLIENT_SAFE")
            item.setdefault("compute_posture", "CLIENT_SAFE")
            client_safe_facts.append(item)

        internal_advisory_facts = []
        for an in advisory_nodes:
            item = dict(an) if isinstance(an, dict) else an.to_dict()
            item.setdefault("namespace", "INTERNAL_ADVISORY")
            item.setdefault("compute_posture", "INTERNAL_ADVISORY")
            internal_advisory_facts.append(item)

        res["client_safe_facts"] = client_safe_facts
        res["internal_advisory_facts"] = internal_advisory_facts
        return res
