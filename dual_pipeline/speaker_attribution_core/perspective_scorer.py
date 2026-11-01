"""
dual_pipeline/speaker_attribution_core/perspective_scorer.py

Scores candidate attribution using:
1. Task Ownership memory & first-person claim detection
2. Company perspective ("we/our" vs "you/your") alignment and penalty
3. Speaking speed (WPM) and meeting role habits (opener, inquiry lead)
4. Roster elimination lockout across multiple clusters
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

FIRST_PERSON_TASK_PATTERNS = [
    re.compile(r"\bi\s+(?:finished|completed|updated|built|created|resolved|fixed|delivered|own|lead|handled|pushed)\b", re.IGNORECASE),
    re.compile(r"\bmy\s+(?:team|deliverable|action\s+item|task|workstream|ticket|scope|pr|branch)\b", re.IGNORECASE),
    re.compile(r"\bon\s+my\s+(?:side|end)\b", re.IGNORECASE),
]

_TIMESTAMP_RE = re.compile(r"\[(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\]")


def _parse_timestamp_seconds(text: str) -> Optional[float]:
    m = _TIMESTAMP_RE.search(text)
    if not m:
        return None
    h = int(m.group(1)) if m.group(1) else 0
    m_val = int(m.group(2))
    s_val = int(m.group(3))
    return float(h * 3600 + m_val * 60 + s_val)


def score_task_ownership(
    metadata: Dict[str, Any],
    candidate_name: str,
    full_text_lower: str,
) -> Tuple[float, List[str]]:
    """Calculates task ownership boost for candidate based on first-person claims."""
    try:
        from dual_pipeline.speaker_memory import get_speaker_memory

        mem = metadata.get("speaker_memory") or get_speaker_memory()
        sc, pts = mem.match_task_ownership(candidate_name, full_text_lower)
        if sc > 0:
            return sc, pts

        task_ownership = metadata.get("task_ownership")
        if isinstance(task_ownership, dict):
            cand_tasks = task_ownership.get(candidate_name) or task_ownership.get(candidate_name.lower()) or []
            if isinstance(cand_tasks, (list, tuple)):
                has_first_person = any(pat.search(full_text_lower) for pat in FIRST_PERSON_TASK_PATTERNS)
                if has_first_person:
                    for task in cand_tasks:
                        if str(task).lower().strip() in full_text_lower:
                            return 0.25, [f"First-person task ownership claim ('{task}') (+0.25)"]
    except Exception as e:
        logger.debug("Task ownership scoring encountered non-fatal error: %s", e)
    return 0.0, []


def _extract_org_keywords(org: str) -> List[str]:
    """Generates search keywords for organization name and acronyms."""
    keywords = [org.strip().lower()]
    norm = org.strip().lower()
    if "apex_global" in norm:
        keywords.extend(["apex_global", "nea", "apex"])
    if "partner_advisory" in norm:
        keywords.extend(["partner_advisory"])
    if "boston consulting" in norm or "bcg" in norm:
        keywords.extend(["bcg", "boston consulting"])
    return list(dict.fromkeys(k for k in keywords if len(k) >= 2))


def score_company_perspective(
    org: str,
    full_text_lower: str,
    all_roster_orgs: Optional[Sequence[str]] = None,
) -> Tuple[float, List[str]]:
    """Scores candidate based on first-person vs second-person company references."""
    if not org or len(org.strip()) < 2:
        return 0.0, []

    org_keywords = _extract_org_keywords(org)
    points: List[str] = []
    score_delta = 0.0

    for kw in org_keywords:
        first_person_re = rf"\b(?:our\s+team\s+at|we\s+at|on\s+our\s+side\s+at|our\s+{re.escape(kw)}\s+team)\s+{re.escape(kw)}\b|\b(?:on\s+our\s+side|our\s+team)\b.*?\b{re.escape(kw)}\b"
        if re.search(first_person_re, full_text_lower):
            score_delta += 0.20
            points.append(f"First-person organizational affiliation with {org} (+0.20)")
            break

    for kw in org_keywords:
        second_person_re = rf"\b(?:your\s+team\s+at|you\s+at|on\s+your\s+side\s+at|your\s+{re.escape(kw)}\s+team|you\s+guys\s+at)\s+{re.escape(kw)}\b|\b(?:on\s+your\s+side|your\s+team)\b.*?\b{re.escape(kw)}\b"
        if re.search(second_person_re, full_text_lower):
            score_delta -= 0.25
            points.append(f"Addressed candidate organization {org} in second-person (-0.25)")
            break

    return score_delta, points


_INTERVAL_RE = re.compile(
    r"\[(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s*(?:-|–|to)\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\]",
    re.IGNORECASE,
)


def _find_next_timestamp(lines: List[str], start_idx: int) -> Optional[float]:
    for j in range(start_idx, min(len(lines), start_idx + 5)):
        cand = lines[j].strip()
        if cand:
            ts = _parse_timestamp_seconds(cand)
            if ts is not None:
                return ts
    return None


def _compute_line_turn_duration(
    lines: List[str], idx: int, line_clean: str, active_header_ts: Optional[float]
) -> float:
    interval_m = _INTERVAL_RE.search(line_clean)
    if interval_m:
        t0 = (int(interval_m.group(1) or 0) * 3600) + int(interval_m.group(2)) * 60 + int(interval_m.group(3))
        t1 = (int(interval_m.group(4) or 0) * 3600) + int(interval_m.group(5)) * 60 + int(interval_m.group(6))
        return float(t1 - t0) if t1 > t0 else 0.0
    ts_matches = list(_TIMESTAMP_RE.finditer(line_clean))
    if len(ts_matches) >= 2:
        t0 = _parse_timestamp_seconds(ts_matches[0].group(0))
        t1 = _parse_timestamp_seconds(ts_matches[1].group(0))
        return float(t1 - t0) if (t0 is not None and t1 is not None and t1 > t0) else 0.0
    t0 = _parse_timestamp_seconds(ts_matches[0].group(0)) if ts_matches else active_header_ts
    next_ts = _find_next_timestamp(lines, idx + 1)
    if t0 is not None and next_ts is not None and next_ts > t0:
        return min(120.0, float(next_ts - t0))
    return 0.0


def _line_matches_speaker(line_clean: str, clean_spk: str) -> bool:
    if not clean_spk:
        return False
    lower_line = re.sub(r"^(?:#+\s*)?(?:\[[^\]]+\]\s*)+", "", line_clean.strip()).strip().lower()
    return (
        f"speaker {clean_spk}" in lower_line
        or f"speaker_{clean_spk}" in lower_line
        or f"[{clean_spk}]" in lower_line
        or re.match(rf"^[\*\[\s]*{re.escape(clean_spk)}(?:\b|\]|\*|:|\s*\()", lower_line) is not None
    )


def _extract_turn_words_from_line(lines: List[str], idx: int, line_clean: str) -> int:
    stripped = re.sub(r"^(?:#+\s*)?(?:\[[^\]]+\]\s*)+", "", line_clean).strip()
    stripped = re.sub(r"\[[^\]]+\]", "", stripped)
    utter_part = stripped.split(":", 1)[-1].strip().strip("*") if ":" in stripped else ""
    if not utter_part and idx + 1 < len(lines):
        nxt = lines[idx + 1].strip()
        if nxt and not nxt.startswith("#") and _parse_timestamp_seconds(nxt) is None:
            utter_part = nxt
    return len([w for w in utter_part.split() if w and not w.startswith("[") and not w.endswith("]")])


def _evaluate_line_turn_stats(
    lines: List[str], idx: int, line_clean: str, active_header_ts: Optional[float]
) -> Tuple[int, float]:
    w = _extract_turn_words_from_line(lines, idx, line_clean)
    d = _compute_line_turn_duration(lines, idx, line_clean, active_header_ts)
    if w >= 5 and d >= 2.0:
        turn_wpm = (w / d) * 60.0
        if 50.0 <= turn_wpm <= 280.0:
            return w, d
    return 0, 0.0


def extract_speaker_wpm(
    spk_id: str,
    transcript: str,
    ordered_turns: Optional[List[Tuple[str, str]]] = None,
) -> float:
    """Calculates words per minute for a speaker cluster using turn timestamps."""
    if not transcript:
        return 0.0
    lines = transcript.splitlines()
    clean_spk = re.sub(r"\s*\([^)]*\)", "", spk_id.replace("Speaker", "").strip()).strip().lower()
    total_words = 0
    total_duration = 0.0
    active_header_ts: Optional[float] = None

    for idx, line in enumerate(lines):
        line_clean = line.strip()
        if not line_clean or line_clean.startswith("|") or line_clean.startswith("- "):
            continue
        if line_clean.startswith("#"):
            hdr_ts = _parse_timestamp_seconds(line_clean)
            if hdr_ts is not None:
                active_header_ts = hdr_ts
            if ":" not in line_clean.split("]")[-1]:
                continue
        if _line_matches_speaker(line_clean, clean_spk):
            w, d = _evaluate_line_turn_stats(lines, idx, line_clean, active_header_ts)
            total_words += w
            total_duration += d

    if total_duration >= 5.0 and total_words >= 5:
        return round((total_words / total_duration) * 60.0, 1)
    return 0.0


def score_speaking_speed_and_habits(
    metadata: Dict[str, Any],
    candidate_name: str,
    role: Optional[str],
    full_text: str,
    full_text_lower: str,
    turns: List[str],
    ordered_turns: List[Tuple[str, str]],
    spk_id: str,
    observed_wpm: Optional[float] = None,
) -> Tuple[float, List[str]]:
    """Scores candidate based on speaking speed (WPM) and meeting role habits."""
    score_delta = 0.0
    points: List[str] = []

    try:
        from dual_pipeline.speaker_memory import get_speaker_memory

        mem = metadata.get("speaker_memory") or get_speaker_memory()
        profile = mem.get_profile(candidate_name)

        wpm = observed_wpm if observed_wpm is not None else float(metadata.get("speaker_wpm", {}).get(spk_id, 0.0))
        if profile and profile.avg_words_per_minute > 0 and wpm > 0:
            ratio = min(wpm, profile.avg_words_per_minute) / max(wpm, profile.avg_words_per_minute)
            if ratio >= 0.70:
                score_delta += 0.10
                points.append(f"Speaking speed match ({wpm:.0f} WPM vs avg {profile.avg_words_per_minute:.0f}) (+0.10)")
            elif ratio < 0.40:
                score_delta -= 0.10
                points.append(f"Speaking speed mismatch ({wpm:.0f} WPM vs avg {profile.avg_words_per_minute:.0f}) (-0.10)")

        is_opener = False
        clean_spk = spk_id.replace("Speaker", "").strip().lower()
        if ordered_turns:
            first_spk = ordered_turns[0][0].replace("Speaker", "").strip().lower()
            if first_spk in {clean_spk, f"speaker {clean_spk}"}:
                is_opener = True

        if is_opener:
            if profile and profile.call_opener_count > 0:
                score_delta += 0.10
                points.append("Learned meeting host / call opener habit (+0.10)")
            elif role and any(r in role.lower() for r in ("director", "lead", "owner", "manager", "host")):
                score_delta += 0.05
                points.append(f"Role meeting lead alignment ({role}) (+0.05)")

        if turns:
            q_count = sum(t.count("?") for t in turns)
            if len(turns) >= 2 and (q_count / len(turns)) >= 0.30:
                if role and any(r in role.lower() for r in ("recruiter", "analyst", "director", "interviewer")):
                    score_delta += 0.10
                    points.append(f"Inquiry-heavy call habit match ({q_count} questions) (+0.10)")
    except Exception as e:
        logger.debug("Speaking speed and habits scoring encountered non-fatal error: %s", e)

    return score_delta, points


def apply_roster_elimination_lockout(
    cluster_candidates_map: Dict[str, List[Any]],
) -> Dict[str, List[Any]]:
    """Iteratively locks high-confidence speakers and resolves remaining clusters by elimination."""
    locked_attendees: Set[str] = set()

    for _ in range(len(cluster_candidates_map) + 1):
        new_locks = False
        for spk_id, candidates in cluster_candidates_map.items():
            if not candidates:
                continue
            top = candidates[0]
            if top.confidence >= 0.85 and top.attendee_name not in locked_attendees:
                locked_attendees.add(top.attendee_name)
                new_locks = True

        for spk_id, candidates in cluster_candidates_map.items():
            if not candidates:
                continue
            if candidates[0].attendee_name in locked_attendees and candidates[0].confidence >= 0.85:
                continue

            filtered = [c for c in candidates if c.attendee_name not in locked_attendees]
            if len(filtered) == 1:
                sole = filtered[0]
                sole.confidence = max(sole.confidence, 0.90)
                sole.confidence_tier = "HIGH"
                sole.is_recommended = True
                sole.evidence_rationale = (
                    f"Process of elimination lockout: sole remaining eligible roster attendee ({sole.attendee_name})"
                )
                cluster_candidates_map[spk_id] = [sole]
                locked_attendees.add(sole.attendee_name)
                new_locks = True
            elif filtered:
                filtered.sort(key=lambda c: c.confidence, reverse=True)
                cluster_candidates_map[spk_id] = filtered

        if not new_locks:
            break

    return cluster_candidates_map


def score_candidate_perspectives(
    metadata: Dict[str, Any],
    candidate_name: str,
    org: str,
    role: Optional[str],
    spk_id: str,
    full_text: str,
    full_text_lower: str,
    turns: List[str],
    ordered_turns: List[Tuple[str, str]],
    wpm: float = 0.0,
) -> Tuple[float, List[str]]:
    """Combines task ownership, company perspective, and speech habits into an aggregate perspective score."""
    score = 0.0
    evidence_points: List[str] = []

    task_sc, task_pts = score_task_ownership(metadata, candidate_name, full_text_lower)
    score += task_sc
    evidence_points.extend(task_pts)

    comp_sc, comp_pts = score_company_perspective(org, full_text_lower)
    score += comp_sc
    evidence_points.extend(comp_pts)

    speed_sc, speed_pts = score_speaking_speed_and_habits(
        metadata=metadata,
        candidate_name=candidate_name,
        role=role,
        full_text=full_text,
        full_text_lower=full_text_lower,
        turns=turns,
        ordered_turns=ordered_turns,
        spk_id=spk_id,
        observed_wpm=wpm if wpm > 0 else None,
    )
    score += speed_sc
    evidence_points.extend(speed_pts)

    conv_sc, conv_pts = _score_learned_session_dynamics(metadata, candidate_name, spk_id, ordered_turns, wpm)
    score += conv_sc
    evidence_points.extend(conv_pts)
    return score, evidence_points


def _score_learned_session_dynamics(
    metadata: Dict[str, Any],
    candidate_name: str,
    spk_id: str,
    ordered_turns: List[Tuple[str, str]],
    wpm: float,
) -> Tuple[float, List[str]]:
    priors_path = metadata.get("speaker_priors_path")
    if not priors_path and not metadata.get("use_learned_speaker_priors"):
        return 0.0, []
    try:
        from pathlib import Path
        from dual_pipeline.transcript_metadata_core.speaker_priors_registry import score_learned_conversational_priors
        spk_map = metadata.get("speaker_map") or {}
        norm_spk = spk_id.replace("Speaker ", "").strip().lower()
        prev_resolved: Optional[str] = None
        for idx, (t_spk, _) in enumerate(ordered_turns):
            if t_spk.replace("Speaker ", "").strip().lower() == norm_spk and idx > 0:
                raw_prev = ordered_turns[idx - 1][0]
                prev_resolved = spk_map.get(raw_prev) or raw_prev
                break
        raw_peers = metadata.get("attendees") or metadata.get("roster") or []
        peers = [str(p.get("name") if isinstance(p, dict) else p) for p in (raw_peers if isinstance(raw_peers, list) else [])]
        return score_learned_conversational_priors(
            candidate_name,
            prev_speaker=prev_resolved,
            roster_peers=peers,
            observed_wpm=wpm,
            priors_path=Path(priors_path) if priors_path else None,
        )
    except Exception as exc:
        logger.debug("Learned session dynamics scoring skipped: %s", exc)
        return 0.0, []

