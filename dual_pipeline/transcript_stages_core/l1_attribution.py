"""
dual_pipeline/transcript_stages_core/l1_attribution.py

L1 Stage Modular Attribution & Forensic Assembly Engine.
Decoupled speaker mapping, generic speaker detection, participant resolution,
and Ambiguity Lock Register assembly for Stage L1.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from .constants import _GENERIC_SPEAKER_PATTERN
from .hitl_guard import assert_no_u0_hitl_bypass
from .lexical_normalizer import format_ambiguity_lock_register
from .models import HITLRequiredError
from .cluster_attribution import (
    apply_cluster_attribution_resolution,
    build_cluster_review_items,
)


def _apply_generic_speaker_patterns(resolved_text: str, clean_k: str, clean_v: str) -> str:
    k_lower = clean_k.lower()
    if not (k_lower.startswith("speaker ") or k_lower == "speaker" or k_lower.startswith("speaker_")):
        return resolved_text
    spk_letter = clean_k.replace("Speaker ", "").replace("speaker ", "").replace("Speaker_", "").replace("speaker_", "").strip()
    if spk_letter and spk_letter.lower() != "speaker":
        resolved_text = re.sub(
            rf"\*\*Speaker[\s_]+{re.escape(spk_letter)}\*\*(?=\s*\[[^\]]+\]\s*:)",
            f"**{clean_v}**",
            resolved_text,
            flags=re.IGNORECASE,
        )
        resolved_text = re.sub(
            rf"\*\*Speaker[\s_]+{re.escape(spk_letter)}:?\*\*(?!\s*\[[^\]]+\]\s*:):?",
            f"**{clean_v}:**",
            resolved_text,
            flags=re.IGNORECASE,
        )
        resolved_text = re.sub(
            rf"\bSpeaker[\s_]+{re.escape(spk_letter)}:", f"{clean_v}:", resolved_text, flags=re.IGNORECASE
        )
        if clean_k.lower() != clean_v.lower():
            resolved_text = re.sub(
                rf"\bSpeaker[\s_]+{re.escape(spk_letter)}\b", clean_v, resolved_text, flags=re.IGNORECASE
            )
    elif spk_letter.lower() == "speaker" or k_lower == "speaker":
        resolved_text = re.sub(
            r"\*\*Speaker\*\*(?=\s*\[[^\]]+\]\s*:)", f"**{clean_v}**", resolved_text, flags=re.IGNORECASE
        )
        resolved_text = re.sub(
            r"\*\*Speaker:?\*\*(?!\s*\[[^\]]+\]\s*:):?", f"**{clean_v}:**", resolved_text, flags=re.IGNORECASE
        )
    return resolved_text


def apply_speaker_map(text: str, speaker_map: Dict[str, str]) -> str:
    """Applies speaker mapping replacements to transcript dialogue idempotently."""
    resolved_text = text
    for k, v in speaker_map.items():
        clean_k = k.strip("[]").strip()
        clean_v = v.strip("[]").strip()
        if not clean_k or not clean_v:
            continue

        # 1a. Bold speaker followed by [HH:MM:SS]: (preserve single colon after timestamp)
        b_start = r"\b" if re.match(r"^\w", clean_k) else r"(?<!\w)"
        b_end = r"\b" if re.search(r"\w$", clean_k) else r"(?!\w)"
        pat_ts_after = rf"(^|\n)\*{{0,2}}\s*\[?{b_start}{re.escape(clean_k)}{b_end}\]?\*\*(?=\s*\[[^\]]+\]\s*:)"
        resolved_text = re.sub(
            pat_ts_after,
            rf"\1**{clean_v}**",
            resolved_text,
            flags=re.IGNORECASE,
        )

        # 1b. Bracketed or timestamp-preceding speaker formats:
        pat_turn = rf"(^|\n|\]\s*)\*{{0,2}}\s*\[?{b_start}{re.escape(clean_k)}{b_end}\]?\s*(?::\*\*|\*\*:?|:)(?!\s*\[[^\]]+\]\s*:)"
        resolved_text = re.sub(
            pat_turn,
            rf"\1**{clean_v}:**",
            resolved_text,
            flags=re.IGNORECASE,
        )

        # 2. Traditional 'Speaker X' patterns (applied when k is generic)
        resolved_text = _apply_generic_speaker_patterns(resolved_text, clean_k, clean_v)
    return resolved_text



def _isolate_dialogue_section(transcript: str) -> str:
    check_text = transcript
    for divider in ("\n## Full transcript", "\n## Live Feed", "\n## Transcript", "\n---"):
        if divider in check_text:
            parts = check_text.split(divider, 1)
            if len(parts) > 1 and len(parts[1].strip()) > 50:
                check_text = parts[1]
                break
    footer_m = re.search(r"(?m)^#{1,3}\s+(?:Ambiguity Lock Register|Attribution Summary)\b", check_text)
    if footer_m:
        check_text = check_text[:footer_m.start()]
    return check_text


_TURN_SPEAKER_PATTERN = re.compile(
    r"(?:^|\n|\](?:\*\*)?\s*)(?:\*\*)?\s*\[?(Speaker(?:\s+[A-Za-z0-9]+)?|Voice(?:\s+[A-Za-z0-9]+)?|SPEAKER_\d+|Unknown(?:\s+Speaker)?|Unattributed)\]?(?:\*\*)?(?:\s*\[[^\]]+\])?:",
    re.IGNORECASE,
)

_IGNORED_GENERIC_TOKENS = {
    "speaker labels", "speaker label", "speaker attribution", "speaker roster",
    "speaker turns", "speaker notes",
    "speaker tags", "speaker diarization", "speaker identification",
    "speaker verification", "speaker mapping", "speaker hints",
}


def _find_generic_speakers(check_text: str) -> List[str]:
    detected_turns = {
        s for s in _TURN_SPEAKER_PATTERN.findall(check_text)
        if s.strip("[]").strip().lower() not in _IGNORED_GENERIC_TOKENS
    }
    if detected_turns:
        return sorted(detected_turns)
    # If the transcript already has structured named speaker turns, do not scan mid-sentence prose
    # for unanchored words like 'voice here' or 'active voice right'.
    if re.search(r"(?m)^(?:\*\*[A-Z][^*:\n]+:\*\*|###\s*\[\d{2}:\d{2}:\d{2}\])", check_text):
        return []
    detected_generic = {
        m for m in _GENERIC_SPEAKER_PATTERN.findall(check_text)
        if m.lower() not in _IGNORED_GENERIC_TOKENS
    }
    return sorted(detected_generic)


def check_generic_speakers(
    transcript: str,
    speaker_map: Dict[str, str],
    is_undiarized_audio: bool = False,
    source_type: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> List[str]:
    """Detects unmapped generic speaker tokens and verifies zero HITL bypass."""
    if kwargs:
        assert_no_u0_hitl_bypass(kwargs)

    check_text = _isolate_dialogue_section(transcript)
    generic_speakers = _find_generic_speakers(check_text)

    clean_mapped = {k.strip("[]").strip().lower(): v for k, v in speaker_map.items()}
    unmapped = []
    for spk in generic_speakers:
        spk_clean = spk.strip("[]").strip().lower()
        if (
            spk_clean not in clean_mapped
            and f"speaker {spk_clean}" not in clean_mapped
            and spk not in speaker_map
            and f"Speaker {spk}" not in speaker_map
        ):
            unmapped.append(spk)

    if unmapped:
        from .cluster_attribution import build_cluster_review_items

        meta = metadata or kwargs.get("metadata") or {}
        review_items, all_contexts, digest, prompt, options = build_cluster_review_items(
            transcript, unmapped, metadata=meta
        )

        msg = (
            f"L1 ATTRIBUTION GATE BLOCKED: Audio input has unmapped generic speakers: {unmapped}. "
            "Rigorous speaker attribution HITL is required before Output 01 can pass."
            if (is_undiarized_audio or source_type == "AUDIO_ONLY_UNDIARIZED")
            else f"L1 ATTRIBUTION GATE BLOCKED: Unmapped generic speakers detected: {unmapped}. "
                 f"Provide a speaker_map in metadata to resolve before Output 1 can pass."
        )

        raise HITLRequiredError(
            msg,
            unmapped_speakers=unmapped,
            hitl_type="speaker_attribution",
            options=options,
            prompt=prompt,
            evidence={
                "review_items": review_items,
                "review_contexts": all_contexts,
                "context_digest": digest,
            },
            review_contexts=all_contexts,
            review_items=review_items,
            context_digest=digest,
        )

    return unmapped


def _format_roster_dict_list(roster: List[Dict[str, Any]], metadata: Dict[str, Any]) -> str:
    from dual_pipeline.transcript_stages_core.graph_post_roster import is_cross_project_silent_attendee
    by_org: Dict[str, List[str]] = {}
    for r in roster:
        name = str(r.get("name", ""))
        if is_cross_project_silent_attendee(re.sub(r"\s*\([^)]*\)", "", name).strip(), metadata):
            continue
        role = r.get("role", "")
        by_org.setdefault(r.get("org", "Unknown"), []).append(f"{name} ({role})" if role else name)
    return "\n".join(f"- **{org}:** {', '.join(people)}" for org, people in sorted(by_org.items()) if people)


def _filter_attendee_list(items: List[Any], metadata: Dict[str, Any]) -> List[str]:
    from dual_pipeline.transcript_stages_core.graph_post_roster import is_cross_project_silent_attendee
    kept: List[str] = []
    for a in items:
        label = a.get("name", str(a)) if isinstance(a, dict) else str(a)
        if not is_cross_project_silent_attendee(re.sub(r"\s*\([^)]*\)", "", label).strip(), metadata):
            kept.append(label)
    return kept


def build_participants_string(
    metadata: Dict[str, Any],
    speaker_map: Dict[str, str],
) -> str:
    """Extracts and formats participant roster from metadata or speaker map."""
    roster = metadata.get("roster")
    if isinstance(roster, list) and roster and all(isinstance(r, dict) and r.get("org") for r in roster):
        return _format_roster_dict_list(roster, metadata)

    att = metadata.get("attendees") or metadata.get("roster")
    if isinstance(att, dict):
        return "\n".join(
            f"- **{org}:** {', '.join(kept)}"
            for org, people in att.items()
            for kept in (_filter_attendee_list(people, metadata),)
            if kept
        )
    if isinstance(att, list) and att:
        return ", ".join(_filter_attendee_list(att, metadata))
    if isinstance(att, str) and att.strip():
        parts = [p.strip() for p in re.split(r"[,;\n]+(?![^(]*\))", att) if p.strip()]
        return ", ".join(_filter_attendee_list(parts, metadata))
    if speaker_map:
        return ", ".join(sorted(set(speaker_map.values())))
    raise ValueError("Fail-Fast: Cannot build participants string; both attendee roster and speaker_map are empty.")


def _normalize_header_date(date_str: Any) -> str:
    s = str(date_str).strip()
    m_iso = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", s)
    if m_iso:
        return f"{int(m_iso.group(2)):02d}/{int(m_iso.group(3)):02d}/{m_iso.group(1)}"
    m_us = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$", s)
    if m_us:
        return f"{int(m_us.group(1)):02d}/{int(m_us.group(2)):02d}/{m_us.group(3)}"
    return s


def build_presented_collateral_string(metadata: Dict[str, Any]) -> str:
    """Extracts and formats presented collateral artifacts from metadata."""
    attachments = metadata.get("attachments") or []
    if not isinstance(attachments, list) or not attachments:
        return ""
    cr_attachments = [
        a for a in attachments
        if isinstance(a, dict) and not str(a.get("filename", "")).endswith((".txt", ".md", ".json"))
        and (a.get("attribution_mode") == "COLLATERAL_REVIEWED" or not any(x.get("attribution_mode") == "COLLATERAL_REVIEWED" for x in attachments if isinstance(x, dict)))
    ]
    if not cr_attachments:
        return ""

    lines = ["### Presented Collateral & Artifacts"]
    for idx, att in enumerate(cr_attachments, 1):
        fname = att.get("filename", f"artifact_{idx}.png")
        rel_path = att.get("relative_staged_path") or f"referenced_artifacts/{fname}"
        title = att.get("title") or att.get("name") or fname.rsplit(".", 1)[0].replace("_", " ").title()
        presenter = att.get("presenter") or ""
        desc = att.get("description") or att.get("summary") or ""
        lines.append(f"- **Artifact {idx}:** `{title}`")
        if att.get("url"):
            lines.append(f"  - **URL:** `{att['url']}`")
        if presenter:
            lines.append(f"  - **Presenter:** {presenter}")
        lines.append(f"  - **File Reference:** `{rel_path}`")
        if desc:
            lines.append(f"  - **View Scope:** {desc}")
        workflows = att.get("components") or att.get("claims") or []
        if isinstance(workflows, list) and workflows:
            clean_wf = [str(w) for w in workflows if str(w).strip()]
            if clean_wf:
                lines.append(f"  - **Key Workflows:** {', '.join(clean_wf[:5])}")
    return "\n".join(lines)


def assemble_native_hitl_transcript(
    resolved_text: str,
    metadata: Dict[str, Any],
    speaker_map: Dict[str, str],
) -> str:
    """Assembles final Output 01 document with metadata headers and Ambiguity Lock Register."""
    # Check for shared conference room microphones requiring disambiguation
    from dual_pipeline.speaker_disambiguation import resolve_shared_room_microphones
    resolved_text, metadata = resolve_shared_room_microphones(resolved_text, metadata, speaker_map)

    participants_str = build_participants_string(metadata, speaker_map)
    ambiguity_note = format_ambiguity_lock_register(metadata, speaker_map)
    collateral_str = build_presented_collateral_string(metadata)

    raw_date = metadata.get("date") or metadata.get("meeting_date") or metadata.get("date_filename")
    canonical_date: Optional[str] = _normalize_header_date(raw_date) if raw_date else None

    stripped = resolved_text.strip()
    if stripped.startswith("#") and (
        "HITL-Resolved Transcript" in stripped
        or "Forensic Transcript" in stripped
        or "\n**Date:**" in stripped
    ):
        if not canonical_date:
            m_found = re.search(r"(?:\*\*Date:?\*\*:?|Date:)\s*([^\n\r]+)", resolved_text)
            if m_found:
                canonical_date = _normalize_header_date(m_found.group(1).strip())

        if canonical_date:
            has_canon_date = bool(re.search(rf"\*\*Date:\*\*\s*{re.escape(canonical_date)}", resolved_text))
            if not has_canon_date:
                if re.search(r"(?m)^(?:\*\*Date:?\*\*:?|Date:)", resolved_text):
                    resolved_text = re.sub(
                        r"(?m)^(?:\*\*Date:?\*\*:?|Date:)\s*[^\n]+",
                        f"**Date:** {canonical_date}",
                        resolved_text,
                        count=1,
                    )
                else:
                    m_title = re.search(r"^#[^\n]+\n+", resolved_text)
                    if m_title:
                        resolved_text = resolved_text[:m_title.end()] + f"**Date:** {canonical_date}\n\n" + resolved_text[m_title.end():]

        if collateral_str and "### Presented Collateral & Artifacts" not in resolved_text and not metadata.get("_document_source_path"):
            m_split = re.search(r"(?m)^---\s*$", resolved_text)
            if m_split:
                resolved_text = resolved_text[:m_split.start()] + f"{collateral_str}\n\n" + resolved_text[m_split.start():]
            else:
                resolved_text = f"{collateral_str}\n\n{resolved_text}"

        if "## Ambiguity Lock Register" not in resolved_text and "# Ambiguity Lock Register" not in resolved_text:
            resolved_text = resolved_text.rstrip() + f"\n\n---\n## Ambiguity Lock Register\n{ambiguity_note}\n"
        return resolved_text

    meeting_name = metadata.get("meeting_name") or metadata.get("title")
    if not meeting_name:
        raise ValueError("Fail-Fast: metadata['meeting_name'] (or 'title') is required to assemble Output 01 transcript.")
    if not canonical_date:
        raise ValueError("Fail-Fast: metadata['date'] (or 'meeting_date') is required to assemble Output 01 transcript.")

    collateral_block = f"\n\n{collateral_str}" if collateral_str else ""
    return (
        f"# {meeting_name} -- HITL-Resolved Transcript | {canonical_date}\n\n"
        f"**Date:** {canonical_date}\n"
        f"**Platform Reference:** {metadata.get('platform', 'Not provided')}\n"
        f"**Participants:**\n{participants_str}{collateral_block}\n\n"
        f"---\n{resolved_text}\n\n---\n"
        f"## Ambiguity Lock Register\n{ambiguity_note}\n"
    )

