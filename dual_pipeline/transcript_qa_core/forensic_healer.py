"""
dual_pipeline/transcript_qa_core/forensic_healer.py

Deterministic lossless syntactic and verbatim normalizer for Output 01 (Forensic Transcript).
Enforces verbatim dialogue preservation, prevents editorial metadata pollution inside spoken turns,
ensures corporate affiliation fidelity, and restores substantive turns.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.transcript_qa_core.forensic_healer_helpers import (
    heal_alr_special_cases,
    heal_insufficient_alr_rows,
    heal_unbound_alr_claims,
    interpolate_collapsed_timestamps,
    normalize_unbound_hitl_title,
    reformat_roster_affiliations,
    restore_omitted_source_turns,
    zero_pad_timestamps,
)

logger = logging.getLogger(__name__)
_BROADCAST_INLINE_PAT = re.compile(r"(\s*This is a production of [^\n.!?]+(?:,\s*LLC)?\.?)", re.IGNORECASE)
_BROADCAST_TURN_PAT = re.compile(r"(?m)^###\s*\[(\d{2}:\d{2}:\d{2})\]\s*\n+[^\n]*This is a production of [^\n]+", re.IGNORECASE)
_MAXIMO_HOMOPHONE_PAT = re.compile(r"\b(written\s+to|in|writing\s+back\s+to|back\s+to|into)\s+[Mm]aximum(\b|\s+fields?|\s+have\s+a\s+field)")
_MAXIMO_WRITEBACK_PAT = re.compile(r"\b(?:maximum|maximal|Maxwell|Maximo)\s+(?:right|write)[-\s]+back\b", re.IGNORECASE)


def _strip_inline_broadcast_credits(text: str) -> Tuple[str, List[str]]:
    if "this is a production of" not in text.lower():
        return text, []
    locs = [m.group(1) for m in _BROADCAST_TURN_PAT.finditer(text)]
    return _BROADCAST_INLINE_PAT.sub("", text), (locs or ["inline-broadcast-credit"])


def _apply_phonetic_and_maximo_maps(current: str, meta: Dict[str, Any], ch: List[str]) -> str:
    if any(meta.get(k) for k in ("phonetic_corrections", "phonetic_map", "lexicon_map")):
        from dual_pipeline.transcript_stages_core.lexical_normalizer import _apply_phonetic_maps  # noqa: lazy-decouple
        phonetic_norm = _apply_phonetic_maps(current, meta)
        if phonetic_norm != current:
            current = phonetic_norm
            ch.append("Applied phonetic and domain homophone normalizations")
    low_cur = current.lower()
    if "maximum" in low_cur or "maximal" in low_cur or "maximo" in low_cur:
        maximo_norm = _MAXIMO_HOMOPHONE_PAT.sub(r"\1 Maximo\2", _MAXIMO_WRITEBACK_PAT.sub("Maximo write-back", current))
        if maximo_norm != current:
            current = maximo_norm
            ch.append("Normalized ASR homophone 'maximum/Maximum' to 'Maximo' in system field context")
    return current


def _iso_to_us_date(iso_str: str) -> str:
    s = iso_str.strip()
    m_iso = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", s)
    if m_iso:
        return f"{int(m_iso.group(2)):02d}/{int(m_iso.group(3)):02d}/{m_iso.group(1)}"
    m_us = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$", s)
    if m_us:
        return f"{int(m_us.group(1)):02d}/{int(m_us.group(2)):02d}/{m_us.group(3)}"
    return iso_str


def heal_header_date(
    text: str,
    target_date: Optional[str],
    changes: List[str],
    audit_log: List[Dict[str, Any]],
) -> str:
    if not target_date:
        m_existing = re.search(r"(?:\*\*Date:?\*\*:?|Date:)\s*([^\n\r]+)", text)
        if m_existing:
            target_date = m_existing.group(1).strip()
    if not target_date:
        return text

    us_date = _iso_to_us_date(target_date)
    has_bold_us_date = bool(re.search(rf"\*\*Date:\*\*\s*{re.escape(us_date)}", text))
    if has_bold_us_date:
        return text

    desc: Optional[str] = None
    if "**Date:**" in text:
        text = re.sub(r"\*\*Date:\*\*.*", f"**Date:** {us_date}", text, count=1)
        desc = f"Fixed header date to **Date:** {us_date}"
        changes.append(desc)
    elif re.search(r"(?m)^Date:.*", text):
        text = re.sub(r"(?m)^Date:.*", f"**Date:** {us_date}", text, count=1)
        desc = f"Fixed header date to **Date:** {us_date}"
        changes.append(desc)
    else:
        m_subj = re.search(r"^#\s*[^\n]+\n+", text, re.MULTILINE)
        if m_subj:
            text = text[:m_subj.end()] + f"**Date:** {us_date}\n\n" + text[m_subj.end():]
            desc = f"Injected missing header date **Date:** {us_date}"
            changes.append(desc)
    if desc:
        audit_log.append({"event": "header_normalized", "rule": "RULE-LOSSLESS-SYNTAX", "detail": desc, "timestamp": time.time()})
    return text


def heal_recording_gaps(
    text: str,
    changes: List[str],
    audit_log: List[Dict[str, Any]],
) -> str:
    """Inject standard Rule C-11 recording gap notations for unannotated gaps (matching Gate 1.5)."""
    alr_split = re.split(r"(?m)(?=^#{1,3}\s+Ambiguity Lock Register\b)", text, maxsplit=1)
    body = alr_split[0]
    tail = alr_split[1] if len(alr_split) > 1 else ""
    # Strip any stray recording gap/natural pause annotations that erroneously leaked into the ALR table
    if tail:
        tail = re.sub(r"(?m)^\s*\[(?:Recording gap|Natural pause|Session pause):[^\]]+\]\s*\n*", "", tail)

    # Ensure every turn header is preceded by newlines only if consecutive turns were merged on a single line
    new_body = re.sub(
        r"(?<=[^\r\n])\s*(#{1,3}\s*\[\d{1,2}:\d{2}(?::\d{2})?\]|\*\*\[\d{1,2}:\d{2}(?::\d{2})?\]\*\*|\[\d{1,2}:\d{2}(?::\d{2})?\]\s*\*\*[A-Z])",
        r"\n\n\1",
        body,
    )
    if new_body != body:
        changes.append("Separated merged turns on same line")
        audit_log.append({"event": "separated_merged_turns", "rule": "RULE-LOSSLESS-SYNTAX", "timestamp": time.time()})
        body = new_body

    matches = list(re.finditer(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]", body))
    parsed = []
    for m in matches:
        p = m.group(1).split(":")
        sec = (
            int(p[0]) * 60 + int(p[1])
            if len(p) == 2
            else (int(p[0]) * 3600 + int(p[1]) * 60 + int(p[2]) if len(p) == 3 else None)
        )
        if sec is not None:
            parsed.append((sec, m.start(), m.end(), m.group(1)))

    for i in range(len(parsed) - 2, -1, -1):
        t1_sec, _, t1_end, t1_str = parsed[i]
        t2_sec, t2_start, _, t2_str = parsed[i + 1]
        delta = t2_sec - t1_sec
        turn_text = body[t1_end:t2_start].strip()
        from .gate1 import _is_unannotated_gap
        if _is_unannotated_gap(turn_text, delta):
            p1 = t1_str.split(":")
            s1_str = f"00:{int(p1[0]):02d}:{int(p1[1]):02d}" if len(p1) == 2 else t1_str
            p2 = t2_str.split(":")
            s2_str = f"00:{int(p2[0]):02d}:{int(p2[1]):02d}" if len(p2) == 2 else t2_str
            if delta >= 180:
                gap_note = f"\n\n[Recording gap: {s1_str} – {s2_str}]\n\n"
            else:
                gap_note = f"\n\n[Natural pause: {s1_str} – {s2_str} (no speech)]\n\n"
            line_start = body.rfind("\n", 0, t2_start)
            insert_pos = line_start if line_start != -1 else t2_start
            body = body[:insert_pos].rstrip() + gap_note + body[insert_pos:].lstrip()
            desc = f"Injected Rule C-11 standard recording gap annotation: [{s1_str} – {s2_str}] ({delta}s gap)"
            changes.append(desc)
            audit_log.append({
                "event": "gap_annotation_normalized",
                "rule": "RULE-LOSSLESS-SYNTAX",
                "detail": desc,
                "timestamp": time.time(),
            })

    if parsed:
        t0_sec, t0_start, _, t0_str = parsed[0]
        if t0_sec >= 180:
            preceding_body = body[:t0_start]
            if not re.search(r"\[(?:Recording gap|Natural pause):\s*00:00:00", preceding_body):
                formatted_t0 = f"00:{t0_str}" if len(t0_str.split(":")) == 2 else t0_str
                gap_note = f"\n\n[Recording gap: 00:00:00 – {formatted_t0}]\n\n"
                line_start = body.rfind("\n", 0, t0_start)
                insert_pos = line_start if line_start != -1 else t0_start
                body = body[:insert_pos] + gap_note + body[insert_pos:]
                desc = f"Injected Rule C-11 standard recording gap annotation: [00:00:00 – {formatted_t0}] ({t0_sec}s opening gap)"
                changes.append(desc)
                audit_log.append({
                    "event": "gap_annotation_normalized",
                    "rule": "RULE-LOSSLESS-SYNTAX",
                    "detail": desc,
                    "timestamp": time.time(),
                })
    return body + tail


def purge_asr_outro_hallucinations(
    text: str,
    changes: Optional[List[str]] = None,
    audit_log: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, List[str]]:
    """Purge YouTube-style ASR outro hallucinations and subscribe artifacts."""
    outro_phrases = [
        "Thank you for watching.", "Thank you for watching!", "Thank you for joining us.", "Thank you for joining us!",
        "Please subscribe.", "Like and subscribe.", "Thanks for watching.",
        "This is a production of the Center for Autism and Related Disorders, LLC.",
    ]
    current_text, purged_locators = _strip_inline_broadcast_credits(text)
    outro_combo_pat = (
        r"(?m)^###\s*\[(\d{2}:\d{2}:\d{2})\]\s*\n+\*\*[^*:\n]+:\*\*\s*"
        r"(?:(?:Thank you for watching[.!]|Please subscribe[.]|Like and subscribe[.]|Thanks for watching[.])\s*)+\n*"
    )
    for match in re.finditer(outro_combo_pat, current_text):
        purged_locators.append(match.group(1))
    current_text = re.sub(outro_combo_pat, "", current_text)
    for phrase in outro_phrases:
        pattern = rf"(###\s*\[(\d{{2}}:\d{{2}}:\d{{2}})\]\s*\n+\*\*[^*:\n]+:\*\*\s*{re.escape(phrase)}\s*\n+)"
        for match in re.finditer(pattern, current_text):
            purged_locators.append(match.group(2))
        current_text = re.sub(pattern, "", current_text)

        trailing_pattern = rf"(\s*{re.escape(phrase)})(?=\s*(?:\n+###|\Z))"
        current_text = re.sub(trailing_pattern, "", current_text)

    # Purge any turns left empty after outro removal
    empty_turn_pat = r"(?m)^###\s*\[\d{2}:\d{2}:\d{2}\]\s*\n+\*\*[^*:\n]+:\*\*\s*(?=\n*###|\Z)"
    current_text = re.sub(empty_turn_pat, "", current_text)

    # Purge recording metadata turns erroneously emitted as spoken dialogue
    system_notice_pat = r"(?m)(?:^###\s*\[\d{2}:\d{2}:\d{2}\]\s*\n+)?\*\*[^*:\n]+:\*\*\s*(?:Stopped transcription|Started transcription)\.?\s*\n*"
    if re.search(system_notice_pat, current_text, flags=re.IGNORECASE):
        current_text = re.sub(system_notice_pat, "", current_text, flags=re.IGNORECASE)
        if changes is not None:
            changes.append("Purged system recording metadata turn ('Stopped/Started transcription')")

    # Purge repeated canned ASR startup hallucinations over silence
    canned_phrases = [
        "I'm going to go ahead and get started",
        "I’m going to go ahead and get started",
    ]
    for phrase in canned_phrases:
        pattern = rf"(?m)^\*+\[(\d{{2}}:\d{{2}}:\d{{2}})\]\*+\s+[^:\n]+:\s*{re.escape(phrase)}[.,]?\s*\n+"
        for match in re.finditer(pattern, current_text):
            purged_locators.append(match.group(1))
        current_text = re.sub(pattern, "", current_text)

    if purged_locators and changes is not None:
        changes.append(f"Purged ASR outro hallucinations at locators: {', '.join(purged_locators)}")
    if purged_locators and audit_log is not None:
        audit_log.append({"event": "asr_outro_purged", "rule": "RULE-LOSSLESS-SYNTAX", "detail": f"Purged ASR outro hallucinations at locators: {', '.join(purged_locators)}", "timestamp": time.time()})
    return current_text, purged_locators


def heal_output_1_forensic(
    text: str,
    metadata: Optional[Dict[str, Any]] = None,
    changes: Optional[List[str]] = None,
    audit_log: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Applies deterministic lossless syntactic and structural healing to Output 01 markdown transcript."""
    if not text:
        return text

    ch = changes if changes is not None else []
    al = audit_log if audit_log is not None else []
    meta = metadata if metadata is not None else {}

    current, _ = purge_asr_outro_hallucinations(text, ch, al)
    current = zero_pad_timestamps(current, ch, al)

    # 1. Dialogue Hygiene: Strip generic repetitive ASR startup hallucinations
    asr_startup_patterns = [
        (r"(?i)\bI'm going to go ahead and start the video\.?\s*(?:and\s+more\.?\s*|\s*and\s*)?", ""),
        (r"(?i)\bI'm going to go ahead and start the recording\.?\s*", ""),
    ]
    for pat_str, repl_str in asr_startup_patterns:
        if re.search(pat_str, current):
            current = re.sub(pat_str, repl_str, current)
            ch.append(f"Purged ASR startup artifact: '{pat_str}'")

    # 1b. Organic Signal & Disfluency Normalization
    from dual_pipeline.transcript_stages_core.organic_signal_scrubber import scrub_organic_signal  # noqa: lazy-decouple
    is_diarized = str(meta.get("diarization_status", "")).upper() in ("NATIVE", "DIARIZED_ACOUSTIC_RESOLVED", "VERIFIED_STEREO_SEPARATION")
    scrubbed = scrub_organic_signal(current, preserve_verbatim_turns=is_diarized)
    if scrubbed != current:
        current = scrubbed
        ch.append("Enriched Output 01 organic signal and pruned disfluencies/backchannels")

    # 2. Extract and protect GraphDB SSOT and roster stakeholder names
    roster_names: List[str] = []
    attendees = meta.get("attendees") or meta.get("roster") or []
    if isinstance(attendees, list):
        for att in attendees:
            n = att.get("name") if isinstance(att, dict) else str(att)
            if n:
                roster_names.append(n.strip().lower())
    elif isinstance(attendees, dict):
        for org_atts in attendees.values():
            if isinstance(org_atts, list):
                for att in org_atts:
                    n = att.get("name") if isinstance(att, dict) else str(att)
                    if n:
                        roster_names.append(n.strip().lower())

    # 2b. Universal Sanitization of Ungrounded Platform / Diarization Editorial Notes
    editorial_pats = [
        (r"(?m)^\s*(?:\*\*)?(?:Platform Reference|Diarization Limitation Notice|Recording Platform|Diarization Status)(?:\*\*)?:.*?\n+", "Removed ungrounded platform/diarization metadata line"),
        (r"(?m)^\s*#{1,4}\s+(?:Diarization Limitation Notice|Platform Reference|Editorial Commentary|Executive Editorial Summary|Evidence Locks[^\n]*)\b.*?\n+", "Removed ungrounded diarization limitation notice section header"),
        (r"(?m)^\s*#{1,4}\s+Evidence Locks[^\n]*\n[\s\S]*?(?=\n#{1,4}\s+|\Z)", "Removed ungrounded evidence locks / editorial constraints section"),
        (r"(?m)^\s*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*(?:\*\*)?(?:Editorial Summary|Platform Note|Editorial Commentary)(?:\*\*)?:.*?\n+", "Removed ungrounded editorial commentary turn"),
    ]
    for pat, desc in editorial_pats:
        if re.search(pat, current):
            current = re.sub(pat, "", current)
            ch.append(desc)

    # 3. Standardize Forensic Transcript heading
    if re.search(r"(?m)^#\s+(?:Live Meeting Transcript|Transcript)\b.*$", current):
        current = re.sub(r"(?m)^#\s+(?:Live Meeting Transcript|Transcript)\b.*$", "# Forensic Transcript", current)
        ch.append("Normalized transcript heading to '# Forensic Transcript'")

    # 4. Standardize turn timestamp syntax: **Speaker [HH:MM:SS]:** -> **Speaker:** [HH:MM:SS]
    norm_turns = re.sub(
        r"(?m)^\*\*([A-Za-z0-9][a-zA-Z0-9\s\-\.,/\(\)\'’ʼ]+?)\s*\[(\d{1,2}:\d{2}(?::\d{2})?[^\]]*)\]:\*\*",
        r"**\1:** [\2]",
        current,
    )
    if norm_turns != current:
        current = norm_turns
        ch.append("Normalized turn timestamp syntax from **Speaker [HH:MM:SS]:** to **Speaker:** [HH:MM:SS]")

    # 4b. Standardize bold turn colon syntax: [HH:MM:SS] **Speaker**: -> [HH:MM:SS] **Speaker:**
    current = re.sub(
        r"(?m)^(\s*(?:\[\d{1,2}:\d{2}(?::\d{2})?\]\s*)?)\*\*([^*:\n]+)\*\*:\s*",
        r"\1**\2:** ",
        current,
    )

    # 5. Expand 2-component timestamps [MM:SS] to 3-component [00:MM:SS]
    expanded_ts = re.sub(
        r"(?m)(?<=\[)(\d{1,2}:\d{2})(?=\])",
        lambda m: f"00:{int(m.group(1).split(':')[0]):02d}:{int(m.group(1).split(':')[1]):02d}",
        current,
    )
    if expanded_ts != current:
        current = expanded_ts
        ch.append("Expanded 2-component timestamps [MM:SS] to standard [00:MM:SS]")

    # 5b. Restore omitted short source turns from reference source text
    if "### [" in current:
        dedup_generic = re.sub(
            r"(?m)^\[\d{2}:\d{2}:\d{2}\]\s*\*\*Speaker:\*\*[^\n]+\n*",
            "",
            current,
        )
        if dedup_generic != current:
            current = dedup_generic
            ch.append("Removed duplicate inline generic [HH:MM:SS] **Speaker:** lines where ### [HH:MM:SS] turns exist")
    current = restore_omitted_source_turns(current, meta, ch, al)

    # 5b-2. Apply phonetic and domain homophone normalizations and strip stacked passive backchannels
    current = _apply_phonetic_and_maximo_maps(current, meta, ch)
    backchannel_stripped = re.sub(
        r"(?m)^(\*\*[^*:\n]+:\*\*\s*)Yeah\.\s*No\.\s*Okay\.\s+(?=[A-Z])",
        r"\1",
        current,
    )
    if backchannel_stripped != current:
        current = backchannel_stripped
        ch.append("Stripped leading passive backchannel stack 'Yeah. No. Okay.'")

    # 5c. Interpolate collapsed timestamps if unique_ratio < 0.25
    interpolated, did_interp = interpolate_collapsed_timestamps(current)
    if did_interp:
        current = interpolated
        ch.append("Interpolated collapsed timestamps monotonically across dialogue turns")

    # 5d. Inject standard Rule C-11 recording gap / natural pause notations for unannotated gaps
    current = heal_recording_gaps(current, ch, al)

    # 6. Deduplicate consecutive pause/gap annotations
    dedup_pauses = re.sub(
        r"(\[(?:Natural pause|Recording gap):[^\]]+\]\n*)\s*(?:\[(?:Natural pause|Recording gap):[^\]]+\]\n*)+",
        r"\1",
        current,
    )
    if dedup_pauses != current:
        current = dedup_pauses
        ch.append("Deduplicated consecutive pause annotations")

    # 7. Heal Attribution Summary with dynamic rebuild if present
    if "## Attribution Summary" in current or "# Attribution Summary" in current:
        try:
            from dual_pipeline.transcript_stages_core.attribution_builder import AttributionSummaryBuilder  # noqa: lazy-decouple
            split_summary = re.split(r"(?:^|\n)#{1,3}\s+Attribution Summary\b", current, flags=re.IGNORECASE)
            if len(split_summary) > 1:
                dialogue_part, after_summary = split_summary[0], split_summary[1]
                sub_m = re.search(r"(?:^|\n)(#{1,6}\s+(?!Attribution Summary)[^\n]+[\s\S]*)$", after_summary, re.IGNORECASE)
                sub_text = ("\n\n" + sub_m.group(1).strip()) if sub_m else ""
                new_summary = AttributionSummaryBuilder.build_summary(meta, dialogue_text=dialogue_part)
                if new_summary:
                    current = f"{dialogue_part.rstrip()}\n\n{new_summary.strip()}{sub_text}\n"
                    ch.append("Rebuilt Attribution Summary dynamically from roster metadata")
        except Exception as e:
            logger.debug(f"Dynamic AttributionSummaryBuilder fallback: {e}")

    # 8. Ensure ALR has specialized rows for attendees and domain terms
    current = heal_alr_special_cases(current, ch)
    current = re.sub(r"(?<=\S)\s+---\s*\n+## Ambiguity Lock Register", "\n\n---\n\n## Ambiguity Lock Register", current)

    # 9. Verify cryptographic ALR receipt: set meta flag if verified in ledger.
    # Unauthenticated '(Operator Verified)' claims are preserved so Gate 1.7 fails closed.
    if not (meta.get("hitl_receipt_bound") or meta.get("operator_signature")):
        try:
            from dual_pipeline.transcript_qa_core.gate1_alr import verify_alr_cryptographic_receipt
            alr_ok, alr_receipt, _ = verify_alr_cryptographic_receipt(current, meta)
            if alr_ok and alr_receipt.get("hitl_receipt_bound"):
                meta["hitl_receipt_bound"] = True
                meta["alr_cryptographic_receipt"] = alr_receipt
        except Exception:
            pass

    for c in ch:
        al.append({"event": "forensic_transcript_normalized", "rule": "RULE-LOSSLESS-SYNTAX", "detail": c, "timestamp": time.time()})

    return current
