"""
Component-Level Retry Planner & Dependency Invalidation Engine.

Enforces:
1. Exact component-level retry scoping with protected invariant boundaries.
2. Separate retry classes (Transport, Evaluator, Content, Dependency) with distinct budgets.
3. Dependency-aware invalidation (e.g. speaker correction invalidates downstream criteria).
4. No-op patch and recurring defect termination to prevent endless loops.
"""
from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


class RetryClass(str, enum.Enum):
    TRANSPORT = "TRANSPORT"
    EVALUATOR_REPAIR = "EVALUATOR_REPAIR"
    CONTENT_REPAIR = "CONTENT_REPAIR"
    DEPENDENCY_REVALIDATION = "DEPENDENCY_REVALIDATION"


@dataclass
class ComponentPlan:
    retry_class: RetryClass
    target_component: str
    writable_paths: List[str]
    protected_paths: List[str]
    affected_criteria: List[str]
    dependency_reason: Optional[str] = None
    baseline_hashes: Dict[str, str] = field(default_factory=dict)
    defect_signature: Optional[str] = None
    defect_message: Optional[str] = None
    budget_snapshot: Dict[str, Any] = field(default_factory=dict)
    attempt_number: int = 1
    approval_binding_digest: Optional[str] = None
    is_valid: bool = True
    invalidation_reason: Optional[str] = None
    plan_id: Optional[str] = None


@dataclass
class RetryBudget:
    max_total_attempts: int = 5
    max_content_repairs: int = 2
    max_evaluator_repairs: int = 1
    max_transport_retries: int = 3
    total_attempts: int = 0
    content_repairs: int = 0
    evaluator_repairs: int = 0
    transport_retries: int = 0

    def can_attempt(self, r_class: RetryClass) -> Tuple[bool, str]:
        if self.total_attempts >= self.max_total_attempts:
            return False, f"Global retry budget exhausted ({self.total_attempts}/{self.max_total_attempts})"
        if r_class == RetryClass.CONTENT_REPAIR and self.content_repairs >= self.max_content_repairs:
            return False, f"Content repair budget exhausted ({self.content_repairs}/{self.max_content_repairs})"
        if r_class == RetryClass.EVALUATOR_REPAIR and self.evaluator_repairs >= self.max_evaluator_repairs:
            return False, f"Evaluator repair budget exhausted ({self.evaluator_repairs}/{self.max_evaluator_repairs})"
        if r_class == RetryClass.TRANSPORT and self.transport_retries >= self.max_transport_retries:
            return False, f"Transport retry budget exhausted ({self.transport_retries}/{self.max_transport_retries})"
        return True, "Budget available"

    def record_attempt(self, r_class: RetryClass) -> None:
        self.total_attempts += 1
        if r_class == RetryClass.CONTENT_REPAIR:
            self.content_repairs += 1
        elif r_class == RetryClass.EVALUATOR_REPAIR:
            self.evaluator_repairs += 1
        elif r_class == RetryClass.TRANSPORT:
            self.transport_retries += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_total_attempts": self.max_total_attempts,
            "max_content_repairs": self.max_content_repairs,
            "max_evaluator_repairs": self.max_evaluator_repairs,
            "max_transport_retries": self.max_transport_retries,
            "total_attempts": self.total_attempts,
            "content_repairs": self.content_repairs,
            "evaluator_repairs": self.evaluator_repairs,
            "transport_retries": self.transport_retries,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RetryBudget:
        return cls(
            max_total_attempts=int(data.get("max_total_attempts", 5)),
            max_content_repairs=int(data.get("max_content_repairs", 2)),
            max_evaluator_repairs=int(data.get("max_evaluator_repairs", 1)),
            max_transport_retries=int(data.get("max_transport_retries", 3)),
            total_attempts=int(data.get("total_attempts", 0)),
            content_repairs=int(data.get("content_repairs", 0)),
            evaluator_repairs=int(data.get("evaluator_repairs", 0)),
            transport_retries=int(data.get("transport_retries", 0)),
        )

    def restore_attempts(self, prior_attempts: int) -> None:
        """Ensure restarts do not reset cumulative attempt counters."""
        if prior_attempts > self.total_attempts:
            self.total_attempts = prior_attempts


class ComponentRetryPlanner:
    """Plans scoped retries and tracks component dependency invalidation."""

    def __init__(
        self,
        budget: Optional[RetryBudget] = None,
        integrator: Optional[Any] = None,
    ) -> None:
        self.budget = budget or RetryBudget()
        self._prior_defect_signatures: Set[str] = set()
        self._prior_candidate_hashes: Dict[str, str] = {}
        self._repair_history: List[Dict[str, Any]] = []
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

    @staticmethod
    def compute_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def restore_budget(self, budget_data: Dict[str, Any]) -> None:
        """Restores budget state across restarts preventing attempt reset to zero."""
        self.budget = RetryBudget.from_dict(budget_data)

    def plan_appendix_retry(
        self,
        f2_text: str,
        critique_envelope: Optional[Dict[str, Any]] = None,
        attempt: int = 1,
        defect_message: str = "",
        approval_binding_digest: Optional[str] = None,
    ) -> ComponentPlan:
        """Plans scoped appendix repair, protecting all other sections and deliverables."""
        from dual_pipeline.transcript_pipeline_core.surgical_remediator import compute_section_hashes
        hashes = compute_section_hashes(f2_text)

        protected = [
            "output_1",
            "output_2:header_and_overview",
            "output_2:key_updates_technical_substance",
            "output_2:conditional_extensions",
            "output_2:action_ledger",
            "output_3",
        ]
        defect_sig = f"output_2:appendix:{defect_message.strip().lower()}" if defect_message else "output_2:appendix"
        plan_id = f"plan-appendix-a{attempt}-{hashlib.sha256((f2_text[:64] + defect_sig).encode('utf-8')).hexdigest()[:12]}"

        can_retry, reason = self.budget.can_attempt(RetryClass.CONTENT_REPAIR)
        if not can_retry or attempt > self.budget.max_content_repairs:
            exhaustion_msg = reason if not can_retry else f"Content repairs exhausted ({attempt}/{self.budget.max_content_repairs})"
            self.record_retry_exhaustion("output_2:appendix", self.budget.total_attempts, exhaustion_msg)
            return ComponentPlan(
                retry_class=RetryClass.CONTENT_REPAIR,
                target_component="output_2:appendix",
                writable_paths=[],
                protected_paths=protected,
                affected_criteria=["appendix_stakeholder_perspectives", "dimension_3_strategic_synthesis"],
                is_valid=False,
                invalidation_reason=f"RETRY_EXHAUSTION: {exhaustion_msg}",
                defect_message=defect_message,
                attempt_number=attempt,
                budget_snapshot=self.budget.to_dict(),
                plan_id=plan_id,
            )

        self.budget.record_attempt(RetryClass.CONTENT_REPAIR)
        return ComponentPlan(
            retry_class=RetryClass.CONTENT_REPAIR,
            target_component="output_2:appendix",
            writable_paths=["# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown"],
            protected_paths=protected,
            affected_criteria=["appendix_stakeholder_perspectives", "dimension_3_strategic_synthesis"],
            baseline_hashes=hashes,
            defect_signature=defect_sig,
            defect_message=defect_message,
            budget_snapshot=self.budget.to_dict(),
            attempt_number=attempt,
            approval_binding_digest=approval_binding_digest,
            plan_id=plan_id,
        )

    def plan_dependency_invalidation(
        self,
        event_type: str,
        changed_artifact: str,
        detail: str = "",
        attempt: int = 1,
    ) -> ComponentPlan:
        """Determines downstream criteria invalidated by upstream evidence/speaker mutations."""
        if changed_artifact == "output_1" and ("speaker" in event_type.lower() or "speaker" in detail.lower()):
            plan_id = f"plan-dep-speaker-a{attempt}"
            return ComponentPlan(
                retry_class=RetryClass.DEPENDENCY_REVALIDATION,
                target_component="output_1:speakers",
                writable_paths=[],
                protected_paths=[],
                affected_criteria=[
                    "output_2:dimension_2_technical_substance",
                    "output_2:dimension_3_strategic_synthesis",
                    "output_2:appendix_stakeholder_perspectives",
                    "output_3:entities",
                    "output_3:decisions",
                ],
                dependency_reason="Transcript speaker correction invalidates dependent recap and graph attributions",
                budget_snapshot=self.budget.to_dict(),
                attempt_number=attempt,
                plan_id=plan_id,
            )
        plan_id = f"plan-dep-{changed_artifact}-a{attempt}"
        return ComponentPlan(
            retry_class=RetryClass.DEPENDENCY_REVALIDATION,
            target_component=changed_artifact,
            writable_paths=[],
            protected_paths=[],
            affected_criteria=[f"{changed_artifact}:all"],
            dependency_reason=f"Upstream change on {changed_artifact}: {detail}",
            budget_snapshot=self.budget.to_dict(),
            attempt_number=attempt,
            plan_id=plan_id,
        )

    def check_noop_or_recurring_defect(
        self,
        target_component: str,
        candidate_text: str,
        defect_message: str,
    ) -> Tuple[bool, str]:
        """Detects no-op patches or recurring unchanged defects."""
        cand_hash = self.compute_hash(candidate_text)
        prior_cand_hash = self._prior_candidate_hashes.get(target_component)
        if prior_cand_hash and prior_cand_hash == cand_hash:
            return True, f"NO_OP_PATCH: Candidate text for '{target_component}' did not change after repair"

        defect_sig = f"{target_component}:{defect_message.strip().lower()}"
        if defect_sig in self._prior_defect_signatures:
            return True, f"RECURRING_DEFECT: Defect '{defect_message[:50]}' recurred without resolution"

        self._prior_candidate_hashes[target_component] = cand_hash
        self._prior_defect_signatures.add(defect_sig)
        return False, "OK"

    def verify_protected_component_invariance(
        self,
        plan: ComponentPlan,
        current_hashes: Dict[str, str],
    ) -> Tuple[bool, List[str]]:
        """Verifies that all protected section hashes match baseline exactly."""
        violations: List[str] = []
        for sec, orig_h in plan.baseline_hashes.items():
            if "appendix" in sec and plan.target_component == "output_2:appendix":
                continue  # target is expected to change
            curr_h = current_hashes.get(sec)
            if curr_h != orig_h:
                violations.append(f"Protected section '{sec}' was mutated: baseline {orig_h[:8]} != current {curr_h[:8] if curr_h else 'MISSING'}")
        return len(violations) == 0, violations

    def validate_approval_freshness(
        self,
        plan: ComponentPlan,
        current_deliverable_digest: str,
    ) -> Tuple[bool, str]:
        """Ensures that a previously granted approval cannot be reused on modified deliverables."""
        if not plan.approval_binding_digest:
            return True, "NO_PRIOR_APPROVAL_BOUND"
        if plan.approval_binding_digest == "STALE" or plan.approval_binding_digest != current_deliverable_digest:
            return False, "STALE_APPROVAL_DETECTED: Stale approval cannot be reused across modified deliverables or restarts"
        return True, "APPROVAL_FRESH"

    def record_component_repair(
        self,
        component: str,
        repair_action: str,
        retry_class: RetryClass | str,
        was_successful: bool,
        defect_message: str = "",
        attempt: int = 1,
        run_id: Optional[str] = None,
        receipt_id: Optional[str] = None,
    ) -> Any:
        """Records a component-level repair outcome into the learning history and live telemetry stream."""
        r_class_str = retry_class.value if isinstance(retry_class, RetryClass) else str(retry_class)
        entry = {
            "target_component": component,
            "repair_action": repair_action,
            "retry_class": r_class_str,
            "was_successful": was_successful,
            "defect_message": defect_message,
            "attempt": attempt,
        }
        self._repair_history.append(entry)

        if self.integrator:
            from dual_pipeline.learning_adapters.agent_outcome_adapter import (
                AgentOutcomeAdapter,
            )
            evt = AgentOutcomeAdapter.build_component_repair_event(
                target_component=component,
                repair_action=repair_action,
                retry_class=r_class_str,
                was_successful=was_successful,
                defect_message=defect_message,
                attempt=attempt,
                source_agent="ComponentRetryPlanner",
                run_id=run_id,
                receipt_id=receipt_id,
            )
            return self.integrator.record_outcome(evt)
        return None

    def record_retry_exhaustion(
        self,
        target_component: str,
        total_attempts: int,
        reason: str,
        run_id: Optional[str] = None,
        receipt_id: Optional[str] = None,
    ) -> Any:
        """Records a retry budget exhaustion outcome into live telemetry stream."""
        if self.integrator:
            from dual_pipeline.learning_adapters.agent_outcome_adapter import (
                AgentOutcomeAdapter,
            )
            evt = AgentOutcomeAdapter.build_retry_exhaustion_event(
                target_component=target_component,
                total_attempts=total_attempts,
                reason=reason,
                source_agent="ComponentRetryPlanner",
                run_id=run_id,
                receipt_id=receipt_id,
            )
            return self.integrator.record_outcome(evt)
        return None

    def rank_repair_strategies(
        self,
        defect_dimension: str,
        available_strategies: Sequence[str],
        historical_repairs: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> List[str]:
        """Ranks candidate repair strategies based on empirical historical success rates.

        Uses Laplace smoothing: score = (successes + 1.0) / (total + 2.0).
        Preserves original sequence order on ties.
        """
        if not available_strategies:
            return []

        history = list(historical_repairs) if historical_repairs is not None else list(self._repair_history)
        if self.integrator and historical_repairs is None:
            try:
                from dual_pipeline.learning_adapters.agent_outcome_adapter import OutcomeType
                events = self.integrator.get_recorded_events(outcome_type=OutcomeType.COMPONENT_REPAIR)
                for e in events:
                    obs = e.observed_outcome if isinstance(e.observed_outcome, dict) else {}
                    history.append({
                        "repair_action": obs.get("repair_action", ""),
                        "target_component": obs.get("target_component", ""),
                        "was_successful": obs.get("was_successful", e.status == "PASS"),
                    })
            except Exception:
                pass

        scores: Dict[str, float] = {}
        for idx, strat in enumerate(available_strategies):
            strat_key = strat.strip().lower()
            relevant = [
                h for h in history
                if str(h.get("repair_action", "")).strip().lower() == strat_key
                or strat_key in str(h.get("repair_action", "")).strip().lower()
            ]
            if relevant:
                successes = sum(1 for h in relevant if h.get("was_successful") is True)
                total = len(relevant)
                yield_rate = (successes + 1.0) / (total + 2.0)
            else:
                yield_rate = 0.5

            # Tie-breaker preserves stable original sequence order
            scores[strat] = yield_rate - (idx * 1e-6)

        return sorted(available_strategies, key=lambda s: scores.get(s, 0.5), reverse=True)
