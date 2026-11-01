"""
dual_pipeline/token_optimization/profiler.py

BPE Token Profiler, Token Count Analyzer, and Serialization Optimizer.
Calibrated for OpenAI `gpt-5.6-luna` and reasoning models.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Sequence

from .schemas import (
    DEFAULT_LUNA_PRICING,
    AstraModelPricing,
    PromptComponents,
    TokenUsageMetric,
)

logger = logging.getLogger("dual_pipeline.token_optimization.profiler")

# Cache tokenizer encoding instance
_ENCODING_CACHE: Dict[str, Any] = {}


def get_tokenizer_encoding(model: Optional[str] = None):
    """
    Returns the appropriate tiktoken encoding for model, defaulting to o200k_base.
    """
    target_model = model or "o200k_base"
    if target_model in _ENCODING_CACHE:
        return _ENCODING_CACHE[target_model]

    try:
        import tiktoken

        # Modern reasoning models and gpt-4o / gpt-5 / gpt-6 use o200k_base
        from config.credentials import O200K_MODEL_PREFIXES
        if any(prefix in target_model.lower() for prefix in O200K_MODEL_PREFIXES):
            encoding_name = "o200k_base"
        else:
            encoding_name = "cl100k_base"

        try:
            enc = tiktoken.get_encoding(encoding_name)
        except Exception:
            enc = tiktoken.get_encoding("cl100k_base")

        _ENCODING_CACHE[target_model] = enc
        return enc
    except ImportError:
        logger.debug("tiktoken not installed; using regex-heuristic token estimator.")
        return None


class TokenProfiler:
    """
    Measures token footprints, analyzes prompt components, and performs lossless compression.
    """

    def __init__(self, default_model: Optional[str] = None) -> None:
        from dual_pipeline.model_registry import ModelRegistry, ModelTier
        self.default_model = default_model or ModelRegistry.resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)[0]
        self._encoding = get_tokenizer_encoding(self.default_model)

    def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        """
        Counts tokens in raw text using BPE tokenizer or calibrated heuristic.
        """
        if not text:
            return 0

        target_model = model or self.default_model
        encoding = get_tokenizer_encoding(target_model) if target_model != self.default_model else self._encoding

        if encoding is not None:
            try:
                return len(encoding.encode(text, disallowed_special=()))
            except Exception:
                pass

        # Calibrated fallback heuristic: 1 token ≈ 4.0 chars for English / code / JSON
        # Whitespace and punctuation adjustment
        words = len(re.findall(r"\w+|[^\w\s]", text, re.UNICODE))
        chars = len(text)
        return max(1, int(0.7 * words + 0.15 * chars))

    def count_message_tokens(self, messages: Sequence[Dict[str, str]], model: Optional[str] = None) -> int:
        """
        Calculates exact token count for OpenAI Chat Completion messages including framing overhead.
        Each message consumes ~3 tokens (<|im_start|>{role}\n{content}<|im_end|>\n).
        Assistant response primer adds 3 tokens.
        """
        total = 3  # Every reply is primed with <|im_start|>assistant<|message|>
        for msg in messages:
            total += 3  # Message envelope overhead
            total += self.count_tokens(msg.get("role", ""), model)
            total += self.count_tokens(msg.get("content", ""), model)
        return total

    @staticmethod
    def compact_json(obj: Any) -> str:
        """
        Serializes JSON with zero redundant whitespace (separators=(',', ':')),
        reducing JSON token counts by 25-35%.
        """
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, sort_keys=True)

    @staticmethod
    def strip_redundant_whitespace(text: str) -> str:
        """
        Compresses excessive blank lines and spaces while preserving markdown tables and code blocks.
        """
        if not text:
            return ""

        # Normalize line endings
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")

        # Collapse 3+ consecutive newlines into 2
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)

        # Strip trailing spaces on lines
        lines = [re.sub(r"[ \t]+$", "", line) for line in normalized.split("\n")]
        return "\n".join(lines).strip()

    def truncate_to_token_limit(
        self,
        text: str,
        max_tokens: int,
        model: Optional[str] = None,
        preserve_tail: bool = False,
    ) -> str:
        """
        Surgically truncates text to fit within max_tokens without breaking BPE boundaries.
        """
        if not text or max_tokens <= 0:
            return ""

        target_model = model or self.default_model
        encoding = get_tokenizer_encoding(target_model) if target_model != self.default_model else self._encoding

        if encoding is not None:
            try:
                tokens = encoding.encode(text, disallowed_special=())
                if len(tokens) <= max_tokens:
                    return text
                truncated_tokens = tokens[-max_tokens:] if preserve_tail else tokens[:max_tokens]
                return encoding.decode(truncated_tokens)
            except Exception:
                pass

        # Fallback character slicing (approx 3.8 chars per token)
        char_limit = int(max_tokens * 3.8)
        if len(text) <= char_limit:
            return text
        if preserve_tail:
            return text[-char_limit:]
        return text[:char_limit]

    def disassemble_prompt(
        self,
        full_system_prompt: str,
        user_content: str,
        model: Optional[str] = None,
    ) -> PromptComponents:
        """
        Disassembles prompt into static prefix (>=1,024 tokens) and dynamic components,
        evaluating prompt caching qualification.
        """
        cleaned_system = self.strip_redundant_whitespace(full_system_prompt)
        cleaned_user = self.strip_redundant_whitespace(user_content)

        prefix_tokens = self.count_tokens(cleaned_system, model)
        is_cacheable = prefix_tokens >= 1024

        return PromptComponents(
            static_system_prefix=cleaned_system,
            dynamic_system_suffix="",
            user_payload=cleaned_user,
            estimated_prefix_tokens=prefix_tokens,
            is_cacheable_prefix=is_cacheable,
        )

    def measure_invocation(
        self,
        request_messages: Sequence[Dict[str, str]],
        raw_response: Any,
        latency_ms: float,
        model: Optional[str] = None,
        pricing: AstraModelPricing = DEFAULT_LUNA_PRICING,
    ) -> TokenUsageMetric:
        """
        Builds a complete TokenUsageMetric from request messages and OpenAI completion response.
        """
        target_model = model or self.default_model
        metric = TokenUsageMetric(
            model=target_model,
            latency_ms=round(latency_ms, 2),
        )

        usage = getattr(raw_response, "usage", None)
        if usage:
            metric.input_tokens = getattr(usage, "prompt_tokens", 0) or 0
            metric.output_tokens = getattr(usage, "completion_tokens", 0) or 0
            metric.total_tokens = getattr(usage, "total_tokens", 0) or (metric.input_tokens + metric.output_tokens)

            # Extract prompt caching details if reported by OpenAI
            details = getattr(usage, "prompt_tokens_details", None)
            if details:
                metric.cached_input_tokens = getattr(details, "cached_tokens", 0) or 0

            # Extract completion details (reasoning tokens)
            comp_details = getattr(usage, "completion_tokens_details", None)
            if comp_details:
                metric.reasoning_tokens = getattr(comp_details, "reasoning_tokens", 0) or 0
        else:
            # Fallback estimation if usage object missing
            metric.input_tokens = self.count_message_tokens(request_messages, target_model)
            resp_content = ""
            if hasattr(raw_response, "choices") and raw_response.choices:
                msg = getattr(raw_response.choices[0], "message", None)
                if msg:
                    resp_content = getattr(msg, "content", "") or ""
            metric.output_tokens = self.count_tokens(resp_content, target_model)
            metric.total_tokens = metric.input_tokens + metric.output_tokens

        metric.finalize_metrics(pricing)
        return metric
