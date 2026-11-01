"""Explicit Gemini Context Caching for Deliverable 02 (Executive Recap) synthesis.

Enables caching of the shared prefix (governance context + Output 01 transcript)
to eliminate 3x input token multiplication across the parallel passes of the modular DAG.
Provides run-bound TTL and graceful fallback when the prefix is below the minimum cacheable size.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
import uuid
from contextlib import suppress
from typing import Any, Dict, Optional

from config.credentials import CredentialManager
from config.transport import urllib_json_request

logger = logging.getLogger(__name__)

DEFAULT_CACHE_TTL_SECONDS = 300  # 5 minutes run-bound TTL
MIN_CACHEABLE_CHAR_ESTIMATE = 4_096  # ~1,024 tokens matching Gemini Flash context caching minimum


def is_context_caching_enabled() -> bool:
    """Check if explicit context caching is enabled via environment."""
    val = (CredentialManager.get_credential("NEE_D02_DISABLE_CACHE", "") or "").strip().lower()
    return val not in ("1", "true", "yes", "on")


def normalize_gemini_model_resource(model: str) -> str:
    """Normalize model string to models/... resource format for cachedContents."""
    clean = model.strip()
    if not clean.startswith("models/"):
        return f"models/{clean}"
    return clean


def create_gemini_context_cache(
    model: str,
    system_prompt: str,
    user_content: str,
    ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    display_name: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: float = 30.0,
    force_cache: bool = False,
    min_cacheable_chars: int = MIN_CACHEABLE_CHAR_ESTIMATE,
) -> Optional[Dict[str, Any]]:
    """Creates an explicit cachedContents resource on Google Generative Language API.

    Returns the cache resource dict (containing 'name': 'cachedContents/...') if successful.
    Returns None if caching is disabled, if prefix is below the minimum cacheable size,
    or if the API rejects cache creation (graceful fallback).
    """
    if not is_context_caching_enabled():
        logger.debug("Gemini context caching disabled via NEE_D02_DISABLE_CACHE.")
        return None

    prefix_len = len(user_content) + len(system_prompt)
    force = force_cache or ((CredentialManager.get_credential("NEE_D02_FORCE_CACHE", "") or "").strip() == "1")
    if not force and prefix_len < min_cacheable_chars:
        logger.debug(
            "Shared prefix (%d chars) is below minimum cacheable size (%d chars); falling back to uncached execution.",
            prefix_len,
            min_cacheable_chars,
        )
        return None

    resolved_key = (
        api_key
        or CredentialManager.get_credential("GEMINI_API_KEY", "").strip()
        or CredentialManager.get_credential("GOOGLE_API_KEY", "").strip()
    )
    if not resolved_key:
        logger.debug("No API key available for context cache creation; falling back to uncached.")
        return None

    endpoint = "https://generativelanguage.googleapis.com/v1beta/cachedContents"
    model_res = normalize_gemini_model_resource(model)
    name_tag = display_name or f"d02_prefix_{uuid.uuid4().hex[:8]}"

    body: Dict[str, Any] = {
        "model": model_res,
        "displayName": name_tag,
        "ttl": f"{ttl_seconds}s",
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_content}],
            }
        ],
    }

    if system_prompt and system_prompt.strip():
        body["systemInstruction"] = {
            "parts": [{"text": system_prompt.strip()}],
        }

    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": resolved_key,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            cache_name = data.get("name")
            if cache_name:
                logger.info("Created explicit Gemini context cache '%s' (TTL: %ds)", cache_name, ttl_seconds)
                return data
            logger.warning("Gemini cachedContents response did not contain 'name': %s", data)
            return None
    except urllib.error.HTTPError as http_err:
        err_body = ""
        with suppress(Exception):
            err_body = http_err.read().decode("utf-8")
        # HTTP 400 is expected when prompt is below minimum cacheable size (e.g. < 32768 tokens)
        logger.info(
            "Gemini context cache creation bypassed or rejected (status %d: %s); falling back to uncached execution.",
            http_err.code,
            err_body[:200],
        )
        return None
    except Exception as exc:
        logger.info("Gemini context cache creation failed (%s); falling back to uncached execution.", exc)
        return None


def delete_gemini_context_cache(
    cache_name: str,
    api_key: Optional[str] = None,
    timeout: float = 10.0,
) -> bool:
    """Deletes a cachedContents resource after DAG execution completes.

    Returns True if successfully deleted, False otherwise.
    """
    if not cache_name or not cache_name.strip():
        return False

    resolved_key = (
        api_key
        or CredentialManager.get_credential("GEMINI_API_KEY", "").strip()
        or CredentialManager.get_credential("GOOGLE_API_KEY", "").strip()
    )
    if not resolved_key:
        return False

    clean_name = cache_name.strip()
    if clean_name.startswith("cachedContents/"):
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/{clean_name}"
    else:
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/cachedContents/{clean_name}"

    req = urllib.request.Request(
        endpoint,
        method="DELETE",
        headers={
            "x-goog-api-key": resolved_key,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            logger.info("Successfully deleted Gemini context cache '%s' (status %d)", clean_name, resp.status)
            return True
    except Exception as exc:
        logger.debug("Failed to delete context cache '%s' (non-fatal, TTL will expire): %s", clean_name, exc)
        return False
