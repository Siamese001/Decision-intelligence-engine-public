"""L1 Post-L2 Review Contracts & Tamper-Evident Digest Schema.

Defines the immutable data structures and verdicts for L1 cognitive review
of sealed L2 observations before workflow progression to exit gates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Optional, Sequence

from dual_pipeline.core import digest, stable_id, utc_now


class L1ReviewVerdict(StrEnum):
    """Overall outcome of L1 cognitive review over a sealed L2 observation."""
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT_RETRY = "INSUFFICIENT_RETRY"
    INSUFFICIENT_REPLAN = "INSUFFICIENT_REPLAN"
    FATAL_BLOCK = "FATAL_BLOCK"


class DeterministicReasonCode(StrEnum):
    """Closed vocabulary of deterministic verification failure reasons."""
    L2_EXECUTION_FAILED = "L2_EXECUTION_FAILED"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    REQUIRED_FIELDS_MISSING = "REQUIRED_FIELDS_MISSING"
    CLAIMS_UNGROUNDED = "CLAIMS_UNGROUNDED"
    CITATIONS_UNRESOLVED = "CITATIONS_UNRESOLVED"
    GRAPH_MUTATION_VIOLATION = "GRAPH_MUTATION_VIOLATION"
    ACCEPTANCE_CRITERIA_UNMET = "ACCEPTANCE_CRITERIA_UNMET"
    CANONICAL_DIGEST_MISMATCH = "CANONICAL_DIGEST_MISMATCH"
    ANSWER_MISSING = "ANSWER_MISSING"
    EVIDENCE_PACKET_MISSING = "EVIDENCE_PACKET_MISSING"


@dataclass(frozen=True)
class DeterministicCheckItem:
    """A single objective execution verification check."""
    name: str
    passed: bool
    details: str = ""
    reason_code: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "details": self.details,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class L1PostL2ReviewContract:
    """Tamper-evident audit artifact recording L1 post-L2 review."""
    review_id: str
    run_id: str
    query_id: str
    plan_id: str
    sealed_artifact_id: str
    verdict: L1ReviewVerdict
    deterministic_passed: bool
    deterministic_checks: tuple[DeterministicCheckItem, ...]
    semantic_passed: bool
    semantic_reasoning: dict[str, Any] = field(default_factory=dict)
    replan_directive: Optional[dict[str, Any]] = None
    reviewed_at: str = field(default_factory=utc_now)
    canonical_digest: dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        query_id: str,
        plan_id: str,
        sealed_artifact_id: str,
        verdict: L1ReviewVerdict,
        deterministic_passed: bool,
        deterministic_checks: Sequence[DeterministicCheckItem],
        semantic_passed: bool,
        semantic_reasoning: Optional[Mapping[str, Any]] = None,
        replan_directive: Optional[Mapping[str, Any]] = None,
        reviewed_at: Optional[str] = None,
    ) -> "L1PostL2ReviewContract":
        ts = reviewed_at or utc_now()
        r_id = stable_id("l1-review", run_id, query_id, ts)

        body_without_digest: dict[str, Any] = {
            "review_id": r_id,
            "run_id": run_id,
            "query_id": query_id,
            "plan_id": plan_id,
            "sealed_artifact_id": sealed_artifact_id,
            "verdict": verdict.value,
            "deterministic_passed": deterministic_passed,
            "deterministic_checks": [c.to_dict() for c in deterministic_checks],
            "semantic_passed": semantic_passed,
            "semantic_reasoning": dict(semantic_reasoning or {}),
            "replan_directive": dict(replan_directive) if replan_directive else None,
            "reviewed_at": ts,
        }

        c_digest = {
            "algorithm": "sha256",
            "scope": "artifact_without_canonical_digest",
            "value": digest(body_without_digest),
        }

        return cls(
            review_id=r_id,
            run_id=run_id,
            query_id=query_id,
            plan_id=plan_id,
            sealed_artifact_id=sealed_artifact_id,
            verdict=verdict,
            deterministic_passed=deterministic_passed,
            deterministic_checks=tuple(deterministic_checks),
            semantic_passed=semantic_passed,
            semantic_reasoning=dict(semantic_reasoning or {}),
            replan_directive=dict(replan_directive) if replan_directive else None,
            reviewed_at=ts,
            canonical_digest=c_digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "run_id": self.run_id,
            "query_id": self.query_id,
            "plan_id": self.plan_id,
            "sealed_artifact_id": self.sealed_artifact_id,
            "verdict": self.verdict.value,
            "deterministic_passed": self.deterministic_passed,
            "deterministic_checks": [c.to_dict() for c in self.deterministic_checks],
            "semantic_passed": self.semantic_passed,
            "semantic_reasoning": self.semantic_reasoning,
            "replan_directive": self.replan_directive,
            "reviewed_at": self.reviewed_at,
            "canonical_digest": self.canonical_digest,
        }

    def verify_integrity(self) -> bool:
        """Verifies that the canonical digest matches the artifact body."""
        body = self.to_dict()
        recorded = body.pop("canonical_digest", None)
        if not isinstance(recorded, dict) or "value" not in recorded:
            return False
        computed = digest(body)
        return computed == recorded["value"]
