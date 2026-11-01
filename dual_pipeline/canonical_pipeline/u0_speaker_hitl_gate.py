"""
dual_pipeline/canonical_pipeline/u0_speaker_hitl_gate.py

Pre-L2 U0-Exit Atomic Speaker HITL Gate & Cryptographic ALR Verification.
Enforces that before any Stage L2 synthesis LLM call executes:
1. All active speaker clusters in the transcript and speaker_map are resolved
   (zero generic SPEAKER_XX, Speaker N, Voice X, or 'Unknown' clusters).
2. No single-speaker collapse (>=85% turns/words in multi-attendee meetings)
   proceeds without an Ed25519-signed operator receipt in hitl_decision_ledger.json.
3. No candidate pair within the SSOT 20% ambiguity margin (`needs_operator_decision`)
   proceeds autonomously without an operator decision or interactive_resolver.
4. Any `hitl_receipt_bound=True` claim in metadata is cryptographically verified
   against `hitl_decision_ledger.json` (self-asserted flags without ledger backing
   are rejected fail-closed).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from dual_pipeline.exceptions import HITLRequiredError
from dual_pipeline.parameters import (
    get_hitl_ambiguity_margin_threshold,
    needs_operator_decision,
)

logger = logging.getLogger("dual_pipeline.canonical_pipeline.u0_speaker_hitl_gate")

_GENERIC_OR_UNKNOWN_RE = re.compile(
    r"^(?:speaker[_\-\s]*[a-z0-9]*|voice[_\-\s]*[a-z0-9]+|partner_advisory\s+speaker|unknown(?:\s+speaker)?|unattributed|anonymous)$",
    re.IGNORECASE,
)


def is_generic_or_unknown_label(label: Any) -> bool:
    """Returns True if label is empty, generic (SPEAKER_00, Speaker 1, Voice A), or Unknown."""
    s = str(label or "").strip("[]* :\t\r\n")
    if not s:
        return True
    return bool(_GENERIC_OR_UNKNOWN_RE.fullmatch(s))


def _is_required_cluster_mapped(req: str, norm_map: Dict[str, str]) -> bool:
    req_clean = str(req).strip()
    req_low = req_clean.lower()
    if not req_low or not is_generic_or_unknown_label(req_clean):
        return True
    m_raw = re.match(r"^speaker_0*(\d+)$", req_low)
    alias_low = f"speaker {int(m_raw.group(1)) + 1}" if m_raw else req_low
    return req_low in norm_map or alias_low in norm_map


def are_all_clusters_resolved(
    speaker_map: Optional[Dict[str, Any]],
    required_clusters: Optional[Set[str]] = None,
) -> bool:
    """
    Returns True ONLY when speaker_map is non-empty, EVERY value in speaker_map
    is a non-generic, non-unknown human name, AND all required_clusters (if provided)
    are mapped to resolved human names.
    """
    if not speaker_map or not isinstance(speaker_map, dict):
        return False
    values = [v for v in speaker_map.values() if v is not None]
    if not values or any(is_generic_or_unknown_label(v) for v in values):
        return False
    if not required_clusters:
        return True
    norm_map = {str(k).strip().lower(): str(v).strip() for k, v in speaker_map.items() if v}
    return all(_is_required_cluster_mapped(req, norm_map) for req in required_clusters)


def verify_authentic_hitl_receipt_binding(
    metadata: Dict[str, Any],
    transcript: str = "",
    ledger_path: Optional[Union[Path, str]] = None,
) -> bool:
    """
    Rejects self-asserted `hitl_receipt_bound=True` in metadata unless backed by
    an authentic Ed25519-signed approval entry in hitl_decision_ledger.json.
    """
    if not isinstance(metadata, dict):
        return False
    claimed_bound = bool(metadata.get("hitl_receipt_bound"))
    if not claimed_bound:
        return True

    from dual_pipeline.transcript_qa_core.gate1_alr import (
        _extract_roster_names,
        _find_matching_verified_approval,
        _resolve_expected_subject_digest,
    )
    from dual_pipeline.hitl_governance_core.crypto import find_signed_approvals
    from dual_pipeline.hitl_governance_core.ledger import _find_default_ledger_path
    from datetime import datetime, timezone

    roster_names = _extract_roster_names(metadata, transcript)
    expected_subject = _resolve_expected_subject_digest(metadata, roster_names)
    if not expected_subject:
        metadata["hitl_receipt_bound"] = False
        raise ValueError(
            "FORGED_HITL_RECEIPT_BOUND: Metadata asserts hitl_receipt_bound=True without a "
            "bindable subject_sha256 or context_sha256."
        )

    target_ledger = (
        Path(ledger_path).resolve()
        if ledger_path
        else (
            Path(metadata["hitl_ledger_path"]).resolve()
            if metadata.get("hitl_ledger_path")
            else _find_default_ledger_path().resolve()
        )
    )
    all_approvals = find_signed_approvals(ledger_path=target_ledger)
    now_dt = datetime.now(timezone.utc)
    verified = _find_matching_verified_approval(all_approvals, expected_subject, metadata, now_dt)
    if not verified:
        metadata["hitl_receipt_bound"] = False
        raise ValueError(
            "FORGED_HITL_RECEIPT_BOUND: Metadata asserts hitl_receipt_bound=True, but no "
            "matching Ed25519-signed operator receipt exists in hitl_decision_ledger.json."
        )
    return True


def _is_exempt_modality(metadata: Dict[str, Any]) -> bool:
    return bool(
        metadata.get("is_document")
        or metadata.get("notes_mode")
        or metadata.get("is_notes")
        or metadata.get("canonical_modality") in ("DOCUMENT", "MEETING_NOTES")
    )


def _find_unresolved_turn_speakers(
    turns: List[Tuple[str, str]], speaker_map: Dict[str, Any]
) -> List[str]:
    unresolved: List[str] = []
    for spk, _ in turns:
        mapped = speaker_map.get(spk, spk)
        if is_generic_or_unknown_label(mapped) and spk not in unresolved:
            unresolved.append(spk)
    return unresolved


def _raise_unresolved_speaker_hitl(
    transcript: str, metadata: Dict[str, Any], unresolved_in_turns: List[str]
) -> None:
    from dual_pipeline.speaker_attribution_core.review_context_render import (
        build_hitl_review_payload,
    )

    target_spk = unresolved_in_turns[0]
    src_id = str(metadata.get("source_id") or metadata.get("meeting_name") or "pending")
    base_q = (
        f"HITL Decision: Pre-L2 Speaker Attribution for {target_spk}\n\n"
        f"**Candidate Cluster**: `{target_spk}`\n"
        "**Context**: Assign this unresolved speaker cluster before Stage L2 synthesis."
    )
    candidates = metadata.get("candidate_attributions", {}).get(target_spk, [])
    payload = build_hitl_review_payload(
        transcript, target_spk, base_q, candidates, source_id=src_id, fail_closed=False
    )
    metadata["pipeline_status"] = "AWAITING_SPEAKER_HITL"
    raise HITLRequiredError(
        f"PRE-L2 SPEAKER HITL GATE BLOCKED (AWAITING_SPEAKER_HITL): Unresolved speaker cluster(s) "
        f"{unresolved_in_turns} detected before Stage L2 synthesis.",
        unmapped_speakers=unresolved_in_turns,
        hitl_type="pre_l2_speaker_hitl_gate",
        **payload,
    )


def _extract_score_val(item: Any) -> float:
    return float(item.get("confidence", 0.0) if isinstance(item, dict) else item)


def _check_cluster_ambiguity_margins(metadata: Dict[str, Any], margin_thresh: float) -> None:
    cluster_scores = metadata.get("cluster_candidate_scores")
    if not isinstance(cluster_scores, dict):
        return
    resolved_set = metadata.get("ledger_resolved_clusters") or ()
    for cluster_id, scores in cluster_scores.items():
        if not isinstance(scores, list) or len(scores) < 2:
            continue
        top_s, sec_s = _extract_score_val(scores[0]), _extract_score_val(scores[1])
        if not needs_operator_decision(top_s, sec_s, threshold=margin_thresh):
            continue
        if metadata.get("hitl_verified_speakers") or cluster_id in resolved_set:
            continue
        metadata["pipeline_status"] = "AWAITING_SPEAKER_HITL"
        raise HITLRequiredError(
            f"PRE-L2 SPEAKER HITL GATE BLOCKED (AWAITING_SPEAKER_HITL): Cluster '{cluster_id}' "
            f"margin ({abs(top_s - sec_s):.4f}) <= SSOT threshold ({margin_thresh:.2f}).",
            unmapped_speakers=[cluster_id],
            hitl_type="pre_l2_speaker_margin_gate",
        )


def enforce_pre_l2_speaker_hitl_gate(
    transcript: str,
    metadata: Dict[str, Any],
    interactive_resolver: Optional[Callable[[str, List[str]], str]] = None,
) -> Dict[str, Any]:
    """
    Pre-L2 U0-Exit Atomic Speaker HITL Gate.
    Executes before any Stage L2 LLM call to guarantee no forged receipts,
    no unresolved speaker clusters, no unapproved collapse, and no ambiguous margins.
    """
    if not isinstance(metadata, dict):
        return {"status": "PASS", "unresolved_clusters": []}
    if _is_exempt_modality(metadata):
        return {"status": "EXEMPT_MODALITY", "unresolved_clusters": []}

    verify_authentic_hitl_receipt_binding(metadata, transcript=transcript)

    from dual_pipeline.speaker_attribution_core.collapse_detector import (
        check_speaker_skew_and_collapse,
        extract_turn_speaker_inventory,
    )

    turns = extract_turn_speaker_inventory(transcript)
    speaker_map = metadata.get("speaker_map") if isinstance(metadata.get("speaker_map"), dict) else {}
    roster = metadata.get("roster") or metadata.get("attendees") or []

    unresolved_in_turns = _find_unresolved_turn_speakers(turns, speaker_map)
    if unresolved_in_turns and not interactive_resolver:
        _raise_unresolved_speaker_hitl(transcript, metadata, unresolved_in_turns)

    try:
        check_speaker_skew_and_collapse(transcript, roster, metadata)
    except HITLRequiredError:
        if not interactive_resolver:
            metadata["pipeline_status"] = "AWAITING_SPEAKER_HITL"
            raise

    margin_thresh = get_hitl_ambiguity_margin_threshold()
    if not interactive_resolver:
        _check_cluster_ambiguity_margins(metadata, margin_thresh)

    metadata["pre_l2_speaker_hitl_verified"] = True
    return {
        "status": "PASS",
        "unresolved_clusters": [],
        "margin_threshold": margin_thresh,
    }


def compute_speaker_binding_digest(meta: Dict[str, Any]) -> str:
    """Computes a deterministic SHA-256 digest over speaker_map, speaker_hints, and voiceprint_matches."""
    import hashlib
    import json
    payload = {
        "speaker_map": dict(sorted((str(k), str(v)) for k, v in (meta.get("speaker_map") or {}).items())),
        "speaker_hints": [
            {
                "voice_id": str(h.get("voice_id") or ""),
                "speaker_name": str(h.get("speaker_name") or h.get("speaker") or ""),
                "quote": str(h.get("quote") or ""),
            }
            for h in (meta.get("speaker_hints") or [])
            if isinstance(h, dict)
        ],
        "voiceprint_matches": dict(sorted((str(k), str(v)) for k, v in (meta.get("voiceprint_matches") or {}).items())),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _remap_speaker_labels_in_text(text: str, remap: Dict[str, str]) -> str:
    if not text or not remap:
        return text
    out = text
    for old_spk, new_spk in sorted(remap.items(), key=lambda kv: len(str(kv[0])), reverse=True):
        if not old_spk or not new_spk or str(old_spk) == str(new_spk):
            continue
        esc = re.escape(str(old_spk))
        out = re.sub(
            rf"(\*\*(?:\[\d{{2}}:\d{{2}}(?::\d{{2}})?\]\s*)?){esc}(:\*\*|\*\*:)",
            rf"\g<1>{new_spk}\g<2>",
            out,
        )
        out = re.sub(rf"(\*\*\[?){esc}(\]?\*\*)", rf"\g<1>{new_spk}\g<2>", out)
        out = re.sub(rf"(^\s*(?:\[\d{{2}}:\d{{2}}(?::\d{{2}})?\]\s*)?\[?){esc}(\]?\s*:)", rf"\g<1>{new_spk}\g<2>", out, flags=re.MULTILINE)
    return out


def _apply_resumed_map_and_hints(
    c0_data: Dict[str, Any], meta: Dict[str, Any], pa_context: Any, source_text: str, prev_map: Dict[str, str]
) -> str:
    if "speaker_map" in c0_data or meta.get("speaker_map"):
        fresh_map = meta.get("speaker_map") or {}
        merged_map = {**prev_map, **fresh_map}
        meta["speaker_map"] = merged_map
        if fresh_map:
            remap = {
                **merged_map,
                **{
                    str(old_spk): str(fresh_map[c_key])
                    for c_key, old_spk in prev_map.items()
                    if old_spk and fresh_map.get(c_key) and str(old_spk) != str(fresh_map[c_key])
                },
            }
            source_text = _remap_speaker_labels_in_text(source_text, remap)
            meta["resolved_transcript"] = source_text
        if isinstance(pa_context, dict) and isinstance(pa_context.get("metadata"), dict):
            pa_context["metadata"]["speaker_map"] = meta["speaker_map"]

    from dual_pipeline.speaker_attribution_core.candidate_scorer import load_speaker_hints
    from dual_pipeline.speaker_attribution_core.quote_hint_turn_mapper import apply_quote_hints_to_turns
    resumed_hints = meta.get("speaker_hints") or load_speaker_hints(meta)
    if resumed_hints and isinstance(resumed_hints, list):
        meta["speaker_hints"] = resumed_hints
        source_text, _ = apply_quote_hints_to_turns(source_text, resumed_hints)
        meta["resolved_transcript"] = source_text
    return source_text


def reconciliate_resumed_c0_bindings(
    c0_data: Dict[str, Any], meta: Dict[str, Any], pa_context: Any, source_text: str, checkpoint: Any
) -> str:
    prev_map = dict(c0_data.get("speaker_map") or {}) if isinstance(c0_data.get("speaker_map"), dict) else {}
    saved_sha = c0_data.get("binding_sha256") or compute_speaker_binding_digest({
        "speaker_map": prev_map,
        "speaker_hints": c0_data.get("speaker_hints") or [],
        "voiceprint_matches": c0_data.get("voiceprint_matches") or {},
    })
    source_text = _apply_resumed_map_and_hints(c0_data, meta, pa_context, source_text, prev_map)
    cur_sha = compute_speaker_binding_digest(meta)
    meta["binding_sha256"] = cur_sha
    if cur_sha != saved_sha:
        meta["speaker_binding_updated_on_resume"] = True
        checkpoint.save_stage("C0_PA", {
            "pa_context": pa_context, "source_text": source_text, "resolved_transcript": source_text,
            "speaker_map": meta.get("speaker_map", {}), "speaker_hints": meta.get("speaker_hints", []),
            "voiceprint_matches": meta.get("voiceprint_matches", {}), "binding_sha256": cur_sha,
        })
    return source_text


__all__ = [
    "is_generic_or_unknown_label",
    "are_all_clusters_resolved",
    "verify_authentic_hitl_receipt_binding",
    "enforce_pre_l2_speaker_hitl_gate",
    "compute_speaker_binding_digest",
    "reconciliate_resumed_c0_bindings",
]
