import collections.abc
import dataclasses
from typing import Any, Dict, Optional


@dataclasses.dataclass
class IntakeResult(collections.abc.Mapping):
    status: str = ""
    milestone: str = ""
    source_kind: str = ""
    package_file_sha256: str = ""
    batch_admission: Optional[Dict[str, Any]] = None
    a2a_finalization_binding: Optional[Dict[str, Any]] = None
    a2a_finalization_reference: Optional[Dict[str, Any]] = None
    intake_id: str = ""
    run_id: str = ""
    trace_id: str = ""
    source_id: str = ""
    authorization: Dict[str, Any] = dataclasses.field(default_factory=dict)
    intake_authorization: Dict[str, Any] = dataclasses.field(default_factory=dict)
    review_authorization: Optional[Dict[str, Any]] = None
    production_qualified: bool = False
    embedding_backend: Optional[str] = None
    model_id: Optional[str] = None
    model_revision: Optional[str] = None
    model_manifest_digest: Optional[str] = None
    model_or_index_generation: Optional[str] = None
    candidate_count: int = 0
    entity_mention_count: int = 0
    entity_resolution_count: int = 0
    route_count: int = 0
    disposition_count: int = 0
    disposition_counts: Dict[str, int] = dataclasses.field(default_factory=dict)
    ingestion_decision_ledger_path: str = ""
    ingestion_decision_ledger_sha256: str = ""
    ingestion_decision_english_path: str = ""
    ingestion_decision_english_sha256: str = ""
    ingestion_decision_english_canonical_digest: str = ""
    ingestion_decision_table_path: str = ""
    ingestion_decision_table_sha256: str = ""
    hitl: Dict[str, Any] = dataclasses.field(default_factory=dict)
    graph_change_set_id: Optional[str] = None
    graph_operation_count: int = 0
    graph_delta_summary_path: Optional[str] = None
    graph_delta_summary_digest: Optional[str] = None
    graph_change_table: Optional[str] = None
    graph_sha256_before: str = ""
    graph_sha256_after: str = ""
    graph_mutation_count: int = 0
    persistent_embedding_write_count: int = 0
    ingestion_reasoning: Dict[str, Any] = dataclasses.field(default_factory=dict)
    provider_call_count: int = 0
    external_l2_call_count: int = 0
    primary_call_count: int = 0
    repair_call_count: int = 0
    escalation_call_count: int = 0
    judge_call_count: int = 0
    challenger_call_count: int = 0
    learning_event_count: int = 0
    learning_event_chain_digest: str = ""
    semantic_cache: Dict[str, Any] = dataclasses.field(default_factory=dict)
    idempotent_replay: bool = False

    def __init__(self, **kwargs):
        self._extra = {}
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
