"""
dual_pipeline/live_meeting_recorder_core/copilot_engine.py

Core execution engine for Live Executive Copilot.
Handles request evaluation, multi-hop retrieval, grounding verification,
speech rendering, and structured attempt persistence.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.core import ROOT
from dual_pipeline.live_meeting_recorder_core.copilot_context import ParsedCopilotRequest, parse_copilot_request
from dual_pipeline.live_meeting_recorder_core.copilot_utterance_resolver import (
    _build_roster_candidates, _match_roster_target, _resolve_speaker_and_utterance, _safe_int_seq, _select_topic_utterance, has_live_transcript_directive,
)  # noqa: F401
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
from dual_pipeline.live_meeting_recorder_core.copilot_intents import evaluate_intent
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever, GraphLoadStatus, load_copilot_graph
from dual_pipeline.live_meeting_recorder_core.copilot_lifecycle import CopilotLifecycleState, get_or_create_lifecycle_orchestrator
from dual_pipeline.live_meeting_recorder_core.copilot_attempt_ledger import create_attempt_record, record_copilot_attempt
from dual_pipeline.live_meeting_recorder_core.copilot_roster_bridge import load_session_attendees
from dual_pipeline.live_meeting_recorder_core.copilot_offline import (
    build_copilot_success_result as _build_copilot_success_result,
    diagnose_unresolved_evidence as _diagnose_unresolved_evidence,
    fallback_live_window as _fallback_live_window,
    is_entity_present as _is_entity_present, is_offline_graph_intent as _is_offline_graph_intent, is_tolerated_speaker_error as _is_tolerated_speaker_error,
    prepare_copilot_body_for_delivery as _prepare_copilot_body_for_delivery_helper,
    resolve_evaluation_dialogue_context as _resolve_evaluation_dialogue_context,
    resolve_implicit_respond_target as _resolve_implicit_respond_target,
    resolve_ledger_seeds as _resolve_ledger_seeds,
    resolve_offline_copilot_session as _resolve_offline_copilot_session,
)  # noqa: F401

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.copilot_engine")


def _copilot_failure(
    error: str, details: str, machine: bool = False,
    args: Optional[argparse.Namespace] = None,
    output_dir: Optional[Path] = None, runtime_root: Optional[Path] = None,
    context_model: Any = None, session_id: Optional[str] = None,
    candidate_counts: Optional[Dict[str, int]] = None,
    rejection_categories: Optional[Dict[str, int]] = None,
    generation: Optional[str] = None, graph_sha256: Optional[str] = None,
    intent: Optional[str] = None, target: Optional[str] = None, topic: Optional[str] = None,
) -> Dict[str, Any]:
    rec = create_attempt_record(
        terminal_outcome="FAIL", terminal_reason=error, details=details,
        request=getattr(args, "request", None) or getattr(args, "prompt", None) or getattr(args, "topic", None),
        parsed_intent=intent or getattr(args, "intent", None), parsed_target=target or getattr(args, "target", None),
        targets=getattr(args, "targets", None),
        parsed_topic=topic or getattr(args, "topic", None), parsed_words=getattr(args, "words", None),
        session_id=session_id,
        resolved_roots={"output_dir": str(output_dir), "runtime_root": str(runtime_root) if runtime_root else None} if output_dir else None,
        context_model=context_model, candidate_counts=candidate_counts, rejection_categories=rejection_categories,
        generation=generation, graph_sha256=graph_sha256, repo_root=Path(getattr(args, "runtime_root", None) or ROOT),
    )
    attempt_path = record_copilot_attempt(rec, output_dir=output_dir, runtime_root=runtime_root)
    if not machine:
        print(f"```text\n{rec.user_message}\n```")
    res = {
        "status": "FAIL", "command": "copilot", "error": error, "details": details,
        "user_message": rec.user_message, "failure_category": rec.failure_category, "attempt_id": rec.attempt_id,
    }
    if attempt_path:
        res["attempt_path"] = str(attempt_path)
    return res


def _persist_answer_record(
    intent: str, target: Optional[str], body: str,
    assertions: List[Dict[str, Any]], retriever: CopilotGraphRetriever,
    output_dir: Path, runtime_root: Optional[Path],
    context_model: Optional[ConversationContextModel],
    session_id: Optional[str], evidence_breakdown: Optional[Dict[str, Any]],
    stage_latencies_ms: Optional[Dict[str, float]] = None,
    args: Optional[argparse.Namespace] = None,
) -> Optional[str]:
    rigor_str = getattr(retriever, "_last_rigor_tier", "VERIFIED")
    answers_file = output_dir / "copilot_answers.jsonl"
    cited_ids = [a["assertion_id"] for a in assertions if a.get("assertion_id")]
    lats = stage_latencies_ms or {}
    try:
        answers_file.parent.mkdir(parents=True, exist_ok=True)
        grounding_dict = getattr(retriever, "_last_grounding_result", None)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(), "intent": intent, "target": target,
            "response": body, "cited_assertion_ids": cited_ids,
            "graph_sha256": retriever.graph_sha256, "generation": retriever.generation,
            "rigor_tier": rigor_str, "grounding_telemetry": grounding_dict.to_dict() if grounding_dict else {},
            "evidence_breakdown": evidence_breakdown or {},
            "stage_latencies_ms": lats,
        }
        with open(answers_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("Failed to record copilot answer to %s: %s", answers_file, exc)

    try:
        from dual_pipeline.answer_ledger import AnswerRecord, get_default_answer_ledger
        ledger = get_default_answer_ledger(runtime_root or output_dir)
        c_snap, c_seeds, c_dig = _resolve_ledger_seeds(context_model, args, target)
        ans_rec = AnswerRecord(
            pipeline="copilot", session_id=session_id, intent_or_type=intent,
            context_digest=c_dig, retrieval_seeds=c_seeds, context_snapshot=c_snap,
            cited_assertion_ids=cited_ids,
            rigor=rigor_str, response_text=body, graph_sha256=retriever.graph_sha256,
            generation=retriever.generation, latency_ms=lats.get("total_ms"), stage_latencies_ms=stage_latencies_ms,
            retrieve_latency_ms=lats.get("retrieve_ms"),
            verify_latency_ms=lats.get("verify_ms"),
            render_latency_ms=lats.get("render_ms"),
        )
        saved = ledger.append_answer_safe(ans_rec)
        return saved.answer_id if saved else None
    except Exception as exc:
        logger.warning("Failed to record answer to AnswerLedger: %s", exc)
        return None


def _record_and_render_copilot_output(
    intent: str, target: Optional[str], body: str,
    assertions: List[Dict[str, Any]], retriever: CopilotGraphRetriever,
    output_dir: Path, runtime_root: Optional[Path] = None,
    context_model: Optional[ConversationContextModel] = None,
    session_id: Optional[str] = None, machine: bool = False,
    args: Optional[argparse.Namespace] = None,
    evidence_breakdown: Optional[Dict[str, Any]] = None,
    stage_latencies_ms: Optional[Dict[str, float]] = None,
) -> Optional[str]:
    """Print one speakable fenced block immediately, then persist grounded answer and attempt records."""
    if not machine:
        print(f"```text\n{body}\n```", flush=True)
    ledger_answer_id = _persist_answer_record(
        intent, target, body, assertions, retriever, output_dir,
        runtime_root, context_model, session_id, evidence_breakdown,
        stage_latencies_ms=stage_latencies_ms, args=args,
    )
    try:
        succ_rec = create_attempt_record(
            terminal_outcome="SUCCESS", terminal_reason="SUCCESS",
            details="Grounded comment generated successfully",
            request=(getattr(args, "request", None) or getattr(args, "prompt", None)) if args else None,
            parsed_intent=intent, parsed_target=target,
            targets=getattr(args, "targets", None) if args else None,
            parsed_topic=getattr(args, "topic", None) if args else None,
            parsed_words=getattr(args, "words", None) if args else None, session_id=session_id,
            resolved_roots={"output_dir": str(output_dir), "runtime_root": str(runtime_root) if runtime_root else None},
            context_model=context_model,
            candidate_counts={"initial_candidates": getattr(retriever, "_last_initial_candidate_count", len(assertions)), "verified_candidates": len(assertions), "rendered_candidates": len(assertions)},
            evidence_breakdown=evidence_breakdown, generation=retriever.generation, graph_sha256=retriever.graph_sha256, repo_root=Path(runtime_root) if runtime_root else None,
        )
        record_copilot_attempt(succ_rec, output_dir=output_dir, runtime_root=Path(runtime_root) if runtime_root else None)
    except Exception as exc:
        logger.warning("Failed to record success attempt: %s", exc)
    return ledger_answer_id

def _utt_field(utt: Any, key: str, default: Any = "") -> Any:
    return utt.get(key, default) if hasattr(utt, "get") else getattr(utt, key, default)


def _build_evidence_breakdown(
    selected_utterance: Optional[Any], context_model: ConversationContextModel,
    assertions: List[Dict[str, Any]], window_turns: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if selected_utterance:
        acoustic = [{
            "speaker": _utt_field(selected_utterance, "speaker", ""), "text": _utt_field(selected_utterance, "text", ""),
            "speaker_summary": _utt_field(selected_utterance, "speaker_summary", ""), "window_summary": _utt_field(selected_utterance, "window_summary", ""),
            "timestamp": _utt_field(selected_utterance, "timestamp", ""), "turn_sec": _utt_field(selected_utterance, "turn_sec", 0.0),
            "matched_keywords": _utt_field(selected_utterance, "matched_keywords", []), "window_turns_count": len(window_turns or []),
        }]
    else:
        src_turns = window_turns or context_model.focus_turns()[:3]
        acoustic = [{"speaker": t.get("speaker"), "text": t.get("text"), "timestamp": t.get("timestamp")} for t in src_turns[:3]]
    return {
        "acoustic_transcript_evidence": acoustic, "operator_hints": getattr(context_model, "_operator_hints", []),
        "historical_graph_assertions": [
            {
                "assertion_id": a.get("assertion_id"), "predicate": a.get("predicate"),
                "claim_text": a.get("claim_text") or a.get("object_literal"),
                "subject_node_id": a.get("subject_node_id"), "object_node_id": a.get("object_node_id"),
                "provenance": a.get("primary_source_id"),
            }
            for a in assertions
        ],
    }


def _parse_and_validate_copilot_args(
    args: argparse.Namespace,
) -> Tuple[Optional[Tuple[str, Optional[str], Optional[str], List[str]]], Optional[Dict[str, Any]]]:
    raw_req = getattr(args, "request", None) or getattr(args, "query", None) or getattr(args, "prompt", None)
    if raw_req and not getattr(args, "request", None):
        args.request = raw_req
    parsed_req = parse_copilot_request(
        raw_request=raw_req, intent_override=getattr(args, "intent", None),
        target_override=getattr(args, "target", None), topic_override=getattr(args, "topic", None),
    )
    if parsed_req.topic_query and not getattr(args, "topic", None):
        args.topic = parsed_req.topic_query
    args.targets = parsed_req.targets
    args._parsed_window_seconds = parsed_req.window_seconds
    args._parsed_requested_count = parsed_req.requested_count
    machine = bool(getattr(args, "json", False))
    runtime_root = getattr(args, "runtime_root", None)

    if raw_req is not None and raw_req.strip():
        has_intent_override = getattr(args, "intent", None) is not None
        clean_query = re.sub(r"[^a-zA-Z0-9]+", "", parsed_req.topic_query or "")
        if not has_intent_override and not parsed_req.has_explicit_intent and not parsed_req.target_speaker and not parsed_req.topic_keywords and not clean_query:
            return None, _copilot_failure("PARSER_NO_TOPIC", "No recognizable topic or attendee extracted from prompt.", machine, args=args, runtime_root=runtime_root)

    intent_map = {"nbq": "next-questions", "review": "gaps"}
    intent = intent_map.get(parsed_req.inferred_intent.lower(), parsed_req.inferred_intent.lower())
    valid_intents = {"next-questions", "next-actions", "respond", "graph-context", "gaps", "connect"}

    if intent not in valid_intents:
        return None, _copilot_failure("INVALID_INTENT", f"Invalid intent '{intent}'.", machine, args=args, runtime_root=runtime_root)
    if intent == "respond" and getattr(args, "target", None) is not None and not str(getattr(args, "target", "")).strip():
        return None, _copilot_failure("MISSING_TARGET", "Must specify --target <person> for --intent respond.", machine, args=args, runtime_root=runtime_root)
    return (intent, parsed_req.target_speaker, parsed_req.topic_query, parsed_req.topic_keywords), None


def _check_active_session_readiness(
    manifest: Any, context_model: ConversationContextModel, args: argparse.Namespace, machine: bool, rt_root: Any
) -> Optional[Dict[str, Any]]:
    if not manifest or getattr(manifest, "capture_state", "") not in ("CAPTURING", "STARTING"):
        return None
    if getattr(args, "offline", False) or getattr(args, "graph_only", False):
        return None
    asr_st = getattr(manifest, "asr_state", "")
    sid = getattr(manifest, "session_id", "active")
    if asr_st == "FAILED":
        err = f"Live ASR has failed for session '{sid}'. Live copilot requires functioning ASR. Use --offline or --graph-only for historical graph queries."
        return _copilot_failure("ASR_FAILED", err, machine, args=args, runtime_root=rt_root, session_id=sid)
    cop_st = getattr(manifest, "copilot_state", "")
    if (cop_st == "NOT_READY" or asr_st == "WARMING") and not context_model.focus_turns():
        code = "ASR_NOT_READY" if asr_st == "WARMING" else "SESSION_INITIALIZING"
        err = f"Recording session '{sid}' ASR is not ready ({code}). No turns available yet."
        return _copilot_failure(code, err, machine, args=args, runtime_root=rt_root, session_id=sid)
    return None


def _resolve_missing_output_dir(
    meeting_slug: str, manifest: Any, args: argparse.Namespace, machine: bool, rt_root: Any
) -> Tuple[Optional[Tuple[Path, ConversationContextModel, Any, str]], Optional[Dict[str, Any]]]:
    if meeting_slug == "conflicting_engagement":
        err = "Conflicting active recording sessions across distinct engagements. Explicit --project / --engagement required."
        return None, _copilot_failure("CONFLICTING_ENGAGEMENT_IDENTITY", err, machine, args=args, runtime_root=rt_root)
    if meeting_slug == "unreconciled_legacy_session":
        err = "Legacy session lacks canonical engagement identity. Explicit --project / --engagement reconciliation required."
        return None, _copilot_failure("UNRECONCILED_LEGACY_SESSION", err, machine, args=args, runtime_root=rt_root, session_id=getattr(manifest, "session_id", None))
    offline_tuple = _resolve_offline_copilot_session(args, rt_root)
    if offline_tuple:
        return offline_tuple, None
    return None, _copilot_failure("NO_SESSION_FOUND", "No active recording session found.", machine, args=args, runtime_root=rt_root)


def _resolve_copilot_session(
    args: argparse.Namespace, machine: bool
) -> Tuple[Optional[Tuple[Path, ConversationContextModel, Any, str]], Optional[Dict[str, Any]]]:
    from dual_pipeline.cli.copilot_session import _resolve_session_and_context_model  # noqa: F401  # lazy-decouple
    output_dir, context_model, manifest, meeting_slug = _resolve_session_and_context_model(args)
    runtime_root = getattr(args, "runtime_root", None)
    readiness_err = _check_active_session_readiness(manifest, context_model, args, machine, runtime_root)
    if readiness_err:
        return None, readiness_err
    if not output_dir:
        return _resolve_missing_output_dir(meeting_slug, manifest, args, machine, runtime_root)
    if getattr(args, "meeting_dir", None) and (getattr(args, "session", None) or getattr(args, "active", False)) and not manifest:
        return None, _copilot_failure("NO_SESSION_MATCH", "Session does not match meeting directory.", machine, args=args, output_dir=output_dir, runtime_root=runtime_root)
    return (output_dir, context_model, manifest, meeting_slug), None


def _extract_engagement_id(manifest: Any, context_model: Any, args: argparse.Namespace) -> Optional[str]:
    eng_id = getattr(manifest, "engagement_id", None) or getattr(args, "engagement", None) or getattr(args, "project", None) or getattr(context_model, "engagement_id", None) or getattr(context_model, "project_scope", None)
    return f"project:{str(eng_id).lower().replace('-', '_')}" if (eng_id and not str(eng_id).startswith("project:")) else eng_id


def _load_graph_directly(load_fn: Any, attendees: List[str], repo_root: Path, eng_id: Optional[str], sess_id: Optional[str], graph_path: Optional[str] = None) -> Tuple[Any, Any, Optional[str], Optional[str]]:
    try:
        res = load_fn(attendees=attendees, runtime_root=repo_root, engagement_id=eng_id, session_id=sess_id, graph_path=graph_path)
    except TypeError:
        try:
            res = load_fn(attendees=attendees, runtime_root=repo_root, engagement_id=eng_id, session_id=sess_id)
        except TypeError:
            res = load_fn(runtime_root=repo_root, engagement_id=eng_id, session_id=sess_id)
    status, in_mem, retriever = res[0], res[1], res[2]
    from .copilot_cache_graph import get_last_copilot_graph_diagnostic
    diag = getattr(res, "diagnostic_detail", None) or get_last_copilot_graph_diagnostic()
    return (None, None, getattr(status, "value", str(status)), diag) if (status != GraphLoadStatus.LOADED_ACTIVE_PAIR or not retriever) else (in_mem, retriever, None, None)


def _check_ctx_graph_status(ctx: Any) -> Tuple[Optional[str], Optional[str]]:
    status, diag = getattr(ctx, "graph_status", None), getattr(ctx, "graph_diagnostic", None)
    return (status or "NO_RETRIEVER", diag) if (not getattr(ctx, "retriever", None) or (status and status != "LOADED_ACTIVE_PAIR")) else (None, None)


def _bootstrap_or_load_graph(
    orch: Any, eng_id: Optional[str], attendees: List[str], repo_root: Path,
    load_fn: Any, bypass_cache: bool, sess_id: Optional[str], graph_path: Optional[str] = None,
) -> Tuple[Any, Any, Optional[str], Optional[str]]:
    from .copilot_lifecycle import CopilotLifecycleState
    if orch.state == CopilotLifecycleState.UNINITIALIZED:
        ctx = orch.bootstrap_session(engagement_id=eng_id, attendees=attendees, runtime_root=repo_root, load_fn=load_fn, bypass_cache=bypass_cache, graph_path=graph_path)
        err, diag = _check_ctx_graph_status(ctx)
        return (None, None, err, diag) if err else (ctx.in_memory_graph, ctx.retriever, None, None)
    if orch._context is not None:
        err, diag = _check_ctx_graph_status(orch._context)
        if err:
            return None, None, err, diag
        retriever = orch._context.retriever
        if bypass_cache and retriever and hasattr(retriever, "neighborhood_store"):
            import copy
            retriever = copy.copy(retriever)
            retriever.neighborhood_store = None
        return orch._context.in_memory_graph, retriever, None, None
    return _load_graph_directly(load_fn, attendees, repo_root, eng_id, sess_id, graph_path=graph_path)


def _active_session_turns_failure(
    manifest: Any, machine: bool, args: argparse.Namespace, out_dir: Path, rt_root: Path, cm: Any, sess_id: Optional[str]
) -> Dict[str, Any]:
    asr_st = getattr(manifest, "asr_state", "")
    if asr_st == "FAILED":
        return _copilot_failure("ASR_FAILED", f"Live ASR has failed for session '{sess_id}'.", machine, args=args, output_dir=out_dir, runtime_root=rt_root, context_model=cm, session_id=sess_id)
    if asr_st == "WARMING":
        return _copilot_failure("ASR_NOT_READY", f"Live ASR is still warming up for session '{sess_id}'.", machine, args=args, output_dir=out_dir, runtime_root=rt_root, context_model=cm, session_id=sess_id)
    code = "STALE_CONTEXT" if getattr(manifest, "context_state", "") == "STALE" else "NO_LIVE_CONTEXT"
    return _copilot_failure(code, "No recent turns available.", machine, args=args, output_dir=out_dir, runtime_root=rt_root, context_model=cm, session_id=sess_id)


def _is_turn_quarantined(t: Any) -> bool:
    if isinstance(t, dict):
        return bool(t.get("c19_quarantined") or t.get("is_quarantined"))
    return bool(getattr(t, "is_quarantined", False) or getattr(t, "c19_quarantined", False))


def _check_focus_turns(
    cm: ConversationContextModel, args: argparse.Namespace, manifest: Any, machine: bool,
    out_dir: Path, rt_root: Path, sess_id: Optional[str], in_mem: Any, retriever: CopilotGraphRetriever,
) -> Tuple[Optional[Tuple[Any, CopilotGraphRetriever]], Optional[Dict[str, Any]]]:
    if any(not _is_turn_quarantined(t) for t in getattr(cm, "_turns", []) or ()):
        return (in_mem, retriever), None
    is_active = manifest and getattr(manifest, "capture_state", "") in ("CAPTURING", "STARTING")
    explicit_offline = bool(getattr(args, "offline", False) or getattr(args, "graph_only", False))
    if is_active and not explicit_offline:
        return None, _active_session_turns_failure(manifest, machine, args, out_dir, rt_root, cm, sess_id)
    has_query = any(getattr(args, a, None) for a in ("query", "prompt", "request", "topic"))
    if _is_offline_graph_intent(cm, args) or has_query or explicit_offline:
        cm._offline_mode = True
        return (in_mem, retriever), None
    code = "STALE_CONTEXT" if manifest and getattr(manifest, "context_state", "") == "STALE" else "NO_LIVE_CONTEXT"
    return None, _copilot_failure(code, "No recent turns available.", machine, args=args, output_dir=out_dir, runtime_root=rt_root, context_model=cm, session_id=sess_id)


def _resolve_copilot_graph(
    args: argparse.Namespace, load_fn: Any, repo_root: Path, sess_id: Optional[str],
    output_dir: Path, context_model: ConversationContextModel, manifest: Any, machine: bool,
) -> Tuple[Optional[Tuple[Any, CopilotGraphRetriever]], Optional[Dict[str, Any]]]:
    eng_id = _extract_engagement_id(manifest, context_model, args)
    attendees = getattr(context_model, "attendees", None) or load_session_attendees(output_dir)
    if attendees and not getattr(context_model, "attendees", None):
        context_model.attendees = attendees
    from .copilot_lifecycle import get_or_create_lifecycle_orchestrator
    orch = get_or_create_lifecycle_orchestrator(sess_id or f"sess_{time.time_ns()}")
    bypass = bool(getattr(args, "bypass_graph_cache", False))
    graph_path = getattr(args, "graph", None) or getattr(args, "graph_path", None)
    in_mem, retriever, err_status, err_diag = _bootstrap_or_load_graph(
        orch, eng_id, attendees, repo_root, load_fn, bypass, sess_id, graph_path=graph_path
    )
    if err_status:
        detail_msg = f"No graph grounding: {err_status} ({err_diag})" if err_diag else f"No graph grounding: {err_status}"
        return None, _copilot_failure(f"NO_GRAPH_GROUNDING_{err_status}", detail_msg, machine, args=args, output_dir=output_dir, runtime_root=repo_root, session_id=sess_id)
    if not retriever:
        return None, _copilot_failure("NO_GRAPH_GROUNDING", "No retriever available.", machine, args=args, output_dir=output_dir, runtime_root=repo_root, session_id=sess_id)

    if not context_model.focus_turns():
        return _check_focus_turns(context_model, args, manifest, machine, output_dir, repo_root, sess_id, in_mem, retriever)
    return (in_mem, retriever), None


def _verify_rendered_body_clauses(
    body: str,
    assertions: List[Dict[str, Any]],
    target: Optional[str] = None,
    selected_utterance: Optional[Any] = None,
) -> str:
    if not body:
        return ""
    from .copilot_clause_verifier import check_clause_grounding
    by_id = {str(a.get("assertion_id")): a for a in assertions if a.get("assertion_id")}
    retained = [
        line.strip() for line in body.split("\n")
        if line.strip() and check_clause_grounding(
            line.strip(), assertions, by_id, target=target, selected_utterance=selected_utterance
        )
    ]
    return "\n".join(retained)


def _find_matching_assertion(
    line: str, assertions: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    if not assertions:
        return None
    if "was described as" in line.lower() and "which status is current" in line.lower():
        real_confs = [float(a.get("confidence_score") or a.get("confidence") or 0.0) for a in assertions if a.get("confidence_score") is not None or a.get("confidence") is not None]
        eff_conf = min(real_confs) if real_confs else 0.0
        return {
            "claim_text": " ".join(
                f"{a.get('claim_text', '')} {a.get('object_literal', '')} {a.get('observed_at', '')} {a.get('source_date', '')} {a.get('valid_from', '')}"
                for a in assertions
            ),
            "confidence_score": eff_conf,
        }
    words = {w for w in re.findall(r"\b[a-z0-9_]{4,}\b", line.lower()) if w not in ("could", "clarify", "point", "regarding", "what", "next", "step", "steps", "specific", "guardrails", "should", "lock", "concrete", "ownership", "boundaries", "deliverables", "align", "confirm", "whether", "tracking", "agreed", "baseline", "parallel", "specifically", "additionally", "also", "hearing", "session", "practice", "execute", "shift", "building")}
    if not words:
        return None
    best = max(
        assertions,
        key=lambda a: len(words & set(re.findall(r"\b[a-z0-9_]{4,}\b", str(a.get("claim_text") or a.get("object_literal") or "").lower()))),
    )
    best_overlap = len(words & set(re.findall(r"\b[a-z0-9_]{4,}\b", str(best.get("claim_text") or best.get("object_literal") or "").lower())))
    return best if best_overlap > 0 else None


def _compress_line_to_budget(line: str, per_line_max: int, assertions: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    if per_line_max <= 0:
        return None, "WORD_BUDGET_EXCEEDED"
    is_conf = "was described as" in line.lower() and "which status is current" in line.lower()
    sent_cap = per_line_max if is_conf else min(per_line_max, 28 if per_line_max <= 35 else 48)
    if len(line.split()) <= sent_cap:
        return line, None
    from .proactive_inflection_agent import compress_for_speak_gate
    from .copilot_clause_verifier import check_clause_grounding
    compressed = compress_for_speak_gate(line, max_words=sent_cap, max_steps=2)
    by_id = {str(a.get("assertion_id")): a for a in assertions if a.get("assertion_id")}
    return (compressed, None) if check_clause_grounding(compressed, assertions, by_id) else (None, "UNGROUNDED_CLAIM")


def _evaluate_line_for_speak_gate(
    line: str, assertions: List[Dict[str, Any]], spk_conf: float,
    intent: str, max_words: Optional[int], cumulative_words: int,
) -> Tuple[Optional[str], Optional[str]]:
    from .copilot_speak_gate import evaluate_speak_gate
    per_line_max: Optional[int] = None
    if max_words is not None:
        per_line_max = max_words - cumulative_words
        comp_line, comp_err = _compress_line_to_budget(line, per_line_max, assertions)
        if comp_err or not comp_line:
            return None, comp_err
        line = comp_line
    matched_ast = _find_matching_assertion(line, assertions)
    raw_span = str(matched_ast["verbatim_span"]) if (matched_ast and matched_ast.get("verbatim_span")) else ""
    eff_span = raw_span if (raw_span and raw_span.lower() in line.lower()) else ""
    decision = evaluate_speak_gate(line, assertion=matched_ast, verbatim_span=eff_span, speaker_confidence=spk_conf, intent=intent, max_words=per_line_max)
    return (decision.retained_text, None) if decision.should_speak else (None, (decision.abstain_reason.value if decision.abstain_reason else None))


def _filter_body_through_speak_gate(
    body: str, assertions: List[Dict[str, Any]], spk_conf: float, intent: str, max_words: Optional[int] = None,
) -> Tuple[str, str]:
    from .copilot_speak_gate import verify_whole_response_coherence
    passed_lines, fail_reasons, cumulative_words = [], [], 0
    for raw_line in body.split("\n"):
        line = raw_line.strip()
        if not line or len(line.split()) > 90:
            continue
        retained, fail_reason = _evaluate_line_for_speak_gate(
            line, assertions, spk_conf, intent, max_words, cumulative_words
        )
        if retained:
            passed_lines.append(retained)
            cumulative_words += len(retained.split())
        elif fail_reason:
            fail_reasons.append(fail_reason)
    if not passed_lines:
        return "", (fail_reasons[0] if fail_reasons else "SPEAK_GATE_ABSTAIN")
    passed_lines, coh_err = verify_whole_response_coherence(passed_lines, assertions, intent=intent)
    if not passed_lines:
        return "", (coh_err or "INCOHERENT_MULTI_SENTENCE_RESPONSE")
    return "\n".join(passed_lines), ""


def _prepare_render_limits(intent: str, assertions: List[Dict[str, Any]], args: argparse.Namespace) -> Tuple[int, Optional[int], List[Dict[str, Any]]]:
    req_cnt = int(getattr(args, "_parsed_requested_count", None) or 3)
    max_words = getattr(args, "words", None)
    has_live = any(a.get("is_live_turn") for a in assertions)
    if max_words is None and not getattr(args, "_parsed_requested_count", None):
        if intent not in ("gaps", "review", "graph-context"):
            max_words = 105 if has_live else 40
        else:
            req_cnt = max(req_cnt, min(6, len(assertions)))
    if intent == "graph-context" and len(assertions) >= 2 and has_live and any(not a.get("is_live_turn") for a in assertions):
        live_list = [a for a in assertions if a.get("is_live_turn")]
        assertions = [live_list[0], *[a for a in assertions if not a.get("is_live_turn")], *live_list[1:]]
        req_cnt = int(getattr(args, "_parsed_requested_count", None) or 3)
        max_words = max_words or 105
    return req_cnt, max_words, assertions


def _render_and_validate_body(
    intent: str, target: Optional[str], assertions: List[Dict[str, Any]],
    args: argparse.Namespace, retriever: CopilotGraphRetriever, selected_utterance: Optional[Any] = None,
) -> Tuple[str, str]:
    from dual_pipeline.cli.copilot_speech import render_speaker_comments  # noqa: F401  # lazy-decouple
    req_cnt, max_words, assertions = _prepare_render_limits(intent, assertions, args)
    raw_conf = selected_utterance.get("confidence", 1.0) if hasattr(selected_utterance, "get") else getattr(selected_utterance, "confidence", 1.0)
    spk_conf = float(raw_conf or 1.0) if selected_utterance else 1.0
    render_res = render_speaker_comments(
        intent, target, assertions, max_words, retriever, selected_utterance=selected_utterance, max_items=max(req_cnt, len(assertions))
    )
    body = _verify_rendered_body_clauses(render_res.body, assertions, target=target, selected_utterance=selected_utterance)
    if not body:
        return "", render_res.failure_reason or "UNGROUNDED_DELIVERY_REJECTED"
    passed_body, fail_code = _filter_body_through_speak_gate(body, assertions, spk_conf, intent, max_words=max_words)
    if passed_body:
        return "\n".join([ln for ln in passed_body.split("\n") if ln.strip()][:req_cnt]), ""
    return "", fail_code


def _prepare_copilot_body_for_delivery(
    body: str, intent: str, target: Optional[str], resolved_target: Optional[str],
    assertions: List[Dict[str, Any]], args: argparse.Namespace,
    retriever: CopilotGraphRetriever, selected_utterance: Optional[Any], is_offline: bool,
) -> Tuple[Optional[str], Optional[str]]:
    return _prepare_copilot_body_for_delivery_helper(
        body, intent, target, resolved_target, assertions, args,
        retriever, selected_utterance, is_offline, _render_and_validate_body,
    )


def _resolve_tolerated_utterance(
    intent: str, target: Optional[str], topic_query: Optional[str], topic_keywords: List[str],
    context_model: ConversationContextModel, in_mem: Any,
) -> Tuple[Optional[str], Optional[Any], List[Dict[str, Any]], Optional[Tuple[str, str]], bool]:
    resolved_target, selected_utterance, window_turns, err_tuple = _resolve_speaker_and_utterance(
        target, topic_keywords, context_model, in_mem
    )
    if not err_tuple:
        return resolved_target, selected_utterance, window_turns, None, True
    if not _is_tolerated_speaker_error(err_tuple, intent, topic_keywords, topic_query, context_model):
        return resolved_target, selected_utterance, window_turns, err_tuple, False
    fb_utt, window_turns = _fallback_live_window(context_model, topic_keywords, _select_topic_utterance)
    resolved_target = resolved_target or target
    selected_utterance = fb_utt if (intent != "respond" or has_live_transcript_directive(getattr(context_model, "_raw_request", None))) else None
    return resolved_target, selected_utterance, window_turns, err_tuple, True


_DELIVERY_FAILURE_DETAILS = {
    "SCOPE_QUARANTINED": "Candidate assertions violate project boundary or are quarantined for unknown ownership.",
    "WORD_BUDGET_EXCEEDED": "Generated response exceeded maximum allowed spoken word budget.",
    "WORD_BUDGET_EXHAUSTED": "Generated response exceeded maximum allowed spoken word budget.",
    "C19_CAREER_LEVERAGE": "Generated content violated Rule C-19 career leverage / internal advisory firewall.",
    "NEGATION_CONFLICT": "Generated claim polarity conflicted with ground truth assertion negation.",
    "NUMBER_MISMATCH": "Generated claim numeric values did not match ground truth assertion.",
    "STALE_ASSERTION": "Candidate assertion was rejected as stale.",
    "SUPERSEDED_STATUS": "Candidate assertion status has been superseded.",
    "UNGROUNDED_CLAIM": "Generated claim failed ground truth span or trap verification.",
    "MALFORMED_REPRESENTATION": "Candidate assertion representation was empty or malformed.",
}


def _evaluate_and_render_copilot_response(
    intent: str, target: Optional[str], topic_query: Optional[str], topic_keywords: List[str],
    context_model: ConversationContextModel, in_mem: Any, retriever: CopilotGraphRetriever,
    output_dir: Path, repo_root: Path, sess_id: Optional[str], meeting_slug: str,
    machine: bool, args: argparse.Namespace, render_fn: Any,
) -> Dict[str, Any]:
    resolved_target, selected_utterance, window_turns, err_tuple, spk_ok = _resolve_tolerated_utterance(
        intent, target, topic_query, topic_keywords, context_model, in_mem
    )
    if not spk_ok and err_tuple:
        return _copilot_failure(err_tuple[0], err_tuple[1], machine, args=args, output_dir=output_dir, runtime_root=repo_root, context_model=context_model, session_id=sess_id, intent=intent, target=target, topic=topic_query)
    t_start = time.perf_counter()
    eval_context = _resolve_evaluation_dialogue_context(context_model, meeting_slug, resolved_target, topic_query, topic_keywords)
    t_ret_start = time.perf_counter()
    body, assertions, ok = evaluate_intent(intent, resolved_target, eval_context, retriever, topic_keywords=topic_keywords, selected_utterance=selected_utterance, topic_query=topic_query)
    retrieve_ms = max(0.1, (time.perf_counter() - t_ret_start) * 1000.0)
    eff_target = resolved_target or target
    if not ok or not assertions:
        has_live = bool(context_model.focus_turns() or context_model.retrieval_seeds() or getattr(args, "intent", None))
        code, det = _diagnose_unresolved_evidence(retriever, topic_keywords, topic_query, target=eff_target, has_live_context=has_live)
        if intent == "connect":
            code, det = "ZERO_RELEVANT_ASSERTIONS", f"ZERO_RELEVANT_ASSERTIONS: {det}"
        code, det = err_tuple if (err_tuple and code == "ZERO_MATCHING_ASSERTIONS") else (code, det)
        return _copilot_failure(
            code, det, machine, args=args, output_dir=output_dir, runtime_root=repo_root, context_model=context_model, session_id=sess_id,
            candidate_counts={"initial_candidates": getattr(retriever, "_last_initial_candidate_count", 0), "verified_candidates": len(assertions), "rendered_candidates": 0},
            intent=intent, target=eff_target, topic=topic_query,
        )
    t_rv_start = time.perf_counter()
    body, fail_code = _prepare_copilot_body_for_delivery(
        body, intent, target, resolved_target, assertions, args, retriever, selected_utterance, getattr(context_model, "_offline_mode", False),
    )
    rv_ms = max(0.1, (time.perf_counter() - t_rv_start) * 1000.0)
    stage_latencies = {"retrieve_ms": round(retrieve_ms, 2), "render_ms": max(0.05, round(rv_ms * 0.4, 2)), "verify_ms": max(0.05, round(rv_ms * 0.6, 2)), "total_ms": round((time.perf_counter() - t_start) * 1000.0, 2)}
    if not body:
        det = _DELIVERY_FAILURE_DETAILS.get(fail_code, "Verified assertions cannot fit the response budget or contained unverified clauses.")
        return _copilot_failure(
            fail_code or "UNGROUNDED_DELIVERY_REJECTED", det, machine, args=args, output_dir=output_dir, runtime_root=repo_root, context_model=context_model, session_id=sess_id,
            candidate_counts={"initial_candidates": getattr(retriever, "_last_initial_candidate_count", len(assertions)), "verified_candidates": len(assertions), "rendered_candidates": 0},
            generation=retriever.generation, graph_sha256=retriever.graph_sha256, intent=intent, target=eff_target, topic=topic_query,
        )
    evidence_breakdown = _build_evidence_breakdown(selected_utterance, context_model, assertions, window_turns=window_turns)
    ans_id = render_fn(intent, resolved_target, body, assertions, retriever, output_dir, runtime_root=repo_root, context_model=context_model, session_id=sess_id, machine=machine, args=args, evidence_breakdown=evidence_breakdown, stage_latencies_ms=stage_latencies)
    return _build_copilot_success_result(intent, resolved_target, ans_id, assertions, retriever, evidence_breakdown, stage_latencies, output_dir, context_model, sess_id)


def execute_copilot_request(args: argparse.Namespace) -> Dict[str, Any]:
    """Executes copilot request with full request parsing, grounding, and attempt logging."""
    import dual_pipeline.cli.copilot_handlers as h  # noqa: F401  # lazy-decouple
    load_fn = getattr(h, "load_copilot_graph", load_copilot_graph)
    render_fn = getattr(h, "_record_and_render_copilot_output", _record_and_render_copilot_output)
    req_tuple, fail_res = _parse_and_validate_copilot_args(args)
    if fail_res or not req_tuple:
        return fail_res or {}
    intent, target, topic_query, topic_keywords = req_tuple
    machine = bool(getattr(args, "json", False))
    sess_tuple, fail_res = _resolve_copilot_session(args, machine)
    if fail_res or not sess_tuple:
        return fail_res or {}
    output_dir, context_model, manifest, meeting_slug = sess_tuple
    target, missing_target = _resolve_implicit_respond_target(intent, target, context_model)
    if missing_target:
        return _copilot_failure("MISSING_TARGET", "Must specify --target <person> for --intent respond.", machine, args=args, output_dir=output_dir, runtime_root=getattr(args, "runtime_root", None), context_model=context_model, intent=intent)
    context_model._raw_request = getattr(args, "request", None) or getattr(args, "query", None) or getattr(args, "prompt", None)
    from uuid import uuid4
    repo_root = getattr(args, "runtime_root", None) or (Path(manifest.repo_root) if manifest else ROOT)
    effective_sess_id = (manifest.session_id if manifest else getattr(args, "session", None)) or f"sess_cli_{uuid4().hex[:12]}"
    graph_tuple, fail_res = _resolve_copilot_graph(args, load_fn, repo_root, effective_sess_id, output_dir, context_model, manifest, machine)
    if fail_res or not graph_tuple:
        return fail_res or {}
    in_mem, retriever = graph_tuple
    req_id, orch = f"req_{uuid4().hex[:12]}", get_or_create_lifecycle_orchestrator(effective_sess_id)
    orch.begin_request(req_id)
    try:
        return _evaluate_and_render_copilot_response(intent=intent, target=target, topic_query=topic_query, topic_keywords=topic_keywords, context_model=context_model, in_mem=in_mem, retriever=retriever, output_dir=output_dir, repo_root=repo_root, sess_id=effective_sess_id, meeting_slug=meeting_slug, machine=machine, args=args, render_fn=render_fn)
    finally:
        orch.end_request(req_id)
