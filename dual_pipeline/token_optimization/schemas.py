"""
dual_pipeline/token_optimization/schemas.py

Programmatic schemas and contracts for Token Optimization Engine.
Supports governed model `gpt-5.6-luna` and Unified Agent Architecture.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple

from dual_pipeline.model_registry import ModelTier, resolve_tier_model_name


class ReasoningEffortTier(str, Enum):
    """Reasoning effort tiers for reasoning models (none, minimal, low, medium, high, xhigh, max)."""
    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


@dataclass(frozen=True)
class AstraModelPricing:
    """Pricing schedule for reasoning models in USD per million tokens."""
    model_id: str = "gpt-6-luna"
    uncached_input_usd_per_million: float = 10.00
    cached_input_usd_per_million: float = 1.00
    output_reasoning_usd_per_million: float = 50.00
    cache_write_usd_per_million: Optional[float] = 12.50

    def get_cache_write_usd_per_million(self) -> float:
        if self.cache_write_usd_per_million is not None:
            return self.cache_write_usd_per_million
        return round(self.uncached_input_usd_per_million * 1.25, 6)

    def calculate_cost(
        self,
        uncached_input_tokens: int = 0,
        cached_input_tokens: int = 0,
        output_tokens: int = 0,
        cache_write_tokens: int = 0,
        is_batch: bool = False,
    ) -> float:
        """Calculates total cost in USD for token usage including cache read/write differentials and batch discounts."""
        uncached_cost = (uncached_input_tokens / 1_000_000.0) * self.uncached_input_usd_per_million
        cached_cost = (cached_input_tokens / 1_000_000.0) * self.cached_input_usd_per_million
        write_cost = (cache_write_tokens / 1_000_000.0) * self.get_cache_write_usd_per_million()
        output_cost = (output_tokens / 1_000_000.0) * self.output_reasoning_usd_per_million
        total = uncached_cost + cached_cost + write_cost + output_cost
        if is_batch:
            total *= 0.5
        return round(total, 6)


MODEL_PRICING_TABLE: Dict[str, AstraModelPricing] = {
    "gpt-6.1-sol": AstraModelPricing(
        model_id="gpt-6.1-sol",
        uncached_input_usd_per_million=2.00,
        cached_input_usd_per_million=0.10,
        cache_write_usd_per_million=2.50,
        output_reasoning_usd_per_million=10.00,
    ),
    "gpt-6-sol": AstraModelPricing(
        model_id="gpt-6-sol",
        uncached_input_usd_per_million=2.00,
        cached_input_usd_per_million=0.20,
        cache_write_usd_per_million=2.50,
        output_reasoning_usd_per_million=10.00,
    ),
    "gpt-5.6-sol": AstraModelPricing(
        model_id="gpt-5.6-sol",
        uncached_input_usd_per_million=4.00,
        cached_input_usd_per_million=0.40,
        cache_write_usd_per_million=5.00,
        output_reasoning_usd_per_million=20.00,
    ),
    "gpt-5.6-terra": AstraModelPricing(
        model_id="gpt-5.6-terra",
        uncached_input_usd_per_million=2.00,
        cached_input_usd_per_million=0.20,
        cache_write_usd_per_million=2.50,
        output_reasoning_usd_per_million=12.00,
    ),
    "gpt-5.6-luna": AstraModelPricing(
        model_id="gpt-5.6-luna",
        uncached_input_usd_per_million=0.20,
        cached_input_usd_per_million=0.02,
        cache_write_usd_per_million=0.25,
        output_reasoning_usd_per_million=1.20,
    ),
    "gpt-6-luna": AstraModelPricing(
        model_id="gpt-6-luna",
        uncached_input_usd_per_million=0.10,
        cached_input_usd_per_million=0.01,
        cache_write_usd_per_million=0.125,
        output_reasoning_usd_per_million=0.50,
    ),
    "gemini-3.8-flash": AstraModelPricing(
        model_id="gemini-3.8-flash",
        uncached_input_usd_per_million=0.10,
        cached_input_usd_per_million=0.025,
        cache_write_usd_per_million=0.125,
        output_reasoning_usd_per_million=0.40,
    ),
    "gemini-3.5-flash-lite": AstraModelPricing(
        model_id="gemini-3.5-flash-lite",
        uncached_input_usd_per_million=0.075,
        cached_input_usd_per_million=0.01875,
        cache_write_usd_per_million=0.09375,
        output_reasoning_usd_per_million=0.30,
    ),
}

DEFAULT_ASTRA_PRICING = MODEL_PRICING_TABLE["gpt-6.1-sol"]
DEFAULT_SOL_PRICING = MODEL_PRICING_TABLE["gpt-6.1-sol"]
DEFAULT_TERRA_PRICING = MODEL_PRICING_TABLE["gpt-5.6-terra"]
DEFAULT_LUNA_PRICING = MODEL_PRICING_TABLE["gpt-6-luna"]
DEFAULT_COPILOT_FAST_SNAP_PRICING = MODEL_PRICING_TABLE["gpt-6-luna"]
DEFAULT_GEMINI_PRICING = MODEL_PRICING_TABLE["gemini-3.8-flash"]

_MODEL_KEYWORD_MATCHES: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("astra", "6.1-sol", "6.1"), "gpt-6.1-sol"),
    (("6-sol",), "gpt-6-sol"),
    (("5.6-sol",), "gpt-5.6-sol"),
    (("terra",), "gpt-5.6-terra"),
    (("5.6-luna",), "gpt-5.6-luna"),
    (("6-luna", "luna", "copilot"), "gpt-6-luna"),
    (("gemini-3.8", "gemini-flash"), "gemini-3.8-flash"),
    (("gemini-3.5", "flash-lite"), "gemini-3.5-flash-lite"),
    (("flash", "pro", "gemini"), "gemini-3.8-flash"),
    (("lite",), "gemini-3.5-flash-lite"),
    (("sol",), "gpt-6.1-sol"),
)


def get_model_pricing(model: Optional[str] = None) -> AstraModelPricing:
    """Returns the pricing schedule for the given model tier or model identifier."""
    if not model:
        model = resolve_tier_model_name(ModelTier.QUERY_REASONING)

    m = str(model).strip().lower()

    if m in MODEL_PRICING_TABLE:
        return MODEL_PRICING_TABLE[m]

    try:
        from config.credentials import canonicalize_model_alias
        canonical = canonicalize_model_alias(m)
        if canonical in MODEL_PRICING_TABLE:
            return MODEL_PRICING_TABLE[canonical]
    except Exception:
        pass

    for keywords, model_key in _MODEL_KEYWORD_MATCHES:
        if any(kw in m for kw in keywords):
            return MODEL_PRICING_TABLE[model_key]

    return DEFAULT_SOL_PRICING


@dataclass
class TokenUsageMetric:
    """Detailed token usage measurement for an LLM invocation."""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    uncached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    model: str = field(default_factory=lambda: resolve_tier_model_name(ModelTier.FRONTIER_HIGH_REASONING))
    cache_hit_rate: float = 0.0

    def finalize_metrics(self, pricing: Optional[AstraModelPricing] = None) -> None:
        """Calculates derived metrics (uncached tokens, cache hit rate, cost)."""
        eff_pricing = pricing or get_model_pricing(self.model)
        if self.uncached_input_tokens == 0 and self.input_tokens > 0:
            self.uncached_input_tokens = max(0, self.input_tokens - self.cached_input_tokens - self.cache_write_tokens)
        if self.input_tokens > 0:
            self.cache_hit_rate = round(self.cached_input_tokens / self.input_tokens, 4)
        if self.total_tokens == 0:
            self.total_tokens = self.input_tokens + self.output_tokens
        self.cost_usd = eff_pricing.calculate_cost(
            uncached_input_tokens=self.uncached_input_tokens,
            cached_input_tokens=self.cached_input_tokens,
            output_tokens=self.output_tokens,
            cache_write_tokens=self.cache_write_tokens,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TokenBudget:
    """
    SRE Catastrophic Circuit Breaker and Token Budget Governor.

    Operates under corporate enterprise OpenAI keys not as an economic micro-manager,
    but as a structural fail-closed circuit breaker protecting against infinite
    recursive reasoning loops, indirect prompt injections, and organization-wide
    TPM/RPM quota starvation.
    """
    max_input_tokens: int = 500_000
    max_output_tokens: int = 100_000
    max_total_tokens: int = 600_000
    max_cost_usd: float = 15.00
    enforce_fail_closed: bool = True

    def check_exceeded(self, usage: TokenUsageMetric) -> tuple[bool, Optional[str]]:
        """Verifies whether observed usage exceeds declared budget."""
        if usage.input_tokens > self.max_input_tokens:
            return True, f"INPUT_TOKEN_LIMIT_EXCEEDED: {usage.input_tokens} > {self.max_input_tokens}"
        if usage.output_tokens > self.max_output_tokens:
            return True, f"OUTPUT_TOKEN_LIMIT_EXCEEDED: {usage.output_tokens} > {self.max_output_tokens}"
        if usage.total_tokens > self.max_total_tokens:
            return True, f"TOTAL_TOKEN_LIMIT_EXCEEDED: {usage.total_tokens} > {self.max_total_tokens}"
        if usage.cost_usd > self.max_cost_usd:
            return True, f"COST_LIMIT_EXCEEDED: ${usage.cost_usd:.4f} > ${self.max_cost_usd:.4f}"
        return False, None


@dataclass
class PromptComponents:
    """Disassembled prompt components separating cacheable prefix from dynamic payload."""
    static_system_prefix: str = ""
    dynamic_system_suffix: str = ""
    user_payload: str = ""
    schema_payload: Optional[str] = None
    estimated_prefix_tokens: int = 0
    is_cacheable_prefix: bool = False

    def build_messages(self) -> List[Dict[str, str]]:
        """Constructs standardized OpenAI chat messages maximizing prompt cache efficiency."""
        messages: List[Dict[str, str]] = []
        static_system = self.static_system_prefix.strip()
        if static_system:
            messages.append({"role": "system", "content": static_system})

        user_parts: List[str] = []
        if self.dynamic_system_suffix.strip():
            user_parts.append(self.dynamic_system_suffix.strip())
        if self.user_payload.strip():
            user_parts.append(self.user_payload.strip())

        if user_parts:
            messages.append({"role": "user", "content": "\n\n".join(user_parts)})

        return messages


@dataclass
class OptimizationDirective:
    """Directives produced by TokenOptimizationAgent for request execution."""
    recommended_reasoning_effort: ReasoningEffortTier
    static_system_prefix: str
    dynamic_user_content: str
    target_max_completion_tokens: int
    enable_compact_json: bool = True
    estimated_baseline_tokens: int = 0
    estimated_optimized_tokens: int = 0
    projected_savings_ratio: float = 0.0
    rationale: str = ""


_EFFORT_STEP_ORDER: Tuple[ReasoningEffortTier, ...] = (
    ReasoningEffortTier.LOW,
    ReasoningEffortTier.MEDIUM,
    ReasoningEffortTier.HIGH,
    ReasoningEffortTier.XHIGH,
)


def step_down_effort_tier(
    tier: ReasoningEffortTier,
    floor: ReasoningEffortTier = ReasoningEffortTier.LOW,
) -> ReasoningEffortTier:
    """Steps down reasoning effort by one tier, bounded below by floor."""
    if tier not in _EFFORT_STEP_ORDER or floor not in _EFFORT_STEP_ORDER:
        return floor
    idx = _EFFORT_STEP_ORDER.index(tier)
    floor_idx = _EFFORT_STEP_ORDER.index(floor)
    return _EFFORT_STEP_ORDER[max(floor_idx, idx - 1)]


def _max_tier(a: Optional[ReasoningEffortTier], b: ReasoningEffortTier) -> ReasoningEffortTier:
    if a in _EFFORT_STEP_ORDER and b in _EFFORT_STEP_ORDER:
        return a if _EFFORT_STEP_ORDER.index(a) > _EFFORT_STEP_ORDER.index(b) else b
    return b


def evaluate_effort_hysteresis(
    current_tier: Optional[ReasoningEffortTier],
    consecutive_clean: int,
    loop_result: Mapping[str, Any],
    base_tier: ReasoningEffortTier,
    escalated_tier: ReasoningEffortTier,
    required_clean_runs: int = 3,
) -> Tuple[ReasoningEffortTier, int, bool]:
    """Evaluates bidirectional reasoning effort transitions with N=3 clean-run hysteresis."""
    outcome = str(loop_result.get("terminal_outcome", "")).upper()
    steps = int(loop_result.get("steps_executed", 0))
    margin = float(loop_result.get("margin_delta", loop_result.get("confidence_margin", 0.30)))

    if outcome in ("STAGNATED", "BUDGET_EXHAUSTED", "FAILED"):
        return _max_tier(current_tier, escalated_tier), 0, True
    if steps >= 3:
        return _max_tier(current_tier, escalated_tier), 0, False
    from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold

    if outcome == "COMPLETED" and steps == 1 and margin > get_hitl_ambiguity_margin_threshold():
        active = current_tier or base_tier
        if active == base_tier:
            return base_tier, 0, False
        new_clean = consecutive_clean + 1
        if new_clean >= required_clean_runs:
            return step_down_effort_tier(active, floor=base_tier), 0, False
        return active, new_clean, False

    return (current_tier or base_tier), 0, False
