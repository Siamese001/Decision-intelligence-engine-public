"""Data models and serialization utilities for Answer Evaluation Receipt."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "contracts"
    / "answer_eval"
    / "answer_eval_receipt.schema.json"
)


def compute_eval_receipt_digest(payload: Dict[str, Any]) -> str:
    """Computes deterministic RFC 8785 canonical SHA-256 over receipt payload."""
    payload_copy = {k: v for k, v in payload.items() if k != "receipt_digest"}
    canonical_json = json.dumps(
        payload_copy,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def validate_eval_receipt(payload: Dict[str, Any]) -> None:
    """Validates payload against answer_eval_receipt JSON schema."""
    if not SCHEMA_PATH.is_file():
        return
    from jsonschema import validate
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = json.load(f)
    validate(instance=payload, schema=schema)


@dataclass
class OperatorUsefulnessStats:
    useful_count: int = 0
    not_useful_count: int = 0
    wrong_count: int = 0
    total_feedback: int = 0
    beta_posterior_mean: float = 0.50

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LatencyStats:
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    by_rigor_tier: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReplayComparison:
    candidate_parameter_snapshot_digest: Optional[str] = None
    candidate_params: Dict[str, Any] = field(default_factory=dict)
    reproduced_citation_exact_match_rate: float = 1.0
    groundedness_delta: float = 0.0
    latency_p95_delta_ms: float = 0.0
    replayed_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LLMJudgeScore:
    evaluated_count: int = 0
    average_relevance_score: float = 0.0
    provider_bound: bool = False
    model_id: Optional[str] = None
    evaluator_kind: str = "structural_heuristic"
    semantic_validation_passed: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AnswerEvalReceipt:
    eval_id: str
    timestamp: str
    pipeline: str
    ledger_range: Dict[str, Any]
    metrics: Dict[str, Any]
    contract_id: str = "urn:nee:eval:answer-eval-receipt:1.0.0"
    schema_version: str = "1.0.0"
    graph_sha256: Optional[str] = None
    generation: Optional[Any] = None
    parameter_snapshot_digest: Optional[str] = None
    replay_comparison: Optional[Dict[str, Any]] = None
    llm_judge: Optional[Dict[str, Any]] = None
    receipt_digest: str = ""

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        if not self.receipt_digest:
            res["receipt_digest"] = compute_eval_receipt_digest(res)
        return res
