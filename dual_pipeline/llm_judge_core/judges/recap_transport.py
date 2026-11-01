"""Recap judge transport with deadline propagation, observed model provenance, and single retry-owner pattern."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Dict

from config.request_deadline import (
    check_deadline,
    cooperative_sleep,
    deadline_scope,
    remaining_timeout,
    without_sdk_retries,
)
from config.transport import execute_with_deadline_and_heartbeat
from dual_pipeline.prompts import CANONICAL_PROMPT_CONFIG

logger = logging.getLogger(__name__)


def is_transient_recap_error(e: Exception) -> bool:
    """Classifies transient network, rate limit, and server errors for retry gating."""
    if isinstance(e, (TimeoutError, ConnectionError)):
        return True
    err_str = f"{type(e).__name__} {str(e)}".lower()
    return any(code in err_str for code in ("429", "500", "502", "503", "504", "rate_limit", "timeout", "timed out", "connection"))


def _call_openai_recap(
    client: Any,
    model: str,
    effort: str,
    system_prompt: str,
    user_content: str,
    token_limit: int,
) -> Any:
    cfg = CANONICAL_PROMPT_CONFIG.judge
    for attempt in range(3):
        if attempt > 0:
            from dual_pipeline.cost_tracker import get_token_tracker
            estimated_tokens = (len(system_prompt) + len(user_content)) // 4
            get_token_tracker().intercept_token_call(estimated_tokens)
        check_deadline()
        try:
            bounded_client = without_sdk_retries(client, model)
            response = bounded_client.chat.completions.create(
                model=model,
                reasoning_effort=effort,
                messages=[
                    {"role": "system", "content": system_prompt.strip()},
                    {"role": "user", "content": user_content.strip()},
                ],
                max_completion_tokens=token_limit,
                response_format={"type": "json_object"},
                timeout=remaining_timeout(cfg.deadline_seconds),
                cache_namespace="judge_recap",
            )
            check_deadline()
            return response
        except Exception as exc:
            check_deadline()
            from dual_pipeline.exceptions import PipelineTokenBudgetExhaustedError
            if isinstance(exc, PipelineTokenBudgetExhaustedError):
                raise
            if not is_transient_recap_error(exc) or attempt == 2:
                raise
            cooperative_sleep(2.0 * (attempt + 1))


def _validate_or_retry_recap_scorecard(
    raw_json: str,
    client: Any,
    model: str,
    reasoning_effort: str,
    system_prompt: str,
    user_content: str,
    cfg: Any,
) -> Dict[str, Any]:
    from dual_pipeline.release_policy import validate_micro_judge_scorecard
    try:
        parsed = json.loads(raw_json)
    except Exception as json_err:
        parsed = {"status": "FAIL", "verdict": "FAIL", "composite_score": 0, "validation_errors": [str(json_err)]}
    is_valid, errs = validate_micro_judge_scorecard(parsed, output_num=2)
    if is_valid or client is None:
        return parsed

    logger.warning("ExecutiveRecapJudge returned malformed scorecard: %s. Initiating single bounded retry.", errs)
    try:
        response2 = execute_with_deadline_and_heartbeat(
            _call_openai_recap,
            client,
            model,
            reasoning_effort,
            system_prompt,
            user_content + "\n\nCRITICAL: Return strictly valid JSON conforming to the Output 2 scorecard schema with section_scores.",
            cfg.max_completion_tokens,
            deadline_seconds=remaining_timeout(cfg.deadline_seconds),
            heartbeat_interval=cfg.heartbeat_interval,
            operation_name="L4-Judge Executive Recap Evaluation Retry",
        )
        raw_json2 = response2.choices[0].message.content or "{}"
        parsed2 = json.loads(raw_json2)
        is_valid2, errs2 = validate_micro_judge_scorecard(parsed2, output_num=2)
        if is_valid2:
            return parsed2
        errs = errs2
    except Exception as retry_exc:
        errs = errs + [str(retry_exc)]

    return {
        **parsed,
        "is_valid_scorecard": False,
        "status": "FAIL",
        "verdict": "FAIL",
        "composite_score": 0,
        "validation_errors": errs,
    }


def generate_client_recap(
    client: Any,
    model: str,
    reasoning_effort: str,
    system_prompt: str,
    user_content: str,
    recorder: Any,
) -> Dict[str, Any]:
    """Execute ExecutiveRecapJudge evaluation under cumulative deadline and single retry-owner pattern."""
    cfg = CANONICAL_PROMPT_CONFIG.judge
    with deadline_scope(cfg.deadline_seconds):
        t_start = time.perf_counter()
        try:
            response = execute_with_deadline_and_heartbeat(
                _call_openai_recap,
                client,
                model,
                reasoning_effort,
                system_prompt,
                user_content,
                cfg.max_completion_tokens,
                deadline_seconds=cfg.deadline_seconds,
                heartbeat_interval=cfg.heartbeat_interval,
                operation_name="L4-Judge Executive Recap Evaluation",
            )
            check_deadline()
            elapsed = max(0.001, time.perf_counter() - t_start)
            recorder.record_response(response)
            from dual_pipeline.llm_judge_core.telemetry import record_judge_llm_usage
            record_judge_llm_usage(
                response,
                model=model,
                duration_seconds=elapsed,
                operation_name="L4-Judge Executive Recap Evaluation",
            )
            is_fallback = False
            raw_json = response.choices[0].message.content or "{}"
        except Exception as exc:
            check_deadline()
            from dual_pipeline.exceptions import PipelineTokenBudgetExhaustedError
            if isinstance(exc, PipelineTokenBudgetExhaustedError):
                raise
            err_msg = str(exc).lower()
            if any(w in err_msg for w in ("429", "insufficient_quota", "spend_limit", "spend limit", "rate_limit", "rate limit")):
                logger.warning("Provider 429 / spend limit in Recap Judge: %s. Routing to HITL (Wave 4).", exc)
                p_digest = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]
                return {
                    "status": "HITL_REQUIRED",
                    "verdict": "HITL_REQUIRED",
                    "composite_score": None,
                    "section_scores": {
                        "header_and_overview": 0,
                        "key_updates_technical_substance": 0,
                        "conditional_extensions": 0,
                        "appendix_stakeholder_perspectives": 0,
                    },
                    "defect_codes": ["ERR_PROVIDER_429_HITL"],
                    "executive_rationale": f"PROVIDER RATE LIMIT (429): Judge API rate limit / quota exceeded ({exc}). Routed to human-in-the-loop review.",
                    "actionable_remediation_diff": "Review rate limits or authorize release via operator decision.",
                    "judge_model": model,
                    "prompt_digest": p_digest,
                    "rubric_version": "2.0",
                    "sample_count": 1,
                    "is_fallback": True,
                }
            raise
        parsed = _validate_or_retry_recap_scorecard(
            raw_json, client, model, reasoning_effort, system_prompt, user_content, cfg
        )
        from .judge_serialization import enrich_eval_telemetry
        from config.credentials import DEFAULT_GEMINI_FAILOVER_MODEL
        enrich_eval_telemetry(
            parsed,
            provider="google" if is_fallback else "openai",
            model=DEFAULT_GEMINI_FAILOVER_MODEL if is_fallback else model,
            effort=reasoning_effort,
            latency_seconds=elapsed,
            is_fallback=is_fallback,
        )
        return parsed
