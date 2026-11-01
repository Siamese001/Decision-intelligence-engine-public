#!/usr/bin/env python3
"""
storage/credentials.py

Backwards-compatible facade re-exporting canonical credential management
from `config.credentials`.
Enforces Layer 0 downward dependency flow and unified SSOT resolution.
"""
from __future__ import annotations

from config.credentials import (
    GOVERNED_MODELS,
    REASONING_MODEL_PREFIXES,
    SSOT_ENV_FILE,
    WORKSPACE_ROOT,
    CredentialManager,
    OpenAICredentials,
    ensure_ssot_env_loaded,
    get_async_openai_client,
    get_openai_client,
    mask_secret,
    resolve_openai_credentials,
)

__all__ = [
    "CredentialManager",
    "OpenAICredentials",
    "resolve_openai_credentials",
    "get_openai_client",
    "get_async_openai_client",
    "mask_secret",
    "ensure_ssot_env_loaded",
    "GOVERNED_MODELS",
    "REASONING_MODEL_PREFIXES",
    "WORKSPACE_ROOT",
    "SSOT_ENV_FILE",
]
