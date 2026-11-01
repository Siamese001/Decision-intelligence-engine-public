#!/usr/bin/env python3
"""
tests/test_unapproved_model_prevention.py

Comprehensive test suite verifying that unapproved models (e.g. gpt-4.1-mini,
gpt-4o-mini, o1, o3-mini, gpt-6-luna) are strictly prevented from being used
across all layers of the pipeline, and only approved models (gpt-5.6-sol,
gpt-5.6-terra, gpt-5.6-luna) are authorized for OpenAI API dispatch.
"""
from __future__ import annotations

import os
from unittest import mock

import pytest

from config.credentials import (
    OPENAI_APPROVED_MODELS,
    _guard_client_completions,
    resolve_openai_credentials,
)
from dual_pipeline.model_registry import (
    BANNED_REGISTRY_MODELS,
    ModelTier,
    prepare_call_kwargs,
    prepare_copilot_call_kwargs,
    resolve_copilot_tier_model,
    resolve_tier_model,
)


class TestApprovedModelsSSOT:
    """Verifies that the SSOT allowlist contains exactly the approved models."""

    def test_ssot_allowlist_contents(self) -> None:
        """Only models from model_registry.json approved_models.openai are approved for OpenAI."""
        assert {"gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"} == OPENAI_APPROVED_MODELS

    def test_banned_models_presence(self) -> None:
        """Unapproved models must be explicitly included in banned registry models."""
        unapproved = [
            "gpt-4.1-mini",
            "gpt-4.1",
            "gpt-4o-mini",
            "gpt-4o",
            "gpt-4",
            "o1",
            "o1-mini",
            "o1-preview",
            "o3",
            "o3-mini",
        ]
        for m in unapproved:
            assert m in BANNED_REGISTRY_MODELS, f"Expected {m} to be in BANNED_REGISTRY_MODELS"
            assert m not in OPENAI_APPROVED_MODELS, f"Expected {m} NOT to be in OPENAI_APPROVED_MODELS"

    def test_approved_models_not_in_banned_set(self) -> None:
        """Approved models must never be present in the banned registry set."""
        for m in OPENAI_APPROVED_MODELS:
            assert m not in BANNED_REGISTRY_MODELS, f"Approved model {m} is erroneously in BANNED_REGISTRY_MODELS"


class TestTierResolutionPrevention:
    """Verifies that resolve_tier_model and resolve_copilot_tier_model reject unapproved models."""

    @pytest.mark.parametrize("unapproved_model", ["gpt-4.1-mini", "gpt-4o-mini", "o1", "o3-mini"])
    def test_resolve_tier_model_rejects_unapproved(self, unapproved_model: str) -> None:
        """Environment override with unapproved model falls back to default gpt-6.1-sol."""
        with mock.patch.dict(os.environ, {"OPENAI_MODEL": unapproved_model}):
            model_id, effort = resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
            assert model_id == "gpt-6.1-sol"
            assert effort == "max"

    @pytest.mark.parametrize("approved_model", ["gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"])
    def test_resolve_tier_model_accepts_approved(self, approved_model: str) -> None:
        """Environment override with approved model is accepted without fallback."""
        with mock.patch.dict(os.environ, {"OPENAI_MODEL": approved_model}):
            model_id, effort = resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
            assert model_id == approved_model

    @pytest.mark.parametrize("unapproved_model", ["gpt-4.1-mini", "gpt-4o-mini", "o1", "o3-mini"])
    def test_resolve_copilot_tier_model_rejects_unapproved(self, unapproved_model: str) -> None:
        """Copilot environment override with unapproved model falls back to default gpt-6-luna."""
        with mock.patch.dict(os.environ, {"COPILOT_MODEL": unapproved_model}):
            model_id, effort = resolve_copilot_tier_model(ModelTier.COPILOT_FAST_SNAP)
            assert model_id == "gpt-6-luna"
            assert effort == "medium"

    @pytest.mark.parametrize("approved_model", ["gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"])
    def test_resolve_copilot_tier_model_accepts_approved(self, approved_model: str) -> None:
        """Copilot environment override with approved model is accepted directly."""
        with mock.patch.dict(os.environ, {"COPILOT_MODEL": approved_model}):
            model_id, effort = resolve_copilot_tier_model(ModelTier.COPILOT_FAST_SNAP)
            assert model_id == approved_model


class TestCallKwargsSanitizationPrevention:
    """Verifies prepare_call_kwargs and prepare_copilot_call_kwargs sanitize unapproved models."""

    def test_prepare_call_kwargs_remaps_unapproved(self) -> None:
        raw_kwargs = {
            "model": "gpt-4.1-mini",
            "messages": [{"role": "user", "content": "Test"}],
        }
        sanitized = prepare_call_kwargs(ModelTier.FRONTIER_HIGH_REASONING, raw_kwargs)
        assert sanitized["model"] == "gpt-6.1-sol"
        assert sanitized["reasoning_effort"] == "xhigh"

    def test_prepare_call_kwargs_preserves_approved(self) -> None:
        raw_kwargs = {
            "model": "gpt-5.6-terra",
            "messages": [{"role": "user", "content": "Test"}],
        }
        sanitized = prepare_call_kwargs(ModelTier.FRONTIER_HIGH_REASONING, raw_kwargs)
        assert sanitized["model"] == "gpt-5.6-terra"

    def test_prepare_copilot_call_kwargs_remaps_unapproved(self) -> None:
        raw_kwargs = {
            "model": "gpt-4.1-mini",
            "messages": [{"role": "user", "content": "Test"}],
        }
        sanitized = prepare_copilot_call_kwargs(ModelTier.COPILOT_FAST_SNAP, raw_kwargs)
        assert sanitized["model"] == "gpt-6-luna"
        assert sanitized["reasoning_effort"] == "medium"


class TestCredentialsResolutionAndEgressGuard:
    """Verifies resolve_openai_credentials and the client completion interception guard."""

    def test_resolve_openai_credentials_remaps_unapproved_model_argument(self) -> None:
        with pytest.raises(ValueError, match="not an approved OpenAI model") as exc_info:
            resolve_openai_credentials(allow_mock=True, model="gpt-4.1-mini")
        assert "gpt-4.1-mini" in str(exc_info.value)
        assert "not an approved OpenAI model" in str(exc_info.value)

    def test_resolve_openai_credentials_remaps_unapproved_env_model(self) -> None:
        with mock.patch.dict(os.environ, {"OPENAI_MODEL": "gpt-4.1-mini"}):
            with pytest.raises(ValueError, match="not an approved OpenAI model") as exc_info:
                resolve_openai_credentials(allow_mock=True)
            assert "gpt-4.1-mini" in str(exc_info.value)
            assert "not an approved OpenAI model" in str(exc_info.value)

    def test_resolve_openai_credentials_accepts_approved(self) -> None:
        creds = resolve_openai_credentials(allow_mock=True, model="gpt-5.6-terra")
        assert creds.model == "gpt-5.6-terra"

    def test_client_completions_guard_intercepts_unapproved_model(self) -> None:
        """Client egress guard intercepts unapproved model and blocks transport dispatch fail-closed."""
        captured_kwargs = {}

        class DummyCompletions:
            def create(self, **kwargs):
                nonlocal captured_kwargs
                captured_kwargs = kwargs
                return {"status": "ok"}

        class DummyChat:
            completions = DummyCompletions()

        class DummyClient:
            chat = DummyChat()

        guarded_client = _guard_client_completions(DummyClient(), default_model="gpt-5.6-sol")
        with pytest.raises(ValueError, match="not an approved OpenAI model"):
            guarded_client.chat.completions.create(model="gpt-4.1-mini", messages=[])

        assert captured_kwargs == {}

    def test_client_completions_guard_passes_approved_models(self) -> None:
        """Client egress guard allows approved models through untouched."""
        captured_kwargs = {}

        class DummyCompletions:
            def create(self, **kwargs):
                nonlocal captured_kwargs
                captured_kwargs = kwargs
                return {"status": "ok"}

        class DummyChat:
            completions = DummyCompletions()

        class DummyClient:
            chat = DummyChat()

        guarded_client = _guard_client_completions(DummyClient(), default_model="gpt-5.6-sol")

        for approved in ["gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]:
            guarded_client.chat.completions.create(model=approved, messages=[])
            assert captured_kwargs["model"] == approved
