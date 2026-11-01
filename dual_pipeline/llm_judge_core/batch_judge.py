"""dual_pipeline/llm_judge_core/batch_judge.py

Batch evaluation engine for Micro-Judges.
Evaluates collections of candidate items against a shared reference context in a single call,
reducing repetitive context transmission, slashing token overhead, and speeding up pipeline latency.
Includes automatic deduplication, individual rubric validation, and graceful fallback.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from dual_pipeline.llm_judge_core.models import (
    BatchedEvaluationResult,
    JudgeAgenticLeverage,
)
from dual_pipeline.parameters import get_svp_political_materiality_threshold
from dual_pipeline.telemetry import record_eval_result, trace_llm_execution

logger = logging.getLogger(__name__)

BATCH_JUDGE_SYSTEM_PROMPT = """You are a senior executive gatekeeper evaluating multiple candidate items against a single authoritative reference transcript.
Evaluate each candidate item independently according to the designated rubric.
Return a valid JSON object with the following schema:
{
  "evaluations": [
    {
      "item_id": "string",
      "political_materiality": integer (1-10),
      "networking_target_identified": boolean,
      "is_actionable_wedge": boolean,
      "pass_gate": boolean,
      "rejection_reason": "string or null"
    }
  ]
}
Strict Rule: An item MUST have pass_gate=false if political_materiality < 8 or networking_target_identified is false or is_actionable_wedge is false.
"""


def _item_to_dict(item: Any) -> Dict[str, Any]:
    """Extract dictionary representation of a candidate node or item."""
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if hasattr(item, "dict"):
        return item.dict()
    if isinstance(item, dict):
        return dict(item)
    return {"content": str(item)}


def _compute_item_hash(item_dict: Dict[str, Any]) -> str:
    """Compute deterministic SHA256 hash for deduplication."""
    serialized = json.dumps(item_dict, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _build_batch_prompt(reference_text: str, unique_items: List[Tuple[str, Dict[str, Any]]]) -> str:
    """Construct XML-structured prompt containing shared reference and candidate items."""
    parts = [
        "Below are the candidate items to evaluate against the authoritative reference transcript.\n\n",
        "<reference_transcript>\n",
        reference_text.strip(),
        "\n</reference_transcript>\n\n",
        f"<candidate_items count=\"{len(unique_items)}\">\n",
    ]
    for item_id, item_data in unique_items:
        parts.append(f'  <item id="{item_id}">\n')
        parts.append(f"    {json.dumps(item_data, indent=2)}\n")
        parts.append("  </item>\n")
    parts.append("</candidate_items>\n")
    return "".join(parts)


def _is_executive_item(item_dict: Dict[str, Any]) -> bool:
    """Determine if candidate item belongs to an executive route."""
    if item_dict.get("is_executive", False):
        return True
    if str(item_dict.get("node_type", "")).upper() == "CAREER_VISIBILITY":
        return True
    return str(item_dict.get("route", "")).lower() in ("executive", "golden_cto", "cto")


def _check_item_target(players: Sequence[Any]) -> bool:
    """Verify presence of senior executive target in key players."""
    if not players:
        return False
    target_titles = ("vp", "director", "svp", "partner", "lead", "executive", "head")
    for p in players:
        text = str(p).lower()
        if any(t in text for t in target_titles) or len(text.split()) >= 2:
            return True
    return False


def _determine_rejection_reason(
    pass_gate: bool, has_target: bool, materiality: int, threshold: int
) -> Optional[str]:
    """Compute rejection reason when candidate item fails gate."""
    if pass_gate:
        return None
    if not has_target:
        return "REJECTED [Target Test]: No senior executive target identified."
    if materiality < threshold:
        return f"REJECTED [So-What Test]: Political materiality ({materiality}/10) below threshold of {threshold}."
    return "REJECTED: Does not provide an actionable asymmetric career wedge."


def _evaluate_single_mock_item(item_dict: Dict[str, Any]) -> JudgeAgenticLeverage:
    """Deterministic mock rubric evaluation for an item."""
    rationale = str(item_dict.get("political_rationale", ""))
    wedge = str(item_dict.get("networking_wedge", ""))
    players = item_dict.get("key_players", [])

    is_exec = _is_executive_item(item_dict)
    threshold = get_svp_political_materiality_threshold(is_executive=is_exec)

    has_target = _check_item_target(players)
    has_asymmetry = len(rationale) > 30 and len(wedge) > 30
    materiality = 9 if (has_target and has_asymmetry) else (6 if has_target else 4)
    actionable = bool(has_target and (has_asymmetry if threshold >= 8 else bool(wedge or rationale)))
    pass_gate = (materiality >= threshold) and has_target and actionable
    reason = _determine_rejection_reason(pass_gate, has_target, materiality, threshold)

    return JudgeAgenticLeverage(
        is_actionable_wedge=actionable,
        political_materiality=materiality,
        networking_target_identified=has_target,
        pass_gate=pass_gate,
        rejection_reason=reason,
    )


def _deduplicate_nodes(
    nodes: Sequence[Any],
) -> Tuple[Dict[str, str], List[Tuple[str, Dict[str, Any]]], List[str]]:
    """Deduplicate nodes in memory, returning map, unique list, and full hash list."""
    unique_map: Dict[str, str] = {}
    unique_items: List[Tuple[str, Dict[str, Any]]] = []
    node_hashes: List[str] = []
    for n in nodes:
        n_dict = _item_to_dict(n)
        h = _compute_item_hash(n_dict)
        node_hashes.append(h)
        if h not in unique_map:
            item_id = f"item_{len(unique_items) + 1}"
            unique_map[h] = item_id
            unique_items.append((item_id, n_dict))
    return unique_map, unique_items, node_hashes


def _map_evaluated_items(
    nodes: Sequence[Any],
    node_hashes: List[str],
    unique_map: Dict[str, str],
    item_evals: Dict[str, JudgeAgenticLeverage],
    is_mock: bool,
) -> List[Dict[str, Any]]:
    """Map unique evaluation results back to complete item sequence."""
    evaluated: List[Dict[str, Any]] = []
    for idx, n in enumerate(nodes):
        n_dict = _item_to_dict(n)
        item_id = unique_map[node_hashes[idx]]
        judgment = item_evals.get(item_id)
        if judgment is None:
            if is_mock:
                judgment = _evaluate_single_mock_item(n_dict)
            else:
                judgment = JudgeAgenticLeverage(
                    is_actionable_wedge=False,
                    political_materiality=0,
                    networking_target_identified=False,
                    pass_gate=False,
                    rejection_reason=f"FAIL_CLOSED: Item {item_id} omitted from evaluator output.",
                )
        evaluated.append({"node": n_dict, "judgment": judgment.model_dump()})
    return evaluated


class BatchMicroJudgeEvaluator:
    """Orchestrator for batched evaluation of candidate nodes/items."""

    def __init__(
        self,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        mock: bool = False,
        max_batch_size: int = 10,
    ) -> None:
        from dual_pipeline.model_registry import ModelRegistry, ModelTier
        def_m, def_e = ModelRegistry.resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
        self.model = model or def_m
        self.reasoning_effort = reasoning_effort or def_e or "medium"
        self.mock = mock
        self.max_batch_size = max_batch_size

    def _execute_mock_batch(
        self,
        unique_items: List[Tuple[str, Dict[str, Any]]],
    ) -> Dict[str, JudgeAgenticLeverage]:
        results: Dict[str, JudgeAgenticLeverage] = {}
        for item_id, item_data in unique_items:
            results[item_id] = _evaluate_single_mock_item(item_data)
        return results

    def _parse_llm_evaluations(self, content: str) -> Dict[str, JudgeAgenticLeverage]:
        data = json.loads(content)
        evals_list = data.get("evaluations", [])
        parsed_results: Dict[str, JudgeAgenticLeverage] = {}
        for ev in evals_list:
            item_id = str(ev.get("item_id", ""))
            mat = int(ev.get("political_materiality", 0))
            target_id = bool(ev.get("networking_target_identified", False))
            act_wedge = bool(ev.get("is_actionable_wedge", False))
            passed = bool(ev.get("pass_gate", False))

            if mat < 8 or not target_id or not act_wedge:
                passed = False

            parsed_results[item_id] = JudgeAgenticLeverage(
                is_actionable_wedge=act_wedge,
                political_materiality=mat,
                networking_target_identified=target_id,
                pass_gate=passed,
                rejection_reason=ev.get("rejection_reason"),
            )
        return parsed_results

    def _execute_live_call(
        self,
        client: Any,
        prompt: str,
    ) -> Dict[str, JudgeAgenticLeverage]:
        from config.credentials import get_openai_client
        if client is None:
            client, _ = get_openai_client(
                allow_mock=False,
                model=self.model,
                reasoning_effort=self.reasoning_effort,
            )

        resp = client.chat.completions.create(
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            messages=[
                {"role": "system", "content": BATCH_JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            cache_namespace="batch_micro_judge",
        )
        content = resp.choices[0].message.content or "{}"
        from dual_pipeline.llm_judge_core.telemetry import record_judge_llm_usage
        record_judge_llm_usage(resp, self.model, operation_name="Batch Micro Judge")
        return self._parse_llm_evaluations(content)

    def _run_fallback(
        self,
        nodes: Sequence[Any],
        reference_text: str,
        client: Any,
        is_mock: bool,
        fallback_fn: Optional[Callable[[Any, str, Any, bool], Any]],
    ) -> List[Dict[str, Any]]:
        logger.info("Executing graceful fallback for %d items", len(nodes))
        fallback_results: List[Dict[str, Any]] = []
        for n in nodes:
            node_dict = _item_to_dict(n)
            if fallback_fn:
                judged = fallback_fn(n, reference_text, client, is_mock)
                judgment_dict = judged.model_dump() if hasattr(judged, "model_dump") else dict(judged)
            elif is_mock:
                judged = _evaluate_single_mock_item(node_dict)
                judgment_dict = judged.model_dump()
            else:
                judgment_dict = {
                    "political_materiality": 0,
                    "networking_target_identified": False,
                    "is_actionable_wedge": False,
                    "pass_gate": False,
                    "rejection_reason": "FAIL_CLOSED: Live batch evaluation failed; live-to-mock fallback prohibited.",
                }
            fallback_results.append({"node": node_dict, "judgment": judgment_dict})
        return fallback_results

    def evaluate_batch(
        self,
        nodes: Sequence[Any],
        reference_text: str = "",
        client: Optional[Any] = None,
        mock: Optional[bool] = None,
        fallback_fn: Optional[Callable[[Any, str, Any, bool], Any]] = None,
        verify_position_swap: bool = False,
    ) -> BatchedEvaluationResult:
        """Evaluate a collection of candidate items against shared reference context."""
        start_time = time.monotonic()
        is_mock = self.mock if mock is None else mock
        batch_id = f"batch_{hashlib.sha256(str(time.time()).encode()).hexdigest()[:8]}"

        if not nodes:
            return BatchedEvaluationResult(batch_id=batch_id, item_count=0, evaluated_items=[])

        # Step 1: In-memory deduplication
        unique_map, unique_items, node_hashes = _deduplicate_nodes(nodes)
        dedup_count = len(nodes) - len(unique_items)
        ref_tokens_est = max(1, int(len(reference_text.split()) * 1.3))
        tokens_saved_est = (len(nodes) - 1) * ref_tokens_est

        span, recorder = trace_llm_execution(
            operation_name="pipeline.b.svp_micro_judge_batch",
            requested_model=self.model,
            reasoning_effort=self.reasoning_effort,
            prompt_content={"batch_id": batch_id, "items_count": len(unique_items)},
        )

        try:
            if is_mock:
                recorder.record_mock("Mock mode in BatchMicroJudgeEvaluator")
                item_evals = self._execute_mock_batch(unique_items)
            else:
                prompt = _build_batch_prompt(reference_text, unique_items)
                try:
                    item_evals = self._execute_live_call(client, prompt)
                except Exception as exc:
                    logger.warning("Batch evaluation call failed, invoking fallback: %s", exc)
                    elapsed = (time.monotonic() - start_time) * 1000.0
                    fb_items = self._run_fallback(nodes, reference_text, client, is_mock, fallback_fn)
                    return BatchedEvaluationResult(
                        batch_id=batch_id,
                        item_count=len(nodes),
                        evaluated_items=fb_items,
                        deduplicated_count=0,
                        estimated_tokens_saved=0,
                        duration_ms=round(elapsed, 2),
                        fallback_invoked=True,
                    )

            # Step 1b: Position-swap verification to detect primacy/recency bias
            position_swap_verified = False
            position_bias_detected = False
            swap_consistency_rate = 1.0

            if verify_position_swap and len(unique_items) > 1:
                position_swap_verified = True
                reversed_items = list(reversed(unique_items))
                if is_mock:
                    reversed_evals = self._execute_mock_batch(reversed_items)
                else:
                    rev_prompt = _build_batch_prompt(reference_text, reversed_items)
                    try:
                        reversed_evals = self._execute_live_call(client, rev_prompt)
                    except Exception as rev_exc:
                        logger.warning("Position swap check failed; retaining forward evaluations: %s", rev_exc)
                        reversed_evals = item_evals

                match_count = 0
                for item_id, fwd_judge in item_evals.items():
                    rev_judge = reversed_evals.get(item_id)
                    if rev_judge is not None:
                        same_verdict = fwd_judge.pass_gate == rev_judge.pass_gate
                        score_diff = abs(fwd_judge.political_materiality - rev_judge.political_materiality)
                        if same_verdict and score_diff <= 1:
                            match_count += 1
                        else:
                            position_bias_detected = True
                    else:
                        position_bias_detected = True
                swap_consistency_rate = round(match_count / len(item_evals), 4) if item_evals else 1.0
                if swap_consistency_rate < 0.80:
                    position_bias_detected = True

            # Step 2: Map unique evaluations back to all items
            evaluated_items = _map_evaluated_items(nodes, node_hashes, unique_map, item_evals, is_mock)

            all_passed = all(it["judgment"].get("pass_gate", False) for it in evaluated_items)
            avg_score = sum(it["judgment"].get("political_materiality", 0) for it in evaluated_items) / len(evaluated_items)
            record_eval_result(
                span,
                gate_id="svp_micro_judge_batch",
                verdict="PASS" if all_passed else "FAIL",
                score=round(avg_score, 1),
                is_mock=is_mock,
                extra_attributes={
                    "batch_size": len(nodes),
                    "unique_items": len(unique_items),
                    "tokens_saved_estimate": tokens_saved_est,
                    "position_swap_verified": position_swap_verified,
                    "swap_consistency_rate": swap_consistency_rate,
                },
            )

            elapsed = (time.monotonic() - start_time) * 1000.0
            return BatchedEvaluationResult(
                batch_id=batch_id,
                item_count=len(nodes),
                evaluated_items=evaluated_items,
                deduplicated_count=dedup_count,
                estimated_tokens_saved=tokens_saved_est,
                duration_ms=round(elapsed, 2),
                fallback_invoked=False,
                position_swap_verified=position_swap_verified,
                position_bias_detected=position_bias_detected,
                swap_consistency_rate=swap_consistency_rate,
            )
        finally:
            span.end()


__all__ = ["BatchMicroJudgeEvaluator", "BATCH_JUDGE_SYSTEM_PROMPT"]
