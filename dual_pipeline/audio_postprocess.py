"""
dual_pipeline/audio_postprocess.py

Decoupled ASR Post-Processing, Hallucination Filtering, and Vocabulary Seeding.
Provides:
1. Whisper initial_prompt generation from attendee roster, project context, and domain lexicon.
2. Anchored ASR hallucination filtering against silence artifacts, subtitle watermarks, and credit lines.
3. Repetition loop detection, stutter collapsing, and conditioning prompt echo prevention.
4. Segment-level telemetry gating (no_speech_prob, compression_ratio, logprob).
5. Immutable drop logging to asr_filter_log.jsonl.
"""
from __future__ import annotations

import datetime
import difflib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("dual_pipeline.audio_postprocess")

DEFAULT_DOMAIN_TERMS: Tuple[str, ...] = (
    "Apex Global Enterprises", "APEX", "NEE", "AWS", "Snowflake", "Databricks",
    "Terraform", "LAMS", "Unifier", "GIS", "Power BI", "SAP", "Forge",
    "Garbaria", "agrivoltaics", "SOW", "Jira", "Slack", "Teams",
)

# Full-utterance credit lines, subtitle watermarks, and media end-cards.
# Anchored with ^ and $ (plus optional punctuation) so genuine conversational speech
# containing words like 'subscribe' or 'portal' is never dropped.
ANCHORED_CREDIT_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"^(?:subtitles?|captions?|closed\s+captions?|closed\s+captioning)\s+(?:by|created\s+by|provided\s+by|sponsored\s+by).*?[\s.!?]*$", re.IGNORECASE),
    re.compile(r"^(?:transcribed|translated)\s+by.*?[\s.!?]*$", re.IGNORECASE),
    re.compile(r"^(?:closed\s+captioning|closed\s+captions)[\s.!?]*$", re.IGNORECASE),
    re.compile(r"^tyler\s+at\s+gpknives[\s.!?]*$", re.IGNORECASE),
    re.compile(r"^gpknives[\s.!?]*$", re.IGNORECASE),
    re.compile(r"^amara\.org[\s.!?]*$", re.IGNORECASE),
    re.compile(r"^(?:thank\s+you|thanks)\s+for\s+(?:watching|listening)[\s.!?]*$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?(?:like\s+and\s+)?subscribe(?:\s+to\s+(?:my|this|the|our)\s+channel)?[\s.!?]*$", re.IGNORECASE),
    re.compile(r"^(?:don't\s+forget\s+to\s+)?(?:like\s+and\s+)?subscribe[\s.!?]*$", re.IGNORECASE),
    re.compile(r"^like\s+and\s+subscribe[\s.!?]*$", re.IGNORECASE),
    re.compile(r"^see\s+you\s+(?:in\s+the\s+next\s+video|next\s+time)[\s.!?]*$", re.IGNORECASE),
    re.compile(r"^english\s+subtitles\s+provided\s+by.*?[\s.!?]*$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?leave\s+a\s+comment\s+below.*?[\s.!?]*$", re.IGNORECASE),
    re.compile(r".*?\bglad\s+to\s+have\s+you\s+on\s+the\s+show\b.*?[\s.!?]*$", re.IGNORECASE),
    re.compile(r".*?\bthis\s+is\s+a\s+hero\s+of\s+my\s+life\b.*?[\s.!?]*$", re.IGNORECASE),
)

# Standalone formulaic silence artifacts emitted on dead or low-RMS audio.
ANCHORED_SILENCE_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"^(?:thank\s+you|thanks|the\s+end|you|bye|goodbye|you're\s+welcome|see\s+you\s+later)[\s.!?]*$", re.IGNORECASE),
)

# Non-conversational movie/subtitle endcard artifacts that are dropped even with VAD energy.
ANCHORED_NON_CONVERSATIONAL_SILENCE_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"^(?:the\s+end|you)[\s.!?]*$", re.IGNORECASE),
)

# Standalone bare URLs or naked domain names without conversational context.
STANDALONE_URL_PATTERN = re.compile(
    r"^(?:https?://\S+|www\.\S+|[a-zA-Z0-9_\-]+\.(?:com|org|net|io|gov|edu|tv|ai|co))[\s.!?]*$",
    re.IGNORECASE,
)

# Standalone acoustic vocalizations and bracketed/parenthesized tags.
NON_SPEECH_VOCALIZATIONS: frozenset[str] = frozenset({
    "sigh", "sighs", "sighing", "gasp", "gasps", "groan", "cough",
    "applause", "laughter", "cheering", "snicker", "music", "silence",
})

# Backward-compatibility alias for tests
ASR_HALLUCINATION_BLOCKLIST: Tuple[str, ...] = (
    "thank you for watching", "thanks for watching", "thank you for listening", "thanks for listening",
    "subtitles by", "transcription by", "captioning by", "the end.", "bye.", "goodbye.", "see you later.", "see you next time.", "you're welcome.",
)


def _clean_attendee_name(item: Any) -> Optional[str]:
    """Cleans a single attendee roster entry."""
    if isinstance(item, dict) and item.get("name"):
        return str(item["name"])
    if isinstance(item, (str, int)):
        clean = re.sub(r"\s*\(.*?\)", "", str(item)).strip()
        return clean or None
    return None


def _extract_attendees_component(metadata: Dict[str, Any]) -> Optional[str]:
    """Extracts cleaned attendee names from metadata."""
    attendees: List[str] = []
    roster = metadata.get("roster") or metadata.get("attendees")
    raw_list: List[Any] = []
    if isinstance(roster, list):
        raw_list = roster
    elif isinstance(roster, dict):
        for group in roster.values():
            if isinstance(group, list):
                raw_list.extend(group)

    for item in raw_list:
        name = _clean_attendee_name(item)
        if name:
            attendees.append(name)

    if attendees:
        return f"Meeting participants: {', '.join(attendees[:8])}."
    return None


def _extract_project_component(metadata: Dict[str, Any]) -> List[str]:
    """Extracts project and topic strings from metadata."""
    parts: List[str] = []
    proj = metadata.get("project_name") or metadata.get("project")
    meeting_name = metadata.get("meeting_name")
    if proj:
        parts.append(f"Project: {proj}.")
    if meeting_name and meeting_name != "Meeting_Session":
        clean_name = meeting_name.replace("_", " ")
        parts.append(f"Topic: {clean_name}.")
    return parts


PROJECT_DOMAIN_LEXICONS: Dict[str, Tuple[str, ...]] = {
    "apex_trans": ("ROCC", "Renewable Operations Control Center", "SCADA", "EMS", "Energy Management System", "Pano AI", "NERC-CIP", "CIP-005", "EDO", "Substation Onboarding", "DERMS", "ADMS", "PI Historian", "Apex Global Enterprises", "Apex Power & Grid", "APEX", "PartnerAdvisory", "Wind Turbines", "Solar Inverters"),
    "project_nexus": ("NEA", "Apex Global Analytics", "Nexus", "ROCC", "SCADA", "EMS", "NERC-CIP", "CIP-005", "PartnerAdvisory"),
    "project_lens": ("LENS", "Single Turbine", "Turbine Fleet", "PartnerAdvisory", "Apex Global Enterprises", "AWS", "BigQuery"),
    "project_beacon": ("Project Beacon", "Sherpa", "Data Mesh", "Catalog", "Lakehouse", "BigQuery", "PartnerAdvisory"),
    "internal_consulting": ("PartnerAdvisory", "Florida Market", "Account Planning"),
    "project_atlas": ("APEX", "Apex Power & Grid", "Rewire", "Grid Modernization", "Substation", "AMI", "Smart Meter", "PartnerAdvisory"),
}


def _append_unique_terms(terms: List[str], new_items: Sequence[Any], limit: Optional[int] = None) -> None:
    items = list(new_items)[:limit] if limit is not None else list(new_items)
    for item in items:
        s = str(item)
        if s not in terms:
            terms.append(s)


def _extract_domain_keywords_component(metadata: Dict[str, Any]) -> str:
    """Extracts domain terminology keywords from metadata and project lexicons."""
    lexicon = metadata.get("lexicon_map") or metadata.get("domain_lexicon")
    terms: List[str] = list(DEFAULT_DOMAIN_TERMS[:10])
    proj = (metadata.get("project_name") or metadata.get("project") or "").lower()
    if proj in PROJECT_DOMAIN_LEXICONS:
        _append_unique_terms(terms, PROJECT_DOMAIN_LEXICONS[proj])
    if isinstance(lexicon, (dict, list)):
        _append_unique_terms(terms, list(lexicon.keys()) if isinstance(lexicon, dict) else lexicon, limit=8)
    learned_aliases = metadata.get("learned_aliases")
    if isinstance(learned_aliases, dict):
        _append_unique_terms(terms, list(learned_aliases.values()), limit=8)
    return f"Keywords: {', '.join(terms)}."


def build_initial_prompt(metadata: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Constructs an initial_prompt for Whisper decoder conditioning (capped to 800 chars)."""
    if not metadata:
        return None
    components: List[str] = []
    attendees_part = _extract_attendees_component(metadata)
    if attendees_part:
        components.append(attendees_part)
    components.extend(_extract_project_component(metadata))
    components.append(_extract_domain_keywords_component(metadata))
    full_prompt = " ".join(components).strip()
    return full_prompt[:800] if full_prompt else None


def log_asr_drop(
    log_path: Optional[Path | str],
    raw_text: str,
    reason: str,
    pattern: str = "",
    chunk_idx: Optional[int] = None,
    chunk_file: Optional[str] = None,
) -> None:
    """Logs dropped ASR artifact to asr_filter_log.jsonl with failure reason."""
    if not log_path:
        return
    p = Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "chunk_idx": chunk_idx,
        "chunk_file": chunk_file,
        "raw_text": raw_text,
        "reason": reason,
        "pattern": pattern,
    }
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


_PROMPT_ECHO_SIGNATURES: Tuple[str, ...] = (
    "domain terms lens single turbine",
    "domain terms apex_global energy",
    "meeting participants mandy",
    "keywords apex_global energy apex nee",
    "technical and domain vocabulary",
    "enterprise stakeholders discussing",
)


def _is_short_clause_echo(text: str, t_clean: str, p_clean: str, whisper_prompt: str) -> bool:
    if t_clean not in p_clean:
        return False
    if any(hdr in t_clean for hdr in ("domain terms", "keywords", "meeting participants", "project", "topic", "discussing")):
        return True
    if text.count(",") >= 2:
        return True
    for raw_clause in re.split(r"[.;]", whisper_prompt):
        c_clean = " ".join(re.sub(r"[^\w\s]", "", raw_clause).lower().strip().split())
        if c_clean and t_clean == c_clean:
            return True
    return False


def is_prompt_echo(text: str, whisper_prompt: Optional[str] = None) -> bool:
    """Detects when Whisper outputs conditioning prompt tokens verbatim on silence."""
    if not text:
        return False
    t_clean = " ".join(re.sub(r"[^\w\s]", "", text).lower().strip().split())
    if not t_clean:
        return False
    if any(sig in t_clean for sig in _PROMPT_ECHO_SIGNATURES):
        return True
    if t_clean.startswith(("domain terms ", "meeting participants ", "meeting context ", "keywords ", "project ", "topic ")):
        return True
    if " domain terms " in t_clean and ("discussing " in t_clean or "apex_global" in t_clean):
        return True
    if not whisper_prompt:
        return False
    p_clean = " ".join(re.sub(r"[^\w\s]", "", whisper_prompt).lower().strip().split())
    if not p_clean:
        return False
    if t_clean == p_clean:
        return True
    words = t_clean.split()
    if len(words) >= 6:
        return difflib.SequenceMatcher(None, t_clean, p_clean).ratio() >= 0.85 or t_clean in p_clean
    if 2 <= len(words) < 6:
        return _is_short_clause_echo(text, t_clean, p_clean, whisper_prompt)
    return False


def is_hallucinated_or_prompt_echo_turn(
    text: str, no_speech_prob: Optional[float] = None, whisper_prompt: Optional[str] = None
) -> bool:
    """Fast deterministic check for prompt echoes, silence hallucinations, or high no_speech_prob."""
    if no_speech_prob is not None and float(no_speech_prob) >= 0.60:
        return True
    cleaned = (text or "").strip()
    if not cleaned:
        return True
    if is_prompt_echo(cleaned, whisper_prompt):
        return True
    return any(pat.search(cleaned) for pat in ANCHORED_CREDIT_PATTERNS)


def _is_bracketed_artifact_or_vocalization(text: str) -> bool:
    """Detects isolated non-speech vocalizations and bracketed/parenthesized acoustic subtitle tags."""
    norm = re.sub(r"[^\w\s]", "", text).lower().strip()
    if norm in NON_SPEECH_VOCALIZATIONS:
        return True
    return bool(re.fullmatch(r"\[.*?\]|\(.*?\)", text.strip()))


def _word_matches_brand_branch(word: str) -> bool:
    """Detects branded word completions or brand domain patterns."""
    w_lower = word.lower()
    if w_lower.startswith("apex_global") and ("victory" in w_lower or "play" in w_lower):
        return True
    return "." in word and any(term in w_lower for term in ("apex_global", "apex", "partner_advisory"))


def _is_generative_prompt_branch(text: str, whisper_prompt: Optional[str]) -> bool:
    """Detects when Whisper branches off a prompt entity into an unprompted compound (e.g. Apex Global -> Apex GlobalPlay)."""
    if not whisper_prompt or not text:
        return False
    clean_text = re.sub(r"[^\w\s\.]", "", text).strip()
    words = clean_text.split()
    if len(words) > 4:
        return False
    text_lower = clean_text.lower()
    if (
        "apex_globalvictory" in text_lower
        or "apex_globalplay" in text_lower
        or "apex_global energy victory" in text_lower
    ):
        return True
    return any(_word_matches_brand_branch(w) for w in words)


def _count_pattern_repeats(words: List[str], pattern: List[str], start_idx: int, n: int) -> int:
    """Counts consecutive repetitions of an n-gram pattern."""
    repeats = 1
    idx = start_idx + n
    while idx + n <= len(words):
        next_chunk = [w.lower().strip(".,!?:;\"'") for w in words[idx : idx + n]]
        if next_chunk != pattern:
            break
        repeats += 1
        idx += n
    return repeats


def _check_n_gram_loop(words: List[str], n: int) -> bool:
    """Checks if a repeating n-gram exceeds loop threshold."""
    min_repeats = 4 if n == 1 else 3
    if len(words) < n * min_repeats:
        return False
    for i in range(len(words) - n * min_repeats + 1):
        pattern = [w.lower().strip(".,!?:;\"'") for w in words[i : i + n]]
        if not any(pattern):
            continue
        repeats = _count_pattern_repeats(words, pattern, i, n)
        if repeats >= min_repeats:
            span = repeats * n
            if span >= 6 or (span / len(words)) >= 0.35:
                return True
    return False


def has_runaway_loop(words: List[str]) -> bool:
    """Detects runaway autoregressive loops across 1-gram to 8-gram scales."""
    if len(words) < 5:
        return False
    return any(_check_n_gram_loop(words, n) for n in range(1, 9))


def _match_and_transfer_punct(kept_phrase: List[str], last_discarded: str) -> List[str]:
    """Transfers terminal punctuation (.?!) from discarded phrase to kept phrase."""
    if not (last_discarded and last_discarded[-1] in ".?!" and kept_phrase):
        return kept_phrase
    res = list(kept_phrase)
    last_word = res[-1]
    if last_word and last_word[-1] in ",;:":
        res[-1] = last_word[:-1] + last_discarded[-1]
    elif last_word and last_word[-1] not in ".?!":
        res[-1] = last_word + last_discarded[-1]
    return res


def _try_collapse_at_index(words: List[str], i: int) -> Tuple[Optional[List[str]], int]:
    """Attempts to match and collapse a duplicated phrase starting at index i."""
    for n in range(8, 1, -1):
        if i + 2 * n > len(words):
            continue
        pat1 = [w.lower().strip(".,!?:;\"'") for w in words[i : i + n]]
        pat2 = [w.lower().strip(".,!?:;\"'") for w in words[i + n : i + 2 * n]]
        if pat1 == pat2 and any(pat1):
            kept = _match_and_transfer_punct(words[i : i + n], words[i + 2 * n - 1])
            return kept, i + 2 * n
    return None, i + 1


def collapse_duplicate_phrases(words: List[str]) -> List[str]:
    """Collapses consecutive identical multi-word phrases (e.g. 'I think we, I think we')."""
    result: List[str] = []
    i = 0
    while i < len(words):
        collapsed, next_i = _try_collapse_at_index(words, i)
        if collapsed is not None:
            result.extend(collapsed)
        else:
            result.append(words[i])
        i = next_i
    return result


def suppress_repetition_loops(text: str) -> str:
    """Suppresses runaway loops or collapses smaller stutter phrases."""
    words = text.split()
    if len(words) < 5:
        return text
    if has_runaway_loop(words):
        logger.debug("Suppressed repetition loop: %s", text[:60])
        return ""
    return " ".join(collapse_duplicate_phrases(words))


def filter_segments_and_collect_valid(
    segments: List[Dict[str, Any]],
    decode_config: Optional[Any] = None,
    filter_log_path: Optional[Path | str] = None,
    chunk_idx: Optional[int] = None,
    chunk_file: Optional[str] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Evaluates Whisper segment telemetry and returns concatenated text and valid segments."""
    no_speech_thresh = getattr(decode_config, "no_speech_threshold", 0.60)
    comp_ratio_thresh = getattr(decode_config, "compression_ratio_threshold", 2.40)
    logprob_thresh = getattr(decode_config, "logprob_threshold", -1.0)

    valid_texts: List[str] = []
    valid_segments: List[Dict[str, Any]] = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        no_speech = seg.get("no_speech_prob", 0.0)
        avg_logprob = seg.get("avg_logprob", 0.0)
        comp_ratio = seg.get("compression_ratio", 1.0)

        if no_speech >= no_speech_thresh:
            log_asr_drop(
                filter_log_path, text, "HIGH_NO_SPEECH_PROB",
                pattern=f"no_speech_prob={no_speech:.2f}", chunk_idx=chunk_idx, chunk_file=chunk_file,
            )
            continue
        if comp_ratio > comp_ratio_thresh:
            log_asr_drop(
                filter_log_path, text, "HIGH_COMPRESSION_RATIO",
                pattern=f"comp_ratio={comp_ratio:.2f}", chunk_idx=chunk_idx, chunk_file=chunk_file,
            )
            continue
        if avg_logprob < logprob_thresh:
            log_asr_drop(
                filter_log_path, text, "LOW_LOGPROB",
                pattern=f"avg_logprob={avg_logprob:.2f}", chunk_idx=chunk_idx, chunk_file=chunk_file,
            )
            continue
        valid_texts.append(text)
        valid_segments.append(seg)
    return " ".join(valid_texts).strip(), valid_segments


def filter_segments_by_telemetry(
    segments: List[Dict[str, Any]],
    decode_config: Optional[Any] = None,
    filter_log_path: Optional[Path | str] = None,
    chunk_idx: Optional[int] = None,
    chunk_file: Optional[str] = None,
) -> str:
    """Evaluates Whisper segment telemetry (no_speech_prob, compression_ratio, avg_logprob)."""
    text, _ = filter_segments_and_collect_valid(
        segments, decode_config=decode_config, filter_log_path=filter_log_path,
        chunk_idx=chunk_idx, chunk_file=chunk_file,
    )
    return text


def _has_strong_vad_backing(vad_backed: bool, no_speech_prob: Optional[float], avg_logprob: Optional[float]) -> bool:
    if vad_backed:
        return True
    if no_speech_prob is None or float(no_speech_prob) >= 0.25:
        return False
    return avg_logprob is None or float(avg_logprob) > -0.65


def _matches_any_pattern(cleaned: str, patterns: Sequence[re.Pattern]) -> Optional[re.Pattern]:
    for pat in patterns:
        if pat.search(cleaned):
            return pat
    return None


def sanitize_transcript_text(
    text: str,
    whisper_prompt: Optional[str] = None,
    filter_log_path: Optional[Path | str] = None,
    chunk_idx: Optional[int] = None,
    chunk_file: Optional[str] = None,
    no_speech_prob: Optional[float] = None,
    avg_logprob: Optional[float] = None,
    vad_backed: bool = False,
) -> str:
    """Cleans transcript text: suppresses loops, collapses stutters, and filters hallucinations."""
    cleaned = text.strip()
    if len(cleaned) < 2:
        if cleaned:
            log_asr_drop(filter_log_path, cleaned, "TOO_SHORT", chunk_idx=chunk_idx, chunk_file=chunk_file)
        return ""
    if _is_bracketed_artifact_or_vocalization(cleaned):
        log_asr_drop(filter_log_path, cleaned, "NON_SPEECH_VOCALIZATION", pattern="bracketed_or_vocalization", chunk_idx=chunk_idx, chunk_file=chunk_file)
        return ""
    credit_pat = _matches_any_pattern(cleaned, ANCHORED_CREDIT_PATTERNS)
    if credit_pat:
        log_asr_drop(filter_log_path, cleaned, "CREDIT_HALLUCINATION", pattern=credit_pat.pattern, chunk_idx=chunk_idx, chunk_file=chunk_file)
        return ""
    silence_pats = ANCHORED_NON_CONVERSATIONAL_SILENCE_PATTERNS if _has_strong_vad_backing(vad_backed, no_speech_prob, avg_logprob) else ANCHORED_SILENCE_PATTERNS
    sil_pat = _matches_any_pattern(cleaned, silence_pats)
    if sil_pat:
        log_asr_drop(filter_log_path, cleaned, "SILENCE_HALLUCINATION", pattern=sil_pat.pattern, chunk_idx=chunk_idx, chunk_file=chunk_file)
        return ""
    if STANDALONE_URL_PATTERN.search(cleaned):
        log_asr_drop(filter_log_path, cleaned, "STANDALONE_URL", pattern=STANDALONE_URL_PATTERN.pattern, chunk_idx=chunk_idx, chunk_file=chunk_file)
        return ""
    if _is_generative_prompt_branch(cleaned, whisper_prompt):
        log_asr_drop(filter_log_path, cleaned, "PROMPT_BRANCH", chunk_idx=chunk_idx, chunk_file=chunk_file)
        return ""
    if is_prompt_echo(cleaned, whisper_prompt):
        log_asr_drop(filter_log_path, cleaned, "PROMPT_ECHO", chunk_idx=chunk_idx, chunk_file=chunk_file)
        return ""
    words = cleaned.split()
    if has_runaway_loop(words):
        log_asr_drop(filter_log_path, cleaned, "REPETITION_LOOP", pattern="runaway_loop", chunk_idx=chunk_idx, chunk_file=chunk_file)
        return ""
    res = " ".join(collapse_duplicate_phrases(words)).strip()
    if len(res) < 2:
        log_asr_drop(filter_log_path, cleaned, "COLLAPSED_EMPTY", chunk_idx=chunk_idx, chunk_file=chunk_file)
        return ""
    return res


from dual_pipeline.audio_ingestion_core.asr_deduplicator import deduplicate_consecutive_asr_segments


