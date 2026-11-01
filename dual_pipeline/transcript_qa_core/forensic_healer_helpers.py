"""
dual_pipeline/transcript_qa_core/forensic_healer_helpers.py

Modular helper functions for Forensic Transcript (Output 1) QA healing.
Handles ALR normalization, participant roster affiliations, and unbound verification stripping.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def ensure_crosstalk_alr_rows(current: str, ch: List[str]) -> str:
    """Annotate simultaneous turns (consecutive turns sharing identical timestamps) in ALR."""
    if not ("## Ambiguity Lock Register" in current or "# Ambiguity Lock Register" in current):
        return current

    alr_split = re.split(r"(?m)(?=^#{1,3}\s+Ambiguity Lock Register\b)", current, maxsplit=1)
    body = alr_split[0]

    turn_list: List[Tuple[str, str]] = []
    current_ts = ""
    for line in body.splitlines():
        line_s = line.strip()
        ts_m = re.match(r"^#{1,3}\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\]", line_s)
        if ts_m:
            current_ts = ts_m.group(1)
            continue
        inline_ts_m = re.search(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]", line_s)
        if inline_ts_m:
            current_ts = inline_ts_m.group(1)
        spk_m = re.match(r"^\*\*([A-Za-z0-9_\s\.\-]+?)\*\*:", line_s)
        if spk_m and current_ts:
            turn_list.append((current_ts, spk_m.group(1).strip()))

    simultaneous_pairs: List[Tuple[str, str, str]] = []
    for i in range(len(turn_list) - 1):
        ts1, spk1 = turn_list[i]
        ts2, spk2 = turn_list[i + 1]
        if ts1 == ts2 and spk1 != spk2:
            simultaneous_pairs.append((ts1, spk1, spk2))

    if not simultaneous_pairs:
        return current

    alr_m = re.search(r"(#{1,3}\s+Ambiguity Lock Register\b[\s\S]*?\|---[^\n]*\n)", current)
    if not alr_m:
        return current

    alr_section = current[alr_m.end():]
    new_rows = []
    seen_timestamps: set[str] = set()
    for ts, spk1, spk2 in simultaneous_pairs:
        if ts in seen_timestamps:
            continue
        seen_timestamps.add(ts)
        if ts in alr_section and any(k in alr_section.lower() for k in ("overlap", "simultaneous", "crosstalk")):
            continue
        row_id = f"ALR-XTALK-{ts.replace(':', '')}"
        row = (
            f"| {row_id} | Same-timestamp overlapping / simultaneous exchanges ({spk1} & {spk2}) "
            f"| [{ts}] | stmt-xtalk | Consecutive turns recorded with identical timestamp [{ts}]; "
            f"distinct speaker blocks preserved. | Acoustic Overlap & Turn Disambiguation |\n"
        )
        new_rows.append(row)
        ch.append(f"Added ALR row for simultaneous turns at [{ts}] ({spk1} & {spk2})")

    if new_rows:
        insert_at = alr_m.end()
        current = current[:insert_at] + "".join(new_rows) + current[insert_at:]

    return current


def heal_alr_special_cases(current: str, ch: List[str]) -> str:
    """Ensures dynamic crosstalk and overlap disambiguation in ALR without meeting-specific hardcoding."""
    return ensure_crosstalk_alr_rows(current, ch)


def heal_unbound_alr_claims(current: str, meta: Dict[str, Any], ch: List[str]) -> str:
    """Verify cryptographic ALR receipt and sanitize unauthenticated claims when unbound."""
    if not (meta.get("hitl_receipt_bound") or meta.get("operator_signature")):
        try:
            from dual_pipeline.transcript_qa_core.gate1_alr import verify_alr_cryptographic_receipt
            alr_ok, alr_receipt, _ = verify_alr_cryptographic_receipt(current, meta)
            if alr_ok and alr_receipt.get("hitl_receipt_bound"):
                meta["hitl_receipt_bound"] = True
                meta["alr_cryptographic_receipt"] = alr_receipt
        except Exception:
            pass
    if not (meta.get("hitl_receipt_bound") or meta.get("operator_signature")):
        try:
            from dual_pipeline.transcript_qa_core.gate1_alr import OPERATOR_VERIFIED_PATTERNS
            for pat in OPERATOR_VERIFIED_PATTERNS:
                current = pat.sub("Canonical Grounding", current)
        except Exception:
            pass
        current = re.sub(r"\s*[\(\[]OPERATOR VERIFIED[\)\]]", "", current, flags=re.IGNORECASE)
        current = re.sub(r"\bOperator (?:Confirmed|Verified)\b", "Canonical Grounding", current, flags=re.IGNORECASE)
    return current


def heal_insufficient_alr_rows(current: str, meta: Dict[str, Any], ch: List[str]) -> str:
    """Expand ALR table if row count is below minimum."""
    if "## Ambiguity Lock Register" in current or "# Ambiguity Lock Register" in current:
        from dual_pipeline.transcript_qa_core.gate1_attribution import check_alr_row_count_minimum
        passed, actual, min_rows, _ = check_alr_row_count_minimum(current, meta)
        if not passed:
            from dual_pipeline.transcript_stages_core.lexical_normalizer import format_ambiguity_lock_register  # noqa: lazy-decouple
            speaker_map = meta.get("speaker_map", {})
            new_alr = format_ambiguity_lock_register(meta, speaker_map)
            alr_m = re.search(r"(?:^|\n)#{1,3}\s+Ambiguity Lock Register\b[\s\S]*?(?=\n#{1,3}\s+|\Z)", current)
            if alr_m:
                start, end = alr_m.start(), alr_m.end()
                prefix = "\n" if current[start:start+1] == "\n" else ""
                current = current[:start] + prefix + f"## Ambiguity Lock Register\n\n{new_alr}\n" + current[end:]
                ch.append(f"Healed Ambiguity Lock Register table: expanded from {actual} to {min_rows} rows")
    return current


def normalize_unbound_hitl_title(current: str, meta: Dict[str, Any], ch: List[str]) -> str:
    """Normalize unbound title and ALR basis when no cryptographic receipt is present."""
    if not (meta.get("hitl_receipt_bound") or meta.get("operator_signature")):
        if re.search(r"(?m)^#\s+(.*?)\s+--\s+HITL-Resolved Transcript\b", current):
            current = re.sub(r"(?m)^#\s+(.*?)\s+--\s+HITL-Resolved Transcript\b", r"# \1 -- Forensic Transcript", current)
            ch.append("Normalized unbound HITL title to '-- Forensic Transcript'")
        current = re.sub(r"\|\s*Verified Participant Roster Alignment\s*\|", "| Contextual Participant Roster Alignment |", current)
    return current


def reformat_roster_affiliations(current: str, ch: List[str]) -> str:
    """Heal corporate affiliation formatting in Participants section."""
    from dual_pipeline.llm_judge_core.forensic_checks import check_corporate_affiliation_leaks
    if not check_corporate_affiliation_leaks(current):
        return current

    part_m = re.search(r"(\*\*Participants:\*\*)(.*?)(?=\n\s*\n|\n---|\n#|\Z)", current, re.DOTALL)
    if not part_m:
        return current

    hdr, body = part_m.group(1), part_m.group(2)
    if not ("," in body and not body.strip().startswith("-")):
        return current

    raw_entries = [e.strip() for e in body.split(",") if e.strip()]
    from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity
    partner_advisory_members = []
    google_members = []
    client_members = []
    other_members = []
    for entry in raw_entries:
        clean_name = re.sub(r"\s*\(.*?\)", "", entry).strip()
        ent = resolve_person_entity(clean_name)
        org = (ent.org if ent else "") or ("Google" if any(k in entry.lower() for k in ("google", "gcp")) else "PartnerAdvisory")
        role = (ent.role if ent else "") or (re.search(r"\((.*?)\)", entry).group(1) if re.search(r"\((.*?)\)", entry) else "")
        formatted = f"- {clean_name} ({role}, {org})" if role and org not in role else f"- {clean_name} ({role or org})"
        if org.lower() == "partner_advisory":
            partner_advisory_members.append(formatted)
        elif org.lower() == "google":
            google_members.append(formatted)
        elif org.lower() in ("apex_global", "apex_global energy", "client"):
            client_members.append(formatted)
        else:
            other_members.append(formatted)

    new_roster_lines = ["\n"]
    if partner_advisory_members:
        new_roster_lines.append("PartnerAdvisory:")
        new_roster_lines.extend(partner_advisory_members)
    if google_members:
        if partner_advisory_members:
            new_roster_lines.append("")
        new_roster_lines.append("Google:")
        new_roster_lines.extend(google_members)
    if client_members:
        if partner_advisory_members or google_members:
            new_roster_lines.append("")
        new_roster_lines.append("Client:")
        new_roster_lines.extend(client_members)
    if other_members:
        new_roster_lines.extend(other_members)
    new_roster = "\n".join(new_roster_lines)
    current = current[:part_m.start()] + hdr + new_roster + current[part_m.end():]
    ch.append("Reformatted participant roster into structured bulleted list with corporate affiliations")
    return current


def interpolate_collapsed_timestamps(text: str) -> tuple[str, bool]:
    """Interpolate sparse turn timestamps linearly between anchors if collapsed (<25% unique)."""
    from dual_pipeline.llm_judge_core.forensic_checks import check_timestamp_integrity
    passed, defect_codes, metrics = check_timestamp_integrity(text)
    if "ERR_TIMESTAMP_COLLAPSE" not in defect_codes and metrics.get("unique_ratio", 1.0) >= 0.25:
        return text, False

    parts = re.split(r"(?:^|\n)#{1,3}\s+(?:Ambiguity Lock Register|Attribution Summary)\b", text, flags=re.IGNORECASE)
    dialogue_part = parts[0]
    suffix = text[len(dialogue_part):]

    lines = dialogue_part.splitlines()
    spk_re = re.compile(r"^(?:\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*)?(?:\*\*)?([A-Z][a-zA-Z\.\s]+?)(?:\*\*)?:\s*(.*)$")
    ts_hdr_re = re.compile(r"^(?:#{1,3}\s*)?\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?$")

    turns = []
    current_anchor = None

    for idx, line in enumerate(lines):
        ls = line.strip()
        m_ts = ts_hdr_re.match(ls)
        if m_ts and not any(c.isalpha() for c in ls):
            current_anchor = m_ts.group(1)
            continue
        m_spk = spk_re.match(ls)
        if m_spk:
            inline_ts = m_spk.group(1)
            if inline_ts:
                current_anchor = inline_ts
            turns.append({
                "turn_idx": len(turns),
                "speaker": m_spk.group(2).strip(),
                "text": m_spk.group(3).strip(),
                "anchor": current_anchor,
            })
            current_anchor = None

    if not turns:
        return text, False

    def to_sec(ts: str) -> int:
        p = list(map(int, ts.split(":")))
        return p[0] * 3600 + p[1] * 60 + p[2] if len(p) == 3 else p[0] * 60 + p[1]

    def to_ts(s: float) -> str:
        s_int = int(round(s))
        return f"{s_int // 3600:02d}:{(s_int % 3600) // 60:02d}:{s_int % 60:02d}"

    anchors = []
    for t in turns:
        if t["anchor"]:
            anchors.append((t["turn_idx"], to_sec(t["anchor"])))

    if not anchors:
        anchors = [(0, 0)]
    if anchors[0][0] != 0:
        anchors.insert(0, (0, max(0, anchors[0][1] - anchors[0][0] * 5)))
    if anchors[-1][0] != len(turns) - 1:
        last_turn = len(turns) - 1
        end_sec = anchors[-1][1] + (last_turn - anchors[-1][0]) * 5
        end_m = re.search(r"Transcription ended after\s*(\d{1,2}:\d{2}(?::\d{2})?)", text, re.IGNORECASE)
        if end_m:
            end_sec = max(end_sec, to_sec(end_m.group(1)))
        anchors.append((last_turn, end_sec))

    turn_times = {}
    for a_i in range(len(anchors) - 1):
        i1, s1 = anchors[a_i]
        i2, s2 = anchors[a_i + 1]
        for k in range(i1, i2):
            frac = (k - i1) / (i2 - i1) if i2 > i1 else 0
            turn_times[k] = s1 + frac * (s2 - s1)
    turn_times[len(turns) - 1] = anchors[-1][1]

    last_s = -1
    for k in range(len(turns)):
        s = turn_times[k]
        if s <= last_s:
            s = last_s + 1
            turn_times[k] = s
        last_s = s

    new_lines = []
    turn_ptr = 0
    for line in lines:
        ls = line.strip()
        m_ts = ts_hdr_re.match(ls)
        if m_ts and not any(c.isalpha() for c in ls):
            continue
        m_spk = spk_re.match(ls)
        if m_spk:
            ts_str = to_ts(turn_times[turn_ptr])
            new_lines.append(f"[{ts_str}] {m_spk.group(2).strip()}: {m_spk.group(3).strip()}")
            turn_ptr += 1
        else:
            new_lines.append(line)

    reconstructed = "\n".join(new_lines) + suffix
    return reconstructed, True


def zero_pad_timestamps(
    text: str,
    changes: Optional[List[str]] = None,
    audit_log: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Pad 2-part [MM:SS] timestamps to standard 3-part [00:MM:SS] format and bracket standalone timestamps."""
    def _bracket_standalone(m: re.Match) -> str:
        ts = m.group(1)
        parts = ts.split(":")
        if len(parts) == 2:
            return f"[00:{int(parts[0]):02d}:{int(parts[1]):02d}]"
        elif len(parts) == 3:
            return f"[{int(parts[0]):02d}:{int(parts[1]):02d}:{int(parts[2]):02d}]"
        return f"[{ts}]"

    current_standalone_pat = r"(?m)^(?:#{1,3}\s*)?(\d{1,2}:\d{2}(?::\d{2})?)\s*$"
    standalone_matches = list(re.finditer(current_standalone_pat, text))
    if standalone_matches:
        text = re.sub(current_standalone_pat, _bracket_standalone, text)
        desc = f"Bracketed {len(standalone_matches)} standalone timestamp headers"
        if changes is not None:
            changes.append(desc)
        if audit_log is not None:
            audit_log.append({
                "event": "timestamp_bracketed",
                "rule": "RULE-LOSSLESS-SYNTAX",
                "detail": desc,
                "timestamp": time.time(),
            })

    def _pad_bracketed(m: re.Match) -> str:
        mm = int(m.group(1))
        ss = int(m.group(2))
        return f"[00:{mm:02d}:{ss:02d}]"

    pattern = r"\[(\d{1,2}):(\d{2})\]"
    matches = list(re.finditer(pattern, text))
    if matches:
        text = re.sub(pattern, _pad_bracketed, text)
        desc = f"Zero-padded {len(matches)} [MM:SS] timestamps to [00:MM:SS]"
        if changes is not None:
            changes.append(desc)
        if audit_log is not None:
            audit_log.append({
                "event": "timestamp_zero_padded",
                "rule": "RULE-LOSSLESS-SYNTAX",
                "detail": desc,
                "timestamp": time.time(),
            })

    def _pad_gap(m: re.Match) -> str:
        pre = m.group(1)
        m1, s1 = int(m.group(2)), int(m.group(3))
        sep = m.group(4)
        m2, s2 = int(m.group(5)), int(m.group(6))
        post = m.group(7)
        return f"{pre}00:{m1:02d}:{s1:02d}{sep}00:{m2:02d}:{s2:02d}{post}"

    gap_pat = r"(\[(?:Recording gap|Natural pause|Session pause)[^\]]*?)(?<![:\d])(\d{1,2}):(\d{2})(?![:\d])([^\]]*?[–\-]\s*)(?<![:\d])(\d{1,2}):(\d{2})(?![:\d])([^\]]*?\])"
    text = re.sub(gap_pat, _pad_gap, text)

    return text


def _normalize_turn_timestamp(ts: str) -> Tuple[str, str]:
    """Returns (norm_ts [00:MM:SS], clean_ts [MM:SS or HH:MM:SS])."""
    parts = ts.split(":")
    if len(parts) == 2:
        norm_ts = f"00:{int(parts[0]):02d}:{int(parts[1]):02d}"
    elif len(parts) == 3:
        norm_ts = f"{int(parts[0]):02d}:{int(parts[1]):02d}:{int(parts[2]):02d}"
    else:
        norm_ts = ts
    clean_ts = norm_ts.lstrip("0:") if norm_ts.startswith("00:") else norm_ts
    return norm_ts, clean_ts


def _parse_ts_to_seconds(ts_str: str) -> Optional[int]:
    parts = ts_str.strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return None
    return None


def _timestamp_regex(ts: str) -> str:
    """Returns a regex matching variations of ts: [00:MM:SS], [MM:SS], [M:SS]."""
    parts = ts.strip().split(":")
    try:
        if len(parts) == 2:
            m, s = int(parts[0]), int(parts[1])
            h = 0
        elif len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        else:
            return re.escape(ts)
        pats = [
            f"{h:02d}:{m:02d}:{s:02d}",
            f"{m:02d}:{s:02d}",
            f"{m}:{s:02d}",
        ]
        if h > 0:
            pats.append(f"{h}:{m:02d}:{s:02d}")
        return r"(?:" + "|".join(re.escape(p) for p in dict.fromkeys(pats)) + r")"
    except (ValueError, TypeError):
        return re.escape(ts)


def _find_turn_in_text(text: str, ts: str, spk: str, utt: str) -> Optional[re.Match[str]]:
    """Locate turn by timestamp and speaker, falling back to speaker and utterance prefix."""
    ts_re = _timestamp_regex(ts)
    esc_spk = re.escape(spk)
    m = re.search(
        rf"(?m)^\s*\[{ts_re}\]\s*(?:\*\*)?{esc_spk}\b",
        text,
        re.IGNORECASE,
    )
    if not m:
        m = re.search(
            rf"(?m)^\s*(?:\*\*)?{esc_spk}\b[^\n]*?\[{ts_re}\]",
            text,
            re.IGNORECASE,
        )
    if not m:
        m = re.search(
            rf"(?m)^\s*#{{1,4}}\s*\[{ts_re}\]\s*(?:\*\*)?{esc_spk}\b",
            text,
            re.IGNORECASE,
        )
    if not m:
        first_words = re.escape(" ".join(utt.split()[:4]))
        if first_words and len(first_words) >= 10:
            m = re.search(
                rf"(?m)^\s*\[\d{{1,2}}:\d{{2}}(?::\d{{2}})?\]\s*(?:\*\*)?{esc_spk}\b[^\n]*?{first_words}",
                text,
                re.IGNORECASE,
            )
    return m


def restore_omitted_source_turns(
    text: str,
    metadata: Optional[Dict[str, Any]] = None,
    changes: Optional[List[str]] = None,
    audit_log: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Restores short source turns present in source_text but dropped during synthesis."""
    if not metadata or not text:
        return text
    src = metadata.get("source_text") or metadata.get("clean_transcript") or metadata.get("raw_text")
    if not src or not isinstance(src, str):
        return text

    src_turn_pattern = re.compile(
        r"(?:^|\n)\s*(?:\[(\d{1,2}:\d{2}(?::\d{2})?)\]|\*\*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\*\*)\s*(?:\*\*)?([^:\n]+?)(?:\*\*:|:\*\*|:\s*\*+|\s*:\s*\*+|\s*:)\s*([^\n]+)"
    )
    src_turns = []
    for m in src_turn_pattern.finditer(src):
        ts = m.group(1) or m.group(2)
        spk = m.group(3).strip().strip("*").rstrip(":").strip()
        utt = m.group(4).strip().lstrip("*").strip()
        utt_clean_l = utt.lower().rstrip(".!")
        if utt_clean_l in (
            "thank you for watching",
            "thank you for joining us",
            "thanks for watching",
            "please subscribe",
            "like and subscribe",
        ):
            continue
        if utt and not utt.lower().startswith(("recording gap", "natural pause", "session pause")):
            src_turns.append((ts, spk, utt))

    if not src_turns:
        return text

    ch = changes if changes is not None else []
    al = audit_log if audit_log is not None else []

    f1_words = " ".join(re.sub(r"[^\w\s]", " ", text).lower().split())

    speaker_map = metadata.get("speaker_map") if isinstance(metadata.get("speaker_map"), dict) else {}

    for i, (ts, spk, utt) in enumerate(src_turns):
        norm_ts, clean_ts = _normalize_turn_timestamp(ts)
        ts_re = _timestamp_regex(ts)
        mapped_spk = speaker_map.get(spk, spk)
        esc_spk = re.escape(spk)
        esc_mapped = re.escape(mapped_spk)
        has_turn = bool(
            re.search(rf"(?m)^\s*\[{ts_re}\]\s*(?:\*\*)?(?:{esc_spk}|{esc_mapped})\b", text, re.IGNORECASE)
            or re.search(rf"(?m)^\s*(?:\*\*)?(?:{esc_spk}|{esc_mapped})\b[^\n]*?\[{ts_re}\]", text, re.IGNORECASE)
            or re.search(rf"(?m)^\s*#{{1,4}}\s*\[{ts_re}\]\s*(?:\*\*)?(?:{esc_spk}|{esc_mapped})\b", text, re.IGNORECASE)
            or re.search(rf"(?m)^\s*#{{1,4}}\s*\[{ts_re}\]\s*$", text)
            or re.search(rf"(?m)^\s*\*\*\[{ts_re}\]\*\*\s*(?:\*\*)?(?:{esc_spk}|{esc_mapped})\b", text, re.IGNORECASE)
        )
        if has_turn:
            continue

        utt_words = " ".join(re.sub(r"[^\w\s]", " ", utt).lower().split())
        utt_no_fillers = " ".join(
            w for w in utt_words.split() if w not in ("um", "uh", "ugh", "er", "ah", "mm", "hmm")
        )
        if len(utt_words) < 3:
            continue
        if utt_words in f1_words or (utt_no_fillers and utt_no_fillers in f1_words):
            continue

        turn_sec = _parse_ts_to_seconds(norm_ts)
        turn_line = f"[{norm_ts}] **{spk}:** {utt}"

        inserted = False
        for next_idx in range(i + 1, len(src_turns)):
            next_ts, next_spk, next_utt = src_turns[next_idx]
            next_sec = _parse_ts_to_seconds(next_ts)
            if turn_sec is not None and next_sec is not None and turn_sec > next_sec:
                continue
            next_words = " ".join(re.sub(r"[^\w\s]", " ", next_utt).lower().split())
            if next_words and next_words[:20] in f1_words:
                m_next = _find_turn_in_text(text, next_ts, next_spk, next_utt)
                if m_next:
                    pos = m_next.start()
                    text = text[:pos] + f"{turn_line}\n\n" + text[pos:]
                    inserted = True
                    desc = f"Restored omitted source turn: '{turn_line}'"
                    ch.append(desc)
                    al.append({"event": "turn_restored", "rule": "RULE-LOSSLESS-SYNTAX", "detail": desc, "timestamp": time.time()})
                    f1_words = " ".join(re.sub(r"[^\w\s]", " ", text).lower().split())
                    break

        if not inserted:
            for prev_idx in range(i - 1, -1, -1):
                prev_ts, prev_spk, prev_utt = src_turns[prev_idx]
                prev_sec = _parse_ts_to_seconds(prev_ts)
                if turn_sec is not None and prev_sec is not None and turn_sec < prev_sec:
                    continue
                prev_words = " ".join(re.sub(r"[^\w\s]", " ", prev_utt).lower().split())
                if prev_words and prev_words[:20] in f1_words:
                    m_prev = _find_turn_in_text(text, prev_ts, prev_spk, prev_utt)
                    if m_prev:
                        pos = m_prev.end()
                        next_turn_m = re.search(r"\n\s*(?:\[|\*\*|##|\Z)", text[pos:])
                        end_pos = (pos + next_turn_m.start()) if next_turn_m else len(text)
                        text = text[:end_pos].rstrip() + f"\n\n{turn_line}\n\n" + text[end_pos:].lstrip()
                        inserted = True
                        desc = f"Restored omitted source turn: '{turn_line}'"
                        ch.append(desc)
                        al.append({"event": "turn_restored", "rule": "RULE-LOSSLESS-SYNTAX", "detail": desc, "timestamp": time.time()})
                        f1_words = " ".join(re.sub(r"[^\w\s]", " ", text).lower().split())
                        break

    return text


