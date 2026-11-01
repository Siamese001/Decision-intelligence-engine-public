"""llm_recorder.py — LLM Execution Tracing & Semantic Conventions."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

logger = logging.getLogger(__name__)


def compute_prompt_hash(prompt_content: Any) -> str:
    """Computes SHA-256 fingerprint of prompt text to guarantee privacy without losing auditability."""
    try:
        if isinstance(prompt_content, (dict, list)):
            text = json.dumps(prompt_content, sort_keys=True)
        else:
            text = str(prompt_content)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    except Exception:
        return ""


class LLMTraceRecorder:
    def __init__(self, span: trace.Span) -> None:
        self.span = span

    def record_response(self, response_obj: Any) -> None:
        """Extracts actual returned model and token counts from the response."""
        try:
            # Actual response model
            actual_model = getattr(response_obj, "model", None)
            if not actual_model and isinstance(response_obj, dict):
                actual_model = response_obj.get("model")
            if actual_model:
                self.span.set_attribute("gen_ai.response.model", str(actual_model))

            # Response ID
            resp_id = getattr(response_obj, "id", None)
            if not resp_id and isinstance(response_obj, dict):
                resp_id = response_obj.get("id")
            if resp_id:
                self.span.set_attribute("gen_ai.response.id", str(resp_id))

            # Token usage
            usage = getattr(response_obj, "usage", None)
            if usage is None and isinstance(response_obj, dict):
                usage = response_obj.get("usage") or response_obj.get("usageMetadata") or response_obj.get("usage_metadata")
            elif usage is None and hasattr(response_obj, "usage_metadata"):
                usage = response_obj.usage_metadata

            if usage:
                prompt_tokens = getattr(usage, "prompt_tokens", None) or getattr(usage, "promptTokenCount", None)
                completion_tokens = getattr(usage, "completion_tokens", None) or getattr(usage, "candidatesTokenCount", None)
                total_tokens = getattr(usage, "total_tokens", None) or getattr(usage, "totalTokenCount", None)

                if isinstance(usage, dict):
                    prompt_tokens = usage.get("prompt_tokens") or usage.get("promptTokenCount")
                    completion_tokens = usage.get("completion_tokens") or usage.get("candidatesTokenCount")
                    total_tokens = usage.get("total_tokens") or usage.get("totalTokenCount")

                if prompt_tokens is not None:
                    self.span.set_attribute("gen_ai.usage.prompt_tokens", int(prompt_tokens))
                if completion_tokens is not None:
                    self.span.set_attribute("gen_ai.usage.completion_tokens", int(completion_tokens))
                if total_tokens is not None:
                    self.span.set_attribute("gen_ai.usage.total_tokens", int(total_tokens))

                # Reasoning tokens (OpenAI o1/o3/gpt-5 style)
                details = getattr(usage, "completion_tokens_details", None)
                if details is None and isinstance(usage, dict):
                    details = usage.get("completion_tokens_details")

                reasoning_tokens = None
                if details:
                    reasoning_tokens = getattr(details, "reasoning_tokens", None)
                    if reasoning_tokens is None and isinstance(details, dict):
                        reasoning_tokens = details.get("reasoning_tokens")

                if reasoning_tokens is not None:
                    self.span.set_attribute(
                        "gen_ai.usage.reasoning_tokens", int(reasoning_tokens)
                    )

            # Prompt caching metrics via SSOT normalizer
            from dual_pipeline.prompt_cache import normalize_cached_tokens

            cached_tokens = normalize_cached_tokens(response_obj)
            if cached_tokens > 0:
                self.span.set_attribute("gen_ai.usage.cached_tokens", int(cached_tokens))

            # Prompt cache diagnostics telemetry
            diag = getattr(response_obj, "prompt_cache_diagnostics", None)
            if diag is None and isinstance(response_obj, dict):
                diag = response_obj.get("prompt_cache_diagnostics")

            if diag:
                diag_type = getattr(diag, "type", None) or (diag.get("type") if isinstance(diag, dict) else None)
                if diag_type:
                    self.span.set_attribute("gen_ai.prompt_cache.type", str(diag_type))
                miss_reason = getattr(diag, "reason", None) or (diag.get("reason") if isinstance(diag, dict) else None)
                if miss_reason:
                    self.span.set_attribute("gen_ai.prompt_cache.reason", str(miss_reason))
                reusable = getattr(diag, "comparison_reusable_tokens", None) or (diag.get("comparison_reusable_tokens") if isinstance(diag, dict) else None)
                if reusable is not None:
                    self.span.set_attribute("gen_ai.prompt_cache.reusable_tokens", int(reusable))
                missed = getattr(diag, "cache_missed_tokens", None) or (diag.get("cache_missed_tokens") if isinstance(diag, dict) else None)
                if missed is not None:
                    self.span.set_attribute("gen_ai.prompt_cache.missed_tokens", int(missed))

            self.span.set_status(Status(StatusCode.OK))
        except Exception as exc:
            logger.debug("Failed to record LLM response attributes: %s", exc)

    def record_mock(self, mock_reason: str = "Client unavailable or mock mode enabled") -> None:
        self.span.set_attribute("eval.is_mock", True)
        self.span.set_attribute("eval.mock_reason", mock_reason)
        self.span.set_status(Status(StatusCode.OK))

    def record_error(self, exc: Exception) -> None:
        self.span.record_exception(exc)
        self.span.set_status(Status(StatusCode.ERROR, str(exc)))


class LLMTraceContext(tuple):
    def __new__(cls, span: trace.Span, recorder: LLMTraceRecorder) -> "LLMTraceContext":
        return super().__new__(cls, (span, recorder))

    def __enter__(self) -> LLMTraceRecorder:
        return self[1]

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_val is not None and isinstance(exc_val, Exception):
            self[1].record_error(exc_val)
        self[0].end()
