"""
dual_pipeline/live_meeting_recorder_core/copilot.py

Low-latency live copilot reasoning engine powered by GPT-6-astra under env_di.
Evaluates incremental meeting dialogue turns to produce immediate executive advisory feedback:
- Detects strategic/commercial risks
- Identifies technical architecture trade-offs
- Formulates concise, high-value executive coaching cues
- Enforces sub-second graph context retrieval, strict latency limits, and stale-turn invalidation
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from dual_pipeline.prompts.registry import CANONICAL_PROMPT_REGISTRY

from .collateral_watcher import CollateralFsWatcher
from .copilot_context_model import ConversationContextModel
from .copilot_lifecycle import (
    CopilotAnswerLedgerAdapter,
    CopilotDeliberationQueue,
    CopilotRequestSnapshot,
    GroundedContextCache,
    ensure_inferred_memory_boundary,
)
from .copilot_retriever import (
    CopilotGraphRetriever,
    GraphLoadStatus,
    load_copilot_graph,
    search_session_context,
)
from .copilot_validator import validate_copilot_feedback

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.copilot")

COPILOT_SYSTEM_PROMPT = CANONICAL_PROMPT_REGISTRY.get_prompt("LIVE_COPILOT")


def _format_copilot_context_blocks(
    dialogue: str,
    graph_context: str,
    prior_context: str,
    inflection_event: Optional[Any],
    flow_summary: Optional[str] = None,
    salient_entities: Optional[str] = None,
    engagement_id: Optional[str] = None,
) -> str:
    context_blocks = []
    if engagement_id:
        context_blocks.append(
            f"Engagement Boundary: Strictly scoped to {engagement_id}. All factual assertions, durations, and dependencies must be strictly grounded in Verified Graph Context."
        )
    if graph_context:
        context_blocks.append(f"Verified Graph Context:\n{graph_context}")
    if flow_summary and flow_summary != "Flow: Steady discussion":
        context_blocks.append(f"Meeting Flow & State:\n{flow_summary}")
    if salient_entities:
        context_blocks.append(f"Active Salient Entities:\n{salient_entities}")
    if prior_context:
        context_blocks.append(f"Prior session context:\n{prior_context}")
    context_blocks.append(f"Recent meeting dialogue turns:\n{dialogue}")
    if inflection_event and getattr(inflection_event, "reason", None):
        context_blocks.append(f"[Trigger Event: {inflection_event.reason} on '{inflection_event.cue_text}']")
    context_blocks.append("Provide copilot evaluation JSON:")
    return "\n\n".join(context_blocks)


def _log_cached_tokens(resp: Any, logger_inst: logging.Logger) -> None:
    try:
        from dual_pipeline.prompt_cache import normalize_cached_tokens

        cached_toks = normalize_cached_tokens(resp)
        if cached_toks > 0:
            logger_inst.debug("Live copilot evaluation utilized %d cached tokens.", cached_toks)
    except Exception:
        pass


class LiveCopilotAdvisor:
    """Executes live copilot reasoning turns over rolling meeting segments."""

    def __init__(
        self,
        project_name: Optional[str] = None,
        meeting_name: str = "Live Meeting",
        attendees: Optional[List[Dict[str, str]]] = None,
        mock_mode: bool = False,
        cooldown_seconds: float = 4.0,
        model_name: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        session_output_dir: Optional[Path] = None,
        get_elapsed_fn: Optional[Any] = None,
        max_freshness_seconds: float = 8.0,
        request_timeout: float = 7.0,
        collateral_watcher: Optional[CollateralFsWatcher] = None,
        session_context_packet: Optional[Any] = None,
        enable_prefetch: bool = True,
        prefetch_timeout_seconds: float = 5.0,
    ):
        self.project_name = project_name or "UNRESOLVED"
        self.meeting_name = meeting_name
        self.attendees = attendees or []
        self.mock_mode = mock_mode
        self.cooldown_seconds = cooldown_seconds
        from config.credentials import CredentialManager
        self.max_freshness_seconds = float(
            CredentialManager.get_credential("COPILOT_MAX_FRESHNESS_SECONDS") or max_freshness_seconds
        )
        self.request_timeout = float(
            CredentialManager.get_credential("COPILOT_REQUEST_TIMEOUT") or request_timeout
        )
        self.enable_prefetch = enable_prefetch
        self.prefetch_timeout_seconds = prefetch_timeout_seconds

        self._last_evaluated_time = 0.0
        self._turn_seq_counter = 0
        self._recent_turns: List[Dict[str, Any]] = []
        self._session_turns: List[Dict[str, Any]] = []
        self._cached_prefetched_graph_context: Optional[str] = None

        from concurrent.futures import Future, ThreadPoolExecutor

        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="live_copilot")
        self._active_future: Optional[Future] = None
        self._pending_priority_task: Optional[Tuple[Any, ...]] = None
        self._is_shutdown: bool = False
        self.session_id: str = f"sess_{uuid4().hex[:12]}"
        self.generation_id: str = "gen_0"
        self.current_topic: str = "default_topic"
        self.grounded_context_cache = GroundedContextCache()
        self.deliberation_queue = CopilotDeliberationQueue(enabled=False)
        self.answer_ledger_adapter = CopilotAnswerLedgerAdapter()
        self._active_request_snapshot: Optional[CopilotRequestSnapshot] = None

        self.attendees_summary = ", ".join(
            (a if isinstance(a, str) else f"{a.get('name', 'Unknown')} ({a.get('role', 'Role')}, {a.get('org', 'Org')})")
            for a in self.attendees
        )

        self.client = None
        from dual_pipeline.model_registry import ModelTier, resolve_copilot_tier_model

        if model_name:
            self.model_id = model_name
            self.reasoning_effort = reasoning_effort or "medium"
        else:
            self.model_id, self.reasoning_effort = resolve_copilot_tier_model(
                ModelTier.COPILOT_FAST_SNAP
            )
            if reasoning_effort:
                self.reasoning_effort = reasoning_effort

        from .copilot_lifecycle import (
            CopilotLifecycleState,
            get_or_create_lifecycle_orchestrator,
        )
        self.orchestrator = get_or_create_lifecycle_orchestrator(self.session_id)
        if self.orchestrator.state == CopilotLifecycleState.UNINITIALIZED:
            ctx = self.orchestrator.bootstrap_session(
                engagement_id=self.project_name,
                attendees=self.attendees,
            )
            self.graph_status = GraphLoadStatus.LOADED_ACTIVE_PAIR
            self.in_memory_graph = ctx.in_memory_graph
            self.retriever = ctx.retriever
        else:
            self.graph_status, self.in_memory_graph, self.retriever = load_copilot_graph(attendees=self.attendees)

        # Initialize or pre-fetch session context packet
        if session_context_packet is not None:
            self.session_context_packet = session_context_packet
        elif self.enable_prefetch and (
            self.in_memory_graph is not None or self.retriever is not None
        ):
            from .copilot_retriever import prefetch_session_packet

            self.session_context_packet = prefetch_session_packet(
                in_memory_graph=self.in_memory_graph,
                retriever=self.retriever,
                project_name=self.project_name,
                meeting_name=self.meeting_name,
                attendees=self.attendees,
                timeout_seconds=self.prefetch_timeout_seconds,
            )
        else:
            from .session_context_packet import SessionContextPacket

            self.session_context_packet = SessionContextPacket.empty(
                project_scope=self.project_name
            )

        self.context_model = ConversationContextModel(
            project_scope=self.project_name,
            attendees=self.attendees,
        )

        self.disabled = False
        if not self.mock_mode:
            try:
                CredentialManager.ensure_ssot_env_loaded()
                self.client, _ = CredentialManager.get_openai_client(allow_mock=False)
            except Exception as e:
                logger.error("LiveCopilotAdvisor failed to initialize credentials. Disabling copilot: %s", e)
                self.disabled = True
                self.client = None

        from .flash_snap import FlashSnapEngine
        from .streaming_copilot import StreamingCopilotHandler

        self.flash_snap_engine = FlashSnapEngine(in_memory_graph=self.in_memory_graph)
        self.streaming_handler = (
            StreamingCopilotHandler(
                client=self.client,
                model_id=self.model_id,
                reasoning_effort=self.reasoning_effort,
                request_timeout=self.request_timeout,
            )
            if self.client
            else None
        )

        # Collateral fs watcher: auto-starts when session_output_dir is provided or attaches to existing
        self._collateral_watcher: Optional[CollateralFsWatcher] = collateral_watcher
        self._session_output_dir: Optional[Path] = session_output_dir
        if self._collateral_watcher is None and session_output_dir and get_elapsed_fn:
            ref_dir = Path(session_output_dir) / "referenced_artifacts"
            self._collateral_watcher = CollateralFsWatcher(
                ref_dir=ref_dir,
                get_elapsed_fn=get_elapsed_fn,
            )
            self._collateral_watcher.start()
            logger.info("Collateral fs watcher started on %s", ref_dir)

        from .copilot_grounding_verifier import (
            AsyncSpeculativeEncoder,
            CopilotGroundingVerifier,
        )

        self.grounding_verifier = CopilotGroundingVerifier()
        self.speculative_encoder = AsyncSpeculativeEncoder.get_instance()
        self._last_grounding_result = None

    def _ensure_active_retriever(self) -> Optional[Any]:
        if self.in_memory_graph is not None:
            if self.retriever is None or getattr(self.retriever, "_topology", None) is not self.in_memory_graph:
                self.retriever = CopilotGraphRetriever(self.in_memory_graph, attendees=self.attendees)
        return self.retriever

    def _retrieve_context_and_candidates(self, dialogue: Any) -> Tuple[str, List[Dict[str, Any]]]:
        retriever = self._ensure_active_retriever()
        if not retriever:
            return "", []
        ctx = retriever.retrieve_context(dialogue)
        cands = retriever.extract_candidate_assertions(dialogue) if hasattr(retriever, "extract_candidate_assertions") else []
        return ctx, cands

    def _apply_grounding_verification(self, candidates: List[Dict[str, Any]], dialogue: Any, pkt: Any) -> None:
        if hasattr(self, "grounding_verifier") and candidates:
            packet_sha = getattr(pkt, "graph_sha256", None) if pkt else None
            self._last_grounding_result = self.grounding_verifier.verify_and_ground(
                candidates=candidates,
                dialogue=dialogue,
                topology=self.in_memory_graph,
                packet_graph_sha=packet_sha,
            )

    def _lookup_graph_context(self, dialogue: Any) -> str:
        """Retrieves zero-latency contextual assertions from in-memory graph topology with Wave 4 grounding verification."""
        d_key = dialogue if isinstance(dialogue, str) else (dialogue.compute_hash() if hasattr(dialogue, "compute_hash") else self._build_dialogue_text())
        ctx_hash = hashlib.sha256(str(d_key).encode("utf-8")).hexdigest()
        cache_key = self.grounded_context_cache.make_key(self.session_id, ctx_hash, self.generation_id, self.model_id)
        cached = self.grounded_context_cache.get(cache_key)
        if cached is not None:
            return cached

        retrieved_ctx, candidates = self._retrieve_context_and_candidates(dialogue)
        pkt = getattr(self, "session_context_packet", None)
        if pkt and not pkt.is_empty:
            packet_str = pkt.format_for_copilot_prompt(max_chars=2000)
            if packet_str:
                retrieved_ctx = f"{retrieved_ctx}\n\n{packet_str}" if retrieved_ctx else packet_str

        self._apply_grounding_verification(candidates, dialogue, pkt)
        if len(retrieved_ctx) > 3500:
            retrieved_ctx = retrieved_ctx[:3500] + "\n... (context capped)"
        self.grounded_context_cache.put(cache_key, retrieved_ctx)
        return retrieved_ctx

    def record_turn(
        self,
        speaker: str,
        text: str,
        timestamp: str,
        turn_sec: Optional[float] = None,
    ) -> int:
        """Buffers a confirmed transcript turn into recent window, context model, and session memory with speculative prefetch."""
        self._turn_seq_counter += 1
        arrival = turn_sec if turn_sec is not None else time.time()
        turn_data = {
            "seq": self._turn_seq_counter,
            "speaker": speaker,
            "text": text,
            "timestamp": timestamp,
            "arrival_time": arrival,
        }
        self._session_turns.append(turn_data)
        self._recent_turns.append(turn_data)
        if len(self._recent_turns) > 8:
            self._recent_turns.pop(0)

        # Feed 2-layer context model
        if hasattr(self, "context_model"):
            self.context_model.add_turn(
                speaker=speaker,
                text=text,
                timestamp=timestamp,
                turn_sec=arrival,
            )

        # Speculative background dense encoding (Decision D2)
        if hasattr(self, "speculative_encoder") and len(text.strip()) >= 10:
            self.speculative_encoder.trigger_speculative_encode(text)

        # Speculative zero-latency graph context pre-fetch
        if len(text.strip()) >= 20 and self.retriever:
            try:
                prefetch_target = (
                    self.context_model if hasattr(self, "context_model") else text
                )
                self._cached_prefetched_graph_context = self._lookup_graph_context(prefetch_target)
            except Exception:
                pass

        return self._turn_seq_counter

    def add_turn(
        self,
        speaker: str,
        text: str,
        timestamp: str,
        turn_sec: Optional[float] = None,
    ) -> int:
        """Alias for record_turn satisfying conversation context model interface."""
        return self.record_turn(speaker, text, timestamp, turn_sec)

    def _search_session_context(self, query: str, top_k: int = 2) -> str:
        """Retrieves relevant prior dialogue turns from full session memory outside recent window in <0.5ms."""
        return search_session_context(self._session_turns, query, top_k=top_k)

    def _build_mock_feedback(self, graph_context: Optional[str]) -> Dict[str, Any]:
        res = {
            "type": "COPILOT_FEEDBACK",
            "has_guidance": True,
            "category": "STRATEGIC",
            "snap_cue": "Anchor on two-week spike",
            "suggested_script": "Should we phase the charter into an initial 30-day spike before locking commitments?",
            "insightful_questions": [
                "Should we phase the charter into an initial 30-day spike before locking commitments?"
            ],
            "risk_trap": "Unscoped milestone extension without funding",
            "risk_alert": "Unscoped milestone extension without funding",
            "opportunity": "Anchor delivery framework to two-week proof-of-value gates.",
            "confidence": 0.95,
            "model": self.model_id or "mock-copilot",
            "provider_mode": "MOCK",
        }
        if graph_context:
            res["graph_context"] = graph_context
        return res

    def _format_copilot_output(
        self,
        parsed: Dict[str, Any],
        graph_context: Optional[str],
        inflection_event: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        verified_asrt = None
        rigor_tier = "FAST"
        if getattr(self, "_last_grounding_result", None):
            verified_asrt = self._last_grounding_result.verified_assertions
            rigor_tier = self._last_grounding_result.rigor_tier
            parsed["grounding_telemetry"] = self._last_grounding_result.to_dict()
            parsed["rigor_tier"] = rigor_tier
            if self._last_grounding_result.conflicts:
                parsed["conflicts"] = self._last_grounding_result.conflicts
        else:
            parsed["rigor_tier"] = rigor_tier

        return validate_copilot_feedback(
            parsed=parsed,
            graph_context=graph_context,
            in_memory_graph=self.in_memory_graph,
            inflection_event=inflection_event,
            model_id=self.model_id,
            verified_assertions=verified_asrt,
            rigor_tier=rigor_tier,
            engagement_id=getattr(self, "engagement_id", None),
        )

    def _build_dialogue_text(self) -> str:
        focus = self.context_model.focus_turns() if hasattr(self, "context_model") else []
        turns = focus if focus else self._recent_turns[-4:]
        return "\n".join(f"{t['timestamp']} {t['speaker']}: {t['text']}" for t in turns)

    def _format_flow_and_salient(self) -> Tuple[Optional[str], Optional[str]]:
        if not hasattr(self, "context_model"):
            return None, None
        flow_summary = self.context_model.format_flow_summary()
        salient = self.context_model.salient_entities()
        salient_str = ", ".join(f"{ent} ({score:.2f})" for ent, score in salient[:5]) if salient else None
        return flow_summary, salient_str

    def _build_call_kwargs(self, system_prompt: str, prompt_context: str) -> Dict[str, Any]:
        from config.credentials import sanitize_model_call_kwargs
        from dual_pipeline.sanitizer import LocalPreEgressSanitizer

        raw_kwargs: Dict[str, Any] = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt_context},
            ],
            "response_format": {"type": "json_object"},
            "max_completion_tokens": 400,
            "cache_namespace": "live_executive_copilot",
        }
        if self.reasoning_effort:
            raw_kwargs["reasoning_effort"] = self.reasoning_effort
        LocalPreEgressSanitizer.sanitize_messages(raw_kwargs["messages"])
        return sanitize_model_call_kwargs(self.model_id, raw_kwargs)

    def _evaluate_dialogue_sync(
        self, inflection_event: Optional[Any] = None
    ) -> Optional[Dict[str, Any]]:
        """Internal synchronous evaluation over rolling dialogue turns with strict timeout budget."""
        dialogue = self._build_dialogue_text()
        if len(dialogue.strip()) < 30:
            return None

        flow_summary, salient_str = self._format_flow_and_salient()
        lookup_target: Any = self.context_model if hasattr(self, "context_model") else dialogue
        graph_context = self._lookup_graph_context(lookup_target)
        prior_context = self._search_session_context(dialogue)

        if self.mock_mode:
            return self._format_copilot_output(self._build_mock_feedback(graph_context), graph_context, inflection_event)

        try:
            system_prompt = COPILOT_SYSTEM_PROMPT.format(
                project_name=self.project_name,
                meeting_name=self.meeting_name,
                attendees_summary=self.attendees_summary,
                graph_context="[Verified Graph Context is supplied with each dialogue turn below]",
            )
            prompt_context = _format_copilot_context_blocks(
                dialogue=dialogue,
                graph_context=graph_context,
                prior_context=prior_context,
                inflection_event=inflection_event,
                flow_summary=flow_summary,
                salient_entities=salient_str,
                engagement_id=getattr(self, "engagement_id", None) or self.project_name,
            )
            call_kwargs = self._build_call_kwargs(system_prompt, prompt_context)
            if getattr(self, "streaming_handler", None):
                verified_asrt = None
                rigor_tier = "FAST"
                if getattr(self, "_last_grounding_result", None):
                    verified_asrt = self._last_grounding_result.verified_assertions
                    rigor_tier = self._last_grounding_result.rigor_tier
                streamed = self.streaming_handler.execute_streaming_evaluation(
                    call_kwargs=call_kwargs,
                    graph_context=graph_context,
                    in_memory_graph=self.in_memory_graph,
                    inflection_event=inflection_event,
                    verified_assertions=verified_asrt,
                    rigor_tier=rigor_tier,
                    engagement_id=getattr(self, "engagement_id", None),
                )
                if streamed:
                    return streamed
                if getattr(self.streaming_handler, "last_stream_outcome", "") in ("ABSTAINED", "CANCELLED", "DEADLINE_EXCEEDED"):
                    return None

            resp = self.client.chat.completions.create(timeout=self.request_timeout, **call_kwargs)
            _log_cached_tokens(resp, logger)
            raw_content = resp.choices[0].message.content or "{}"
            parsed = json.loads(raw_content)
            return self._format_copilot_output(parsed, graph_context, inflection_event)
        except Exception as e:
            from dual_pipeline.sanitizer import EgressSanitizationError
            lvl = logging.ERROR if isinstance(e, EgressSanitizationError) else logging.DEBUG
            logger.log(lvl, "Live copilot evaluation error: %s", e)
            return None

    def evaluate_live_turns(
        self, current_time: float, inflection_event: Optional[Any] = None
    ) -> Optional[Dict[str, Any]]:
        """Evaluates rolling dialogue turns synchronously if cooldown has elapsed or priority event."""
        is_priority = bool(inflection_event and getattr(inflection_event, "is_priority", False))
        if not is_priority and (current_time - self._last_evaluated_time < self.cooldown_seconds):
            return None
        if not self._recent_turns:
            return None
        self._last_evaluated_time = current_time
        return self._evaluate_dialogue_sync(inflection_event)

    def _should_throttle(self, current_time: float, is_priority: bool) -> bool:
        if self._is_shutdown or not self._recent_turns or getattr(self, "disabled", False):
            return True
        return not is_priority and (current_time - self._last_evaluated_time < self.cooldown_seconds)

    def set_generation(self, generation_id: str, topic_id: Optional[str] = None) -> None:
        """Transitions to a new topic or generation, suppressing stale active/pending requests."""
        old_gen = self.generation_id
        self.generation_id = generation_id
        if topic_id:
            self.current_topic = topic_id
        if self._active_request_snapshot:
            self._active_request_snapshot.suppress(f"generation_changed ({old_gen} -> {generation_id})")
        self._pending_priority_task = None
        self.deliberation_queue.clear()
        self.grounded_context_cache.invalidate_generation(old_gen)

    def cancel(self) -> None:
        """Cancels active copilot execution, suppressing all pending callbacks."""
        self.deliberation_queue.clear()
        if self._active_request_snapshot:
            self._active_request_snapshot.cancel("cancelled")
        self._pending_priority_task = None
        if self.streaming_handler:
            self.streaming_handler.cancel()

    def _is_request_fresh(
        self,
        turn_ts: float,
        snap: Optional[CopilotRequestSnapshot],
        turn_seq: int,
    ) -> bool:
        if snap and not snap.is_valid(self.generation_id, time.time()):
            logger.debug("Copilot request %s invalid/suppressed before run", getattr(snap, "request_id", ""))
            return False
        age = time.time() - turn_ts
        if age > self.max_freshness_seconds:
            if snap:
                snap.suppress(reason="max_freshness_exceeded")
            logger.debug(
                "Stale copilot turn %d discarded (age=%.2fs > %.2fs)",
                turn_seq,
                age,
                self.max_freshness_seconds,
            )
            return False
        return True

    def _deliver_turn_feedback(
        self,
        res: Dict[str, Any],
        snap: Optional[CopilotRequestSnapshot],
        turn_ts: float,
        cb: Optional[Any],
        turn_seq: int,
    ) -> None:
        if not cb:
            return
        now = time.time()
        fresh = (now - turn_ts <= self.max_freshness_seconds)
        valid = (not snap or snap.is_valid(self.generation_id, now))
        if fresh and valid:
            stable_id = self.answer_ledger_adapter.record_delivery_safe(
                feedback=res,
                session_id=self.session_id,
                runtime_root=self._session_output_dir,
                retriever=self.retriever,
            )
            res["ledger_id"] = stable_id
            if snap:
                snap.mark_delivered()
            cb(res)
        else:
            if snap:
                snap.suppress(reason="deadline_or_generation_stale")
            logger.debug(
                "Copilot evaluation finished late/stale; suppressed delivery for turn %d",
                turn_seq,
            )

    def _drain_pending_priority_task(self) -> None:
        pending = self._pending_priority_task
        if not pending or self._is_shutdown:
            return
        self._pending_priority_task = None
        p_seq, p_ts, p_cb, p_ev = pending[0], pending[1], pending[2], pending[3]
        p_snap = pending[4] if len(pending) > 4 else None
        p_res = self._evaluate_dialogue_sync(p_ev)
        if p_res and p_res.get("has_guidance", True):
            self._deliver_turn_feedback(p_res, p_snap, p_ts, p_cb, p_seq)

    def _run_async_worker(
        self,
        turn_seq: int,
        turn_ts: float,
        ev: Optional[Any],
        cb: Optional[Any],
        snapshot: Optional[CopilotRequestSnapshot] = None,
    ) -> Optional[Dict[str, Any]]:
        snap = snapshot or self._active_request_snapshot
        try:
            if not self._is_request_fresh(turn_ts, snap, turn_seq):
                return None

            res = self._evaluate_dialogue_sync(ev)
            if not res or not res.get("has_guidance", True):
                if snap:
                    snap.mark_abstained()
                return None

            self._deliver_turn_feedback(res, snap, turn_ts, cb, turn_seq)
            self._drain_pending_priority_task()
            return res
        except Exception as ex:
            logger.debug("Async copilot worker error: %s", ex)
            return None

    def submit_turn_async(
        self,
        speaker: str,
        text: str,
        timestamp: str,
        current_time: float,
        on_feedback: Optional[Any] = None,
        inflection_event: Optional[Any] = None,
    ) -> bool:
        """Buffers turn and asynchronously dispatches evaluation, preempting cooldown on priority inflection."""
        if self._is_shutdown:
            return False
        turn_seq = self.record_turn(
            speaker=speaker, text=text, timestamp=timestamp, turn_sec=current_time
        )

        is_priority = bool(inflection_event and getattr(inflection_event, "is_priority", False))
        snapshot = CopilotRequestSnapshot(
            session_id=self.session_id,
            generation_id=self.generation_id,
            topic_id=self.current_topic,
            deadline_seconds=self.request_timeout,
            turn_seq=turn_seq,
            speaker=speaker,
            text=text,
            is_priority=is_priority,
        )
        self._active_request_snapshot = snapshot

        if self.deliberation_queue.enabled:
            self.deliberation_queue.submit_deliberation(snapshot, on_feedback)

        # Track 1: Sub-500ms Instant Flash Snap
        if on_feedback and getattr(self, "flash_snap_engine", None):
            graph_ctx = getattr(self, "_cached_prefetched_graph_context", None) or self._lookup_graph_context(text)
            flash = self.flash_snap_engine.evaluate_utterance(
                utterance=text, speaker=speaker, timestamp=timestamp, graph_context=graph_ctx
            )
            if flash:
                with contextlib.suppress(Exception):
                    on_feedback(flash)

        if self._should_throttle(current_time, is_priority):
            return False

        wall_clock = time.time()
        if self._active_future is not None and not self._active_future.done():
            if is_priority:
                self._pending_priority_task = (turn_seq, wall_clock, on_feedback, inflection_event, snapshot)
                return True
            return False

        self._last_evaluated_time = current_time
        try:
            self._active_future = self._executor.submit(
                self._run_async_worker, turn_seq, wall_clock, inflection_event, on_feedback, snapshot
            )
            return True
        except (RuntimeError, Exception):
            return False

    @property
    def collateral_drops(self) -> List[Dict[str, Any]]:
        """Returns all collateral drops recorded by the fs watcher during this session."""
        if self._collateral_watcher:
            return self._collateral_watcher.drops
        return []

    def shutdown(self, wait: bool = False, cancel_futures: Optional[bool] = None, **_kwargs: Any) -> None:
        """Gracefully terminates background copilot worker thread and collateral watcher."""
        self._is_shutdown = True
        self.deliberation_queue.clear()
        if not wait and self._active_request_snapshot:
            self._active_request_snapshot.suppress("shutdown")
        self._pending_priority_task = None
        if self.streaming_handler:
            self.streaming_handler.shutdown()
        if self._collateral_watcher and self._session_output_dir:
            log_path = Path(self._session_output_dir) / "collateral_drop_log.json"
            self._collateral_watcher.stop(log_path=log_path)
        with contextlib.suppress(Exception):
            eff_cancel = (not wait) if cancel_futures is None else bool(cancel_futures)
            self._executor.shutdown(wait=wait, cancel_futures=eff_cancel)
        if hasattr(self, "orchestrator") and self.orchestrator:
            with contextlib.suppress(Exception):
                self.orchestrator.shutdown()
