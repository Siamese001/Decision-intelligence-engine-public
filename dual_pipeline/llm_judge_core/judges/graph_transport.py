"""Graph judge transport with deadline propagation and observed model provenance."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

from config.request_deadline import check_deadline, cooperative_sleep, deadline_scope, remaining_timeout, without_sdk_retries
from config.transport import execute_with_deadline_and_heartbeat
from dual_pipeline.prompts import CANONICAL_PROMPT_CONFIG


def _call_openai(client, model, effort, system_prompt, user_content, token_limit, transient):
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
                model=model, reasoning_effort=effort,
                messages=[{"role": "system", "content": system_prompt.strip()},
                          {"role": "user", "content": user_content.strip()}],
                max_completion_tokens=token_limit, response_format={"type": "json_object"},
                timeout=remaining_timeout(cfg.deadline_seconds),
                cache_namespace="judge_graph",
            )
            check_deadline()
            return response
        except Exception as exc:
            check_deadline()
            from dual_pipeline.exceptions import PipelineTokenBudgetExhaustedError
            if isinstance(exc, PipelineTokenBudgetExhaustedError):
                raise
            if not transient(exc) or attempt == 2:
                raise
            cooperative_sleep(2.0 * (attempt + 1))


def generate_client_graph(client, model, reasoning_effort, system_prompt, user_content, recorder,
                          max_completion_tokens, assign_verdict, transient):
    """The fallback shares the first attempt's clock; never replaces a default model."""
    cfg = CANONICAL_PROMPT_CONFIG.judge
    with deadline_scope(cfg.deadline_seconds):
        execution = {"requested_model": model, "requested_effort": reasoning_effort}
        t_start = time.perf_counter()
        try:
            response = execute_with_deadline_and_heartbeat(
                _call_openai, client, model, reasoning_effort, system_prompt, user_content,
                max_completion_tokens, transient, deadline_seconds=cfg.deadline_seconds,
                heartbeat_interval=cfg.heartbeat_interval,
                operation_name="L4-Judge Graph Extraction Evaluation",
            )
            check_deadline()
            elapsed = max(0.001, time.perf_counter() - t_start)
            recorder.record_response(response)
            from dual_pipeline.llm_judge_core.telemetry import record_judge_llm_usage
            record_judge_llm_usage(response, model=model, duration_seconds=elapsed, operation_name="L4-Judge Graph Extraction Evaluation")
            raw_json = response.choices[0].message.content or "{}"
            actual = getattr(response, "model", None)
            actual = actual if isinstance(actual, str) and actual else None
            revision = getattr(response, "model_revision", None)
            if isinstance(revision, str) and revision:
                execution["observed_revision"] = revision
            execution.update(effective_model=actual or model, effective_effort=reasoning_effort,
                             model_identity="response" if actual else "configured_only", status="COMPLETED")
        except Exception as exc:
            check_deadline()
            from dual_pipeline.exceptions import PipelineTokenBudgetExhaustedError
            if isinstance(exc, PipelineTokenBudgetExhaustedError):
                raise
            if any(word in str(exc).lower() for word in
                   ("429", "insufficient_quota", "spend_limit", "spend limit", "rate_limit", "rate limit")):
                logger.warning("Provider 429 / spend limit in Graph Judge: %s. Routing to HITL (Wave 4).", exc)
                p_digest = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]
                return {
                    "status": "HITL_REQUIRED",
                    "verdict": "HITL_REQUIRED",
                    "composite_score": None,
                    "dimension_1_strategic_edge": 0,
                    "dimension_2_knowledge_layer_density": 0,
                    "dimension_3_semantic_atomicity": 0,
                    "dimension_4_predicate_specificity": 0,
                    "dimension_5_exclusion_of_action_items": 0,
                    "dimension_6_evidence_grounding_and_schema": 0,
                    "defect_codes": ["ERR_PROVIDER_429_HITL"],
                    "graph_rationale": f"PROVIDER RATE LIMIT (429): Judge API rate limit / quota exceeded ({exc}). Routed to human-in-the-loop review.",
                    "actionable_remediation_diff": "Review rate limits or authorize release via operator decision.",
                    "judge_model": model,
                    "prompt_digest": p_digest,
                    "rubric_version": "2.0",
                    "sample_count": 1,
                    "is_fallback": True,
                }
            raise
        parsed = assign_verdict(json.loads(raw_json))
        parsed["execution"] = execution
        from .judge_serialization import enrich_eval_telemetry
        enrich_eval_telemetry(
            parsed,
            provider="google" if execution.get("fallback") else "openai",
            model=execution.get("effective_model", model),
            effort=execution.get("effective_effort") or reasoning_effort,
            latency_seconds=max(0.001, time.perf_counter() - t_start),
            is_fallback=bool(execution.get("fallback")),
        )
        return parsed


class GraphTransportJudge:
    """Micro-judge ensuring graph LLM transport adheres to deadline propagation and provenance logging."""

    def __init__(self, priors_path: Optional[Any] = None) -> None:
        self.priors_path = priors_path

    def get_borderline_band(self, priors_path: Optional[Any] = None) -> Tuple[float, float]:
        from dual_pipeline.llm_judge_core.calibration_protocol import (
            get_calibrated_borderline_band,
        )
        p_path = priors_path if priors_path is not None else self.priors_path
        return get_calibrated_borderline_band("graph_transport", output_num=3, priors_path=p_path)
