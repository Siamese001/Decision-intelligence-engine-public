"""
dual_pipeline/live_meeting_recorder_core/copilot_offline.py

Offline session, intent, context, and response preparation helpers for copilot.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
)


def resolve_offline_copilot_session(
    args: argparse.Namespace, runtime_root: Optional[Path]
) -> Optional[Tuple[Path, ConversationContextModel, Any, str]]:
    req_proj = getattr(args, "project", None) or getattr(args, "engagement", None)
    intent_val = (getattr(args, "intent", None) or "").lower()
    is_offline_intent = intent_val in {"gaps", "graph-context", "connect", "review"}
    is_explicit_offline = bool(req_proj and not getattr(args, "active", False))
    has_offline_request = bool(
        (getattr(args, "request", None) or getattr(args, "topic", None))
        and not getattr(args, "active", False)
    )

    if not (
        is_explicit_offline
        or is_offline_intent
        or has_offline_request
        or (req_proj and is_offline_intent)
    ):
        return None

    from dual_pipeline.core import ROOT
    from dual_pipeline.live_meeting_recorder_core.session_identity import normalize_engagement_id

    repo_root = Path(runtime_root or getattr(args, "runtime_root", None) or ROOT).resolve()
    proj_str = str(req_proj) if req_proj else "offline"
    normalized_eng = normalize_engagement_id(req_proj) if req_proj else None
    offline_dir = repo_root / ".local" / "copilot" / proj_str.replace(":", "_").replace("-", "_")
    offline_dir.mkdir(parents=True, exist_ok=True)
    ctx_model = ConversationContextModel(project_scope=normalized_eng, attendees=[])
    from dual_pipeline.cli.copilot_session import _read_turns_from_jsonl

    existing_turns = _read_turns_from_jsonl(offline_dir)
    for t in existing_turns:
        ctx_model.add_turn(
            speaker=t["speaker"],
            text=t["text"],
            timestamp=t["timestamp"],
            turn_sec=t.get("turn_sec"),
        )
    ctx_model._offline_mode = not bool(existing_turns)
    if getattr(args, "request", None):
        ctx_model._offline_request = getattr(args, "request", None)
    return (offline_dir, ctx_model, None, proj_str)


def is_offline_graph_intent(
    context_model: ConversationContextModel, args: argparse.Namespace
) -> bool:
    if getattr(context_model, "_offline_mode", False):
        return True
    intent = getattr(args, "intent", None) or ""
    if intent.lower() in {"gaps", "graph-context", "connect", "review"}:
        return True
    is_active = bool(getattr(args, "active", False))
    has_proj = bool(getattr(args, "project", None))
    has_req = bool(getattr(args, "request", None) or getattr(args, "topic", None))
    return not is_active and (has_proj or has_req)


def is_tolerated_speaker_error(
    err_tuple: Optional[Tuple[str, str]],
    intent: str,
    topic_keywords: List[str],
    topic_query: Optional[str],
    context_model: ConversationContextModel,
) -> bool:
    if not err_tuple:
        return True
    tolerated_codes = {
        "ZERO_RELEVANT_UTTERANCES",
        "NO_RELEVANT_UTTERANCE",
        "SPEAKER_HAS_NO_TURNS",
        "SPEAKER_NOT_IN_ROSTER",
    }
    if intent != "respond" and bool(context_model.focus_turns() or context_model.retrieval_seeds()):
        return err_tuple[0] in tolerated_codes
    has_query_scope = bool(
        topic_keywords or topic_query or getattr(context_model, "_offline_mode", False)
    )
    if not has_query_scope:
        return False
    if intent != "respond":
        return True
    if getattr(context_model, "_offline_mode", False):
        return err_tuple[0] in {"ZERO_RELEVANT_UTTERANCES", "NO_RELEVANT_UTTERANCE", "SPEAKER_HAS_NO_TURNS"}
    return err_tuple[0] in {"ZERO_RELEVANT_UTTERANCES", "NO_RELEVANT_UTTERANCE"}


def resolve_evaluation_dialogue_context(
    context_model: ConversationContextModel,
    meeting_slug: str,
    resolved_target: Optional[str],
    topic_query: Optional[str],
    topic_keywords: List[str],
) -> Any:
    if context_model.retrieval_seeds() or context_model.focus_turns():
        return context_model
    from dual_pipeline.live_meeting_recorder_core.copilot_intents import BlendedDialogueContext

    return BlendedDialogueContext(
        dialogue=meeting_slug,
        target=resolved_target,
        topic_query=topic_query,
        topic_keywords=topic_keywords,
    )


def _sanitize_offline_fallback(body: str) -> str:
    if not body:
        return "No conflicting or changing commitments found in GraphDB."
    lines: List[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or re.match(r"^(?:Advisory Coaching|Graph Context|Open Gaps & Risks)\b", line):
            continue
        cleaned = re.sub(r"^-\s*(?:\[[A-Z0-9_]+\]|[A-Z0-9_]+)\s*:\s*", "", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines[:3]) if lines else body


def prepare_copilot_body_for_delivery(
    body: str,
    intent: str,
    target: Optional[str],
    resolved_target: Optional[str],
    assertions: List[Dict[str, Any]],
    args: argparse.Namespace,
    retriever: Any,
    selected_utterance: Optional[Dict[str, Any]],
    is_offline: bool,
    render_and_validate_body_fn: Any,
) -> Tuple[Optional[str], Optional[str]]:
    if is_offline:
        if assertions:
            rendered_body, fail_code = render_and_validate_body_fn(
                intent,
                target or resolved_target,
                assertions,
                args,
                retriever,
                selected_utterance=selected_utterance,
            )
            if rendered_body:
                return rendered_body, None
            return None, (fail_code or "SPEAK_GATE_REJECTED")
        return _sanitize_offline_fallback(body), None
    rendered_body, fail_code = render_and_validate_body_fn(
        intent,
        target or resolved_target,
        assertions,
        args,
        retriever,
        selected_utterance=selected_utterance,
    )
    if not rendered_body:
        return None, fail_code
    return rendered_body, None


def matches_attendee_target(norm_target: str, retriever: Any) -> bool:
    att_nodes = getattr(retriever, "_attendee_node_ids", set())
    if f"attendee:{norm_target}" in att_nodes:
        return True
    for att in getattr(retriever, "_attendees", []):
        if isinstance(att, dict) and norm_target in str(att.get("name", "")).lower():
            return True
    return False


def matches_graph_target(tokens: Set[str], retriever: Any) -> bool:
    inv = getattr(retriever, "_inverted_index", {})
    if any(t in inv for t in tokens):
        return True
    nodes = getattr(getattr(retriever, "_topology", None), "nodes", {})
    if not isinstance(nodes, dict):
        return False
    for n in nodes.values():
        if isinstance(n, dict):
            cn = str(n.get("canonical_name") or "").lower()
            dn = str(n.get("display_name") or "").lower()
            if any(t in cn or t in dn for t in tokens):
                return True
    return False


def is_entity_present(target: Optional[str], retriever: Any) -> bool:
    if not target or not target.strip():
        return True
    if not retriever:
        return False
    target_clean = target.strip()
    norm_target = re.sub(r"[^a-z0-9_]+", "_", target_clean.lower()).strip("_")
    if matches_attendee_target(norm_target, retriever):
        return True
    tokens = set(re.findall(r"[a-z0-9]+", target_clean.lower()))
    return matches_graph_target(tokens, retriever)


def build_match_tokens(topic_keywords: List[str], topic_query: Optional[str]) -> Set[str]:
    tokens = {k.lower() for k in topic_keywords if len(k) > 2}
    if topic_query:
        tokens.update(t for t in re.findall(r"[a-z0-9]+", topic_query.lower()) if len(t) > 2)
    return tokens


def has_scope_filtered_assertions(
    retriever: Any, topic_keywords: List[str], topic_query: Optional[str]
) -> bool:
    if not retriever or not getattr(retriever, "engagement_id", None):
        return False
    if getattr(retriever, "_scope_filtered_count", 0) <= 0:
        return False
    assertions_dict = getattr(getattr(retriever, "_topology", None), "assertions", {})
    if not isinstance(assertions_dict, dict) or not assertions_dict:
        return False
    tokens = build_match_tokens(topic_keywords, topic_query)
    if not tokens:
        return True
    for a in assertions_dict.values():
        if isinstance(a, dict):
            claim = str(a.get("claim_text") or a.get("object_literal") or "").lower()
            if any(t in claim for t in tokens):
                return True
    return False


def diagnose_unresolved_evidence(
    retriever: Any,
    topic_keywords: List[str],
    topic_query: Optional[str],
    target: Optional[str] = None,
    has_live_context: bool = False,
) -> Tuple[str, str]:
    grounding_result = getattr(retriever, "_last_grounding_result", None)
    init_count = getattr(retriever, "_last_initial_candidate_count", 0)
    if grounding_result and getattr(grounding_result, "is_budget_breached", False):
        return "DEADLINE_EXHAUSTED", "Grounding verification exceeded the latency deadline."
    if init_count > 0:
        return (
            "REJECTED_EVIDENCE",
            f"Retrieved {init_count} candidate assertion(s) but all failed grounding or quarantine checks.",
        )
    clean_query = re.sub(r"[^a-zA-Z0-9]+", "", topic_query or "")
    if not has_live_context and not topic_keywords and not clean_query and (not target or not target.strip()):
        return "PARSER_NO_TOPIC", "No recognizable topic or attendee extracted from prompt."
    if target and not is_entity_present(target, retriever):
        return "ENTITY_NOT_FOUND", f"Target '{target}' not found in graph or active roster."
    if has_scope_filtered_assertions(retriever, topic_keywords, topic_query):
        return "SCOPE_FILTERED", "Assertions exist but were filtered by active project scope."
    return "ZERO_MATCHING_ASSERTIONS", "No relevant graph assertions match the current discussion."


def fallback_live_window(
    context_model: Any, topic_keywords: List[str], select_topic_utterance_fn: Any
) -> Tuple[Optional[Any], List[Dict[str, Any]]]:
    all_turns = [
        {"seq": t.seq, "speaker": t.speaker, "text": t.text, "timestamp": t.timestamp, "turn_sec": t.turn_sec}
        for t in getattr(context_model, "_turns", []) if not getattr(t, "is_quarantined", False)
    ]
    if not all_turns:
        return None, []
    fb_utt, fb_win = select_topic_utterance_fn(all_turns, topic_keywords)
    return fb_utt, (fb_win or context_model.focus_turns())


def build_copilot_success_result(
    intent: str,
    resolved_target: Optional[str],
    ans_id: Optional[str],
    assertions: List[Dict[str, Any]],
    retriever: Any,
    evidence_breakdown: Dict[str, Any],
    stage_latencies: Dict[str, float],
    output_dir: Path,
    context_model: Any,
    sess_id: Optional[str],
) -> Dict[str, Any]:
    res: Dict[str, Any] = {
        "status": "PASS", "command": "copilot", "intent": intent, "target": resolved_target,
        "answer_id": ans_id, "cited_assertion_ids": [a.get("assertion_id") for a in assertions if a.get("assertion_id")],
        "graph_sha256": retriever.graph_sha256, "generation": retriever.generation,
        "evidence_breakdown": evidence_breakdown, "stage_latencies_ms": stage_latencies,
        "answers_path": str(output_dir / "copilot_answers.jsonl"), "attempts_path": str(output_dir / "copilot_attempts.jsonl"),
    }
    if getattr(context_model, "_offline_mode", False):
        res["context_mode"] = "GRAPH_ONLY"
    if sess_id:
        from .copilot_lifecycle import get_or_create_lifecycle_orchestrator
        orch = get_or_create_lifecycle_orchestrator(sess_id)
        if orch.degraded_state.is_degraded:
            res["degraded_state"] = orch.degraded_state.to_dict()
            res["fallback_active"] = True
    return res


def resolve_implicit_respond_target(
    intent: str, target: Optional[str], context_model: ConversationContextModel
) -> Tuple[Optional[str], bool]:
    """Resolves the active non-host speaker when `respond` is invoked on a live session without `--target`."""
    if intent != "respond" or (target and target.strip()):
        return target, False
    if getattr(context_model, "_offline_mode", False):
        return None, True
    from .copilot_context import _pick_latest_substantive_turn
    from .live_buffer_reader import coalesce_contiguous_turns

    raw_turns = [
        {"seq": t.seq, "speaker": t.speaker, "text": t.text, "timestamp": t.timestamp, "turn_sec": t.turn_sec}
        for t in getattr(context_model, "_turns", [])
        if not getattr(t, "is_quarantined", False)
    ]
    coalesced = coalesce_contiguous_turns(raw_turns)
    non_host = [
        (idx, t)
        for idx, t in enumerate(coalesced)
        if str(t.get("speaker") or "").strip().lower() not in {"amit", "amit ayer", "host", "voice host", "system", "unknown", "none", ""}
    ]
    if not non_host:
        return None, True
    _, best_turn = _pick_latest_substantive_turn(non_host)
    spk = str(best_turn.get("speaker") or "").strip()
    return (spk, False) if spk else (None, True)


def resolve_ledger_seeds(
    context_model: Any, args: Optional[argparse.Namespace], target: Optional[str]
) -> Tuple[List[Dict[str, Any]], List[str], str]:
    c_snap = context_model.focus_turns() if context_model else []
    c_seeds = list(context_model.retrieval_seeds()) if context_model else []
    if not c_seeds:
        raw_q = f"{getattr(args, 'topic', '') or ''} {getattr(args, 'request', '') or ''} {target or ''}".strip()
        c_seeds = [w.lower() for w in re.findall(r"\b[A-Za-z0-9_-]{3,}\b", raw_q)][:8]
    c_dig = context_model.compute_hash() if context_model and hasattr(context_model, "compute_hash") else ""
    return c_snap, c_seeds, c_dig


