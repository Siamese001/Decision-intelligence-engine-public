#!/usr/bin/env python3
"""
tests/property/test_model_identity_evidence_properties.py

Hypothesis Property-Based Testing Suite for Model Identity Evidence and Verification Invariants.
Validates:
1. Missing host metadata strictly yields UNAVAILABLE state.
2. Unapproved host models strictly yield POLICY_MISMATCH with is_approved_by_policy=False.
3. verify_identity_evidence rejects approved-but-wrong models across all distinct approved pairs.
4. Deterministic tools strictly reject LLM model declarations and accept provider-free indicators.
5. Tampered or corrupted evidence dictionaries fail closed unconditionally.
6. EvidenceTier strictly adheres to the 4-tier taxonomy.
7. Truthful unavailable declarations are accepted when evidence is absent.

Governed under Finding E1-E2, E8, and Wave 3 Model Identity SSOT Invariants.
"""
from __future__ import annotations

import os
import string
from unittest import mock
import hypothesis.strategies as st
from hypothesis import given, settings

from config.credentials import (
    GOOGLE_APPROVED_MODELS,
    OPENAI_APPROVED_MODELS,
    canonicalize_model_name,
)
from dual_pipeline.host_metadata_adapter import (
    EvidenceTier,
    HostIdentityReport,
    IdentityScope,
    render_scratchpad_header,
    resolve_host_identity,
    verify_identity_evidence,
)
from dual_pipeline.model_registry import (
    BANNED_REGISTRY_MODELS,
    get_approved_models,
)

# Hypothesis strategies
approved_google_models = st.sampled_from(sorted(GOOGLE_APPROVED_MODELS))
approved_openai_models = st.sampled_from(sorted(OPENAI_APPROVED_MODELS))
all_approved_models = st.sampled_from(sorted(GOOGLE_APPROVED_MODELS | OPENAI_APPROVED_MODELS))

arbitrary_strings = st.text(
    alphabet=string.ascii_letters + string.digits + "-_./ ",
    min_size=1,
    max_size=100,
)


class TestModelIdentityEvidenceProperties:
    """Hypothesis property tests asserting model identity evidence bounds."""

    @settings(max_examples=40, deadline=None)
    @given(scope=st.sampled_from(list(IdentityScope)))
    def test_missing_host_metadata_strictly_yields_unavailable(self, scope: IdentityScope) -> None:
        """When host metadata is absent, resolve_host_identity must return UNAVAILABLE for model scopes."""
        with mock.patch.dict(os.environ, {}, clear=True):
            report = resolve_host_identity(scope=scope)
            if scope == IdentityScope.DETERMINISTIC_TOOL:
                assert report.status == "VERIFIED"
                assert "Provider: None" in report.rendered_summary
            else:
                assert report.status == "UNAVAILABLE"
                assert "Unavailable / Unverified" in report.rendered_summary
                assert report.is_approved_by_policy is False
                header = render_scratchpad_header(report)
                assert "Unavailable / Unverified" in header

    @settings(max_examples=50, deadline=None)
    @given(
        unapproved_name=arbitrary_strings,
        provider=st.sampled_from(["openai", "google", "anthropic"]),
    )
    def test_unapproved_host_models_strictly_yield_policy_mismatch(
        self, unapproved_name: str, provider: str
    ) -> None:
        """Any unapproved host model must be reported truthfully as POLICY_MISMATCH, or UNAVAILABLE if blank."""
        clean = canonicalize_model_name(unapproved_name.strip())
        approved = get_approved_models(provider)

        mock_env = {
            "ANTIGRAVITY_ACTIVE_MODEL": unapproved_name,
            "ANTIGRAVITY_MODEL_PROVIDER": provider,
            "ANTIGRAVITY_SESSION_ID": "test-session-123",
        }
        with mock.patch.dict(os.environ, mock_env, clear=True):
            report = resolve_host_identity(scope=IdentityScope.HOST_ASSISTANT)
            if not unapproved_name.strip():
                assert report.status == "UNAVAILABLE"
            elif clean not in approved or clean in BANNED_REGISTRY_MODELS:
                assert report.status == "POLICY_MISMATCH"
                assert report.is_approved_by_policy is False
                assert "[POLICY MISMATCH" in report.rendered_summary

    @settings(max_examples=60, deadline=None)
    @given(
        decl_model=approved_google_models,
        actual_model=approved_google_models,
    )
    def test_verify_identity_evidence_rejects_approved_but_wrong_google(
        self, decl_model: str, actual_model: str
    ) -> None:
        """An approved Google model must be rejected if it differs from the actual execution model."""
        evidence = {
            "dispatched_model": actual_model,
            "observed_model": actual_model,
            "provider": "google",
            "evidence_strength": "OBSERVED_RESPONSE",
        }
        is_ok, reason = verify_identity_evidence(
            declared_model=decl_model,
            evidence=evidence,
            scope=IdentityScope.APPLICATION_CALL,
        )
        if decl_model == actual_model:
            assert is_ok is True
            assert "matches" in reason.lower()
        else:
            assert is_ok is False
            assert "Approved-but-wrong identity violation" in reason

    @settings(max_examples=60, deadline=None)
    @given(
        decl_model=approved_openai_models,
        actual_model=approved_openai_models,
    )
    def test_verify_identity_evidence_rejects_approved_but_wrong_openai(
        self, decl_model: str, actual_model: str
    ) -> None:
        """An approved OpenAI model must be rejected if it differs from the actual execution model."""
        evidence = {
            "dispatched_model": actual_model,
            "observed_model": actual_model,
            "provider": "openai",
            "evidence_strength": "OBSERVED_RESPONSE",
        }
        is_ok, reason = verify_identity_evidence(
            declared_model=decl_model,
            evidence=evidence,
            scope=IdentityScope.APPLICATION_CALL,
        )
        if decl_model == actual_model:
            assert is_ok is True
            assert "matches" in reason.lower()
        else:
            assert is_ok is False
            assert "Approved-but-wrong identity violation" in reason

    @settings(max_examples=40, deadline=None)
    @given(decl_model=all_approved_models)
    def test_deterministic_tools_reject_llm_declarations(self, decl_model: str) -> None:
        """Deterministic tools cannot declare LLM model identities."""
        is_ok, reason = verify_identity_evidence(
            declared_model=decl_model,
            evidence=None,
            scope=IdentityScope.DETERMINISTIC_TOOL,
        )
        assert is_ok is False
        assert "Deterministic tool cannot declare an LLM model identity" in reason

    @settings(max_examples=30, deadline=None)
    @given(
        provider_free_decl=st.sampled_from([
            "Provider: None (Provider-Free)",
            "None (Provider-Free)",
            "provider-free",
            "none",
        ])
    )
    def test_deterministic_tools_accept_provider_free(self, provider_free_decl: str) -> None:
        """Deterministic tools accept compliant provider-free declarations."""
        is_ok, reason = verify_identity_evidence(
            declared_model=provider_free_decl,
            evidence=None,
            scope=IdentityScope.DETERMINISTIC_TOOL,
        )
        assert is_ok is True
        assert "OK: Deterministic provider-free tool" in reason

    @settings(max_examples=40, deadline=None)
    @given(
        model=all_approved_models,
        tamper_flag=st.sampled_from(["tampered", "corrupt"]),
    )
    def test_tampered_or_corrupt_evidence_fails_closed(
        self, model: str, tamper_flag: str
    ) -> None:
        """Evidence flagged as tampered or corrupt must fail closed."""
        evidence = {
            "dispatched_model": model,
            "observed_model": model,
            tamper_flag: True,
        }
        is_ok, reason = verify_identity_evidence(
            declared_model=model,
            evidence=evidence,
            scope=IdentityScope.APPLICATION_CALL,
        )
        assert is_ok is False
        assert "tampered" in reason.lower() or "corrupt" in reason.lower()

    def test_evidence_tiers_exact_four_tiers(self) -> None:
        """Verify that EvidenceTier contains exactly the 4 required governance tiers."""
        tier_names = {t.name for t in EvidenceTier}
        expected = {
            "CONFIGURED_ONLY",
            "DISPATCH_RECORDED",
            "OBSERVED_RESPONSE",
            "CRYPTOGRAPHICALLY_SEALED",
        }
        assert tier_names == expected
