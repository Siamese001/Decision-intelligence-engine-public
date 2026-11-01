"""
dual_pipeline.autonomous_pipeline_tracker

Autonomous agent coordination and learning proposal mixins for UnifiedAgenticPipeline.
Enforces bounded agent budgets, non-mutating learning change proposals, and multi-agent lifecycles.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dual_pipeline.exceptions import DualPipelineValueError, PipelineTokenBudgetExhaustedError


class AutonomousAgentTrackerMixin:
    """Provides autonomous agent registration, budget tracking, and failure learning capture."""

    def init_autonomous_tracker(self) -> None:
        self.autonomous_agents: Dict[str, Dict[str, Any]] = {}
        self.learning_events: List[Dict[str, Any]] = []

    def register_autonomous_agent(
        self,
        agent_id: str,
        stage: Any,
        reasoning_tier: str = "Standard",
        token_budget: int = 4000,
        timeout_s: float = 60.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Register an autonomous agent operating within bounded stage budgets."""
        st_val = stage.value if hasattr(stage, "value") else str(stage)
        agent_record = {
            "agent_id": agent_id,
            "stage": st_val,
            "reasoning_tier": reasoning_tier,
            "token_budget": token_budget,
            "timeout_s": timeout_s,
            "tokens_consumed": 0,
            "status": "REGISTERED",
            "metadata": metadata or {},
        }
        self.autonomous_agents[agent_id] = agent_record
        if hasattr(self, "metadata"):
            self.metadata.setdefault("autonomous_agents", {})[agent_id] = agent_record

    def record_agent_execution(
        self,
        agent_id: str,
        tokens_consumed: int = 0,
        duration_s: float = 0.0,
        status: str = "SUCCESS",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record autonomous agent execution and enforce token/timeout budget invariants."""
        agent = self.autonomous_agents.get(agent_id)
        if not agent:
            raise DualPipelineValueError(f"Agent '{agent_id}' is not registered.")

        budget = agent.get("token_budget", 4000)
        new_total = agent.get("tokens_consumed", 0) + tokens_consumed
        if new_total > budget:
            raise PipelineTokenBudgetExhaustedError(
                f"Agent '{agent_id}' token budget exhausted: {new_total} > {budget}."
            )

        timeout = agent.get("timeout_s", 60.0)
        if duration_s > timeout:
            raise DualPipelineValueError(
                f"Agent '{agent_id}' execution timed out: {duration_s:.2f}s > {timeout:.2f}s."
            )

        agent["tokens_consumed"] = new_total
        agent["duration_s"] = duration_s
        agent["status"] = status
        agent["details"] = details or {}
        if hasattr(self, "record_token_usage"):
            self.record_token_usage(tokens_consumed)

    def record_learning_proposal_from_failure(
        self,
        stage: Any,
        failure_reason: str,
        error_type: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Synthesize a schema-valid learning-change-proposal:1.0.0 without mutating canonical state."""
        st_val = stage.value if hasattr(stage, "value") else str(stage)
        proposal_id = f"prop-learn-{st_val.lower()}-{uuid.uuid4().hex[:12]}"
        proposal = {
            "proposal_id": proposal_id,
            "contract_id": "urn:nee:dual-pipeline:learning-change-proposal:1.0.0",
            "schema_version": "1.0.0",
            "proposal_type": "pipeline_failure_remediation",
            "status": "proposed",
            "approval_required": True,
            "auto_apply": False,
            "canonical_digest": {
                "algorithm": "sha256",
                "scope": "artifact_without_canonical_digest",
                "value": hashlib.sha256(f"{st_val}:{failure_reason}".encode("utf-8")).hexdigest(),
            },
            "extensions": {},
            "evidence": {
                "stage": st_val,
                "failure_reason": failure_reason,
                "error_type": error_type or "StageExecutionFailure",
                "payload": payload or {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }
        self.learning_events.append(proposal)
        if hasattr(self, "metadata"):
            self.metadata.setdefault("learning_events", []).append(proposal)
        return proposal


class AutonomousLifecycleMixin:
    """Provides full 7-stage autonomous lifecycle execution for UnifiedAgenticPipeline."""

    def run_agentic_lifecycle(
        self,
        input_path: str | Path,
        output_dir: str | Path,
        metadata: Optional[Dict[str, Any]] = None,
        stage_runners: Optional[Dict[Any, Any]] = None,
    ) -> Any:
        """Execute full 7-stage agentic lifecycle with autonomous coordination."""
        from dual_pipeline.unified_agentic_pipeline import PIPELINE_STAGE_SEQUENCE
        tracker = self.create_tracker(input_path, metadata)
        with tracker.execution_scope():
            for stage in PIPELINE_STAGE_SEQUENCE:
                tracker.start_stage(stage)
                runner = (stage_runners or {}).get(stage)
                details = runner(tracker, stage) if callable(runner) else {"stage": stage.value, "status": "COMPLETED"}
                tracker.complete_stage(stage, details=details)
        receipt = tracker.build_receipt(output_dir, terminal_outcome="COMPLETE")
        receipt.write_to_dir(output_dir)
        return receipt
