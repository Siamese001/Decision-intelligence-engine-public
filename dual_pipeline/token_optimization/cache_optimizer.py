"""
dual_pipeline/token_optimization/cache_optimizer.py

Prompt Cache Optimization Engine for OpenAI gpt-5.6-luna.
Ensures static system prompt prefixes (>= 1,024 tokens) are strictly isolated
and stabilized across runs to maximize prompt cache hit rates.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.prompt_cache import PROVIDER_MIN_PREFIX_TOKENS, PromptPrefixQuarantine

from .profiler import TokenProfiler
from .schemas import DEFAULT_LUNA_PRICING, AstraModelPricing, PromptComponents

logger = logging.getLogger("dual_pipeline.token_optimization.cache_optimizer")

# Standard minimum token threshold required for OpenAI automatic prompt caching
OPENAI_MIN_CACHE_PREFIX_TOKENS = PROVIDER_MIN_PREFIX_TOKENS["openai"]


class PromptCacheOptimizer:
    """
    Analyzes, stabilizes, and structures prompts to maximize OpenAI Prompt Caching hits.
    """

    def __init__(
        self,
        profiler: Optional[TokenProfiler] = None,
        pricing: AstraModelPricing = DEFAULT_LUNA_PRICING,
    ) -> None:
        self.profiler = profiler or TokenProfiler()
        self.pricing = pricing
        self.quarantine = PromptPrefixQuarantine(self.profiler)

    def compute_prefix_hash(self, text: str) -> str:
        """Computes SHA-256 digest of normalized static prefix."""
        return self.quarantine.compute_prefix_hash(text)

    def stabilize_system_prefix(
        self,
        system_prompt: str,
        min_prefix_tokens: int = OPENAI_MIN_CACHE_PREFIX_TOKENS,
    ) -> Tuple[str, str, int, bool]:
        """
        Splits system prompt into an immutable static prefix and dynamic elements.
        Returns: (static_prefix, dynamic_tail, prefix_token_count, is_cacheable)
        """
        static_prefix, dynamic_parts = self.quarantine.sanitize_prefix(system_prompt)
        dynamic_tail = "\n\n".join(dynamic_parts)
        prefix_tokens = self.profiler.count_tokens(static_prefix)
        is_cacheable = prefix_tokens >= min_prefix_tokens
        return static_prefix, dynamic_tail, prefix_tokens, is_cacheable

    def build_cacheable_messages(
        self,
        system_prompt: str,
        user_content: str,
        model: Optional[str] = None,
    ) -> Tuple[List[Dict[str, str]], PromptComponents]:
        """
        Builds standardized OpenAI messages optimizing prompt cache alignment.
        """
        messages, components, _ = self.quarantine.build_quarantined_messages(
            system_prompt=system_prompt,
            user_content=user_content,
            min_prefix_tokens=OPENAI_MIN_CACHE_PREFIX_TOKENS,
        )
        return messages, components

    def estimate_cache_savings(
        self,
        total_input_tokens: int,
        cached_tokens: int,
        pricing: Optional[AstraModelPricing] = None,
    ) -> Dict[str, Any]:
        """
        Calculates exact dollar and percentage savings achieved via prompt caching.
        """
        p = pricing or self.pricing
        uncached = max(0, total_input_tokens - cached_tokens)

        cost_without_caching = (total_input_tokens / 1_000_000.0) * p.uncached_input_usd_per_million
        cost_with_caching = (
            (uncached / 1_000_000.0) * p.uncached_input_usd_per_million
            + (cached_tokens / 1_000_000.0) * p.cached_input_usd_per_million
        )

        saved_usd = max(0.0, cost_without_caching - cost_with_caching)
        savings_ratio = (saved_usd / cost_without_caching) if cost_without_caching > 0 else 0.0

        return {
            "total_input_tokens": total_input_tokens,
            "cached_tokens": cached_tokens,
            "uncached_tokens": uncached,
            "cache_hit_ratio": round(cached_tokens / max(1, total_input_tokens), 4),
            "cost_without_caching_usd": round(cost_without_caching, 6),
            "cost_with_caching_usd": round(cost_with_caching, 6),
            "saved_usd": round(saved_usd, 6),
            "savings_ratio": round(savings_ratio, 4),
        }
