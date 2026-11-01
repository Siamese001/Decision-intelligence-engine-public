"""
dual_pipeline/speaker_attribution_core/candidate_scorer.py

Linguistic and contextual candidate attribution scoring engine.
Fuses:
  1. Operator quote hints (speaker_hints.json) -> +0.45 boost
  2. Self-attribution cues (SELF_ATTRIBUTION_PATTERNS) -> +0.40 boost
  3. Addressee handoff cues (ADDRESSEE_PATTERNS) -> +0.35 boost
  4. Role linguistic alignment (_ROLE_PATTERNS) -> +0.25 boost
  5. Bijective deduction for final remaining unassigned attendee
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .audio_uncertainty_detector import (
    _ROLE_KEYWORDS,
    _ROLE_PATTERNS,
    extract_roster_with_roles,
)
from .models import CandidateAttribution
from .perspective_scorer import (
    extract_speaker_wpm,
    score_candidate_perspectives,
)

logger = logging.getLogger("dual_pipeline.speaker_attribution_core.candidate_scorer")

SELF_ATTRIBUTION_PATTERNS: List[re.Pattern] = [
    re.compile(r"\bthis is ([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", re.IGNORECASE),
    re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?) speaking\b", re.IGNORECASE),
    re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?) here\b", re.IGNORECASE),
    re.compile(r"\bmy name is ([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", re.IGNORECASE),
    re.compile(r"\bspeaking as ([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", re.IGNORECASE),
]

ADDRESSEE_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?),\s*(?:can you|do you|what do you|could you|would you)\b", re.IGNORECASE),
    re.compile(r"\bthanks(?:,\s*|\s+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", re.IGNORECASE),
    re.compile(r"\bthank you(?:,\s*|\s+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", re.IGNORECASE),
    re.compile(r"\bover to you(?:,\s*|\s+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", re.IGNORECASE),
]

from dual_pipeline.speaker_attribution_core.onboarding_tenure import (
    HISTORICAL_ENGAGEMENT_PATTERNS as _HISTORICAL_ENGAGEMENT_PATTERNS,
    detect_onboarding_historical_conflict,
)
_detect_onboarding_historical_conflict = detect_onboarding_historical_conflict

_ORDERED_TURN_REGEX = re.compile(
    r"(?m)^(?:\*\*|\#\#\s*|\#\s*)?(?:\[[^\]]+\](?:\*\*)?\s*)?(?:\*\*)?\[?([A-Za-z0-9_\s\.\-\(\)/]+?)\]?(?:\*\*)?(?:\s*\[[^\]]+\])?(?::\*\*|\*\*:|:)\s*(.+)$"
)

_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "do", "does", "for",
    "from", "get", "got", "had", "has", "have", "he", "her", "here", "him", "his",
    "how", "i", "if", "in", "into", "is", "it", "its", "just", "like", "me", "my",
    "no", "not", "now", "of", "ok", "okay", "on", "or", "our", "out", "right",
    "so", "some", "that", "the", "their", "them", "then", "there", "these", "they",
    "this", "to", "up", "us", "was", "we", "were", "what", "when", "where", "which",
    "who", "why", "will", "with", "would", "yeah", "yes", "you", "your",
})


_CONTRACTION_MAP = (
    (re.compile(r"\bwon['’]t\b"), "will not"),
    (re.compile(r"\bcan['’]t\b"), "cannot"),
    (re.compile(r"\bshan['’]t\b"), "shall not"),
    (re.compile(r"\bain['’]t\b"), "is not"),
    (re.compile(r"n['’]t\b"), " not"),
    (re.compile(r"\blet['’]s\b"), "let us"),
    (re.compile(r"\bi['’]m\b"), "i am"),
    (re.compile(r"['’]re\b"), " are"),
    (re.compile(r"\b(he|she|it|that|what|there|here|who|where|when|why|how)['’]s\b"), r"\1 is"),
    (re.compile(r"['’]ve\b"), " have"),
    (re.compile(r"['’]ll\b"), " will"),
    (re.compile(r"['’]d\b"), " would"),
)


def normalize_search_text(text: str) -> str:
    """Lowercases, expands English contractions, strips punctuation, and normalizes whitespace."""
    if not text:
        return ""
    lower = text.lower()
    for pat, repl in _CONTRACTION_MAP:
        lower = pat.sub(repl, lower)
    clean = re.sub(r"[^\w\s]", " ", lower)
    return " ".join(clean.split())


_MULTILINE_HEADER_RE = re.compile(
    r"^(?:#+\s*)?(?:\[[^\]]+\]\s*)?\*\*([^*:]+?):?\*\*:?\s*$"
)


def _match_speaker_header_label(label: str, spk_id: str, clean_id: str) -> bool:
    lbl_clean = re.sub(r"\s*\([^)]*\)", "", label.strip()).strip().lower()
    return lbl_clean in {clean_id.lower(), f"speaker {clean_id.lower()}", f"speaker_{clean_id.lower()}", spk_id.strip().lower()}


def _collect_multiline_turn_body(lines: List[str], start_idx: int) -> Tuple[str, int]:
    body: List[str] = []
    idx = start_idx
    while idx < len(lines):
        nxt = lines[idx].strip()
        if not nxt or nxt.startswith("#") or _ORDERED_TURN_REGEX.match(nxt):
            break
        body.append(nxt)
        idx += 1
    return " ".join(body), idx


def extract_speaker_turns(spk_id: str, transcript: str) -> List[str]:
    """Extracts all dialogue lines spoken by a specific speaker ID (single-line and multi-line Output 01)."""
    clean_id = spk_id.replace("Speaker ", "").strip()
    id_escaped = re.escape(clean_id)
    full_escaped = re.escape(str(spk_id).strip())
    time_prefix = r"(?:(?:\*\*|#+\s*)?\[\d{1,2}:\d{2}(?::\d{2})?(?:\s*(?:-|–|to)\s*\d{1,2}:\d{2}(?::\d{2})?)?\](?:\*\*)?\s*)?"
    time_suffix = r"(?:\s*\[\d{1,2}:\d{2}(?::\d{2})?\])?"
    pats = [
        re.compile(rf"^{time_prefix}(?:\*\*Speaker\s+{id_escaped}:?\*\*|\bSpeaker\s+{id_escaped}\b){time_suffix}(?::\*\*|\*\*:|:)?\s*(.+)$", re.IGNORECASE),
        re.compile(rf"^{time_prefix}(?:\*\*{full_escaped}:?\*\*|\b{full_escaped}\b){time_suffix}(?::\*\*|\*\*:|:)?\s*(.+)$", re.IGNORECASE),
    ]
    turns: List[str] = []
    lines = transcript.split("\n")
    idx = 0
    while idx < len(lines):
        line_s = lines[idx].strip()
        idx += 1
        if not line_s or line_s.startswith("# ---") or line_s.startswith("---") or line_s.startswith("|"):
            continue
        hdr_m = _MULTILINE_HEADER_RE.match(line_s)
        if hdr_m and _match_speaker_header_label(hdr_m.group(1), spk_id, clean_id):
            body_text, idx = _collect_multiline_turn_body(lines, idx)
            if body_text:
                turns.append(body_text)
            continue
        m = next((p.match(line_s) for p in pats if p.match(line_s)), None)
        if m:
            turns.append(m.group(1).strip())
    return turns


def extract_ordered_dialogue_turns(transcript: str) -> List[Tuple[str, str]]:
    """Extracts ordered list of (speaker_label, utterance_text) turns (single-line and multi-line)."""
    turns: List[Tuple[str, str]] = []
    lines = transcript.splitlines()
    idx = 0
    while idx < len(lines):
        line_clean = lines[idx].strip()
        idx += 1
        if not line_clean or line_clean.startswith("---") or line_clean.startswith("|") or line_clean.startswith("- "):
            continue
        hdr_m = _MULTILINE_HEADER_RE.match(line_clean)
        if hdr_m:
            body_text, idx = _collect_multiline_turn_body(lines, idx)
            if body_text:
                turns.append((hdr_m.group(1).strip(), body_text))
            continue
        if line_clean.startswith("#") and not re.match(r"^#+\s*\[\d", line_clean):
            continue
        m = _ORDERED_TURN_REGEX.match(line_clean)
        if m:
            turns.append((m.group(1).strip(), m.group(2).strip()))
    return turns


def _append_unique_dict_hints(target: List[Dict[str, Any]], items: Any) -> None:
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item not in target:
                target.append(item)


def _collect_hint_candidate_paths(metadata: Dict[str, Any]) -> List[Path]:
    candidates: List[Path] = []
    for key in ("speaker_hints_path", "cache_dir", "transcript_cache_dir", "input_dir", "output_dir", "input_path", "container_dir"):
        val = metadata.get(key)
        if not val:
            continue
        p = Path(val)
        if p.is_file():
            if p.name in ("speaker_hints.json", "speaker_hints_queue.json"):
                candidates.append(p)
            else:
                candidates.extend([p.parent / "speaker_hints.json", p.parent / "speaker_hints_queue.json"])
        elif p.is_dir():
            candidates.extend([p / "speaker_hints.json", p / "speaker_hints_queue.json"])
    return candidates


def _parse_hint_file(path: Path, all_hints: List[Dict[str, Any]]) -> None:
    if not (path.exists() and path.is_file()):
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            _append_unique_dict_hints(all_hints, data)
        elif isinstance(data, dict):
            raw_items = data.get("hints") or data.get("queue")
            if isinstance(raw_items, list):
                _append_unique_dict_hints(all_hints, raw_items)
            else:
                all_hints.extend({"quote": k, "speaker_name": v} for k, v in data.items())
    except Exception as e:
        logger.debug("Failed to parse speaker hints from %s: %s", path, e)


def load_speaker_hints(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Loads operator speaker hints from metadata or co-located speaker_hints.json and speaker_hints_queue.json."""
    all_hints: List[Dict[str, Any]] = []
    _append_unique_dict_hints(all_hints, metadata.get("speaker_hints"))
    _append_unique_dict_hints(all_hints, metadata.get("turn_hints"))
    for path in _collect_hint_candidate_paths(metadata):
        _parse_hint_file(path, all_hints)
    return all_hints


def _read_first_diarization_track_in_dir(d: Path) -> Optional[List[Dict[str, Any]]]:
    for f in d.glob("*diarization_tracks.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception as e:
            logger.debug("Failed to read diarization tracks from %s: %s", f, e)
    return None


def load_diarization_tracks(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Loads diarization tracks from metadata or co-located *_diarization_tracks.json."""
    if isinstance(metadata.get("diarization_tracks"), list):
        return metadata["diarization_tracks"]
    for key in ("cache_dir", "transcript_cache_dir", "input_dir", "output_dir", "input_path", "container_dir"):
        val = metadata.get(key)
        if not val:
            continue
        p = Path(val)
        search_dir = p if p.is_dir() else (p.parent if p.is_file() else None)
        if search_dir:
            tracks = _read_first_diarization_track_in_dir(search_dir)
            if tracks is not None:
                return tracks
    return []


def _detect_self_attribution(turns: List[str], first_name: str, full_name: str) -> Optional[str]:
    """Detects self-attribution cue in speaker turns."""
    f_lower, full_lower = first_name.lower(), full_name.lower()
    for turn in turns:
        for pat in SELF_ATTRIBUTION_PATTERNS:
            m = pat.search(turn)
            if m and (m.group(1).strip().lower() in (f_lower, full_lower) or f_lower in m.group(1).strip().lower()):
                return m.group(0).strip()
    return None


def _match_addressed_name(text: str, f_lower: str, full_lower: str) -> Optional[str]:
    for pat in ADDRESSEE_PATTERNS:
        m = pat.search(text)
        if m:
            addressed = m.group(1).strip().lower()
            if addressed in (f_lower, full_lower) or f_lower in addressed:
                return m.group(0).strip()
    return None


def _detect_addressee_handoff(
    ordered_turns: List[Tuple[str, str]], spk_id: str, first_name: str, full_name: str,
) -> Optional[str]:
    """Detects if preceding speaker addressed this candidate immediately before spk_id spoke."""
    clean_target = spk_id.replace("Speaker ", "").strip().lower()
    f_lower, full_lower = first_name.lower(), full_name.lower()
    for idx in range(1, len(ordered_turns)):
        curr_label, _ = ordered_turns[idx]
        prev_label, prev_text = ordered_turns[idx - 1]
        clean_curr = curr_label.replace("Speaker ", "").strip().lower()
        if clean_curr == clean_target and clean_curr != prev_label.replace("Speaker ", "").strip().lower():
            matched = _match_addressed_name(prev_text, f_lower, full_lower)
            if matched:
                return matched
    return None


def _detect_role_alignment(
    full_text: str,
    attendee_role: str,
) -> Optional[Tuple[str, str]]:
    """Detects role linguistic alignment between attendee role and spoken phrases."""
    if not attendee_role:
        return None
    role_norm = attendee_role.lower()
    matched_cat: Optional[str] = None
    for cat, kws in _ROLE_KEYWORDS.items():
        if any(kw in role_norm for kw in kws):
            matched_cat = cat
            break
    if not matched_cat or matched_cat not in _ROLE_PATTERNS:
        return None

    pat = _ROLE_PATTERNS[matched_cat]
    m = pat.search(full_text)
    if m:
        return matched_cat, m.group(0).strip()
    return None


def _hint_matches_attendee(h: Dict[str, Any], norm_name: str, norm_tokens: Set[str]) -> bool:
    h_name = str(h.get("speaker_name") or h.get("speaker") or "").lower().strip()
    return bool(h_name and (h_name == norm_name or h_name in norm_tokens or norm_name in h_name or any(p in h_name.split() for p in norm_tokens)))


def _build_speaker_variants(spk_id: str) -> Set[str]:
    spk_clean = spk_id.replace("Speaker ", "").strip().lower()
    spk_clean_s = re.sub(r"[\*\[\]]+", "", spk_clean).strip()
    spk_variants = {spk_id.lower().strip(), f"speaker {spk_clean}", spk_clean, f"speaker {spk_clean_s}", spk_clean_s}
    if spk_clean_s.isdigit():
        num = int(spk_clean_s)
        spk_variants.update({f"speaker_{num - 1:02d}", f"speaker_{num:02d}", str(num - 1)})
        if 1 <= num <= 26:
            char = chr(ord('a') + num - 1)
            spk_variants.update({char, f"voice {char}", f"voice_{char}"})
    elif len(spk_clean_s) == 1 and spk_clean_s.isalpha():
        char = spk_clean_s.lower()
        num = ord(char) - ord('a') + 1
        spk_variants.update({str(num), f"speaker {num}", f"speaker_{num - 1:02d}", f"voice {char}", f"voice_{char}"})
    return spk_variants


def _quote_matches_normalized_turns(norm_q: str, norm_turns: List[str]) -> bool:
    if not norm_q:
        return False
    q_tokens = set(norm_q.split())
    content_tokens = [t for t in q_tokens if t not in _STOPWORDS]
    for nt in norm_turns:
        nt_tokens = set(nt.split())
        if (norm_q in nt_tokens if len(q_tokens) == 1 else norm_q in nt):
            return True
        if len(content_tokens) >= 2 and len(q_tokens) >= 3 and (len(q_tokens.intersection(nt_tokens)) / len(q_tokens)) >= 0.65:
            return True
        if len(q_tokens) >= 2 and len(content_tokens) >= 1 and q_tokens.issubset(nt_tokens):
            return True
    return False


def _bind_hint_to_speaker(h: Dict[str, Any], spk_id: str, quote: str) -> str:
    h["bound_speaker_id"] = spk_id
    if h.get("status") == "QUEUED":
        h["status"] = "BOUND"
    return quote


def _check_quote_hint_match(
    hints: List[Dict[str, Any]], spk_id: str, name: str, full_text: str, turns: List[str],
) -> Optional[str]:
    """Matches operator quote hint for an attendee to individual turns of this speaker cluster."""
    norm_name = name.lower().strip()
    norm_tokens = set(norm_name.split())
    spk_variants = _build_speaker_variants(spk_id)
    norm_turns = [normalize_search_text(t) for t in turns if t]

    for h in hints:
        quote = str(h.get("quote") or h.get("quote_text") or "").strip()
        if not quote or not _hint_matches_attendee(h, norm_name, norm_tokens):
            continue
        bound_spk_clean = re.sub(r"[\*\[\]]+", "", str(h.get("bound_speaker_id") or "").strip().lower()).strip()
        if bound_spk_clean and bound_spk_clean in spk_variants:
            return quote
        hint_voice_clean = re.sub(r"[\*\[\]]+", "", str(h.get("voice_id") or "").strip().lower()).strip()
        if hint_voice_clean and hint_voice_clean in spk_variants:
            return _bind_hint_to_speaker(h, spk_id, quote)
        if _quote_matches_normalized_turns(normalize_search_text(quote), norm_turns):
            return _bind_hint_to_speaker(h, spk_id, quote)
    return None


def _lookup_cluster_embedding(
    cluster_embeddings: Dict[str, Any], label_map: Dict[str, Any], spk_id: str
) -> Optional[Any]:
    rev_map = {str(v).strip().lower(): str(k).strip() for k, v in label_map.items() if isinstance(k, str) and isinstance(v, str)}
    raw_id = rev_map.get(spk_id.lower().strip()) or rev_map.get(f"speaker {spk_id}".lower().strip())
    clean_num = spk_id.replace("Speaker ", "").strip()
    emb = (
        (cluster_embeddings.get(raw_id) if raw_id else None)
        or cluster_embeddings.get(spk_id)
        or cluster_embeddings.get(f"Speaker {spk_id}")
        or cluster_embeddings.get(clean_num)
    )
    if emb is None and clean_num.isdigit():
        idx = int(clean_num) - 1
        emb = cluster_embeddings.get(f"SPEAKER_{idx:02d}") or cluster_embeddings.get(f"speaker_{idx:02d}") or cluster_embeddings.get(f"SPEAKER_{idx}")
    return emb


def _parse_vp_match_entry(match: Any) -> Tuple[str, float]:
    if isinstance(match, (list, tuple)):
        return str(match[0]), float(match[1]) if len(match) > 1 else 0.85
    if isinstance(match, dict):
        return str(match.get("name", "")), float(match.get("score", 0.85))
    return str(match), 0.85


def _query_store_for_candidate(
    metadata: Dict[str, Any], emb: Any, candidate_name: str
) -> Optional[Tuple[float, str]]:
    try:
        from dual_pipeline.audio_ingestion_core.voiceprint_store import _normalize_vector, VoiceprintStore
        store = metadata.get("voiceprint_store") or VoiceprintStore(db_path=metadata.get("voiceprint_db_path"))
        proj = str(metadata.get("project", "GLOBAL"))
        vp = store.get_voiceprint(candidate_name, project=proj)
        if vp and vp.get("embedding"):
            norm_emb = _normalize_vector(emb)
            vp_emb = vp["embedding"]
            sim = round(sum(norm_emb[i] * vp_emb[i] for i in range(len(norm_emb))), 4)
            if sim >= 0.82:
                return sim, "voiceprint store"
        res = store.match_voiceprint(emb, project=proj, threshold=0.82)
        if res and res[0].lower().strip() == candidate_name.lower().strip():
            return res[1], "voiceprint store"
    except Exception as e:
        logger.debug("Voiceprint match check encountered non-fatal error: %s", e)
    return None


def _check_voiceprint_match(
    metadata: Dict[str, Any], spk_id: str, candidate_name: str,
) -> Optional[Tuple[float, str]]:
    """Checks for enrolled voiceprint match for this cluster and candidate name."""
    vp_matches = metadata.get("voiceprint_matches")
    if isinstance(vp_matches, dict):
        match = vp_matches.get(spk_id) or vp_matches.get(f"Speaker {spk_id}") or vp_matches.get(spk_id.replace("Speaker ", ""))
        if match:
            matched_name, score = _parse_vp_match_entry(match)
            if matched_name.lower().strip() == candidate_name.lower().strip():
                return score, "enrolled voiceprint"
    cluster_embeddings = metadata.get("cluster_embeddings") or metadata.get("cluster_centroids")
    if isinstance(cluster_embeddings, dict):
        label_map = metadata.get("cluster_label_map") or metadata.get("acoustic_speaker_map") or {}
        emb = _lookup_cluster_embedding(cluster_embeddings, label_map, spk_id)
        if emb:
            return _query_store_for_candidate(metadata, emb, candidate_name)
    return None


def _find_complementary_matched_candidates(
    candidates: List[CandidateAttribution], other_spks: Set[str],
    transcript: str, hints: List[Dict[str, Any]],
) -> Set[str]:
    matched_other: Set[str] = set()
    for c in candidates:
        for o_spk in other_spks:
            o_turns = extract_speaker_turns(o_spk, transcript)
            if o_turns and _check_quote_hint_match(hints, o_spk, c.attendee_name, "", o_turns):
                matched_other.add(c.attendee_name)
    return matched_other


def _apply_complementary_elimination(
    candidates: List[CandidateAttribution], spk_id: str, transcript: str,
    ordered_turns: List[Tuple[str, str]], hints: List[Dict[str, Any]],
) -> None:
    if len(candidates) != 2 or not hints or any("Operator quote hint matched" in c.evidence_rationale for c in candidates):
        return
    spk_clean = spk_id.replace("Speaker ", "").strip().lower()
    spk_variants = {spk_id.lower().strip(), f"speaker {spk_clean}", spk_clean}
    other_spks = {lbl for lbl, _ in ordered_turns if lbl.replace("Speaker ", "").strip().lower() not in spk_variants and lbl.lower().startswith("speaker")}
    target_others = other_spks or ({"Speaker 1", "Speaker 2"} - {spk_id, f"Speaker {spk_clean}"})
    matched_other = _find_complementary_matched_candidates(candidates, target_others, transcript, hints)
    if len(matched_other) != 1:
        return
    other_name = next(iter(matched_other))
    for c in candidates:
        if c.attendee_name not in matched_other:
            c.confidence = round(max(c.confidence, 0.88), 2)
            c.confidence_tier = "HIGH"
            c.evidence_rationale = f"1-to-1 complementary elimination ({other_name} matched hint on complementary cluster)"
        else:
            c.confidence = min(c.confidence, 0.50)
            c.confidence_tier = "MEDIUM" if c.confidence >= 0.50 else "LOW"


def _score_speaker_style(
    metadata: Dict[str, Any], candidate_name: str, spk_id: str,
    full_text: str, ordered_turns: List[Tuple[str, str]], turns: Optional[List[str]] = None,
) -> Tuple[float, List[str]]:
    """Calculates style matching score and evidence points for candidate."""
    try:
        from dual_pipeline.speaker_memory import get_speaker_memory
        mem = metadata.get("speaker_memory") or get_speaker_memory()
        prev_speaker = None
        norm_spk = spk_id.replace("Speaker ", "").strip().lower()
        for idx, (turn_spk, _) in enumerate(ordered_turns):
            if turn_spk.replace("Speaker ", "").strip().lower() == norm_spk and idx > 0:
                prev_speaker = ordered_turns[idx - 1][0]
                break
        res = mem.match_speaker_style_score(candidate_name, full_text, prev_speaker=prev_speaker, turns=turns)
        points: List[str] = []
        if res.get("opener", 0) > 0:
            points.append(f"Learned opener phrase match (+{res['opener']})")
        if res.get("vocab", 0) > 0:
            points.append(f"Learned vocabulary alignment (+{res['vocab']})")
        if res.get("turn_len", 0) > 0:
            points.append(f"Learned speaking pace/turn length match (+{res['turn_len']})")
        if res.get("interlocutor", 0) > 0:
            points.append(f"Learned conversation partner pattern with {prev_speaker} (+{res['interlocutor']})")
        total_sc = float(res.get("total", 0.0))
        if topic_sc := mem.match_speaker_score(candidate_name, full_text):
            sc = min(0.35, topic_sc)
            points.append(f"Learned domain topic alignment (+{sc:.2f})")
            total_sc += sc
        return total_sc, points
    except Exception as e:
        logger.debug("Speaker style matching check encountered non-fatal error: %s", e)
        return 0.0, []


def calibrate_scorer_weights_from_ledger(entries: List[Dict[str, Any]]) -> Dict[str, float]:
    """Calibrates candidate scorer feature multipliers from HITL AFFIRMED/OVERRIDDEN ledger records."""
    weights = {"hint": 0.45, "voiceprint": 0.50, "self_attr": 0.40, "handoff": 0.35, "role": 0.25}
    for rec in entries or []:
        outcome = str(rec.get("outcome_type") or "").upper()
        rat = str(rec.get("rationale") or "").lower()
        delta = 0.02 if outcome == "AFFIRMED" else (-0.03 if outcome == "OVERRIDDEN" else 0.0)
        if delta and "voiceprint" in rat:
            weights["voiceprint"] = round(max(0.25, min(0.65, weights["voiceprint"] + delta)), 3)
        if delta and "role linguistic" in rat:
            weights["role"] = round(max(0.10, min(0.45, weights["role"] + delta)), 3)
    return weights


def _score_single_candidate(
    name: str, org: str, role: Optional[str], cluster_label: str, spk_id: str,
    full_text: str, full_text_lower: str, turns: List[str], ordered_turns: List[Tuple[str, str]],
    hints: List[Dict[str, Any]], metadata: Dict[str, Any], wpm: float = 0.0,
) -> CandidateAttribution:
    if name.lower() in {cluster_label.lower(), f"speaker_{spk_id}".lower(), spk_id.lower()}:
        return CandidateAttribution(attendee_name=name, organization=org, confidence=0.99, confidence_tier="HIGH", evidence_rationale=f"Direct roster name matches {cluster_label}", role=role)
    w_map, first_name, score, evidence_points = (metadata.get("learned_scorer_weights") or {}), name.split()[0].lower(), 0.50, []
    if hint_quote := _check_quote_hint_match(hints, spk_id, name, full_text, turns):
        score += float(w_map.get("hint", 0.45))
        evidence_points.append(f"Operator quote hint matched in speaker_hints.json ('{hint_quote[:30]}...')")
    if vp_match := _check_voiceprint_match(metadata, spk_id, name):
        score += float(w_map.get("voiceprint", 0.50))
        evidence_points.append(f"Acoustic voiceprint match ({vp_match[1]}, cosine={vp_match[0]:.2f} >= 0.82)")
    if self_att := _detect_self_attribution(turns, first_name, name):
        score += float(w_map.get("self_attr", 0.40))
        evidence_points.append(f"Self-attribution cue ('{self_att}')")
    if handoff := _detect_addressee_handoff(ordered_turns, spk_id, first_name, name):
        score += float(w_map.get("handoff", 0.35))
        evidence_points.append(f"Addressee handoff cue from preceding turn ('{handoff}')")
    if re.search(rf"\b{re.escape(first_name)}\b", full_text_lower):
        score -= 0.30
        evidence_points.append(f"Addressed in second/third person ('{first_name}')")
    if conflict := _detect_onboarding_historical_conflict(name, full_text_lower, metadata):
        score -= 0.40
        evidence_points.append(f"Historical project tenure conflict for onboarding member ('{conflict}')")
    if role_res := _detect_role_alignment(full_text_lower, role or ""):
        score += float(w_map.get("role", 0.25))
        evidence_points.append(f"Role linguistic alignment ({role_res[0]}: '{role_res[1]}')")
    style_sc, style_pts = _score_speaker_style(metadata, name, spk_id, full_text, ordered_turns, turns=turns)
    persp_sc, persp_pts = score_candidate_perspectives(metadata, name, org, role, spk_id, full_text, full_text_lower, turns, ordered_turns, wpm)
    score += style_sc + persp_sc
    evidence_points.extend(style_pts + persp_pts)
    final_score = round(max(0.05, min(0.99, score)), 2)
    tier = "HIGH" if final_score >= 0.85 else ("MEDIUM" if final_score >= 0.50 else "LOW")
    return CandidateAttribution(attendee_name=name, organization=org, confidence=final_score, confidence_tier=tier, evidence_rationale="; ".join(evidence_points) if evidence_points else "Baseline roster prior", role=role)


def score_candidates_for_speaker(
    spk_id: str, transcript: str, roster: List[Tuple[str, str]],
    metadata: Dict[str, Any], assigned_attendees: Optional[Set[str]] = None, total_clusters: int = 1,
) -> List[CandidateAttribution]:
    """Scores roster candidates for a speaker cluster using linguistic, contextual, and hint evidence."""
    turns = extract_speaker_turns(spk_id, transcript)
    full_text = " ".join(turns)
    full_text_lower = full_text.lower()
    ordered_turns = extract_ordered_dialogue_turns(transcript)
    wpm = extract_speaker_wpm(spk_id, transcript, ordered_turns)
    hints = load_speaker_hints(metadata)
    roster_with_roles = {r["name"]: r.get("role", "") for r in extract_roster_with_roles(metadata)}

    candidates: List[CandidateAttribution] = []
    assigned = assigned_attendees or set()
    spk_clean = re.sub(r"[_\s]+", " ", spk_id.lower().strip())
    enforce_injective = total_clusters <= len(roster) and spk_clean not in ("unknown", "speaker unknown", "unattributed")
    eligible_roster = [(name, org) for name, org in roster if not (enforce_injective and name in assigned)]

    if enforce_injective and len(eligible_roster) == 1:
        sole_name, sole_org = eligible_roster[0]
        return [CandidateAttribution(
            attendee_name=sole_name, organization=sole_org, confidence=0.88,
            confidence_tier="HIGH", is_recommended=True,
            evidence_rationale="Bijective deduction: Only unassigned attendee remaining on roster",
            role=roster_with_roles.get(sole_name),
        )]

    cluster_label = f"Speaker {spk_id}"
    for name, org in eligible_roster:
        role = roster_with_roles.get(name)
        cand = _score_single_candidate(
            name=name, org=org, role=role, cluster_label=cluster_label,
            spk_id=spk_id, full_text=full_text, full_text_lower=full_text_lower,
            turns=turns, ordered_turns=ordered_turns, hints=hints, metadata=metadata,
            wpm=wpm,
        )
        candidates.append(cand)

    if enforce_injective:
        _apply_complementary_elimination(candidates, spk_id, transcript, ordered_turns, hints)
    candidates.sort(key=lambda c: c.confidence, reverse=True)
    if candidates:
        candidates[0].is_recommended = True
    return candidates
