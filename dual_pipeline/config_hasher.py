#!/usr/bin/env python3
"""
dual_pipeline/config_hasher.py

Cryptographic Configuration Pinning & Drift Guard Engine.
Computes deterministic RFC-8259 canonical SHA-256 digests of active configuration state
(model tiers, gateway endpoints, reasoning parameters, provider postures)
while strictly isolating and excluding raw secret tokens.

Enforces:
- AGENTS.md §"Runtime and retrieval claims are evidence-bound"
- AGENTS.md Invariant 4 (Hermetic Credential Isolation & SSOT)
- Automated drift detection across execution runs
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, Optional

from config.credentials import (
    ensure_ssot_env_loaded,
    resolve_anthropic_credentials,
    resolve_google_credentials,
    resolve_local_embedding_credentials,
    resolve_openai_credentials,
)
from dual_pipeline.model_registry import ModelTier, get_tier_spec, resolve_tier_model

logger = logging.getLogger("dual_pipeline.config_hasher")


def _resolve_providers_summary() -> Dict[str, Any]:
    """Resolves non-sensitive provider posture summary via CredentialManager SSOT."""
    try:
        openai_creds = resolve_openai_credentials(allow_mock=False)
    except Exception:
        from config.credentials import CredentialManager, OpenAICredentials
        fallback_model, fallback_effort = resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
        target_base_url = CredentialManager.get_credential("OPENAI_BASE_URL") or CredentialManager.get_credential("OPENAI_API_BASE")
        target_org = CredentialManager.get_credential("OPENAI_ORG_ID")
        target_project = CredentialManager.get_credential("OPENAI_PROJECT_ID")
        openai_creds = OpenAICredentials(
            api_key=None, is_mock=False, model=fallback_model, reasoning_effort=fallback_effort or "max",
            masked_key="[NOT_CONFIGURED]", source="none", base_url=target_base_url,
            organization=target_org, project=target_project,
        )

    try:
        anthropic_creds = resolve_anthropic_credentials(allow_mock=False)
    except Exception:
        from config.credentials import AnthropicCredentials, CredentialManager
        target_base_url = CredentialManager.get_credential("ANTHROPIC_BASE_URL")
        anthropic_creds = AnthropicCredentials(
            api_key=None, is_mock=False, masked_key="[NOT_CONFIGURED]", source="none", base_url=target_base_url,
        )

    try:
        google_creds = resolve_google_credentials(allow_mock=False)
    except Exception:
        from config.credentials import GoogleCredentials
        google_creds = GoogleCredentials(
            api_key=None, is_mock=False, masked_key="[NOT_CONFIGURED]", source="none",
        )

    local_creds = resolve_local_embedding_credentials()
    return {
        "openai": {
            "configured": openai_creds.has_live_key,
            "model": openai_creds.model,
            "reasoning_effort": openai_creds.reasoning_effort,
            "base_url": openai_creds.base_url or "default",
            "organization": bool(openai_creds.organization),
            "project": bool(openai_creds.project),
        },
        "anthropic": {
            "configured": not anthropic_creds.is_mock and bool(anthropic_creds.api_key),
            "base_url": anthropic_creds.base_url or "default",
        },
        "google": {
            "configured": not google_creds.is_mock and bool(google_creds.api_key),
        },
        "local_embedding": {
            "available": local_creds.is_available,
            "device": local_creds.device,
            "fp16": local_creds.fp16,
        },
    }


def compute_active_config_snapshot() -> Dict[str, Any]:
    """Builds a deterministic, non-sensitive snapshot of the active SSOT configuration."""
    ensure_ssot_env_loaded()
    providers_summary = _resolve_providers_summary()

    # Collect model tier specifications
    tiers_summary: Dict[str, Any] = {}
    for tier in ModelTier:
        spec = get_tier_spec(tier)
        tiers_summary[tier.value] = {
            "default_model": spec.get("default_model"),
            "provider": spec.get("provider"),
            "reasoning_effort": spec.get("reasoning_effort"),
            "strip_temperature": spec.get("parameter_immunity", {}).get("strip_temperature", True),
        }

    from pathlib import Path
    from dual_pipeline.asr_decode_config import DEFAULT_ASR_DECODE_CONFIG
    from dual_pipeline.model_registry import compute_policy_digest
    from dual_pipeline.parameters import compute_parameters_digest, get_known_projects
    from dual_pipeline.release_thresholds import ReleaseThresholdPolicy

    audio_norm_path = Path(__file__).resolve().parent.parent / "config" / "audio_normalization_profiles.yaml"
    audio_norm_digest = (
        hashlib.sha256(audio_norm_path.read_bytes()).hexdigest()
        if audio_norm_path.is_file()
        else hashlib.sha256(b"").hexdigest()
    )

    return {
        "schema_version": "1.0.0",
        "parameters_digest": compute_parameters_digest(),
        "release_policy_digest": ReleaseThresholdPolicy().compute_policy_digest(),
        "model_registry_digest": compute_policy_digest(),
        "asr_decode_digest": DEFAULT_ASR_DECODE_CONFIG.compute_config_digest(),
        "audio_normalization_digest": audio_norm_digest,
        "known_projects": sorted(list(get_known_projects())),
        "providers": providers_summary,
        "model_tiers": tiers_summary,
    }


def compute_config_canonical_sha256(snapshot: Optional[Dict[str, Any]] = None) -> str:
    """Serializes snapshot to RFC-8259 canonical UTF-8 JSON and returns its SHA-256 digest."""
    data = snapshot if snapshot is not None else compute_active_config_snapshot()
    canonical_bytes = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def verify_config_integrity(expected_sha256: str) -> bool:
    """Verifies that the current active configuration matches an expected canonical digest."""
    current_sha256 = compute_config_canonical_sha256()
    return current_sha256 == expected_sha256


__all__ = [
    "compute_active_config_snapshot",
    "compute_config_canonical_sha256",
    "verify_config_integrity",
]
