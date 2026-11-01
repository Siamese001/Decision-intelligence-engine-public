"""
dual_pipeline/llm_judge_core/enterprise.py

Tier 2: Specialized Post-Activation Strategic Architecture Judge.
Evaluates canonical graph projections / snapshots against the 10 Strategic Intelligence Dimensions
of the Executive Cognitive Exoskeleton ($1.2M Benchmark).
Emits a standalone governance audit report. NEVER mutates Output 03 or CANONICAL_GRAPH.db.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

from dual_pipeline.llm_judge_core.prompts import EXECUTIVE_EXOSKELETON_REVIEW_PROMPT
from dual_pipeline.prompts import CANONICAL_PROMPT_CONFIG
from dual_pipeline.telemetry import record_eval_result, trace_llm_execution
from dual_pipeline.token_optimization.profiler import TokenProfiler

logger = logging.getLogger(__name__)


def _resolve_enterprise_client(model: str, reasoning_effort: str) -> Any:
    """Resolve live OpenAI client fail-closed under Zero Mocks Invariant."""
    from config.credentials import get_openai_client
    try:
        client, creds = get_openai_client(
            allow_mock=False,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    except ValueError as exc:
        raise RuntimeError(
            f"Zero mocks invariant violated: Live OpenAI client required for {model}, "
            f"but credentials missing: {exc}"
        ) from exc
    if creds.is_mock or client is None:
        raise RuntimeError(
            f"Zero mocks invariant violated: Live OpenAI client required for {model}, "
            f"but credentials resolved with is_mock={getattr(creds, 'is_mock', True)}."
        )
    return client


def _execute_enterprise_llm_call(
    client: Any,
    model: str,
    reasoning_effort: str,
    prompt: Optional[str] = None,
    recorder: Any = None,
    system_prompt: Optional[str] = None,
    user_content: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute LLM chat completion for enterprise exoskeleton review with prompt caching."""
    if system_prompt and user_content:
        messages = [
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": user_content.strip()},
        ]
    elif prompt and "CANONICAL GRAPH PROJECTION:" in prompt:
        parts = prompt.split("CANONICAL GRAPH PROJECTION:", 1)
        sys_part = parts[0].strip()
        user_part = "CANONICAL GRAPH PROJECTION:" + parts[1]
        messages = [
            {"role": "system", "content": sys_part},
            {"role": "user", "content": user_part},
        ]
    else:
        messages = [{"role": "user", "content": (prompt or user_content or "").strip()}]

    from config.request_deadline import remaining_timeout

    resp = client.chat.completions.create(
        model=model,
        reasoning_effort=reasoning_effort,
        messages=messages,
        max_completion_tokens=CANONICAL_PROMPT_CONFIG.judge.max_completion_tokens,
        response_format={"type": "json_object"},
        cache_namespace="enterprise_graph_review_judge",
        timeout=remaining_timeout(180.0),
    )
    recorder.record_response(resp)
    raw_content = resp.choices[0].message.content or "{}"
    parsed = json.loads(raw_content)
    parsed["schema_version"] = "enterprise-graph-review-1.0"
    parsed["canonical_mutation"] = False
    if "evaluated_at" not in parsed:
        parsed["evaluated_at"] = datetime.now(timezone.utc).isoformat()
    return parsed


class EnterpriseGraphReviewJudge:
    """Specialized micro-judge for scoring the enterprise scaffolding, consulting leverage,
    and business capability projection of Output 3.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        mock: bool = False,
    ) -> None:
        from dual_pipeline.model_registry import ModelRegistry, ModelTier
        default_model, default_effort = ModelRegistry.resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
        self.model = model or default_model
        self.reasoning_effort = reasoning_effort or default_effort or "medium"
        self.mock = mock

    def get_borderline_band(self, priors_path: Optional[Any] = None) -> Tuple[float, float]:
        from dual_pipeline.llm_judge_core.calibration_protocol import (
            get_calibrated_borderline_band,
        )
        return get_calibrated_borderline_band("enterprise", output_num=3, priors_path=priors_path)

    def _build_mock_result(self) -> Dict[str, Any]:
        """Produce deterministic mock review result."""
        return {
            "schema_version": "enterprise-graph-review-1.0",
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "maturity_band": "Level 4 (Strategic Co-Pilot)",
            "composite_score": 88,
            "dimension_scores": {
                "dimension_1_business_capability_depth": 9,
                "dimension_2_multi_hop_latent_linkage": 9,
                "dimension_3_cross_silo_contradiction_detection": 8,
                "dimension_4_dual_audience_consulting_leverage": 9,
                "dimension_5_conways_law_organizational_topology": 9,
                "dimension_6_ingestion_signal_fidelity_alpha": 9,
                "dimension_7_bi_temporal_rigor_provider_free": 10,
                "dimension_8_dialectical_decision_intelligence": 8,
                "dimension_9_board_level_synthesis_next_best_action": 9,
                "dimension_10_multi_client_federation_scalability": 8,
            },
            "executive_summary": (
                "Canonical projection exhibits strong enterprise capability mapping, provider-free "
                "bi-temporal boundaries, and dual-audience consulting leverage."
            ),
            "strategic_recommendations": [
                "Expand latent linkage by connecting technical leads to downstream business value streams.",
                "Formalize CapEx/OpEx financialization as cloud inference telemetry metrics mature.",
            ],
            "canonical_mutation": False,
        }

    def evaluate(
        self,
        canonical_graph_data: Union[Dict[str, Any], str],
        context_text: str = "",
        client: Optional[Any] = None,
        mock: Optional[bool] = None,
    ) -> Dict[str, Any]:
        is_mock = self.mock if mock is None else mock
        span, recorder = trace_llm_execution(
            operation_name="pipeline.b.enterprise_graph_review_judge",
            requested_model=self.model,
            reasoning_effort=self.reasoning_effort,
            prompt_content=str(canonical_graph_data)[:1000],
        )
        try:
            if is_mock:
                recorder.record_mock("Mock mode in EnterpriseGraphReviewJudge")
                res = self._build_mock_result()
                record_eval_result(
                    span,
                    gate_id="enterprise_graph_review_judge",
                    verdict="PASS" if res.get("composite_score", 0) >= 80 else "FAIL",
                    score=res.get("composite_score"),
                    is_mock=True,
                    extra_attributes={"maturity_band": res.get("maturity_band")},
                )
                return res

            if client is None:
                client = _resolve_enterprise_client(self.model, self.reasoning_effort)

            if isinstance(canonical_graph_data, (dict, list)):
                graph_json = TokenProfiler.compact_json(canonical_graph_data)
            else:
                graph_json = str(canonical_graph_data)

            profiler = TokenProfiler(default_model=self.model)
            graph_json_bounded = profiler.truncate_to_token_limit(
                graph_json, max_tokens=CANONICAL_PROMPT_CONFIG.output3.max_tokens, model=self.model
            )
            context_text_bounded = profiler.truncate_to_token_limit(
                context_text, max_tokens=CANONICAL_PROMPT_CONFIG.output1.max_tokens, model=self.model
            )
            prompt = EXECUTIVE_EXOSKELETON_REVIEW_PROMPT.format(
                canonical_graph_json=graph_json_bounded,
                context_text=context_text_bounded,
            )
            parsed = _execute_enterprise_llm_call(
                client=client,
                model=self.model,
                reasoning_effort=self.reasoning_effort,
                prompt=prompt,
                recorder=recorder,
            )
            score = parsed.get("composite_score", 0) or 0
            record_eval_result(
                span,
                gate_id="enterprise_graph_review_judge",
                verdict="PASS" if score >= 80 else "FAIL",
                score=score,
                is_mock=False,
                extra_attributes={"maturity_band": parsed.get("maturity_band")},
            )
            return parsed
        except Exception as exc:
            if isinstance(exc, RuntimeError) and "Zero mocks invariant violated" in str(exc):
                raise
            recorder.record_error(exc)
            logger.error("EnterpriseGraphReviewJudge failed: %s", exc)
            return {
                "schema_version": "enterprise-graph-review-1.0",
                "composite_score": 0,
                "maturity_band": "Level 1 (Tactical Logging)",
                "executive_summary": f"Evaluation failed: {exc}",
                "canonical_mutation": False,
            }
        finally:
            span.end()


EnterpriseImpactJudge = EnterpriseGraphReviewJudge

__all__ = [
    "EnterpriseGraphReviewJudge",
    "EnterpriseImpactJudge",
]
