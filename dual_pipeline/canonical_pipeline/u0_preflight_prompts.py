"""Stage U0 Preflight Speaker Review Prompt Builder."""
from __future__ import annotations

import contextlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_DIALOGUE_LINE_RE = re.compile(
    r"^\s*(?:\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*)?"
    r"(?:\*{0,2}\[?([A-Za-z0-9_\-\s]+?)\]?\*{0,2})"
    r"(?:\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\])?\s*:\*{0,2}\s*(.+)$"
)


def build_preflight_speaker_prompts(
    raw_utterances: List[Dict[str, Any]],
    unresolved_indices: List[int],
    source_id: str,
) -> List[Dict[str, Any]]:
    """Build pre-rendered 5-turn context review prompts for unresolved speaker turns."""
    from dual_pipeline.speaker_attribution_core.review_context import (
        build_speaker_review_context,
    )
    from dual_pipeline.speaker_attribution_core.review_context_render import (
        format_speaker_review_context,
    )

    turns_for_context: List[Dict[str, Any]] = []
    for idx, utt in enumerate(raw_utterances):
        if not isinstance(utt, dict):
            continue
        ts = utt.get("timestamp")
        s_raw = utt.get("start") if utt.get("start") is not None else utt.get("start_time_sec")
        if not ts and s_raw is not None:
            try:
                s_sec = float(s_raw)
                ts = f"{int(s_sec // 60):02d}:{int(s_sec % 60):02d}"
            except (ValueError, TypeError):
                ts = str(s_raw)
        elif not ts:
            ts = "Unavailable"

        turns_for_context.append({
            "turn_id": str(utt.get("turn_id", utt.get("utterance_id", utt.get("id", idx)))),
            "source_position": idx,
            "speaker": str(utt.get("speaker") or utt.get("raw_speaker") or "Unknown"),
            "text": str(utt.get("text") or ""),
            "timestamp": ts,
            "source_id": source_id,
        })

    rendered: List[Dict[str, Any]] = []
    for u_idx in unresolved_indices:
        if u_idx < 0 or u_idx >= len(raw_utterances):
            continue
        target_id = str(raw_utterances[u_idx].get("turn_id", raw_utterances[u_idx].get("utterance_id", raw_utterances[u_idx].get("id", u_idx))))
        try:
            ctx = build_speaker_review_context(
                turns_for_context,
                target_id,
                source_id,
                expected_source_id=source_id,
            )
            rendered.append({
                "turn_index": u_idx,
                "target_turn_id": target_id,
                "speaker_cluster": str(raw_utterances[u_idx].get("speaker") or raw_utterances[u_idx].get("raw_speaker") or "Unknown"),
                "prompt": format_speaker_review_context(ctx),
            })
        except Exception as exc:
            logger.debug("Failed building review context for turn %s: %s", u_idx, exc)

    return rendered


def _extract_turns_from_metadata_or_dialogue(
    metadata: Dict[str, Any], raw_transcript: str
) -> List[Dict[str, Any]]:
    raw_utterances = metadata.get("structured_utterances")
    if not raw_utterances and metadata.get("structured_utterances_path"):
        u_p = Path(str(metadata["structured_utterances_path"]))
        if u_p.is_file():
            with contextlib.suppress(Exception):
                raw_utterances = json.loads(u_p.read_text(encoding="utf-8"))
    if isinstance(raw_utterances, list) and raw_utterances:
        return [dict(u) for u in raw_utterances if isinstance(u, dict)]

    turns: List[Dict[str, Any]] = []
    for line in (raw_transcript or "").splitlines():
        m = _DIALOGUE_LINE_RE.match(line.strip())
        if not m:
            continue
        ts1, spk, ts2, txt = m.groups()
        turns.append({
            "turn_id": str(len(turns)),
            "speaker": spk.strip("*[] "),
            "text": txt.strip(),
            "timestamp": ts1 or ts2 or f"00:{len(turns):02d}",
        })
    return turns


def _normalize_speaker_label(label: str) -> str:
    s = str(label or "").strip("*[] ").lower()
    m = re.match(r"^speaker_0*(\d+)$", s)
    if m:
        return f"speaker {int(m.group(1)) + 1}"
    return re.sub(r"\s+", " ", s)


def hydrate_generic_speaker_preflight_prompts(
    metadata: Dict[str, Any],
    raw_transcript: str,
    generic_labels: List[str],
) -> None:
    """Hydrates 5-turn speaker review prompts and scored roster candidate options for unmapped generic speaker clusters."""
    turns = _extract_turns_from_metadata_or_dialogue(metadata, raw_transcript)
    if not turns or not generic_labels:
        return

    spk_map = metadata.get("speaker_map") or {}
    mapped_norm = {_normalize_speaker_label(k) for k, v in spk_map.items() if v}
    unmapped_labels = [lbl for lbl in generic_labels if _normalize_speaker_label(lbl) not in mapped_norm]
    if not unmapped_labels:
        return

    # Build order-of-appearance alias map (e.g., first unique SPEAKER_XX in utterances -> 'speaker 1')
    appearance_alias: Dict[str, str] = {}
    for t in turns:
        raw_s = str(t.get("speaker") or t.get("raw_speaker") or "").strip()
        if raw_s and raw_s.lower() not in appearance_alias:
            appearance_alias[raw_s.lower()] = f"speaker {len(appearance_alias) + 1}"

    target_indices: List[int] = []
    index_to_label: Dict[int, str] = {}
    for lbl in unmapped_labels:
        norm_lbl = _normalize_speaker_label(lbl)
        matching_idxs: List[int] = []
        for idx, t in enumerate(turns):
            t_spk = str(t.get("speaker") or t.get("raw_speaker") or "").strip()
            t_low = t_spk.lower()
            is_match = (
                t_low == lbl.strip().lower()
                or (t_low in appearance_alias and appearance_alias[t_low] == norm_lbl)
                or (t_low not in appearance_alias and _normalize_speaker_label(t_spk) == norm_lbl)
            )
            if is_match and idx not in index_to_label:
                matching_idxs.append(idx)
        if matching_idxs:
            chosen_idx = next(
                (i for i in matching_idxs if len(str(turns[i].get("text") or "").split()) >= 8),
                matching_idxs[0],
            )
            target_indices.append(chosen_idx)
            index_to_label[chosen_idx] = lbl

    source_id = str(metadata.get("meeting_name") or metadata.get("title") or metadata.get("container_slug") or "meeting_source")
    prompts = build_preflight_speaker_prompts(turns, target_indices, source_id)

    speaker_options: List[Dict[str, Any]] = []
    with contextlib.suppress(Exception):
        from dual_pipeline.speaker_attribution_core.audio_uncertainty_detector import extract_roster_with_roles
        from dual_pipeline.speaker_attribution_core.candidate_scorer import score_candidates_for_speaker

        roster_dicts = extract_roster_with_roles(metadata)
        roster_tuples = [
            (str(r["name"]), str(r.get("org") or r.get("role") or ""))
            for r in roster_dicts
            if isinstance(r, dict) and r.get("name")
        ]
        if roster_tuples:
            for p_entry in prompts:
                lbl = index_to_label.get(p_entry["turn_index"], p_entry.get("speaker_cluster", "Speaker"))
                p_entry["speaker_cluster"] = lbl
                cands = score_candidates_for_speaker(
                    spk_id=lbl,
                    transcript=raw_transcript or "\n".join(f"{t.get('speaker')}: {t.get('text')}" for t in turns),
                    roster=roster_tuples,
                    metadata=metadata,
                    total_clusters=max(1, len(unmapped_labels)),
                )
                p_entry["candidates"] = [c.to_option_string() for c in cands]
                for c in cands[:4]:
                    speaker_options.append({
                        "label": f"Assign {lbl} -> {c.attendee_name}",
                        "description": c.to_option_string(),
                        "field_name": f"speaker_map:{lbl}",
                        "speaker_cluster": lbl,
                        "candidate_name": c.attendee_name,
                        "confidence": c.confidence,
                        "confidence_tier": c.confidence_tier,
                    })

    if prompts:
        existing = list(metadata.get("preflight_speaker_review_prompts") or [])
        seen_idx = {p.get("turn_index") for p in existing if isinstance(p, dict)}
        for p in prompts:
            if p.get("turn_index") not in seen_idx:
                existing.append(p)
        metadata["preflight_speaker_review_prompts"] = existing
    if speaker_options:
        metadata["preflight_speaker_options"] = speaker_options
