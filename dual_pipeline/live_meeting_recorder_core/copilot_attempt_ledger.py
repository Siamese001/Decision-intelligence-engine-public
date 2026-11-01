"""
dual_pipeline/live_meeting_recorder_core/copilot_attempt_ledger.py

Governed attempt record and diagnostic ledger for Live Executive Copilot.
Enforces Phase 1 requirements:
- Bounded attempt record for every Copilot terminal outcome (both successes and failures).
- Complete isolation of failed attempts from copilot_answers.jsonl.
- Capture of request metadata, parsed arguments, session, roots, revision,
  context freshness, candidate counts, rejection categories, and terminal reason.
- Zero transcript-body duplication.
- Concise, actionable human messages for all failure modes.
- Fail-safe persistence: diagnostic recording failure cannot hang capture or produce false success.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

logger = logging.getLogger("dual_pipeline.copilot_attempt_ledger")

# Canonical failure categories
CATEGORY_SESSION_DISCOVERY = "session_discovery"
CATEGORY_STALE_CONTEXT = "stale_context"
CATEGORY_GRAPH_BINDING = "graph_binding"
CATEGORY_ABSENT_CANDIDATES = "absent_candidates"
CATEGORY_REJECTED_EVIDENCE = "rejected_evidence"
CATEGORY_RENDERING_FAILURE = "rendering_failure"
CATEGORY_DEADLINE_EXHAUSTION = "deadline_exhaustion"
CATEGORY_USAGE = "usage"
CATEGORY_SCOPE_BOUNDARY = "scope_boundary"
CATEGORY_UNHANDLED_ERROR = "unhandled_error"


@dataclass
class CopilotAttemptRecord:
    """Bounded, truthful record of a single Copilot terminal attempt."""
    attempt_id: str
    timestamp: str  # ISO-8601 UTC
    terminal_outcome: str  # "SUCCESS" or "FAIL"
    terminal_reason: str  # Machine reason code
    failure_category: Optional[str]  # High-level category
    user_message: str  # Concise, actionable message emitted
    request: Optional[str] = None
    parsed_intent: Optional[str] = None
    parsed_target: Optional[str] = None
    targets: List[str] = field(default_factory=list)
    parsed_topic: Optional[str] = None
    parsed_words: Optional[int] = None
    session_id: Optional[str] = None
    resolved_roots: Dict[str, Optional[str]] = field(default_factory=dict)
    source_revision: Optional[str] = None
    context_freshness: Dict[str, Any] = field(default_factory=dict)
    candidate_counts: Dict[str, int] = field(default_factory=dict)
    rejection_categories: Dict[str, int] = field(default_factory=dict)
    evidence_breakdown: Dict[str, Any] = field(default_factory=dict)
    generation: Optional[str] = None
    graph_sha256: Optional[str] = None
    error_details: Optional[str] = None
    latency_ms: Optional[float] = None
    stage_latencies_ms: Dict[str, float] = field(default_factory=dict)
    age_seconds: Optional[float] = None
    temporal_decay_params: Dict[str, Any] = field(default_factory=dict)
    retrieval_seeds: List[str] = field(default_factory=list)
    speaker_note_metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_REASON_MAP: Dict[str, Tuple[str, str]] = {
    "NO_SESSION_FOUND": (CATEGORY_SESSION_DISCOVERY, "[Session Discovery] No active recording session found in .session_registry/. Ensure live recording is running."),
    "NO_SESSION_MATCH": (CATEGORY_SESSION_DISCOVERY, "[Session Discovery] No active recording session found in .session_registry/. Ensure live recording is running."),
    "NO_LIVE_CONTEXT": (CATEGORY_STALE_CONTEXT, "[Session Context] No recent speaker turns available. Recording is initializing or idle."),
    "STALE_LIVE_CONTEXT": (CATEGORY_STALE_CONTEXT, "[Session Context] No recent speaker turns available. Recording is initializing or idle."),
    "GRAPH_NOT_LOADED": (CATEGORY_GRAPH_BINDING, "[Graph Grounding] No active graph pair loaded at runtime root. Run graph release or check runtime path."),
    "MISSING_DATABASE": (CATEGORY_GRAPH_BINDING, "[Graph Grounding] No active graph pair loaded at runtime root. Run graph release or check runtime path."),
    "LOAD_FAILED": (CATEGORY_GRAPH_BINDING, "[Graph Grounding] No active graph pair loaded at runtime root. Run graph release or check runtime path."),
    "ZERO_RELEVANT_ASSERTIONS": (CATEGORY_ABSENT_CANDIDATES, "[Graph Retrieval] No relevant graph assertions match the current discussion."),
    "ZERO_MATCHING_ASSERTIONS": (CATEGORY_ABSENT_CANDIDATES, "[Graph Retrieval] No relevant graph assertions match the current discussion."),
    "SCOPE_FILTERED": (CATEGORY_SCOPE_BOUNDARY, "[Scope Boundary] Assertions exist but were filtered by active project scope."),
    "SCOPE_QUARANTINED": (CATEGORY_SCOPE_BOUNDARY, "[Scope Boundary] Assertion violates scope isolation or project boundary."),
    "CROSS_PROJECT_VIOLATION": (CATEGORY_SCOPE_BOUNDARY, "[Scope Boundary] Assertion references out-of-scope cross-project entities."),
    "C19_CAREER_LEVERAGE": (CATEGORY_SCOPE_BOUNDARY, "[Scope Boundary] Assertion violates career leverage or confidential boundary."),
    "OWNERSHIP_TRAP": (CATEGORY_SCOPE_BOUNDARY, "[Scope Boundary] Assertion violates ownership boundary."),
    "ENTITY_NOT_FOUND": (CATEGORY_ABSENT_CANDIDATES, "[Entity Resolution] Target not found in graph or active roster."),
    "EMPTY_CLAIM": (CATEGORY_ABSENT_CANDIDATES, "[Graph Retrieval] Candidate claim text is empty."),
    "PARSER_NO_TOPIC": (CATEGORY_USAGE, "[Prompt Parser] No recognizable topic or attendee extracted from prompt."),
    "REJECTED_EVIDENCE": (CATEGORY_REJECTED_EVIDENCE, "[Grounding Verification] Retrieved candidates failed grounding, provenance, or quarantine checks."),
    "UNVERIFIED_EVIDENCE": (CATEGORY_REJECTED_EVIDENCE, "[Grounding Verification] Retrieved candidates failed grounding, provenance, or quarantine checks."),
    "GROUNDING_REJECTED": (CATEGORY_REJECTED_EVIDENCE, "[Grounding Verification] Retrieved candidates failed grounding, provenance, or quarantine checks."),
    "UNGROUNDED_CLAIM": (CATEGORY_REJECTED_EVIDENCE, "[Grounding Verification] Assertion text is ungrounded in graph evidence."),
    "STALE_ASSERTION": (CATEGORY_REJECTED_EVIDENCE, "[Grounding Verification] Assertion has been superseded or is stale."),
    "SUPERSEDED_STATUS": (CATEGORY_REJECTED_EVIDENCE, "[Grounding Verification] Assertion status has been superseded."),
    "NUMBER_MISMATCH": (CATEGORY_REJECTED_EVIDENCE, "[Grounding Verification] Assertion numbers do not match graph evidence."),
    "NEGATION_CONFLICT": (CATEGORY_REJECTED_EVIDENCE, "[Grounding Verification] Assertion negation conflicts with graph evidence."),
    "CONTRADICTION": (CATEGORY_REJECTED_EVIDENCE, "[Grounding Verification] Assertion contradicts graph evidence."),
    "NO_SPEAKABLE_ASSERTIONS": (CATEGORY_RENDERING_FAILURE, "[Speech Synthesis] Verified assertions exceed the response word budget."),
    "RENDERING_BUDGET_EXCEEDED": (CATEGORY_RENDERING_FAILURE, "[Speech Synthesis] Verified assertions exceed the response word budget."),
    "WORD_BUDGET_EXHAUSTED": (CATEGORY_RENDERING_FAILURE, "[Speech Synthesis] Verified assertions exceed the response word budget."),
    "WORD_BUDGET_EXCEEDED": (CATEGORY_RENDERING_FAILURE, "[Speech Synthesis] Verified assertions exceed the response word budget."),
    "CANNED_FALLBACK_REJECTED": (CATEGORY_RENDERING_FAILURE, "[Speech Synthesis] Canned fallback response was rejected by verifier."),
    "UNGROUNDED_DELIVERY_REJECTED": (CATEGORY_RENDERING_FAILURE, "[Speech Synthesis] Final delivered wording failed grounding verification."),
    "MALFORMED_REPRESENTATION": (CATEGORY_RENDERING_FAILURE, "[Speech Synthesis] Assertion representation is malformed or unresolvable."),
    "INVALID_CLAIM_REPRESENTATION": (CATEGORY_RENDERING_FAILURE, "[Speech Synthesis] Assertion representation is malformed or unresolvable."),
    "DEADLINE_EXHAUSTED": (CATEGORY_DEADLINE_EXHAUSTION, "[Retrieval Deadline] Grounding verification exceeded the latency deadline."),
    "BUDGET_EXHAUSTED": (CATEGORY_DEADLINE_EXHAUSTION, "[Retrieval Deadline] Grounding verification exceeded the latency deadline."),
    "TIMEOUT": (CATEGORY_DEADLINE_EXHAUSTION, "[Retrieval Deadline] Grounding verification exceeded the latency deadline."),
    "INVALID_INTENT": (CATEGORY_USAGE, "[Usage Error] Invalid command parameters."),
    "MISSING_TARGET": (CATEGORY_USAGE, "[Usage Error] Invalid command parameters."),
    "AMBIGUOUS_SPEAKER_IDENTITY": (CATEGORY_USAGE, "[Speaker Identity] Target speaker identity error."),
    "SPEAKER_NOT_IN_ROSTER": (CATEGORY_USAGE, "[Speaker Identity] Target speaker identity error."),
    "SPEAKER_HAS_NO_TURNS": (CATEGORY_USAGE, "[Speaker Identity] Target speaker identity error."),
    "LOW_SPEAKER_CONFIDENCE": (CATEGORY_USAGE, "[Speaker Identity] Speaker diarization confidence too low for attribution."),
    "ZERO_RELEVANT_UTTERANCES": (CATEGORY_STALE_CONTEXT, "[Utterance Selection] No relevant turns address the topic."),
    "NO_RELEVANT_UTTERANCE": (CATEGORY_STALE_CONTEXT, "[Utterance Selection] No relevant turns address the topic."),
    "REJECTED_UNRELATED_EVIDENCE": (CATEGORY_REJECTED_EVIDENCE, "[Evidence Relevance] Retrieved assertions do not match the topic."),
}


def categorize_copilot_terminal_reason(reason_code: str, details: str = "") -> Tuple[str, str]:
    """Maps reason code and details to (category, actionable_user_message)."""
    code = (reason_code or "").strip().upper()
    det = (details or "").strip()

    if code.startswith("NO_GRAPH_GROUNDING"):
        cat = CATEGORY_GRAPH_BINDING
        msg = f"[Graph Grounding] {det or 'No active graph pair loaded at runtime root. Run graph release or check runtime path.'}"
        return cat, msg

    entry = _REASON_MAP.get(code)
    if entry:
        cat, default_msg = entry
        prefix = default_msg.split("] ")[0] + "] "
        msg = f"{prefix}{det}" if det else default_msg
        return cat, msg

    return CATEGORY_UNHANDLED_ERROR, f"[Copilot Error] Unable to complete request ({code}): {det or 'internal error'}"


import functools


@functools.lru_cache(maxsize=8)
def _cached_git_rev(cwd_key: str) -> str:
    try:
        cmd = ["git", "rev-parse", "HEAD"]
        cwd = cwd_key if cwd_key else None
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=2.0)
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return "unknown"


def get_source_revision(repo_root: Optional[Path] = None) -> str:
    """Safely retrieves git revision hash without throwing (cached per repo_root)."""
    cwd_key = str(repo_root.resolve()) if repo_root and repo_root.exists() else ""
    return _cached_git_rev(cwd_key)


def extract_context_freshness(context_model: Any) -> Dict[str, Any]:
    """Extracts summary freshness metrics without duplicating transcript text."""
    if not context_model:
        return {"turn_count": 0, "context_digest": "", "age_seconds": None, "seeds": []}

    try:
        turns = context_model.focus_turns() if hasattr(context_model, "focus_turns") else []
        digest = context_model.compute_hash() if hasattr(context_model, "compute_hash") else ""
        seeds = context_model.retrieval_seeds() if hasattr(context_model, "retrieval_seeds") else []
        last_ts = None
        age_sec: Optional[float] = None
        if turns and isinstance(turns[-1], dict):
            last_ts = next(
                (
                    t.get("timestamp") or t.get("start_time")
                    for t in reversed(turns)
                    if isinstance(t, dict) and (t.get("timestamp") or t.get("start_time")) not in (None, "", "00:00:00")
                ),
                turns[-1].get("timestamp") or turns[-1].get("start_time"),
            )
            t_sec = turns[-1].get("turn_sec")
            latest_time = getattr(context_model, "_latest_time", None)
            if isinstance(t_sec, (int, float)) and isinstance(latest_time, (int, float)):
                age_sec = round(max(0.0, float(latest_time) - float(t_sec)), 3)

        return {
            "turn_count": len(turns),
            "context_digest": digest,
            "last_turn_timestamp": last_ts,
            "age_seconds": age_sec,
            "seeds": seeds[:5] if seeds else [],
        }
    except Exception:
        return {"turn_count": 0, "context_digest": "", "age_seconds": None, "seeds": []}


def _emit_learning_outcome_safe(record: CopilotAttemptRecord, runtime_root: Optional[Path] = None) -> None:
    try:
        from dual_pipeline.learning_adapters.live_pipeline_integrator import get_live_pipeline_integrator
        integrator = get_live_pipeline_integrator(runtime_root=runtime_root)
        details = {
            "attempt_id": record.attempt_id,
            "intent": record.parsed_intent or "respond",
            "target": record.parsed_target,
            "latency_ms": record.latency_ms,
            "stage_latencies_ms": record.stage_latencies_ms,
            "age_seconds": record.age_seconds,
            "temporal_decay_params": record.temporal_decay_params,
            "retrieval_seeds": record.retrieval_seeds,
            "speaker_note_metrics": record.speaker_note_metrics,
        }
        if record.terminal_outcome == "SUCCESS":
            integrator.record_success(
                source_agent="LiveCopilotAgent",
                category=record.parsed_intent or "copilot_delivery",
                stage="L4",
                details=details,
                run_id=record.session_id,
            )
        else:
            integrator.record_abstention(
                source_agent="LiveCopilotAgent",
                category=record.failure_category or "copilot_abstention",
                reason=record.terminal_reason,
                stage="L4",
                details=details,
                run_id=record.session_id,
            )
    except Exception as exc:
        logger.debug("Non-blocking copilot learning integrator emission skipped: %s", exc)


def record_copilot_attempt(
    record: CopilotAttemptRecord,
    output_dir: Optional[Path] = None,
    runtime_root: Optional[Path] = None,
) -> Optional[Path]:
    """Safely persists an attempt record to copilot_attempts.jsonl.
    
    Fail-safe guarantee: Never raises exceptions, never alters return values,
    and never produces false success.
    """
    targets = []
    if output_dir:
        targets.append(Path(output_dir) / "copilot_attempts.jsonl")
    if runtime_root:
        targets.append(Path(runtime_root) / "learning" / "copilot_attempts.jsonl")

    if not targets:
        targets.append(Path.cwd() / "copilot_attempts.jsonl")

    written_path: Optional[Path] = None
    payload = json.dumps(record.to_dict(), ensure_ascii=False) + "\n"

    for path in targets:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
            if written_path is None:
                written_path = path
        except Exception as exc:
            logger.warning("Failed to append copilot attempt to %s: %s", path, exc)

    if runtime_root:
        _emit_learning_outcome_safe(record, runtime_root=runtime_root)
    return written_path


def _resolve_speaker_note_metrics(
    speaker_note_metrics: Optional[Dict[str, Any]], context_model: Any, parsed_target: Optional[str]
) -> Dict[str, Any]:
    if speaker_note_metrics:
        return dict(speaker_note_metrics)
    if context_model and parsed_target and hasattr(context_model, "get_speaker_notes"):
        prof = context_model.get_speaker_notes(parsed_target)
        if prof is not None:
            return {
                "turn_count": getattr(prof, "turn_count", 0),
                "commitments_count": len(getattr(prof, "commitments", [])),
                "risks_count": len(getattr(prof, "objections_and_risks", [])),
            }
    return {}


def create_attempt_record(
    terminal_outcome: str,
    terminal_reason: str,
    details: str = "",
    request: Optional[str] = None,
    parsed_intent: Optional[str] = None,
    parsed_target: Optional[str] = None,
    targets: Optional[List[str]] = None,
    parsed_topic: Optional[str] = None,
    parsed_words: Optional[int] = None,
    session_id: Optional[str] = None,
    resolved_roots: Optional[Dict[str, Optional[str]]] = None,
    context_model: Any = None,
    candidate_counts: Optional[Dict[str, int]] = None,
    rejection_categories: Optional[Dict[str, int]] = None,
    evidence_breakdown: Optional[Dict[str, Any]] = None,
    generation: Optional[str] = None,
    graph_sha256: Optional[str] = None,
    repo_root: Optional[Path] = None,
    latency_ms: Optional[float] = None,
    stage_latencies_ms: Optional[Dict[str, float]] = None,
    temporal_decay_params: Optional[Dict[str, Any]] = None,
    speaker_note_metrics: Optional[Dict[str, Any]] = None,
) -> CopilotAttemptRecord:
    """Builds a populated CopilotAttemptRecord with category and actionable user message."""
    category, user_msg = categorize_copilot_terminal_reason(terminal_reason, details)
    rev = get_source_revision(repo_root)
    freshness = extract_context_freshness(context_model)
    default_decay = {"volatile_half_life_days": 30.0, "structural_half_life_days": 90.0, "cliff_days": 60.0}
    spk_notes = _resolve_speaker_note_metrics(speaker_note_metrics, context_model, parsed_target)
    eff_targets = list(targets) if targets else ([parsed_target] if parsed_target else [])
    return CopilotAttemptRecord(
        attempt_id=f"att-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}",
        timestamp=datetime.now(timezone.utc).isoformat(),
        terminal_outcome=terminal_outcome,
        terminal_reason=terminal_reason,
        failure_category=category if terminal_outcome != "SUCCESS" else None,
        user_message=user_msg if terminal_outcome != "SUCCESS" else "SUCCESS",
        request=request,
        parsed_intent=parsed_intent,
        parsed_target=parsed_target,
        targets=eff_targets,
        parsed_topic=parsed_topic,
        parsed_words=parsed_words,
        session_id=session_id,
        resolved_roots=resolved_roots or {},
        source_revision=rev,
        context_freshness=freshness,
        candidate_counts=candidate_counts or {},
        rejection_categories=rejection_categories or {},
        evidence_breakdown=evidence_breakdown or {},
        generation=generation,
        graph_sha256=graph_sha256,
        error_details=details or None,
        latency_ms=latency_ms,
        stage_latencies_ms=dict(stage_latencies_ms or {}),
        age_seconds=freshness.get("age_seconds"),
        temporal_decay_params=dict(temporal_decay_params or default_decay),
        retrieval_seeds=list(freshness.get("seeds") or []),
        speaker_note_metrics=spk_notes,
    )
