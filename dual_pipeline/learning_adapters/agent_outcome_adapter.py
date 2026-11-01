"""
dual_pipeline/learning_adapters/agent_outcome_adapter.py

Layer 5 Outcome Adapter bridging agent-level operational failures, misses,
corrections, and overrides to governed learning events (urn:nee:dual-pipeline:agent-outcome-event:1.0.0
and urn:nee:dual-pipeline:learning-event:1.0.0).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional

from dual_pipeline.contracts import create_artifact, validate_artifact
from dual_pipeline.core import (
    digest,
    span_id as make_span_id,
    stable_id,
    trace_id as make_trace_id,
)
from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold


class OutcomeType(str, Enum):
    ADJUDICATION_ERROR = "adjudication_error"
    RETRIEVAL_MISS = "retrieval_miss"
    SPEAKER_CORRECTION = "speaker_correction"
    DECISION_OVERRIDE = "decision_override"
    QUALITY_CHECK_FAILURE = "quality_check_failure"
    MAINTENANCE_FINDING = "maintenance_finding"
    COMPONENT_REPAIR = "component_repair"
    RETRY_EXHAUSTION = "retry_exhaustion"


VALID_OUTCOME_TYPES = frozenset(t.value for t in OutcomeType)


@dataclass(frozen=True)
class AgentOutcomeEvent:
    event_id: str
    run_id: str
    trace_id: str
    span_id: str
    outcome_type: OutcomeType
    source_agent: str
    stage: str
    category: str
    observed_outcome: Dict[str, Any]
    confidence: float
    expected_or_corrected_outcome: Optional[Dict[str, Any]] = None
    provenance_receipt_id: Optional[str] = None
    status: str = "FAIL"
    created_at: Optional[str] = None
    extensions: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["outcome_type"] = self.outcome_type.value
        return d


class AgentOutcomeAdapter:
    """Adapts agent execution outcomes into governed learning artifacts and cohort records."""

    @staticmethod
    def create_outcome_event(
        outcome_type: OutcomeType | str,
        source_agent: str,
        stage: str,
        category: str,
        observed_outcome: Mapping[str, Any],
        run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        confidence: float = 1.0,
        expected_or_corrected_outcome: Optional[Mapping[str, Any]] = None,
        provenance_receipt_id: Optional[str] = None,
        status: str = "FAIL",
        created_at: Optional[str] = None,
        extensions: Optional[Mapping[str, Any]] = None,
    ) -> AgentOutcomeEvent:
        """Constructs and validates a strongly typed AgentOutcomeEvent."""
        type_val = outcome_type.value if isinstance(outcome_type, OutcomeType) else str(outcome_type)
        if type_val not in VALID_OUTCOME_TYPES:
            raise ValueError(f"Invalid outcome_type '{type_val}'. Must be one of {sorted(VALID_OUTCOME_TYPES)}")

        now = created_at or datetime.now(timezone.utc).isoformat()
        resolved_run_id = run_id or f"run-outcome-{stable_id('run', source_agent, now, length=12)}"
        resolved_trace = trace_id or make_trace_id(resolved_run_id, source_agent)
        evt_id = stable_id("evt-outcome", source_agent, type_val, now, length=24)
        resolved_span = span_id or make_span_id(resolved_trace, evt_id)

        return AgentOutcomeEvent(
            event_id=evt_id,
            run_id=resolved_run_id,
            trace_id=resolved_trace,
            span_id=resolved_span,
            outcome_type=OutcomeType(type_val),
            source_agent=source_agent,
            stage=stage,
            category=category,
            observed_outcome=dict(observed_outcome),
            confidence=max(0.0, min(1.0, float(confidence))),
            expected_or_corrected_outcome=dict(expected_or_corrected_outcome) if expected_or_corrected_outcome else None,
            provenance_receipt_id=provenance_receipt_id,
            status=status,
            created_at=now,
            extensions=dict(extensions or {}),
        )

    @classmethod
    def build_adjudication_error_event(
        cls,
        conflicting_entities: List[str],
        reason: str,
        source_agent: str = "AdjudicationJudgeAgent",
        run_id: Optional[str] = None,
        candidate_id: Optional[str] = None,
        corrected_canonical: Optional[str] = None,
        receipt_id: Optional[str] = None,
    ) -> AgentOutcomeEvent:
        observed = {
            "candidate_id": candidate_id or "unknown",
            "conflicting_entities": conflicting_entities,
            "failure_reason": reason,
        }
        corrected = {"canonical_id": corrected_canonical} if corrected_canonical else None
        return cls.create_outcome_event(
            outcome_type=OutcomeType.ADJUDICATION_ERROR,
            source_agent=source_agent,
            stage="L2",
            category="entity_resolution",
            observed_outcome=observed,
            expected_or_corrected_outcome=corrected,
            run_id=run_id,
            provenance_receipt_id=receipt_id,
        )

    @classmethod
    def build_retrieval_miss_event(
        cls,
        query_text: str,
        missing_requirements: List[str],
        source_agent: str = "RetrievalCoordinator",
        run_id: Optional[str] = None,
        retrieved_count: int = 0,
        receipt_id: Optional[str] = None,
    ) -> AgentOutcomeEvent:
        observed = {
            "query_text": query_text,
            "missing_requirements": missing_requirements,
            "retrieved_count": retrieved_count,
            "failure_status": "RETRIEVAL_COVERAGE_INSUFFICIENT",
        }
        return cls.create_outcome_event(
            outcome_type=OutcomeType.RETRIEVAL_MISS,
            source_agent=source_agent,
            stage="L1",
            category="coverage_gap",
            observed_outcome=observed,
            run_id=run_id,
            provenance_receipt_id=receipt_id,
        )

    @classmethod
    def build_speaker_correction_event(
        cls,
        assigned_speaker: str,
        confirmed_speaker: str,
        source_agent: str = "RosterReconcilerAgent",
        turn_ids: Optional[List[str]] = None,
        run_id: Optional[str] = None,
        receipt_id: Optional[str] = None,
    ) -> AgentOutcomeEvent:
        observed = {
            "assigned_speaker": assigned_speaker,
            "affected_turn_ids": turn_ids or [],
        }
        corrected = {"confirmed_speaker": confirmed_speaker}
        return cls.create_outcome_event(
            outcome_type=OutcomeType.SPEAKER_CORRECTION,
            source_agent=source_agent,
            stage="L1",
            category="speaker_disambiguation",
            observed_outcome=observed,
            expected_or_corrected_outcome=corrected,
            run_id=run_id,
            provenance_receipt_id=receipt_id,
        )

    @classmethod
    def build_decision_override_event(
        cls,
        decision_id: str,
        category: str,
        recommended_option: str,
        operator_chosen_option: str,
        source_agent: str = "HitlArbiterAgent",
        margin_at_decision: Optional[float] = None,
        run_id: Optional[str] = None,
        receipt_id: Optional[str] = None,
    ) -> AgentOutcomeEvent:
        eff_margin = margin_at_decision if margin_at_decision is not None else get_hitl_ambiguity_margin_threshold()
        observed = {
            "decision_id": decision_id,
            "recommended_option": recommended_option,
            "margin_at_decision": eff_margin,
            "outcome_type": "OVERRIDDEN",
        }
        corrected = {"operator_chosen_option": operator_chosen_option}
        return cls.create_outcome_event(
            outcome_type=OutcomeType.DECISION_OVERRIDE,
            source_agent=source_agent,
            stage="L4",
            category=category,
            observed_outcome=observed,
            expected_or_corrected_outcome=corrected,
            run_id=run_id,
            provenance_receipt_id=receipt_id,
        )

    @classmethod
    def build_quality_failure_event(
        cls,
        gate_name: str,
        defect_dimension: str,
        failure_message: str,
        source_agent: str = "TranscriptQASwarm",
        artifact_id: Optional[str] = None,
        run_id: Optional[str] = None,
        receipt_id: Optional[str] = None,
    ) -> AgentOutcomeEvent:
        observed = {
            "gate_name": gate_name,
            "defect_dimension": defect_dimension,
            "failure_message": failure_message,
            "target_artifact_id": artifact_id or "unknown",
        }
        return cls.create_outcome_event(
            outcome_type=OutcomeType.QUALITY_CHECK_FAILURE,
            source_agent=source_agent,
            stage="E0",
            category=defect_dimension,
            observed_outcome=observed,
            run_id=run_id,
            provenance_receipt_id=receipt_id,
        )

    @classmethod
    def build_maintenance_finding_event(
        cls,
        finding_type: str,
        entity_ids: List[str],
        description: str,
        source_agent: str = "ContinuousMaintenanceSwarm",
        run_id: Optional[str] = None,
        receipt_id: Optional[str] = None,
    ) -> AgentOutcomeEvent:
        observed = {
            "finding_type": finding_type,
            "entity_ids": entity_ids,
            "description": description,
        }
        return cls.create_outcome_event(
            outcome_type=OutcomeType.MAINTENANCE_FINDING,
            source_agent=source_agent,
            stage="L3",
            category=finding_type,
            observed_outcome=observed,
            run_id=run_id,
            provenance_receipt_id=receipt_id,
        )

    @classmethod
    def build_component_repair_event(
        cls,
        target_component: str,
        retry_class: str,
        defect_message: str,
        was_successful: bool,
        attempt: int = 1,
        source_agent: str = "ComponentRetryPlanner",
        run_id: Optional[str] = None,
        receipt_id: Optional[str] = None,
        repair_action: str = "",
    ) -> AgentOutcomeEvent:
        observed = {
            "target_component": target_component,
            "repair_action": repair_action,
            "retry_class": retry_class,
            "defect_message": defect_message,
            "attempt": attempt,
            "was_successful": was_successful,
        }
        return cls.create_outcome_event(
            outcome_type=OutcomeType.COMPONENT_REPAIR,
            source_agent=source_agent,
            stage="E0",
            category="component_repair",
            observed_outcome=observed,
            status="PASS" if was_successful else "FAIL",
            run_id=run_id,
            provenance_receipt_id=receipt_id,
        )

    @classmethod
    def build_retry_exhaustion_event(
        cls,
        target_component: str,
        total_attempts: int,
        reason: str,
        source_agent: str = "ComponentRetryPlanner",
        run_id: Optional[str] = None,
        receipt_id: Optional[str] = None,
    ) -> AgentOutcomeEvent:
        observed = {
            "target_component": target_component,
            "total_attempts": total_attempts,
            "exhaustion_reason": reason,
        }
        return cls.create_outcome_event(
            outcome_type=OutcomeType.RETRY_EXHAUSTION,
            source_agent=source_agent,
            stage="E0",
            category="retry_budget",
            observed_outcome=observed,
            status="FAIL",
            run_id=run_id,
            provenance_receipt_id=receipt_id,
        )

    @staticmethod
    def to_contract_artifact(event: AgentOutcomeEvent) -> Dict[str, Any]:
        """Creates and validates a formal urn:nee:dual-pipeline:agent-outcome-event:1.0.0 artifact."""
        art_id = stable_id("artifact-outcome", event.event_id, length=24)
        return create_artifact(
            "agent-outcome-event",
            art_id,
            event_id=event.event_id,
            trace_id=event.trace_id,
            span_id=event.span_id,
            run_id=event.run_id,
            outcome_type=event.outcome_type.value,
            source_agent=event.source_agent,
            stage=event.stage,
            category=event.category,
            observed_outcome=event.observed_outcome,
            expected_or_corrected_outcome=event.expected_or_corrected_outcome,
            confidence=event.confidence,
            provenance_receipt_id=event.provenance_receipt_id,
            status=event.status,
            created_at=event.created_at,
            extensions=event.extensions,
        )

    @staticmethod
    def to_learning_event(event: AgentOutcomeEvent, policy_version: str = "1.0.0") -> Dict[str, Any]:
        """Converts an outcome event into a validated urn:nee:dual-pipeline:learning-event:1.0.0 envelope."""
        event_type_map = {
            OutcomeType.ADJUDICATION_ERROR: "entity_resolution_decision",
            OutcomeType.RETRIEVAL_MISS: "retrieval_event",
            OutcomeType.SPEAKER_CORRECTION: "hitl_decision",
            OutcomeType.DECISION_OVERRIDE: "hitl_decision",
            OutcomeType.QUALITY_CHECK_FAILURE: "evaluation_result",
            OutcomeType.MAINTENANCE_FINDING: "graph_change_operation",
            OutcomeType.COMPONENT_REPAIR: "evaluation_result",
            OutcomeType.RETRY_EXHAUSTION: "evaluation_result",
        }
        l_type = event_type_map[event.outcome_type]
        payload = event.to_dict()
        p_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        p_digest = digest(payload)
        art_id = stable_id("learning-event-outcome", event.event_id, length=24)

        return create_artifact(
            "learning-event",
            art_id,
            event_id=event.event_id,
            trace_id=event.trace_id,
            span_id=event.span_id,
            parent_span_id=None,
            run_id=event.run_id,
            use_case="shared",
            stage=event.stage,
            event_type=l_type,
            policy_version=policy_version,
            graph_snapshot=None,
            model_or_index_generation=None,
            input_digest=None,
            output_digest=None,
            status=event.status,
            created_at=event.created_at,
            correlation={
                "source_id": event.provenance_receipt_id or f"source-{event.event_id}",
                "candidate_id": event.observed_outcome.get("candidate_id") or (f"cand-{event.event_id}" if l_type in ("entity_resolution_decision", "assertion_disposition", "candidate_record") else None),
                "query_id": event.observed_outcome.get("query_text") or (f"query-{event.event_id}" if l_type == "query_plan" else None),
                "graph_operation_id": event.observed_outcome.get("operation_id") or (f"graph-op-{event.event_id}" if l_type == "graph_change_operation" else None),
                "hitl_item_id": event.observed_outcome.get("decision_id") or (f"hitl-item-{event.event_id}" if l_type == "hitl_decision" else None),
            },
            payload_reference=f"adapter://agent_outcome/{event.outcome_type.value}/{event.event_id}",
            payload_digest=p_digest,
            extensions={
                "com.partner_advisory.nee.agent-outcome": {
                    "source_agent": event.source_agent,
                    "outcome_type": event.outcome_type.value,
                    "category": event.category,
                }
            },
        )

    @staticmethod
    def to_cohort_item(event: AgentOutcomeEvent) -> Dict[str, Any]:
        """Formats the outcome event for ClosedLoopLearningEngine failure cohort analysis."""
        return {
            "source": "agent_outcome",
            "event_id": event.event_id,
            "run_id": event.run_id,
            "outcome_type": event.outcome_type.value,
            "source_agent": event.source_agent,
            "stage": event.stage,
            "category": event.category,
            "status": event.status,
            "confidence": event.confidence,
            "created_at": event.created_at,
            "details": event.observed_outcome,
            "expected_or_corrected": event.expected_or_corrected_outcome,
            "failure_message": event.observed_outcome.get("failure_reason") or event.observed_outcome.get("failure_message") or str(event.observed_outcome),
        }
