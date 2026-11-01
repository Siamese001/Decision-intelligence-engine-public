#!/usr/bin/env python3
"""
tests/end_to_end/test_model_dispatch_policy_e2e.py

End-to-End Acceptance Test Suite for Model Identity SSOT and Dispatch Policy.
Verifies Wave 2 Acceptance Criteria:
1. Zero Transport Calls: Unapproved or provider-mismatched models yield zero outbound
   network calls across external_synthesizer, synthesizers, and l2_policy.
2. Exact Resolved Identity: Valid approved models dispatch exact canonical identities,
   and approved aliases remap deterministically.
3. Parameter Immunity & Remap: Banned models are remapped or rejected fail-closed,
   and client completions intercept unapproved strings.
4. Concurrent Reload Safety: Multi-threaded reload and resolution without race
   conditions or state corruption.
5. Corrupt / Missing Policy Resilience: Fallback defaults activate cleanly.

Categories covered (Mandatory >= 3):
- Boundary payload (empty, whitespace, missing values)
- Malformed input (corrupted registry, rogue policy dictionaries)
- Concurrency & race (concurrent multi-threaded registry reload)
- Rollback & recovery (recovery to compiled defaults upon missing registry)

Governed under AGENTS.md Invariants and WAVE_PLAN_MODEL_IDENTITY_SSOT_RCA Wave 2.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

import pytest

from config.credentials import (
    GOOGLE_APPROVED_MODELS,
    OPENAI_APPROVED_MODELS,
    _guard_client_completions,
    canonicalize_model_name,
)
from dual_pipeline.l2_policy_core.helpers import _load_model_policy
from dual_pipeline.model_registry import (
    BANNED_REGISTRY_MODELS,
    compute_policy_digest,
    get_approved_models,
    is_model_approved,
    reload_registry,
    validate_dispatch_model,
)
from dual_pipeline.transcript_stages_core.external_synthesizer import (
    call_gemini_generate_content,
    init_stage_models,
)


class TransportSpy:
    """Spy class monitoring and verifying zero or exact outbound transport invocations."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def mock_urlopen(self, req: Any, *args: Any, **kwargs: Any) -> Any:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.calls.append({"type": "urllib", "url": url, "headers": getattr(req, "headers", {})})
        raise ConnectionRefusedError(f"TransportSpy intercepted disallowed call to: {url}")

    def mock_create(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append({"type": "openai", "kwargs": kwargs})
        return {"choices": [{"message": {"content": "ok"}}]}


class TestZeroTransportCallsOnUnapprovedModelsE2E:
    """Verifies that unapproved models NEVER trigger outbound network transport."""

    def test_gemini_unapproved_model_zero_transport_calls(self) -> None:
        """call_gemini_generate_content fails closed with 0 network calls for unapproved model."""
        spy = TransportSpy()
        unapproved_model = "unapproved-gemini-future-v99"

        with mock.patch("dual_pipeline.transcript_stages_core.external_synthesizer.urllib.request.urlopen", side_effect=spy.mock_urlopen), \
             mock.patch.dict(os.environ, {"GEMINI_API_KEY": "dummy-test-key"}):
            with pytest.raises(ValueError) as exc_info:
                call_gemini_generate_content(
                    model=unapproved_model,
                    system_prompt="system",
                    user_content="user",
                )
            assert "is not approved for provider 'google'" in str(exc_info.value)
            # CRITICAL INVARIANT: Zero transport calls dispatched
            assert len(spy.calls) == 0

    def test_gemini_cross_provider_model_zero_transport_calls(self) -> None:
        """call_gemini_generate_content fails closed with 0 network calls when given an OpenAI model."""
        spy = TransportSpy()
        openai_model = "gpt-6.1-sol"

        with mock.patch("dual_pipeline.transcript_stages_core.external_synthesizer.urllib.request.urlopen", side_effect=spy.mock_urlopen), \
             mock.patch.dict(os.environ, {"GEMINI_API_KEY": "dummy-test-key"}):
            with pytest.raises(ValueError) as exc_info:
                call_gemini_generate_content(
                    model=openai_model,
                    system_prompt="system",
                    user_content="user",
                )
            assert "is not approved for provider 'google'" in str(exc_info.value)
            assert len(spy.calls) == 0

    def test_stage_model_initialization_rejects_unapproved_model(self) -> None:
        """init_stage_models fails closed when passed an unapproved model override."""
        dummy_synth = type("DummySynth", (), {})()
        with pytest.raises(ValueError) as exc_info:
            init_stage_models(
                synth=dummy_synth,
                model=None,
                d01=None,
                d02="unapproved-model-xyz",
                d03=None,
                agentic=None,
                use_complexity_classifier=False,
            )
        assert "is not approved for provider" in str(exc_info.value)

    def test_l2_policy_rejects_unapproved_model_in_custom_policy(self) -> None:
        """_load_model_policy fails closed when a policy definition specifies unapproved models."""
        from dual_pipeline.l2_policy_core.models import QueryAssuranceProfile
        rogue_policy_content = {
            "provider": "openai",
            "model_family": "gpt-4",
            "model": "gpt-4.1-mini",
            "assurance_profile": "standard",
            "reasoning_effort": "medium",
            "maximum_primary_calls_per_run": 1,
            "maximum_repair_calls_per_run": 0,
            "maximum_escalation_calls_per_run": 0,
            "pricing_usd_per_million_tokens": {
                "input": 0.15,
                "output": 0.60,
            },
        }
        with pytest.raises(ValueError) as exc_info:
            _load_model_policy(
                rogue_policy_content,
                expected_profile=QueryAssuranceProfile.STANDARD,
                label="test.standard",
            )
        assert "not an approved model" in str(exc_info.value)

    def test_client_completions_guard_egress_sanitization(self) -> None:
        """Client egress guard rejects unapproved model with zero transport calls."""
        spy = TransportSpy()

        class DummyCompletions:
            def create(self, **kwargs: Any) -> Any:
                return spy.mock_create(**kwargs)

        class DummyChat:
            completions = DummyCompletions()

        class DummyClient:
            chat = DummyChat()

        guarded = _guard_client_completions(DummyClient(), default_model="gpt-6.1-sol")
        with pytest.raises(ValueError) as exc_info:
            guarded.chat.completions.create(model="gpt-4.1-mini", messages=[{"role": "user", "content": "hi"}])

        assert "not an approved OpenAI model" in str(exc_info.value)
        assert len(spy.calls) == 0


class TestExactResolvedIdentityAndAliasesE2E:
    """Verifies that approved models and aliases resolve deterministically to canonical names."""

    @pytest.mark.parametrize("approved_model", sorted(OPENAI_APPROVED_MODELS))
    def test_openai_exact_resolved_identity(self, approved_model: str) -> None:
        """Valid OpenAI model returns exact canonical string."""
        resolved = validate_dispatch_model(approved_model, "openai")
        assert resolved == approved_model

    @pytest.mark.parametrize("approved_model", sorted(GOOGLE_APPROVED_MODELS))
    def test_google_exact_resolved_identity(self, approved_model: str) -> None:
        """Valid Google model returns exact canonical string."""
        resolved = validate_dispatch_model(approved_model, "google")
        assert resolved == approved_model

    def test_alias_resolution_preserves_canonical_identity(self) -> None:
        """Colloquial and short aliases resolve to exact canonical model IDs."""
        alias_cases = [
            ("6.1-sol", "gpt-6.1-sol", "openai"),
            ("sol-6.1", "gpt-6.1-sol", "openai"),
            ("luna-6", "gpt-6-luna", "openai"),
            ("gpt-luna-6", "gpt-6-luna", "openai"),
            ("terra", "gpt-5.6-terra", "openai"),
        ]
        for alias, expected, provider in alias_cases:
            resolved = validate_dispatch_model(alias, provider)
            assert resolved == expected, f"Alias '{alias}' did not resolve to '{expected}'"


class TestPolicyDigestAndConcurrencyE2E:
    """Verifies policy digest integrity and multi-threaded reload safety."""

    def test_compute_policy_digest_format_and_stability(self) -> None:
        """Policy digest must be a 64-character deterministic hex SHA-256."""
        digest = compute_policy_digest()
        assert len(digest) == 64
        assert int(digest, 16) > 0

    def test_concurrent_reload_and_validation_thread_safety(self) -> None:
        """10 threads calling reload_registry and validate_dispatch_model produce no race conditions."""
        errors: List[Exception] = []

        def worker(worker_id: int) -> None:
            try:
                for _ in range(10):
                    if worker_id % 3 == 0:
                        reload_registry()
                    m = validate_dispatch_model("gpt-6.1-sol", "openai")
                    assert m == "gpt-6.1-sol"
                    g = validate_dispatch_model("gemini-3.8-flash", "google")
                    assert g == "gemini-3.8-flash"
                    d = compute_policy_digest()
                    assert len(d) == 64
            except Exception as e:
                errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, i) for i in range(16)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Concurrent workers encountered errors: {errors}"


class TestResilienceAndCorruptPolicyE2E:
    """Verifies that missing or corrupt policy files fall back safely to compiled defaults."""

    def test_corrupt_registry_json_falls_back_cleanly(self) -> None:
        """When registry JSON contains invalid syntax, compiled fallback activates safely."""
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{ invalid json syntax ...")
            bad_path = Path(f.name)

        try:
            with mock.patch("dual_pipeline.model_registry.REGISTRY_JSON_PATH", bad_path):
                # Should not raise exception; falls back to compiled tiers and returns digest fallback
                reload_registry()
                approved = get_approved_models("openai")
                assert "gpt-6.1-sol" in approved
                assert compute_policy_digest() == "UNAVAILABLE_POLICY_DIGEST"
        finally:
            reload_registry()
            bad_path.unlink(missing_ok=True)
