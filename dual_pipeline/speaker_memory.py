"""
dual_pipeline/speaker_memory.py

Layer 2 Cross-Session Speaker & Terminology Memory Store.
Maintains persistent profiles of confirmed meeting attendees, voice/topic associations,
phonetic ASR aliases, and domain terminology across sessions.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from config.paths import get_speaker_memory_path

logger = logging.getLogger(__name__)


def resolve_canonical_speaker_memory_path(base_file: Optional[Path] = None) -> Path:
    """Resolves canonical speaker_memory.json path across git worktrees."""
    return get_speaker_memory_path(base_file)


DEFAULT_SPEAKER_MEMORY_PATH = resolve_canonical_speaker_memory_path()


@dataclass
class SpeakerProfile:
    name: str
    person_id: str
    organization: str = ""
    role: str = ""
    domain_topics: List[str] = field(default_factory=list)
    phonetic_aliases: List[str] = field(default_factory=list)
    key_phrases: List[str] = field(default_factory=list)
    opener_phrases: List[str] = field(default_factory=list)
    common_vocabulary: Dict[str, int] = field(default_factory=dict)
    avg_words_per_turn: float = 0.0
    avg_words_per_minute: float = 0.0
    frequent_interlocutors: Dict[str, int] = field(default_factory=dict)
    owned_tasks: List[str] = field(default_factory=list)
    call_opener_count: int = 0
    confirmed_turn_count: int = 0
    updated_at: str = ""


from .speaker_memory_core.bootstrap import BOOTSTRAP_PROFILES

_NON_HUMAN_SPEAKER_TOKENS = frozenset({
    "transcript", "artifact", "annotation", "meeting", "attendee", "attendees",
    "diarization", "status", "source", "sources", "table", "convention",
    "limitation", "limitations", "summary", "speaker", "unknown", "unattributed",
    "group", "label", "message", "concern", "partner", "partners", "pipeline",
    "domain", "domains", "title", "subtitle", "lock", "capture", "roster",
    "reference", "references", "action", "note", "notes", "client", "moderator",
    "participant", "participants", "enhance", "transform", "go-to-market",
    "engineer", "resources", "energy", "partner_advisory", "apex_global", "hyperscalers",
})

_ORTHO_ALIAS_MAP = {
    "stephan": "stefan", "bryan": "brian", "steven": "stephen",
    "kathryn": "catherine", "kristen": "kristin", "jon": "john",
}


_VOCAB_STOPWORDS = frozenset({
    "that", "this", "with", "from", "have", "were", "they", "what", "your", "their", "there",
    "about", "would", "could", "should", "just", "like", "will", "been", "some", "them", "then",
    "into", "yeah", "okay", "sure", "well", "good", "fine", "yep", "nope", "going", "think",
    "know", "want", "really", "actually", "something", "because", "doing", "things", "other",
    "where", "which", "right", "very", "even",
})


def clean_speaker_identity(raw_name: str) -> str:
    s = re.sub(r"\s*\([^)]*\)", "", str(raw_name or "")).strip()
    s = re.sub(r"^[\-\*\d\.\)\[\]\s]+", "", s).strip()
    s = re.sub(r"\s*\d{1,2}:\d{2}(?::\d{2})?\s*$", "", s).strip()
    s = re.sub(r"[\*\[\]:]+", "", s).strip()
    return " ".join(s.split())


def _canonical_speaker_key(name: str) -> str:
    cleaned = clean_speaker_identity(name).lower()
    return " ".join(_ORTHO_ALIAS_MAP.get(t, t) for t in cleaned.split() if t)


def is_valid_human_speaker_name(name: str) -> bool:
    cleaned = clean_speaker_identity(name)
    if not cleaned or "(" in str(name).strip()[:1]:
        return False
    tokens = cleaned.split()
    if not (1 <= len(tokens) <= 3):
        return False
    if any(any(ch.isdigit() for ch in t) for t in tokens):
        return False
    low_tokens = [t.lower().strip(".,!?:;\"'/-") for t in tokens]
    if any(not re.fullmatch(r"[a-z][a-z'\-]*\.?", lt) for lt in low_tokens if lt):
        return False
    if any(lt in _NON_HUMAN_SPEAKER_TOKENS for lt in low_tokens):
        return False
    return True


def _update_turn_pace(profile: SpeakerProfile, tokens: Sequence[str]) -> None:
    if tokens:
        prev_tot = profile.avg_words_per_turn * max(0, min(profile.confirmed_turn_count - 1, 50))
        denom = max(1, min(profile.confirmed_turn_count, 51))
        profile.avg_words_per_turn = round((prev_tot + len(tokens)) / denom, 1)


def _is_valid_opener_phrase(phrase: str) -> bool:
    p = phrase.strip().lower()
    if len(p) < 4 or len(p.split()) < 2 or any(ch.isdigit() for ch in p):
        return False
    return not (p.startswith("turn ") or " turn" in p)


def _sanitize_opener_phrases(phrases: Sequence[str]) -> List[str]:
    out: List[str] = []
    for p in phrases:
        raw_p = str(p or "").strip().lower()
        if any(ch.isdigit() for ch in raw_p):
            continue
        clean_p = re.sub(r"^[\*\[\]:\s]+", "", raw_p).strip()
        if _is_valid_opener_phrase(clean_p) and clean_p not in out and len(out) < 15:
            out.append(clean_p)
    return out


def _learn_opener_phrase(
    profile: SpeakerProfile, utterance: str, tokens: Sequence[str], allow_learn: bool = True
) -> None:
    if not allow_learn or len(tokens) < 2:
        return
    comma_parts = utterance.strip().lower().split(",", 1)
    opener = comma_parts[0].strip() if len(comma_parts) > 1 and len(comma_parts[0].split()) <= 3 else " ".join(tokens[:2]).lower()
    opener = re.sub(r"^[\*\[\]\d:\s]+", "", opener).strip()
    if _is_valid_opener_phrase(opener) and opener not in profile.opener_phrases:
        if len(profile.opener_phrases) < 15:
            profile.opener_phrases.append(opener)
        else:
            profile.opener_phrases[-1] = opener


def _learn_vocabulary(profile: SpeakerProfile, tokens: Sequence[str]) -> None:
    turn_added: List[str] = []
    for t in tokens:
        tl = t.lower()
        if len(tl) >= 4 and tl not in _VOCAB_STOPWORDS:
            profile.common_vocabulary[tl] = profile.common_vocabulary.get(tl, 0) + 1
            if tl not in turn_added:
                turn_added.append(tl)
    if len(profile.common_vocabulary) > 30:
        recent = turn_added[:10]
        keep_slots = max(0, 30 - len(recent))
        top_old = [
            (k, v) for k, v in sorted(profile.common_vocabulary.items(), key=lambda x: -x[1])
            if k not in recent
        ][:keep_slots]
        kept = dict(top_old)
        for tl in recent:
            kept[tl] = profile.common_vocabulary[tl]
        profile.common_vocabulary = kept


def _learn_interlocutor(profile: SpeakerProfile, name: str, interlocutor: Optional[str]) -> None:
    if interlocutor and interlocutor.strip():
        int_clean = interlocutor.strip()
        if int_clean.lower() != name.lower():
            profile.frequent_interlocutors[int_clean] = profile.frequent_interlocutors.get(int_clean, 0) + 1


def _learn_topics(profile: SpeakerProfile, topics: Optional[Sequence[str]]) -> None:
    if not topics:
        return
    existing = {x.lower() for x in profile.domain_topics}
    for t in topics:
        t_clean = t.strip()
        if t_clean and t_clean.lower() not in existing:
            profile.domain_topics.append(t_clean)
            existing.add(t_clean.lower())


def _learn_tasks(profile: SpeakerProfile, tasks: Optional[Sequence[str]]) -> None:
    if not tasks:
        return
    existing = {x.lower() for x in profile.owned_tasks}
    for t in tasks:
        t_clean = t.strip()
        if t_clean and t_clean.lower() not in existing:
            profile.owned_tasks.append(t_clean)
            existing.add(t_clean.lower())


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _safe_list(val: Any) -> List[str]:
    return [str(x) for x in val] if isinstance(val, list) else []


def _safe_dict(val: Any) -> Dict[str, Any]:
    return dict(val) if isinstance(val, dict) else {}


def _sanitize_wpm(val: Any) -> float:
    w = _safe_float(val, 0.0)
    return round(w, 1) if 50.0 <= w <= 280.0 else 0.0


def _parse_profile_dict(key: str, pdata: Dict[str, Any]) -> SpeakerProfile:
    norm_key = key.lower().strip()
    return SpeakerProfile(
        name=str(pdata.get("name") or key),
        person_id=str(pdata.get("person_id") or f"person:{norm_key}"),
        organization=str(pdata.get("organization") or ""),
        role=str(pdata.get("role") or ""),
        domain_topics=_safe_list(pdata.get("domain_topics")),
        phonetic_aliases=_safe_list(pdata.get("phonetic_aliases")),
        key_phrases=_safe_list(pdata.get("key_phrases")),
        opener_phrases=_sanitize_opener_phrases(_safe_list(pdata.get("opener_phrases"))),
        common_vocabulary=_safe_dict(pdata.get("common_vocabulary")),
        avg_words_per_turn=_safe_float(pdata.get("avg_words_per_turn", 0.0)),
        avg_words_per_minute=_sanitize_wpm(pdata.get("avg_words_per_minute", 0.0)),
        frequent_interlocutors=_safe_dict(pdata.get("frequent_interlocutors")),
        owned_tasks=_safe_list(pdata.get("owned_tasks")),
        call_opener_count=_safe_int(pdata.get("call_opener_count", 0)),
        confirmed_turn_count=max(0, _safe_int(pdata.get("confirmed_turn_count", 0))),
        updated_at=str(pdata.get("updated_at") or ""),
    )


def _merge_speaker_profiles(existing: SpeakerProfile, parsed: SpeakerProfile) -> None:
    existing.confirmed_turn_count += parsed.confirmed_turn_count
    existing.call_opener_count += parsed.call_opener_count
    if parsed.avg_words_per_minute > 0 and existing.avg_words_per_minute == 0:
        existing.avg_words_per_minute = parsed.avg_words_per_minute
    if parsed.avg_words_per_turn > 0 and existing.avg_words_per_turn == 0:
        existing.avg_words_per_turn = parsed.avg_words_per_turn
    existing.opener_phrases = _sanitize_opener_phrases([*existing.opener_phrases, *parsed.opener_phrases])
    for k_v, cnt in parsed.common_vocabulary.items():
        existing.common_vocabulary[k_v] = existing.common_vocabulary.get(k_v, 0) + cnt
    _learn_topics(existing, parsed.domain_topics)
    _learn_tasks(existing, parsed.owned_tasks)


def _load_raw_profiles_from_disk(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            file_profiles = data.get("profiles", {})
            if isinstance(file_profiles, dict):
                return {k.lower(): v for k, v in file_profiles.items() if isinstance(v, dict)}
    except Exception as e:
        logger.warning("Failed to load speaker memory from %s: %s", path, e)
    return {}


def _create_initial_profile(name: str, person_id: Optional[str], now_str: str) -> SpeakerProfile:
    norm_name = name.lower().strip()
    return SpeakerProfile(
        name=name,
        person_id=person_id or f"person:{norm_name.replace(' ', '_')}",
        confirmed_turn_count=1,
        updated_at=now_str,
    )


class SpeakerMemory:
    """Persistent cross-session memory store for speaker attribution and domain terminology."""

    def __init__(self, memory_path: Optional[Path] = None) -> None:
        self.memory_path = memory_path or DEFAULT_SPEAKER_MEMORY_PATH
        self._profiles: Dict[str, SpeakerProfile] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        raw_profiles = dict(BOOTSTRAP_PROFILES)
        raw_profiles.update(_load_raw_profiles_from_disk(self.memory_path))
        cleaned_map: Dict[str, SpeakerProfile] = {}
        is_custom_path = self.memory_path != DEFAULT_SPEAKER_MEMORY_PATH
        for key, pdata in raw_profiles.items():
            raw_name = str(pdata.get("name") or key) if isinstance(pdata, dict) else ""
            is_test_token = is_custom_path and bool(re.fullmatch(r"speaker_\d+", raw_name.strip().lower()))
            if not is_valid_human_speaker_name(raw_name) and not is_test_token:
                continue
            clean_name = raw_name.strip() if is_test_token else clean_speaker_identity(raw_name)
            norm_key = clean_name.lower()
            parsed = _parse_profile_dict(norm_key, {**pdata, "name": clean_name})
            if norm_key in cleaned_map:
                _merge_speaker_profiles(cleaned_map[norm_key], parsed)
            else:
                cleaned_map[norm_key] = parsed
        self._profiles = cleaned_map

    def get_profile(self, name_or_alias: str) -> Optional[SpeakerProfile]:
        clean_q = clean_speaker_identity(name_or_alias).lower()
        alias_q = _canonical_speaker_key(name_or_alias)
        if not clean_q:
            return None
        if clean_q in self._profiles:
            return self._profiles[clean_q]
        for p in self._profiles.values():
            p_low = p.name.lower()
            p_alias = _canonical_speaker_key(p.name)
            if clean_q == p_low or alias_q == p_alias or any(clean_q == a.lower() for a in p.phonetic_aliases):
                return p
            if " " not in clean_q and p_low.split()[0] == clean_q:
                return p
            if " " not in p_low and clean_q.split()[0] == p_low:
                return p
        return None

    def match_speaker_score(self, speaker_name: str, utterance: str) -> float:
        """Calculate a domain topic match score for a candidate speaker on an utterance."""
        profile = self.get_profile(speaker_name)
        if not profile:
            return 0.0
        score = 0.0
        utt_lower = utterance.lower()
        for topic in profile.domain_topics:
            if topic.lower() in utt_lower:
                score += 0.40
        for phrase in profile.key_phrases:
            if phrase.lower() in utt_lower:
                score += 0.25
        return score

    def match_speaker_style_score(
        self,
        speaker_name: str,
        utterance: str,
        prev_speaker: Optional[str] = None,
        turns: Optional[Sequence[str]] = None,
    ) -> Dict[str, float]:
        """Calculates style matching scores for candidate speaker across one or more turns."""
        profile = self.get_profile(speaker_name)
        if not profile:
            return {"opener": 0.0, "vocab": 0.0, "turn_len": 0.0, "interlocutor": 0.0, "total": 0.0}
        turn_list = [t.strip() for t in (turns or utterance.splitlines()) if t and t.strip()] or [utterance]
        words = [w.strip(".,!?:;\"'()[]{}").lower() for w in utterance.split() if w.strip(".,!?:;\"'()[]{}")]
        w_count = len(words)
        avg_w = w_count / max(1, len(turn_list))
        opener_sc = 0.15 if any(t.lower().startswith(op) for t in turn_list for op in profile.opener_phrases) else 0.0
        hits = sum(1 for w in words if w in profile.common_vocabulary) if (profile.common_vocabulary and w_count > 0) else 0
        vocab_sc = min(0.20, round((hits / max(1, w_count)) * 0.40, 3))
        ratio = (min(avg_w, profile.avg_words_per_turn) / max(avg_w, profile.avg_words_per_turn)) if (profile.avg_words_per_turn > 0 and avg_w > 0) else 0.0
        turn_sc = round(ratio * 0.10, 3) if ratio >= 0.5 else 0.0
        inter_sc = 0.15 if (prev_speaker and any(prev_speaker.strip().lower() == k.strip().lower() for k in profile.frequent_interlocutors)) else 0.0
        tot = round(opener_sc + vocab_sc + turn_sc + inter_sc, 3)
        return {"opener": opener_sc, "vocab": vocab_sc, "turn_len": turn_sc, "interlocutor": inter_sc, "total": tot}

    def match_task_ownership(self, speaker_name: str, utterance: str) -> Tuple[float, List[str]]:
        """Calculate task and deliverable ownership match score for candidate."""
        profile = self.get_profile(speaker_name)
        if not profile or not profile.owned_tasks:
            return 0.0, []
        utt_lower = utterance.lower()
        first_person_patterns = [
            r"\bi\s+(?:finished|completed|updated|built|created|resolved|fixed|delivered|own|lead|handled|pushed)\b",
            r"\bmy\s+(?:team|deliverable|action\s+item|task|workstream|ticket|scope|pr|branch)\b",
            r"\bon\s+my\s+(?:side|end)\b",
        ]
        has_first_person = any(re.search(pat, utt_lower) for pat in first_person_patterns)
        if not has_first_person:
            return 0.0, []
        for task in profile.owned_tasks:
            if task.lower() in utt_lower:
                return 0.25, [f"First-person task ownership claim ('{task}') (+0.25)"]
        return 0.0, []

    def record_attribution(
        self,
        name: str,
        utterance: str,
        topics: Optional[Sequence[str]] = None,
        person_id: Optional[str] = None,
        interlocutor: Optional[str] = None,
        owned_tasks: Optional[Sequence[str]] = None,
        persist: bool = True,
        is_call_opener: bool = False,
        observed_wpm: float = 0.0,
        is_speaker_first_turn: bool = True,
    ) -> SpeakerProfile:
        """Learn and persist speaker attribution, style patterns, and domain topics."""
        clean_name = clean_speaker_identity(name) or name.strip()
        with self._lock:
            profile = self.get_profile(clean_name)
            now_str = datetime.now(timezone.utc).isoformat()
            if profile is None:
                profile = _create_initial_profile(clean_name, person_id, now_str)
                self._profiles[clean_name.lower().strip()] = profile
            else:
                profile.confirmed_turn_count += 1
                profile.updated_at = now_str
            if is_call_opener:
                profile.call_opener_count += 1
            if observed_wpm >= 50.0:
                clamped_wpm = min(280.0, float(observed_wpm))
                profile.avg_words_per_minute = (
                    round(0.75 * profile.avg_words_per_minute + 0.25 * clamped_wpm, 1)
                    if profile.avg_words_per_minute >= 50.0
                    else round(clamped_wpm, 1)
                )

            tokens = [w.strip(".,!?:;\"'()[]{}") for w in utterance.split() if w.strip(".,!?:;\"'()[]{}")]
            _update_turn_pace(profile, tokens)
            _learn_opener_phrase(profile, utterance, tokens, allow_learn=(is_call_opener or is_speaker_first_turn))
            _learn_vocabulary(profile, tokens)
            _learn_interlocutor(profile, clean_name, clean_speaker_identity(interlocutor) if interlocutor else None)

            _learn_topics(profile, topics)
            _learn_tasks(profile, owned_tasks)

        if persist:
            self.save()
        return profile

    def learn_from_resolved_transcript(
        self,
        resolved_transcript: str,
        speaker_map: Optional[Dict[str, str]] = None,
        persist: bool = True,
    ) -> int:
        """Learns vocabulary, opener phrases, turn pace, WPM, and interlocutors from a resolved transcript."""
        from dual_pipeline.speaker_attribution_core.candidate_scorer import extract_ordered_dialogue_turns
        from dual_pipeline.speaker_attribution_core.perspective_scorer import extract_speaker_wpm
        from dual_pipeline.transcript_stages_core.constants import _GENERIC_SPEAKER_PATTERN

        ordered_turns = extract_ordered_dialogue_turns(resolved_transcript)
        valid_speakers = (
            {clean_speaker_identity(str(v)).lower() for v in speaker_map.values() if v}
            if speaker_map else None
        )
        learned_count = 0
        prev_spk: Optional[str] = None
        seen_speakers: set[str] = set()
        for raw_spk, text in ordered_turns:
            clean_spk = clean_speaker_identity(raw_spk)
            if not is_valid_human_speaker_name(clean_spk) or _GENERIC_SPEAKER_PATTERN.fullmatch(clean_spk):
                continue
            if valid_speakers and clean_spk.lower() not in valid_speakers:
                continue
            if len(text.split()) >= 3:
                is_first = clean_spk.lower() not in seen_speakers
                seen_speakers.add(clean_spk.lower())
                wpm = extract_speaker_wpm(clean_spk, resolved_transcript, ordered_turns) if is_first else 0.0
                self.record_attribution(
                    name=clean_spk, utterance=text, interlocutor=prev_spk, persist=False,
                    is_call_opener=(learned_count == 0), observed_wpm=wpm, is_speaker_first_turn=is_first,
                )
                learned_count += 1
            prev_spk = clean_spk

        if learned_count > 0 and persist:
            self.save()
        return learned_count

    def save(self) -> None:
        """Atomically persist current memory to disk."""
        with self._lock:
            data = {
                "schema_version": "1.0.0",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "description": "Governed runtime store for cross-session speaker memory and terminology.",
                "profiles": {k: asdict(v) for k, v in self._profiles.items()},
            }
            self.memory_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = self.memory_path.with_name(f"{self.memory_path.name}.tmp_{uuid.uuid4().hex}")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, self.memory_path)


# Module-level Singleton
_GLOBAL_SPEAKER_MEMORY = SpeakerMemory()


def get_speaker_memory() -> SpeakerMemory:
    return _GLOBAL_SPEAKER_MEMORY
