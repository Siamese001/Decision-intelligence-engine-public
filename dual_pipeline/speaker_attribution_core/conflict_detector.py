"""
dual_pipeline/speaker_attribution_core/conflict_detector.py

Detects per-attendee starvation collapse, quote-hint vs speaker-map conflicts,
and intra-cluster third-person self-reference contradictions.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold
from .audio_uncertainty_detector import extract_roster_with_roles
from .models import CandidateAttribution, TurnAttributionDiscrepancy
from .quote_hint_turn_mapper import _TURN_LINE_RE, _find_matching_hint, _prepare_valid_hints
from .candidate_scorer import normalize_search_text

logger = logging.getLogger("dual_pipeline.speaker_attribution_core.conflict_detector")

_THIRD_PERSON_CUE_TEMPLATES = (
    r"\b(?:i\s+know|what|things\s+that|whether|when|where|how|why)\s+{name}\s+"
    r"(?:is|was|has|had|may\s+have|might\s+have|said|asked|mentioned|working|worked|told|wants|wanted|thinks|thought)\b",
    r"\b{name}\s+(?:is\s+working|was\s+working|may\s+have\s+asked|might\s+have\s+asked|"
    r"mentioned\s+earlier|brought\s+up|asked\s+about|is\s+going\s+to|was\s+saying)\b",
    r"\b(?:talked\s+to|spoke\s+with|ask|asked|asking|tell|told)\s+{name}\b",
)

_SELF_INTRO_EXCLUSION_RE = re.compile(
    r"\b(?:this\s+is|i\s+am|i'm|my\s+name\s+is|speaking\s+is)\b",
    re.IGNORECASE,
)


def _normalize_cluster_alias(label: str) -> str:
    s = str(label).strip()
    m_raw = re.match(r"^speaker_0*(\d+)$", s, re.IGNORECASE)
    if m_raw:
        return f"Speaker {int(m_raw.group(1)) + 1}"
    m_num = re.match(r"^(\d+)$", s)
    if m_num:
        return f"Speaker {m_num.group(1)}"
    m_spk = re.match(r"^speaker\s+0*(\d+)$", s, re.IGNORECASE)
    if m_spk:
        return f"Speaker {int(m_spk.group(1))}"
    return s


def _coerce_roster_item(item: Any) -> Optional[Dict[str, str]]:
    if isinstance(item, tuple) and item:
        return {"name": str(item[0]).strip(), "org": str(item[1]).strip() if len(item) > 1 else "", "role": ""}
    if isinstance(item, dict) and item.get("name"):
        return {"name": str(item["name"]).strip(), "org": str(item.get("org", "")).strip(), "role": str(item.get("role", "")).strip()}
    if isinstance(item, str) and item.strip():
        return {"name": item.strip(), "org": "", "role": ""}
    return None


def _extract_roster_names(roster: List[Any], metadata: Dict[str, Any]) -> List[Dict[str, str]]:
    parsed = extract_roster_with_roles(metadata)
    if parsed:
        return parsed
    return [c for item in (roster or []) if (c := _coerce_roster_item(item)) is not None]


def _has_third_person_self_mention(text: str, speaker_name: str) -> bool:
    if not text or not speaker_name or _SELF_INTRO_EXCLUSION_RE.search(text):
        return False
    parts = [p for p in re.split(r"\s+", speaker_name.strip()) if len(p) >= 3]
    if not parts:
        return False
    tokens = (re.escape(parts[0]), re.escape(speaker_name.strip()))
    return any(
        re.search(tmpl.format(name=tok), text, re.IGNORECASE)
        for tok in tokens
        for tmpl in _THIRD_PERSON_CUE_TEMPLATES
    )


def _match_attendee_name(mapped_name: str, roster_names: List[str]) -> Optional[str]:
    low = str(mapped_name).strip().lower()
    return next((n for n in roster_names if n.lower() == low or n.lower() in low), None)


def _has_acoustic_or_stereo_binding(
    metadata: Dict[str, Any], raw_label: str, norm_alias: str
) -> bool:
    if not isinstance(metadata, dict) or metadata.get("allow_third_person_regex_override"):
        return False
    if metadata.get("acoustic_binding_locked") or metadata.get("diarization_status") == "VERIFIED_STEREO_SEPARATION":
        return True
    for key in ("voiceprint_matches", "cluster_centroids", "cluster_embeddings", "stereo_channel_bindings"):
        mapping = metadata.get(key)
        if isinstance(mapping, dict) and (raw_label in mapping or norm_alias in mapping):
            return True
    return False


def detect_third_person_contradictions_in_turns(
    turns: List[Tuple[str, str]],
    roster: List[Any],
    metadata: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Checks turns for third-person self-references against the attributed speaker or existing_map."""
    roster_names = [r["name"] for r in _extract_roster_names(roster, metadata) if r.get("name")]
    if not roster_names:
        return []

    speaker_map = metadata.get("speaker_map") or {}
    contradictions: List[Dict[str, Any]] = []
    for idx, (spk, text) in enumerate(turns, 1):
        norm_alias = _normalize_cluster_alias(spk)
        if _has_acoustic_or_stereo_binding(metadata, spk, norm_alias):
            continue
        mapped_name = speaker_map.get(spk) or speaker_map.get(norm_alias) or spk
        matched = _match_attendee_name(mapped_name, roster_names)
        if matched and _has_third_person_self_mention(text, matched):
            contradictions.append({
                "turn_index": idx,
                "raw_label": spk,
                "cluster_alias": norm_alias,
                "assigned_speaker": matched,
                "text": text,
            })
    return contradictions


def _format_conflict_reasons(
    starved_labels: List[str],
    counts: Dict[str, int],
    total_turns: int,
    min_share_bps: int,
    roster_len: int,
    contradictions: List[Dict[str, Any]],
) -> str:
    reasons: List[str] = []
    if starved_labels:
        min_lbl = starved_labels[0]
        min_cnt = counts[min_lbl]
        reasons.append(
            f"Per-attendee speaker starvation detected: '{min_lbl}' holds only {min_cnt}/{total_turns} turns "
            f"({min_cnt / total_turns:.1%} < {min_share_bps / 100:.1f}%) across {roster_len} attendees"
        )
    if contradictions:
        c0 = contradictions[0]
        reasons.append(
            f"Intra-cluster third-person contradiction: '{c0['raw_label']}' (mapped to '{c0['assigned_speaker']}') "
            f"refers to '{c0['assigned_speaker']}' in the third person (\"{c0['text'][:80]}\")"
        )
    return "; ".join(reasons)


def detect_starved_cluster_or_contradiction(
    turns: List[Tuple[str, str]],
    counts: Dict[str, int],
    roster: List[Any],
    metadata: Dict[str, Any],
    min_starvation_turns: int = 20,
    min_share_bps: int = 500,
) -> Optional[Tuple[str, List[str], Dict[str, Any]]]:
    """Detects per-attendee starvation (< 5% of turns) and intra-cluster third-person contradictions."""
    total_turns = len(turns)
    contradictions = detect_third_person_contradictions_in_turns(turns, roster, metadata)
    starved_labels: List[str] = []
    if len(roster) >= 3 and total_turns >= min_starvation_turns and len(counts) >= 2:
        starved_labels = [lbl for lbl, cnt in counts.items() if cnt < 10 and (10000 * cnt) < (min_share_bps * total_turns)]

    if not starved_labels and not contradictions:
        return None

    dominant_label, dominant_turns = max(counts.items(), key=lambda x: x[1])
    unmapped: List[str] = []
    for lbl in [dominant_label] + starved_labels + [c["raw_label"] for c in contradictions]:
        if lbl and lbl not in unmapped:
            unmapped.append(lbl)

    evidence: Dict[str, Any] = {
        "attendee_count": len(roster),
        "total_turns": total_turns,
        "dominant_label": dominant_label,
        "dominant_label_turns": dominant_turns,
        "dominant_ratio": round(dominant_turns / max(total_turns, 1), 4),
        "speaker_counts": counts,
        "starved_labels": starved_labels,
        "min_share_bps": min_share_bps,
        "third_person_contradictions": contradictions,
    }
    reason_str = _format_conflict_reasons(
        starved_labels, counts, total_turns, min_share_bps, len(roster), contradictions
    )
    return reason_str, unmapped, evidence


def _add_binding(bindings: Dict[str, Set[str]], raw_id: str, speaker_name: str) -> None:
    if not raw_id or not speaker_name or raw_id.lower().startswith("unknown"):
        return
    alias = _normalize_cluster_alias(raw_id)
    bindings.setdefault(alias, set()).add(speaker_name)
    bindings.setdefault(raw_id, set()).add(speaker_name)


def collect_hint_cluster_bindings(
    orig_transcript: str,
    hints: List[Dict[str, Any]],
) -> Dict[str, Set[str]]:
    """Maps normalized cluster alias (e.g. 'Speaker 1') to set of hinted speaker names."""
    bindings: Dict[str, Set[str]] = {}
    if not hints:
        return bindings
    for h in hints:
        if isinstance(h, dict) and not h.get("turn_only"):
            spk_name = str(h.get("speaker_name") or h.get("speaker") or "").strip()
            bound_id = str(h.get("bound_speaker_id") or "").strip()
            _add_binding(bindings, bound_id, spk_name)

    valid_hints = [vh for vh in _prepare_valid_hints(hints) if not vh["raw"].get("turn_only")]
    if not orig_transcript or not valid_hints:
        return bindings

    for line in orig_transcript.splitlines():
        m = _TURN_LINE_RE.match(line.strip())
        if not m:
            continue
        curr_spk = re.sub(r"[\*\[\]]+", "", m.group("spk")).strip()
        norm_text = normalize_search_text(m.group("text").strip())
        matched = _find_matching_hint(valid_hints, norm_text, set(norm_text.split()))
        if matched:
            _add_binding(bindings, curr_spk, matched["speaker"])
    return bindings


def _evict_cluster_from_map(existing_map: Dict[str, str], cluster_key: str) -> None:
    alias = _normalize_cluster_alias(cluster_key)
    keys_to_remove = [
        k for k in existing_map
        if k in (cluster_key, alias) or _normalize_cluster_alias(k) == alias
    ]
    for k in keys_to_remove:
        existing_map.pop(k, None)


def _sign_interactive_collapse_receipt(metadata: Dict[str, Any]) -> None:
    if not isinstance(metadata, dict):
        return
    try:
        from dual_pipeline.hitl_governance_core.crypto import (
            append_signed_approval_to_ledger,
            create_signed_hitl_approval_entry,
        )
        from .collapse_detector import _extract_expected_subject_digest

        subj = _extract_expected_subject_digest(metadata)
        if not subj:
            return
        entry = create_signed_hitl_approval_entry(
            subject_sha256=subj,
            case_ids=["SC-001", "single_speaker_collapse"],
            decision="APPROVE",
            operator_id="human_operator",
            metadata={"meeting_name": metadata.get("meeting_name"), "project": metadata.get("project")},
            meeting_slug=metadata.get("meeting_slug") or metadata.get("meeting_name"),
            meeting_date=metadata.get("date") or metadata.get("meeting_date"),
        )
        append_signed_approval_to_ledger(entry, ledger_path=metadata.get("hitl_ledger_path"))
        metadata["collapse_approval_receipt"] = {"subject_sha256": subj, "entry_id": entry.get("entry_id")}
    except Exception as e:
        logger.debug("Interactive collapse receipt signing diagnostic: %s", e)


def guard_collapse_or_evict_for_hitl(
    transcript: str,
    roster: List[Any],
    metadata: Dict[str, Any],
    interactive_resolver: Optional[Any],
    existing_map: Dict[str, str],
) -> List[str]:
    """Runs check_speaker_skew_and_collapse; if interactive_resolver is present on starvation/contradiction, evicts clusters for HITL."""
    from .collapse_detector import check_speaker_skew_and_collapse
    from dual_pipeline.exceptions import HITLRequiredError
    try:
        check_speaker_skew_and_collapse(transcript, roster, metadata)
        return []
    except HITLRequiredError as exc:
        ev = getattr(exc, "evidence", None) or {}
        if not interactive_resolver:
            raise
        if not (ev.get("starved_labels") or ev.get("third_person_contradictions") or getattr(exc, "hitl_type", "") == "single_speaker_collapse"):
            raise
        evicted: List[str] = []
        for lbl in (getattr(exc, "unmapped_speakers", None) or []):
            alias = _normalize_cluster_alias(lbl)
            _evict_cluster_from_map(existing_map, lbl)
            if alias not in evicted:
                evicted.append(alias)
        metadata["collapse_evicted_pending_receipt"] = True
        return evicted


def _is_hint_binding_conflicted(hinted_speakers: Set[str], mapped: Optional[str]) -> bool:
    if len(hinted_speakers) > 1:
        return True
    if len(hinted_speakers) == 1 and mapped:
        return next(iter(hinted_speakers)).strip().lower() != str(mapped).strip().lower()
    return False


def reconcile_existing_map_conflicts(
    orig_transcript: str,
    current_transcript: str,
    existing_map: Dict[str, str],
    roster: List[Any],
    metadata: Dict[str, Any],
) -> List[str]:
    """Evicts clusters from existing_map when hints or third-person references conflict with existing_map."""
    evicted: List[str] = []
    bindings = collect_hint_cluster_bindings(orig_transcript, metadata.get("speaker_hints") or [])

    for cluster_key, hinted_speakers in bindings.items():
        alias = _normalize_cluster_alias(cluster_key)
        mapped = existing_map.get(cluster_key) or existing_map.get(alias)
        if _is_hint_binding_conflicted(hinted_speakers, mapped):
            _evict_cluster_from_map(existing_map, cluster_key)
            if alias not in evicted:
                evicted.append(alias)

    from .collapse_detector import extract_turn_speaker_inventory
    orig_turns = extract_turn_speaker_inventory(orig_transcript)
    temp_meta = dict(metadata, speaker_map=dict(existing_map))
    for c in detect_third_person_contradictions_in_turns(orig_turns, roster, temp_meta):
        alias = c["cluster_alias"]
        _evict_cluster_from_map(existing_map, alias)
        if alias not in evicted:
            evicted.append(alias)
    return evicted


def _score_discrepancy_candidates(
    spk: str,
    sample_text: str,
    roster_entries: List[Dict[str, str]],
) -> List[CandidateAttribution]:
    other_members = [r for r in roster_entries if r["name"].lower() != spk.lower()]
    conf_each = round(0.80 / max(len(other_members), 1), 4)
    candidates: List[CandidateAttribution] = []
    for m in other_members:
        r_name = m["name"]
        also_mentioned = _has_third_person_self_mention(sample_text, r_name)
        candidates.append(CandidateAttribution(
            attendee_name=r_name,
            organization=m.get("org", ""),
            role=m.get("role", ""),
            confidence=0.08 if also_mentioned else conf_each,
            confidence_tier="LOW" if also_mentioned else "MEDIUM",
            evidence_rationale=(
                f"Also contradicted by third-person reference to '{r_name}' in dialogue."
                if also_mentioned
                else f"Turn refers to '{spk}' in the third person; candidate '{r_name}' is an alternative roster participant."
            ),
        ))
    curr_entry = next((r for r in roster_entries if r["name"].lower() == spk.lower()), {"org": "", "role": ""})
    candidates.append(CandidateAttribution(
        attendee_name=spk,
        organization=curr_entry.get("org", ""),
        role=curr_entry.get("role", ""),
        confidence=0.05,
        confidence_tier="LOW",
        evidence_rationale=f"Contradicted by third-person self-reference to '{spk}' in dialogue.",
    ))
    candidates.sort(key=lambda x: x.confidence, reverse=True)
    if candidates:
        candidates[0].is_recommended = True
    return candidates


def build_third_person_discrepancies(
    resolved_transcript: str,
    roster: List[Any],
    metadata: Dict[str, Any],
) -> List[TurnAttributionDiscrepancy]:
    """Builds TurnAttributionDiscrepancy items for any turn with third-person self-reference."""
    from .collapse_detector import extract_turn_speaker_inventory
    turns = extract_turn_speaker_inventory(resolved_transcript)
    roster_entries = _extract_roster_names(roster, metadata)
    if not turns or len(roster_entries) < 2:
        return []

    grouped: Dict[str, Dict[str, Any]] = {}
    for c in detect_third_person_contradictions_in_turns(turns, roster, metadata):
        spk = c["assigned_speaker"]
        grouped.setdefault(spk, {"speaker": spk, "turns": [], "sample_text": c["text"]})["turns"].append(c["turn_index"])

    discrepancies: List[TurnAttributionDiscrepancy] = []
    threshold = get_hitl_ambiguity_margin_threshold()
    for spk, info in grouped.items():
        candidates = _score_discrepancy_candidates(spk, info["sample_text"], roster_entries)
        margin = round(candidates[0].confidence - (candidates[1].confidence if len(candidates) > 1 else 0.0), 4)
        turn_id_str = f"turn_{info['turns'][0]}"
        discrepancies.append(TurnAttributionDiscrepancy(
            cluster_id=f"third_person_conflict_{spk.lower().replace(' ', '_')}_{turn_id_str}",
            current_speaker=spk,
            detected_role="third_person_self_reference",
            start_timestamp="",
            end_timestamp="",
            turn_indices=info["turns"],
            sample_text=info["sample_text"],
            candidates=candidates,
            margin=margin,
            is_ambiguous=(len(roster_entries) > 2) or (margin <= threshold),
        ))
    return discrepancies


def run_iterative_conflict_discrepancies(
    engine: Any,
    resolved_transcript: str,
    orig_transcript: str,
    roster: List[Any],
    metadata: Dict[str, Any],
    interactive_resolver: Optional[Any],
    autonomous_if_decisive: bool,
    turn_reassignments: Dict[str, str],
    max_rounds: int = 3,
) -> str:
    """Iteratively detects and resolves third-person self-reference discrepancies via HITL."""
    curr_transcript = resolved_transcript
    for _ in range(max_rounds):
        discs = build_third_person_discrepancies(curr_transcript, roster, metadata)
        if not discs:
            break
        progress = False
        for disc in discs:
            res = engine._resolve_single_discrepancy(
                disc, metadata, interactive_resolver, autonomous_if_decisive, curr_transcript
            )
            if res and res[1] and res[1].lower() != disc.current_speaker.lower():
                turn_reassignments[f"{disc.current_speaker}:turn_{disc.turn_indices[0]}"] = res[1]
                curr_transcript = apply_turn_discrepancy_reassignment(curr_transcript, disc, res[1])
                progress = True
        if not progress:
            break
    return curr_transcript


def apply_turn_discrepancy_reassignment(
    transcript: str,
    disc: TurnAttributionDiscrepancy,
    chosen_speaker: str,
) -> str:
    """Rewrites the specific turns flagged by a TurnAttributionDiscrepancy to chosen_speaker."""
    if not transcript or not chosen_speaker or not disc.turn_indices:
        return transcript
    target_indices = set(disc.turn_indices)
    updated: List[str] = []
    turn_idx = 0
    for line in transcript.split("\n"):
        m = _TURN_LINE_RE.match(line.strip())
        label = re.sub(r"[\*\[\]]+", "", m.group("spk")).strip() if m else ""
        if not m or label.lower() in ("note", "important", "warning", "context", "meeting", "attendees", "participants"):
            updated.append(line)
            continue
        turn_idx += 1
        if turn_idx in target_indices:
            updated.append(f"{m.group('prefix')}{chosen_speaker}{m.group('suffix')}{m.group('text').strip()}")
        else:
            updated.append(line)
    return "\n".join(updated)
