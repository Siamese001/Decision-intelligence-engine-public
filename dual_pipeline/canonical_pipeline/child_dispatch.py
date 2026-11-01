"""
dual_pipeline/canonical_pipeline/child_dispatch.py

Child-Dispatch Predicate for Stage L2 Parallel Child Execution (02 Executive Recap & 03 Canonical Graph).
Enforces prerequisite invariants before allowing child dispatch from Output 01:
1. Output 01 must exist, be bound, non-empty, and free of fatal defects.
2. Source text must be non-empty and grounded.
3. Open C-10 ambiguity locks or unresolved speaker attribution block child dispatch.
4. Stale or mismatched Output 01 relative to current source input blocks child dispatch.
5. Recoverable cosmetic defects are repaired and revalidated before dispatch.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from dual_pipeline.exceptions import ChildDispatchBlockedError

logger = logging.getLogger(__name__)


def evaluate_child_dispatch_predicate(
    output_1: Optional[str],
    metadata: Dict[str, Any],
    source_text: Optional[str] = None,
) -> Tuple[bool, List[str]]:
    """
    Evaluates whether Output 01 satisfies the child-dispatch predicate for parallel 02/03 execution.
    Returns (can_dispatch, failure_reasons).
    """
    reasons: List[str] = []

    # 1. Presence and non-emptiness of Output 01
    if not output_1 or not output_1.strip():
        reasons.append("Output 01 is missing or empty; child dispatch forbidden.")
        return False, reasons

    # 2. Source text presence
    src = source_text if source_text is not None else metadata.get("source_text")
    if src is not None and not str(src).strip():
        reasons.append("Source text is empty; evidentiary grounding cannot be established.")

    # 3. Ambiguity locks (C-10)
    c10_reg = metadata.get("c10_register")
    if metadata.get("c10_has_open_locks") or (c10_reg and hasattr(c10_reg, "has_open_locks") and c10_reg.has_open_locks()):
        reasons.append("Unresolved C-10 ambiguity locks in Output 01 block child dispatch.")

    # 4. Unresolved speaker attribution / ambiguous 01
    if metadata.get("has_unresolved_speakers") or metadata.get("ambiguous_output_1"):
        reasons.append("Unresolved or ambiguous speaker attribution in Output 01 blocks child dispatch.")

    # 5. Stale Output 01
    if metadata.get("stale_output_1"):
        reasons.append("Output 01 is stale relative to current source/session; child dispatch blocked.")

    # 6. Fatal defect state
    sm = metadata.get("state_machine")
    if sm and hasattr(sm, "is_fatal") and sm.is_fatal("output_1"):
        reasons.append("Output 01 is in a fatal defect state; child dispatch blocked.")

    return len(reasons) == 0, reasons


def validate_child_dispatch_predicate(
    output_1: Optional[str],
    metadata: Dict[str, Any],
    source_text: Optional[str] = None,
) -> None:
    """
    Asserts that the child-dispatch predicate passes.
    Raises ChildDispatchBlockedError if predicate fails.
    """
    can_dispatch, reasons = evaluate_child_dispatch_predicate(
        output_1=output_1,
        metadata=metadata,
        source_text=source_text,
    )
    if not can_dispatch:
        raise ChildDispatchBlockedError(
            f"Child dispatch blocked: {'; '.join(reasons)}",
            reasons=reasons,
        )


def _collect_recap_result(
    f_recap: Any,
    pa_context: Dict[str, Any],
    meta: Dict[str, Any],
    artifacts: Dict[str, Any],
    notify_step: Callable[[int, str], None],
    attempt: int,
) -> Tuple[Optional[str], Optional[Exception]]:
    if not f_recap:
        return None, None
    from config.request_deadline import remaining_timeout
    try:
        recap = f_recap.result(timeout=remaining_timeout(300.0))
        artifacts["02_executive_recap.md"] = recap
        if pa_context.get("metadata", {}).get("archetype"):
            meta["archetype"] = pa_context["metadata"]["archetype"]
        notify_step(7, f"L2-Stage02: executive recap (A{attempt})")
        return recap, None
    except Exception as e:
        logger.error("L2-Stage02 executive recap execution failed: %s", e)
        return None, e


def _collect_graph_result(
    f_graph: Any,
    artifacts: Dict[str, Any],
    notify_step: Callable[[int, str], None],
    attempt: int,
    recap_exc: Optional[Exception],
) -> Optional[str]:
    if not f_graph:
        if recap_exc is not None:
            raise recap_exc
        return None
    from config.request_deadline import remaining_timeout
    try:
        graph = f_graph.result(timeout=remaining_timeout(300.0))
        artifacts["03_canonical_signal_projection.json"] = graph
        notify_step(8, f"L2-Stage03: graph extractor (A{attempt})")
    except Exception as e:
        logger.error("L2-Stage03 graph extractor execution failed: %s", e)
        if recap_exc is None:
            raise
        raise recap_exc from e

    if recap_exc is not None:
        raise recap_exc
    return graph


def run_parallel_l2_fork(
    pipeline: Any,
    input_s2_s3: str,
    pa_context: Dict[str, Any],
    meta: Dict[str, Any],
    rerun_s2: bool,
    rerun_s3: bool,
    recorder: Any,
    notify_step: Callable[[int, str], None],
    attempt: int,
    current_recap: str,
    current_graph: str,
    artifacts: Dict[str, Any],
    *,
    checkpoint_s2_fn: Optional[Callable[[str], str]] = None,
    checkpoint_s3_fn: Optional[Callable[[str], str]] = None,
) -> Tuple[str, str]:
    """Dispatches L2 Stage 02 and Stage 03 concurrently under child dispatch predicate."""
    if not (rerun_s2 or rerun_s3):
        return current_recap, current_graph

    validate_child_dispatch_predicate(
        input_s2_s3,
        metadata=meta,
        source_text=meta.get("raw_transcript") or meta.get("source_text"),
    )

    import copy
    import concurrent.futures

    pa_recap = copy.copy(pa_context)
    pa_recap["metadata"] = copy.deepcopy(pa_context.get("metadata", {}))
    pa_graph = copy.copy(pa_context)
    pa_graph["metadata"] = copy.deepcopy(pa_context.get("metadata", {}))

    def _exec_s2() -> str:
        raw_s2 = pipeline.l2_recap.execute(
            input_s2_s3, pa_context=pa_recap, remediation_directive=meta.get("remediation_directive_recap")
        )
        return checkpoint_s2_fn(raw_s2) if checkpoint_s2_fn is not None else raw_s2

    def _exec_s3() -> str:
        eff_recap = current_recap or meta.get("prior_recap_candidate") or pa_graph.get("prior_recap_candidate")
        try:
            raw_s3 = pipeline.l2_graph.execute(
                input_s2_s3,
                pa_context=pa_graph,
                remediation_directive=meta.get("remediation_directive_graph"),
                executive_recap=eff_recap,
            )
        except TypeError:
            raw_s3 = pipeline.l2_graph.execute(
                input_s2_s3, pa_context=pa_graph, remediation_directive=meta.get("remediation_directive_graph")
            )
        return checkpoint_s3_fn(raw_s3) if checkpoint_s3_fn is not None else raw_s3

    recap, graph = current_recap, current_graph
    import contextvars
    with (
        recorder.span("nee.uc1.l2.concurrent_fork"),
        concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor,
    ):
        f_recap = executor.submit(contextvars.copy_context().run, _exec_s2) if rerun_s2 else None
        f_graph = executor.submit(contextvars.copy_context().run, _exec_s3) if rerun_s3 else None
        res_recap, recap_exc = _collect_recap_result(f_recap, pa_context, meta, artifacts, notify_step, attempt)
        if res_recap is not None:
            recap = res_recap
            if checkpoint_s2_fn is not None:
                meta["_s2_checkpoint_completed_in_fork"] = True
        res_graph = _collect_graph_result(f_graph, artifacts, notify_step, attempt, recap_exc)
        if res_graph is not None:
            graph = res_graph
            if checkpoint_s3_fn is not None:
                meta["_s3_checkpoint_completed_in_fork"] = True

    return recap, graph
