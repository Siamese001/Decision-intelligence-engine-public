"""Two-Stage Ambiguity Shield and LLM escalation execution for speaker disambiguation."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from .models import DisambiguationResult, RoomMicConfig, TurnAttribution

if TYPE_CHECKING:
    from .disambiguator import SpeakerDisambiguator

logger = logging.getLogger(__name__)


def _parse_llm_batch_response(
    raw_json: str,
    batch: List[Dict[str, Any]],
    room_config: RoomMicConfig,
    margin_threshold: float,
    model_name: str,
) -> List[TurnAttribution]:
    name_to_person = {att.name: att.person_id for att in room_config.in_room_attendees}
    try:
        data = json.loads(raw_json)
        arr = data.get("attributions", data.get("turns", []))
        if isinstance(arr, list):
            res: List[TurnAttribution] = []
            for item in arr:
                idx = item.get("turn_index")
                spk = item.get("assigned_speaker")
                conf = float(item.get("confidence", 0.0))
                sec_cand = item.get("second_candidate")
                sec_conf = float(item.get("second_confidence", 0.0)) if item.get("second_confidence") is not None else None
                margin = float(item.get("margin_delta", conf - (sec_conf or 0.0)))
                mode = item.get("attribution_mode", "DIRECT")
                rat = item.get("rationale", "")

                orig = next((t for t in batch if t["line_index"] == idx), None)
                if not orig or not spk:
                    continue

                res.append(TurnAttribution(
                    turn_index=idx,
                    timestamp=orig["timestamp"],
                    original_speaker=orig["speaker"],
                    assigned_speaker=spk,
                    person_id=name_to_person.get(spk, f"org:{spk.lower()}"),
                    confidence=conf,
                    second_candidate=sec_cand,
                    second_confidence=sec_conf,
                    margin_delta=margin,
                    attribution_mode=mode,
                    rationale=f"[{model_name}]: {rat}",
                    text_snippet=orig["text"][:100],
                ))
            return res
    except Exception as parse_err:
        logger.warning("Failed parsing LLM disambiguation JSON: %s", parse_err)
    return []


def _process_single_llm_batch(
    disambiguator: SpeakerDisambiguator,
    b_idx: int,
    batch: List[Dict[str, Any]],
    all_turns: List[Dict[str, Any]],
    system_prompt: str,
    total_batches: int,
    room_config: RoomMicConfig,
) -> List[TurnAttribution]:
    batch_line_indices = {t["line_index"] for t in batch}
    min_idx, max_idx = min(batch_line_indices), max(batch_line_indices)
    window_turns = [t for t in all_turns if (min_idx - 3) <= t["line_index"] <= (max_idx + 3)]

    user_turns_payload = [
        {
            "turn_index": t["line_index"],
            "speaker": t["speaker"],
            "timestamp": t["timestamp"],
            "text": t["text"],
            "is_room_mic_target": t["line_index"] in batch_line_indices,
        }
        for t in window_turns
    ]

    user_prompt = (
        "Disambiguate the room mic turns marked with 'is_room_mic_target: true' "
        "using surrounding conversation for context:\n\n"
        f"{json.dumps(user_turns_payload, separators=(',', ':'))}"
    )

    for attempt in range(3):
        try:
            response = disambiguator.client.chat.completions.create(
                model=disambiguator.model,
                reasoning_effort=disambiguator.reasoning_effort,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_completion_tokens=64000,
                response_format={"type": "json_object"},
                timeout=300,
                cache_namespace="speaker_disambiguation",
            )
            raw_json = response.choices[0].message.content or "{}"
            parsed = _parse_llm_batch_response(
                raw_json, batch, room_config, disambiguator.margin_threshold, disambiguator.model
            )
            if parsed:
                return parsed
        except Exception as batch_err:
            logger.warning("LLM batch %d attempt %d failed: %s", b_idx + 1, attempt + 1, batch_err)

    return disambiguator._disambiguate_offline(batch, room_config)


def disambiguate_llm_batches(
    disambiguator: SpeakerDisambiguator,
    all_turns: List[Dict[str, Any]],
    room_turns: List[Dict[str, Any]],
    room_config: RoomMicConfig,
    remote_attendees: List[Dict[str, str]],
    batch_size: int,
) -> List[TurnAttribution]:
    roster_desc = [
        {"name": a.name, "role": a.role, "company": a.company, "domain_topics": a.domain_topics}
        for a in room_config.in_room_attendees
    ]
    from dual_pipeline.prompts.registry import CANONICAL_PROMPT_REGISTRY
    prompt_template = CANONICAL_PROMPT_REGISTRY.get_prompt("SPEAKER_DISAMBIGUATION")
    system_prompt = prompt_template.format(
        mic_label=room_config.mic_label,
        in_room_attendees=json.dumps(roster_desc, separators=(',', ':')),
        remote_attendees=json.dumps(remote_attendees, separators=(',', ':')),
        margin_threshold=disambiguator.margin_threshold,
        display_label=room_config.display_label,
    )

    batches = [room_turns[i : i + batch_size] for i in range(0, len(room_turns), batch_size)]
    attributions: List[TurnAttribution] = []
    for b_idx, batch in enumerate(batches):
        attributions.extend(
            _process_single_llm_batch(
                disambiguator, b_idx, batch, all_turns, system_prompt, len(batches), room_config
            )
        )
    return attributions


def _resolve_escalator_attributions(
    escalator: SpeakerDisambiguator,
    parsed_turns: List[Dict[str, Any]],
    target_turns: List[Dict[str, Any]],
    room_config: RoomMicConfig,
    remote_attendees: Optional[List[Dict[str, str]]],
) -> List[TurnAttribution]:
    if escalator.mock or escalator.client is None:
        return escalator._disambiguate_offline(target_turns, room_config)
    try:
        return escalator._disambiguate_llm(
            parsed_turns,
            target_turns,
            room_config,
            remote_attendees or [],
            batch_size=min(10, max(1, len(target_turns))),
        )
    except Exception as esc_err:
        logger.warning("Two-Stage Shield escalation LLM call failed: %s", esc_err)
        return []


def _apply_shield_promotions(
    primary_turns: List[TurnAttribution],
    esc_by_idx: Dict[int, TurnAttribution],
    margin_threshold: float,
    esc_model: str,
) -> Tuple[List[TurnAttribution], int]:
    new_turns: List[TurnAttribution] = []
    resolved_count = 0
    for turn in primary_turns:
        if turn.turn_index in esc_by_idx:
            esc_turn = esc_by_idx[turn.turn_index]
            if esc_turn.margin_delta >= margin_threshold:
                resolved_count += 1
                new_turns.append(TurnAttribution(
                    turn_index=turn.turn_index,
                    timestamp=turn.timestamp,
                    original_speaker=turn.original_speaker,
                    assigned_speaker=esc_turn.assigned_speaker,
                    person_id=esc_turn.person_id,
                    confidence=esc_turn.confidence,
                    second_candidate=esc_turn.second_candidate,
                    second_confidence=esc_turn.second_confidence,
                    margin_delta=esc_turn.margin_delta,
                    attribution_mode=esc_turn.attribution_mode,
                    rationale=f"Two-Stage Shield ({esc_model} resolved margin={esc_turn.margin_delta:.2f}): {esc_turn.rationale}",
                    text_snippet=turn.text_snippet,
                ))
                continue
        new_turns.append(turn)
    return new_turns, resolved_count


def execute_two_stage_shield(
    primary_disambiguator: SpeakerDisambiguator,
    transcript: str,
    room_config: RoomMicConfig,
    remote_attendees: Optional[List[Dict[str, str]]] = None,
    secondary_sol_disambiguator: Optional[SpeakerDisambiguator] = None,
    secondary_escalator: Optional[SpeakerDisambiguator] = None,
    batch_size: Optional[int] = None,
) -> DisambiguationResult:
    primary_result = primary_disambiguator.disambiguate(
        transcript=transcript,
        room_config=room_config,
        remote_attendees=remote_attendees,
        batch_size=batch_size,
    )
    escalator = secondary_escalator or secondary_sol_disambiguator
    ambiguous_turns = [
        t for t in primary_result.turns
        if (t.attribution_mode == "SHARED_ROOM_MIC" or t.second_candidate)
        and t.margin_delta < primary_disambiguator.margin_threshold
    ]

    esc_by_idx: Dict[int, TurnAttribution] = {}
    if escalator is not None and ambiguous_turns:
        parsed_turns = primary_disambiguator.parse_turns(transcript)
        ambiguous_indices = {t.turn_index for t in ambiguous_turns}
        target_room_turns = [t for t in parsed_turns if t["line_index"] in ambiguous_indices]
        esc_attributions = _resolve_escalator_attributions(
            escalator, parsed_turns, target_room_turns, room_config, remote_attendees
        )
        esc_by_idx = {a.turn_index: a for a in esc_attributions}

    esc_model = getattr(escalator, "model", "Escalator") if escalator else "Escalator"
    new_turns, shield_resolved_count = _apply_shield_promotions(
        primary_result.turns, esc_by_idx, primary_disambiguator.margin_threshold, esc_model
    )

    if shield_resolved_count > 0:
        parsed_turns = primary_disambiguator.parse_turns(transcript)
        resolved_tx, spk_dist, directly_attr, shared_pres = (
            primary_disambiguator._reconstruct_transcript(transcript, parsed_turns, new_turns, room_config)
        )
        manifest = primary_disambiguator._build_manifest(
            room_config, remote_attendees or [], new_turns, spk_dist
        )
    else:
        resolved_tx = primary_result.resolved_transcript
        spk_dist = primary_result.speaker_distribution
        directly_attr = primary_result.directly_attributed_turns
        shared_pres = primary_result.shared_room_preserved_turns
        manifest = dict(primary_result.attribution_manifest)

    if "telemetry" not in manifest or not isinstance(manifest["telemetry"], dict):
        manifest["telemetry"] = {}
    manifest["telemetry"]["two_stage_shield"] = {
        "ambiguous_turns_evaluated": len(ambiguous_turns),
        "shield_resolved_turns": shield_resolved_count,
        "hitl_prompts_suppressed": shield_resolved_count,
    }

    return DisambiguationResult(
        resolved_transcript=resolved_tx,
        attribution_manifest=manifest,
        turns=new_turns,
        total_room_turns=primary_result.total_room_turns,
        directly_attributed_turns=directly_attr,
        shared_room_preserved_turns=shared_pres,
        speaker_distribution=spk_dist,
    )
