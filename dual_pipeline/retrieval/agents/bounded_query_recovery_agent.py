"""Agent-C-Recovery: Bounded Query Recovery Agent.

Observes missing requirement facets in retrieval coverage and executes a single
bounded, targeted secondary search to recover missing evidence without exceeding
latency, iteration, or token budgets.
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from dual_pipeline.a2a import (
    A2ATask,
    AgentCard,
    ComputePosture,
    ModelBinding,
)
from dual_pipeline.core import stable_id

logger = logging.getLogger("dual_pipeline.agents.bounded_query_recovery")

MAX_ALLOWED_RECOVERY_ITERATIONS = 1


@dataclass(frozen=True)
class QueryRecoveryObservation:
    """Observation of retrieval coverage against required facets."""

    query: str
    required_facets: List[str]
    covered_facets: List[str]
    missing_facets: List[str]
    coverage_score: float
    has_gaps: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RecoveryActionReceipt:
    """Immutable receipt documenting a bounded query recovery attempt."""

    receipt_id: str
    action_type: str
    original_query: str
    targeted_query: str
    missing_facets: List[str]
    recovered_facets: List[str]
    initial_coverage_score: float
    final_coverage_score: float
    iteration: int
    max_iterations: int
    terminal_reason: str
    caller_namespace: str
    stealth_mode: bool
    latency_ms: float
    recovered_facts_count: int
    is_success: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def get_bounded_query_recovery_card() -> AgentCard:
    """Emits the A2A AgentCard for BoundedQueryRecoveryAgent."""
    return AgentCard(
        agent_id="urn:nee:agent:pipeline-c:bounded-query-recovery:1.0.0",
        name="BoundedQueryRecoveryAgent",
        version="1.0.0",
        role="Bounded Query Recovery",
        domain_focus="Missing requirement facet observation, bounded query replanning, and targeted secondary retrieval",
        compute_posture=ComputePosture.CLIENT_SAFE,
        model_binding=ModelBinding(
            model_id="deterministic",
            provider="local",
            temperature=0.0,
        ),
        skills=[
            "ObserveMissingFacets",
            "ReplanTargetedQuery",
            "BoundRecoveryAttempt",
            "EnforceAccessBoundaries",
        ],
        allowed_tools=["a2a_event_broker", "targeted_search"],
    )


def fact_matches_facet(fact: Mapping[str, Any], facet: str) -> bool:
    """Determines whether a candidate fact satisfies a specific requirement facet."""
    f_lower = facet.strip().lower()
    if not f_lower:
        return False

    # 1. Exact metadata match on facet or category
    for key in ("facet", "category", "requirement_id", "lens"):
        val = str(fact.get(key, "")).strip().lower()
        if val and (val == f_lower or f_lower in val):
            return True

    # 2. Tag array match
    tags = [str(t).strip().lower() for t in fact.get("tags", []) if isinstance(t, (str, int))]
    if f_lower in tags:
        return True

    # 3. Content / text body match
    text_corpus = " ".join([
        str(fact.get("name", "")),
        str(fact.get("content", "")),
        str(fact.get("summary", "")),
        str(fact.get("description", "")),
    ]).lower()
    return f_lower in text_corpus


class BoundedQueryRecoveryAgent:
    """Agent executing bounded, requirement-scoped secondary query retrieval."""

    def __init__(
        self,
        max_iterations: int = MAX_ALLOWED_RECOVERY_ITERATIONS,
        mock_mode: bool = False,
    ) -> None:
        self.max_iterations = min(max_iterations, MAX_ALLOWED_RECOVERY_ITERATIONS)
        self.mock_mode = mock_mode
        self.card = get_bounded_query_recovery_card()

    def observe_coverage(
        self,
        query: str,
        fused_facts: Sequence[Mapping[str, Any]],
        required_facets: Sequence[str],
    ) -> QueryRecoveryObservation:
        """Observes which required facets are satisfied by current retrieved facts."""
        clean_reqs = [str(r).strip() for r in required_facets if str(r).strip()]
        if not clean_reqs:
            return QueryRecoveryObservation(
                query=query,
                required_facets=[],
                covered_facets=[],
                missing_facets=[],
                coverage_score=1.0,
                has_gaps=False,
            )

        covered: List[str] = []
        missing: List[str] = []

        for req in clean_reqs:
            if any(fact_matches_facet(fact, req) for fact in fused_facts):
                covered.append(req)
            else:
                missing.append(req)

        coverage_score = round(len(covered) / len(clean_reqs), 4)
        return QueryRecoveryObservation(
            query=query,
            required_facets=clean_reqs,
            covered_facets=covered,
            missing_facets=missing,
            coverage_score=coverage_score,
            has_gaps=len(missing) > 0,
        )

    def formulate_targeted_query(
        self,
        query: str,
        missing_facets: Sequence[str],
    ) -> str:
        """Constructs a scoped secondary query targeting only the missing requirement facets."""
        facets_str = " ".join(missing_facets)
        return f"{query} [target_facets: {facets_str}]".strip()

    def _check_early_termination(
        self,
        query: str,
        fused_facts: List[Dict[str, Any]],
        initial_obs: Any,
        remaining_budget: int,
        iteration: int,
        caller_namespace: str,
        stealth_mode: bool,
        t0: float,
    ) -> Optional[Tuple[List[Dict[str, Any]], RecoveryActionReceipt]]:
        reason = None
        is_success = False
        iteration_to_record = iteration

        if not initial_obs.has_gaps:
            reason = "COVERAGE_COMPLETE"
            is_success = True
            iteration_to_record = 0
        elif remaining_budget <= 0:
            reason = "BUDGET_EXHAUSTED"
        elif iteration > self.max_iterations:
            reason = "MAX_ITERATIONS_REACHED"

        if reason is None:
            return None

        receipt = self._build_receipt(
            query=query,
            targeted_query=query,
            missing_facets=[] if reason == "COVERAGE_COMPLETE" else initial_obs.missing_facets,
            recovered_facets=[],
            initial_score=initial_obs.coverage_score,
            final_score=initial_obs.coverage_score,
            iteration=iteration_to_record,
            terminal_reason=reason,
            caller_namespace=caller_namespace,
            stealth_mode=stealth_mode,
            latency_ms=(time.time() - t0) * 1000.0,
            recovered_count=0,
            is_success=is_success,
        )
        return list(fused_facts), receipt

    def _filter_recovery_candidates(
        self,
        candidate_facts: Sequence[Mapping[str, Any]],
        missing_facets: Sequence[str],
        caller_namespace: str,
        stealth_mode: bool,
        existing_keys: set,
    ) -> List[Dict[str, Any]]:
        recovered: List[Dict[str, Any]] = []
        for cand in candidate_facts:
            cand_dict = dict(cand)
            cand_ns = cand_dict.get("namespace", "CLIENT_SAFE")
            if caller_namespace == "CLIENT_SAFE" and cand_ns != "CLIENT_SAFE":
                continue
            if stealth_mode and cand_dict.get("is_quarantined", False):
                continue

            matched_facets = [mf for mf in missing_facets if fact_matches_facet(cand_dict, mf)]
            if matched_facets:
                ckey = str(cand_dict.get("node_id") or cand_dict.get("id") or cand_dict.get("name") or hash(str(cand_dict)))
                if ckey not in existing_keys:
                    existing_keys.add(ckey)
                    cand_dict["retrieval_lane"] = "bounded_recovery_secondary"
                    cand_dict["recovered_for_facets"] = matched_facets
                    recovered.append(cand_dict)
        return recovered

    def recover(
        self,
        query: str,
        fused_facts: List[Dict[str, Any]],
        candidate_facts: Sequence[Mapping[str, Any]],
        required_facets: Sequence[str],
        caller_namespace: str = "CLIENT_SAFE",
        stealth_mode: bool = True,
        iteration: int = 1,
        remaining_budget: int = 1,
    ) -> Tuple[List[Dict[str, Any]], RecoveryActionReceipt]:
        """Executes a bounded recovery attempt to satisfy missing requirements."""
        t0 = time.time()
        initial_obs = self.observe_coverage(query, fused_facts, required_facets)

        early_exit = self._check_early_termination(
            query, fused_facts, initial_obs, remaining_budget, iteration, caller_namespace, stealth_mode, t0
        )
        if early_exit is not None:
            return early_exit

        # Execute targeted retrieval
        targeted_q = self.formulate_targeted_query(query, initial_obs.missing_facets)
        existing_keys = {
            str(f.get("node_id") or f.get("id") or f.get("name") or hash(str(f)))
            for f in fused_facts
        }

        recovered_facts = self._filter_recovery_candidates(
            candidate_facts, initial_obs.missing_facets, caller_namespace, stealth_mode, existing_keys
        )

        updated_fused_facts = list(fused_facts) + recovered_facts
        final_obs = self.observe_coverage(query, updated_fused_facts, required_facets)

        recovered_facets = [
            f for f in initial_obs.missing_facets if f in final_obs.covered_facets
        ]
        is_success = len(recovered_facets) > 0
        terminal_reason = "RECOVERY_SUCCESS" if is_success else "NO_PROGRESS_STOP"

        receipt = self._build_receipt(
            query=query,
            targeted_query=targeted_q,
            missing_facets=initial_obs.missing_facets,
            recovered_facets=recovered_facets,
            initial_score=initial_obs.coverage_score,
            final_score=final_obs.coverage_score,
            iteration=iteration,
            terminal_reason=terminal_reason,
            caller_namespace=caller_namespace,
            stealth_mode=stealth_mode,
            latency_ms=(time.time() - t0) * 1000.0,
            recovered_count=len(recovered_facts),
            is_success=is_success,
        )
        return updated_fused_facts, receipt


    def _build_receipt(
        self,
        query: str,
        targeted_query: str,
        missing_facets: List[str],
        recovered_facets: List[str],
        initial_score: float,
        final_score: float,
        iteration: int,
        terminal_reason: str,
        caller_namespace: str,
        stealth_mode: bool,
        latency_ms: float,
        recovered_count: int,
        is_success: bool,
    ) -> RecoveryActionReceipt:
        action_id = stable_id("receipt-query-recovery", query, str(iteration), length=24)
        return RecoveryActionReceipt(
            receipt_id=action_id,
            action_type="TARGETED_SECONDARY_SEARCH",
            original_query=query,
            targeted_query=targeted_query,
            missing_facets=list(missing_facets),
            recovered_facets=list(recovered_facets),
            initial_coverage_score=round(initial_score, 4),
            final_coverage_score=round(final_score, 4),
            iteration=iteration,
            max_iterations=self.max_iterations,
            terminal_reason=terminal_reason,
            caller_namespace=caller_namespace,
            stealth_mode=stealth_mode,
            latency_ms=round(latency_ms, 2),
            recovered_facts_count=recovered_count,
            is_success=is_success,
        )

    async def handle_task(self, task: A2ATask) -> Dict[str, Any]:
        """A2A task handler for BoundedQueryRecoveryAgent."""
        payload = task.input_payload
        fused_facts, receipt = self.recover(
            query=payload.get("query", ""),
            fused_facts=payload.get("fused_facts", []),
            candidate_facts=payload.get("candidate_facts", []),
            required_facets=payload.get("required_facets", []),
            caller_namespace=payload.get("caller_namespace", "CLIENT_SAFE"),
            stealth_mode=payload.get("stealth_mode", True),
            iteration=int(payload.get("iteration", 1)),
            remaining_budget=int(payload.get("remaining_budget", 1)),
        )
        return {
            "task_id": task.task_id,
            "status": "COMPLETED",
            "fused_facts": fused_facts,
            "recovery_receipt": receipt.to_dict(),
        }
