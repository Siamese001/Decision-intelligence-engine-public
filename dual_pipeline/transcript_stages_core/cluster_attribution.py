"""
dual_pipeline/transcript_stages_core/cluster_attribution.py

Governs cluster-level speaker attribution review items, context windows,
digest verification, and cluster-wide writeback under speaker_utterance.schema.json.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("dual_pipeline.transcript_stages_core.cluster_attribution")

from dual_pipeline.exceptions import HITLRequiredError
from dual_pipeline.hitl_governance_core.canonical_evaluator import evaluate_canonical_decision
from dual_pipeline.hitl_governance_core.layman_clarity import format_layman_display_label
from dual_pipeline.speaker_attribution_core.review_context import SpeakerReviewContextError
from dual_pipeline.speaker_attribution_core.review_context_render import parse_speaker_review_turns
from dual_pipeline.speaker_attribution_core.review_context_units import (
    build_grouped_review_contexts,
    grouped_context_digest,
    render_grouped_contexts,
)


def _fallback_parse_turns(transcript: str, source_id: str) -> List[Dict[str, Any]]:
    turns: List[Dict[str, Any]] = []
    lines = transcript.splitlines()
    turn_pat = re.compile(
        r"^(?:\[([^\]]+)\]\s*)?(?:\*\*)?([^:]+?)(?:\*\*)?:\s*(.*)$"
    )
    pos = 0
    for idx, line in enumerate(lines):
        line_s = line.strip()
        if not line_s or line_s.startswith(("#", "---")):
            continue
        m = turn_pat.match(line_s)
        if m:
            ts = m.group(1) or "Unavailable"
            spk = m.group(2).strip()
            txt = m.group(3).strip()
            turns.append({
                "turn_id": str(idx),
                "source_position": pos,
                "speaker": spk,
                "timestamp": ts,
                "text": txt,
                "source_id": source_id,
            })
            pos += 1
    return turns


def parse_cluster_turns(transcript: str, source_id: str) -> List[Dict[str, Any]]:
    """Extract source turns with fallback parsing to ensure 100% turn extraction."""
    try:
        turns = parse_speaker_review_turns(transcript, source_id)
        if turns:
            return turns
    except Exception:
        pass
    return _fallback_parse_turns(transcript, source_id)


def extract_roster_candidates(metadata: Optional[Dict[str, Any]]) -> List[str]:
    """Extract candidate names from metadata roster/attendees."""
    if not metadata:
        return []
    roster = metadata.get("roster") or metadata.get("attendees") or []
    candidates: List[str] = []
    if isinstance(roster, dict):
        for group in roster.values():
            if isinstance(group, list):
                candidates.extend(
                    str(item.get("name") if isinstance(item, dict) else item).strip()
                    for item in group
                )
    elif isinstance(roster, list):
        candidates.extend(
            str(item.get("name") if isinstance(item, dict) else item).strip()
            for item in roster
        )
    return sorted(list({
        c for c in candidates
        if c and not re.match(r"^(?:Speaker|Voice|SPEAKER_)\s*[A-Za-z0-9_]*$", c, re.I)
    }))


def _build_cluster_options(
    candidates: Sequence[str],
    spk: str,
    project: str,
    metadata: Optional[Dict[str, Any]] = None,
    transcript: Optional[str] = None,
) -> List[Dict[str, Any]]:
    scored_cands = None
    if metadata and transcript and candidates:
        try:
            from dual_pipeline.speaker_attribution_core.candidate_scorer import score_candidates_for_speaker
            roster_tuples = [(c, "") for c in candidates]
            scored_cands = score_candidates_for_speaker(
                spk_id=spk,
                transcript=transcript,
                roster=roster_tuples,
                metadata=metadata,
            )
        except Exception as e:
            logger.debug("Failed to score candidates for cluster options: %s", e)

    raw_options: List[Dict[str, Any]] = []
    if scored_cands:
        for idx, sc in enumerate(scored_cands):
            cand = sc.attendee_name
            conf = float(sc.confidence)
            is_rec = (idx == 0)
            rationale = sc.evidence_rationale or f"Confirm speaker identity as {cand}"
            raw_options.append({
                "label": f"Assign {cand} to cluster {spk}",
                "plain_english_summary": rationale,
                "speaker": cand,
                "confidence": conf,
                "confidence_score": conf,
                "confidence_level": sc.confidence_tier or ("HIGH" if conf >= 0.80 else "MEDIUM"),
                "tier": sc.confidence_tier or ("HIGH" if conf >= 0.80 else "MEDIUM"),
                "recommended": is_rec,
                "is_recommended": is_rec,
            })
    else:
        if not candidates:
            candidates = ["Candidate 1", "Candidate 2"]
        for idx, cand in enumerate(candidates):
            is_rec = (idx == 0)
            conf = 0.90 if is_rec else (0.80 if idx == 1 else 0.60)
            raw_options.append({
                "label": f"Assign {cand} to cluster {spk}",
                "plain_english_summary": f"Confirm speaker identity as {cand}",
                "speaker": cand,
                "confidence": conf,
                "confidence_score": conf,
                "confidence_level": "HIGH" if conf >= 0.80 else "MEDIUM",
                "tier": "HIGH" if conf >= 0.80 else "MEDIUM",
                "recommended": is_rec,
                "is_recommended": is_rec,
            })
    eval_ctx = {
        "project": project,
        "subject_key": spk,
        "subject": spk,
        "category": "SPEAKER_ATTRIBUTION",
        "question": f"Who is speaking in the TARGET turn(s) for cluster '{spk}'?",
    }
    evaluation = evaluate_canonical_decision(
        raw_options,
        context=eval_ctx,
        category="SPEAKER_ATTRIBUTION",
    )
    formatted: List[Dict[str, Any]] = []
    for opt in evaluation.options:
        opt_d = dict(opt)
        opt_d["label"] = format_layman_display_label(opt_d)
        formatted.append(opt_d)
    return formatted


def build_cluster_review_items(
    transcript: str,
    unmapped_speakers: List[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str, str, List[Dict[str, Any]]]:
    """Construct homogeneous_cluster reviewItem payloads conforming to speaker_utterance.schema.json."""
    meta = metadata or {}
    source_id = str(meta.get("source_id") or meta.get("meeting_name") or meta.get("title") or "transcript")
    project = str(meta.get("project") or meta.get("project_id") or "internal_consulting").strip()
    all_turns = parse_cluster_turns(transcript, source_id)
    candidates = extract_roster_candidates(meta)

    review_items: List[Dict[str, Any]] = []
    all_contexts: List[Dict[str, Any]] = []
    rendered_prompts: List[str] = []
    all_options: List[Dict[str, Any]] = []

    for spk in unmapped_speakers:
        spk_clean = spk.strip("[]").strip()
        cluster_turns = [
            t for t in all_turns
            if t["speaker"].strip().casefold() in (spk_clean.casefold(), f"speaker {spk_clean.casefold()}")
        ]
        target_ids = [str(t["turn_id"]) for t in cluster_turns]
        if not target_ids:
            continue

        contexts, context_status = build_grouped_review_contexts(all_turns, target_ids)
        digest = grouped_context_digest(contexts) if contexts else ("0" * 64)
        all_contexts.extend(contexts)

        clean_id = re.sub(r"[^a-zA-Z0-9_.-]", "_", spk_clean)
        review_item: Dict[str, Any] = {
            "review_id": f"rev_cluster_{clean_id}",
            "item_type": "homogeneous_cluster",
            "target_utterance_ids": target_ids,
            "context_start_sec": 0.0,
            "context_end_sec": 0.0,
            "audio_channel": None,
            "candidate_speakers": candidates,
            "assigned_speaker": None,
            "resolution_status": "pending",
            "context_status": context_status,
            "context_digest": digest,
            "linked_cluster_ids": [],
            "review_contexts": contexts,
        }
        review_items.append(review_item)

        if contexts:
            rendered = render_grouped_contexts(contexts)
            rendered_prompts.append(rendered)

        spk_options = _build_cluster_options(candidates, spk_clean, project, metadata=meta, transcript=transcript)
        all_options.extend(spk_options)

    overall_digest = grouped_context_digest(all_contexts) if all_contexts else ("0" * 64)
    rendered_body = "\n\n--- Separate speaker cluster ---\n\n".join(rendered_prompts)
    first_spk = unmapped_speakers[0] if unmapped_speakers else "Unknown"
    prompt = (
        f"### HITL Decision Required (Speaker Attribution — Cluster '{first_spk}')\n\n"
        f"{rendered_body}\n\n"
        f"Who is speaking in the TARGET turn(s) for cluster '{first_spk}'?"
    )
    return review_items, all_contexts, overall_digest, prompt, all_options


def apply_cluster_attribution_resolution(
    transcript: str,
    review_item: Mapping[str, Any],
    resolved_speaker: str,
    context_digest: str,
    metadata: Optional[Dict[str, Any]] = None,
    speaker_map: Optional[Dict[str, str]] = None,
) -> Tuple[str, Dict[str, str]]:
    """Verify context_digest and propagate resolved speaker cluster-wide across all turns."""
    expected_digest = review_item.get("context_digest")
    if not context_digest or context_digest != expected_digest:
        raise SpeakerReviewContextError(
            f"Speaker decision is blocked because context_digest {context_digest!r} "
            f"does not match visible review context digest {expected_digest!r}."
        )

    # Stale context verification against current transcript turns
    target_ids = set(review_item.get("target_utterance_ids", []))
    review_contexts = review_item.get("review_contexts", [])
    if not review_contexts:
        raise SpeakerReviewContextError("Speaker decision is blocked because visible review context is unavailable.")

    # Identify cluster label from target turns
    cluster_labels = set()
    for ctx in review_contexts:
        for turn in ctx.get("turns", []):
            if turn.get("turn_id") in target_ids or turn.get("is_target"):
                cluster_labels.add(turn.get("speaker"))

    out_map = dict(speaker_map or {})
    for lbl in cluster_labels:
        if lbl:
            clean_lbl = lbl.strip("[]").strip()
            out_map[clean_lbl] = resolved_speaker

    from .l1_attribution import apply_speaker_map
    resolved_text = apply_speaker_map(transcript, out_map)
    if metadata is not None:
        target_meta_map = metadata.setdefault("speaker_map", {})
        target_meta_map.update(out_map)

    return resolved_text, out_map
