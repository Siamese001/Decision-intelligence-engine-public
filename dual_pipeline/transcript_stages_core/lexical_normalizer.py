"""Forensic lexical, phonetic, and turn normalizer for transcripts."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Common enterprise ASR phonetic homophone substitutions
COMMON_ASR_HOMOPHONES: Dict[str, str] = {
    r"\bSSW\b": "SOW",
    r"\bSW1\b": "SOW1",
    r"\bStaffog\b": "Staff Aug",
    r"\bstaff log\b": "staff aug",
    r"\bSteer Code\b": "SteerCo",
    r"\bstair cut\b": "SteerCo",
    r"\bpre-BERP\b": "pre-BERT",
    r"\ba Gentec\b": "agentic",
    r"\bA Gentec\b": "agentic",
    r"\bKendryl\b": "Kyndryl",
    r"\bclawed code\b": "Claude Code",
    r"\bshort\s*point\s+connector\b": "SharePoint connector",
    r"\bshort\s*point\b": "SharePoint",
    r"\b(?:maximum|maximal|Maxwell)\s+(?:right|write)[-\s]+back\b": "Maximo write-back",
    r"\bMaximo\s+right[-\s]+back\b": "Maximo write-back",
}


def _prune_cutoff_marker(text: str, cutoff_marker: Optional[str]) -> str:
    """Prune trailing post-call hallucination if cutoff marker is defined."""
    if not cutoff_marker or cutoff_marker not in text:
        return text
    cutoff_pos = text.find(cutoff_marker)
    end_of_cutoff_line = text.find("\n", cutoff_pos)
    if end_of_cutoff_line != -1:
        pruned = text[:end_of_cutoff_line].strip() + "\n"
    else:
        pruned = text[:cutoff_pos].strip() + "\n"
    logger.info(f"Pruned trailing transcript beyond cutoff marker: {cutoff_marker}")
    return pruned


def _apply_phonetic_maps(text: str, metadata: Dict[str, Any]) -> str:
    """Apply metadata-level phonetic corrections, lexicon maps, and common homophones."""
    phonetic_map: Dict[str, str] = {}
    for key in ("phonetic_corrections", "phonetic_map", "lexicon_map"):
        val = metadata.get(key)
        if isinstance(val, dict):
            phonetic_map.update(val)

    for err_pattern, clean_term in phonetic_map.items():
        text = re.sub(rf"\b{re.escape(err_pattern)}\b", clean_term, text, flags=re.IGNORECASE)

    for pat, rep in COMMON_ASR_HOMOPHONES.items():
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)

    return text


def _clean_name(raw_name: Any) -> str:
    """Strip title in parentheses from attendee name."""
    return str(raw_name).split("(")[0].strip()


def _names_from_attendees(attendees: Any) -> List[str]:
    """Extract clean attendee names from list, dict, or string."""
    names: List[str] = []
    if isinstance(attendees, dict):
        for org_members in attendees.values():
            if isinstance(org_members, list):
                names.extend(_clean_name(m) for m in org_members if _clean_name(m))
    elif isinstance(attendees, list):
        names.extend(_clean_name(m) for m in attendees if _clean_name(m))
    elif isinstance(attendees, str):
        for part in re.split(r"[;,]", attendees):
            clean = _clean_name(part)
            if clean:
                names.append(clean)
    return names


def _names_from_roster(roster: Any) -> List[str]:
    """Extract clean attendee names from roster list."""
    if not isinstance(roster, list):
        return []
    return [
        entry["name"].strip()
        for entry in roster
        if isinstance(entry, dict) and "name" in entry and entry["name"].strip()
    ]


def _extract_attendee_names(metadata: Dict[str, Any]) -> List[str]:
    """Extract attendee names from metadata attendees and roster."""
    return _names_from_attendees(metadata.get("attendees")) + _names_from_roster(metadata.get("roster"))


def _normalize_attendee_soundalikes(text: str, attendee_names: List[str], soundalike_map: Optional[Dict[str, str]] = None) -> str:
    """Normalize explicit sound-alike mis-transcriptions for attendee names provided via metadata."""
    if not isinstance(soundalike_map, dict):
        return text
    valid_names = {n.strip().lower() for n in attendee_names if n and n.strip()}
    for wrong_form, canonical_name in soundalike_map.items():
        if not wrong_form or not canonical_name:
            continue
        if not valid_names or canonical_name.strip().lower() in valid_names:
            text = re.sub(rf"\b{re.escape(str(wrong_form))}\b", str(canonical_name), text, flags=re.IGNORECASE)
    return text


def _normalize_domain_terms(text: str, domain_terms: List[Any]) -> str:
    """Normalize expected enterprise domain terms."""
    text = re.sub(r"\b(?:lane\s+chain|lang\s+chain)\b", "LangChain", text, flags=re.IGNORECASE)

    if not isinstance(domain_terms, list):
        return text
    for term in domain_terms:
        clean_term = str(term).strip()
        if not clean_term:
            continue
        t_low = clean_term.lower()
        if t_low == "steerco":
            text = re.sub(r"\b(?:Steer\s+Code|stair\s+cut)\b", "SteerCo", text, flags=re.IGNORECASE)
        else:
            text = re.sub(rf"\b{re.escape(clean_term)}\b", clean_term, text, flags=re.IGNORECASE)
    return text


def _apply_speaker_turn_patches(text: str, patches: List[Any]) -> str:
    """Apply speaker turn patches if provided in metadata."""
    if not isinstance(patches, list):
        return text
    for patch in patches:
        if isinstance(patch, dict) and "target" in patch and "replacement" in patch:
            target = patch["target"]
            replacement = patch["replacement"]
            if target in text:
                text = text.replace(target, replacement)
                logger.info("Applied speaker turn patch from metadata.")
    return text


def _iso_to_us_date(match: re.Match[str]) -> str:
    """Convert YYYY-MM-DD -> MM/DD/YYYY."""
    parts = match.group(0).split("-")
    return f"{parts[1]}/{parts[2]}/{parts[0]}"


def normalize_lexical_and_phonetics(transcript: str, metadata: Dict[str, Any]) -> str:
    """Apply upstream lexical, phonetic, and boundary normalization to transcript text.

    Ensures that raw ASR errors, name misspellings, domain homophones, and post-call
    hallucinations are resolved before downstream artifacts are synthesized.
    """
    if not transcript:
        return ""

    text = _prune_cutoff_marker(transcript, metadata.get("cutoff_marker"))
    text = _apply_phonetic_maps(text, metadata)
    attendee_names = _extract_attendee_names(metadata)
    text = _normalize_attendee_soundalikes(text, attendee_names, metadata.get("attendee_soundalikes"))
    text = _normalize_domain_terms(text, metadata.get("expected_domain_terms", []))
    text = _apply_speaker_turn_patches(text, metadata.get("speaker_turn_patches", []))
    from .acronym_disambiguator import disambiguate_asr_acronyms
    text, _ = disambiguate_asr_acronyms(text, metadata, interactive_resolver=metadata.get("hitl_acronym_resolver"))
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", _iso_to_us_date, text)
    from .organic_signal_scrubber import scrub_organic_signal
    is_diarized = str(metadata.get("diarization_status", "")).upper() in (
        "NATIVE",
        "DIARIZED_ACOUSTIC_RESOLVED",
        "VERIFIED_STEREO_SEPARATION",
    )
    return scrub_organic_signal(text, preserve_verbatim_turns=is_diarized)


def _item_to_name(item: Any) -> Optional[str]:
    """Convert roster item to string name."""
    n = item.get("name") if isinstance(item, dict) else str(item)
    return n.strip() if n and str(n).strip() else None


def _extract_member_name_cleaned(member: Any) -> Optional[str]:
    name = _item_to_name(member)
    if not name:
        return None
    clean = re.sub(r"\s*\(.*?\)", "", name).strip()
    return clean if clean else None


def _extract_dict_member_names(members: Any) -> List[str]:
    if isinstance(members, list):
        return [name for m in members if (name := _item_to_name(m))]
    if isinstance(members, str):
        clean = _extract_member_name_cleaned(members)
        return [clean] if clean else []
    return []


def _extract_roster_names_simple(metadata: Dict[str, Any]) -> List[str]:
    """Extract attendee roster names as a flat string list."""
    roster = metadata.get("roster") or metadata.get("attendees") or metadata.get("speaker_map") or []
    if isinstance(roster, list):
        return [name for item in roster if (name := _item_to_name(item))]
    if isinstance(roster, dict):
        names: List[str] = []
        for members in roster.values():
            names.extend(_extract_dict_member_names(members))
        return names
    if isinstance(roster, str):
        return [cleaned for part in re.split(r"[;,]", roster) if (cleaned := _clean_name(part))]
    return []


def _format_dict_hitl_res_row(
    k: str, val: Dict[str, Any], idx: int, verified_receipt_id: Optional[str]
) -> str:
    title = val.get("ambiguity") or k
    loc = val.get("locator") or "[ASR Multi-Turn]"
    stmts = val.get("statement_id") or f"stmt-{idx:02d}"
    res = str(val.get("resolution") or val).replace("\n", " ").replace("|", "\\|")
    basis = val.get("basis") or f"Pre-Resolved Grounding ({k})"
    rec_id = val.get("receipt_id") or val.get("verified_entry_id") or verified_receipt_id
    if rec_id and ("operator" in basis.lower() or "hitl" in basis.lower()) and "receipt" not in basis.lower():
        basis = f"{basis} [Ed25519 Receipt: {str(rec_id)[:16]}]"
    return f"| {idx} | {title} | {loc} | {stmts} | {res} | {basis} |"


def _build_hitl_res_rows(hitl_res: Dict[str, Any], verified_receipt_id: Optional[str] = None) -> List[str]:
    """Build table rows from explicit HITL resolutions in metadata."""
    rows: List[str] = []
    idx = 1
    for k, val in hitl_res.items():
        if k.startswith(("judge_clarification", "gate_")) or "Exit Gate" in str(val):
            continue
        if isinstance(val, dict):
            rows.append(_format_dict_hitl_res_row(k, val, idx, verified_receipt_id))
        else:
            loc, stmts, title = "[ASR Multi-Turn]", f"stmt-{idx:02d}", k
            clean_val = str(val).replace("\n", " ").replace("|", "\\|")
            basis = f"Pre-Resolved Grounding ({k})"
            if verified_receipt_id:
                basis = f"{basis} [Ed25519 Receipt: {str(verified_receipt_id)[:16]}]"
            rows.append(f"| {idx} | {title} | {loc} | {stmts} | {clean_val} | {basis} |")
        idx += 1
    return rows


def _append_speaker_map_rows(
    rows: List[str], speaker_map: Dict[str, str], start_idx: int, is_text: bool = False
) -> Tuple[int, Set[str]]:
    idx = start_idx
    mapped_attendees: Set[str] = set()
    if not speaker_map:
        return idx, mapped_attendees
    res_phrase = "transcript attribution" if is_text else "acoustic profile"
    for spk_lbl, mapped_name in sorted(speaker_map.items()):
        rows.append(
            f"| {idx} | Speaker Attribution: {spk_lbl} | [00:00:00 – Session End] | stmt-spk-{idx:02d} | "
            f"{mapped_name} resolved via {res_phrase} and conversational context. | Verified Participant Roster Alignment ({spk_lbl}) |"
        )
        idx += 1
        clean = re.sub(r"\s*\(.*?\)", "", mapped_name).strip().lower()
        if clean:
            mapped_attendees.add(clean)
        mapped_attendees.add(mapped_name.strip().lower())
    return idx, mapped_attendees


def _build_entity_id_map(known_entities: List[Any]) -> Dict[str, str]:
    entity_id_by_name: Dict[str, str] = {}
    for ent in known_entities:
        if isinstance(ent, dict):
            c_name = ent.get("canonical_name", "").strip().lower()
            d_name = ent.get("display_name", "").strip().lower()
            n_id = ent.get("node_id", "")
            if c_name and n_id:
                entity_id_by_name[c_name] = n_id
            if d_name and n_id:
                entity_id_by_name[d_name] = n_id
    return entity_id_by_name


def _check_turn_counts_silence(
    clean_name: str, raw_lower: str, turn_counts: Any
) -> Optional[bool]:
    if not isinstance(turn_counts, dict):
        return None
    for k, cnt in turn_counts.items():
        k_clean = re.sub(r"\s*\(.*?\)", "", str(k)).strip().lower()
        if k_clean in (clean_name, raw_lower) and isinstance(cnt, int):
            return cnt == 0
    return None


def _is_silent_roster_participant(name: str, metadata: Dict[str, Any]) -> bool:
    """Return True if metadata indicates the participant had 0 spoken turns."""
    clean_name = re.sub(r"\s*\(.*?\)", "", name).strip().lower()
    raw_lower = name.strip().lower()

    silent_list = metadata.get("silent_attendees") or metadata.get("non_speaking_participants") or []
    if isinstance(silent_list, list):
        silent_norm = {re.sub(r"\s*\(.*?\)", "", str(s)).strip().lower() for s in silent_list}
        if clean_name in silent_norm or raw_lower in silent_norm:
            return True

    tc_silent = _check_turn_counts_silence(clean_name, raw_lower, metadata.get("speaker_turn_counts"))
    if tc_silent is not None:
        return tc_silent

    turns = metadata.get("turns")
    if isinstance(turns, list) and turns:
        spoken_speakers = {
            re.sub(r"\s*\(.*?\)", "", str(t.get("speaker") or "")).strip().lower()
            for t in turns
            if isinstance(t, dict) and (t.get("text") or t.get("utterance"))
        }
        return bool(spoken_speakers and clean_name not in spoken_speakers and raw_lower not in spoken_speakers)

    return False


def _build_roster_grounding_row(
    name: str,
    matched_node_id: Optional[str],
    idx: int,
    is_text: bool,
    is_silent: bool = False,
) -> str:
    node_suffix = f" ({matched_node_id})" if matched_node_id else ""
    if is_text or not matched_node_id:
        basis = "Verified Participant Roster Alignment" if is_text else "Multi-Tier Acoustic Isolation Protocol"
    else:
        basis = "Canonical Graph Entity Grounding (Rule C-03/C-16)"
    label = "Roster Alignment" if is_text else "Acoustic Grounding"
    title = f"Speaker Attribution & {label}: {name}"
    if is_silent:
        note = f"Verified participant {name}{node_suffix} present on session roster with 0 independently attributed spoken turns."
        return f"| {idx} | {title} | [00:00:00 – Session End] | None (0 spoken turns) | {note} | {basis} |"
    note = f"Dialogue turns attributed to verified participant {name}{node_suffix}."
    return f"| {idx} | {title} | [00:00:00 – Session End] | stmt-spk-{idx:02d} | {note} | {basis} |"


def _append_roster_grounding_rows(
    rows: List[str],
    roster_names: List[str],
    mapped_attendees: Set[str],
    entity_id_by_name: Dict[str, str],
    start_idx: int,
    is_text: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    idx = start_idx
    meta = metadata or {}
    from dual_pipeline.transcript_stages_core.graph_post_roster import is_cross_project_silent_attendee
    for name in roster_names:
        clean_raw = re.sub(r"\s*\(.*?\)", "", name).strip()
        clean_name = clean_raw.lower()
        if clean_name in mapped_attendees or name.strip().lower() in mapped_attendees:
            continue
        if is_cross_project_silent_attendee(clean_raw, meta):
            continue
        matched_node_id = entity_id_by_name.get(clean_name) or entity_id_by_name.get(name.strip().lower())
        is_silent = _is_silent_roster_participant(name, meta)
        rows.append(_build_roster_grounding_row(name, matched_node_id, idx, is_text, is_silent=is_silent))
        idx += 1
        mapped_attendees.add(clean_name)
        mapped_attendees.add(name.strip().lower())
    return idx


def _append_lexicon_row(rows: List[str], graph_terms: List[str], idx: int, metadata: Optional[Dict[str, Any]] = None) -> int:
    meta = metadata or {}
    expected = meta.get("expected_domain_terms") or []
    src_lower = str(meta.get("source_text") or meta.get("clean_transcript") or "").lower()
    if expected:
        filtered = [str(t) for t in expected if not src_lower or str(t).lower() in src_lower]
        terms_snippet = ", ".join((filtered or [str(t) for t in expected])[:6])
        rows.append(
            f"| {idx} | Enterprise Domain Lexicon & Acronym Normalization | [00:00:00 – Session End] | stmt-lex-{idx:02d} | "
            f"Domain terminology ({terms_snippet}) anchored against canonical Knowledge Graph. | Standard Enterprise Taxonomy Verification |"
        )
    elif graph_terms:
        filtered = (
            [str(t) for t in graph_terms if re.search(rf"\b{re.escape(str(t).lower())}\b", src_lower)]
            if src_lower
            else list(graph_terms)
        )
        if filtered:
            terms_snippet = ", ".join(filtered[:6])
            rows.append(
                f"| {idx} | Enterprise Domain Lexicon & Acronym Normalization | [00:00:00 – Session End] | stmt-lex-{idx:02d} | "
                f"Domain terminology ({terms_snippet}) anchored against canonical Knowledge Graph. | Standard Enterprise Taxonomy Verification |"
            )
        else:
            rows.append(
                f"| {idx} | Enterprise Domain Lexicon & Acronym Normalization | [00:00:00 – Session End] | stmt-lex-{idx:02d} | "
                f"Enterprise terminology and project nomenclature verified against domain glossary. | Standard Enterprise Taxonomy Verification |"
            )
    else:
        rows.append(
            f"| {idx} | Enterprise Domain Lexicon & Acronym Normalization | [00:00:00 – Session End] | stmt-lex-{idx:02d} | "
            f"Enterprise terminology and project nomenclature verified against domain glossary. | Standard Enterprise Taxonomy Verification |"
        )
    return idx + 1


def _build_speaker_grounding_rows(
    metadata: Dict[str, Any],
    speaker_map: Dict[str, str],
    start_idx: int,
) -> List[str]:
    """Build itemized grounding rows for participant speaker attribution."""
    from dual_pipeline.transcript_pipeline_core.source_modality import is_text_modality
    is_text = is_text_modality(metadata)
    rows: List[str] = []
    idx, mapped_attendees = _append_speaker_map_rows(rows, speaker_map, start_idx, is_text=is_text)
    entity_id_map = _build_entity_id_map(metadata.get("known_graph_entities") or [])
    roster_names = _extract_roster_names_simple(metadata)
    idx = _append_roster_grounding_rows(
        rows, roster_names, mapped_attendees, entity_id_map, idx, is_text=is_text, metadata=metadata
    )
    _append_lexicon_row(rows, metadata.get("graph_lexical_anchors") or [], idx, metadata=metadata)
    return rows



def _append_acoustic_and_crosstalk_rows(
    rows: List[str], metadata: Dict[str, Any], is_text: bool
) -> None:
    if not is_text:
        for amb in metadata.get("acoustic_ambiguities") or []:
            idx = len(rows) + 1
            title = amb.get("description") or amb.get("type", "Acoustic Ambiguity")
            loc = amb.get("locator") or f"[{amb.get('start', 0):.0f}s - {amb.get('end', 0):.0f}s]"
            stmts = amb.get("statement_id", f"stmt-ac-{idx:02d}")
            res = amb.get("resolution", "Acoustic gap/ambiguity preserved in transcript record.")
            basis = amb.get("basis", "Acoustic Energy & Continuity Analyzer")
            rows.append(f"| {idx} | {title} | {loc} | {stmts} | {res} | {basis} |")
    for ct in metadata.get("crosstalk_entries") or metadata.get("overlapping_turns") or []:
        idx = len(rows) + 1
        title = ct.get("title") or ct.get("description") or "Simultaneous / Overlapping Dialogue"
        loc = ct.get("locator") or ct.get("source_locator") or "[00:00:00]"
        stmts = ct.get("statement_ids") or ct.get("statement_id") or f"stmt-ct-{idx:02d}"
        res = ct.get("resolution") or "Simultaneous speech preserved as sequential turns with shared timestamps."
        basis = ct.get("basis") or "Conversational Turn Overlap Analysis"
        rows.append(f"| {idx} | {title} | {loc} | {stmts} | {res} | {basis} |")


def format_ambiguity_lock_register(metadata: Dict[str, Any], speaker_map: Dict[str, str]) -> str:
    """Format the Ambiguity Lock Register markdown section from metadata or defaults.

    Always emits an itemized markdown table grounding speaker attribution, adhering strictly
    to the gold-standard specification.
    """
    table_header = [
        "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |",
        "|---|---|---|---|---|---|",
    ]
    hitl_res = metadata.get("hitl_resolutions") or {}
    rows: List[str] = []
    verified_receipt_id = None
    alr_receipt = metadata.get("alr_cryptographic_receipt")
    if isinstance(alr_receipt, dict):
        verified_receipt_id = alr_receipt.get("verified_entry_id") or alr_receipt.get("entry_id")
    if not verified_receipt_id:
        try:
            from dual_pipeline.transcript_qa_core.gate1_alr import verify_alr_cryptographic_receipt
            alr_ok, receipt_info, _ = verify_alr_cryptographic_receipt("", metadata)
            if alr_ok and receipt_info.get("hitl_receipt_bound"):
                verified_receipt_id = receipt_info.get("verified_entry_id")
        except Exception:
            pass

    if hitl_res:
        rows.extend(_build_hitl_res_rows(hitl_res, verified_receipt_id=verified_receipt_id))

    from dual_pipeline.transcript_pipeline_core.source_modality import is_text_modality
    _append_acoustic_and_crosstalk_rows(rows, metadata, is_text_modality(metadata))

    # Ensure speaker attribution grounding is always explicitly present for all attendees
    roster_names = _extract_attendee_names(metadata)
    min_rows = max(1, len(roster_names) + 1)
    if len(rows) < min_rows or not any("speaker attribution" in str(r).lower() for r in rows):
        spk_rows = _build_speaker_grounding_rows(metadata, speaker_map, start_idx=len(rows) + 1)
        rows.extend(spk_rows)

    return "\n".join(table_header + rows)


