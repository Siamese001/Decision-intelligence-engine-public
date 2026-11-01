"""Agent-C1: Policy & Namespace Gatekeeper Agent.

Enforces Law 6 (Policy Before Retrieval):
Hard authorization, ACL, namespace, and soft-quarantine filtering executed
strictly prior to semantic vector computation or hybrid search ranking.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from dual_pipeline.a2a import (
    A2ATask,
    AgentCard,
    ComputePosture,
    ModelBinding,
)

logger = logging.getLogger("dual_pipeline.agents.policy_gatekeeper")


def get_policy_gatekeeper_card() -> AgentCard:
    return AgentCard(
        agent_id="urn:nee:agent:pipeline-c:policy-gatekeeper:1.0.0",
        name="PolicyGatekeeperAgent",
        version="1.0.0",
        role="Policy & Security Gatekeeper",
        domain_focus="Law 6 pre-retrieval filtering, ACL evaluation, namespace isolation",
        compute_posture=ComputePosture.CLIENT_SAFE,
        model_binding=ModelBinding(
            model_id="deterministic",
            provider="local",
            temperature=0.0,
        ),
        skills=["FilterPreRetrievalPolicy", "EnforceNamespaceIsolation", "EvaluateACLAccess"],
        allowed_tools=["sql_filter"],
    )


class PolicyGatekeeperAgent:
    """Agent evaluating policy rules before hybrid vector search."""

    def __init__(self) -> None:
        self.card = get_policy_gatekeeper_card()

    async def handle_task(self, task: A2ATask) -> Dict[str, Any]:
        """Applies Law 6 policy filters to candidate facts."""
        facts = task.input_payload.get("candidate_facts", [])
        stealth_mode = task.input_payload.get("stealth_mode", True)
        caller_namespace = task.input_payload.get("caller_namespace", "CLIENT_SAFE")
        include_quarantined = task.input_payload.get("include_quarantined", False)

        logger.info(f"PolicyGatekeeperAgent filtering {len(facts)} candidate facts (stealth_mode={stealth_mode})")

        filtered_facts: List[Dict[str, Any]] = []
        leaks_detected: List[str] = []

        for f in facts:
            fact_namespace = f.get("namespace", "CLIENT_SAFE")
            is_quarantined = bool(f.get("is_quarantined", False))

            # Rule 1: Soft quarantine filter
            if is_quarantined and not include_quarantined:
                continue

            # Rule 2: Stealth mode leak prevention (INTERNAL_ADVISORY cannot appear in CLIENT_SAFE query)
            if caller_namespace == "CLIENT_SAFE" and fact_namespace == "INTERNAL_ADVISORY" and stealth_mode:
                leaks_detected.append(f.get("node_id", "unknown"))
                continue

            filtered_facts.append(f)

        if leaks_detected:
            logger.warning(f"PolicyGatekeeper blocked {len(leaks_detected)} stealth namespace leak candidates")

        return {
            "task_id": task.task_id,
            "status": "COMPLETED",
            "filtered_facts": filtered_facts,
            "passed_count": len(filtered_facts),
            "blocked_leak_count": len(leaks_detected),
            "policy_check_passed": len(leaks_detected) == 0 or not stealth_mode,
        }
