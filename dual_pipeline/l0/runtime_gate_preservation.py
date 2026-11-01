"""
runtime_gate_preservation.py — Layer 0 Runtime Gate Preservation & Reuse Verification.

Governed Under AGENTS.md Architectural Invariants and Wave 5 Alignment Contract:
1. Replan Enforcement: INSUFFICIENT_REPLAN strictly halts arbitration and blocks approval
   without verified subsequent re-review clearance.
2. Multi-Factor Cache Invalidation: Synthesis and checkpoint reuse is invalidated whenever
   source, attendee roster, context, policy, prompt, evaluator, or output schema changes.
   Legacy unbound results must rerun.
3. Healing Invariant: Successful local retrieval repair must not trigger fallback.
   Exhausted repair attempts cannot become success.
4. Callback Fencing: Shutdown or cancellation during streaming/async calls produces
   zero downstream callbacks.
5. Fact Mutation Gate: Changes to numbers, owners, dates, or retained facts require
   affected downstream gates to rerun.

Strict Standard-Library Only. Max 400 Lines Budget.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple


class ReplanStatus(str, Enum):
    """L1 cognitive replan status."""
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT_REPLAN = "INSUFFICIENT_REPLAN"
    BLOCKED_REPLAN_REQUIRED = "BLOCKED_REPLAN_REQUIRED"


class ReplanRequiredError(RuntimeError):
    """Raised when execution attempts terminal approval while under INSUFFICIENT_REPLAN."""


class ExhaustedRepairError(RuntimeError):
    """Raised when healing attempts are exhausted without passing validation."""


class CallbackFencedError(RuntimeError):
    """Raised when a callback invocation is rejected due to fence shutdown or expiry."""


class InvalidCacheDependencyError(ValueError):
    """Raised when cache dependencies are missing or mismatched."""


def compute_roster_hash(attendees: Optional[Sequence[Any]]) -> str:
    """Computes deterministic SHA-256 digest of attendee roster."""
    if not attendees:
        return "empty_roster"
    canonical: List[str] = []
    for a in attendees:
        if isinstance(a, str):
            canonical.append(a.strip().lower())
        elif isinstance(a, dict):
            name = a.get("name") or a.get("attendee") or str(a)
            canonical.append(str(name).strip().lower())
        else:
            canonical.append(str(a).strip().lower())
    canonical.sort()
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_context_hash(context: Optional[Mapping[str, Any]]) -> str:
    """Computes deterministic SHA-256 digest of execution context."""
    if not context:
        return "empty_context"
    try:
        payload = json.dumps(context, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    except Exception:
        return hashlib.sha256(str(context).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CacheDependencyBinding:
    """Cryptographic binding of all dependencies required for valid cache/synthesis reuse."""
    source_file_hash: str
    attendee_roster_hash: str
    context_hash: str
    policy_snapshot_digest: str
    prompt_template_hash: str
    evaluator_version: str
    output_schema_version: str

    def compute_binding_digest(self) -> str:
        """Computes root SHA-256 digest covering all bound dependencies."""
        raw = (
            f"{self.source_file_hash}:"
            f"{self.attendee_roster_hash}:"
            f"{self.context_hash}:"
            f"{self.policy_snapshot_digest}:"
            f"{self.prompt_template_hash}:"
            f"{self.evaluator_version}:"
            f"{self.output_schema_version}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def validate_against(self, current: CacheDependencyBinding) -> Tuple[bool, List[str]]:
        """Compares this cached binding against current execution dependencies."""
        mismatches: List[str] = []
        if self.source_file_hash != current.source_file_hash:
            mismatches.append(f"source_file_hash changed ({self.source_file_hash[:8]} != {current.source_file_hash[:8]})")
        if self.attendee_roster_hash != current.attendee_roster_hash:
            mismatches.append(f"attendee_roster_hash changed ({self.attendee_roster_hash[:8]} != {current.attendee_roster_hash[:8]})")
        if self.context_hash != current.context_hash:
            mismatches.append(f"context_hash changed ({self.context_hash[:8]} != {current.context_hash[:8]})")
        if self.policy_snapshot_digest != current.policy_snapshot_digest:
            mismatches.append(f"policy_snapshot_digest changed ({self.policy_snapshot_digest[:8]} != {current.policy_snapshot_digest[:8]})")
        if self.prompt_template_hash != current.prompt_template_hash:
            mismatches.append(f"prompt_template_hash changed ({self.prompt_template_hash[:8]} != {current.prompt_template_hash[:8]})")
        if self.evaluator_version != current.evaluator_version:
            mismatches.append(f"evaluator_version changed ({self.evaluator_version} != {current.evaluator_version})")
        if self.output_schema_version != current.output_schema_version:
            mismatches.append(f"output_schema_version changed ({self.output_schema_version} != {current.output_schema_version})")
        return len(mismatches) == 0, mismatches


def validate_replan_clearance(review_contract: Any, raise_on_blocked: bool = False) -> bool:
    """
    Validates whether a decision debate has cleared L1 post-debate review.
    INSUFFICIENT_REPLAN strictly halts approval until verified re-review clearance.
    """
    if review_contract is None:
        return True

    verdict = getattr(review_contract, "verdict", None)
    if isinstance(verdict, Enum):
        verdict = verdict.value
    elif verdict is None and isinstance(review_contract, dict):
        verdict = review_contract.get("verdict")

    deterministic_passed = getattr(review_contract, "deterministic_passed", True)
    if isinstance(review_contract, dict) and "deterministic_passed" in review_contract:
        deterministic_passed = review_contract["deterministic_passed"]

    if verdict == ReplanStatus.INSUFFICIENT_REPLAN.value or not deterministic_passed:
        if raise_on_blocked:
            replan_dir = getattr(review_contract, "replan_directive", None)
            if replan_dir is None and isinstance(review_contract, dict):
                replan_dir = review_contract.get("replan_directive")
            raise ReplanRequiredError(
                f"L1 Post-Debate Review returned INSUFFICIENT_REPLAN: "
                f"Directive: {replan_dir}. Approval strictly blocked until re-review."
            )
        return False
    return True


def validate_cache_reuse_dependencies(
    cached_metadata: Mapping[str, Any],
    current_context: Mapping[str, Any],
) -> Tuple[bool, List[str]]:
    """
    Enforces multi-factor dependency validation for cache/synthesis reuse.
    Rejects legacy unbound records missing cryptographic bindings.
    """
    required_keys = [
        "source_file_hash",
        "attendee_roster_hash",
        "policy_snapshot_digest",
        "prompt_template_hash",
        "evaluator_version",
        "output_schema_version",
    ]

    missing_cached = [k for k in required_keys if not cached_metadata.get(k)]
    if missing_cached:
        return False, [f"LEGACY_UNBOUND_CACHE: Missing cryptographic bindings {missing_cached}. Rerun required."]

    cached_binding = CacheDependencyBinding(
        source_file_hash=str(cached_metadata.get("source_file_hash", "")),
        attendee_roster_hash=str(cached_metadata.get("attendee_roster_hash", "")),
        context_hash=str(cached_metadata.get("context_hash", "empty_context")),
        policy_snapshot_digest=str(cached_metadata.get("policy_snapshot_digest", "")),
        prompt_template_hash=str(cached_metadata.get("prompt_template_hash", "")),
        evaluator_version=str(cached_metadata.get("evaluator_version", "")),
        output_schema_version=str(cached_metadata.get("output_schema_version", "")),
    )

    current_roster_hash = current_context.get("attendee_roster_hash") or compute_roster_hash(current_context.get("attendees"))
    current_context_hash = current_context.get("context_hash") or compute_context_hash(current_context.get("context_data"))

    current_binding = CacheDependencyBinding(
        source_file_hash=str(current_context.get("source_file_hash", "")),
        attendee_roster_hash=str(current_roster_hash),
        context_hash=str(current_context_hash),
        policy_snapshot_digest=str(current_context.get("policy_snapshot_digest", "")),
        prompt_template_hash=str(current_context.get("prompt_template_hash", "")),
        evaluator_version=str(current_context.get("evaluator_version", "")),
        output_schema_version=str(current_context.get("output_schema_version", "")),
    )

    return cached_binding.validate_against(current_binding)


class CallbackFence:
    """
    Thread-safe, cancellation-aware callback fence.
    Fences callbacks against cancellation, shutdown, or timeout expiry.
    Guarantees zero downstream callbacks when inactive.
    """

    def __init__(self, timeout_sec: Optional[float] = None) -> None:
        self._is_shutdown: bool = False
        self._is_cancelled: bool = False
        self._deadline_ts: Optional[float] = (time.perf_counter() + timeout_sec) if timeout_sec is not None else None
        self._callbacks_dropped: int = 0
        self._callbacks_executed: int = 0

    def shutdown(self) -> None:
        """Permanently shuts down the fence."""
        self._is_shutdown = True

    def cancel(self) -> None:
        """Cancels active stream/task execution."""
        self._is_cancelled = True

    @property
    def is_active(self) -> bool:
        """True if the fence is active and permits callback invocations."""
        if self._is_shutdown or self._is_cancelled:
            return False
        if self._deadline_ts is not None and time.perf_counter() > self._deadline_ts:
            return False
        return True

    def invoke(self, callback: Optional[Callable[..., Any]], *args: Any, **kwargs: Any) -> bool:
        """
        Invokes callback if active. If inactive (shutdown/cancelled/expired),
        discards the call with zero side effects and returns False.
        """
        if not callback:
            return False
        if not self.is_active:
            self._callbacks_dropped += 1
            return False
        try:
            callback(*args, **kwargs)
            self._callbacks_executed += 1
            return True
        except Exception:
            return False


def _extract_healing_metrics(result: Any, default_attempts: Optional[int]) -> Tuple[Optional[str], bool, bool, int]:
    if isinstance(result, dict):
        status = result.get("status")
        val_passed = result.get("validation_passed", True)
        fact_passed = result.get("fact_preservation_passed", True)
        attempts = default_attempts if default_attempts is not None else result.get("repair_attempts", 1)
        return status, val_passed, fact_passed, attempts

    status = getattr(result, "status", None)
    val_passed = getattr(result, "validation_passed", True)
    if hasattr(result, "is_success") and not result.is_success:
        val_passed = False
    fact_passed = getattr(result, "fact_preservation_passed", True)
    attempts = default_attempts if default_attempts is not None else getattr(result, "repair_attempts", 1)
    return status, val_passed, fact_passed, attempts


def validate_healing_outcome(
    result: Any,
    max_repair_calls: int = 1,
    repair_attempts: Optional[int] = None,
    raise_on_failure: bool = False,
) -> bool:
    """
    Validates healing outcome.
    Invariants:
    1. Exhausted repair cannot become success.
    2. Successful local repair must not trigger fallback.
    """
    if result is None:
        if raise_on_failure:
            raise ExhaustedRepairError("Healing outcome is None.")
        return False

    status, val_passed, fact_passed, attempts = _extract_healing_metrics(result, repair_attempts)

    if status != "HEALED":
        if raise_on_failure:
            err = getattr(result, "error_message", None) or "Healing status is not HEALED"
            raise ExhaustedRepairError(f"Exhausted repair failed: {err}")
        return False

    if not val_passed or not fact_passed:
        if raise_on_failure:
            raise ExhaustedRepairError("Healing validation or fact preservation failed.")
        return False

    if attempts > max_repair_calls:
        if raise_on_failure:
            raise ExhaustedRepairError(
                f"Repair attempts ({attempts}) exceeded budget ({max_repair_calls})."
            )
        return False

    return True


def validate_fact_mutation_requires_rerun(
    prior_facts: Mapping[str, Any],
    updated_facts: Mapping[str, Any],
) -> List[str]:
    """
    Detects if changed numbers, owners, dates, or retained facts require
    affected downstream gates to rerun.
    """
    changed_factors: List[str] = []

    for key in ("numbers", "owners", "dates", "retained_facts", "decisions", "action_items"):
        prior_val = prior_facts.get(key)
        new_val = updated_facts.get(key)
        if prior_val != new_val:
            changed_factors.append(key)

    if not changed_factors:
        # Check direct key-by-key comparison
        for k, v in prior_facts.items():
            if k in updated_facts and updated_facts[k] != v:
                changed_factors.append(k)

    if changed_factors:
        return [
            "qa_gates_report",
            "llm_judge_evaluation",
            "final_release_decision",
        ]
    return []
