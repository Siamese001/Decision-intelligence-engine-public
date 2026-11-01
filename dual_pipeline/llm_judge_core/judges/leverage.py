"""AgenticLeverageJudge for internal advisory wedges and political dynamics."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Sequence, Union

from dual_pipeline.llm_judge_core.models import JudgeAgenticLeverage
from dual_pipeline.llm_judge_core.prompts import HARDENED_SVP_JUDGE_PROMPT
from dual_pipeline.parameters import (
    get_svp_political_materiality_threshold,
    is_deterministic_judge_prefilter_enabled,
)
from dual_pipeline.prompts import CANONICAL_PROMPT_CONFIG
from dual_pipeline.telemetry import (
    record_eval_result,
    record_quarantine_event,
    trace_llm_execution,
)

logger = logging.getLogger(__name__)


def _check_executive_target(players: Sequence[Any]) -> bool:
    """Identify if any player is a senior executive target."""
    if not players:
        return False
    target_titles = ("vp", "director", "svp", "partner", "lead", "managing director", "executive", "head")
    for p in players:
        text = str(p).lower()
        if any(t in text for t in target_titles) or len(text.split()) >= 2:
            return True
    return False


def _mock_rejection_reason(has_target: bool, materiality: int, threshold: int) -> str:
    """Compute deterministic mock rejection reason."""
    if not has_target:
        return "REJECTED [Target Test]: No senior executive target (Director/VP/SVP/Partner) identified."
    if materiality < threshold:
        return f"REJECTED [So-What Test]: Political materiality ({materiality}/10) below SVP threshold of {threshold}."
    return "REJECTED: Does not provide an actionable, socially calibrated asymmetric career wedge."


def _evaluate_mock_agentic_leverage(
    node_dict: Dict[str, Any],
    threshold: int,
    span: Any,
    recorder: Any,
) -> JudgeAgenticLeverage:
    """Deterministic mock evaluation logic."""
    recorder.record_mock("Mock mode in evaluate_agentic_leverage")
    rationale = node_dict.get("political_rationale", "")
    wedge = node_dict.get("networking_wedge", "")
    players = node_dict.get("key_players", [])

    has_target = _check_executive_target(players)
    has_asymmetry = len(rationale) > 30 and len(wedge) > 30

    materiality = 9 if (has_target and has_asymmetry) else (6 if has_target else 4)
    actionable = bool(has_target and (has_asymmetry if threshold >= 8 else bool(wedge or rationale)))
    pass_gate = (materiality >= threshold) and has_target and actionable

    rejection = None if pass_gate else _mock_rejection_reason(has_target, materiality, threshold)

    record_eval_result(
        span,
        gate_id="svp_micro_judge",
        verdict="PASS" if pass_gate else "FAIL",
        score=materiality,
        is_mock=True,
        extra_attributes={
            "target_identified": has_target,
            "is_actionable_wedge": actionable,
            "pass_gate": pass_gate,
        },
    )
    if not pass_gate:
        wedge_key = str(node_dict.get("speaker", "")) + str(node_dict.get("networking_wedge", ""))
        adv_node_id = str(node_dict.get("node_id") or f"adv:{hashlib.sha256(wedge_key.encode()).hexdigest()[:12]}")
        record_quarantine_event(
            span,
            node_id=adv_node_id,
            reason=rejection or "Failed SVP gate",
            rubric_scores={"political_materiality": materiality, "target_identified": has_target},
        )

    return JudgeAgenticLeverage(
        is_actionable_wedge=actionable,
        political_materiality=materiality,
        networking_target_identified=has_target,
        pass_gate=pass_gate,
        rejection_reason=rejection,
    )


def _check_deterministic_prefilter(
    node_dict: Dict[str, Any],
    span: Any,
) -> Optional[JudgeAgenticLeverage]:
    """Pre-filter nodes lacking essential keys before paid LLM call."""
    if not is_deterministic_judge_prefilter_enabled():
        return None
    players = node_dict.get("key_players", [])
    target = str(node_dict.get("target_stakeholder") or (players[0] if players else "")).strip()
    wedge = str(node_dict.get("networking_wedge") or "").strip()
    if not wedge or not target:
        rejection = "Deterministic pre-filter: missing networking_wedge or target_stakeholder."
        record_eval_result(
            span,
            gate_id="svp_micro_judge",
            verdict="FAIL",
            score=0,
            is_mock=False,
            extra_attributes={
                "target_identified": bool(target),
                "is_actionable_wedge": bool(wedge),
                "pass_gate": False,
                "pre_filter_rejected": True,
            },
        )
        return JudgeAgenticLeverage(
            is_actionable_wedge=bool(wedge),
            political_materiality=0,
            networking_target_identified=bool(target),
            pass_gate=False,
            rejection_reason=rejection,
        )
    return None


def _resolve_live_client(model: str, reasoning_effort: str) -> Any:
    """Resolve live client respecting zero mocks invariant."""
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


def _invoke_live_judge_llm(
    client: Any,
    model: str,
    reasoning_effort: str,
    transcript_text: str,
    node_dict: Dict[str, Any],
    threshold: int,
) -> Any:
    """Invoke live OpenAI completion with bounded prompt context."""
    from dual_pipeline.token_optimization import TokenProfiler
    profiler = TokenProfiler(default_model=model)
    compact_node_json = profiler.compact_json(node_dict)
    bounded_transcript = profiler.truncate_to_token_limit(
        transcript_text, max_tokens=CANONICAL_PROMPT_CONFIG.output1.max_tokens, model=model
    )

    system_prompt = f"""{HARDENED_SVP_JUDGE_PROMPT}

Return JSON:
{{
  "is_actionable_wedge": <bool>,
  "political_materiality": <int 1-10>,
  "networking_target_identified": <bool>,
  "pass_gate": <bool: MUST be FALSE if political_materiality < {threshold} OR networking_target_identified is False>,
  "rejection_reason": <string or null: Concisely state why this node fails if pass_gate is false>
}}"""

    user_content = f"""MEETING TRANSCRIPT CONTEXT:
{bounded_transcript}

PROPOSED AGENTIC LEVERAGE NODE:
{compact_node_json}

Evaluate this node against the three gates."""

    from config.request_deadline import remaining_timeout

    return client.chat.completions.create(
        model=model,
        reasoning_effort=reasoning_effort,
        messages=[
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": user_content.strip()},
        ],
        max_completion_tokens=CANONICAL_PROMPT_CONFIG.judge.max_completion_tokens,
        response_format={"type": "json_object"},
        cache_namespace="svp_leverage_judge",
        timeout=remaining_timeout(180.0),
    )


def _parse_and_validate_live_response(
    resp: Any,
    threshold: int,
    span: Any,
    node_dict: Dict[str, Any],
) -> JudgeAgenticLeverage:
    """Parse JSON response and enforce hard gate constraints."""
    raw = resp.choices[0].message.content or "{}"
    result_dict = json.loads(raw)

    mat = int(result_dict.get("political_materiality", 0))
    target_id = bool(result_dict.get("networking_target_identified", False))
    act_wedge = bool(result_dict.get("is_actionable_wedge", False))
    passed = bool(result_dict.get("pass_gate", False))

    if mat < threshold or not target_id or not act_wedge:
        passed = False
        if not result_dict.get("rejection_reason"):
            result_dict["rejection_reason"] = (
                f"Hard gate failed: political_materiality={mat} (<{threshold}), "
                f"networking_target_identified={target_id}, or is_actionable_wedge={act_wedge}."
            )

    record_eval_result(
        span,
        gate_id="svp_micro_judge",
        verdict="PASS" if passed else "FAIL",
        score=mat,
        is_mock=False,
        extra_attributes={
            "target_identified": target_id,
            "is_actionable_wedge": act_wedge,
            "pass_gate": passed,
        },
    )
    if not passed:
        wedge_key = str(node_dict.get("speaker", "")) + str(node_dict.get("networking_wedge", ""))
        adv_node_id = str(node_dict.get("node_id") or f"adv:{hashlib.sha256(wedge_key.encode()).hexdigest()[:12]}")
        record_quarantine_event(
            span,
            node_id=adv_node_id,
            reason=result_dict.get("rejection_reason", "Hard gate failed"),
            rubric_scores={"political_materiality": mat, "target_identified": target_id},
        )

    return JudgeAgenticLeverage(
        is_actionable_wedge=act_wedge,
        political_materiality=mat,
        networking_target_identified=target_id,
        pass_gate=passed,
        rejection_reason=result_dict.get("rejection_reason"),
    )


class AgenticLeverageJudge:
    """Specialized Micro-Judge for restricted internal advisory wedges and political dynamics."""

    def __init__(
        self,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        mock: bool = False,
    ):
        from dual_pipeline.model_registry import ModelRegistry, ModelTier
        def_m, def_e = ModelRegistry.resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
        self.model = model or def_m
        self.reasoning_effort = reasoning_effort or def_e or "medium"
        self.mock = mock

    def get_borderline_band(self, priors_path: Optional[Any] = None) -> Tuple[float, float]:
        from dual_pipeline.llm_judge_core.calibration_protocol import (
            get_calibrated_borderline_band,
        )
        return get_calibrated_borderline_band("leverage", output_num=2, priors_path=priors_path)

    def evaluate_agentic_leverage(
        self,
        node: Union[Dict[str, Any], Any],
        transcript_text: str = "",
        client: Optional[Any] = None,
        mock: Optional[bool] = None,
    ) -> JudgeAgenticLeverage:
        """Evaluate a proposed AgenticLeverageNode against the cutthroat SVP gatekeeper rubric."""
        is_mock = self.mock if mock is None else mock
        node_dict = (
            node.model_dump()
            if hasattr(node, "model_dump")
            else (node.dict() if hasattr(node, "dict") else dict(node))
        )
        node_type = str(node_dict.get("node_type", "")).upper()
        is_executive = bool(
            node_dict.get("is_executive", False)
            or str(node_dict.get("route", "")).lower() in ("executive", "golden_cto", "cto")
            or node_type == "CAREER_VISIBILITY"
        )
        threshold = get_svp_political_materiality_threshold(is_executive=is_executive)

        span, recorder = trace_llm_execution(
            operation_name="pipeline.b.svp_micro_judge",
            requested_model=self.model,
            reasoning_effort=self.reasoning_effort,
            prompt_content=node_dict,
        )
        try:
            if is_mock:
                return _evaluate_mock_agentic_leverage(node_dict, threshold, span, recorder)

            if client is None:
                client = _resolve_live_client(self.model, self.reasoning_effort)

            prefilter_result = _check_deterministic_prefilter(node_dict, span)
            if prefilter_result is not None:
                return prefilter_result

            resp = _invoke_live_judge_llm(
                client, self.model, self.reasoning_effort, transcript_text, node_dict, threshold
            )
            recorder.record_response(resp)
            from dual_pipeline.llm_judge_core.telemetry import record_judge_llm_usage
            record_judge_llm_usage(resp, model=self.model, operation_name="L4-Judge Agentic Leverage Evaluation")
            return _parse_and_validate_live_response(resp, threshold, span, node_dict)
        except Exception as exc:
            if isinstance(exc, RuntimeError) and "Zero mocks invariant violated" in str(exc):
                raise
            recorder.record_error(exc)
            logger.error("evaluate_agentic_leverage failed: %s", exc)
            return JudgeAgenticLeverage(
                is_actionable_wedge=False,
                political_materiality=0,
                networking_target_identified=False,
                pass_gate=False,
                rejection_reason=f"Evaluation failed: {exc}",
            )
        finally:
            span.end()

    def evaluate_agentic_nodes(
        self,
        nodes: List[Any],
        transcript_text: str = "",
        client: Optional[Any] = None,
        mock: Optional[bool] = None,
        batch: bool = True,
    ) -> List[Dict[str, Any]]:
        """Evaluate a collection of AgenticLeverageNodes and return detailed scorecards."""
        if not nodes:
            return []
        is_mock = self.mock if mock is None else mock
        if batch and len(nodes) > 1:
            from dual_pipeline.llm_judge_core.batch_judge import BatchMicroJudgeEvaluator
            batch_evaluator = BatchMicroJudgeEvaluator(
                model=self.model,
                reasoning_effort=self.reasoning_effort,
                mock=is_mock,
            )
            batch_res = batch_evaluator.evaluate_batch(
                nodes=nodes,
                reference_text=transcript_text,
                client=client,
                mock=is_mock,
                fallback_fn=self.evaluate_agentic_leverage,
            )
            return batch_res.evaluated_items

        scorecards = []
        for n in nodes:
            judged = self.evaluate_agentic_leverage(n, transcript_text, client, is_mock)
            node_dict = (
                n.model_dump()
                if hasattr(n, "model_dump")
                else (n.dict() if hasattr(n, "dict") else dict(n))
            )
            scorecards.append({
                "node": node_dict,
                "judgment": judged.model_dump(),
            })
        return scorecards
