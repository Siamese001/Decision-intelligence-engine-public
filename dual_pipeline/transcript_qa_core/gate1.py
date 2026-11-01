"""
Deterministic QA Gate 1: Forensic Transcript QA Gates.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, List, Optional

from dual_pipeline.transcript_duration_helper import resolve_meeting_duration

from .gate1_attribution import (
    check_alr_row_count_minimum,
    check_ambiguity_lock_register_and_speaker_hitl,
    check_attribution_hygiene,
)
from .gate1_entity_context import (
    _check_attribution_summary_table,
    _extract_roster_names_g18,
)
from .gate1_helpers import _extract_all_turns, compute_released_text_coverage

logger = logging.getLogger(__name__)


def _parse_timestamp_seconds(ts_str: str) -> Optional[int]:
    """Parse MM:SS or HH:MM:SS string into total seconds."""
    parts = ts_str.strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        return None
    return None


def _check_attribution_hygiene(
    f1_text: str,
    meta: Dict[str, Any],
) -> tuple[bool, List[str], Dict[str, bool], List[str]]:
    """Preserve compatibility while delegating to gate1_attribution."""
    return check_attribution_hygiene(f1_text, meta)


def _check_temporal_strictness(f1_text: str) -> tuple[bool, bool, List[str], List[str]]:
    header_date = bool(
        re.search(r"(?:\*\*Date:?\*\*:?|Date:)\s*\d{2}/\d{2}/\d{4}", f1_text)
    )
    iso_dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", f1_text)
    gate_passed = header_date and (len(iso_dates) == 0)
    failures: List[str] = []
    if not header_date:
        failures.append("Output 1 missing canonical header date formatted as MM/DD/YYYY.")
    if iso_dates:
        failures.append(f"Output 1 contains forbidden ISO-8601 dates: {iso_dates[:3]}")
    return gate_passed, header_date, iso_dates, failures


def _check_vocabulary_preservation(
    source: str,
    f1_text: str,
    meta: Dict[str, Any],
) -> tuple[bool, Dict[str, bool], List[str]]:
    domain_terms = meta.get("expected_domain_terms", [])
    terms_present = {}
    source_lower = source.lower()
    f1_lower = f1_text.lower()
    for term in domain_terms:
        if term.lower() in source_lower:
            terms_present[term] = term.lower() in f1_lower
    gate_passed = all(terms_present.values()) if terms_present else True
    failures: List[str] = []
    if not gate_passed:
        missing_terms = [t for t, p in terms_present.items() if not p]
        failures.append(f"Output 1 truncated domain vocabulary: {missing_terms}")
    return gate_passed, terms_present, failures


_ASR_HARD_BLOCKLIST: List[str] = [
    "thank you.", "thank you so much.", "thanks.", "the end.",
    "bye.", "goodbye.", "see you.", "see you later.", "you're welcome.",
]
_ASR_REPETITION_THRESHOLD = 3   # ≥3 occurrences of the same ≤4-word isolated turn
_ASR_FAILURE_THRESHOLD = 5      # advisory becomes gate failure at ≥5 total candidates


def _extract_transcript_body_for_asr(f1_text: str) -> str:
    """Extract transcript body only (skip header metadata) for ASR scan."""
    split_res = re.split(r"\n#{1,3}\s+(?:Forensic\s+)?Transcript\b", f1_text, maxsplit=1, flags=re.IGNORECASE)
    return split_res[-1] if len(split_res) > 1 else f1_text


def _count_isolated_short_phrases(body: str) -> Dict[str, int]:
    """Count short isolated turns (≤5 words, terminal punctuation, no attribution markers)."""
    phrase_counts: Dict[str, int] = {}
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^\*\*\[|^\[|^#|^--|^\|", line):
            continue
        if re.match(r"^\*\*[A-Z]", line):
            continue
        if line[-1] not in ".!?":
            continue
        if len(line.split()) <= 5:
            phrase_counts[line] = phrase_counts.get(line, 0) + 1
    return phrase_counts


def _classify_asr_candidates(phrase_counts: Dict[str, int]) -> List[str]:
    """Classify phrase counts into blocklist hits and repetition candidates."""
    candidates: List[str] = []
    for phrase, count in phrase_counts.items():
        phrase_lower = phrase.lower().strip()
        if phrase_lower in _ASR_HARD_BLOCKLIST:
            candidates.append(
                f"[ASR_HALLUCINATION_BLOCKLIST] '{phrase}' appeared {count}× as isolated turn "
                f"(hard-blocked Whisper hallucination phrase)."
            )
        elif count >= _ASR_REPETITION_THRESHOLD:
            candidates.append(
                f"[ASR_HALLUCINATION_CANDIDATE] '{phrase}' appeared {count}× as isolated "
                f"single-line turn — likely Whisper near-silence hallucination."
            )
    return candidates


def _check_repetitive_isolated_turn_hallucinations(
    f1_text: str,
) -> tuple[bool, List[str], List[str]]:
    """Gate 1.4 sub-check: detect Whisper ASR hallucination artifacts.

    Returns:
        (gate_passed, hallucination_candidates, failures)
        - gate_passed: True unless ≥ _ASR_FAILURE_THRESHOLD candidates detected
        - hallucination_candidates: list of advisory strings for ALR
        - failures: gate failure messages (only populated when threshold crossed)
    """
    body = _extract_transcript_body_for_asr(f1_text)
    phrase_counts = _count_isolated_short_phrases(body)
    candidates = _classify_asr_candidates(phrase_counts)

    failures: List[str] = []
    if len(candidates) >= _ASR_FAILURE_THRESHOLD:
        failures.append(
            f"Gate 1.4 Hallucination Density: {len(candidates)} ASR hallucination candidates detected "
            f"(threshold: {_ASR_FAILURE_THRESHOLD}). Add to ALR and review transcript before release."
        )
    return len(failures) == 0, candidates, failures


def _is_unannotated_gap(intervening_text: str, delta: int) -> bool:
    if delta <= 32 or re.search(r"\[(?:Recording gap|Natural pause|Session pause):\s*[^\]]+\]", intervening_text, re.IGNORECASE):
        return False
    word_count = len(intervening_text.split())
    if word_count >= 15:
        return False
    if delta > 180 and word_count < 15:
        return True
    if delta > 32:
        cleaned_speech = re.sub(r"[\*#_\[\]\(\)]", " ", intervening_text)
        cleaned_speech = re.sub(r"[A-Za-z0-9\s]+:", "", cleaned_speech).strip().lower()
        if not cleaned_speech or cleaned_speech in _ASR_HARD_BLOCKLIST:
            return True
    return False


def _check_timestamp_continuity(transcript_body: str) -> tuple[bool, List[str], List[str]]:
    timestamp_matches = list(re.finditer(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]", transcript_body))
    parsed_timestamps = [
        (sec, m.start(), m.end(), m.group(1))
        for m in timestamp_matches
        if (sec := _parse_timestamp_seconds(m.group(1))) is not None
    ]

    unannotated_gaps = []
    for i in range(len(parsed_timestamps) - 1):
        t1_sec, _, t1_end, t1_str = parsed_timestamps[i]
        t2_sec, t2_start, _, t2_str = parsed_timestamps[i + 1]
        delta = t2_sec - t1_sec
        intervening_text = transcript_body[t1_end:t2_start].strip()
        if _is_unannotated_gap(intervening_text, delta):
            unannotated_gaps.append(f"[{t1_str}] to [{t2_str}] ({delta}s gap)")

    gate_passed = len(unannotated_gaps) == 0
    failures: List[str] = []
    if not gate_passed:
        failures.append(
            f"Output 1 violates Rule C-11: unannotated recording gap(s) > 32s detected without standard '[Recording gap: HH:MM:SS – HH:MM:SS]' or '[Natural pause: HH:MM:SS – HH:MM:SS (no speech)]' notation: {', '.join(unannotated_gaps)}"
        )
    return gate_passed, unannotated_gaps, failures



def _subtract_annotated_gaps(clean_body: str, meeting_dur: float) -> float:
    eff_dur = float(meeting_dur)
    gap_pattern = re.compile(
        r"\[(?:Recording gap|Natural pause|Session pause|Pause|Gap):\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*[–-]\s*(\d{1,2}:\d{2}(?::\d{2})?)[^\]]*\]",
        re.IGNORECASE,
    )
    for m in gap_pattern.finditer(clean_body):
        s_sec = _parse_timestamp_seconds(m.group(1)) or 0
        e_sec = _parse_timestamp_seconds(m.group(2)) or 0
        gap_mins = max(0.0, (e_sec - s_sec) / 60.0)
        eff_dur = max(1.0, eff_dur - gap_mins)
    return eff_dur


def _compute_density_floors(eff_dur: float, f1_text: str, meta: Dict[str, Any], total_words: int, total_turns: int) -> tuple[int, int]:
    from dual_pipeline.parameters import get_output1_word_density_bounds
    wpm_mult, turn_words_bypass = get_output1_word_density_bounds()
    min_turns = max(2 if eff_dur < 8 else 4, int(eff_dur * 0.5))
    is_pre = bool(meta.get("is_pre_resolved") or meta.get("force_rerun") or "hitl_resolved" in f1_text.lower() or "hitl-resolved" in f1_text.lower())
    if eff_dur < 8:
        min_words = max(20, int(eff_dur * wpm_mult))
    elif is_pre:
        min_words = max(80, int(eff_dur * 15))
    else:
        min_words = max(150, int(eff_dur * 25))
    if total_words >= min_words and (total_words / max(1, total_turns)) >= turn_words_bypass:
        min_turns = max(2, int(eff_dur * 0.25))
    return min_turns, min_words


def _check_duration_and_turn_density(
    f1_text: str,
    transcript_body: str,
    meta: Dict[str, Any],
) -> tuple[bool, Optional[int], int, int, List[str]]:
    meeting_dur = resolve_meeting_duration(meta, f1_text) or resolve_meeting_duration(meta, transcript_body)
    clean_body = re.sub(
        r"(?:^|\n)#{1,3}\s+Ambiguity Lock Register\b[\s\S]*?(?=\n(?:#{1,3}\s+|---|___|\*\*\*|\Z))",
        "\n",
        transcript_body,
    )
    total_turns = len(_extract_all_turns(transcript_body))
    total_words = len(clean_body.split())
    gate_passed = True
    failures: List[str] = []
    if meeting_dur:
        eff_dur = _subtract_annotated_gaps(clean_body, float(meeting_dur))
        min_turns, min_words = _compute_density_floors(eff_dur, f1_text, meta, total_words, total_turns)
        if total_turns < min_turns:
            gate_passed = False
            failures.append(
                f"Output 1 turn density failure: found only {total_turns} turns for a {meeting_dur}-minute meeting (< {min_turns} turns required). Upstream audio/ASR truncation suspected."
            )
        elif total_words < min_words:
            gate_passed = False
            failures.append(
                f"Output 1 word count density failure: found only {total_words} words for a {meeting_dur}-minute meeting (< {min_words} words required). Upstream audio/ASR truncation suspected."
            )
    return gate_passed, meeting_dur, total_turns, total_words, failures


def _check_title_and_date(
    f1_text: str,
    meta: Dict[str, Any],
) -> tuple[bool, List[str]]:
    """Gate 1.1b: Title Audit verifying primary H1 contains meeting name and date for live transcripts."""
    failures: List[str] = []
    h1_match = re.search(r"^#\s+(.+)$", f1_text, re.MULTILINE)
    h1_title = h1_match.group(1).strip() if h1_match else ""

    is_live = bool(
        meta.get("is_live")
        or meta.get("enforce_title_date")
        or meta.get("source_modality") == "LIVE_AUDIO"
        or meta.get("source_type") in ("AUDIO_LIVE", "LIVE_CAPTURE", "LIVE")
        or (h1_title and re.match(r"^Live Meeting Transcript\b", h1_title, re.IGNORECASE))
    )

    if not is_live:
        return True, []

    if not h1_match:
        failures.append("Gate 1.1b Title Audit: Transcript missing primary '# ' H1 title heading.")
        return False, failures

    if is_live:
        has_date = bool(re.search(r"\b(\d{2}[/-]\d{2}[/-]\d{4}|\d{4}-\d{2}-\d{2})\b", h1_title))
        if not has_date:
            failures.append(
                f"Gate 1.1b Title Audit: Live meeting heading must include meeting date in format ' | MM/DD/YYYY'. Heading: '{h1_title}'"
            )

        meeting_name = meta.get("meeting_name") or meta.get("title") or meta.get("meeting")
        if meeting_name:
            norm_name = re.sub(r"[_\-]+", " ", str(meeting_name)).strip().lower()
            norm_h1 = re.sub(r"[_\-]+", " ", h1_title).strip().lower()
            name_tokens = [tok for tok in norm_name.split() if len(tok) > 2]
            if norm_name not in norm_h1 and not any(tok in norm_h1 for tok in name_tokens):
                failures.append(
                    f"Gate 1.1b Title Audit: Live meeting heading must include meeting name '{meeting_name}'. Heading: '{h1_title}'"
                )

    return len(failures) == 0, failures


def run_qa_gate_1(
    f1_text: str,
    source_text: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute Gate 1: Forensic Transcript QA Gates."""
    meta = metadata or {}
    source = source_text if source_text is not None else f1_text
    failures: List[str] = []

    # Gate 1.1: Immutable Source Binding
    source_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()
    output_sha256 = hashlib.sha256(f1_text.encode("utf-8")).hexdigest()
    gate_1_1_passed = bool(f1_text.strip())

    # Gate 1.1b: Live Meeting Title and Date Audit
    gate_1_1b_passed, f_1_1b = _check_title_and_date(f1_text, meta)
    failures.extend(f_1_1b)

    # Gate 1.2: Structured Attribution Hygiene
    gate_1_2_passed, unresolved_speakers, speakers_attributed, f_1_2 = _check_attribution_hygiene(f1_text, meta)
    failures.extend(f_1_2)

    # Gate 1.3: Temporal Strictness
    gate_1_3_passed, header_date, iso_dates, f_1_3 = _check_temporal_strictness(f1_text)
    failures.extend(f_1_3)

    # Gate 1.4: Source-Derived Vocabulary Preservation + ASR Hallucination Detection
    gate_1_4_passed, terms_present, f_1_4 = _check_vocabulary_preservation(source, f1_text, meta)
    failures.extend(f_1_4)
    hallucination_ok, hallucination_candidates, f_1_4_hall = _check_repetitive_isolated_turn_hallucinations(f1_text)
    if not hallucination_ok:
        gate_1_4_passed = False
        failures.extend(f_1_4_hall)

    # Gate 1.5: Timestamp Continuity & Recording Gap Protocol (Rule C-11)
    split_res = re.split(r"\n#{1,3}\s+(?:Forensic\s+)?Transcript\b", f1_text, maxsplit=1, flags=re.IGNORECASE)
    transcript_body = split_res[-1] if len(split_res) > 1 else f1_text
    dialogue_body = re.split(r"(?:^|\n)#{1,3}\s+(?:Ambiguity Lock Register|Attribution Summary)\b", transcript_body, flags=re.IGNORECASE)[0]
    gate_1_5_passed, unannotated_gaps, f_1_5 = _check_timestamp_continuity(dialogue_body)
    failures.extend(f_1_5)

    # Gate 1.6: Duration-to-Turn & Word Density
    gate_1_6_passed, meeting_dur, total_turns, total_words, f_1_6 = _check_duration_and_turn_density(
        f1_text, transcript_body, meta
    )
    failures.extend(f_1_6)

    # Gate 1.7: Ambiguity Lock Register & Detailed HITL Speaker Attribution
    gate_1_7_passed, alr_details, f_1_7 = check_ambiguity_lock_register_and_speaker_hitl(f1_text, meta)
    failures.extend(f_1_7)

    # Gate 1.8: Attribution Summary Table Presence (multi-party meetings)
    gate_1_8_passed, f_1_8, g18_attendee_count = _check_attribution_summary_table(f1_text, meta)
    failures.extend(f_1_8)

    # Gate 1.9: ALR Minimum Row Count (speakers + 1)
    gate_1_9_passed, alr_actual_rows, alr_min_rows, f_1_9 = check_alr_row_count_minimum(f1_text, meta)
    failures.extend(f_1_9)

    # Gate 1.10: Referenced Entity Grounding Gate (Constraint C-27)
    from .gate1_entity_context import evaluate_referenced_entity_context_gate
    recap_for_c27 = meta.get("current_executive_recap") or meta.get("f2_text") or meta.get("prior_executive_recap", "")
    gate_1_10_passed, f_1_10 = evaluate_referenced_entity_context_gate(
        f1_text=f1_text,
        f2_text=recap_for_c27,
        roster_names=_extract_roster_names_g18(meta, f1_text=f1_text),
        entity_clarifications=meta.get("entity_clarifications"),
        project=meta.get("project"),
    )
    failures.extend(f_1_10)

    # Gate 1.11: Turn Formatting Integrity & Modality Provenance Gate
    from .gate1_helpers import check_transcript_formatting_and_provenance
    gate_1_11_passed, f_1_11 = check_transcript_formatting_and_provenance(f1_text, meta)
    failures.extend(f_1_11)

    passed = all([
        gate_1_1_passed, gate_1_1b_passed, gate_1_2_passed, gate_1_3_passed, gate_1_4_passed, gate_1_5_passed,
        gate_1_6_passed, gate_1_7_passed, gate_1_8_passed, gate_1_9_passed, gate_1_10_passed,
        gate_1_11_passed,
    ]) and len(failures) == 0
    cov = compute_released_text_coverage(f1_text)

    return {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "coverage_metrics": cov,
        "gates": {
            "Gate_1_1_Immutable_Source_Binding": {
                "passed": gate_1_1_passed, "source_byte_length": len(source.encode("utf-8")),
                "output_byte_length": len(f1_text.encode("utf-8")), "source_sha256": source_sha256, "output_sha256": output_sha256,
            },
            "Gate_1_1b_Title_And_Date": {
                "passed": gate_1_1b_passed, "failures": f_1_1b,
            },
            "Gate_1_2_Structured_Attribution_Hygiene": {
                "passed": gate_1_2_passed, "unresolved_speakers_count": len(unresolved_speakers),
                "unresolved_speakers": unresolved_speakers, "speakers_verified": speakers_attributed,
                "coverage_metrics": cov,
            },
            "Gate_1_3_Temporal_Strictness": {
                "passed": gate_1_3_passed, "canonical_header_date_present": header_date, "iso_dates_detected": iso_dates,
            },
            "Gate_1_4_Source_Derived_Vocabulary": {
                "passed": gate_1_4_passed, "terms_verified": terms_present,
                "hallucination_candidates": hallucination_candidates, "hallucination_candidate_count": len(hallucination_candidates),
            },
            "Gate_1_5_Timestamp_Continuity": {
                "passed": gate_1_5_passed, "unannotated_gaps": unannotated_gaps,
            },
            "Gate_1_6_Duration_Turn_Density": {
                "passed": gate_1_6_passed, "duration_minutes": meeting_dur, "total_turns": total_turns, "total_words": total_words,
            },
            "Gate_1_7_Ambiguity_Lock_Register_And_Speaker_HITL": {
                "passed": gate_1_7_passed, "present": alr_details.get("present", False),
                "item_count": alr_details.get("item_count", 0),
                "speaker_attribution_grounded": alr_details.get("speaker_attribution_grounded", False),
                "hitl_receipt_bound": alr_details.get("hitl_receipt_bound", False),
            },
            "Gate_1_8_Attribution_Summary_Presence": {
                "passed": gate_1_8_passed, "required_for_multi_party": g18_attendee_count >= 2, "attendee_count": g18_attendee_count,
            },
            "Gate_1_9_ALR_Row_Count_Minimum": {
                "passed": gate_1_9_passed, "actual_rows": alr_actual_rows, "minimum_required_rows": alr_min_rows,
            },
            "Gate_1_10_Referenced_Entity_Context": {
                "passed": gate_1_10_passed,
            },
            "Gate_1_11_Formatting_And_Provenance": {
                "passed": gate_1_11_passed, "failures": f_1_11,
            },
        },
        "failures": failures,
    }
