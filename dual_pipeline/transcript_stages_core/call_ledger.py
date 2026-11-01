"""Call telemetry ledger and budget accounting for Stage L2 Deliverable 02 (Executive Recap).

Instruments per-call observability, tracking:
- pass_id ("2A", "2B", "2C", etc.)
- pass_attempt (1-indexed pass-level attempt)
- transport_attempt (1-indexed HTTP transport attempt)
- usageMetadata (prompt, output, cached, thinking tokens)
- finishReason
- latency_ms
- outcome status and error classification
- prompt-block byte breakdown (governance context, transcript, metadata, remediation)
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ACTIVE_CALL_LEDGER: contextvars.ContextVar[Optional[D02CallLedger]] = contextvars.ContextVar(
    "_ACTIVE_CALL_LEDGER", default=None
)


@dataclass
class CallTelemetryRecord:
    """Individual HTTP invocation record for Gemini / LLM synthesis."""

    call_id: str
    pass_id: str
    pass_attempt: int
    transport_attempt: int
    model: str
    status: str  # "SUCCESS", "FAILED"
    error: Optional[str] = None
    finish_reason: Optional[str] = None
    prompt_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    thinking_tokens: int = 0
    latency_ms: float = 0.0
    workflow: str = "d02_recap"
    cached_token_share: float = 0.0
    requested_model: Optional[str] = None
    resolved_model: Optional[str] = None
    dispatched_model: Optional[str] = None
    observed_model: Optional[str] = None
    evidence_strength: str = "OBSERVED_RESPONSE"
    timestamp_iso: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class D02CallLedger:
    """Thread-safe collector for Output 2 LLM call telemetry and budget receipts."""

    def __init__(self, operation_id: Optional[str] = None) -> None:
        self.operation_id = operation_id or f"d02-call-{int(time.time() * 1000)}"
        self._lock = threading.Lock()
        self._records: List[CallTelemetryRecord] = []
        self._prompt_block_bytes: Dict[str, int] = {}
        self._token: Optional[contextvars.Token] = None
        self._call_counter: int = 0

    def __enter__(self) -> D02CallLedger:
        self._token = _ACTIVE_CALL_LEDGER.set(self)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._token is not None:
            _ACTIVE_CALL_LEDGER.reset(self._token)
            self._token = None

    def record_call(
        self,
        *,
        pass_id: str,
        pass_attempt: int,
        transport_attempt: int,
        model: str,
        status: str,
        error: Optional[str] = None,
        finish_reason: Optional[str] = None,
        usage_metadata: Optional[Dict[str, Any]] = None,
        latency_ms: float = 0.0,
        workflow: Optional[str] = None,
        requested_model: Optional[str] = None,
        resolved_model: Optional[str] = None,
        dispatched_model: Optional[str] = None,
        observed_model: Optional[str] = None,
        evidence_strength: str = "OBSERVED_RESPONSE",
    ) -> CallTelemetryRecord:
        """Record a single HTTP attempt in the telemetry ledger."""
        with self._lock:
            self._call_counter += 1
            call_id = f"{self.operation_id}-{self._call_counter:03d}"

            usage = usage_metadata or {}
            prompt_tokens = int(usage.get("promptTokenCount", 0))
            output_tokens = int(usage.get("candidatesTokenCount", 0))
            cached_tokens = int(usage.get("cachedContentTokenCount", 0))
            total_tokens = int(usage.get("totalTokenCount", prompt_tokens + output_tokens))

            # Extract thinking tokens if reported in details
            thinking_tokens = 0
            tokens_details = usage.get("candidatesTokensDetails", [])
            if isinstance(tokens_details, list):
                for detail in tokens_details:
                    if isinstance(detail, dict) and detail.get("modality") == "THINKING":
                        thinking_tokens += int(detail.get("tokenCount", 0))

            cached_share = round(cached_tokens / max(1, prompt_tokens), 4) if prompt_tokens > 0 else 0.0

            record = CallTelemetryRecord(
                call_id=call_id,
                pass_id=pass_id or "UNKNOWN",
                pass_attempt=max(1, pass_attempt),
                transport_attempt=max(1, transport_attempt),
                model=model,
                status=status,
                error=error,
                finish_reason=finish_reason,
                prompt_tokens=prompt_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                total_tokens=total_tokens,
                thinking_tokens=thinking_tokens,
                latency_ms=round(latency_ms, 2),
                workflow=workflow or "d02_recap",
                cached_token_share=cached_share,
                requested_model=requested_model or model,
                resolved_model=resolved_model or model,
                dispatched_model=dispatched_model or model,
                observed_model=observed_model,
                evidence_strength=evidence_strength,
            )
            self._records.append(record)
            return record

    def set_prompt_block_bytes(self, block_bytes: Dict[str, int]) -> None:
        """Store measured byte breakdown of input prompt components."""
        with self._lock:
            self._prompt_block_bytes = dict(block_bytes)

    @property
    def records(self) -> List[CallTelemetryRecord]:
        with self._lock:
            return list(self._records)

    @property
    def call_count(self) -> int:
        with self._lock:
            return len(self._records)

    def compute_budget_summary(
        self, prompt_block_bytes: Optional[Dict[str, int]] = None
    ) -> Dict[str, Any]:
        """Compute aggregate call counts, token totals, and scenario classification."""
        with self._lock:
            records = list(self._records)
            blocks = prompt_block_bytes or self._prompt_block_bytes

        total_calls = len(records)
        calls_by_pass: Dict[str, int] = {}
        successful_calls = 0
        failed_calls = 0

        total_prompt_tokens = 0
        total_output_tokens = 0
        total_cached_tokens = 0
        total_tokens = 0
        total_thinking_tokens = 0
        total_latency_ms = 0.0
        transport_retries = 0

        for r in records:
            calls_by_pass[r.pass_id] = calls_by_pass.get(r.pass_id, 0) + 1
            if r.status == "SUCCESS":
                successful_calls += 1
            else:
                failed_calls += 1
            if r.transport_attempt > 1:
                transport_retries += 1
            total_prompt_tokens += r.prompt_tokens
            total_output_tokens += r.output_tokens
            total_cached_tokens += r.cached_tokens
            total_tokens += r.total_tokens
            total_thinking_tokens += r.thinking_tokens
            total_latency_ms += r.latency_ms

        total_billable_prompt_tokens = max(0, total_prompt_tokens - total_cached_tokens)

        # Scenario categorization:
        # BEST_CASE: exactly 3 calls (1 for 2A, 1 for 2B, 1 for 2C) with 0 transport retries
        # WORST_CASE: 21 calls (3 passes * 7 worst-case transport attempts)
        if total_calls == 3 and transport_retries == 0 and failed_calls == 0:
            scenario = "BEST_CASE"
        elif total_calls >= 21:
            scenario = "WORST_CASE"
        else:
            scenario = "RETRIED"

        return {
            "operation_id": self.operation_id,
            "scenario": scenario,
            "total_calls": total_calls,
            "successful_calls": successful_calls,
            "failed_calls": failed_calls,
            "transport_retries": transport_retries,
            "calls_by_pass": calls_by_pass,
            "token_totals": {
                "prompt_tokens": total_prompt_tokens,
                "cached_tokens": total_cached_tokens,
                "cached_token_share": round(total_cached_tokens / max(1, total_prompt_tokens), 4) if total_prompt_tokens > 0 else 0.0,
                "uncached_prompt_tokens": max(0, total_prompt_tokens - total_cached_tokens),
                "billable_prompt_tokens": total_billable_prompt_tokens,
                "output_tokens": total_output_tokens,
                "thinking_tokens": total_thinking_tokens,
                "total_tokens": total_tokens,
            },
            "latency": {
                "total_ms": round(total_latency_ms, 2),
                "avg_per_call_ms": (
                    round(total_latency_ms / total_calls, 2) if total_calls else 0.0
                ),
            },
            "prompt_block_bytes": blocks,
            "records": [r.to_dict() for r in records],
        }

    def emit_receipt(self, output_path: Optional[Path] = None) -> Dict[str, Any]:
        """Emit receipt JSON to disk and return summary dict."""
        summary = self.compute_budget_summary()
        target = output_path
        if target is None:
            receipts_dir = Path(".local/receipts")
            receipts_dir.mkdir(parents=True, exist_ok=True)
            target = receipts_dir / f"d02_call_budget_receipt_{self.operation_id}.json"

        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        logger.info("D02 call budget receipt written to %s", target)
        return summary


def get_active_call_ledger() -> Optional[D02CallLedger]:
    """Retrieve active call ledger from context."""
    return _ACTIVE_CALL_LEDGER.get()


def set_active_call_ledger(ledger: Optional[D02CallLedger]) -> None:
    """Set active call ledger in context."""
    _ACTIVE_CALL_LEDGER.set(ledger)


def measure_prompt_block_bytes(
    system_prompt: str, user_content: str
) -> Dict[str, int]:
    """Measure byte breakdown across standard prompt sections and XML blocks."""
    blocks: Dict[str, int] = {
        "system_prompt_bytes": len(system_prompt.encode("utf-8")),
        "user_content_total_bytes": len(user_content.encode("utf-8")),
    }

    # Extract known XML blocks from user_content
    known_tags = [
        "mandatory_structure",
        "meeting_metadata",
        "forensic_transcript",
        "operator_prior_hypothesis",
        "evidence_locks",
        "uploaded_meeting_collateral",
        "prior_context",
        "prior_decisions",
        "prior_risks",
        "user_clarifications",
        "project_resources",
        "prior_recap_candidate",
        "remediation_directive",
    ]

    classified_bytes = 0
    for tag in known_tags:
        pattern = rf"<{tag}>[\s\S]*?</{tag}>"
        matches = re.findall(pattern, user_content)
        if matches:
            tag_bytes = sum(len(m.encode("utf-8")) for m in matches)
            blocks[f"block_{tag}_bytes"] = tag_bytes
            classified_bytes += tag_bytes

    blocks["unclassified_bytes"] = max(
        0, blocks["user_content_total_bytes"] - classified_bytes
    )
    return blocks
