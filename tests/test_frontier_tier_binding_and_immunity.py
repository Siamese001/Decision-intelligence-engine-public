"""
tests/test_frontier_tier_binding_and_immunity.py

Regression test suite validating Wave 2:
1. Frontier and Dialectical Judge tiers bound to high-speed reasoning model.
2. Reasoning effort max safely normalized to xhigh.
3. Parameter immunity strips temperature and top_p for reasoning tier models.
"""
from __future__ import annotations

from config.credentials import _guard_client_completions, sanitize_model_call_kwargs
from dual_pipeline.model_registry import ModelTier, resolve_tier_model, resolve_tier_model_name


def test_frontier_tier_binding():
    """Verify FRONTIER_HIGH_REASONING and DIALECTICAL_JUDGE resolve to high-speed reasoning model."""
    model_name = resolve_tier_model_name(ModelTier.FRONTIER_HIGH_REASONING)
    assert "gpt-5.6-sol" in model_name

    judge_name, judge_effort = resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
    assert "gpt-5.6-sol" in judge_name


def test_frontier_parameter_immunity_max_to_xhigh():
    """Verify that reasoning_effort='max' is safely translated to 'xhigh' for OpenAI API compatibility."""
    kwargs = {
        "temperature": 0.7,
        "top_p": 0.95,
        "reasoning_effort": "max",
    }
    sanitized = sanitize_model_call_kwargs("gpt-5.6-sol", kwargs)

    assert "temperature" not in sanitized
    assert "top_p" not in sanitized
    assert sanitized.get("reasoning_effort") == "xhigh"


def test_frontier_parameter_immunity_preserves_other_kwargs():
    """Verify that non-forbidden kwargs like max_tokens and messages are preserved."""
    kwargs = {
        "temperature": 0.2,
        "max_completion_tokens": 8000,
        "reasoning_effort": "high",
        "stream": False,
    }
    sanitized = sanitize_model_call_kwargs("gpt-5.6-sol", kwargs)

    assert "temperature" not in sanitized
    assert sanitized["max_completion_tokens"] == 8000
    assert sanitized["reasoning_effort"] == "high"
    assert sanitized["stream"] is False


def test_client_completions_guard_interception():
    """Verify that _guard_client_completions wraps client.chat.completions.create seamlessly."""
    class FakeCompletions:
        def __init__(self):
            self.last_kwargs = None

        def create(self, **kwargs):
            self.last_kwargs = kwargs
            return {"status": "ok"}

    class FakeChat:
        def __init__(self):
            self.completions = FakeCompletions()

    class FakeClient:
        def __init__(self):
            self.chat = FakeChat()

    client = FakeClient()
    _guard_client_completions(client, "gpt-5.6-sol")

    # Call wrapped method with reasoning_effort='max'
    res = client.chat.completions.create(
        model="gpt-5.6-sol",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.5,
        reasoning_effort="max",
    )

    assert res == {"status": "ok"}
    recorded = client.chat.completions.last_kwargs
    assert recorded["reasoning_effort"] == "xhigh"
    assert "temperature" not in recorded
