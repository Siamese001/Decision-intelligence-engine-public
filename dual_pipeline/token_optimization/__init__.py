"""
dual_pipeline/token_optimization

Public facade for Token Optimization Agent, BPE Profiler, Cache Optimizer, and Astra Cost Engine.
"""
from __future__ import annotations

import sys
import dual_pipeline.prompt_cache as _prompt_cache_mod
from dual_pipeline.prompt_cache import PromptPrefixQuarantine
from .agent import TokenOptimizationAgent  # noqa: lazy-decouple
from .cache_optimizer import OPENAI_MIN_CACHE_PREFIX_TOKENS, PromptCacheOptimizer
from .pre_gate_screener import PreGateResult, PreGateValidationError, screen_pre_gate, validate_or_raise  # noqa: lazy-decouple
from .profiler import TokenProfiler, get_tokenizer_encoding

sys.modules[f"{__name__}.prompt_cache"] = _prompt_cache_mod
from .schemas import (
    DEFAULT_ASTRA_PRICING,
    DEFAULT_LUNA_PRICING,
    AstraModelPricing,
    OptimizationDirective,
    PromptComponents,
    ReasoningEffortTier,
    TokenBudget,
    TokenUsageMetric,
    get_model_pricing,
)

__all__ = [
    "TokenOptimizationAgent",
    "PromptCacheOptimizer",
    "PromptPrefixQuarantine",
    "OPENAI_MIN_CACHE_PREFIX_TOKENS",
    "TokenProfiler",
    "get_tokenizer_encoding",
    "AstraModelPricing",
    "DEFAULT_ASTRA_PRICING",
    "DEFAULT_LUNA_PRICING",
    "get_model_pricing",
    "OptimizationDirective",
    "PromptComponents",
    "ReasoningEffortTier",
    "TokenBudget",
    "TokenUsageMetric",
    "PreGateResult",
    "PreGateValidationError",
    "screen_pre_gate",
    "validate_or_raise",
]

