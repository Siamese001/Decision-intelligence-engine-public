"""tests/test_tokenomics_model_cascade.py

Unit tests for Wave 2 model pinning right-sizing, FrugalGPT confidence cascades,
dynamic thinking budgets, and multi-provider Batch API 50% discount formatting.
"""
from __future__ import annotations

from dual_pipeline.batch_core.adapter import (
    calculate_batch_cost_savings,
    create_anthropic_batch_request,
    create_batch_request,
    create_gemini_batch_request,
)
from dual_pipeline.model_registry import ModelTier, get_binding, reload_registry
from dual_pipeline.query_router_core.complexity_classifier import resolve_frugal_cascade_plan
from dual_pipeline.token_optimization.prompt_prefix_aligner import PromptPrefixAligner


def test_surgical_healer_and_fast_routing_right_sized() -> None:
    """Verify SURGICAL_HEALER is right-sized to gpt-6-luna (medium) and ANTIGRAVITY_AGENT_FLASH includes gemini-3.5-flash-lite."""
    reload_registry()
    healer_binding = get_binding(ModelTier.SURGICAL_HEALER)
    assert healer_binding.model_id == "gpt-6-luna"
    assert healer_binding.reasoning_effort == "medium"

    fast_binding = get_binding(ModelTier.FAST_ROUTING)
    assert fast_binding.model_id == "gpt-6-luna"
    google_binding = get_binding(ModelTier.ANTIGRAVITY_AGENT_FLASH)
    assert "gemini-3.5-flash-lite" in google_binding.allowed_fallbacks


def test_frugal_cascade_plan_low_vs_high_complexity() -> None:
    """Verify FrugalGPT cascade routes low/medium tasks to luna/flash-lite and escalates high-risk or low-margin tasks."""
    low_plan = resolve_frugal_cascade_plan(
        "Normalize speaker labels and strip timestamps.",
        metadata={"task_type": "asr_cleanup", "confidence_margin": 0.92},
    )
    assert low_plan.requires_immediate_frontier is False
    assert low_plan.initial_model == "gpt-6-luna"
    assert low_plan.initial_effort == "low"
    assert low_plan.escalation_model == "gpt-6.1-sol"

    google_plan = resolve_frugal_cascade_plan(
        "Extract entities from short paragraph.",
        metadata={"preferred_provider": "google", "confidence_margin": 0.90},
    )
    assert google_plan.requires_immediate_frontier is False
    assert google_plan.initial_model == "gemini-3.5-flash-lite"

    high_plan = resolve_frugal_cascade_plan(
        "Evaluate $5 million SOW indemnity breach and liquidated damages conflict.",
        metadata={"confidence_margin": 0.65},
    )
    assert high_plan.requires_immediate_frontier is True
    assert high_plan.initial_model == "gpt-6.1-sol"


def test_dynamic_reasoning_and_gemini_thinking_budget() -> None:
    """Verify stage-scoped dynamic reasoning budgets for surgical_healer, decomposed_judge, and Gemini thinkingConfig."""
    healer_budget = PromptPrefixAligner.calculate_reasoning_token_budget("broken json span", stage="surgical_healer")
    assert healer_budget["budget_tokens"] == 1024
    assert healer_budget["reasoning_effort"] == "low"

    judge_budget = PromptPrefixAligner.calculate_reasoning_token_budget("word " * 500, stage="decomposed_judge")
    assert judge_budget["budget_tokens"] == 2048
    assert judge_budget["reasoning_effort"] == "medium"

    gemini_cfg = PromptPrefixAligner.resolve_gemini_thinking_config(judge_budget)
    assert gemini_cfg == {"thinkingLevel": "MEDIUM", "thinkingBudget": 2048}


def test_multi_provider_batch_requests_and_50pct_discount() -> None:
    """Verify OpenAI, Anthropic, and Gemini batch request builders and 50% Batch API discount calculation."""
    oai = create_batch_request("req-1", {"model": "gpt-6-luna", "messages": []})
    assert oai["url"] == "/v1/chat/completions"

    anth = create_anthropic_batch_request("req-2", {"model": "claude-3-7-sonnet", "max_tokens": 1024, "messages": []})
    assert anth["custom_id"] == "req-2"
    assert "params" in anth

    gem = create_gemini_batch_request("req-3", {"contents": []}, model="gemini-3.5-flash-lite")
    assert gem["model"] == "models/gemini-3.5-flash-lite"

    savings = calculate_batch_cost_savings("gpt-6.1-sol", uncached_input_tokens=100_000, output_tokens=10_000)
    assert savings["discount_pct"] == 50.0
    assert savings["batch_cost_usd"] == round(savings["sync_cost_usd"] * 0.5, 6)
