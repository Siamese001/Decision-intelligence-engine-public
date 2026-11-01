"""Gemini Generative Language API caller and transcript stitcher for transcript stages."""
from __future__ import annotations
import json
import logging
import random
import socket
import threading
import time
import urllib.error
import urllib.request
from contextlib import suppress
from typing import Any, Dict, Optional, Tuple
from config.credentials import REASONING_MODEL_PREFIXES, CredentialManager
from config.transport import SiblingPassAbortedError, execute_with_deadline_and_heartbeat
from config.request_deadline import check_deadline, cooperative_sleep, remaining_timeout
from dual_pipeline.core import canonical_json
from dual_pipeline.model_registry import BANNED_REGISTRY_MODELS, GOOGLE_APPROVED_MODELS
from .call_ledger import D02CallLedger, get_active_call_ledger
from .gemini_context_cache import (
    create_gemini_context_cache as _create_cache_resource,
    delete_gemini_context_cache as _delete_cache_resource,
)
from .models import SynthesisTruncationError

logger = logging.getLogger(__name__)

_DEFAULT_LLM_CONCURRENCY = int(
    CredentialManager.get_credential("NEE_LLM_MAX_CONCURRENT_REQUESTS", "12") or "12"
)
_GEMINI_CONCURRENCY_SEMAPHORE = threading.Semaphore(value=max(1, min(_DEFAULT_LLM_CONCURRENCY, 32)))


def stitch_continuation(prefix: str, continuation: str) -> str:
    """Stitch truncated prefix and continuation, eliminating line or character overlap or mid-word seam."""
    prefix_clean = prefix.rstrip()
    cont_clean = continuation.lstrip()
    if not prefix_clean:
        return cont_clean
    if not cont_clean:
        return prefix_clean
    prefix_lines = prefix_clean.splitlines()
    cont_lines = cont_clean.splitlines()
    overlap_lines = 0
    for i in range(min(30, len(prefix_lines), len(cont_lines)), 0, -1):
        if prefix_lines[-i:] == cont_lines[:i]:
            overlap_lines = i
            break
    if overlap_lines > 0:
        return "\n".join(prefix_lines + cont_lines[overlap_lines:]).strip()
    max_char_ov = min(1000, len(prefix_clean), len(cont_clean))
    for k in range(max_char_ov, 19, -1):
        if prefix_clean.endswith(cont_clean[:k]):
            return (prefix_clean + cont_clean[k:]).strip()
    if prefix_clean[-1] in ("\n", ".", "!", "?", ":", ";", "*", "`", "#"):
        return f"{prefix_clean}\n{cont_clean}".strip()
    return f"{prefix_clean} {cont_clean}".strip()


def clean_json_markdown(text: str) -> str:
    """Strip markdown code fence wrappers from JSON output."""
    import re
    cleaned = text.strip()
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```", cleaned)
    if match:
        return match.group(1).strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _sanitize_gemini_schema(schema: Any) -> Any:
    """Sanitize schema for Gemini response_schema compatibility."""
    if not isinstance(schema, dict):
        return schema
    sanitized: Dict[str, Any] = {}
    for k, v in schema.items():
        if k in ("additionalProperties", "$schema", "title", "definitions", "$defs"):
            continue
        if k == "const":
            if isinstance(v, str):
                sanitized["type"] = "string"
                sanitized["enum"] = [v]
            elif isinstance(v, bool):
                sanitized["type"] = "boolean"
            elif isinstance(v, int):
                sanitized["type"] = "integer"
            continue
        if isinstance(v, dict):
            sanitized[k] = _sanitize_gemini_schema(v)
        elif isinstance(v, list):
            sanitized[k] = [_sanitize_gemini_schema(item) if isinstance(item, dict) else item for item in v]
        else:
            sanitized[k] = v
    return sanitized


def _resolve_gemini_thinking_level(
    model: str,
    thinking_level: Optional[str],
    reasoning_effort: Optional[str],
) -> Optional[str]:
    """Resolve effective Gemini thinking level from parameters and model SSOT."""
    if thinking_level:
        clean_tl = thinking_level.strip().upper()
        if clean_tl not in ("NONE", "OFF", "0", "DISABLED"):
            return clean_tl
        return None
    if reasoning_effort:
        effort_clean = reasoning_effort.strip().lower()
        if effort_clean in ("high", "max", "xhigh"):
            return "HIGH"
        if effort_clean == "medium":
            return "MEDIUM"
        if effort_clean in ("low", "minimal"):
            return "LOW"
        return None
    if any(model.lower().startswith(pfx) for pfx in REASONING_MODEL_PREFIXES):
        return "HIGH"
    return None


def _build_gemini_generation_config(
    eff_thinking_level: Optional[str],
    temperature: Optional[float],
    json_mode: bool,
    json_schema: Optional[Dict[str, Any]],
    thinking_budget: Optional[int] = None,
) -> Dict[str, Any]:
    """Constructs sanitized generationConfig adhering to parameter immunity."""
    gen_config: Dict[str, Any] = {
        "maxOutputTokens": 32000,
    }
    if eff_thinking_level or thinking_budget is not None:
        thinking_cfg: Dict[str, Any] = {}
        if eff_thinking_level:
            thinking_cfg["thinkingLevel"] = eff_thinking_level
        if thinking_budget is not None:
            thinking_cfg["thinkingBudget"] = int(thinking_budget)
        gen_config["thinkingConfig"] = thinking_cfg
    else:
        gen_config["temperature"] = temperature if temperature is not None else 0.2

    if json_mode or json_schema is not None:
        gen_config["responseMimeType"] = "application/json"
    if json_schema is not None:
        gen_config["responseSchema"] = _sanitize_gemini_schema(json_schema)
    return gen_config


class TruncatedResponseError(SynthesisTruncationError):
    """Raised when an LLM provider response is truncated due to token budget exhaustion (finishReason=MAX_TOKENS / LENGTH)."""
    pass


def _extract_gemini_metadata(resp_data: Dict[str, Any]) -> Tuple[str, Dict[str, Any], str]:
    """Extract text content, usageMetadata, and finishReason from Gemini response."""
    candidates = resp_data.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"Gemini API returned no candidates: {resp_data}")
    candidate = candidates[0]
    finish_reason = str(candidate.get("finishReason", "UNKNOWN")).upper()
    if finish_reason in ("MAX_TOKENS", "LENGTH"):
        raise TruncatedResponseError(
            f"Gemini API response truncated mid-stream due to output token limit (finishReason={finish_reason})"
        )
    content = candidate.get("content", {})
    parts = content.get("parts", [])
    text_chunks = [str(p.get("text", "")) for p in parts if isinstance(p, dict) and "text" in p]
    if not text_chunks or not any(chunk.strip() for chunk in text_chunks):
        raise RuntimeError(f"Gemini API empty response (finishReason={finish_reason})")
    usage = resp_data.get("usageMetadata", {})
    return "".join(text_chunks), usage, finish_reason


def _extract_gemini_response_text(resp_data: Dict[str, Any]) -> str:
    """Extract and validate text content from Gemini API response object."""
    text, _, _ = _extract_gemini_metadata(resp_data)
    return text


def _sanitize_gemini_dispatch_payload(user_content: str, system_prompt: str) -> None:
    """Pre-egress sanitization of outbound Gemini payload."""
    from dual_pipeline.sanitizer import LocalPreEgressSanitizer
    LocalPreEgressSanitizer.sanitize_text(user_content)
    if system_prompt:
        LocalPreEgressSanitizer.sanitize_text(system_prompt)


def _intercept_gemini_retry(attempt_idx: int, payload: bytes) -> None:
    if attempt_idx > 0:
        from dual_pipeline.cost_tracker import get_token_tracker
        get_token_tracker().intercept_token_call(len(payload) // 4)


def call_gemini_generate_content(
    model: str,
    system_prompt: str,
    user_content: str,
    json_mode: bool = False,
    json_schema: Optional[Dict[str, Any]] = None,
    deadline_seconds: float = 600.0,
    temperature: Optional[float] = None,
    thinking_level: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    pass_id: Optional[str] = None,
    pass_attempt: int = 1,
    ledger: Optional[D02CallLedger] = None,
    abort_event: Optional[Any] = None,
    cached_content: Optional[str] = None,
) -> str:
    _sanitize_gemini_dispatch_payload(user_content, system_prompt)

    model_orig = str(model).strip()
    from dual_pipeline.model_registry import validate_dispatch_model
    model = validate_dispatch_model(model, "google")
    fail_ev = dict(requested_model=model_orig, resolved_model=model, dispatched_model=model, observed_model=None, evidence_strength="DISPATCH_RECORDED")

    api_key = CredentialManager.get_credential("GEMINI_API_KEY", "").strip() or CredentialManager.get_credential("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Neither GEMINI_API_KEY nor GOOGLE_API_KEY is available for Gemini synthesis")

    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    eff_thinking = _resolve_gemini_thinking_level(model, thinking_level, reasoning_effort)
    gen_config = _build_gemini_generation_config(eff_thinking, temperature, json_mode, json_schema)

    body: Dict[str, Any] = {
        "generationConfig": gen_config,
    }
    if cached_content:
        body["cachedContent"] = cached_content
        body["contents"] = [{"role": "user", "parts": [{"text": user_content}]}]
    else:
        body["contents"] = [{"role": "user", "parts": [{"text": user_content}]}]
        if system_prompt:
            body["systemInstruction"] = {"parts": [{"text": system_prompt}]}

    payload = canonical_json(body).encode("utf-8")

    def _call_api() -> str:
        max_retries = 3
        active_ledger = ledger or get_active_call_ledger()
        for attempt_idx in range(max_retries):
            _intercept_gemini_retry(attempt_idx, payload)
            if abort_event is not None and getattr(abort_event, "is_set", lambda: False)():
                raise SiblingPassAbortedError("Gemini call aborted: sibling pass failed terminally.")
            t_start = time.monotonic()
            transport_attempt = attempt_idx + 1
            check_deadline()
            eff_timeout = remaining_timeout(min(120.0, deadline_seconds))
            req = urllib.request.Request(
                endpoint,
                data=payload,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": api_key,
                },
            )
            try:
                with _GEMINI_CONCURRENCY_SEMAPHORE, urllib.request.urlopen(req, timeout=remaining_timeout(eff_timeout)) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    text, usage, finish_reason = _extract_gemini_metadata(resp_data)
                    latency_ms = (time.monotonic() - t_start) * 1000.0
                    pt = int(usage.get("promptTokenCount", 0)) if isinstance(usage, dict) else 0
                    ct = int(usage.get("candidatesTokenCount", 0)) if isinstance(usage, dict) else 0
                    cpt = int(usage.get("cachedContentTokenCount", 0)) if isinstance(usage, dict) else 0
                    try:
                        from dual_pipeline.prompt_cache import normalize_cached_tokens

                        cached_toks = normalize_cached_tokens(resp_data, provider="gemini")
                        if cached_toks > 0:
                            logger.info("Gemini response utilized %d cached tokens.", cached_toks)
                    except Exception:
                        pass
                    observed_version = resp_data.get("modelVersion") if isinstance(resp_data, dict) else None
                    succ_ev = dict(requested_model=model_orig, resolved_model=model, dispatched_model=model, observed_model=observed_version, evidence_strength="OBSERVED_RESPONSE")
                    with suppress(Exception):
                        from dual_pipeline.cost_tracker import get_cost_tracker, get_token_tracker
                        from dual_pipeline.telemetry_core.metrics import record_llm_call_metrics
                        get_token_tracker().record_usage(prompt_tokens=pt, completion_tokens=ct, cached_prompt_tokens=cpt)
                        get_cost_tracker().record_call(model=model, prompt_tokens=pt, completion_tokens=ct, cached_prompt_tokens=cpt, batch=False, **succ_ev)
                        record_llm_call_metrics(model=model, duration_seconds=time.monotonic() - t_start, prompt_tokens=pt, completion_tokens=ct)
                    if active_ledger is not None:
                        active_ledger.record_call(pass_id=pass_id or "UNKNOWN", pass_attempt=pass_attempt, transport_attempt=transport_attempt, model=model, status="SUCCESS", finish_reason=finish_reason, usage_metadata=usage, latency_ms=latency_ms, **succ_ev)
                    if abort_event is not None and getattr(abort_event, "is_set", lambda: False)():
                        raise SiblingPassAbortedError("Gemini call aborted: sibling pass failed terminally.")
                    check_deadline()
                    return text
            except urllib.error.HTTPError as http_err:
                latency_ms = (time.monotonic() - t_start) * 1000.0
                check_deadline()
                status, err_body = http_err.code, ""
                with suppress(Exception):
                    err_body = http_err.read().decode("utf-8")
                if active_ledger is not None:
                    active_ledger.record_call(pass_id=pass_id or "UNKNOWN", pass_attempt=pass_attempt, transport_attempt=transport_attempt, model=model, status="FAILED", error=f"HTTP {status}: {err_body[:200]}", latency_ms=latency_ms, **fail_ev)
                if status in (429, 500, 503) and attempt_idx < max_retries - 1:
                    sleep_s = (2 ** attempt_idx) * 2.0 + random.uniform(0.1, 0.5)
                    logger.warning(f"Gemini HTTP {status}, retrying in {sleep_s:.2f}s...")
                    cooperative_sleep(sleep_s)
                    continue
                raise RuntimeError(f"Gemini API HTTP {status}: {err_body}") from http_err
            except (urllib.error.URLError, socket.timeout, TimeoutError) as net_err:
                check_deadline()
                latency_ms = (time.monotonic() - t_start) * 1000.0
                if active_ledger is not None:
                    active_ledger.record_call(pass_id=pass_id or "UNKNOWN", pass_attempt=pass_attempt, transport_attempt=transport_attempt, model=model, status="FAILED", error=f"Network: {net_err}", latency_ms=latency_ms, **fail_ev)
                if attempt_idx < max_retries - 1:
                    sleep_s = (2 ** attempt_idx) * 2.0 + random.uniform(0.1, 0.5)
                    logger.warning(f"Gemini network error ({net_err}), retrying in {sleep_s:.2f}s...")
                    cooperative_sleep(sleep_s)
                    continue
                raise RuntimeError(f"Gemini API connection error: {net_err}") from net_err

    return execute_with_deadline_and_heartbeat(
        _call_api,
        deadline_seconds=deadline_seconds,
        heartbeat_interval=15.0,
        operation_name=f"Gemini Synthesis ({model})",
        abort_event=abort_event,
    )


def create_gemini_context_cache(
    model: str,
    contents_text: str,
    ttl_seconds: int = 600,
    display_name: str = "transcript_cache",
) -> Optional[str]:
    """Create an explicit Gemini context cache for shared transcript context across DAG passes."""
    res = _create_cache_resource(
        model=model,
        system_prompt="",
        user_content=contents_text,
        ttl_seconds=ttl_seconds,
        display_name=display_name,
        force_cache=True,
    )
    return str(res.get("name")) if isinstance(res, dict) and res.get("name") else None


def delete_gemini_context_cache(cache_name: str) -> None:
    """Best-effort cleanup of an explicit Gemini context cache."""
    if cache_name:
        _delete_cache_resource(cache_name=cache_name)


def init_stage_models(
    synth: Any, model: Optional[str], d01: Optional[str], d02: Optional[str],
    d03: Optional[str], agentic: Optional[str], use_complexity_classifier: bool = False,
) -> None:
    """Initialize stage-specific models with fallback to 5D complexity tensor or L2 policy purpose models."""
    def _sanitize_stage_model(m: Optional[str]) -> Optional[str]:
        if not m:
            return None
        m_clean = m.strip()
        if m_clean in BANNED_REGISTRY_MODELS:
            from dual_pipeline.model_registry import ModelTier, resolve_tier_model_name
            fb = resolve_tier_model_name(ModelTier.ANTIGRAVITY_AGENT_FLASH if m_clean.startswith("gemini") else ModelTier.FRONTIER_HIGH_REASONING)
            logger.warning("Requested stage model '%s' is strictly banned in registry. Remapping to approved default '%s'.", m_clean, fb)
            return fb
        from dual_pipeline.model_registry import get_approved_models, is_model_approved
        provider = "google" if m_clean.startswith("gemini") else "openai"
        if not is_model_approved(m_clean, provider):
            raise ValueError(f"Requested stage model '{m_clean}' is not approved for provider '{provider}'. Approved models: {sorted(get_approved_models(provider))}")
        return m_clean

    model, d01, d02 = _sanitize_stage_model(model), _sanitize_stage_model(d01), _sanitize_stage_model(d02)
    d03, agentic = _sanitize_stage_model(d03), _sanitize_stage_model(agentic)

    if use_complexity_classifier:
        from dual_pipeline.model_registry import resolve_tier_model_name
        from dual_pipeline.query_router_core.complexity_classifier import classify_task_complexity

        # Use 5D complexity classification for optimal stage-specific tier binding
        d01_tier = classify_task_complexity("forensic transcript asr cleanup", {"task_type": "asr_cleanup", "cognitive_depth": 1, "blast_radius": 2})
        d02_tier = classify_task_complexity("executive recap synthesis", {"task_type": "executive_recap", "cognitive_depth": 3, "blast_radius": 3})
        d03_tier = classify_task_complexity("canonical signal projection knowledge graph", {"task_type": "extraction", "cognitive_depth": 3, "blast_radius": 3, "creates_evidence_item": True})
        agentic_tier = classify_task_complexity("agentic leverage strategic reasoning", {"task_type": "decision", "cognitive_depth": 4, "blast_radius": 3})

        synth.d01_model = d01 or model or resolve_tier_model_name(d01_tier)
        synth.d02_model = d02 or model or resolve_tier_model_name(d02_tier)
        synth.d03_model = d03 or model or resolve_tier_model_name(d03_tier)
        synth.agentic_model = agentic or model or resolve_tier_model_name(agentic_tier)
        return

    if model:
        synth.d01_model = d01 or model
        synth.d02_model = d02 or model
        synth.d03_model = d03 or model
        synth.agentic_model = agentic or model
        return

    cfg = None
    try:
        from dual_pipeline.l2_policy import CallPurpose, load_l2_config
        cfg = load_l2_config()
    except (FileNotFoundError, ImportError):
        pass
    except Exception as e:
        logger.error(f"Failed to load L2 configuration: {e}")
        raise RuntimeError(f"L2 configuration parsing failed: {e}") from e
    from dual_pipeline.model_registry import ModelTier, get_binding
    def_model = get_binding(ModelTier.COPILOT_FAST_SNAP).model_id
    m = getattr(synth, "model", def_model)
    synth.d01_model = d01 or (cfg.model_for_purpose(CallPurpose.FORENSIC_TRANSCRIPT).model if cfg else m)
    synth.d02_model = d02 or (cfg.model_for_purpose(CallPurpose.EXECUTIVE_RECAP).model if cfg else m)
    synth.d03_model = d03 or (cfg.model_for_purpose(CallPurpose.KG_SIGNAL_PROJECTION).model if cfg else m)
    synth.agentic_model = agentic or (cfg.model_for_purpose(CallPurpose.AGENTIC_LEVERAGE).model if cfg else m)
