#!/usr/bin/env python3
"""
tests/property/test_model_policy_properties.py

Hypothesis Property-Based Testing Suite for Model Policy Invariants.
Validates:
1. Arbitrary strings rejected by validate_dispatch_model unless explicitly approved.
2. Strict cross-provider isolation (OpenAI vs Google vs Local).
3. Universal rejection of BANNED_REGISTRY_MODELS across all providers.
4. Determinism and idempotence of compute_policy_digest.
5. Structural validity of all fallback tiers and default models.
6. Parameter immunity and reasoning kwargs sanitization invariants.

Governed under Finding X-32 and Wave 2 Model Identity SSOT Invariants.
"""
from __future__ import annotations

import string
from typing import Any, Dict
import hypothesis.strategies as st
from hypothesis import given, settings

from config.credentials import (
    GOOGLE_APPROVED_MODELS,
    OPENAI_APPROVED_MODELS,
    REASONING_MODEL_PREFIXES,
    canonicalize_model_name,
    sanitize_model_call_kwargs,
)
from dual_pipeline.model_registry import (
    BANNED_REGISTRY_MODELS,
    FALLBACK_TIERS,
    compute_policy_digest,
    get_approved_models,
    is_model_approved,
    validate_dispatch_model,
)

# Strategy for arbitrary candidate strings that might be supplied to model dispatch
arbitrary_model_names = st.text(
    alphabet=string.ascii_letters + string.digits + "-_./ \t\n",
    min_size=0,
    max_size=200,
)

providers = st.sampled_from(["openai", "google", "local"])


class TestModelPolicyProperties:
    """Hypothesis property tests asserting model policy mathematical and invariant bounds."""

    @settings(max_examples=60, deadline=None)
    @given(cand=arbitrary_model_names, provider=providers)
    def test_arbitrary_strings_fail_closed_unless_approved(self, cand: str, provider: str) -> None:
        """Any arbitrary string must be rejected unless present in the provider's approved set."""
        approved = get_approved_models(provider)
        clean = canonicalize_model_name(cand.strip()) if cand else ""

        if not cand or not cand.strip() or clean not in approved or clean in BANNED_REGISTRY_MODELS:
            try:
                validate_dispatch_model(cand, provider)
                raise AssertionError(f"Expected validate_dispatch_model to reject '{cand}' for provider '{provider}'")
            except ValueError:
                pass  # Correctly failed closed
        else:
            # If it happened to match an approved model after canonicalization, it must return canonical
            res = validate_dispatch_model(cand, provider)
            assert res == clean

    @settings(max_examples=40, deadline=None)
    @given(model=st.sampled_from(sorted(OPENAI_APPROVED_MODELS)))
    def test_openai_models_rejected_for_google_provider(self, model: str) -> None:
        """OpenAI models must fail closed if dispatched to Google provider."""
        google_approved = get_approved_models("google")
        if model not in google_approved:
            try:
                validate_dispatch_model(model, "google")
                raise AssertionError(f"Expected cross-provider dispatch of '{model}' to Google to fail")
            except ValueError:
                pass

    @settings(max_examples=40, deadline=None)
    @given(model=st.sampled_from(sorted(GOOGLE_APPROVED_MODELS)))
    def test_google_models_rejected_for_openai_provider(self, model: str) -> None:
        """Google models must fail closed if dispatched to OpenAI provider."""
        openai_approved = get_approved_models("openai")
        if model not in openai_approved:
            try:
                validate_dispatch_model(model, "openai")
                raise AssertionError(f"Expected cross-provider dispatch of '{model}' to OpenAI to fail")
            except ValueError:
                pass

    @settings(max_examples=40, deadline=None)
    @given(banned=st.sampled_from(sorted(BANNED_REGISTRY_MODELS)), provider=providers)
    def test_banned_models_universally_rejected(self, banned: str, provider: str) -> None:
        """Every model in BANNED_REGISTRY_MODELS must be rejected across every provider."""
        assert not is_model_approved(banned, provider)
        assert not is_model_approved(banned, None)
        try:
            validate_dispatch_model(banned, provider)
            raise AssertionError(f"Expected banned model '{banned}' to be rejected for provider '{provider}'")
        except ValueError as exc:
            assert "strictly banned" in str(exc)

    @settings(max_examples=20, deadline=None)
    @given(st.integers(min_value=1, max_value=10))
    def test_policy_digest_determinism_and_immutability(self, _: int) -> None:
        """compute_policy_digest must produce an identical RFC-8259 64-char hex SHA-256."""
        digest1 = compute_policy_digest()
        digest2 = compute_policy_digest()
        assert len(digest1) == 64
        assert digest1 == digest2
        assert all(c in "0123456789abcdef" for c in digest1)

    def test_all_fallback_tiers_reference_approved_models(self) -> None:
        """Every enabled tier definition in FALLBACK_TIERS must specify approved defaults and fallbacks."""
        for tier_name, tier_info in FALLBACK_TIERS.items():
            if tier_info.get("enabled") is False or tier_info.get("provider") == "blocked":
                continue
            provider = tier_info.get("provider", "openai")
            approved = get_approved_models(provider)
            default_m = tier_info.get("default_model")
            assert default_m in approved, (
                f"Tier '{tier_name}' default_model '{default_m}' is not approved for provider '{provider}'"
            )
            for fb in tier_info.get("allowed_fallbacks", []):
                # Local tiers may include non-model algorithmic strategies (e.g. pause_clustering)
                if fb == "pause_clustering":
                    continue
                assert fb in approved, (
                    f"Tier '{tier_name}' fallback '{fb}' is not approved for provider '{provider}'"
                )

    @settings(max_examples=40, deadline=None)
    @given(
        model=st.sampled_from(sorted(OPENAI_APPROVED_MODELS)),
        temp=st.floats(min_value=0.0, max_value=2.0),
        top_p=st.floats(min_value=0.0, max_value=1.0),
        max_tokens=st.integers(min_value=100, max_value=16000),
    )
    def test_parameter_immunity_property(self, model: str, temp: float, top_p: float, max_tokens: int) -> None:
        """For all reasoning models, temperature and top_p must be stripped and max_tokens converted."""
        raw_kwargs: Dict[str, Any] = {
            "model": model,
            "temperature": temp,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "reasoning_effort": "max",
        }
        sanitized = sanitize_model_call_kwargs(model, raw_kwargs)

        m_lower = model.lower()
        is_reasoning = any(m_lower.startswith(pfx) for pfx in REASONING_MODEL_PREFIXES)

        if is_reasoning:
            assert "temperature" not in sanitized
            assert "top_p" not in sanitized
            assert "max_tokens" not in sanitized
            assert sanitized.get("max_completion_tokens") == max_tokens
            assert sanitized.get("reasoning_effort") in ("low", "medium", "high", "xhigh")
