"""
dual_pipeline/live_meeting_recorder_core/copilot_lifecycle.py

Request-scoped lifecycle, episode resolution tracking, grounded context caching,
and answer ledger delivery adapters for Live Copilot.
Enforces Wave 8 governance invariants:
  - Deadlines start per request; topic/generation change, shutdown, and cancellation
    suppress stale callbacks on priority and normal paths.
  - Valid abstention/grounding rejection does not trigger another completion.
  - Cached evidence/vector keys bind session/context/generation/model.
  - Final recorder delivery gets a stable ledger ID.
  - Resolved questions stop recurring, contradictions can reopen them.
  - Inferred memory never becomes canonical fact.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from uuid import uuid4

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.copilot_lifecycle")


@dataclass
class CopilotRequestSnapshot:
    """Immutable request snapshot with per-request deadline and generation binding."""
    request_id: str = field(default_factory=lambda: f"req_{uuid4().hex[:12]}")
    session_id: str = "sess_default"
    generation_id: str = "gen_0"
    topic_id: str = "default_topic"
    created_at: float = field(default_factory=time.time)
    deadline_seconds: float = 7.0
    turn_seq: int = 0
    speaker: str = "Speaker"
    text: str = ""
    is_priority: bool = False
    state: str = "ACTIVE"  # ACTIVE, CANCELLED, SUPPRESSED_STALE, DELIVERED, EXPIRED, ABSTAINED
    suppression_reason: Optional[str] = None

    @property
    def deadline_ts(self) -> float:
        return self.created_at + self.deadline_seconds

    def is_valid(self, current_generation_id: str, now: Optional[float] = None) -> bool:
        """Evaluates whether the request is still eligible for delivery."""
        if self.state != "ACTIVE":
            return False
        curr_time = now if now is not None else time.time()
        if curr_time > self.deadline_ts:
            self.state = "EXPIRED"
            self.suppression_reason = f"deadline_exhausted ({curr_time - self.created_at:.2f}s > {self.deadline_seconds:.2f}s)"
            return False
        if self.generation_id != current_generation_id:
            self.state = "SUPPRESSED_STALE"
            self.suppression_reason = f"generation_mismatch ({self.generation_id} != {current_generation_id})"
            return False
        return True

    def cancel(self, reason: str = "cancelled") -> None:
        self.state = "CANCELLED"
        self.suppression_reason = reason

    def suppress(self, reason: str = "stale") -> None:
        self.state = "SUPPRESSED_STALE"
        self.suppression_reason = reason

    def mark_delivered(self) -> None:
        self.state = "DELIVERED"

    def mark_abstained(self, reason: str = "grounding_rejection") -> None:
        self.state = "ABSTAINED"
        self.suppression_reason = reason

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CopilotEpisode:
    """Tracks a question, objection, or decision episode with resolution links."""
    episode_id: str = field(default_factory=lambda: f"ep_{uuid4().hex[:10]}")
    category: str = "question"  # question, objection, decision
    initiator_speaker: str = "Speaker"
    cue_text: str = ""
    turn_seq: int = 0
    turn_sec: float = 0.0
    status: str = "ACTIVE"  # ACTIVE, RESOLVED, REOPENED
    resolution_text: Optional[str] = None
    resolution_turn_seq: Optional[int] = None
    contradiction_turn_seq: Optional[int] = None
    contradiction_text: Optional[str] = None

    def resolve(self, turn_seq: int, resolution_text: str) -> None:
        self.status = "RESOLVED"
        self.resolution_turn_seq = turn_seq
        self.resolution_text = resolution_text

    def reopen(self, turn_seq: int, contradiction_text: str) -> None:
        self.status = "REOPENED"
        self.contradiction_turn_seq = turn_seq
        self.contradiction_text = contradiction_text

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


CONTRADICTION_CUES = (
    "actually",
    "disagree",
    "reopen",
    "revisit",
    "wait, no",
    "hold on",
    "on second thought",
    "rollback",
    "cannot do",
    "can't do",
    "cancel that",
    "scratch that",
    "not agreed",
    "contradicts",
)


class EpisodeTracker:
    """Manages conversational episodes, preventing recurring advice and reopening on contradiction."""

    def __init__(self, max_episodes: int = 50) -> None:
        self.max_episodes = max_episodes
        self._episodes: Dict[str, CopilotEpisode] = {}
        self._lock = threading.Lock()

    def add_question(
        self,
        speaker: str,
        text: str,
        turn_seq: int,
        turn_sec: float,
    ) -> CopilotEpisode:
        with self._lock:
            ep = CopilotEpisode(
                category="question",
                initiator_speaker=speaker,
                cue_text=text,
                turn_seq=turn_seq,
                turn_sec=turn_sec,
                status="ACTIVE",
            )
            self._episodes[ep.episode_id] = ep
            if len(self._episodes) > self.max_episodes:
                oldest = next(iter(self._episodes))
                del self._episodes[oldest]
            return ep

    def resolve_question(
        self,
        episode_id: str,
        resolution_text: str,
        turn_seq: int,
    ) -> bool:
        with self._lock:
            ep = self._episodes.get(episode_id)
            if ep and ep.status != "RESOLVED":
                ep.resolve(turn_seq=turn_seq, resolution_text=resolution_text)
                return True
            return False

    def evaluate_contradiction(
        self,
        text: str,
        turn_seq: int,
    ) -> List[CopilotEpisode]:
        """Detects contradiction cues against resolved episodes, reopening them."""
        low = (text or "").lower()
        if not any(cue in low for cue in CONTRADICTION_CUES):
            return []

        reopened: List[CopilotEpisode] = []
        with self._lock:
            for ep in self._episodes.values():
                if ep.status == "RESOLVED":
                    ep.reopen(turn_seq=turn_seq, contradiction_text=text)
                    reopened.append(ep)
        return reopened

    def get_active_questions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [ep.to_dict() for ep in self._episodes.values() if ep.category == "question" and ep.status in ("ACTIVE", "REOPENED")]

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {ep_id: ep.to_dict() for ep_id, ep in self._episodes.items()}


class GroundedContextCache:
    """Thread-safe bounded cache binding (session_id, context_hash, generation_id, model_id)."""

    def __init__(self, max_size: int = 128) -> None:
        self.max_size = max_size
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def make_key(session_id: str, context_hash: str, generation_id: str, model_id: str) -> str:
        ctx_prefix = (context_hash or "empty_ctx")[:16]
        return f"{session_id}:{ctx_prefix}:{generation_id}:{model_id}"

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._cache.get(key)
            return entry[1] if entry is not None else None

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            if len(self._cache) >= self.max_size:
                oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][0])
                del self._cache[oldest_key]
            self._cache[key] = (time.time(), value)

    def invalidate_generation(self, generation_id: str) -> int:
        with self._lock:
            keys_to_remove = [k for k in self._cache if f":{generation_id}:" in k]
            for k in keys_to_remove:
                del self._cache[k]
            return len(keys_to_remove)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


class CopilotDeliberationQueue:
    """
    Optional background deliberation path with newest-request coalescing.
    If multiple turns arrive while a background task runs, only the newest turn is kept.
    """

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self._lock = threading.Lock()
        self._pending_snapshot: Optional[CopilotRequestSnapshot] = None
        self._pending_cb: Optional[Callable[[Dict[str, Any]], None]] = None

    def submit_deliberation(
        self,
        snapshot: CopilotRequestSnapshot,
        on_feedback: Optional[Callable[[Dict[str, Any]], None]],
    ) -> bool:
        if not self.enabled:
            return False
        with self._lock:
            if self._pending_snapshot is not None:
                self._pending_snapshot.suppress(reason="coalesced_by_newer_request")
            self._pending_snapshot = snapshot
            self._pending_cb = on_feedback
            return True

    def pop_pending(self) -> Optional[Tuple[CopilotRequestSnapshot, Optional[Callable[[Dict[str, Any]], None]]]]:
        with self._lock:
            if self._pending_snapshot is None:
                return None
            snap, cb = self._pending_snapshot, self._pending_cb
            self._pending_snapshot = None
            self._pending_cb = None
            return snap, cb

    def clear(self) -> None:
        with self._lock:
            if self._pending_snapshot is not None:
                self._pending_snapshot.suppress(reason="queue_cleared")
                self._pending_snapshot = None
                self._pending_cb = None


class CopilotAnswerLedgerAdapter:
    """Assigns stable ledger ID and securely registers final copilot deliveries."""

    @staticmethod
    def record_delivery_safe(
        feedback: Dict[str, Any],
        session_id: str,
        runtime_root: Optional[Path] = None,
        retriever: Optional[Any] = None,
    ) -> str:
        """Injects stable ledger_id into feedback and logs delivery without raising."""
        stable_id = f"ans_copilot_{uuid4().hex[:12]}"
        feedback["ledger_id"] = stable_id

        try:
            from dual_pipeline.answer_ledger import AnswerRecord, get_default_answer_ledger
            ledger = get_default_answer_ledger(runtime_root)
            body = feedback.get("suggested_script") or feedback.get("snap_cue") or ""
            assertions = feedback.get("verified_assertions") or []
            asrt_ids = [str(a.get("assertion_id")) for a in assertions if isinstance(a, dict) and a.get("assertion_id")]
            seeds = [str(s) for s in (feedback.get("retrieval_seeds") or []) if s]
            lat_ms = feedback.get("latency_ms") or feedback.get("elapsed_ms")
            snap_meta: Dict[str, Any] = {"feedback_cue": feedback.get("snap_cue")}
            for k in ("stage_latencies_ms", "age_seconds", "temporal_decay_params", "speaker_note_metrics"):
                if feedback.get(k) is not None:
                    snap_meta[k] = feedback[k]
            rec = AnswerRecord(
                pipeline="live_copilot",
                session_id=session_id,
                intent_or_type=str(feedback.get("category", "STRATEGIC")),
                context_digest=feedback.get("graph_context_digest", ""),
                retrieval_seeds=seeds,
                context_snapshot=[snap_meta],
                cited_assertion_ids=asrt_ids,
                rigor=str(feedback.get("rigor_tier", "FAST")),
                response_text=str(body),
                graph_sha256=getattr(retriever, "graph_sha256", None) if retriever else feedback.get("graph_sha256"),
                generation=getattr(retriever, "generation", None) if retriever else feedback.get("generation"),
                latency_ms=float(lat_ms) if isinstance(lat_ms, (int, float)) else None,
            )
            ledger.append_answer_safe(rec)
            if runtime_root:
                from dual_pipeline.learning_adapters.live_pipeline_integrator import get_live_pipeline_integrator
                get_live_pipeline_integrator(runtime_root=runtime_root).record_success(
                    source_agent="LiveCopilotAgent",
                    category=str(feedback.get("category", "STRATEGIC")),
                    stage="L4",
                    details={"ledger_id": stable_id, "latency_ms": lat_ms, "cited_count": len(asrt_ids)},
                    run_id=session_id,
                )
        except Exception as exc:
            logger.debug("Failed to record live copilot feedback to AnswerLedger: %s", exc)

        return stable_id


def ensure_inferred_memory_boundary(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enforces that conversational inferences (flow summaries, entity scores)
    are strictly demarcated as non-canonical inferred memory.
    """
    cleaned = dict(payload)
    cleaned["is_inferred"] = True
    cleaned["is_canonical"] = False
    cleaned["provenance"] = "conversational_inference"
    return cleaned


class CopilotLifecycleState(str, Enum):
    """Rigid state machine for Copilot runtime lifecycle."""
    UNINITIALIZED = "UNINITIALIZED"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    TERMINATED = "TERMINATED"


class LifecycleStateError(RuntimeError):
    """Raised when an invalid lifecycle state transition or operation occurs."""


@dataclass
class CopilotDegradedState:
    """Represents transparent system degradation when cache, index, or upstream components fail."""
    is_degraded: bool = False
    reason: Optional[str] = None
    error_type: Optional[str] = None
    fallback_mode: str = "nominal"  # "nominal", "l2_disk_fallback", "hybrid_vector_fallback"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_degraded": self.is_degraded,
            "reason": self.reason,
            "error_type": self.error_type,
            "fallback_mode": self.fallback_mode,
            "timestamp": self.timestamp,
        }


@dataclass
class CopilotSessionContext:
    """Encapsulates active session runtime resources under supervised lifecycle management."""
    session_id: str
    engagement_id: Optional[str]
    retriever: Any
    in_memory_graph: Any
    neighborhood_store: Any
    grounded_context_cache: GroundedContextCache
    deliberation_queue: CopilotDeliberationQueue
    answer_ledger_adapter: CopilotAnswerLedgerAdapter
    created_at: float = field(default_factory=time.time)
    state: CopilotLifecycleState = CopilotLifecycleState.READY
    degraded_state: CopilotDegradedState = field(default_factory=CopilotDegradedState)
    graph_status: Optional[str] = None
    graph_diagnostic: Optional[str] = None


def _invoke_graph_loader(
    loader: Callable[..., Any],
    attendees: Optional[List[Dict[str, str]]],
    runtime_root: Optional[Path],
    engagement_id: Optional[str],
    session_id: str,
    graph_path: Optional[Union[str, Path]],
    bypass_cache: bool = False,
) -> Tuple[Any, Any, Any]:
    """Invokes graph loader across supported signature variants."""
    try:
        return loader(
            attendees=attendees, runtime_root=runtime_root,
            engagement_id=engagement_id, session_id=session_id,
            graph_path=graph_path, bypass_cache=bypass_cache,
        )
    except TypeError:
        pass

    try:
        return loader(
            attendees=attendees, runtime_root=runtime_root,
            engagement_id=engagement_id, session_id=session_id,
            graph_path=graph_path,
        )
    except TypeError:
        pass

    try:
        return loader(
            attendees=attendees, runtime_root=runtime_root,
            engagement_id=engagement_id, session_id=session_id,
        )
    except TypeError:
        return loader(
            runtime_root=runtime_root, engagement_id=engagement_id, session_id=session_id,
        )


def _resolve_neighborhood_and_degraded_state(
    bypass_cache: bool,
    neighborhood_store: Optional[Any],
    retriever: Optional[Any],
    engagement_id: Optional[str],
) -> Tuple[Optional[Any], CopilotDegradedState]:
    """Resolves neighborhood store and degraded state with fail-closed L2 fallback."""
    if bypass_cache:
        return None, CopilotDegradedState(
            is_degraded=True,
            reason="explicit_cache_bypass",
            fallback_mode="l2_disk_fallback",
        )

    if neighborhood_store is not None:
        return neighborhood_store, CopilotDegradedState()

    if retriever and getattr(retriever, "neighborhood_store", None) is not None:
        return retriever.neighborhood_store, CopilotDegradedState()

    try:
        from .copilot_neighborhood import CopilotNeighborhoodStore

        store = CopilotNeighborhoodStore(engagement_id=engagement_id)
        return store, CopilotDegradedState()
    except (TimeoutError, MemoryError, Exception) as cache_err:
        logger.warning(
            "Copilot cache initialization failed (%s: %s). Degrading to L2 disk fallback.",
            type(cache_err).__name__,
            cache_err,
        )
        return None, CopilotDegradedState(
            is_degraded=True,
            reason=str(cache_err),
            error_type=type(cache_err).__name__,
            fallback_mode="l2_disk_fallback",
        )


class CopilotLifecycleOrchestrator:
    """
    Unified Lifecycle Orchestrator for all Copilot execution paths (CLI, Daemon, Workers).
    Enforces deterministic state transitions:
      UNINITIALIZED -> INITIALIZING -> READY <-> ACTIVE -> SHUTTING_DOWN -> TERMINATED
    Guarantees that all entry points share identical initialization, graph hydration,
    neighborhood cache configuration, and teardown cleanup.
    """

    def __init__(self, session_id: Optional[str] = None) -> None:
        self.session_id: str = session_id or f"sess_{int(time.time())}"
        self._state: CopilotLifecycleState = CopilotLifecycleState.UNINITIALIZED
        self._lock = threading.RLock()
        self._context: Optional[CopilotSessionContext] = None
        self._active_requests: Set[str] = set()
        self._degraded_state: CopilotDegradedState = CopilotDegradedState()

    @property
    def state(self) -> CopilotLifecycleState:
        with self._lock:
            return self._state

    @property
    def degraded_state(self) -> CopilotDegradedState:
        with self._lock:
            return self._degraded_state

    def bootstrap_session(
        self,
        engagement_id: Optional[str] = None,
        attendees: Optional[List[Dict[str, str]]] = None,
        runtime_root: Optional[Path] = None,
        neighborhood_store: Optional[Any] = None,
        load_fn: Optional[Callable[..., Any]] = None,
        bypass_cache: bool = False,
        graph_path: Optional[Union[str, Path]] = None,
    ) -> CopilotSessionContext:
        """
        Transition state: UNINITIALIZED -> INITIALIZING -> READY.
        Boots retriever, in-memory graph, and registers bounded neighborhood store.
        Degrades gracefully to L2 disk/topology fallback if cache fails or is bypassed.
        """
        with self._lock:
            if self._state == CopilotLifecycleState.READY and self._context is not None:
                return self._context
            if self._state not in (CopilotLifecycleState.UNINITIALIZED, CopilotLifecycleState.TERMINATED):
                raise LifecycleStateError(
                    f"Cannot bootstrap session '{self.session_id}' in state '{self._state.value}'. "
                    f"Expected state '{CopilotLifecycleState.UNINITIALIZED.value}'."
                )

            self._state = CopilotLifecycleState.INITIALIZING
            try:
                from .copilot_retriever import load_copilot_graph

                loader = load_fn or load_copilot_graph
                load_res = _invoke_graph_loader(
                    loader, attendees, runtime_root, engagement_id, self.session_id, graph_path,
                    bypass_cache=bypass_cache,
                )
                status, in_mem, retriever = load_res[0], load_res[1], load_res[2]
                diag = getattr(load_res, "diagnostic_detail", None)
                if not diag:
                    from .copilot_cache_graph import get_last_copilot_graph_diagnostic

                    diag = get_last_copilot_graph_diagnostic()

                neighborhood_store, degraded_state = _resolve_neighborhood_and_degraded_state(
                    bypass_cache, neighborhood_store, retriever, engagement_id
                )

                if retriever and hasattr(retriever, "neighborhood_store") and neighborhood_store is not None:
                    retriever.neighborhood_store = neighborhood_store

                grounded_cache = GroundedContextCache()
                deliberation_q = CopilotDeliberationQueue(enabled=False)
                answer_ledger = CopilotAnswerLedgerAdapter()

                status_val = status.value if hasattr(status, "value") else str(status)
                self._degraded_state = degraded_state
                self._context = CopilotSessionContext(
                    session_id=self.session_id,
                    engagement_id=engagement_id,
                    retriever=retriever,
                    in_memory_graph=in_mem,
                    neighborhood_store=neighborhood_store,
                    grounded_context_cache=grounded_cache,
                    deliberation_queue=deliberation_q,
                    answer_ledger_adapter=answer_ledger,
                    state=CopilotLifecycleState.READY,
                    degraded_state=degraded_state,
                    graph_status=status_val,
                    graph_diagnostic=diag,
                )
                self._state = CopilotLifecycleState.READY
                return self._context
            except Exception as e:
                self._state = CopilotLifecycleState.TERMINATED
                raise LifecycleStateError(f"Failed to bootstrap copilot session '{self.session_id}': {e}") from e

    def begin_request(self, request_id: str) -> None:
        """Transitions state to ACTIVE while request is executing."""
        with self._lock:
            if self._state not in (CopilotLifecycleState.READY, CopilotLifecycleState.ACTIVE):
                raise LifecycleStateError(f"Cannot begin request '{request_id}' in lifecycle state '{self._state.value}'.")
            self._active_requests.add(request_id)
            self._state = CopilotLifecycleState.ACTIVE
            if self._context:
                self._context.state = CopilotLifecycleState.ACTIVE

    def end_request(self, request_id: str) -> None:
        """Concludes request processing; transitions back to READY when no active requests remain."""
        with self._lock:
            self._active_requests.discard(request_id)
            if not self._active_requests and self._state == CopilotLifecycleState.ACTIVE:
                self._state = CopilotLifecycleState.READY
                if self._context:
                    self._context.state = CopilotLifecycleState.READY

    def shutdown(self) -> None:
        """Transition state: READY / ACTIVE -> SHUTTING_DOWN -> TERMINATED and unbind resources."""
        with self._lock:
            if self._state == CopilotLifecycleState.TERMINATED:
                return
            self._state = CopilotLifecycleState.SHUTTING_DOWN
            if self._context:
                try:
                    self._context.deliberation_queue.clear()
                    self._context.grounded_context_cache.clear()
                    if hasattr(self._context.neighborhood_store, "clear"):
                        self._context.neighborhood_store.clear()
                except Exception as e:
                    logger.warning("Error during copilot session cleanup: %s", e)
                self._context.state = CopilotLifecycleState.TERMINATED
                self._context = None
            self._active_requests.clear()
            self._state = CopilotLifecycleState.TERMINATED


_GLOBAL_ORCHESTRATOR_REGISTRY: Dict[str, CopilotLifecycleOrchestrator] = {}
_REGISTRY_LOCK = threading.Lock()
_MAX_ACTIVE_SESSIONS: int = 32


def get_or_create_lifecycle_orchestrator(session_id: str) -> CopilotLifecycleOrchestrator:
    """Returns or creates a supervised lifecycle orchestrator for a given session with bounded eviction."""
    with _REGISTRY_LOCK:
        if session_id not in _GLOBAL_ORCHESTRATOR_REGISTRY:
            if len(_GLOBAL_ORCHESTRATOR_REGISTRY) >= _MAX_ACTIVE_SESSIONS:
                term_keys = [k for k, v in _GLOBAL_ORCHESTRATOR_REGISTRY.items() if v.state == CopilotLifecycleState.TERMINATED]
                evict_key = term_keys[0] if term_keys else next(iter(_GLOBAL_ORCHESTRATOR_REGISTRY))
                evicted = _GLOBAL_ORCHESTRATOR_REGISTRY.pop(evict_key, None)
                if evicted is not None:
                    try:
                        evicted.shutdown()
                    except Exception as exc:
                        logger.debug("Evicted session shutdown error: %s", exc)
            _GLOBAL_ORCHESTRATOR_REGISTRY[session_id] = CopilotLifecycleOrchestrator(session_id=session_id)
        return _GLOBAL_ORCHESTRATOR_REGISTRY[session_id]


def reset_lifecycle_orchestrators() -> None:
    """Tears down all active session orchestrators."""
    with _REGISTRY_LOCK:
        for orch in list(_GLOBAL_ORCHESTRATOR_REGISTRY.values()):
            try:
                orch.shutdown()
            except Exception:
                pass
        _GLOBAL_ORCHESTRATOR_REGISTRY.clear()

