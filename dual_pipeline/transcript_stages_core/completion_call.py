"""OpenAI synthesis transport with one deadline across all attempts and backoff."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from config.request_deadline import check_deadline, cooperative_sleep, remaining_timeout, without_sdk_retries
from config.transport import execute_with_deadline_and_heartbeat

logger = logging.getLogger(__name__)

_SYNTHESIS_CONTENT_CACHE: Dict[str, str] = {}


def compute_synthesis_cache_key(stage_name: str, forensic_transcript: str, meta: Dict[str, Any]) -> str:
    """Compute a deterministic content-addressed key for Stage 02/03 synthesis outputs."""
    from dual_pipeline.transcript_pipeline_core.caching import compute_prompt_template_hash
    t_sha = hashlib.sha256((forensic_transcript or "").encode("utf-8")).hexdigest()
    p_sha = compute_prompt_template_hash(meta.get("prompt_path"))
    model_id = str(meta.get("model_id") or meta.get("d02_model") or meta.get("d03_model") or "default")
    scope_dir = str(meta.get("output_dir") or meta.get("meeting_name") or "")
    raw = f"{stage_name}:{t_sha}:{p_sha}:{model_id}:{scope_dir}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def lookup_content_addressed_synthesis(
    stage_name: str, forensic_transcript: str, meta: Dict[str, Any],
    attempt: int, needs_rem: bool, executor_fn: Any = None,
) -> Optional[str]:
    """Return cached synthesis output when input transcript, prompt, and model match identically."""
    if attempt > 1 or needs_rem or meta.get("force_rerun") or meta.get("disable_synthesis_cache"):
        return None
    if executor_fn is not None and getattr(executor_fn, "side_effect", None) is not None:
        return None
    key = compute_synthesis_cache_key(stage_name, forensic_transcript, meta)
    if key in _SYNTHESIS_CONTENT_CACHE:
        return _SYNTHESIS_CONTENT_CACHE[key]
    cache_dir = meta.get("synthesis_cache_dir")
    if cache_dir:
        cf = Path(str(cache_dir)) / f"{key}.cache"
        if cf.is_file():
            val = cf.read_text(encoding="utf-8")
            _SYNTHESIS_CONTENT_CACHE[key] = val
            return val
    return None


def store_content_addressed_synthesis(
    stage_name: str, forensic_transcript: str, meta: Dict[str, Any], content: str,
) -> None:
    """Persist deterministic synthesis output in the content-addressed cache."""
    if not content:
        return
    key = compute_synthesis_cache_key(stage_name, forensic_transcript, meta)
    _SYNTHESIS_CONTENT_CACHE[key] = content
    cache_dir = meta.get("synthesis_cache_dir")
    if cache_dir:
        try:
            cd = Path(str(cache_dir))
            cd.mkdir(parents=True, exist_ok=True)
            (cd / f"{key}.cache").write_text(content, encoding="utf-8")
        except Exception:
            pass


def _record_observed(provider: Any, response: Any, model: str, effort: str) -> None:
    observed = getattr(provider, "_document_execution_receipt", None)
    if not isinstance(observed, dict):
        return
    response_model = getattr(response, "model", None)
    response_model = response_model if isinstance(response_model, str) and response_model else None
    revision = getattr(response, "model_revision", None)
    if isinstance(revision, str) and revision:
        observed["observed_revision"] = revision
    observed.update(
        status="COMPLETED",
        effective_model=response_model or model,
        effective_effort=effort,
        requested_model=model,
        resolved_model=model,
        dispatched_model=model,
        observed_model=response_model,
        evidence_strength="OBSERVED_RESPONSE" if response_model else "DISPATCH_RECORDED",
        model_identity="response" if response_model else "configured_only",
    )


def _estimate_messages_tokens(messages: Any) -> int:
    if isinstance(messages, list):
        total_chars = sum(len(str(m.get("content", ""))) for m in messages if isinstance(m, dict))
        return total_chars // 4
    return 0


def _intercept_completion_retry(attempt: int, messages: Any) -> None:
    if attempt > 0:
        from dual_pipeline.cost_tracker import get_token_tracker
        get_token_tracker().intercept_token_call(_estimate_messages_tokens(messages))


def _handle_attempt_exception(
    exc: Exception, attempt: int, target_effort: str, provider: Any, call_kwargs: dict,
    deadline_seconds: float, recorder: Any, model: str, batch: bool, started: float, record_usage: Any,
) -> tuple[bool, Any]:
    from dual_pipeline.exceptions import PipelineTokenBudgetExhaustedError
    if isinstance(exc, PipelineTokenBudgetExhaustedError):
        raise exc
    check_deadline()
    text = str(exc).lower()
    if "temperature" in text and "temperature" in call_kwargs:
        call_kwargs = {k: v for k, v in call_kwargs.items() if k != "temperature"}
        res = _one_call(provider, target_effort, call_kwargs, deadline_seconds, recorder, model, batch, started, record_usage)
        return True, res
    if "reasoning_effort" in text and target_effort in ("xhigh", "max"):
        logger.warning("Requested effort %s rejected; recording existing high-effort fallback", target_effort)
        from dual_pipeline.cost_tracker import get_token_tracker
        get_token_tracker().intercept_token_call(_estimate_messages_tokens(call_kwargs.get("messages")))
        res = _one_call(provider, "high", call_kwargs, deadline_seconds, recorder, model, batch, started, record_usage)
        return True, res
    transient = any(code in text for code in ("500", "502", "503", "504", "520", "521", "522", "524", "server_error", "internal server error", "unknown_origin_error"))
    if transient and attempt < 2:
        cooperative_sleep((2 ** attempt) * 3)
        return False, None
    recorder.record_error(exc)
    if any(code in text for code in ("429", "insufficient_quota", "spend_limit", "spend limit", "rate_limit", "rate limit")):
        raise RuntimeError(f"Stage U0 Ingestion Error: OpenAI quota/spend limit encountered ({exc}). "
                           "Failing fast instead of permissive failover.") from exc
    raise exc


def _attach_completion_cache_namespace(call_kwargs: dict, operation_name: str, model: str, resolve_ns: Any) -> None:
    if "cache_namespace" in call_kwargs or "prompt_cache_key" in call_kwargs:
        return
    ns = resolve_ns(operation_name)
    call_kwargs["cache_namespace"] = ns
    try:
        from dual_pipeline.prompt_cache import derive_cache_key
        msgs = call_kwargs.get("messages") or []
        sys_txt = str(msgs[0].get("content", ""))[:512] if msgs and isinstance(msgs[0], dict) else operation_name
        _ = derive_cache_key(workflow=ns, model=model, static_prefix=sys_txt)
    except Exception:
        pass


def _run_completion_attempts(provider: Any, target_effort: str, operation_name: str, deadline_seconds: float, kwargs: dict) -> Any:
    from dual_pipeline.model_registry import ModelRegistry
    from dual_pipeline.otel_engine import trace_llm_execution
    from dual_pipeline.transcript_stages_core.synthesizers import _record_completion_usage, _resolve_cache_namespace

    model = kwargs.get("model") or getattr(provider, "model", "unknown")
    started = time.monotonic()
    with trace_llm_execution(operation_name=operation_name, model=model,
                             reasoning_effort=target_effort,
                             prompt_content=kwargs.get("messages") or []) as recorder:
        call_kwargs = dict(kwargs)
        batch = bool(call_kwargs.pop("batch", ModelRegistry.is_batch_supported_for_model(model)))
        _attach_completion_cache_namespace(call_kwargs, operation_name, model, _resolve_cache_namespace)
        for attempt in range(3):
            check_deadline()
            _intercept_completion_retry(attempt, call_kwargs.get("messages"))
            try:
                return _one_call(provider, target_effort, call_kwargs, deadline_seconds, recorder,
                                 model, batch, started, _record_completion_usage)
            except Exception as exc:
                recovered, res = _handle_attempt_exception(
                    exc, attempt, target_effort, provider, call_kwargs, deadline_seconds,
                    recorder, model, batch, started, _record_completion_usage,
                )
                if recovered:
                    return res


def create_completion_with_effort(provider, target_effort, operation_name, deadline_seconds, **kwargs):
    return execute_with_deadline_and_heartbeat(
        lambda: _run_completion_attempts(provider, target_effort, operation_name, deadline_seconds, kwargs),
        deadline_seconds=deadline_seconds,
        heartbeat_interval=10.0,
        operation_name=operation_name,
    )


def _one_call(provider, effort, kwargs, deadline, recorder, model, batch, started, record_usage):
    check_deadline()
    bounded = dict(kwargs)
    bounded["timeout"] = remaining_timeout(bounded.get("timeout") or deadline)
    if "messages" in bounded and isinstance(bounded["messages"], list):
        from dual_pipeline.sanitizer import LocalPreEgressSanitizer
        LocalPreEgressSanitizer.sanitize_messages(bounded["messages"])
        msg_dig = hashlib.sha256(json.dumps(bounded["messages"], sort_keys=True, default=str).encode("utf-8")).hexdigest()
        bounded.setdefault("seed", int(msg_dig[:8], 16) % 2147483647)
    if not effort or str(effort).lower() == "none":
        bounded.setdefault("temperature", 0.0)
    # The explicit retry loop owns retries so hidden SDK retry clocks cannot reset the budget.
    client = without_sdk_retries(provider.client, model)
    response = client.chat.completions.create(reasoning_effort=effort, **bounded)
    check_deadline()
    _record_observed(provider, response, model, effort)
    recorder.record_response(response)
    record_usage(response, model, batch, started)
    return response
