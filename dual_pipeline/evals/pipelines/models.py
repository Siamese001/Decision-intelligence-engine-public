"""Typed data models and scorecards for Pipelines A–D adversarial evaluations.

Provides structured evaluation outputs for:
- Pipeline A: Acoustic evidence, noise floor resilience, speaker attribution locking, and streaming latency SLA.
- Pipeline B: Fail-closed extraction, C-13 prompt injection defense, C-19 Career Leverage Firewall isolation, and DLQ quarantine.
- Pipeline C: Quad-engine retrieval, generational VectorCache invalidation, policy-before-retrieval, and steady-state latency SLA.
- Pipeline D: HITL 25% ambiguity margin gating, synthetic stop hook rejection, atomic card formatting, and Bayesian prior calibration.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


def _clean_val(v: Any) -> Any:
    if hasattr(v, "item"):
        return v.item()
    if isinstance(v, dict):
        return {k: _clean_val(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean_val(val) for val in v]
    return v


@dataclass(frozen=True)
class PipelineAEvalScorecard:
    """Scorecard for Pipeline A acoustic and intake evaluation."""
    passed: bool
    noise_floor_resilience_score: float
    speaker_locking_accuracy: float
    enrolled_speaker_preservation: float
    chunk_latency_sla_passed: bool
    avg_chunk_latency_ms: float
    max_chunk_latency_ms: float
    chunks_evaluated: int
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _clean_val(asdict(self))


@dataclass(frozen=True)
class PipelineBEvalScorecard:
    """Scorecard for Pipeline B extraction, prompt injection defense, and career firewall."""
    passed: bool
    c13_injection_neutralization_rate: float
    c19_career_firewall_leak_count: int
    json_repair_success_rate: float
    dlq_quarantine_reliability: float
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _clean_val(asdict(self))


@dataclass(frozen=True)
class PipelineCEvalScorecard:
    """Scorecard for Pipeline C quad-engine retrieval, vector cache, and latency SLA."""
    passed: bool
    vector_cache_invalidation_verified: bool
    steady_state_latency_ms: float
    queries_evaluated: int
    latency_sla_passed: bool
    law6_quarantine_enforced: bool
    law2_namespace_isolation_enforced: bool
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _clean_val(asdict(self))


@dataclass(frozen=True)
class PipelineDEvalScorecard:
    """Scorecard for Pipeline D HITL ambiguity gating, synthetic stop hook defense, and ledger priors."""
    passed: bool
    ambiguity_margin_accuracy: float
    synthetic_stop_hook_rejection_rate: float
    atomic_card_format_compliance: float
    bayesian_prior_calibration_gain: float
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _clean_val(asdict(self))


@dataclass
class PipelineADEvalMasterReport:
    """Consolidated master evaluation report across Pipelines A, B, C, and D."""
    run_id: str
    timestamp: str
    overall_passed: bool
    pipeline_a: Optional[PipelineAEvalScorecard] = None
    pipeline_b: Optional[PipelineBEvalScorecard] = None
    pipeline_c: Optional[PipelineCEvalScorecard] = None
    pipeline_d: Optional[PipelineDEvalScorecard] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def compute_canonical_digest(self) -> str:
        """Compute RFC-8259 SHA-256 digest of report contents."""
        data = {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "overall_passed": self.overall_passed,
            "pipeline_a": self.pipeline_a.to_dict() if self.pipeline_a else None,
            "pipeline_b": self.pipeline_b.to_dict() if self.pipeline_b else None,
            "pipeline_c": self.pipeline_c.to_dict() if self.pipeline_c else None,
            "pipeline_d": self.pipeline_d.to_dict() if self.pipeline_d else None,
            "metadata": self.metadata,
        }
        canonical_json = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "overall_passed": self.overall_passed,
            "canonical_digest": self.compute_canonical_digest(),
            "pipeline_a": self.pipeline_a.to_dict() if self.pipeline_a else None,
            "pipeline_b": self.pipeline_b.to_dict() if self.pipeline_b else None,
            "pipeline_c": self.pipeline_c.to_dict() if self.pipeline_c else None,
            "pipeline_d": self.pipeline_d.to_dict() if self.pipeline_d else None,
            "metadata": self.metadata,
        }
        return d


# Canonical domain benchmark aliases
AcousticBenchmarkScorecard = PipelineAEvalScorecard
ExtractionBenchmarkScorecard = PipelineBEvalScorecard
RetrievalBenchmarkScorecard = PipelineCEvalScorecard
DecisionBenchmarkScorecard = PipelineDEvalScorecard
UnifiedBenchmarkMasterReport = PipelineADEvalMasterReport
