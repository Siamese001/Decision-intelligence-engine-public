import collections.abc
import dataclasses
from typing import Any, Dict, List, Optional


@dataclasses.dataclass
class QueryResult(collections.abc.Mapping):
    query_id: str = ""
    query_type: str = ""
    delivery_profile: str = ""
    artifact_type: str = ""
    assurance_profile: str = ""
    routed_materiality: str = ""
    routing_policy_digest: str = ""
    route: str = ""
    retrieval_mode: str = ""
    x3_outcome: str = ""
    graph_snapshot: str = ""
    generation_id: str = ""
    embedding_backend: str = ""
    production_qualified: bool = False
    assurance_tier: str = ""
    operational_status: str = ""
    assurance_status: str = ""
    qualification_id: Optional[str] = None
    qualification_digest: Optional[str] = None
    approved_record_count: int = 0
    candidate_record_count: int = 0
    selected_record_counts_by_lane: Dict[str, int] = dataclasses.field(default_factory=dict)
    l2_report_supported_record_counts_by_lane: Dict[str, int] = dataclasses.field(default_factory=dict)
    l2_visible_record_counts_by_lane: Dict[str, int] = dataclasses.field(default_factory=dict)
    dense_executed: bool = False
    sparse_executed: bool = False
    bm25_executed: bool = False
    colbert_executed: bool = False
    graph_traversal_executed: bool = False
    graph_requested_hops: int = 0
    graph_max_hop_reached: int = 0
    graph_expanded_record_count: int = 0
    connected_evidence_map_id: str = ""
    connected_evidence_map_digest: str = ""
    counterevidence_count: int = 0
    opportunity_context_count: int = 0
    lane_blending: bool = False
    l2_policy: str = ""
    l2_required: bool = False
    l2_reason_code: str = ""
    l2_decision_id: str = ""
    l2_decision_digest: str = ""
    selected_model_profile: str = ""
    selected_model: str = ""
    estimated_prompt_size_chars: int = 0
    routing_rationale: Dict[str, Any] = dataclasses.field(default_factory=dict)
    delivered_model_profile: Optional[str] = None
    delivered_model: Optional[str] = None
    provider_egress_allowed: bool = True
    l2_disposition: str = ""
    answer_delivery: str = ""
    synthesis_status: str = ""
    evidence_only_reason: Optional[str] = None
    provider_initialization_count: int = 0
    model_call_count: int = 0
    external_l2_call_count: int = 0
    content_repair_call_count: int = 0
    total_external_model_call_count: int = 0
    primary_call_count: int = 0
    schema_repair_call_count: int = 0
    quality_escalation_call_count: int = 0
    judge_call_count: int = 0
    challenger_call_count: int = 0
    quality_provider_call_count: int = 0
    x1_quality: Dict[str, Any] = dataclasses.field(default_factory=dict)
    provider_failure_code: Optional[str] = None
    terminal_outcome: Optional[str] = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_input_cost: float = 0.0
    estimated_output_cost: float = 0.0
    estimated_total_cost: float = 0.0
    latency_ms: float = 0.0
    prompt_hash: Optional[str] = None
    query_coverage_manifest_digest: str = ""
    query_coverage_requirement_count: int = 0
    retrieval_evidence_report_digest: str = ""
    claim_evidence_map_digest: str = ""
    retrieval_evidence: Dict[str, Any] = dataclasses.field(default_factory=dict)
    evidence_packet_hash: str = ""
    strategic_evidence_packet_digest: Optional[str] = None
    strategic_reasoning_output_digest: Optional[str] = None
    graph_sha256_before: str = ""
    graph_sha256_after: str = ""
    runtime_binding: Dict[str, Any] = dataclasses.field(default_factory=dict)
    graph_mutation_count: int = 0
    l1_controller: Dict[str, Any] = dataclasses.field(default_factory=dict)
    learning_event_count: int = 0
    learning_event_chain_digest: str = ""
    required_stages: List[str] = dataclasses.field(default_factory=list)
    semantic_cache: Dict[str, Any] = dataclasses.field(default_factory=dict)
    answer: Optional[Dict[str, Any]] = None
    idempotent_replay: bool = False
    card_scope: str = ""

    query_destination_decision_digest: str = ""
    query_graph_fit_decision_digest: str = ""
    graph_fit_status: str = ""
    graph_fit_reason_codes: List[str] = dataclasses.field(default_factory=list)
    destination_reason_codes: List[str] = dataclasses.field(default_factory=list)
    eligibility_checks: Dict[str, Any] = dataclasses.field(default_factory=dict)
    eligibility_status: str = ""
    destination: str = ""

    def __init__(self, **kwargs):
        self._extra = {}
        for f in dataclasses.fields(self):
            if f.default is not dataclasses.MISSING:
                setattr(self, f.name, f.default)
            elif f.default_factory is not dataclasses.MISSING:
                setattr(self, f.name, f.default_factory())
        known = {f.name for f in dataclasses.fields(self)}
        for k, v in kwargs.items():
            if k in known:
                setattr(self, k, v)
            else:
                self._extra[k] = v

    def as_dict(self) -> Dict[str, Any]:
        d = {}
        for f in dataclasses.fields(self):
            d[f.name] = getattr(self, f.name, None)
        if hasattr(self, "_extra"):
            d.update(self._extra)
        return d

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        if hasattr(self, "_extra") and key in self._extra:
            return self._extra[key]
        raise KeyError(key)

    def __iter__(self):
        keys = [f.name for f in dataclasses.fields(self)]
        if hasattr(self, "_extra"):
            for k in self._extra:
                if k not in keys:
                    keys.append(k)
        return iter(keys)

    def __len__(self):
        total = len(dataclasses.fields(self))
        if hasattr(self, "_extra"):
            extra_unique = [k for k in self._extra if not hasattr(self, k)]
            total += len(extra_unique)
        return total

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default
