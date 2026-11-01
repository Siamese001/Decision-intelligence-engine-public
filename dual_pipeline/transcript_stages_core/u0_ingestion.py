"""U0 — Untrusted Ingestion Boundary."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, List, Optional, Set

from .attendee_gate import verify_mandatory_attendee_confirmation
from .constants import _GENERIC_SPEAKER_PATTERN, count_native_diarized_turns, is_valid_colon_speaker_label
from .hitl_guard import assert_no_u0_hitl_bypass, validate_diarization_toggle

logger = logging.getLogger(__name__)


def _find_date_in_text(text: str) -> Optional[str]:
    """Search for MM-DD-YYYY or YYYY-MM-DD date pattern in string."""
    m_path = re.search(r"(?:^|[^0-9a-zA-Z])(0[1-9]|1[0-2])[-_/.](0[1-9]|[12]\d|3[01])[-_/.](20\d{2})(?:$|[^0-9a-zA-Z])", text)
    if m_path:
        return f"{m_path.group(1)}/{m_path.group(2)}/{m_path.group(3)}"
    m_path_iso = re.search(r"(?:^|[^0-9a-zA-Z])(20\d{2})[-_/.](0[1-9]|1[0-2])[-_/.](0[1-9]|[12]\d|3[01])(?:$|[^0-9a-zA-Z])", text)
    if m_path_iso:
        return f"{m_path_iso.group(2)}/{m_path_iso.group(3)}/{m_path_iso.group(1)}"
    return None


def _default_date_and_filenames(metadata: Dict[str, Any], raw_transcript: str) -> None:
    """Do not auto-guess date. Require explicit date."""
    if metadata.get("date"):
        date_str = str(metadata["date"]).replace("-", "/")
        m_date = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", date_str)
        metadata["date"] = f"{int(m_date.group(1)):02d}/{int(m_date.group(2)):02d}/{m_date.group(3)}" if m_date else date_str
        metadata.setdefault("date_filename", metadata["date"].replace("/", "-"))


def _default_time_and_filenames(metadata: Dict[str, Any], raw_transcript: str) -> None:
    """Require explicit or discoverable time metadata."""
    if not metadata.get("time"):
        m = re.search(r"\b(\d{1,2}:\d{2}\s*(?:AM|PM)?\s*[-–]\s*\d{1,2}:\d{2}\s*(?:AM|PM)?(?:\s*(?:ET|EST|EDT|PT|CT))?)\b", raw_transcript[:2000], re.I)
        metadata["time"] = m.group(1).strip() if m else None
        if not metadata["time"]:
            logger.debug("Stage U0: Missing required metadata 'time', deferred to preflight validation.")
    if metadata.get("time") and "time_filename" not in metadata:
        metadata["time_filename"] = re.sub(r"[:\s\-\u2013]+", "", metadata["time"])


def _default_meeting_name(metadata: Dict[str, Any], raw_transcript: str) -> None:
    pass


def _default_platform(metadata: Dict[str, Any], raw_transcript: str) -> None:
    if metadata.get("platform"):
        return
    lt = raw_transcript[:1000].lower()
    matched = next((plat for kw, plat in (("webex", "Webex"), ("copilot", "Microsoft Teams CoPilot"), ("zoom", "Zoom"), ("meet", "Google Meet")) if kw in lt), None)
    if matched:
        metadata["platform"] = matched
        return
    metadata["platform"], metadata["_platform_defaulted"] = "Microsoft Teams", True


def _extract_table_attendees(raw_transcript: str) -> Set[str]:
    from dual_pipeline.transcript_stages_core.output01_parser import extract_non_alr_table_attendees
    return extract_non_alr_table_attendees(raw_transcript)


def _extract_raw_speakers(raw_transcript: str) -> Set[str]:
    from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import is_blacklisted_attendee
    from dual_pipeline.transcript_pipeline_core.source_modality import extract_unique_speakers
    speakers = _extract_table_attendees(raw_transcript)
    for s in extract_unique_speakers(raw_transcript):
        name = re.sub(r"\(.*?\)", "", re.sub(r"(?:\s*[—–]\s*|\s+\-\s+).*$", "", s)).strip().title()
        if not is_blacklisted_attendee(name) and len(name.split()) >= 2 and is_valid_colon_speaker_label(name):
            speakers.add(name)
    if re.search(r"^###\s+\[", raw_transcript, re.MULTILINE):
        return speakers
    for m in re.finditer(r"^([A-Z][a-zA-Z\s\-\.]+?):", raw_transcript, re.MULTILINE):
        spk = m.group(1).rstrip(":").strip()
        if is_valid_colon_speaker_label(spk):
            speakers.add(spk)
    return speakers


def _resolve_speakers_with_mics(speakers: Set[str], metadata: Dict[str, Any]) -> Set[str]:
    spk_map = metadata.get("speaker_map", {})
    resolved_speakers = {spk_map.get(s, s) for s in speakers if not re.match(r"^Speaker\s+[A-Z0-9]+$", spk_map.get(s, s), re.IGNORECASE)}
    resolved_speakers.update(att["name"] for mic in metadata.get("shared_room_mics", []) if isinstance(mic, dict) for att in mic.get("in_room_attendees", []) if isinstance(att, dict) and att.get("name"))
    return resolved_speakers or speakers


def _format_roster_entry(r: Any) -> Tuple[str, str] | None:
    if isinstance(r, dict):
        name = str(r.get("name") or "").strip()
        if not name:
            return None
        role, org = str(r.get("role") or "").strip(), str(r.get("org") or "Client / Partner").strip()
        return org, (f"{name} ({role})" if role and role != "Participant" else name)
    return ("Client / Partner", r.strip()) if isinstance(r, str) and r.strip() else None


def _group_roster_by_org(roster: List[Any]) -> Dict[str, List[str]]:
    c_grouped: Dict[str, List[str]] = {}
    from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import build_roster_from_attendees
    if any(isinstance(r, str) for r in roster):
        roster = build_roster_from_attendees(roster)
    for r in roster:
        entry = _format_roster_entry(r)
        if entry:
            c_grouped.setdefault(entry[0], []).append(entry[1])
    return c_grouped


def _populate_roster_from_transcript(metadata: Dict[str, Any], raw_transcript: str) -> None:
    from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import build_roster_from_attendees
    speakers = _extract_raw_speakers(raw_transcript)
    resolved_speakers = _resolve_speakers_with_mics(speakers, metadata)
    if not resolved_speakers:
        return

    in_room_meta = {att["name"]: {"role": att.get("role"), "org": att.get("org")} for mic in metadata.get("shared_room_mics", []) for att in mic.get("in_room_attendees", []) if att.get("name")}
    roster_list = build_roster_from_attendees(list(resolved_speakers))
    for item in roster_list:
        spec = in_room_meta.get(item.get("name"))
        if spec:
            item.update({k: spec[k] for k in ("role", "org") if spec.get(k)})
    metadata["roster"] = roster_list
    metadata["attendees"] = _group_roster_by_org(metadata["roster"]) or {"Participants": sorted(list(resolved_speakers))}


def _merge_shared_room_mics(metadata: Dict[str, Any]) -> None:
    shared_mics, att = metadata.get("shared_room_mics"), metadata.get("attendees")
    if not isinstance(shared_mics, dict) or not isinstance(att, list):
        return
    existing_lower = {str(a).split("(")[0].strip().lower() for a in att}
    for u in (u for users in shared_mics.values() if isinstance(users, list) for u in users):
        u_clean = str(u).split("(")[0].strip().lower()
        if u_clean and u_clean not in existing_lower:
            att.append(u)
            existing_lower.add(u_clean)


def _default_attendees_and_roster(metadata: Dict[str, Any], raw_transcript: str) -> None:
    """Extract speakers from transcript text if attendees not provided and resolve roster."""
    from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
        build_roster_from_attendees, extract_attendees_from_header_block,
    )
    _merge_shared_room_mics(metadata)
    if not metadata.get("attendees"):
        hdr_att = extract_attendees_from_header_block(raw_transcript)
        if hdr_att:
            metadata["attendees"] = hdr_att
    if metadata.get("roster"):
        if any(isinstance(x, str) for x in metadata["roster"]):
            metadata["roster"] = build_roster_from_attendees(metadata["roster"])
        if not metadata.get("attendees") or isinstance(metadata["attendees"], list):
            metadata["attendees"] = _group_roster_by_org(metadata["roster"])
        return
    if metadata.get("attendees"):
        if "roster" not in metadata or not metadata["roster"]:
            metadata["roster"] = build_roster_from_attendees(metadata["attendees"])
        if isinstance(metadata["attendees"], (list, str)) and metadata.get("roster"):
            metadata["attendees"] = _group_roster_by_org(metadata["roster"])
        return
    is_audio = metadata.get("_from_audio_transcription") or metadata.get("is_audio") or metadata.get("source_modality") == "audio" or (metadata.get("input_ext", "").lower() in (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"))
    if is_audio:
        from .attendee_gate import _extract_inferred_acoustic_candidates
        _extract_inferred_acoustic_candidates(metadata)
        logger.info("Audio source detected without roster — leaving empty for mandatory HITL roster gate (preflight candidates cached).")
        return
    _populate_roster_from_transcript(metadata, raw_transcript)


def _extract_flat_attendees(att: Any) -> List[str]:
    """Flatten attendee names from dict or list format."""
    if isinstance(att, dict):
        return [re.sub(r"\s*\(.*?\)", "", str(m)).strip() for g in att.values() if isinstance(g, list) for m in g if m]
    return [re.sub(r"\s*\(.*?\)", "", str(m)).strip() for m in att if m] if isinstance(att, list) else []

def _default_speaker_map(metadata: Dict[str, Any], raw_transcript: str) -> None:
    pass


def _normalize_single_roster_item(item: Dict[str, Any], resolver_kwargs: Dict[str, Any]) -> None:
    from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity
    explicit_org, explicit_role = item.get("org") or item.get("company"), item.get("role")
    ent = resolve_person_entity(item["name"], explicit_org=explicit_org, explicit_role=explicit_role, **resolver_kwargs)
    if not ent:
        return
    item["name"] = ent.canonical_name
    generic_orgs = ("unknown", "tbd", "placeholder", "client / partner", "client", "partner")
    is_gen = not explicit_org or str(explicit_org).strip().lower() in generic_orgs
    if is_gen and ent.org and str(ent.org).strip().lower() not in generic_orgs:
        item["org"] = ent.org
    elif explicit_org:
        item["org"], item["provenance_origin"] = str(explicit_org).strip(), "ORIGIN_INPUT_PROVENANCE"
    if getattr(ent, "candidates", None):
        item["candidates"] = [c.as_dict() if hasattr(c, "as_dict") else dict(c) for c in ent.candidates]
    if getattr(ent, "requires_operator_confirmation", False) or getattr(ent, "status", "") == "AMBIGUOUS_CONFLICT":
        item["requires_operator_confirmation"], item["status"] = True, "AMBIGUOUS_CONFLICT"
        cand_n = getattr(ent, "candidate_name", None)
        if cand_n:
            item["candidate_name"] = cand_n
        if not item.get("role") or "participant" in str(item.get("role", "")).lower():
            item["role"] = cand_n or ent.role or item.get("role")
    elif ent.role:
        cur_role = str(item.get("role", "")).lower()
        if "participant" in cur_role or not item.get("role") or " / " in cur_role:
            item["role"] = ent.role


def _get_resolver_kwargs(metadata: Dict[str, Any]) -> Dict[str, Any]:
    as_of = metadata.get("as_of_timestamp") or metadata.get("meeting_date") or metadata.get("date")
    return {"repo_root": metadata.get("repo_root"), "runtime_root": metadata.get("runtime_root"), "generation_id": metadata.get("generation_id"), "as_of_timestamp": as_of}


def _normalize_roster_entities(metadata: Dict[str, Any]) -> None:
    roster = metadata.get("roster")
    if not isinstance(roster, list):
        return
    from dual_pipeline.hitl_governance_core.speaker_resolution_contract import strip_org_suffix_from_role
    from .c06_graphdb_role_gate import _sync_hitl_roles_to_roster
    _sync_hitl_roles_to_roster(metadata)
    hitl, spk, kwargs = metadata.get("hitl_role_resolutions") or {}, metadata.get("speaker_role_resolutions") or {}, _get_resolver_kwargs(metadata)
    for item in [i for i in roster if isinstance(i, dict) and i.get("name")]:
        _normalize_single_roster_item(item, kwargs)
        res = hitl.get(item["name"]) or (spk.get(item["name"], {}).get("role") if isinstance(spk.get(item["name"]), dict) else spk.get(item["name"]))
        if res:
            item["role"], found_o = strip_org_suffix_from_role(str(res))
            item["status"] = "RESOLVED"
            if found_o:
                item["org"] = found_o
            item.pop("requires_operator_confirmation", None)
    metadata["attendees"] = _group_roster_by_org(roster)


def _normalize_speaker_headers(raw_transcript: str, metadata: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity
    clean_text, normalizations, kwargs = raw_transcript, [], _get_resolver_kwargs(metadata)
    header_re = re.compile(r"(?m)^(\s*(?:\[[\d:]+\]\s*)?)\*\*([A-Za-z\s\.\-']+?)(?::\*\*|\*\*:)")
    bold_headers = {m.group(2) for m in header_re.finditer(raw_transcript)}
    for spk in bold_headers:
        spk_clean = spk.strip()
        if not spk_clean or len(spk_clean.split()) > 4 or re.match(r"^Speaker\s+[A-Z0-9]+$", spk_clean, re.I):
            continue
        ent = resolve_person_entity(spk_clean, **kwargs)
        if ent and ent.canonical_name != spk_clean:
            pattern = re.compile(rf"(?m)^(\s*(?:\[[\d:]+\]\s*)?)\*\*{re.escape(spk_clean)}(?::\*\*|\*\*:)")
            replacement = rf"\1**{ent.canonical_name}:**"
            clean_text, count = pattern.subn(replacement, clean_text)
            if count > 0:
                logger.info("Stage U0 Normalization: Rewrote speaker header '**%s:**' -> '**%s:**' (%d turns, source: %s)", spk_clean, ent.canonical_name, count, ent.source)
                normalizations.append({"raw_speaker": spk_clean, "canonical_name": ent.canonical_name, "turns_normalized": count, "source": ent.source})
    return clean_text, normalizations


def _normalize_transcript_entities(raw_transcript: str, metadata: Dict[str, Any]) -> str:
    """Stage U0 Entity Normalization Pass."""
    clean_text, normalizations = _normalize_speaker_headers(raw_transcript, metadata)
    _normalize_roster_entities(metadata)
    if normalizations:
        metadata["entity_normalizations"] = normalizations
    return clean_text


class U0IngestionBoundary:
    """U0: Validate source, compute digest, detect diarization status, and scaffold metadata."""

    def execute(self, raw_transcript: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("Executing U0: Untrusted Ingestion Boundary")
        if "copilot_feedback" in raw_transcript.lower() or "copilot_feedback" in str(metadata.get("input_path", "")).lower():
            raise ValueError("Constraint C-29: copilot_feedback telemetry cannot be ingested into Stage U0 raw transcript boundary")

        assert_no_u0_hitl_bypass(metadata)
        metadata = validate_diarization_toggle(metadata)
        hashed = hashlib.sha256(raw_transcript.encode()).hexdigest()

        orig_metadata = metadata
        metadata = dict(metadata)
        metadata["_orig_metadata"] = orig_metadata
        ingestion_ctx = metadata.get("ingestion_context")
        if isinstance(ingestion_ctx, dict):
            assert_no_u0_hitl_bypass(ingestion_ctx)
            for k in ("meeting_name", "date", "time"):
                if k in ingestion_ctx and k not in metadata:
                    metadata[k] = ingestion_ctx[k]
            metadata.setdefault("classification", ingestion_ctx.get("default_classification") or ingestion_ctx.get("classification") or "internal")

        from dual_pipeline.canonical_pipeline.u0_ingress import (
            batch_validate_preflight_checks, hydrate_live_container_artifacts, raise_if_preflight_failed,
        )
        from dual_pipeline.transcript_stages_core.output01_parser import (
            is_completed_output_01_path, is_completed_output_01_text, parse_completed_output_01,
        )
        is_completed_out01 = is_completed_output_01_path(metadata.get("input_path")) or is_completed_output_01_text(raw_transcript)
        if is_completed_out01:
            parse_completed_output_01(raw_transcript, metadata)
            if isinstance(orig_metadata, dict):
                orig_metadata["freeze_output_1"] = True
                orig_metadata["turn_count"] = metadata.get("turn_count")
        hydrate_live_container_artifacts(metadata, input_path=metadata.get("input_path"))
        self._ensure_metadata_defaults(metadata, raw_transcript)
        raise_if_preflight_failed(batch_validate_preflight_checks(metadata, raw_transcript=raw_transcript, input_path=metadata.get("input_path")))

        self._inspect_roster_completeness(metadata, raw_transcript)
        verify_mandatory_attendee_confirmation(metadata, raw_transcript)

        required_keys = {"project", "meeting_name", "date", "time", "classification"}
        if not required_keys.issubset(metadata.keys()):
            raise ValueError(f"Metadata missing required keys: {required_keys - set(metadata.keys())}")

        diarization_status = self._detect_diarization_status(raw_transcript, metadata)
        metadata["diarization_status"] = diarization_status
        logger.info(f"U0: diarization_status={diarization_status}")

        from dual_pipeline.temporal_resolution import detect_multi_meeting_boundaries
        segments = detect_multi_meeting_boundaries(raw_transcript)
        if len(segments) > 1:
            metadata["detected_meeting_segments"] = segments
            logger.warning("Stage U0 detected %d distinct meeting segments in input transcript (triggers: %s)", len(segments), [s["boundary_trigger"] for s in segments])
            raise ValueError(f"Stage U0 Ingestion Error: Multi-meeting transcript detected ({len(segments)} segments). Slice into separate meeting inputs before ingestion.")

        norm_base = metadata.pop("_normalized_for_diarization", None) or raw_transcript
        if is_completed_out01 or metadata.get("freeze_output_1"):
            _normalize_roster_entities(metadata)
            clean_transcript = raw_transcript
        else:
            clean_transcript = _normalize_transcript_entities(norm_base, metadata)
        metadata.pop("_orig_metadata", None)
        return {"hash": hashed, "clean_transcript": clean_transcript, "metadata": metadata, "diarization_status": diarization_status}

    @staticmethod
    def _ensure_metadata_defaults(metadata: Dict[str, Any], raw_transcript: str) -> None:
        """Derive or default metadata attributes so raw inputs execute without manual scripting."""
        metadata.setdefault("classification", "confidential")
        metadata.setdefault("project", metadata.get("project_name") or metadata.get("project_id") or metadata.get("workstream") or "general")
        metadata.setdefault("meeting_name", metadata.get("meeting") or metadata.get("title"))
        metadata.setdefault("date", metadata.get("meeting_date"))
        metadata.setdefault("time", metadata.get("meeting_time"))
        for fn in (_default_date_and_filenames, _default_time_and_filenames, _default_meeting_name, _default_platform, _default_attendees_and_roster, _default_speaker_map):
            fn(metadata, raw_transcript)

    @staticmethod
    def _collect_unverified_attendees(
        pre_roles: Dict[str, Any], metadata: Dict[str, Any], raw_transcript: str = ""
    ) -> List[Tuple[str, str]]:
        from dual_pipeline.transcript_metadata_core.transcript_placeholder_detector import is_placeholder_role
        from dual_pipeline.canonical_pipeline.u0_ingress import _load_ledger_role_resolutions
        _load_ledger_role_resolutions(metadata)
        hitl_receipts, hitl_resolutions = metadata.get("hitl_role_receipts", {}), metadata.get("hitl_role_resolutions", {})

        missing_attendees = []
        from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity
        as_of = metadata.get("date") or metadata.get("as_of_timestamp")
        r_root = metadata.get("repo_root")
        transcript = raw_transcript or str(metadata.get("raw_transcript") or metadata.get("transcript") or "")

        for name, role in list(pre_roles.items()):
            if not name or name in hitl_receipts:
                continue
            role_str = str(role or "").strip()
            ent = resolve_person_entity(name, repo_root=r_root, runtime_root=metadata.get("runtime_root"), generation_id=metadata.get("generation_id"), as_of_timestamp=as_of, explicit_role=role_str if role_str and not is_placeholder_role(role_str) else None)
            parts = ent.canonical_name.split() if (ent and ent.canonical_name) else []
            sur_in_trans = bool(transcript and len(parts) > 1 and re.search(rf"\b{re.escape(parts[-1].lower())}\b", transcript, re.I))
            is_single = len(name.split()) == 1 and (
                not sur_in_trans or ent is None or getattr(ent, "requires_operator_confirmation", False)
                or (len(parts) == 1 and getattr(ent, "source", "") not in ("authoritative_correction", "directory_prior", "charter_csv"))
            )
            if name in hitl_resolutions:
                if is_single or ent is None or getattr(ent, "requires_operator_confirmation", False) or getattr(ent, "status", "") in ("AMBIGUOUS_CONFLICT", "UNRESOLVED_GRAPH_ATTENDEE", "UNRESOLVED_CANDIDATE_ONLY"):
                    from dual_pipeline.hitl_governance_core.speaker_resolution_contract import verify_ledger_backed_role_resolutions
                    verified, rejected = verify_ledger_backed_role_resolutions(metadata, repo_root=r_root)
                    if name in rejected or name not in verified:
                        missing_attendees.append((name, role_str))
                continue
            role_lower = role_str.lower()
            if not is_single and (not role_str or is_placeholder_role(role_str) or "role not listed" in role_lower or "title not provided" in role_lower):
                if ent and ent.role and not is_placeholder_role(ent.role) and "role not listed" not in ent.role.lower() and not ent.requires_operator_confirmation:
                    logger.info("U0 Ingestion: Auto-resolving '%s' placeholder role '%s' -> '%s' from %s", name, role_str, ent.role, ent.source)
                    from dual_pipeline.transcript_stages_core.c05_title_gate import apply_hitl_role_resolution
                    apply_hitl_role_resolution(metadata, name, ent.role, is_human_confirmed=False, provenance=ent.source or "ENTERPRISE_DIRECTORY")
                    continue
                missing_attendees.append((name, role_str))
            elif ent is None or ent.requires_operator_confirmation or ent.status in ("AMBIGUOUS_CONFLICT", "UNRESOLVED_GRAPH_ATTENDEE") or is_single:
                missing_attendees.append((name, role_str))
        return missing_attendees

    @staticmethod
    def _inspect_roster_completeness(metadata: Dict[str, Any], raw_transcript: str = "") -> None:
        """Inspect attendee roster and enforce verified, non-placeholder roles."""
        from dual_pipeline.transcript_stages_core.attendee_gate import _is_document_only_metadata, _is_notes_or_doc_metadata, find_unrecognized_single_token_attendees
        transcript = raw_transcript or str(metadata.get("raw_transcript") or metadata.get("transcript") or "")
        if _is_document_only_metadata(metadata):
            return
        if (_is_notes_or_doc_metadata(metadata) or any(metadata.get(k) for k in ("output3_only", "document_only", "test_fast"))) and not find_unrecognized_single_token_attendees(metadata, transcript):
            return
        if metadata.get("attendee_roles_confirmed_by_operator") and (metadata.get("hitl_role_receipts") or metadata.get("hitl_role_resolutions")):
            return
        from dual_pipeline.transcript_stages_core.c0_gates import _extract_pre_enrichment_roles
        roster = metadata.get("roster") or metadata.get("attendees")
        if not roster:
            return
        pre_roles = metadata.get("pre_enrichment_roles")
        if not isinstance(pre_roles, dict) or not pre_roles:
            metadata["pre_enrichment_roles"] = pre_roles = dict(_extract_pre_enrichment_roles(roster))
        missing_attendees = U0IngestionBoundary._collect_unverified_attendees(pre_roles, metadata, transcript)
        if not missing_attendees:
            return
        metadata["invalidate_prior_outputs"] = True
        if isinstance(metadata.get("_orig_metadata"), dict):
            metadata["_orig_metadata"]["invalidate_prior_outputs"] = True
        from dual_pipeline.transcript_stages_core.c05_title_gate import raise_hitl_for_titles_batch
        raise_hitl_for_titles_batch(missing_attendees, metadata)

    @staticmethod
    def _check_generic_speakers(transcript: str, metadata: Dict[str, Any]) -> Optional[str]:
        from dual_pipeline.transcript_pipeline_core.source_modality import _collect_generic_labels, _find_dialogue_body, _has_unmapped_generic_speakers, extract_all_turn_speakers
        dialogue = _find_dialogue_body(transcript)
        if not _collect_generic_labels(dialogue, extract_all_turn_speakers(dialogue)):
            return None
        real_map = {k: v for k, v in (metadata.get("speaker_map") or {}).items() if v and not _GENERIC_SPEAKER_PATTERN.fullmatch(str(v).strip())} if isinstance(metadata.get("speaker_map"), dict) else {}
        if not real_map:
            from dual_pipeline.canonical_pipeline.u0_ingress import hydrate_live_container_artifacts
            hydrate_live_container_artifacts(metadata, input_path=metadata.get("input_path"))
            real_map = {k: v for k, v in (metadata.get("speaker_map") or {}).items() if v and not _GENERIC_SPEAKER_PATTERN.fullmatch(str(v).strip())} if isinstance(metadata.get("speaker_map"), dict) else {}
        has_c0_evidence = bool(metadata.get("speaker_hints") or metadata.get("turn_hints") or metadata.get("has_acoustic_diarization") or metadata.get("acoustic_clusters") or metadata.get("audio_sha256"))
        from dual_pipeline.exceptions import UnmappedSpeakerError
        if real_map:
            if not _has_unmapped_generic_speakers(dialogue, real_map):
                return "DIARIZED_ACOUSTIC_RESOLVED"
            if has_c0_evidence:
                return "DIARIZED_ACOUSTIC"
            raise UnmappedSpeakerError("Immediate Unmapped Speaker Blocker: Transcript contains generic speakers that are not fully mapped in the provided speaker_map.")
        if has_c0_evidence:
            return "DIARIZED_ACOUSTIC"
        if metadata.get("allow_undiarized"):
            if (
                metadata.get("notes_mode") or metadata.get("is_notes") or metadata.get("canonical_modality") == "MEETING_NOTES"
                or str(metadata.get("source_type") or "").lower() in ("copilot_notes", "notes", "meeting_notes")
            ):
                return "NATIVE"
            src, rost = str(metadata.get("source_type") or "").upper(), (metadata.get("roster") or metadata.get("attendees") or [])
            r_cnt = len([r.strip() for r in rost.split(",") if r.strip()]) if isinstance(rost, str) else len(rost)
            if src.startswith("AUDIO") or metadata.get("has_acoustic_diarization") or r_cnt >= 2:
                raise ValueError("UNDIARIZED_DISQUALIFIED_FOR_AUDIO: Undiarized ingestion is strictly prohibited for multi-attendee or audio sources. Speaker attribution and diarization required.")
            return "UNDIARIZED"
        raise UnmappedSpeakerError("Immediate Unmapped Speaker Blocker: Transcript contains generic speakers but no speaker_map was provided. Halting execution to prevent retry loops.")

    @staticmethod
    def _check_audio_diarization(transcript: str, metadata: Dict[str, Any]) -> str:
        if metadata.get("diarization_status") in ("VERIFIED_STEREO_SEPARATION", "HITL_REQUIRED"):
            return str(metadata["diarization_status"])
        if not (metadata.get("has_acoustic_diarization") or metadata.get("source_type") in ("AUDIO_DIARIZED", "VERIFIED_STEREO_SEPARATION")):
            return "UNDIARIZED"
        if s_map := metadata.get("speaker_map"):
            from dual_pipeline.transcript_pipeline_core.source_modality import _has_unmapped_generic_speakers
            if not _has_unmapped_generic_speakers(transcript, s_map):
                return "DIARIZED_ACOUSTIC_RESOLVED"
        return "DIARIZED_ACOUSTIC"

    @classmethod
    def _detect_diarization_status(cls, transcript: str, metadata: Dict[str, Any]) -> str:
        """Heuristic diarization detection."""
        if (
            metadata.get("notes_mode") or metadata.get("is_notes") or metadata.get("canonical_modality") == "MEETING_NOTES"
            or str(metadata.get("source_type") or "").lower() in ("copilot_notes", "notes", "meeting_notes")
        ):
            return "NATIVE"
        if (st := metadata.get("diarization_status")) and str(st).upper() in ("NATIVE", "UNDIARIZED", "DIARIZED_ACOUSTIC", "DIARIZED_ACOUSTIC_RESOLVED", "VERIFIED_STEREO_SEPARATION", "HITL_REQUIRED"):
            return str(st).upper()
        for tok in ("DIARIZED_ACOUSTIC_RESOLVED", "DIARIZED_ACOUSTIC", "VERIFIED_STEREO_SEPARATION"):
            if tok in transcript:
                return tok
        if transcript and transcript.lstrip().startswith(("{", "[", "WEBVTT")) or "-->" in transcript[:500]:
            try:
                from dual_pipeline.transcript_pipeline_core.normalizer import normalize_raw_input_text
                norm_res = normalize_raw_input_text(transcript, input_ext=str(metadata.get("input_ext") or ""), metadata=metadata)
                norm_txt = norm_res.text if hasattr(norm_res, "text") else str(norm_res)
                if norm_txt and norm_txt != transcript:
                    transcript = norm_txt
                    metadata["_normalized_for_diarization"] = norm_txt
            except Exception:
                pass
        src_type = metadata.get("source_type")
        if metadata.get("source_modality") == "AUDIO_ONLY_UNDIARIZED" or src_type in ("AUDIO_ONLY_UNDIARIZED", "AUDIO_TRANSCRIBED", "LIVE_AUDIO", "AUDIO_DIARIZED", "VERIFIED_STEREO_SEPARATION") or bool(metadata.get("_from_audio_transcription")):
            return cls._check_audio_diarization(transcript, metadata)
        if (generic_status := cls._check_generic_speakers(transcript, metadata)) is not None:
            return generic_status
        platform = (metadata.get("platform") or "").lower()
        if any(p in platform for p in ("apple voice memo", "voice memo", "dictaphone", "recorder", "voice recorder", "audio")):
            return "UNDIARIZED"
        native_matches = count_native_diarized_turns(transcript)
        if native_matches < 3 and transcript and transcript.strip():
            try:
                from dual_pipeline.transcript_pipeline_core.normalizer import normalize_raw_input_text
                norm_res = normalize_raw_input_text(transcript, input_ext=str(metadata.get("input_ext") or ""), metadata=metadata)
                norm_txt = norm_res.text if hasattr(norm_res, "text") else str(norm_res)
                norm_matches = count_native_diarized_turns(norm_txt)
                if norm_matches > native_matches:
                    native_matches = norm_matches
                    metadata["_normalized_for_diarization"] = norm_txt
            except Exception:
                pass
        if not metadata.get("_platform_defaulted") and any(p in platform for p in ("teams", "zoom", "google meet", "webex", "otter", "copilot")):
            if native_matches < 3:
                logger.warning("Platform suggests diarization but transcript lacks sufficient native speaker turns. Classifying as UNDIARIZED.")
                return "UNDIARIZED"
            return "NATIVE"
        return "NATIVE" if native_matches >= 3 else "UNDIARIZED"

