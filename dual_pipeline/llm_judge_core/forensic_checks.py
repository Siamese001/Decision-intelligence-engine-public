"""
dual_pipeline/llm_judge_core/forensic_checks.py

Deterministic preflight and penalty checks for ForensicTranscriptJudge (Output 01).
Extracted from forensic.py to preserve module budget and modularity.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.prompts.config import CANONICAL_PROMPT_CONFIG
from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY
from dual_pipeline.transcript_stages_core.hitl_guard import assert_no_u0_hitl_bypass  # noqa: lazy-decouple

GENERIC_SPEAKER_PATTERN = re.compile(
    r"^\s*(?:\d+\.\s+|[-*]\s+)?\*\*(?:Speaker(?:\s+[A-Z\d]+)?|Unknown(?:\s+Speaker)?|Unattributed):?\*\*",
    re.MULTILINE | re.IGNORECASE,
)
UNIVERSAL_TURN_PATTERN = re.compile(
    r"(?:^|\n)(?:#{1,3}\s*\[(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?(?:\s*[-–—]\s*\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?)?)[\]\)]\s*\n+)?"
    r"(?:\*\*)?(?:\[(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?(?:\s*[-–—]\s*\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?)?)[\]\)])?(?:\*\*)?:?[^\S\r\n]*"
    r"(?:\*\*)?\[?([A-Za-z0-9][a-zA-Z0-9\s\-\.,/\(\)\'’ʼ]+?)\]?(?:\s*\[(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?(?:\s*[-–—]\s*\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?)?)\])?(?::\*\*|\*\*:?|:(?!\d))[^\S\r\n]*"
    r"(?:\[(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?(?:\s*[-–—]\s*\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?)?)[\]\)])?:?[^\S\r\n]*(.*)",
    re.MULTILINE,
)
TOTAL_TURN_PATTERN = UNIVERSAL_TURN_PATTERN
RESERVED_SPEAKER_LABELS = {
    "date", "time", "project", "status", "platform reference",
    "attendees", "participants", "objective", "note", "transcript",
    "natural pause", "recording gap", "pause", "gap",
}
RESERVED_PREFIXES = ("referenced", "attribution", "recording gap", "natural pause", "pause", "asr artifact")

C19_LEAK_MARKERS = [
    "$1.2M", "$1.2m", "INTERNAL_ADVISORY", "networking_wedge", "political_rationale",
    "compensation negotiation", "personal equity expansion", "political wedge",
    "personal promotion", "career advisory", "career notes", "advancement vector", "political power map",
]


def is_corporate_affiliation_fallback(label: str) -> bool:
    """True if label is a corporate or partner affiliation fallback (e.g. PartnerAdvisory Speaker, Client Speaker)."""
    s = label.strip().strip("[]*:").strip().lower()
    if s in ("partner_advisory speaker", "client speaker", "partner speaker", "vendor speaker", "external speaker", "partner_advisory team", "client team"):
        return True
    return s.endswith(" speaker") and s[:-8].strip() in ("client", "partner_advisory", "partner", "vendor", "external")


def is_generic_speaker(label: str) -> bool:
    """True only for ungrounded/un-attributed generic labels (e.g. Speaker, Unknown, Unattributed, Speaker 1)."""
    if is_corporate_affiliation_fallback(label):
        return False
    s = label.strip().strip("[]*:").strip().lower()
    if s in ("speaker", "unknown", "unattributed", "unknown speaker", "speaker unknown", "participant", "anonymous", "voice", "unresolved", "unidentified", "unidentified speaker", "guest", "room"):
        return True
    if s.endswith(" speaker") and s[:-8].strip() in ("guest", "room", "unknown"):
        return True
    if re.match(r"^(?:speaker|voice|participant)[\s_\-]+([a-z\d]+)$", s):
        return True
    return bool(re.match(r"^speaker_\d+$", s, re.IGNORECASE))


def check_c19_leak(text: str) -> bool:
    text_lower = text.lower()
    return any(marker.lower() in text_lower for marker in C19_LEAK_MARKERS)


def _extract_forensic_body(f1_text: str) -> str:
    m = re.search(r"(?:^|\n)##\s+(?:Forensic Transcript|Transcript|Forensic Dialogue)\b.*?\n", f1_text, re.IGNORECASE)
    body = f1_text[m.end():] if m else (f1_text.split("---", 1)[1] if "---" in f1_text else f1_text)
    for marker in ("## Ambiguity Lock Register", "## Ambiguity Register"):
        if marker in body:
            body = body.split(marker, 1)[0]
    return body.strip()


def _is_valid_speaker_turn(s: str) -> bool:
    s_low = s.strip().lower()
    return any(c.isalpha() for c in s) and s_low not in RESERVED_SPEAKER_LABELS and not s_low.startswith(RESERVED_PREFIXES)


def check_generic_speaker_ratio(f1_text: str) -> Tuple[bool, float, int, int]:
    """Returns (is_excessive, ratio, generic_count, total_count)."""
    body = _extract_forensic_body(f1_text)
    turns = [m.group(3).strip() for m in UNIVERSAL_TURN_PATTERN.finditer(body) if _is_valid_speaker_turn(m.group(3))]
    total_turns = len(turns)
    if total_turns == 0:
        return False, 0.0, 0, 0
    generic_turns = sum(1 for s in turns if is_generic_speaker(s))
    ratio = generic_turns / total_turns
    ceil_ratio = CANONICAL_PROMPT_CONFIG.output1.generic_speaker_ceiling_percent / 100.0
    return (ratio > ceil_ratio), ratio, generic_turns, total_turns


def check_single_speaker_collapse(
    f1_text: str, metadata: Optional[Dict[str, Any]] = None
) -> Tuple[bool, str, int, int]:
    """Returns (is_collapsed, dominant_speaker, dominant_count, total_count)."""
    meta = metadata or {}
    roster = meta.get("roster") or meta.get("attendees") or []
    if roster and len(roster) < 2:
        return False, "", 0, 0
    try:
        from dual_pipeline.speaker_attribution_core.collapse_detector import _verify_collapse_approval_receipt
        if _verify_collapse_approval_receipt(meta):
            return False, "", 0, 0
    except Exception:
        pass
    body = _extract_forensic_body(f1_text)
    spk_counts: Dict[str, int] = {}
    for m in UNIVERSAL_TURN_PATTERN.finditer(body):
        s = m.group(3).strip()
        if _is_valid_speaker_turn(s):
            spk_counts[s] = spk_counts.get(s, 0) + 1
    total = sum(spk_counts.values())
    if total < 10:
        return False, "", 0, total
    dom_spk, dom_cnt = max(spk_counts.items(), key=lambda x: x[1])
    thresh = CANONICAL_PROMPT_CONFIG.output1.single_speaker_collapse_threshold
    return (dom_cnt / total >= thresh), dom_spk, dom_cnt, total


def _extract_partner_advisory_attendees(roster: List[Any]) -> set[str]:
    partner_advisory_attendees: set[str] = set()
    for item in roster:
        if isinstance(item, dict):
            name = (item.get("name") or "").strip().lower()
            org = (item.get("organization") or item.get("org") or "").strip().lower()
            if name and "partner_advisory" in org:
                partner_advisory_attendees.add(name)
        elif isinstance(item, str) and "partner_advisory" in item.lower():
            partner_advisory_attendees.add(item.strip().lower().split("(")[0].strip())
    return partner_advisory_attendees


def check_corporate_affiliation_leaks(
    f1_text: str, metadata: Optional[Dict[str, Any]] = None
) -> List[str]:
    """Checks for misattributed corporate affiliations (e.g. PartnerAdvisory personnel as Client/Unknown)."""
    meta = metadata or {}
    partner_advisory_attendees = _extract_partner_advisory_attendees(meta.get("roster") or meta.get("attendees") or [])
    part_match = re.search(r"\*\*Participants:\*\*(.*?)(?:\n\s*\n|\n(?=\*\*|\#|\>|\-)|---|\n##|\Z)", f1_text, re.DOTALL)
    if not part_match or not partner_advisory_attendees:
        return []
    entries = [
        item.strip().lstrip("-* \t")
        for line in part_match.group(1).splitlines()
        for item in re.split(r"[;\n]", line)
        if item.strip().lstrip("-* \t")
    ]
    violations: List[str] = []
    for entry in entries:
        entry_low = entry.lower()
        for s_name in partner_advisory_attendees:
            if s_name in entry_low and "partner_advisory" not in entry_low and any(t in entry_low for t in ("client", "partner", "unknown")):
                violations.append(f"PartnerAdvisory attendee misattributed to client/unknown: '{entry}'")
    return violations


def build_c19_violation_result() -> Dict[str, Any]:
    return {
        "composite_score": 30, "verdict": "FAIL",
        "forensic_rationale": "RULE C-19 VIOLATION: Confidential career leverage / internal advisory markers leaked into Output 01 forensic transcript.",
        "actionable_remediation_diff": "Quarantine and strip all private advisory notes, networking wedges, or compensation markers from Output 01.",
        "grounding_citations": [],
        "critique_diff_envelope": {
            "artifact": "OUTPUT01", "target_path": "Transcript Dialogue Turns",
            "observed_defect": "Confidential career leverage leaked into Output 01.",
            "required_delta": "Quarantine private advisory markers.",
            "protected_paths": ["## Ambiguity Lock Register"], "forbidden_mutations": [],
        },
    }


def build_mock_result() -> Dict[str, Any]:
    return {
        "judge_model": "mock-forensic-judge", "prompt_digest": "mock-prompt-digest-o1",
        "rubric_version": "2.0", "sample_count": 1, "is_fallback": False,
        "effective_model": "mock-forensic-judge", "effective_provider": "local",
        "extracted_evidence_spans": ["[00:01:12] harper Hoffman: 'We are expanding the pilot to three additional sites.'"],
        "prosecutor_counter_arguments": ["Checked for dialogue fabrication and single-speaker collapse: None found."],
        "dimension_1_forensic_grounding_and_fidelity": 25, "dimension_2_attribution_and_room_mic_hygiene": 24,
        "dimension_3_chronological_and_timestamp_integrity": 25, "dimension_4_asr_cleanup_and_domain_preservation": 24,
        "composite_score": 98, "verdict": "PASS",
        "forensic_rationale": "Forensic transcript achieves complete verbatim fidelity and zero hallucination.",
        "actionable_remediation_diff": "", "grounding_citations": ["Turn 1", "Turn 2"],
        "critique_diff_envelope": {
            "artifact": "OUTPUT01", "target_path": "Transcript Dialogue Turns",
            "observed_defect": "None (passing evaluation)", "required_delta": "No change required",
            "protected_paths": ["## Ambiguity Lock Register"], "forbidden_mutations": [],
        },
    }


def apply_collapse_penalty(
    parsed: Dict[str, Any], f1_text: str, metadata: Optional[Dict[str, Any]] = None
) -> None:
    if metadata:
        assert_no_u0_hitl_bypass(metadata)
    is_collapsed, dom_spk, dom_cnt, total_turns = check_single_speaker_collapse(f1_text, metadata=metadata)
    if not is_collapsed:
        return
    parsed["single_speaker_collapse"] = True
    parsed["dimension_2_attribution_and_room_mic_hygiene"] = 5
    pct = int(dom_cnt / total_turns * 100) if total_turns else 0
    cap_rationale = (
        f"ATTRIBUTION FLOOR HARD FAILURE (SINGLE_SPEAKER_COLLAPSE_DETECTED): Single-speaker collapse detected. "
        f"Speaker '{dom_spk}' accounts for {dom_cnt}/{total_turns} ({pct}%) "
        f"of dialogue turns in Output 1. Discrete turns for other attendees must be recovered."
    )
    existing = parsed.get("forensic_rationale", "")
    parsed["forensic_rationale"] = f"{cap_rationale}\n{existing}".strip()
    parsed["actionable_remediation_diff"] = (
        f"HALT AUTONOMOUS MUTATION: Single-speaker collapse detected for {dom_spk}. "
        "Route to operator review via `ask_user_question` and record verified attributions "
        "in the Ambiguity Lock Register and hitl_decision_ledger.json."
    )


def apply_affiliation_penalty(
    parsed: Dict[str, Any], f1_text: str, metadata: Optional[Dict[str, Any]] = None
) -> None:
    aff_violations = check_corporate_affiliation_leaks(f1_text, metadata=metadata)
    if not aff_violations:
        return
    parsed["dimension_2_attribution_and_room_mic_hygiene"] = min(parsed.get("dimension_2_attribution_and_room_mic_hygiene", 25) or 25, 10)
    parsed["composite_score"] = min(parsed.get("composite_score", 100) or 100, 65)
    existing = parsed.get("forensic_rationale", "")
    parsed["forensic_rationale"] = f"CORPORATE AFFILIATION DEFECT: {'; '.join(aff_violations)}\n{existing}".strip()


def apply_generic_speaker_penalty(parsed: Dict[str, Any], f1_text: str) -> None:
    is_excessive, ratio, generic_cnt, total_cnt = check_generic_speaker_ratio(f1_text)
    if not is_excessive:
        return
    capped_d2 = min(parsed.get("dimension_2_attribution_and_room_mic_hygiene", 25) or 25, 5)
    parsed["dimension_2_attribution_and_room_mic_hygiene"] = capped_d2
    d1 = parsed.get("dimension_1_forensic_grounding_and_fidelity", 25) or 25
    d3 = parsed.get("dimension_3_chronological_and_timestamp_integrity", 25) or 25
    d4 = parsed.get("dimension_4_asr_cleanup_and_domain_preservation", 25) or 25
    parsed["composite_score"] = min(parsed.get("composite_score", 100) or 100, d1 + capped_d2 + d3 + d4, 69)
    cap_rationale = (
        f"ATTRIBUTION FLOOR HARD FAILURE: {generic_cnt}/{total_cnt} ({ratio * 100:.1f}%) of dialogue turns "
        f"are un-attributed generic speaker labels (>15% max threshold). Dimension 2 capped at 5/25, triggering an automatic FAIL."
    )
    existing = parsed.get("forensic_rationale", "")
    parsed["forensic_rationale"] = f"{cap_rationale}\n{existing}".strip()
    parsed["actionable_remediation_diff"] = (
        "HALT AUTONOMOUS MUTATION: Un-attributed generic speaker labels exceed 15%. "
        "Route dialogue turns to operator review via `ask_user_question` to resolve identities "
        "and ground them in the Ambiguity Lock Register and hitl_decision_ledger.json."
    )


def apply_alr_speaker_hitl_penalty(
    parsed: Dict[str, Any], f1_text: str, metadata: Optional[Dict[str, Any]] = None
) -> None:
    from dual_pipeline.transcript_qa_core.gate1_attribution import check_ambiguity_lock_register_and_speaker_hitl
    passed, _, failures = check_ambiguity_lock_register_and_speaker_hitl(f1_text, metadata or {})
    if passed:
        return
    parsed["dimension_2_attribution_and_room_mic_hygiene"] = min(parsed.get("dimension_2_attribution_and_room_mic_hygiene", 25) or 25, 8)
    parsed["composite_score"] = min(parsed.get("composite_score", 100) or 100, 68)
    existing = parsed.get("forensic_rationale", "")
    parsed["forensic_rationale"] = (
        f"AMBIGUITY_LOCK_REGISTER_DEFECT: Output 1 lacks an itemized Ambiguity Lock Register with detailed HITL "
        f"speaker attribution ({'; '.join(failures)}). Output 1 cannot pass Judge evaluation without verified speaker attribution.\n{existing}"
    ).strip()
    existing_diff = parsed.get("actionable_remediation_diff", "")
    alr_diff = (
        "HALT AUTONOMOUS MUTATION: Populate '## Ambiguity Lock Register' with an itemized table grounding "
        "speaker attribution and route ambiguous turns to operator review via `ask_user_question`."
    )
    parsed["actionable_remediation_diff"] = f"{existing_diff}\n{alr_diff}".strip() if existing_diff else alr_diff


def parse_timestamp_to_seconds(ts_str: str) -> Optional[int]:
    """Parse HH:MM:SS or MM:SS timestamp string to integer seconds."""
    parts = ts_str.strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return None
    return None


def _evaluate_timestamp_collapse_metrics(
    ts_matches: List[str], total_turns: int, metrics: Dict[str, Any], defects: List[str], defect_codes: List[str]
) -> None:
    if total_turns < 10:
        return
    if not ts_matches:
        defects.append("Missing timestamps across all dialogue turns.")
        defect_codes.append("ERR_MISSING_TIMESTAMPS")
        return
    unique_ts_count = len(set(ts_matches))
    unique_ratio = unique_ts_count / total_turns
    zero_count = sum(1 for ts in ts_matches if parse_timestamp_to_seconds(ts) == 0)
    zero_ratio = zero_count / total_turns
    metrics["unique_ratio"], metrics["zero_ratio"] = unique_ratio, zero_ratio
    if unique_ratio < 0.25 or zero_ratio > 0.15:
        defects.append(
            f"Timestamp collapse detected: {unique_ts_count}/{total_turns} unique timestamps ({unique_ratio*100:.1f}%), "
            f"{zero_count}/{total_turns} zero-timestamps ({zero_ratio*100:.1f}%). Must have >=25% unique and <=15% zero."
        )
        defect_codes.append("ERR_TIMESTAMP_COLLAPSE")


def check_timestamp_integrity(f1_text: str) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Verify chronological monotonicity, non-collapse, and standard format for timestamps."""
    body = _extract_forensic_body(f1_text)
    ts_matches = re.findall(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]", body)
    turns = [m.group(3).strip() for m in UNIVERSAL_TURN_PATTERN.finditer(body) if _is_valid_speaker_turn(m.group(3))]
    total_turns, total_ts = len(turns), len(ts_matches)
    defects: List[str] = []
    defect_codes: List[str] = []
    metrics: Dict[str, Any] = {"total_turns": total_turns, "total_timestamps": total_ts, "unique_timestamps": len(set(ts_matches))}
    _evaluate_timestamp_collapse_metrics(ts_matches, total_turns, metrics, defects, defect_codes)
    parsed_secs = [(ts, sec) for ts in ts_matches if (sec := parse_timestamp_to_seconds(ts)) is not None]
    inversions = [f"{parsed_secs[i - 1][0]} -> {parsed_secs[i][0]}" for i in range(1, len(parsed_secs)) if parsed_secs[i][1] < parsed_secs[i - 1][1]]
    if inversions:
        defects.append(f"Non-monotonic timestamp inversion(s): {', '.join(inversions[:3])}")
        defect_codes.append("ERR_TIMESTAMP_INVERSION")
    metrics["defects"] = defects
    return len(defects) == 0, defect_codes, metrics


def apply_timestamp_penalty(parsed: Dict[str, Any], f1_text: str) -> None:
    """Evaluate timestamp integrity deterministically and apply hard floor penalty on collapse/inversion."""
    passed, defect_codes, metrics = check_timestamp_integrity(f1_text)
    if not passed:
        parsed["dimension_3_chronological_and_timestamp_integrity"] = 5
        defects_str = "; ".join(metrics.get("defects", []))
        existing = parsed.get("forensic_rationale", "")
        parsed["forensic_rationale"] = f"TIMESTAMP DEFECT: {defects_str}\n{existing}".strip()
        parsed.setdefault("defect_codes", []).extend(defect_codes)
        parsed["actionable_remediation_diff"] = (
            "Re-align timestamps monotonically from source with discrete HH:MM:SS timestamps per turn. "
            "Collapsed timestamps (<25% unique or >15% zero) and inversions fail closed."
        )
    else:
        defect_codes_list = parsed.get("defect_codes") or []
        has_gap_defect = any("GAP" in str(c).upper() for c in defect_codes_list)
        if not has_gap_defect or "[Recording gap:" not in f1_text:
            parsed["dimension_3_chronological_and_timestamp_integrity"] = 25
        else:
            llm_d3 = parsed.get("dimension_3_chronological_and_timestamp_integrity")
            parsed["dimension_3_chronological_and_timestamp_integrity"] = 25 if llm_d3 is None else min(25, int(llm_d3))


def _scan_turn_disfluencies(
    t_clean: str, ts: str, filler_re: Any, stutter_re: Any, word_repeat_re: Any
) -> Tuple[List[str], List[str]]:
    fillers = [f"[{ts}] {m.group(0)}" for m in filler_re.finditer(t_clean)]
    stutters = [f"[{ts}] {m.group(0)}" for m in stutter_re.finditer(t_clean)]
    repeats = [f"[{ts}] {m.group(0)}" for m in word_repeat_re.finditer(t_clean) if m.group(1).lower() not in ("that", "had", "can")]
    return fillers, stutters + repeats


def check_organic_signal_hygiene(f1_text: str) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Verify that Output 01 is free of non-lexical vocalized fillers, stutters, and unpruned passive backchannels."""
    from dual_pipeline.transcript_qa_core.gate1_helpers import _extract_all_turns
    from dual_pipeline.transcript_stages_core.organic_signal_scrubber import VOCAL_FILLERS, is_passive_backchannel, is_question_turn  # noqa: lazy-decouple

    turns = _extract_all_turns(_extract_forensic_body(f1_text))
    filler_tokens = "|".join(re.escape(f) for f in sorted(VOCAL_FILLERS, key=len, reverse=True))
    filler_re = re.compile(rf"\b(?:{filler_tokens})\b", re.IGNORECASE)
    stutter_re = re.compile(r"\b([A-Za-z]+)\s*-\s*\1\b", re.IGNORECASE)
    word_repeat_re = re.compile(r"\b([A-Za-z]{2,})\s+\1\b", re.IGNORECASE)

    filler_matches, stutter_matches, passive_backchannels = [], [], []
    prev_was_question = False
    for ts, spk, text in turns:
        t_clean = text.strip()
        if not t_clean:
            continue
        f_list, s_list = _scan_turn_disfluencies(t_clean, ts, filler_re, stutter_re, word_repeat_re)
        filler_matches.extend(f_list)
        stutter_matches.extend(s_list)
        if is_passive_backchannel(t_clean) and not prev_was_question:
            passive_backchannels.append(f"[{ts}] {spk}: {t_clean}")
        prev_was_question = is_question_turn(t_clean)

    defects, defect_codes = [], []
    total_defects = len(filler_matches) + len(stutter_matches) + len(passive_backchannels)
    metrics: Dict[str, Any] = {
        "filler_count": len(filler_matches),
        "stutter_count": len(stutter_matches),
        "backchannel_count": len(passive_backchannels),
        "total_disfluencies": total_defects,
    }
    if filler_matches:
        defects.append(f"Surviving vocalized fillers ({len(filler_matches)}): {', '.join(filler_matches[:3])}")
        defect_codes.append("ERR_UNRESOLVED_DISFLUENCIES")
    if stutter_matches:
        defects.append(f"Surviving stutters/repetitions ({len(stutter_matches)}): {', '.join(stutter_matches[:3])}")
        defect_codes.append("ERR_UNRESOLVED_DISFLUENCIES")
    if passive_backchannels:
        defects.append(f"Unpruned passive backchannels ({len(passive_backchannels)}): {', '.join(passive_backchannels[:3])}")
        defect_codes.append("ERR_PASSIVE_BACKCHANNELS")
    metrics["defects"] = defects
    return total_defects == 0, list(dict.fromkeys(defect_codes)), metrics


def apply_organic_signal_penalty(parsed: Dict[str, Any], f1_text: str) -> None:
    """Evaluate organic signal hygiene deterministically and deduct Dimension 4 on surviving disfluencies."""
    passed, defect_codes, metrics = check_organic_signal_hygiene(f1_text)
    if not passed:
        total = metrics.get("total_disfluencies", 0)
        deduct = 5 if total <= 3 else (10 if total <= 6 else 13)
        orig_d4 = parsed.get("dimension_4_asr_cleanup_and_domain_preservation")
        base_d4 = 25 if orig_d4 is None else int(orig_d4)
        parsed["dimension_4_asr_cleanup_and_domain_preservation"] = max(5, min(base_d4, 25 - deduct))
        defects_str = "; ".join(metrics.get("defects", []))
        existing = parsed.get("forensic_rationale", "")
        parsed["forensic_rationale"] = f"ORGANIC SIGNAL DEFECT: {defects_str}\n{existing}".strip()
        parsed.setdefault("defect_codes", []).extend(defect_codes)
        remed_diff = (
            "Run Stage 01 organic signal scrubber to eliminate non-lexical fillers ('um', 'uh', 'ugh'), "
            "collapse stutters, and prune standalone passive backchannels."
        )
        existing_diff = parsed.get("actionable_remediation_diff", "")
        parsed["actionable_remediation_diff"] = f"{existing_diff}\n{remed_diff}".strip() if existing_diff else remed_diff
    elif parsed.get("dimension_4_asr_cleanup_and_domain_preservation") is None:
        parsed["dimension_4_asr_cleanup_and_domain_preservation"] = 25


def _build_timestamp_regex_pattern(ts: str) -> str:
    ts_parts = ts.strip().split(":")
    try:
        if len(ts_parts) == 2:
            _h, _m, _s = 0, int(ts_parts[0]), int(ts_parts[1])
        elif len(ts_parts) == 3:
            _h, _m, _s = int(ts_parts[0]), int(ts_parts[1]), int(ts_parts[2])
        else:
            return re.escape(ts)
        _pats = [f"{_h:02d}:{_m:02d}:{_s:02d}", f"{_m:02d}:{_s:02d}", f"{_m}:{_s:02d}"]
        if _h > 0:
            _pats.append(f"{_h}:{_m:02d}:{_s:02d}")
        return r"(?:" + "|".join(re.escape(p) for p in dict.fromkeys(_pats)) + r")"
    except (ValueError, TypeError):
        return re.escape(ts)


def _is_source_turn_present(ts: str, spk: str, utt: str, f1_text: str, f1_words: str, speaker_map: Dict[str, Any]) -> bool:
    utt_clean_l = utt.lower().rstrip(".!")
    if utt_clean_l in ("thank you for watching", "thank you for joining us", "thanks for watching", "please subscribe", "like and subscribe"):
        return True
    if len(utt) < 3 or utt.lower().startswith(("recording gap", "natural pause", "session pause")):
        return True
    ts_re = _build_timestamp_regex_pattern(ts)
    esc_spk, esc_mapped = re.escape(spk), re.escape(str(speaker_map.get(spk, spk)))
    spk_pat = rf"(?:{esc_spk}|{esc_mapped})\b"
    turn_re = rf"(?m)^\s*(?:\[{ts_re}\]\s*(?:\*\*)?{spk_pat}|(?:\*\*)?{spk_pat}[^\n]*?\[{ts_re}\]|#{{1,4}}\s*\[{ts_re}\]\s*(?:$|(?:\n+\s*)?(?:\*\*)?{spk_pat}))"
    if re.search(turn_re, f1_text, re.IGNORECASE):
        return True
    utt_words = " ".join(re.sub(r"[^\w\s]", " ", utt).lower().split())
    if not utt_words or utt_words in f1_words:
        return True
    alias_map = {"coro": "corrow", "sabat": "sabet", "cavani": "gavagni", "sw1": "sow1", "ssw": "sow"}
    utt_no_fillers = " ".join(alias_map.get(w, w) for w in utt_words.split() if w not in ("um", "uh", "ugh", "er", "ah", "mm", "hmm"))
    return bool(utt_no_fillers and utt_no_fillers in f1_words)


def _find_omitted_source_turns(src_text: str, f1_text: str, speaker_map: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    src_turn_pattern = re.compile(
        r"(?:^|\n)\s*(?:\[(\d{1,2}:\d{2}(?::\d{2})?)\]|\*\*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\*\*)\s*(?:\*\*)?([^:\n]+?)(?:\*\*:|:\*\*|:\s*\*+|\s*:\s*\*+|\s*:)\s*([^\n]+)"
    )
    f1_words = " ".join(re.sub(r"[^\w\s]", " ", f1_text).lower().split())
    omitted: List[Tuple[str, str, str]] = []
    for m in src_turn_pattern.finditer(src_text):
        ts = m.group(1) or m.group(2)
        spk = m.group(3).strip().strip("*").rstrip(":").strip()
        utt = m.group(4).strip().lstrip("*").strip()
        if not _is_source_turn_present(ts, spk, utt, f1_text, f1_words, speaker_map):
            omitted.append((ts, spk, utt))
    return omitted


def check_dimension1_grounding(
    f1_text: str, metadata: Optional[Dict[str, Any]] = None
) -> Tuple[bool, List[str], List[str], int]:
    """Checks Dimension 1 (Forensic Grounding & Fidelity)."""
    defect_codes: List[str] = []
    rationales: List[str] = []
    total_deduct = 0
    body = _extract_forensic_body(f1_text)
    editorial_pats = (
        r"(?m)^\s*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*(?:\*\*)?(?:Editorial Summary|Platform Note|Editorial Commentary)(?:\*\*)?:",
        r"(?m)^\s*#{1,4}\s+(?:Editorial Commentary|Executive Editorial Summary)\b",
    )
    if any(re.search(pat, body) for pat in editorial_pats):
        total_deduct += 10
        defect_codes.append("ERR_UNGROUNDED_EDITORIAL_COMMENTARY")
        rationales.append("Ungrounded editorial commentary masquerading as verbatim dialogue turns in body (-10 points).")
    meta = metadata or {}
    src_text = meta.get("source_text") or meta.get("clean_transcript") or meta.get("raw_text")
    if src_text and isinstance(src_text, str):
        speaker_map = meta.get("speaker_map") if isinstance(meta.get("speaker_map"), dict) else {}
        omitted_turns = _find_omitted_source_turns(src_text, f1_text, speaker_map)
        if omitted_turns:
            pts = min(13, len(omitted_turns) * 3)
            total_deduct += pts
            defect_codes.append("ERR_OMITTED_SOURCE_TURNS")
            turn_summaries = [f"[{t[0]}] {t[1]}: '{t[2]}'" for t in omitted_turns[:3]]
            rationales.append(f"Omission of spoken turn(s) present in source text: {'; '.join(turn_summaries)} (-{pts} points).")
    return (total_deduct == 0), defect_codes, rationales, total_deduct


def apply_dimension1_penalty(
    parsed: Dict[str, Any], f1_text: str, metadata: Optional[Dict[str, Any]] = None
) -> None:
    """Evaluate Dimension 1 grounding and fidelity deterministically."""
    passed, defect_codes, rationales, deduct = check_dimension1_grounding(f1_text, metadata=metadata)
    orig_d1 = parsed.get("dimension_1_forensic_grounding_and_fidelity")
    base_d1 = 25 if orig_d1 is None else int(orig_d1)
    if not passed:
        parsed["dimension_1_forensic_grounding_and_fidelity"] = max(0, min(base_d1, 25 - deduct))
        parsed.setdefault("defect_codes", []).extend(defect_codes)
        existing = parsed.get("forensic_rationale", "")
        parsed["forensic_rationale"] = (f"{'; '.join(rationales)}\n{existing}").strip()
        remed_diff = (
            "Remove ungrounded platform/diarization editorial header lines and restore all verbatim "
            "dialogue turns present in reference source text."
        )
        existing_diff = parsed.get("actionable_remediation_diff", "")
        parsed["actionable_remediation_diff"] = f"{existing_diff}\n{remed_diff}".strip() if existing_diff else remed_diff
    elif orig_d1 is None:
        parsed["dimension_1_forensic_grounding_and_fidelity"] = 25


def compute_attribution_score(
    f1_text: str, metadata: Optional[Dict[str, Any]] = None
) -> Tuple[int, List[str], List[str]]:
    """Compute Dimension 2 attribution score deterministically from metrics."""
    score = 25
    defect_codes: List[str] = []
    rationales: List[str] = []
    is_collapsed, dom_spk, dom_cnt, total_turns = check_single_speaker_collapse(f1_text, metadata=metadata)
    if is_collapsed:
        pct = int(dom_cnt / total_turns * 100) if total_turns else 0
        defect_codes.append("ERR_SPEAKER_COLLAPSE")
        rationales.append(f"ATTRIBUTION FLOOR HARD FAILURE: Single-speaker collapse detected. Speaker '{dom_spk}' accounts for {dom_cnt}/{total_turns} ({pct}%) of dialogue turns.")
        return 5, defect_codes, rationales
    is_excessive, ratio, generic_cnt, total_cnt = check_generic_speaker_ratio(f1_text)
    if is_excessive:
        defect_codes.append("ERR_GENERIC_SPEAKER_RATIO")
        rationales.append(f"ATTRIBUTION FLOOR HARD FAILURE: {generic_cnt}/{total_cnt} ({ratio * 100:.1f}%) of dialogue turns are un-attributed generic speaker labels (>15% max threshold).")
        return 5, defect_codes, rationales
    if ratio > 0:
        deduct = min(10, int(round(ratio * 50)))
        score -= deduct
        rationales.append(f"Minor generic speaker tags: {generic_cnt}/{total_cnt} ({ratio*100:.1f}%, -{deduct} pts).")
    aff_violations = check_corporate_affiliation_leaks(f1_text, metadata=metadata)
    if aff_violations:
        score = min(score, 10)
        defect_codes.append("ERR_CORPORATE_AFFILIATION")
        rationales.append(f"Corporate affiliation leaks: {'; '.join(aff_violations)}")
    from dual_pipeline.transcript_qa_core.gate1_attribution import check_ambiguity_lock_register_and_speaker_hitl
    passed, _, failures = check_ambiguity_lock_register_and_speaker_hitl(f1_text, metadata or {})
    if not passed:
        score = min(score, 8)
        defect_codes.append("ERR_AMBIGUITY_LOCK_REGISTER")
        rationales.append(f"Ambiguity Lock Register defect: {'; '.join(failures)}")
    return max(0, min(25, score)), defect_codes, rationales


def _resolve_d2_with_collapse_status(d2_score: int, llm_d2: Any, parsed: Dict[str, Any], is_collapse_approved: bool) -> int:
    if is_collapse_approved:
        if isinstance(parsed.get("defect_codes"), list):
            parsed["defect_codes"] = [c for c in parsed["defect_codes"] if c != "ERR_SPEAKER_COLLAPSE"]
        if llm_d2 is not None and "collapse" in str(parsed.get("forensic_rationale", "")).lower() and int(llm_d2) <= 10:
            return d2_score
    return d2_score if llm_d2 is None else min(int(llm_d2), d2_score)


def _apply_d2_attribution_scoring(parsed: Dict[str, Any], f1_text: str, metadata: Optional[Dict[str, Any]]) -> None:
    d2_score, d2_defects, d2_rats = compute_attribution_score(f1_text, metadata=metadata)
    llm_d2 = parsed.get("dimension_2_attribution_and_room_mic_hygiene")
    try:
        from dual_pipeline.speaker_attribution_core.collapse_detector import _verify_collapse_approval_receipt
        is_collapse_approved = bool(metadata and _verify_collapse_approval_receipt(metadata))
    except Exception:
        is_collapse_approved = False
    parsed["dimension_2_attribution_and_room_mic_hygiene"] = _resolve_d2_with_collapse_status(d2_score, llm_d2, parsed, is_collapse_approved)
    if d2_defects:
        parsed.setdefault("defect_codes", []).extend(d2_defects)
        existing_rat = parsed.get("forensic_rationale", "")
        parsed["forensic_rationale"] = (f"{'; '.join(d2_rats)}\n{existing_rat}").strip()
        parsed["actionable_remediation_diff"] = (
            "HALT AUTONOMOUS MUTATION: Speaker attribution defect detected. "
            "Route dialogue turns to operator review via `ask_user_question` and record "
            "verified attributions in the Ambiguity Lock Register and hitl_decision_ledger.json."
        )


def _ensure_critique_diff_envelope(parsed: Dict[str, Any]) -> None:
    env = parsed.get("critique_diff_envelope")
    if isinstance(env, dict) and env.get("target_path"):
        return
    d1 = parsed.get("dimension_1_forensic_grounding_and_fidelity", 25) or 0
    d2 = parsed.get("dimension_2_attribution_and_room_mic_hygiene", 25) or 0
    target_path = "Forensic Transcript" if (int(d2) < 14 or int(d1) < 14) else "Transcript Dialogue Turns"
    parsed["critique_diff_envelope"] = {
        "artifact": "OUTPUT01",
        "target_path": target_path,
        "observed_defect": parsed.get("forensic_rationale", "Forensic evaluation defect"),
        "required_delta": parsed.get("actionable_remediation_diff", "Apply surgical remediation to dialogue turns"),
        "protected_paths": ["## Ambiguity Lock Register"],
        "forbidden_mutations": ["Do not fabricate speakers", "Do not alter verbatim locators", "Fabricating dialogue turns", "Dropping substantive utterances"],
    }


def assign_forensic_verdict(
    parsed: Dict[str, Any], f1_text: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    if metadata is not None:
        assert_no_u0_hitl_bypass(metadata)
    if f1_text:
        _apply_d2_attribution_scoring(parsed, f1_text, metadata)
        apply_dimension1_penalty(parsed, f1_text, metadata=metadata)
        apply_timestamp_penalty(parsed, f1_text)
        apply_organic_signal_penalty(parsed, f1_text)
    from dual_pipeline.prompts.contracts import OUTPUT1_CONTRACT
    floors = OUTPUT1_CONTRACT.get_dimension_floors()
    d_keys = [dim.key for dim in OUTPUT1_CONTRACT.rubric_dimensions]
    floor_failures = [
        f"{k}={parsed.get(k)} (floor={int(floors[k]) if floors[k].is_integer() else floors[k]})"
        for k in d_keys
        if parsed.get(k) is not None and floors.get(k) is not None and float(parsed[k]) < floors[k]
    ]
    has_all_dims = all(k in parsed for k in d_keys)
    score = sum(float(parsed[k]) for k in d_keys) if has_all_dims else (parsed.get("composite_score", 0) or 0)
    if has_all_dims:
        parsed["composite_score"] = score
    if floor_failures:
        parsed["verdict"] = "FAIL"
        existing = parsed.get("forensic_rationale", "")
        parsed["forensic_rationale"] = f"PER-DIMENSION FLOOR FAILURE: {', '.join(floor_failures)}.\n{existing}".strip()
    else:
        parsed["verdict"] = CANONICAL_RELEASE_POLICY.evaluate_output_verdict(1, score).value
    parsed["status"] = parsed["verdict"]
    _ensure_critique_diff_envelope(parsed)
    return parsed
