"""Data models, enums, and decision structures for L2 policy."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class L2Policy(StrEnum):
    FORBIDDEN = "forbidden"
    CONDITIONAL = "conditional"
    REQUIRED = "required"


class QueryAssuranceProfile(StrEnum):
    STANDARD = "standard"
    ENHANCED = "enhanced"


class L2ReasonCode(StrEnum):
    EXACT_LOOKUP_DETERMINISTIC = "EXACT_LOOKUP_DETERMINISTIC"
    SIMPLE_CURRENT_STATE_DETERMINISTIC = "SIMPLE_CURRENT_STATE_DETERMINISTIC"
    RELATIONSHIP_EXPLANATION_REQUIRED = "RELATIONSHIP_EXPLANATION_REQUIRED"
    CROSS_WORKSTREAM_SYNTHESIS_REQUIRED = "CROSS_WORKSTREAM_SYNTHESIS_REQUIRED"
    GAP_OR_CONTRADICTION_REASONING_REQUIRED = "GAP_OR_CONTRADICTION_REASONING_REQUIRED"
    OPPORTUNITY_RECOMMENDATION_REQUIRED = "OPPORTUNITY_RECOMMENDATION_REQUIRED"
    CANDIDATE_REVIEW_REASONING_REQUIRED = "CANDIDATE_REVIEW_REASONING_REQUIRED"
    MULTIPLE_MATERIAL_EVIDENCE_ITEMS = "MULTIPLE_MATERIAL_EVIDENCE_ITEMS"
    MATERIAL_CONFLICT_PRESENT = "MATERIAL_CONFLICT_PRESENT"
    USER_REQUESTED_EXPLANATION = "USER_REQUESTED_EXPLANATION"
    USER_REQUESTED_COMPARISON = "USER_REQUESTED_COMPARISON"
    USER_REQUESTED_RECOMMENDATION = "USER_REQUESTED_RECOMMENDATION"
    USER_REQUESTED_PRIORITIZATION = "USER_REQUESTED_PRIORITIZATION"
    USER_REQUESTED_DEFENSE_OR_RATIONALE = "USER_REQUESTED_DEFENSE_OR_RATIONALE"
    NO_EVIDENCE_L2_NOT_USEFUL = "NO_EVIDENCE_L2_NOT_USEFUL"
    PROVIDER_EGRESS_NOT_AUTHORIZED = "PROVIDER_EGRESS_NOT_AUTHORIZED"
    OFFLINE_EXTERNAL_PROVIDER_FORBIDDEN = "OFFLINE_EXTERNAL_PROVIDER_FORBIDDEN"


class QualityFailureCode(StrEnum):
    MATERIAL_QUERY_COMPONENT_NOT_ANSWERED = "MATERIAL_QUERY_COMPONENT_NOT_ANSWERED"
    MATERIAL_COUNTEREVIDENCE_NOT_ADDRESSED = "MATERIAL_COUNTEREVIDENCE_NOT_ADDRESSED"
    RECOMMENDATION_RATIONALE_INCOMPLETE = "RECOMMENDATION_RATIONALE_INCOMPLETE"
    MATERIAL_CONFLICT_NOT_RECONCILED = "MATERIAL_CONFLICT_NOT_RECONCILED"
    REQUIRED_ASSUMPTIONS_NOT_DISCLOSED = "REQUIRED_ASSUMPTIONS_NOT_DISCLOSED"
    REQUIRED_WHAT_WOULD_CHANGE_MISSING = "REQUIRED_WHAT_WOULD_CHANGE_MISSING"
    MATERIAL_CANDIDATE_POSTURE_NOT_EXPLICIT = "MATERIAL_CANDIDATE_POSTURE_NOT_EXPLICIT"
    MATERIAL_GRAPH_PATH_NOT_EXPLAINED = "MATERIAL_GRAPH_PATH_NOT_EXPLAINED"
    CITATION_DENSITY_INSUFFICIENT = "CITATION_DENSITY_INSUFFICIENT"
    UNRESOLVED_CONTRADICTION_POSTURE = "UNRESOLVED_CONTRADICTION_POSTURE"
    ACTION_FEASIBILITY_INCOMPLETE = "ACTION_FEASIBILITY_INCOMPLETE"


class CallPurpose(StrEnum):
    PRIMARY = "PRIMARY"
    SCHEMA_REPAIR = "SCHEMA_REPAIR"
    QUALITY_ESCALATION = "QUALITY_ESCALATION"
    CONTENT_REPAIR = "CONTENT_REPAIR"
    FORENSIC_TRANSCRIPT = "FORENSIC_TRANSCRIPT"
    EXECUTIVE_RECAP = "EXECUTIVE_RECAP"
    KG_SIGNAL_PROJECTION = "KG_SIGNAL_PROJECTION"
    AGENTIC_LEVERAGE = "AGENTIC_LEVERAGE"


class L2Disposition(StrEnum):
    SKIPPED = "SKIPPED"
    PRIMARY = "PRIMARY"
    REPAIRED = "REPAIRED"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"


class SchemaFailureCode(StrEnum):
    MALFORMED_STRUCTURED_OUTPUT = "MALFORMED_STRUCTURED_OUTPUT"
    MISSING_REQUIRED_OUTPUT_FIELD = "MISSING_REQUIRED_OUTPUT_FIELD"
    INVALID_ENUM_VALUE = "INVALID_ENUM_VALUE"
    INVALID_CITATION_WIRING = "INVALID_CITATION_WIRING"
    SERIALIZATION_DEFECT = "SERIALIZATION_DEFECT"
    GOVERNANCE_VIOLATION = "GOVERNANCE_VIOLATION"


class ProviderFailureCode(StrEnum):
    MISSING_API_KEY = "MISSING_API_KEY"
    PROVIDER_EGRESS_NOT_AUTHORIZED = "PROVIDER_EGRESS_NOT_AUTHORIZED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    UNSUPPORTED_MODEL = "UNSUPPORTED_MODEL"
    INVALID_STRUCTURED_RESPONSE = "INVALID_STRUCTURED_RESPONSE"
    CALL_CAP_EXHAUSTED = "CALL_CAP_EXHAUSTED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    SEMANTIC_QUALITY_GATE_FAILED = "SEMANTIC_QUALITY_GATE_FAILED"


@dataclass(frozen=True)
class PricingPolicy:
    input_per_million: float
    cached_input_per_million: float
    output_per_million: float


@dataclass(frozen=True)
class ModelPolicy:
    provider: str
    model_family: str
    model: str
    assurance_profile: QueryAssuranceProfile
    reasoning_effort: str
    maximum_primary_calls_per_run: int
    maximum_repair_calls_per_run: int
    maximum_escalation_calls_per_run: int
    pricing: PricingPolicy
    context_window_tokens: int = 128000


@dataclass(frozen=True)
class OutputPolicy:
    structured_output_required: bool
    maximum_output_tokens: int


@dataclass(frozen=True)
class ReliabilityPolicy:
    retry_count: int
    timeout_seconds: int


@dataclass(frozen=True)
class RequestBudgetPolicy:
    maximum_provider_request_chars: int


@dataclass(frozen=True)
class L2Config:
    schema_version: str
    mode: str
    provider_class: str
    provider_egress_allowed: bool
    endpoint: str
    api_key_env: str
    local_env_file: str
    standard: ModelPolicy
    enhanced: ModelPolicy
    output: OutputPolicy
    reliability: ReliabilityPolicy
    request_budget: RequestBudgetPolicy
    config_digest: str
    purpose_models: Mapping[str, ModelPolicy] = field(default_factory=dict)
    prompt_cache_enabled: bool = True

    def model_for_assurance(
        self,
        profile: QueryAssuranceProfile | str,
    ) -> ModelPolicy:
        selected = QueryAssuranceProfile(profile)
        return self.standard if selected is QueryAssuranceProfile.STANDARD else self.enhanced

    def model_for_purpose(
        self,
        purpose: CallPurpose | str,
        profile: QueryAssuranceProfile | str = "standard",
    ) -> ModelPolicy:
        p_str = purpose.value if isinstance(purpose, CallPurpose) else str(purpose)
        if p_str in self.purpose_models:
            return self.purpose_models[p_str]
        return self.model_for_assurance(profile)



@dataclass(frozen=True)
class QueryTypePolicy:
    l2_policy: L2Policy
    l2_policy_reason: str
    allowed_provider_class: str | None


@dataclass(frozen=True)
class RequestIntent:
    explanation: bool
    comparison: bool
    recommendation: bool
    prioritization: bool
    defense_or_rationale: bool
    risk_or_opportunity: bool
    candidate_synthesis: bool
    high_materiality: bool
    structured_deliverable: bool
    bounded_factual_state: bool
    bounded_lookup: bool
    component_count: int

    @property
    def semantic_synthesis_requested(self) -> bool:
        return any(
            (
                self.explanation,
                self.comparison,
                self.recommendation,
                self.prioritization,
                self.defense_or_rationale,
                self.risk_or_opportunity,
                self.candidate_synthesis,
            )
        )


@dataclass(frozen=True)
class L2ExecutionDecision:
    decision_id: str
    run_id: str
    trace_id: str
    validated_request_digest: str
    query_plan_digest: str
    route_decision_digest: str
    evidence_packet_hash: str
    policy_config_digest: str
    required: bool
    policy: str
    reason_code: str
    query_type: str
    route: str
    materiality: str
    assurance_profile: str | None
    assurance_trigger_reasons: tuple[str, ...]
    selected_model_profile: str | None
    selected_model: str | None
    approved_evidence_count: int
    candidate_evidence_count: int
    external_public_evidence_count: int
    conflict_count: int
    gap_count: int
    graph_path_count: int
    estimated_prompt_size_chars: int
    prompt_size_source: str
    request_metrics: Mapping[str, Any]
    budget_verdict: Mapping[str, Any]
    structured_output_required: bool
    provider_egress_allowed: bool
    maximum_primary_calls: int
    maximum_repair_calls: int
    maximum_escalation_calls: int
