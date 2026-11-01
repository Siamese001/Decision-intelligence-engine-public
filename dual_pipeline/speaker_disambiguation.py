"""
SpeakerDisambiguator — High-reasoning LLM speaker attribution for shared conference room microphones.

Facade preserving 100% backward compatibility with dual_pipeline.speaker_disambiguation.
Underlying implementations are modularized under dual_pipeline.speaker_disambiguation_core.
"""

from __future__ import annotations

import logging
import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.credentials import resolve_openai_credentials
from dual_pipeline.hitl_core.envelope import create_hitl_envelope
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.model_registry import ModelTier, get_binding
from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold
from dual_pipeline.speaker_attribution_core.review_context import (
    SpeakerReviewContextError,
    build_speaker_review_context,
)
from dual_pipeline.speaker_attribution_core.review_context_render import (
    format_speaker_review_context,
    parse_speaker_review_turns,
)
from dual_pipeline.speaker_disambiguation_core import (
    AttendeeContext,
    DisambiguationResult,
    RoomMicConfig,
    SpeakerDisambiguator,
    TurnAttribution,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AttendeeContext",
    "RoomMicConfig",
    "TurnAttribution",
    "DisambiguationResult",
    "SpeakerDisambiguator",
    "resolve_shared_room_microphones",
]


def _build_attendee_dict(name: str, org: str, role: str, topics: list[str]) -> dict[str, Any]:
    slug = name.lower().replace(" ", "-")
    return {
        "name": name,
        "person_id": f"person:{slug}",
        "role": role,
        "company": org,
        "domain_topics": topics,
    }


def _parse_single_attendee(a: Any, default_org: str = "") -> Optional[dict[str, Any]]:
    if not isinstance(a, dict):
        if not str(a).strip():
            return None
        return _build_attendee_dict(str(a).strip(), default_org, "Participant", [])
    name = a.get("name", "").strip()
    if not name:
        return None
    org = a.get("org") or a.get("company") or default_org
    role = a.get("role", "Participant")
    topics = list(a.get("domain_topics", []))
    return _build_attendee_dict(name, org, role, topics)


def _flatten_list(attendees_data: list) -> list[dict[str, Any]]:
    result = []
    for item in attendees_data:
        parsed = _parse_single_attendee(item)
        if parsed:
            result.append(parsed)
    return result


def _flatten_dict(attendees_data: dict) -> list[dict[str, Any]]:
    result = []
    for org, members in attendees_data.items():
        if isinstance(members, list):
            for item in members:
                parsed = _parse_single_attendee(item, org)
                if parsed:
                    result.append(parsed)
    return result


def _flatten_attendees(attendees_data: Any) -> list[dict[str, Any]]:
    if isinstance(attendees_data, list):
        return _flatten_list(attendees_data)
    if isinstance(attendees_data, dict):
        return _flatten_dict(attendees_data)
    return []


def _auto_synthesize_room_mics(attendees_data: Any) -> List[Dict[str, Any]]:
    """Synthesize room microphone config from attendee roster if generic speakers detected."""
    in_room_attendees = _flatten_attendees(attendees_data)
    if not in_room_attendees:
        return []
    return [{
        "mic_label": "generic-speaker",
        "display_label": "NEA/PartnerAdvisory (room)",
        "in_room_attendees": in_room_attendees,
    }]


def _build_turn_options(turn: Any, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Builds interactive options list for an ambiguous speaker turn."""
    options = [{
        "key": turn.assigned_speaker,
        "label": f"Attribute [{turn.timestamp}] to {turn.assigned_speaker}: \"{turn.text_snippet[:40]}\"",
        "confidence": turn.confidence,
    }]
    if turn.second_candidate:
        options.append({
            "key": turn.second_candidate,
            "label": f"Attribute [{turn.timestamp}] to {turn.second_candidate}",
            "confidence": turn.second_confidence,
        })
    existing_keys = {opt["key"] for opt in options}
    for mic in metadata.get("shared_room_mics", []):
        for att in mic.get("in_room_attendees", []):
            att_name = att.get("name")
            if att_name and att_name not in existing_keys:
                options.append({
                    "key": att_name,
                    "label": f"Attribute [{turn.timestamp}] to {att_name}",
                    "confidence": 0.50,
                })
                existing_keys.add(att_name)
    return options


def _prompt_speaker_resolution(
    turn: Any,
    options: List[Dict[str, Any]],
    store: HITLDecisionStore,
    interactive_resolver: Optional[Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Dispatches HITL question envelope and awaits operator resolution."""
    context_turns = (metadata or {}).get("_speaker_review_turns")
    source_id = (metadata or {}).get("_speaker_review_source_id")
    if not context_turns or not source_id:
        from dual_pipeline.exceptions import HITLRequiredError
        raise HITLRequiredError("Speaker assignment is blocked because its source transcript context is unavailable.")
    try:
        context = build_speaker_review_context(
            context_turns,
            str(turn.turn_index),
            source_id,
            expected_source_id=(metadata or {}).get("_speaker_review_expected_source_id"),
            expected_source_digest=(metadata or {}).get("_speaker_review_expected_digest"),
        )
    except SpeakerReviewContextError as exc:
        from dual_pipeline.exceptions import HITLRequiredError
        raise HITLRequiredError(f"Speaker assignment is blocked: {exc}") from exc
    envelope = create_hitl_envelope(
        category="speaker_diarization_attribution",
        domain="room_mic_disambiguation",
        question=format_speaker_review_context(context),
        options=options,
        store=store,
        threshold=get_hitl_ambiguity_margin_threshold(),
    )
    speaker_resolutions = (metadata or {}).get("speaker_resolutions", {})
    pre_resolved = (
        speaker_resolutions.get(str(turn.turn_index))
        or speaker_resolutions.get(turn.timestamp)
        or speaker_resolutions.get(turn.timestamp.strip("*[] "))
    )
    logger.info(
        "Evaluating ambiguous turn [%s] (index %s): assigned=%s (%.2f) vs second=%s (%.2f), margin=%.2f, pre_resolved=%s",
        turn.timestamp, turn.turn_index, turn.assigned_speaker, turn.confidence,
        turn.second_candidate, turn.second_confidence, turn.margin_delta, pre_resolved,
    )
    if pre_resolved:
        selected_speaker = pre_resolved
    elif interactive_resolver is not None:
        try:
            selected_speaker = str(interactive_resolver(envelope))
        except Exception as e:
            logger.error("Interactive speaker resolver failed: %s", e)
            from dual_pipeline.exceptions import HITLRequiredError
            raise HITLRequiredError(f"Fail-Fast: Interactive speaker resolver failed: {e}") from e
    else:
        logger.error("Speaker attribution ambiguity detected, but no HITL interactive_resolver provided.")
        from dual_pipeline.exceptions import HITLRequiredError
        raise HITLRequiredError(
            f"Fail-Fast: Speaker attribution ambiguity at [{turn.timestamp}] requires HITL interactive_resolver."
        )

    receipt = envelope.resolve_and_persist(
        selection=selected_speaker,
        operator_id="human_operator",
        store=store,
    )
    final_speaker = envelope.selected_option.get("key", selected_speaker) if envelope.selected_option else selected_speaker
    return final_speaker, receipt


def _replace_turn_speaker(resolved_text: str, timestamp: str, old_spk: str, new_spk: str) -> str:
    """Surgically replaces speaker label at a specific timestamp without global alteration."""
    ts_esc = re.escape(timestamp)
    old_spk_esc = re.escape(old_spk)
    pat = re.compile(ts_esc + r"\s*(\*\*)?\[?" + old_spk_esc + r"\]?(\*\*)?(:\*\*|\*\*:\s*|:\s*)")
    if pat.search(resolved_text):
        return pat.sub(f"{timestamp} **{new_spk}:** ", resolved_text, count=1)

    ts_pos = resolved_text.find(timestamp)
    if ts_pos == -1:
        return resolved_text

    target = f"**{old_spk}:**"
    tgt_pos = resolved_text.find(target, ts_pos)
    if tgt_pos != -1 and tgt_pos - ts_pos < 100:
        return resolved_text[:tgt_pos] + f"**{new_spk}:**" + resolved_text[tgt_pos + len(target):]

    target_unbold = f"{old_spk}:"
    tgt_pos_ub = resolved_text.find(target_unbold, ts_pos)
    if tgt_pos_ub != -1 and tgt_pos_ub - ts_pos < 100:
        return resolved_text[:tgt_pos_ub] + f"**{new_spk}:** " + resolved_text[tgt_pos_ub + len(target_unbold):]
    return resolved_text


def _resolve_ambiguous_turns(
    dis_result: DisambiguationResult,
    resolved_text: str,
    metadata: Dict[str, Any],
    store: HITLDecisionStore,
    interactive_resolver: Optional[Any],
) -> str:
    """Evaluate candidate confidence margin for room mic turns (Rule: Delta < 15% requires HITL)."""
    hitl_resolutions = metadata.setdefault("hitl_resolutions", {})
    threshold = get_hitl_ambiguity_margin_threshold()

    for turn in dis_result.turns:
        is_ambiguous = (
            turn.attribution_mode == "SHARED_ROOM_MIC"
            or (turn.second_candidate is not None and turn.margin_delta < threshold)
        )
        if not is_ambiguous:
            continue

        options = _build_turn_options(turn, metadata)
        selected_speaker, receipt = _prompt_speaker_resolution(turn, options, store, interactive_resolver, metadata)

        if selected_speaker != turn.assigned_speaker:
            resolved_text = _replace_turn_speaker(resolved_text, turn.timestamp, turn.assigned_speaker, selected_speaker)
            turn.assigned_speaker = selected_speaker

        hitl_key = f"spk_turn_{turn.turn_index}"
        basis_text = f"Operator Confirmed (HITL Speaker Disambiguation, receipt {receipt['receipt_digest'][:8]})"
        hitl_resolutions[hitl_key] = {
            "ambiguity": f"Shared room mic ambiguity at [{turn.timestamp}]: {turn.assigned_speaker} vs {turn.second_candidate}",
            "locator": f"[{turn.timestamp}]",
            "statement_id": f"stmt-spk-{turn.turn_index:02d}",
            "resolution": f"Attributed to {turn.assigned_speaker} based on conversational context.",
            "basis": basis_text,
        }

    return resolved_text


def _extract_source_id(resolved_text: str, metadata: Dict[str, Any]) -> str:
    context_source_digest = hashlib.sha256(resolved_text.encode("utf-8")).hexdigest()
    return str(
        metadata.get("source_id") or metadata.get("meeting_id") or metadata.get("meeting_name")
        or metadata.get("title") or f"transcript:{context_source_digest}"
    )


def _setup_disambiguators(metadata: Dict[str, Any]) -> Tuple[SpeakerDisambiguator, SpeakerDisambiguator, bool]:
    sol_binding = get_binding(ModelTier.QUERY_REASONING)
    frontier_binding = get_binding(ModelTier.FRONTIER_HIGH_REASONING)
    try:
        creds = resolve_openai_credentials()
        has_api_key = bool(creds.api_key)
    except Exception:
        has_api_key = False
    is_mock = metadata.get("mock_disambiguation", False) or metadata.get("provider") == "offline"
    if not is_mock and not has_api_key:
        raise RuntimeError(
            "Fail-Fast: OpenAI credentials not found for room mic speaker disambiguation. "
            "Silent offline mock fallback is prohibited. Set mock_disambiguation=True for test environments."
        )
    primary_model = metadata.get("disambiguation_model") or metadata.get("model") or sol_binding.model_id
    primary_effort = metadata.get("disambiguation_effort") or metadata.get("reasoning_effort") or sol_binding.reasoning_effort or "high"
    disambiguator = SpeakerDisambiguator(
        model=primary_model,
        reasoning_effort=primary_effort,
        mock=is_mock,
    )
    secondary_astra = SpeakerDisambiguator(
        model=frontier_binding.model_id,
        reasoning_effort=frontier_binding.reasoning_effort or "max",
        mock=is_mock,
    )
    return disambiguator, secondary_astra, is_mock


def resolve_shared_room_microphones(
    resolved_text: str,
    metadata: Dict[str, Any],
    speaker_map: Dict[str, str],
) -> Tuple[str, Dict[str, Any]]:
    """Resolves shared conference room microphones into individual attendee turns."""
    shared_room_mics = metadata.get("shared_room_mics", [])
    if not shared_room_mics:
        generic_pattern = re.compile(r"\bSpeaker\s+([A-Z]|\d+)\b", re.IGNORECASE)
        has_generic = bool(generic_pattern.search(resolved_text))
        if has_generic:
            logger.warning("Generic speaker labels detected without explicit shared_room_mics mapping. Halting for HITL.")
            from dual_pipeline.exceptions import HITLRequiredError
            raise HITLRequiredError("Generic speaker labels detected. Cannot autonomously attribute speakers. HITL required.")
        return resolved_text, metadata

    for room_dict in shared_room_mics:
        attendees = [
            AttendeeContext(
                name=a["name"],
                person_id=a.get("person_id") or f"person:{a['name'].lower().replace(' ', '-')}",
                role=a.get("role", "Participant"),
                company=a.get("company", ""),
                domain_topics=a.get("domain_topics", []),
            )
            for a in room_dict.get("in_room_attendees", [])
        ]
        cfg = RoomMicConfig(
            mic_label=room_dict.get("mic_label", "shared-room-mic"),
            display_label=room_dict.get("display_label", "NEA/APEX (room)"),
            in_room_attendees=attendees,
        )
        
        disambiguator, secondary_astra, _ = _setup_disambiguators(metadata)

        remote_attendees = [
            {"name": v, "person_id": f"person:{v.lower().replace(' ', '-')}"}
            for v in speaker_map.values()
        ]
        
        source_id = _extract_source_id(resolved_text, metadata)
        metadata["_speaker_review_turns"] = parse_speaker_review_turns(resolved_text, source_id)
        metadata["_speaker_review_source_id"] = source_id
        
        dis_result = disambiguator.disambiguate_with_two_stage_shield(
            resolved_text,
            cfg,
            remote_attendees=remote_attendees,
            secondary_escalator=secondary_astra,
            batch_size=metadata.get("disambiguation_batch_size"),
        )
        resolved_text = dis_result.resolved_transcript
        metadata["_attribution_manifest"] = dis_result.attribution_manifest

        store = HITLDecisionStore()
        interactive_resolver = metadata.get("hitl_speaker_resolver")
        resolved_text = _resolve_ambiguous_turns(
            dis_result, resolved_text, metadata, store, interactive_resolver
        )
        
        for key in ["_speaker_review_turns", "_speaker_review_source_id", "_speaker_review_expected_source_id", "_speaker_review_expected_digest"]:
            metadata.pop(key, None)

    return resolved_text, metadata
