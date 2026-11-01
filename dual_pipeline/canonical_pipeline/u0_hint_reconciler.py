"""
dual_pipeline/canonical_pipeline/u0_hint_reconciler.py

Deterministic turn-level speaker hint reconciliation for Stage U0/L1.
Reconciles queued quote hints against transcript turns/segments even when
acoustic clustering collapses to a single voice cluster, and normalizes
ASR product homophones ("Sarah" -> "Sherpa").
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _turn_cluster_id(t: Dict[str, Any]) -> str:
    return str(t.get("voice_id") or t.get("raw_speaker") or t.get("speaker") or "").strip()


def _turn_elapsed_distance(t: Dict[str, Any], elapsed_seconds: float) -> float:
    sec = t.get("start_sec", t.get("start", t.get("seconds")))
    if sec is None:
        return float("inf")
    try:
        return abs(float(sec) - float(elapsed_seconds))
    except (ValueError, TypeError):
        return float("inf")


def _find_matching_turn_indices(
    turns: List[Dict[str, Any]],
    clean_quote: str,
    elapsed_seconds: Optional[float] = None,
) -> List[int]:
    from dual_pipeline.live_meeting_recorder_core.speaker_hint_matcher import (
        segment_contains_quote,
        token_overlap_match,
    )

    matched = [i for i, t in enumerate(turns) if segment_contains_quote(t, clean_quote)]
    if not matched:
        overlap_segs = token_overlap_match(turns, clean_quote, min_ratio=0.60)
        matched = [i for i, t in enumerate(turns) if t in overlap_segs]
    if len(matched) > 1 and elapsed_seconds is not None:
        closest_idx = min(matched, key=lambda idx: _turn_elapsed_distance(turns[idx], float(elapsed_seconds)))
        if _turn_elapsed_distance(turns[closest_idx], float(elapsed_seconds)) < float("inf"):
            vid = _turn_cluster_id(turns[closest_idx])
            return [i for i in matched if _turn_cluster_id(turns[i]) == vid] if vid else [closest_idx]
    return matched


def _propagate_bound_cluster(
    updated_turns: List[Dict[str, Any]], bound_cluster: str, spk: str, anchor_by_idx: Dict[int, str]
) -> None:
    for idx, t in enumerate(updated_turns):
        if idx not in anchor_by_idx and _turn_cluster_id(t) == bound_cluster:
            t.setdefault("raw_speaker", bound_cluster)
            t["speaker"] = spk
            t["hint_propagated"] = True
            anchor_by_idx[idx] = spk


def _apply_hint_to_turns(
    updated_turns: List[Dict[str, Any]],
    hint: Dict[str, Any],
    anchor_by_idx: Dict[int, str],
) -> None:
    from dual_pipeline.live_meeting_recorder_core.speaker_hint_matcher import (
        normalize_search_text,
    )

    quote = str(hint.get("quote") or hint.get("quote_text") or "").strip()
    spk = str(hint.get("speaker_name") or hint.get("speaker") or "").strip()
    if not quote or not spk:
        return
    clean_q = normalize_search_text(quote)
    elapsed_sec = hint.get("session_elapsed_seconds", hint.get("elapsed_seconds"))
    matched_indices = _find_matching_turn_indices(updated_turns, clean_q, elapsed_seconds=elapsed_sec)
    if not matched_indices:
        return
    target_clusters: set[str] = set()
    for idx in matched_indices:
        orig_cluster = _turn_cluster_id(updated_turns[idx])
        if orig_cluster and orig_cluster.lower() not in ("unknown", "speaker unknown", "speaker", "host", "voice host"):
            target_clusters.add(orig_cluster)
            updated_turns[idx].setdefault("raw_speaker", orig_cluster)
        updated_turns[idx]["speaker"] = spk
        updated_turns[idx]["hint_applied"] = True
        updated_turns[idx]["hint_quote"] = quote
        anchor_by_idx[idx] = spk

    if len(target_clusters) == 1 and not hint.get("turn_only"):
        _propagate_bound_cluster(updated_turns, next(iter(target_clusters)), spk, anchor_by_idx)

    hint["status"] = "RECONCILED"
    hint["updated_segments_count"] = len(matched_indices)
    hint["voice_id"] = (
        hint.get("voice_id")
        or (next(iter(target_clusters)) if len(target_clusters) == 1 else None)
        or updated_turns[matched_indices[0]].get("voice_id")
        or "Voice A"
    )


def _can_propagate_mid_turn(t_mid: Dict[str, Any], clus_a: str, spk_a: str) -> bool:
    clus_mid = str(t_mid.get("voice_id") or t_mid.get("raw_speaker") or "").strip()
    if clus_a and clus_mid and clus_mid != clus_a:
        return False
    curr_spk = str(t_mid.get("speaker") or "").strip()
    return not (curr_spk and curr_spk != spk_a and not curr_spk.lower().startswith(("speaker", "voice", "unknown")))


def _propagate_contiguous_speaker_spans(
    updated_turns: List[Dict[str, Any]],
    anchor_by_idx: Dict[int, str],
    max_gap: int = 8,
) -> None:
    sorted_anchors = sorted(anchor_by_idx.items())
    for (idx_a, spk_a), (idx_b, spk_b) in zip(sorted_anchors, sorted_anchors[1:]):
        if spk_a != spk_b or not (0 < (idx_b - idx_a) <= max_gap):
            continue
        clus_a = str(updated_turns[idx_a].get("voice_id") or updated_turns[idx_a].get("raw_speaker") or "").strip()
        for mid in range(idx_a + 1, idx_b):
            if mid not in anchor_by_idx and _can_propagate_mid_turn(updated_turns[mid], clus_a, spk_a):
                updated_turns[mid]["speaker"] = spk_a
                updated_turns[mid]["hint_propagated"] = True


def reconcile_queued_hints_against_turns(
    turns: List[Dict[str, Any]],
    hints: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Deterministically reconcile queued quote hints against transcript turns or segments."""
    from dual_pipeline.identity_resolver_core.resolver import IdentityResolver

    resolver = IdentityResolver()
    updated_turns = [dict(t) for t in turns]
    updated_hints = [dict(h) for h in hints]
    anchor_by_idx: Dict[int, str] = {}

    for h in updated_hints:
        _apply_hint_to_turns(updated_turns, h, anchor_by_idx)

    _propagate_contiguous_speaker_spans(updated_turns, anchor_by_idx)

    for i, t in enumerate(updated_turns):
        txt = str(t.get("text") or "")
        if txt:
            norm_lines, _, _ = resolver.normalize_phonetic_dialogue(
                [(i + 1, str(t.get("speaker") or "Speaker"), txt)]
            )
            t["text"] = norm_lines[0][2]

    return updated_turns, updated_hints


def propagate_u0_context_to_metadata(meta: Dict[str, Any], u0_data: Dict[str, Any]) -> None:
    """Propagates saved U0 stage metadata on resume while preserving fresh CLI inputs."""
    if not isinstance(u0_data.get("metadata"), dict):
        return
    saved_meta = u0_data["metadata"]
    for k, v in saved_meta.items():
        if k not in meta or meta[k] is None:
            meta[k] = v
    if isinstance(saved_meta.get("speaker_map"), dict):
        fresh_map = meta.get("speaker_map") or {}
        if fresh_map:
            meta["speaker_map"] = dict(fresh_map)
        else:
            meta["speaker_map"] = dict(saved_meta["speaker_map"])
    cert = meta.get("audio_certificate")
    if isinstance(cert, dict) and cert.get("verdict") == "WARN" and bool(meta.get("hitl_approved") or meta.get("allow_warn_certificate", True)):
        cert["hitl_approved"] = True


def reconcile_acoustic_unknown_utterances_with_hints(
    raw_utterances: List[Dict[str, Any]],
    hints: List[Dict[str, Any]],
) -> None:
    """Matches quote hints against unknown acoustic utterances in place."""
    if not hints or not raw_utterances:
        return
    from dual_pipeline.speaker_attribution_core.candidate_scorer import normalize_search_text

    for h in hints:
        if not isinstance(h, dict):
            continue
        q = str(h.get("quote") or h.get("quote_text") or "").strip()
        spk_name = str(h.get("speaker_name") or h.get("speaker") or "").strip()
        if not q or not spk_name:
            continue
        norm_q = normalize_search_text(q)
        q_tokens = set(norm_q.split())
        for u in raw_utterances:
            curr_spk = str(u.get("speaker") or u.get("raw_speaker") or "").strip()
            if curr_spk.lower() in ("unknown", "speaker unknown"):
                u_text = str(u.get("text") or "").strip()
                norm_u = normalize_search_text(u_text)
                if norm_q in norm_u or (
                    len(q_tokens) >= 3
                    and (len(q_tokens.intersection(set(norm_u.split()))) / len(q_tokens)) >= 0.65
                ):
                    u["speaker"] = spk_name
                    u["reconciled_by_hint"] = True
                    h["status"] = "RECONCILED"


def _clean_attendee_name_key(raw: str) -> str:
    return raw.split("(")[0].strip().lower()


def extract_manifest_roles(existing_att: Any, att_cand: Any) -> Dict[str, str]:
    """Extracts attendee roles from manifest candidates against existing attendee filter."""
    if not isinstance(att_cand, list):
        return {}
    if not existing_att:
        return {
            str(a["name"]).split("(")[0].strip(): a["role"]
            for a in att_cand
            if isinstance(a, dict) and a.get("name") and a.get("role")
        }
    existing_names: set[str] = set()
    if isinstance(existing_att, str):
        import re
        for n in re.split(r"[,;\n]+(?![^(]*\))", existing_att):
            if n.strip():
                existing_names.add(_clean_attendee_name_key(n))
    elif isinstance(existing_att, list):
        for a in existing_att:
            if isinstance(a, str) and a.strip():
                existing_names.add(_clean_attendee_name_key(a))
            elif isinstance(a, dict) and a.get("name"):
                existing_names.add(_clean_attendee_name_key(str(a["name"])))
    return {
        str(a["name"]).split("(")[0].strip(): a["role"]
        for a in att_cand
        if isinstance(a, dict)
        and a.get("name")
        and a.get("role")
        and _clean_attendee_name_key(str(a["name"])) in existing_names
    }


def extract_role_hints_from_records(records: Any) -> Dict[str, str]:
    """Extracts speaker->role mappings from speaker_hints or chat_attributions records."""
    if not isinstance(records, list):
        return {}
    roles: Dict[str, str] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        spk = str(rec.get("speaker_name") or rec.get("speaker") or "").split("(")[0].strip()
        meta = rec.get("metadata") if isinstance(rec.get("metadata"), dict) else {}
        role = str(rec.get("role") or meta.get("role") or "").strip()
        if spk and role:
            roles[spk] = role
    return roles


def verify_resumed_u0_input_hash(
    input_path: Any,
    meta: Dict[str, Any],
    u0_data: Dict[str, Any],
    input_hash: str,
    src_file_path: Optional[Path],
) -> None:
    if not input_path or not Path(input_path).is_file():
        return
    from dual_pipeline.transcript_pipeline_core.caching import compute_source_file_hash

    curr_hash = compute_source_file_hash(input_path)
    meta.setdefault("source_file_hash", curr_hash)
    saved_src_hash = (
        u0_data.get("source_file_hash")
        or u0_data.get("metadata", {}).get("source_file_hash")
        or u0_data.get("metadata", {}).get("raw_source_sha256")
    )
    expected_hashes = {h for h in (input_hash, saved_src_hash) if h}
    if src_file_path and src_file_path.is_file() and src_file_path.resolve() != Path(input_path).resolve():
        orig_hash = compute_source_file_hash(src_file_path)
        if orig_hash and orig_hash in expected_hashes:
            expected_hashes.add(curr_hash)
    if curr_hash and expected_hashes and curr_hash not in expected_hashes:
        raise ValueError(
            f"Fail-Fast: Input file hash mismatch on resume. Current: {curr_hash} != Checkpoint: {input_hash}. "
            "Source file has changed; stale checkpoint cannot be resumed."
        )
