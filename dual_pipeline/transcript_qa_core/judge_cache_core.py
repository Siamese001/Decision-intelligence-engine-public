"""
Judge Cache Core: Canonical Keys, Single-Flight Deduplication, and Usage Normalization.

Enforces:
1. Canonical multi-dependency key hashing (content, reference, context, prompt, model, partition).
2. Elimination of default prompt hashes and source-free fallbacks.
3. Thread-safe single-flight deduplication of in-flight judge executions.
4. Normalized multi-provider token usage accounting (uncached input vs cached input vs avoided calls).
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class CanonicalJudgeKey:
    """Canonical dependency binding for judge evaluation caching."""
    artifact_type: str
    content_sha256: str
    reference_sha256: str
    context_sha256: str
    prompt_digest: str
    model: str
    reasoning_effort: str
    schema_version: str = "2.0"
    security_partition: str = "default"

    def validate(self) -> Tuple[bool, str]:
        """Validate that all required dependency hashes are present and valid."""
        if not self.artifact_type:
            return False, "Missing artifact_type"
        if not self.content_sha256 or len(self.content_sha256) < 16:
            return False, "Missing or invalid content_sha256"
        if not self.reference_sha256:
            return False, "Missing reference_sha256 (must bind actual reference or explicit 'none')"
        if not self.context_sha256:
            return False, "Missing context_sha256"
        if not self.prompt_digest or self.prompt_digest == "default_prompt":
            return False, f"Invalid prompt_digest '{self.prompt_digest}': default prompt hash prohibited"
        if not self.model:
            return False, "Missing model identifier"
        return True, "Valid"

    def to_key(self) -> str:
        """Serializes into a deterministic canonical cache key string."""
        valid, reason = self.validate()
        if not valid:
            raise ValueError(f"Cannot serialize invalid CanonicalJudgeKey: {reason}")

        payload = {
            "artifact": self.artifact_type,
            "content": self.content_sha256,
            "reference": self.reference_sha256,
            "context": self.context_sha256,
            "prompt": self.prompt_digest,
            "model": self.model,
            "effort": self.reasoning_effort,
            "schema": self.schema_version,
            "partition": self.security_partition,
        }
        canonical_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()
        return f"judge:{self.artifact_type}:{self.security_partition}:{digest}"


class _FlightCall:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.val: Any = None
        self.err: Optional[Exception] = None


class SingleFlightExecutor:
    """Coordinates concurrent calls to avoid duplicate identical in-flight provider invocations."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: Dict[str, _FlightCall] = {}

    def execute(self, key: str, fn: Callable[[], T]) -> Tuple[T, bool]:
        """
        Executes fn for key, or awaits in-flight execution if already underway.
        Returns (result, was_deduplicated).
        """
        with self._lock:
            call = self._calls.get(key)
            if call is not None:
                is_leader = False
            else:
                call = _FlightCall()
                self._calls[key] = call
                is_leader = True

        if not is_leader:
            from config.request_deadline import remaining_timeout
            waited = call.event.wait(timeout=remaining_timeout(120.0))
            if not waited:
                raise TimeoutError(f"SingleFlightGroup: timed out waiting for in-flight leader execution of key '{key}'")
            if call.err is not None:
                raise call.err
            return call.val, True

        try:
            call.val = fn()
            return call.val, False
        except Exception as exc:
            call.err = exc
            raise
        finally:
            with self._lock:
                self._calls.pop(key, None)
            call.event.set()


def _parse_dict_usage(raw_usage: Dict[str, Any]) -> Tuple[int, int, int, int, int]:
    pt = int(raw_usage.get("prompt_tokens") or raw_usage.get("input_tokens") or raw_usage.get("prompt_token_count") or 0)
    ct = int(raw_usage.get("completion_tokens") or raw_usage.get("output_tokens") or raw_usage.get("candidates_token_count") or 0)
    details = raw_usage.get("prompt_tokens_details") or {}
    cpt = int(details.get("cached_tokens") or raw_usage.get("cached_content_token_count") or raw_usage.get("cache_read_input_tokens") or 0)
    cdetails = raw_usage.get("completion_tokens_details") or {}
    rt = int(cdetails.get("reasoning_tokens") or raw_usage.get("thinking_tokens") or 0)
    cwt = int(details.get("cache_creation_input_tokens") or details.get("cache_write_tokens") or raw_usage.get("cache_creation_input_tokens") or 0)
    return pt, ct, cpt, rt, cwt


def _parse_obj_usage(raw_usage: Any) -> Tuple[int, int, int, int, int]:
    pt = int(getattr(raw_usage, "prompt_tokens", 0) or getattr(raw_usage, "input_tokens", 0) or 0)
    ct = int(getattr(raw_usage, "completion_tokens", 0) or getattr(raw_usage, "output_tokens", 0) or 0)
    cpt, cwt, rt = 0, 0, 0
    details = getattr(raw_usage, "prompt_tokens_details", None)
    if details:
        cpt = int(getattr(details, "cached_tokens", 0) or 0)
        cwt = int(getattr(details, "cache_creation_input_tokens", 0) or getattr(details, "cache_write_tokens", 0) or 0)
    cdetails = getattr(raw_usage, "completion_tokens_details", None)
    if cdetails:
        rt = int(getattr(cdetails, "reasoning_tokens", 0) or 0)
    return pt, ct, cpt, rt, cwt


@dataclass
class NormalizedTokenUsage:
    """Normalized multi-provider token usage record."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    uncached_prompt_tokens: int = 0
    reasoning_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0
    model_id: str = ""
    duration_seconds: float = 0.0
    avoided_calls: int = 0
    estimated_avoided_tokens: int = 0

    @classmethod
    def from_raw(
        cls,
        raw_usage: Any,
        model_id: str = "",
        duration_seconds: float = 0.0,
        is_result_cache_hit: bool = False,
        estimated_tokens: int = 0,
    ) -> NormalizedTokenUsage:
        """Parses and normalizes usage from dict, OpenAI, Gemini, or Anthropic objects."""
        if is_result_cache_hit:
            return cls(
                model_id=model_id,
                duration_seconds=duration_seconds,
                avoided_calls=1,
                estimated_avoided_tokens=estimated_tokens,
            )

        if isinstance(raw_usage, dict):
            pt, ct, cpt, rt, cwt = _parse_dict_usage(raw_usage)
        elif raw_usage is not None:
            pt, ct, cpt, rt, cwt = _parse_obj_usage(raw_usage)
        else:
            pt, ct, cpt, rt, cwt = 0, 0, 0, 0, 0

        uncached_pt = max(0, pt - cpt)
        tot = pt + ct
        return cls(
            prompt_tokens=pt,
            completion_tokens=ct,
            cached_tokens=cpt,
            uncached_prompt_tokens=uncached_pt,
            reasoning_tokens=rt,
            cache_write_tokens=cwt,
            total_tokens=tot,
            model_id=model_id,
            duration_seconds=duration_seconds,
            avoided_calls=0,
            estimated_avoided_tokens=0,
        )


GLOBAL_SINGLE_FLIGHT = SingleFlightExecutor()
