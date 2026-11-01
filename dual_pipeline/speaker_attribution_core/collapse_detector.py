"""
dual_pipeline/speaker_attribution_core/collapse_detector.py

Pre-Gate 1 forensic speaker collapse and skew detection engine.
Identifies single-speaker collapse and excessive speaker skew before Output 01 synthesis.
Mandates HITL interruption whenever a multi-attendee meeting has >= 85% of turns or
duration attributed to a single speaker without an authenticated operator receipt.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from dual_pipeline.hitl_governance_core.crypto import (
    compute_alr_subject_digest,
    export_public_key_pem,
    find_signed_approvals,
    get_governed_hitl_keypair,
    verify_hitl_approval_signature,
)
from dual_pipeline.hitl_governance_core.ledger import _find_default_ledger_path
from dual_pipeline.exceptions import HITLRequiredError

logger = logging.getLogger("dual_pipeline.speaker_attribution_core.collapse_detector")

_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200d\ufeff]")
_CYRILLIC_LOOKALIKES = str.maketrans("асеорхуАВЕКМНОРСТХ", "aceopxyABEKMHOPCTX")

_TURN_REGEX = re.compile(
    r"(?m)^(?:"
    r"(?:\*\*)?(?:\[\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?(?:\s*[-–—]\s*\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?)?\]|\d{1,2}:\d{2}(?::\d{2})?)(?:\*\*)?\s*"
    r")?"
    r"(?:\*\*)?\[?([A-Za-z0-9_\s\.\-\(\)/\"\']+?)\]?(?::\*\*|\*\*:|:)\s*(.+)$"
)


_VOICE_TAG_RE = re.compile(r"<v\s+([^>]+)>(.*?)(?:</v>|$)", re.DOTALL)
_TEAMS_HEADER_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_,\s\.\-\(\)/\"\']+?)\s+\d{1,2}:\d{2}(?::\d{2})?$")
_DISALLOWED_LABELS = frozenset({"note", "important", "warning", "context", "meeting", "attendees", "participants"})


def normalize_speaker_token(token: str) -> str:
    """Normalize speaker label against zero-width evasion, homoglyphs, and full-width tokens."""
    cleaned = unicodedata.normalize("NFKC", _ZERO_WIDTH_RE.sub("", token))
    cleaned = cleaned.translate(_CYRILLIC_LOOKALIKES)
    return cleaned.strip("\"' \t\r\n")


def _is_ignorable_line(line: str) -> bool:
    return not line or line.startswith(("#", "WEBVTT")) or "-->" in line


def _match_standard_turn(line: str) -> Optional[Tuple[str, str]]:
    m = _TURN_REGEX.match(line)
    if not m or re.fullmatch(r"\d{2}", m.group(2).strip()):
        return None
    label = normalize_speaker_token(m.group(1))
    if label.lower() in _DISALLOWED_LABELS:
        return None
    return (label, m.group(2).strip())


def extract_turn_speaker_inventory(transcript: str) -> List[Tuple[str, str]]:
    """Extracts ordered list of (speaker_label, utterance_text) from transcript."""
    turns: List[Tuple[str, str]] = []
    pending_speaker: Optional[str] = None
    for line in transcript.splitlines():
        line_clean = unicodedata.normalize("NFKC", _ZERO_WIDTH_RE.sub("", line.strip())).translate(_CYRILLIC_LOOKALIKES)
        if _is_ignorable_line(line_clean):
            continue
        vm = _VOICE_TAG_RE.search(line_clean)
        if vm:
            pending_speaker = None
            turns.append((normalize_speaker_token(vm.group(1)), re.sub(r"<[^>]+>", "", vm.group(2)).strip()))
            continue
        hm = _TEAMS_HEADER_RE.match(line_clean)
        if hm:
            pending_speaker = normalize_speaker_token(hm.group(1))
            continue
        turn = _match_standard_turn(line_clean)
        if turn:
            pending_speaker = None
            turns.append(turn)
            continue
        if pending_speaker:
            turns.append((pending_speaker, line_clean))
            pending_speaker = None
    return turns


def _extract_attendee_names_from_raw(raw_att: Any) -> List[str]:
    if isinstance(raw_att, dict):
        return [
            str(v.get("name") if isinstance(v, dict) else v)
            for vals in raw_att.values()
            if isinstance(vals, list)
            for v in vals
            if v
        ]
    if not isinstance(raw_att, list):
        return []
    out: List[str] = []
    for a in raw_att:
        if isinstance(a, dict) and a.get("name"):
            out.append(str(a["name"]))
        elif isinstance(a, (tuple, list)) and a:
            out.append(str(a[0]))
        elif isinstance(a, str) and a.strip():
            out.append(a.strip())
    return out


def _compute_canonical_meeting_subject_digest(
    metadata: Dict[str, Any],
    turns: Optional[List[Tuple[str, str]]] = None,
) -> Optional[str]:
    m_name = metadata.get("meeting_name")
    m_date = metadata.get("date") or metadata.get("meeting_date")
    att_names = _extract_attendee_names_from_raw(metadata.get("roster") or metadata.get("attendees") or [])
    if not (m_name and m_date and att_names):
        return None
    subject_dict: Dict[str, Any] = {
        "meeting_name": str(m_name),
        "meeting_date": str(m_date),
        "project": str(metadata.get("project", "nea")),
        "attendees": sorted(att_names),
    }
    if turns and metadata.get("bind_transcript_turns_to_subject"):
        import hashlib
        subject_dict["turns_sha256"] = hashlib.sha256(
            "\n".join(f"{spk}:{txt}" for spk, txt in turns).encode("utf-8")
        ).hexdigest()
    return compute_alr_subject_digest(subject_dict)


def _extract_expected_subject_digest(
    metadata: Dict[str, Any],
    turns: Optional[List[Tuple[str, str]]] = None,
) -> Optional[str]:
    """Derive expected subject SHA-256 digest from metadata or canonical meeting identity."""
    if turns and metadata.get("bind_transcript_turns_to_subject"):
        recomputed = _compute_canonical_meeting_subject_digest(metadata, turns=turns)
        if recomputed:
            metadata["subject_sha256"] = recomputed
            metadata["context_sha256"] = recomputed
            return recomputed
    subj = metadata.get("subject_sha256") or metadata.get("context_sha256")
    if not subj and isinstance(metadata.get("collapse_approval_receipt"), dict):
        subj = metadata["collapse_approval_receipt"].get("subject_sha256")
    if not subj and isinstance(metadata.get("subject"), dict):
        subj = compute_alr_subject_digest(metadata["subject"])
    if not subj:
        subj = _compute_canonical_meeting_subject_digest(metadata, turns=turns)
        if subj:
            metadata["subject_sha256"] = subj
            metadata["context_sha256"] = subj
    return subj



def _resolve_ledger_path(
    metadata: Dict[str, Any],
    ledger_path: Optional[Union[Path, str]] = None,
) -> Path:
    if ledger_path:
        return Path(ledger_path).resolve()
    if metadata.get("hitl_ledger_path"):
        return Path(metadata["hitl_ledger_path"]).resolve()
    return _find_default_ledger_path().resolve()


def _is_subject_match(
    payload: Dict[str, Any],
    entry: Dict[str, Any],
    expected_subject: Optional[str],
    metadata: Dict[str, Any],
) -> bool:
    subj = payload.get("subject_sha256") or entry.get("subject_sha256")
    if not expected_subject or not subj:
        return False
    return str(subj) == str(expected_subject)


def _is_signature_valid(entry: Dict[str, Any], payload: Dict[str, Any]) -> bool:
    sig = entry.get("signature", "")
    pub_pem = entry.get("public_key_pem")
    if not pub_pem:
        try:
            _, gov_pub = get_governed_hitl_keypair()
            pub_pem = export_public_key_pem(gov_pub)
        except Exception:
            return False
    if not pub_pem or not sig:
        return False
    return verify_hitl_approval_signature(payload, sig, pub_pem)


def _is_entry_expired(payload: Dict[str, Any], now_dt: datetime, is_bound_subject: bool = False) -> bool:
    expires_at = payload.get("expires_at")
    if not expires_at:
        return False
    try:
        exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        return now_dt > exp_dt
    except (ValueError, TypeError):
        return True


def _is_valid_collapse_entry(
    entry: Dict[str, Any],
    expected_subject: Optional[str],
    metadata: Dict[str, Any],
    now_dt: datetime,
) -> bool:
    payload = entry.get("payload", {})
    if payload.get("decision") != "APPROVE":
        return False
    cat = str(entry.get("category") or payload.get("category") or payload.get("hitl_type") or "").lower()
    if cat in ("speaker_hint", "hint", "quote_hint"):
        return False
    cases = [str(c).lower() for c in (payload.get("case_ids") or [])]
    if cases and all("hint" in c and "collapse" not in c and "sc-001" not in c for c in cases):
        return False
    op_info = payload.get("operator", {})
    op_id = (op_info.get("subject") if isinstance(op_info, dict) else None) or entry.get("operator_id") or ""
    from dual_pipeline.hitl_governance_core.scoped_autonomy import is_autonomous_alias
    if not op_id or is_autonomous_alias(op_id) or "engine" in str(op_id).lower() or op_id in ("mock_operator", "fake_operator"):
        return False
    is_subject_bound = _is_subject_match(payload, entry, expected_subject, metadata)
    if not is_subject_bound:
        return False
    if not _is_signature_valid(entry, payload):
        return False
    return not _is_entry_expired(
        payload, now_dt, is_bound_subject=is_subject_bound
    )


def _verify_collapse_approval_receipt(
    metadata: Dict[str, Any],
    ledger_path: Optional[Union[Path, str]] = None,
    turns: Optional[List[Tuple[str, str]]] = None,
) -> bool:
    """Verify if hitl_decision_ledger.json contains an authentic Ed25519-signed approval."""
    target_ledger = _resolve_ledger_path(metadata, ledger_path)
    if not target_ledger.is_file():
        return False

    expected_subject = _extract_expected_subject_digest(metadata, turns=turns)
    approvals = find_signed_approvals(ledger_path=target_ledger)
    if not approvals:
        return False

    now_dt = datetime.now(timezone.utc)
    for entry in approvals:
        if _is_valid_collapse_entry(entry, expected_subject, metadata, now_dt):
            subj = entry.get("payload", {}).get("subject_sha256") or entry.get("subject_sha256")
            logger.info(
                "Speaker collapse check: Verified authentic Ed25519-signed operator approval "
                "in hitl_decision_ledger.json (entry: %s, subject: %s).",
                entry.get("entry_id") or entry.get("decision_id"),
                subj,
            )
            return True

    return False


verify_collapse_approval_in_ledger = _verify_collapse_approval_receipt


def _collect_turn_and_word_counts(turns: List[Tuple[str, str]]) -> Tuple[Dict[str, int], Dict[str, int]]:
    counts: Dict[str, int] = {}
    word_counts: Dict[str, int] = {}
    for spk, text in turns:
        clean_spk = spk.strip()
        counts[clean_spk] = counts.get(clean_spk, 0) + 1
        word_counts[clean_spk] = word_counts.get(clean_spk, 0) + len(text.split())
    return counts, word_counts


def _select_turns_for_collapse_check(transcript: str, metadata: Dict[str, Any], min_turns: int) -> List[Tuple[str, str]]:
    raw_source = metadata.get("pre_hint_raw_transcript") or metadata.get("raw_transcript")
    turns = extract_turn_speaker_inventory(raw_source) if raw_source else []
    if len(turns) < min_turns:
        candidate_turns = extract_turn_speaker_inventory(transcript)
        if len(candidate_turns) > len(turns):
            turns = candidate_turns
    return turns


def _flatten_roster(roster: Any) -> List[Any]:
    if isinstance(roster, list):
        return [r for r in roster if r]
    if isinstance(roster, dict):
        flat: List[Any] = []
        for vals in roster.values():
            if isinstance(vals, list):
                flat.extend(v for v in vals if v)
            elif vals:
                flat.append(vals)
        return flat
    return []


def _evaluate_dominance_skew(
    counts: Dict[str, int],
    word_counts: Dict[str, int],
    total_turns: int,
    max_share_bps: int,
    roster: List[Any],
    metadata: Dict[str, Any],
) -> Tuple[bool, str, int]:
    dominant_label, dominant_turns = max(counts.items(), key=lambda x: x[1])
    is_skewed = (10000 * dominant_turns) >= (max_share_bps * total_turns)
    total_words = sum(word_counts.values())
    if total_words >= 50:
        dom_word_spk, dom_words = max(word_counts.items(), key=lambda x: x[1])
        if (10000 * dom_words) >= (max_share_bps * total_words):
            is_skewed, dominant_label = True, dom_word_spk
            dominant_turns = counts.get(dom_word_spk, dominant_turns)
    if metadata and metadata.get("ch0_subdiarization_failed") and len(roster) > 2:
        is_skewed = True
    return is_skewed, dominant_label, dominant_turns


def check_speaker_skew_and_collapse(
    transcript: str, roster: Any, metadata: Dict[str, Any],
    min_turns: int = 10, max_share_bps: int = 8500, ledger_path: Optional[Union[Path, str]] = None,
) -> Optional[Dict[str, Any]]:
    """Checks for single-speaker dominance, per-attendee starvation, or third-person contradiction."""
    flat_roster = _flatten_roster(roster)
    if len(flat_roster) < 2:
        return None
    turns = _select_turns_for_collapse_check(transcript, metadata, min_turns)
    total_turns = len(turns)
    counts, word_counts = _collect_turn_and_word_counts(turns)
    total_words = sum(word_counts.values())
    if (total_turns < min_turns and not (total_turns >= 2 and total_words >= 150)) or not counts:
        return None

    is_skewed, dominant_label, dominant_turns = _evaluate_dominance_skew(
        counts, word_counts, total_turns, max_share_bps, flat_roster, metadata
    )
    from .conflict_detector import detect_starved_cluster_or_contradiction
    starvation_or_conflict = detect_starved_cluster_or_contradiction(turns, counts, flat_roster, metadata)
    if (not is_skewed and not starvation_or_conflict) or _verify_collapse_approval_receipt(metadata, ledger_path, turns=turns):
        return None

    expected_subj = _extract_expected_subject_digest(metadata, turns=turns) if isinstance(metadata, dict) else None
    if isinstance(metadata, dict):
        metadata["single_speaker_collapse"] = True
    dominant_ratio = dominant_turns / total_turns
    dominant_words = word_counts.get(dominant_label, 0)
    evidence: Dict[str, Any] = {
        "attendee_count": len(flat_roster), "total_turns": total_turns,
        "dominant_label": dominant_label, "dominant_label_turns": dominant_turns,
        "dominant_label_words": dominant_words, "total_words": total_words,
        "dominant_word_ratio": round(dominant_words / total_words, 4) if total_words > 0 else 0.0,
        "max_share_bps": max_share_bps, "dominant_ratio": round(dominant_ratio, 4),
        "speaker_counts": counts, "word_counts": word_counts,
    }
    if expected_subj:
        evidence["subject_sha256"] = expected_subj
    unmapped_list, reason_suffix = [dominant_label], ""
    if starvation_or_conflict:
        conflict_msg, unmapped_list, extra_ev = starvation_or_conflict
        evidence.update(extra_ev)
        reason_suffix = f" ({conflict_msg})"

    raise HITLRequiredError(
        f"PRE-GATE 1 ATTRIBUTION HALT: Single-speaker collapse detected{reason_suffix}. "
        f"Dominant speaker '{dominant_label}' accounts for {dominant_turns}/{total_turns} turns "
        f"({dominant_ratio:.1%} vs {max_share_bps / 100:.1f}%) across a {len(flat_roster)}-attendee roster. "
        f"Interactive operator attribution required.",
        unmapped_speakers=unmapped_list, hitl_type="single_speaker_collapse", evidence=evidence,
    )
