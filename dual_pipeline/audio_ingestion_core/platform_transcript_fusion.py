"""Platform Per-Channel Diarization Parser and MLX Lexical Token Fusion Engine.

Fuses authoritative per-participant channel diarization (such as Google Meet WebRTC
per-channel transcripts) with MLX Whisper's domain-accurate lexical tokens while
preserving rapid back-and-forth interjections (<1.5s) as discrete speaker turns.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_GENERIC_SPEAKER_RE = re.compile(
    r"^(?:speaker[_\-\s]*\d*|voice[_\-\s]*[a-z0-9]+|unknown(?:\s+speaker)?|unattributed|attendee\s*\d*)$",
    re.IGNORECASE,
)

_TS_ONLY_RE = re.compile(r"^\s*\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s*$")
_MD_BLOCK_RE = re.compile(
    r"###\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*\n+\s*\*\*([^*:\n]+?):?\*\*:?\s*(.*?)(?=\n+###\s*\[|\Z)",
    re.DOTALL,
)
_MD_INLINE_RE = re.compile(
    r"(?:###\s*)?\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*\*\*([^*:\n]+?):?\*\*:?\s*(.+)"
)
_SPK_BRACKET_RE = re.compile(
    r"^\*\*([^*:\n]+?)\*\*\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\]:\s*(.+)$"
)
_PLAIN_SPK_COLON_RE = re.compile(
    r"^(?:\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s+)?([A-Z][A-Za-z0-9\.\-\s']{1,45}?):\s+(.+)$"
)

_DOMAIN_LEXICAL_OVERRIDES: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bFallon\b"), "PartnerAdvisory"),
    (re.compile(r"\b(?:NextA|Nexa)\b"), "Apex Global"),
    (re.compile(r"\bBungie\b"), "Bunge"),
    (re.compile(r"\bPuja\b"), "Pooja"),
    (re.compile(r"\bOracle\s+unifier\b", re.IGNORECASE), "Oracle Unifier"),
)


def _ts_to_sec(ts_str: str) -> float:
    parts = [float(p) for p in ts_str.strip().split(":")]
    if len(parts) == 3:
        return parts[0] * 3600.0 + parts[1] * 60.0 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60.0 + parts[1]
    return parts[0] if parts else 0.0


def _clean_speaker_name(raw_spk: str) -> str:
    cleaned = re.sub(r"[\*\[\]]+", "", (raw_spk or "")).strip().rstrip(":").strip()
    return re.sub(r"\s+", " ", cleaned)


def _is_valid_human_speaker(spk: str) -> bool:
    if not spk or len(spk) < 2 or len(spk) > 50:
        return False
    low = spk.lower()
    if low in ("date", "time", "participants", "attendees", "unknown", "speaker", "note", "agenda"):
        return False
    return not bool(_GENERIC_SPEAKER_RE.fullmatch(spk))


def _apply_domain_lexical_healing(text: str) -> str:
    healed = text
    for pat, repl in _DOMAIN_LEXICAL_OVERRIDES:
        healed = pat.sub(repl, healed)
    return healed


def _build_turn_words(
    text: str, start_sec: float, end_sec: float, speaker: str, turn_id: str
) -> List[Dict[str, Any]]:
    tokens = text.split()
    if not tokens:
        return []
    dur = max(0.2, end_sec - start_sec)
    step = dur / len(tokens)
    words: List[Dict[str, Any]] = []
    for idx, tok in enumerate(tokens):
        w_st = round(start_sec + idx * step, 4)
        w_en = round(w_st + step, 4)
        words.append({
            "word": tok,
            "text": tok,
            "start": w_st,
            "end": w_en,
            "speaker": speaker,
            "overlap": False,
            "turn_id": turn_id,
            "turn_boundary_locked": (idx == 0),
            "timing_provenance": "platform_per_channel",
        })
    return words


def _finalize_platform_turns(raw_items: List[Tuple[float, str, str]]) -> List[Dict[str, Any]]:
    turns: List[Dict[str, Any]] = []
    curr_clock = 0.0
    for idx, (ts_sec, spk, txt) in enumerate(raw_items):
        clean_txt = _apply_domain_lexical_healing(re.sub(r"\s+", " ", txt).strip())
        if not clean_txt or not _is_valid_human_speaker(spk):
            continue
        start_sec = max(ts_sec, curr_clock)
        word_count = max(1, len(clean_txt.split()))
        est_dur = max(0.6, min(12.0, word_count * 0.32))
        if idx + 1 < len(raw_items) and raw_items[idx + 1][0] > start_sec:
            end_sec = min(start_sec + est_dur, max(start_sec + 0.4, raw_items[idx + 1][0]))
        else:
            end_sec = round(start_sec + est_dur, 3)
        curr_clock = round(start_sec + 0.25, 3)
        turn_id = f"pturn_{len(turns) + 1:04d}"
        turns.append({
            "turn_id": turn_id,
            "speaker": spk,
            "raw_speaker": spk,
            "start": round(start_sec, 3),
            "end": round(max(start_sec + 0.3, end_sec), 3),
            "text": clean_txt,
            "timing_provenance": "platform_per_channel",
            "speaker_provenance": "platform_per_channel",
            "turn_boundary_locked": True,
            "words": _build_turn_words(clean_txt, round(start_sec, 3), round(max(start_sec + 0.3, end_sec), 3), spk, turn_id),
        })
    return turns


def _parse_line_oriented_turns(raw_text: str) -> List[Tuple[float, str, str]]:
    items: List[Tuple[float, str, str]] = []
    active_ts = 0.0
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("# ", "**Date:**", "**Participants:**", "---", "|")):
            continue
        m_ts = _TS_ONLY_RE.match(line)
        if m_ts:
            active_ts = _ts_to_sec(m_ts.group(1))
            continue
        m_inl = _MD_INLINE_RE.match(line)
        if m_inl:
            ts_s, spk, txt = m_inl.groups()
            active_ts = _ts_to_sec(ts_s)
            items.append((active_ts, _clean_speaker_name(spk), txt))
            continue
        m_sb = _SPK_BRACKET_RE.match(line)
        if m_sb:
            spk, ts_s, txt = m_sb.groups()
            active_ts = _ts_to_sec(ts_s)
            items.append((active_ts, _clean_speaker_name(spk), txt))
            continue
        m_plain = _PLAIN_SPK_COLON_RE.match(line)
        if m_plain:
            ts_opt, spk, txt = m_plain.groups()
            if ts_opt:
                active_ts = _ts_to_sec(ts_opt)
            items.append((active_ts, _clean_speaker_name(spk), txt))
    return items


def parse_platform_diarized_transcript(raw_text: str) -> List[Dict[str, Any]]:
    """Parses Google Meet / platform per-channel raw transcript into structured turns."""
    if not raw_text or not raw_text.strip():
        return []
    md_blocks = _MD_BLOCK_RE.findall(raw_text)
    if md_blocks:
        raw_items = [(_ts_to_sec(ts_s), _clean_speaker_name(spk), txt) for ts_s, spk, txt in md_blocks]
        return _finalize_platform_turns(raw_items)
    return _finalize_platform_turns(_parse_line_oriented_turns(raw_text))


def _norm_tokens(text: str) -> List[str]:
    return [t for t in re.sub(r"[^\w\s]", "", text.lower()).split() if t]


def _select_best_mlx_lexical_text(
    pturn: Dict[str, Any],
    mlx_segments: Sequence[Dict[str, Any]],
    max_time_drift_sec: float,
) -> Tuple[str, bool]:
    p_text = str(pturn.get("text", "")).strip()
    p_tokens = set(_norm_tokens(p_text))
    if not p_tokens or not mlx_segments:
        return p_text, False
    p_start = float(pturn.get("start", 0.0))
    p_end = float(pturn.get("end", p_start + 1.0))

    best_text = p_text
    best_score = 0.0
    fused = False
    for seg in mlx_segments:
        s_text = str(seg.get("text", "")).strip()
        s_tokens = set(_norm_tokens(s_text))
        if not s_tokens:
            continue
        s_start = float(seg.get("start", 0.0))
        s_end = float(seg.get("end", s_start + 1.0))
        time_gap = max(0.0, max(p_start, s_start) - min(p_end, s_end))
        if time_gap > max_time_drift_sec:
            continue
        inter = len(p_tokens.intersection(s_tokens))
        union = max(1, len(p_tokens.union(s_tokens)))
        jaccard = inter / union
        len_ratio = min(len(p_tokens), len(s_tokens)) / max(1, max(len(p_tokens), len(s_tokens)))
        if jaccard >= 0.60 and len_ratio >= 0.85 and jaccard > best_score:
            best_score = jaccard
            best_text = _apply_domain_lexical_healing(s_text)
            fused = True
    return best_text, fused


def fuse_mlx_segments_with_platform_diarization(
    mlx_segments: Sequence[Dict[str, Any]],
    platform_turns: Sequence[Dict[str, Any]],
    max_time_drift_sec: float = 15.0,
) -> List[Dict[str, Any]]:
    """Fuses MLX Whisper lexical segments onto authoritative platform per-channel turns."""
    if not platform_turns:
        return [dict(s) for s in mlx_segments]
    fused_turns: List[Dict[str, Any]] = []
    for idx, pturn in enumerate(platform_turns):
        spk = str(pturn.get("speaker") or "Unknown").strip()
        if not _is_valid_human_speaker(spk):
            continue
        st = float(pturn.get("start", 0.0))
        en = float(pturn.get("end", st + 0.8))
        turn_id = str(pturn.get("turn_id") or f"pturn_{idx + 1:04d}")
        healed_p_text = _apply_domain_lexical_healing(str(pturn.get("text", "")).strip())
        chosen_text, was_mlx_fused = _select_best_mlx_lexical_text(
            {**pturn, "text": healed_p_text}, mlx_segments, max_time_drift_sec
        )
        prov = "platform_per_channel_fused" if was_mlx_fused else "platform_per_channel"
        words = _build_turn_words(chosen_text, st, en, spk, turn_id)
        fused_turns.append({
            "turn_id": turn_id,
            "speaker": spk,
            "raw_speaker": spk,
            "start": round(st, 3),
            "end": round(en, 3),
            "text": chosen_text,
            "words": words,
            "overlap": False,
            "active_speakers": [spk],
            "timing_provenance": "platform_per_channel",
            "speaker_provenance": prov,
            "turn_boundary_locked": True,
        })
    return fused_turns


def load_platform_transcript_text(path_or_text: Optional[str]) -> str:
    """Loads platform raw transcript from text or file path."""
    if not path_or_text:
        return ""
    cand = str(path_or_text).strip()
    if "\n" not in cand and len(cand) < 1024:
        p = Path(cand)
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return ""
    return cand


def _compute_claimed_similarity(store: Any, claimed_spk: str, emb: Sequence[float], project: str) -> float:
    import math
    claimed_vp = store.get_voiceprint(claimed_spk, project=project)
    if not (claimed_vp and isinstance(claimed_vp.get("embedding"), list)):
        return 1.0
    c_vec = claimed_vp["embedding"]
    n1 = math.sqrt(sum(float(x) * float(x) for x in emb))
    n2 = math.sqrt(sum(float(y) * float(y) for y in c_vec))
    if n1 <= 1e-9 or n2 <= 1e-9:
        return 1.0
    return sum(float(a) * float(b) for a, b in zip(emb, c_vec)) / (n1 * n2)


def _apply_voiceprint_bleed_override(t_copy: Dict[str, Any], claimed_spk: str, matched_spk: str, score: float) -> None:
    t_copy["platform_claimed_speaker"] = claimed_spk
    t_copy["acoustic_platform_mismatch"] = True
    t_copy["speaker"] = matched_spk
    t_copy["speaker_provenance"] = "voiceprint_override_platform_bleed"
    t_copy["acoustic_confidence"] = round(score, 4)
    for w in t_copy.get("words") or []:
        if isinstance(w, dict):
            w["speaker"] = matched_spk


def _verify_single_platform_turn(
    t: Dict[str, Any], emb_map: Dict[str, Sequence[float]], store: Any,
    dim: int, project: str, mismatch_threshold: float,
) -> Dict[str, Any]:
    t_copy = dict(t)
    tid = str(t_copy.get("turn_id") or "")
    claimed_spk = str(t_copy.get("speaker") or "").strip()
    emb = t_copy.get("embedding") or emb_map.get(tid) or emb_map.get(claimed_spk)
    if not (isinstance(emb, (list, tuple)) and len(emb) == dim and claimed_spk):
        return t_copy
    claimed_sim = _compute_claimed_similarity(store, claimed_spk, emb, project)
    match = store.match_voiceprint(emb, project=project, threshold=0.82)
    if match and match[0].strip().lower() != claimed_spk.lower() and claimed_sim < mismatch_threshold and (match[1] - claimed_sim) >= 0.18:
        _apply_voiceprint_bleed_override(t_copy, claimed_spk, match[0], match[1])
    return t_copy


def verify_platform_turns_with_voiceprints(
    fused_turns: Sequence[Dict[str, Any]],
    turn_embeddings: Optional[Dict[str, Sequence[float]]] = None,
    voiceprint_store: Optional[Any] = None,
    project: str = "GLOBAL",
    mismatch_threshold: float = 0.58,
) -> List[Dict[str, Any]]:
    """Cross-verifies platform per-channel speaker tags against enrolled 256-D voiceprints."""
    if not fused_turns:
        return []
    emb_map = turn_embeddings or {}
    if not emb_map and not any(t.get("embedding") for t in fused_turns):
        return [dict(t) for t in fused_turns]
    try:
        from .voiceprint_store import EMBEDDING_DIM, VoiceprintStore
        store = voiceprint_store or VoiceprintStore()
    except Exception:
        return [dict(t) for t in fused_turns]
    return [_verify_single_platform_turn(t, emb_map, store, EMBEDDING_DIM, project, mismatch_threshold) for t in fused_turns]


def try_fuse_metadata_platform_transcript(
    sanitized_segments: Sequence[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]],
) -> Optional[List[Dict[str, Any]]]:
    """Fuses MLX segments with platform per-channel diarization when present in metadata."""
    if not isinstance(metadata, dict):
        return None
    raw_src = metadata.get("platform_transcript_text") or metadata.get("platform_transcript_path")
    if not raw_src:
        return None
    raw_text = load_platform_transcript_text(str(raw_src))
    platform_turns = parse_platform_diarized_transcript(raw_text)
    if not platform_turns:
        return None
    fused = fuse_mlx_segments_with_platform_diarization(sanitized_segments, platform_turns)
    if not fused:
        return None
    if metadata.get("turn_embeddings") or metadata.get("voiceprint_store_path"):
        from .voiceprint_store import VoiceprintStore
        vp_store = VoiceprintStore(db_path=metadata.get("voiceprint_store_path")) if metadata.get("voiceprint_store_path") else None
        fused = verify_platform_turns_with_voiceprints(
            fused,
            turn_embeddings=metadata.get("turn_embeddings"),
            voiceprint_store=vp_store,
            project=str(metadata.get("project") or "GLOBAL"),
        )
    metadata["diarization_method"] = "platform_per_channel_mlx_fusion"
    metadata["diarization_status"] = "DIARIZED_PLATFORM_PER_CHANNEL_RESOLVED"
    metadata["has_acoustic_diarization"] = True
    spk_map = metadata.setdefault("speaker_map", {})
    for turn in fused:
        spk = str(turn.get("speaker") or "").strip()
        if spk:
            spk_map.setdefault(spk, spk)
    return fused


_COMPANION_PATTERNS: Tuple[str, ...] = (
    "*google_meet*transcript*",
    "*platform_transcript*",
    "* - Transcript.pdf",
    "* - Transcript.txt",
    "* - Transcript.md",
)


def _expand_companion_dirs(search_dirs: Sequence[Path]) -> List[Path]:
    cand_dirs: List[Path] = []
    for s_dir in search_dirs:
        p_dir = Path(s_dir)
        if not p_dir.is_dir():
            continue
        cand_dirs.append(p_dir)
        cand_dirs.extend(p_dir / sub for sub in ("attachments", "referenced_artifacts") if (p_dir / sub).is_dir())
    return cand_dirs


def _collect_companion_candidate_files(search_dirs: Sequence[Path]) -> List[Path]:
    return [
        f_path
        for c_dir in _expand_companion_dirs(search_dirs)
        for pat in _COMPANION_PATTERNS
        for f_path in sorted(c_dir.glob(pat))
        if f_path.is_file() and not f_path.name.startswith("01_")
    ]


def hydrate_companion_platform_transcript(
    metadata: Dict[str, Any],
    search_dirs: Sequence[Path],
    hydrated: Optional[List[str]] = None,
) -> bool:
    """Auto-discovers and hydrates companion Google Meet / platform per-channel transcripts."""
    if metadata.get("platform_transcript_text") or metadata.get("platform_transcript_path"):
        return False
    for f_path in _collect_companion_candidate_files(search_dirs):
        raw_text = load_platform_transcript_text(str(f_path))
        turns = parse_platform_diarized_transcript(raw_text)
        if turns:
            metadata["platform_transcript_path"] = str(f_path)
            metadata["platform_transcript_text"] = raw_text
            speakers = list(dict.fromkeys(str(t["speaker"]) for t in turns if t.get("speaker")))
            metadata.setdefault("platform_speakers", speakers)
            if hydrated is not None and "platform_transcript" not in hydrated:
                hydrated.append("platform_transcript")
            return True
    return False


def extract_platform_per_channel_turns(
    meeting_dir: Path,
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]], Optional[Path]]:
    """Extracts verified speaker map and turns from a companion platform transcript in meeting_dir."""
    meta: Dict[str, Any] = {}
    if not hydrate_companion_platform_transcript(meta, [meeting_dir]):
        return {}, [], None
    raw_text = str(meta.get("platform_transcript_text") or "")
    p_path = Path(meta["platform_transcript_path"]) if meta.get("platform_transcript_path") else None
    parsed = parse_platform_diarized_transcript(raw_text)
    verified_map: Dict[str, Dict[str, Any]] = {}
    turns: List[Dict[str, Any]] = []
    for t in parsed:
        spk = str(t.get("speaker") or "").strip()
        if not _is_valid_human_speaker(spk):
            continue
        st = float(t.get("start", 0.0))
        en = float(t.get("end", st + 2.0))
        dur = max(2.0, en - st)
        verified_map.setdefault(
            spk,
            {"canonical_name": spk, "turns_count": 0, "status": "PLATFORM_PER_CHANNEL", "source": "platform_per_channel"},
        )["turns_count"] += 1
        turns.append({
            "speaker": spk,
            "start": st,
            "end": st + dur,
            "duration": dur,
            "text": str(t.get("text", "")),
            "is_overlap": False,
        })
    return verified_map, turns, p_path
