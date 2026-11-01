"""
dual_pipeline/live_meeting_recorder_core/streaming_copilot.py

Track 2: Low-Latency Streaming Deep Reasoning Handler.
Streams token deltas from gpt-6-luna / gpt-5.6-astra to provide immediate progressive
spoken script delivery (<1200ms first token) before full JSON response sealing.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .copilot_validator import validate_copilot_feedback

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.streaming_copilot")


def _extract_chunk_content(chunk: Any) -> Optional[str]:
    choices = getattr(chunk, "choices", None)
    if not choices:
        return None
    delta = getattr(choices[0], "delta", None)
    if not delta:
        return None
    return getattr(delta, "content", None)


def _dispatch_token(
    on_token: Optional[Callable[[str], None]],
    fence: Optional[Any],
    content: str,
) -> None:
    if not on_token:
        return
    if fence is not None:
        fence.invoke(on_token, content)
        return
    try:
        on_token(content)
    except Exception:
        pass


def _consume_stream_chunks(
    stream_response: Any,
    on_token: Optional[Callable[[str], None]],
    t0: float,
    fence: Optional[Any] = None,
    snapshot: Optional[Any] = None,
    current_generation_id: Optional[str] = None,
) -> Tuple[List[str], Optional[float]]:
    first_token_ms: Optional[float] = None
    accumulated: List[str] = []
    gen_id = current_generation_id or (getattr(snapshot, "generation_id", None) if snapshot else "")
    for idx, chunk in enumerate(stream_response):
        if snapshot is not None and idx % 8 == 0 and not snapshot.is_valid(gen_id, time.time()):
            break
        content = _extract_chunk_content(chunk)
        if not content:
            continue
        if first_token_ms is None:
            first_token_ms = (time.perf_counter() - t0) * 1000.0
        accumulated.append(content)
        _dispatch_token(on_token, fence, content)
    return accumulated, first_token_ms


def _parse_stream_json(raw_json: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(raw_json)
    except json.JSONDecodeError:
        clean = re_repair_json(raw_json)
        if clean:
            try:
                return json.loads(clean)
            except Exception:
                return None
    return None


class StreamingCopilotHandler:
    """Manages streaming chat completion requests and progressive token emission."""

    def __init__(
        self,
        client: Any,
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        request_timeout: float = 7.0,
    ) -> None:
        self.client = client
        if model_id:
            self.model_id = model_id
            self.reasoning_effort = reasoning_effort or "medium"
        else:
            from dual_pipeline.model_registry import ModelTier, resolve_copilot_tier_model
            self.model_id, default_effort = resolve_copilot_tier_model(ModelTier.COPILOT_FAST_SNAP)
            self.reasoning_effort = reasoning_effort or default_effort
        self.request_timeout = request_timeout

        from dual_pipeline.l0.runtime_gate_preservation import CallbackFence
        self.fence = CallbackFence(timeout_sec=self.request_timeout)
        self.last_stream_outcome: str = "IDLE"

    def shutdown(self) -> None:
        """Shuts down the streaming handler, fencing all future callbacks."""
        self.fence.shutdown()

    def cancel(self) -> None:
        """Cancels active streaming execution and fences all callbacks."""
        self.fence.cancel()

    def _is_preflight_active(
        self,
        fence: Optional[Any],
        snapshot: Optional[Any],
        generation_id: str,
    ) -> bool:
        active_fence = fence or self.fence
        if not active_fence.is_active:
            return False
        if snapshot and not snapshot.is_valid(generation_id, time.time()):
            return False
        return True

    def _package_validated_feedback(
        self,
        parsed: Dict[str, Any],
        graph_context: Optional[str],
        in_memory_graph: Optional[Any],
        inflection_event: Optional[Any],
        verified_assertions: Optional[List[Dict[str, Any]]],
        rigor_tier: Optional[str],
        engagement_id: Optional[str],
        request_snapshot: Optional[Any],
        first_token_ms: Optional[float],
        total_ms: float,
    ) -> Optional[Dict[str, Any]]:
        validated = validate_copilot_feedback(
            parsed=parsed,
            graph_context=graph_context,
            in_memory_graph=in_memory_graph,
            inflection_event=inflection_event,
            model_id=self.model_id,
            verified_assertions=verified_assertions,
            rigor_tier=rigor_tier,
            engagement_id=engagement_id,
        )
        if not validated or not validated.get("has_guidance", True):
            self.last_stream_outcome = "ABSTAINED"
            if request_snapshot:
                request_snapshot.mark_abstained(reason="grounding_rejection_or_abstention")
            return None
        self.last_stream_outcome = "COMPLETED"
        validated["track"] = "TRACK_2_DEEP_REASONING"
        validated["first_token_ms"] = round(first_token_ms or total_ms, 2)
        validated["total_reasoning_ms"] = round(total_ms, 2)
        validated["displayed_bytes"] = validated.get("suggested_script", "")
        return validated

    def execute_streaming_evaluation(
        self,
        call_kwargs: Dict[str, Any],
        graph_context: Optional[str] = None,
        in_memory_graph: Optional[Any] = None,
        inflection_event: Optional[Any] = None,
        on_token: Optional[Callable[[str], None]] = None,
        fence: Optional[Any] = None,
        verified_assertions: Optional[List[Dict[str, Any]]] = None,
        rigor_tier: Optional[str] = None,
        engagement_id: Optional[str] = None,
        request_snapshot: Optional[Any] = None,
        current_generation_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Executes streaming completion call, validating payload before display."""
        if not self.client:
            return None

        active_fence = fence or self.fence
        gen_id = current_generation_id or (getattr(request_snapshot, "generation_id", None) if request_snapshot else "")
        if not self._is_preflight_active(active_fence, request_snapshot, gen_id):
            self.last_stream_outcome = "CANCELLED"
            return None

        from config.request_deadline import remaining_timeout

        kwargs = {**call_kwargs, "stream": True, "timeout": remaining_timeout(self.request_timeout)}
        t0 = time.perf_counter()
        try:
            stream_response = self.client.chat.completions.create(**kwargs)
            accumulated, first_token_ms = _consume_stream_chunks(
                stream_response, on_token, t0, fence=active_fence, snapshot=request_snapshot, current_generation_id=gen_id
            )
            if not self._is_preflight_active(active_fence, request_snapshot, gen_id):
                self.last_stream_outcome = "CANCELLED"
                return None
            total_ms = (time.perf_counter() - t0) * 1000.0
            raw_json = "".join(accumulated).strip()
            parsed = _parse_stream_json(raw_json) if raw_json else None
            if not parsed:
                self.last_stream_outcome = "ABSTAINED"
                return None
            return self._package_validated_feedback(
                parsed=parsed, graph_context=graph_context, in_memory_graph=in_memory_graph,
                inflection_event=inflection_event, verified_assertions=verified_assertions,
                rigor_tier=rigor_tier, engagement_id=engagement_id,
                request_snapshot=request_snapshot, first_token_ms=first_token_ms, total_ms=total_ms,
            )
        except Exception as e:
            err_low = str(e).lower()
            is_to = "timeout" in err_low or "timed out" in err_low or (time.perf_counter() - t0) >= self.request_timeout * 0.9
            self.last_stream_outcome = "DEADLINE_EXCEEDED" if is_to else "TRANSPORT_ERROR"
            logger.debug("Streaming copilot completion error (%s): %s", self.last_stream_outcome, e)
            return None


def re_repair_json(raw: str) -> Optional[str]:
    """Attempts lightweight extraction and bracket closure of truncated JSON."""
    if "{" not in raw:
        return None
    start = raw.index("{")
    snippet = raw[start:].strip()
    if not snippet.endswith("}"):
        snippet += "}"
    return snippet
