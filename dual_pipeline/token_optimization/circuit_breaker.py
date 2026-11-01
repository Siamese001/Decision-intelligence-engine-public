"""
dual_pipeline/token_optimization/circuit_breaker.py

Agent 5 Fast-Failure Circuit Breaker.
Tracks candidate evaluation error hashes, detects repeat failure loops, and
halts futile retry cycles when consecutive identical candidate failures occur.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class CircuitBreakerTrippedError(RuntimeError):
    """Raised when the candidate retry loop exceeds the identical failure threshold."""
    pass


class CandidateCircuitBreaker:
    """
    Monitors candidate failures during dialectical synthesis and judging.
    Trips when at most max_consecutive_failures identical errors occur.
    """

    def __init__(self, max_consecutive_failures: int = 2) -> None:
        self.max_consecutive_failures = max(1, max_consecutive_failures)
        self.consecutive_failures: int = 0
        self.total_failures: int = 0
        self.last_error_hash: Optional[str] = None
        self.tripped: bool = False
        self.trip_reason: Optional[str] = None
        self.failure_history: List[Dict[str, Any]] = []

    @staticmethod
    def compute_error_hash(error_payload: Any) -> str:
        """
        Computes a deterministic, collision-resistant SHA-256 hash of an error signature.
        Normalizes structures, timestamps, and transient IDs.
        """
        if error_payload is None:
            return hashlib.sha256(b"none").hexdigest()

        normalized: Any = error_payload
        if isinstance(error_payload, Exception):
            normalized = f"{type(error_payload).__name__}:{str(error_payload)}"
        elif isinstance(error_payload, dict):
            # Extract key invariant error details if available
            filtered: Dict[str, Any] = {}
            for k in (
                "status", "overall_verdict", "qa_failures", "judge_failures",
                "reasons", "gate_failures", "final_decision", "output_3_score",
                "output_3_defects", "defect_codes", "scores",
            ):
                if k in error_payload and (error_payload[k] or error_payload[k] == 0):
                    filtered[k] = error_payload[k]
            if not filtered:
                filtered = {k: v for k, v in error_payload.items() if not k.startswith("timestamp") and k != "duration_s"}
            normalized = filtered
        elif isinstance(error_payload, (list, tuple)):
            normalized = sorted([str(x) for x in error_payload])

        try:
            serialized = json.dumps(normalized, sort_keys=True, default=str)
        except Exception:
            serialized = str(normalized)

        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def compute_candidate_hash(candidate: Any) -> str:
        """Computes a SHA-256 hash of the candidate deliverables."""
        if candidate is None:
            return hashlib.sha256(b"none").hexdigest()

        parts: List[str] = []
        for attr in ("output_1", "output_2", "output_3"):
            val = getattr(candidate, attr, None)
            if val is not None:
                parts.append(f"{attr}:{val}")
        if not parts:
            if isinstance(candidate, (dict, list)):
                parts.append(json.dumps(candidate, sort_keys=True, default=str))
            else:
                parts.append(str(candidate))

        return hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()

    def record_failure(
        self,
        error_payload: Any,
        candidate: Optional[Any] = None,
    ) -> bool:
        """
        Records a failure occurrence.
        Returns True if the circuit breaker tripped on this failure.
        """
        err_hash = self.compute_error_hash(error_payload)
        cand_hash = self.compute_candidate_hash(candidate) if candidate is not None else None
        self.total_failures += 1

        if self.last_error_hash == err_hash:
            self.consecutive_failures += 1
        else:
            self.consecutive_failures = 1
            self.last_error_hash = err_hash

        history_entry = {
            "attempt": self.total_failures,
            "error_hash": err_hash,
            "candidate_hash": cand_hash,
            "consecutive_failures": self.consecutive_failures,
        }
        self.failure_history.append(history_entry)

        if self.consecutive_failures >= self.max_consecutive_failures:
            self.tripped = True
            self.trip_reason = (
                f"CandidateCircuitBreaker tripped: {self.consecutive_failures} "
                f"identical consecutive candidate failures (error_hash: {err_hash[:12]})."
            )
            logger.warning(self.trip_reason)
            return True

        return False

    def record_success(self) -> None:
        """Records a successful evaluation; resets consecutive failure counter."""
        if not self.tripped:
            self.consecutive_failures = 0
            self.last_error_hash = None

    def reset(self) -> None:
        """Resets the circuit breaker state completely."""
        self.consecutive_failures = 0
        self.total_failures = 0
        self.last_error_hash = None
        self.tripped = False
        self.trip_reason = None
        self.failure_history.clear()

    def is_tripped(self) -> bool:
        """Returns True if the circuit breaker has tripped."""
        return self.tripped

    def to_dict(self) -> Dict[str, Any]:
        """Serializes circuit breaker state to dictionary."""
        return {
            "max_consecutive_failures": self.max_consecutive_failures,
            "consecutive_failures": self.consecutive_failures,
            "total_failures": self.total_failures,
            "last_error_hash": self.last_error_hash,
            "tripped": self.tripped,
            "trip_reason": self.trip_reason,
            "failure_history": self.failure_history,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CandidateCircuitBreaker:
        """Reconstructs circuit breaker from dictionary."""
        cb = cls(max_consecutive_failures=data.get("max_consecutive_failures", 2))
        cb.consecutive_failures = data.get("consecutive_failures", 0)
        cb.total_failures = data.get("total_failures", 0)
        cb.last_error_hash = data.get("last_error_hash")
        cb.tripped = data.get("tripped", False)
        cb.trip_reason = data.get("trip_reason")
        cb.failure_history = data.get("failure_history", [])
        return cb
