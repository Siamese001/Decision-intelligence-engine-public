"""
dual_pipeline/transcript_stages_core/attendee_gate.py

Mandatory Attendee HITL Confirmation Gate for Audio Files and Raw Transcripts.
Enforces that any pipeline execution ingesting audio or raw (unstructured) transcripts
MUST surface an atomic HITL decision to confirm the meeting attendee list before
advancing to synthesis. Fails closed if unconfirmed.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .models import HITLRequiredError

logger = logging.getLogger("dual_pipeline.transcript_stages_core.attendee_gate")


_AUDIO_EXTS = frozenset({".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".wma", ".raw", ".mp4", ".webm"})
_AUDIO_TYPES = frozenset({"AUDIO_ONLY_UNDIARIZED", "AUDIO_TRANSCRIBED", "UNDIARIZED_TEXT", "LIVE_AUDIO"})
_RAW_TYPES = frozenset({"UNSTRUCTURED_TRANSCRIPT", "RAW_TRANSCRIPT", "TEXT_TRANSCRIPT", "TRANSCRIPT"})


def _has_verified_stage01_checkpoint(metadata: Dict[str, Any]) -> bool:
    if not isinstance(metadata, dict):
        return False
    if metadata.get("stage01_checkpoint_passed") or metadata.get("stage01_passed") or metadata.get("output_1_frozen"):
        return True
    rcpt = (
        metadata.get("stage_01_checkpoint_receipt")
        or metadata.get("stage01_checkpoint_receipt")
        or metadata.get("stage01_qa_gate1")
    )
    return bool(isinstance(rcpt, dict) and (rcpt.get("passed") or rcpt.get("status") in ("PASS", "RESOLVED", "RESOLVED_HITL")))


def _is_notes_or_doc_metadata(metadata: Dict[str, Any]) -> bool:
    return bool(
        metadata.get("notes_mode") or metadata.get("is_notes") or metadata.get("is_document")
        or metadata.get("canonical_modality") in ("MEETING_NOTES", "DOCUMENT")
        or str(metadata.get("source_type") or "").lower() in ("copilot_notes", "notes", "meeting_notes", "document")
    )


def is_audio_or_raw_source(metadata: Dict[str, Any]) -> bool:
    """Detects if input originates from an audio recording or raw unstructured transcript."""
    if _is_notes_or_doc_metadata(metadata):
        return False
    input_path = str(metadata.get("input_path", ""))
    ext = Path(input_path).suffix.lower() if input_path else str(metadata.get("input_ext", "")).lower()
    if ext in _AUDIO_EXTS or metadata.get("_from_audio_transcription") or metadata.get("is_audio") or metadata.get("is_raw_transcript"):
        return True
    st, sm = str(metadata.get("source_type", "")).upper(), str(metadata.get("source_modality", "")).upper()
    if ext in (".txt", ".transcript", ".vtt", ".srt", ".docx") and not _has_verified_stage01_checkpoint(metadata):
        if st == "DIARIZED_TEXT" or sm == "DIARIZED_TRANSCRIPT":
            return False
        has_multi_token_roster = bool(metadata.get("attendees") or metadata.get("roster")) and not find_unrecognized_single_token_attendees(metadata)
        return not has_multi_token_roster
    if st in _AUDIO_TYPES or sm in _AUDIO_TYPES:
        return True
    return bool((st in _RAW_TYPES or sm in _RAW_TYPES) and not _has_verified_stage01_checkpoint(metadata))


def _extract_name_from_roster_item(item: Any) -> Optional[str]:
    if isinstance(item, dict) and item.get("name"):
        return str(item["name"]).strip()
    if isinstance(item, str) and item.strip():
        m = re.match(r"^([^(]+)", item)
        return (m.group(1) if m else item).strip()
    return None


def _extract_from_roster(metadata: Dict[str, Any]) -> List[str]:
    roster = metadata.get("roster") or metadata.get("attendees")
    items: List[Any] = roster if isinstance(roster, list) else [x for v in (roster.values() if isinstance(roster, dict) else []) if isinstance(v, list) for x in v]
    return [name for item in items if (name := _extract_name_from_roster_item(item))]


def _extract_from_speaker_map(metadata: Dict[str, Any]) -> List[str]:
    speaker_map = metadata.get("speaker_map")
    return [str(val).strip() for val in (speaker_map.values() if isinstance(speaker_map, dict) else []) if val and str(val).strip()]


def _extract_from_transcript_speakers(transcript_text: str) -> List[str]:
    if not transcript_text:
        return []
    from .constants import is_valid_colon_speaker_label
    from dual_pipeline.transcript_pipeline_core.source_modality import extract_unique_speakers
    if re.search(r"^###\s+\[", transcript_text, re.MULTILINE):
        out: List[str] = []
        for s in sorted(extract_unique_speakers(transcript_text)):
            name = re.sub(r"\(.*?\)", "", re.sub(r"(?:\s*[—–]\s*|\s+\-\s+).*$", "", s)).strip().title()
            if is_valid_colon_speaker_label(name):
                out.append(name)
        return out
    speaker_matches = re.findall(r"^([A-Z][a-zA-Z0-9_\-\s]{1,30}):", transcript_text, re.MULTILINE)
    return [s.strip() for s in speaker_matches if is_valid_colon_speaker_label(s.strip())]


def _extract_inferred_acoustic_candidates(metadata: Dict[str, Any]) -> List[str]:
    inferred = metadata.get("inferred_acoustic_candidates")
    if not inferred and any(
        metadata.get(k) for k in ("preflight_sample_embeddings", "voiceprint_db_path", "audio_path")
    ):
        try:
            from dual_pipeline.live_meeting_recorder_core.stage0_speaker_preloader import (
                run_acoustic_roster_preflight,
            )
            inferred = run_acoustic_roster_preflight(
                audio_path=metadata.get("audio_path") or metadata.get("input_path"),
                sample_embeddings=metadata.get("preflight_sample_embeddings"),
                project=str(metadata.get("project") or "GLOBAL"),
                voiceprint_db_path=metadata.get("voiceprint_db_path"),
                speaker_memory_path=metadata.get("speaker_memory_path"),
                graph_db_path=metadata.get("graph_db_path"),
            )
            if inferred:
                metadata["inferred_acoustic_candidates"] = inferred
        except Exception as exc:
            logger.debug("Stage U0 acoustic preflight candidate extraction skipped: %s", exc)
    if not isinstance(inferred, list):
        return []
    return [
        str(item.get("name") if isinstance(item, dict) else item).strip()
        for item in inferred
        if (item.get("name") if isinstance(item, dict) else item)
    ]


def extract_candidate_attendees(metadata: Dict[str, Any], transcript_text: str = "") -> List[str]:
    """Extracts candidate attendee names from metadata, transcript speaker turns, or Stage-0 acoustic preflight."""
    raw_text = transcript_text or str(metadata.get("raw_transcript") or metadata.get("transcript") or "")
    candidates = _extract_from_roster(metadata) + _extract_from_speaker_map(metadata) + _extract_from_transcript_speakers(raw_text)
    if not candidates:
        candidates = _extract_inferred_acoustic_candidates(metadata)
    deduped = list(dict.fromkeys(candidates))
    return deduped if deduped else ["Unknown Speaker(s)"]



def _check_single_token_candidate(cand: Any, raw_text: str, ctx_kwargs: Dict[str, Any], metadata: Dict[str, Any]) -> Optional[str]:
    from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity
    from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import is_blacklisted_attendee
    clean_name = re.sub(r"\(.*?\)", "", str(cand or "")).strip()
    tokens = clean_name.split() if clean_name and not is_blacklisted_attendee(clean_name) else []
    if len(tokens) != 1 or tokens[0].lower() in ("speaker", "unknown", "participant", "attendee", "note", "agenda", "slide"):
        return None
    ent = resolve_person_entity(clean_name, **ctx_kwargs)
    c_parts = ent.canonical_name.split() if (ent and ent.canonical_name) else []
    surname_grounded = bool(raw_text and len(c_parts) > 1 and re.search(rf"\b{re.escape(c_parts[-1].lower())}\b", raw_text, re.IGNORECASE))
    needs_confirm = (
        ent is None or getattr(ent, "requires_operator_confirmation", False)
        or getattr(ent, "status", "") in ("AMBIGUOUS_CONFLICT", "UNRESOLVED_GRAPH_ATTENDEE", "UNRESOLVED_CANDIDATE_ONLY")
        or not surname_grounded
    )
    if needs_confirm and clean_name not in metadata.get("hitl_role_resolutions", {}) and clean_name not in metadata.get("hitl_role_receipts", {}):
        return clean_name
    return None


def find_unrecognized_single_token_attendees(metadata: Dict[str, Any], transcript_text: str = "") -> List[str]:
    """Finds single-token first-name attendees lacking verified multi-token directory resolution."""
    rcpt = metadata.get("attendee_confirmation_receipt")
    if metadata.get("attendees_confirmed_by_operator") is True and isinstance(rcpt, dict) and rcpt.get("status") == "RESOLVED_HITL" and (rcpt.get("subject_sha256") or metadata.get("subject_sha256")):
        return []
    raw_text = transcript_text or str(metadata.get("raw_transcript") or metadata.get("transcript") or "")
    ctx_kwargs = {
        "repo_root": metadata.get("repo_root"), "runtime_root": metadata.get("runtime_root"),
        "generation_id": metadata.get("generation_id"),
        "as_of_timestamp": metadata.get("as_of_timestamp") or metadata.get("meeting_date") or metadata.get("date"),
        "project_name": metadata.get("project"),
    }
    unrecognized = [u for c in extract_candidate_attendees(metadata, raw_text) if (u := _check_single_token_candidate(c, raw_text, ctx_kwargs, metadata))]
    return sorted(list(dict.fromkeys(unrecognized)))


def _is_document_only_metadata(metadata: Dict[str, Any]) -> bool:
    return bool(
        metadata.get("is_document")
        or metadata.get("canonical_modality") == "DOCUMENT"
        or str(metadata.get("source_type") or "").lower() == "document"
    )


def _compute_acoustic_roster_confidence(metadata: Dict[str, Any]) -> Tuple[float, int, str, List[Any]]:
    inferred_list = metadata.get("inferred_acoustic_candidates")
    if not isinstance(inferred_list, list) or not inferred_list:
        return 0.90, 90, "HIGH", []
    scores = [
        float(it["confidence"])
        for it in inferred_list
        if isinstance(it, dict) and isinstance(it.get("confidence"), (int, float))
    ]
    rec_conf = round(max(0.80, min(0.98, sum(scores) / len(scores))), 2) if scores else 0.90
    rec_pct = int(round(rec_conf * 100))
    return rec_conf, rec_pct, ("HIGH" if rec_conf >= 0.85 else "MEDIUM"), inferred_list


def verify_mandatory_attendee_confirmation(metadata: Dict[str, Any], transcript_text: str = "") -> None:
    """Verifies that meeting attendees have been explicitly confirmed via HITL when required."""
    if _is_document_only_metadata(metadata):
        return
    is_audio = is_audio_or_raw_source(metadata)
    unrecognized_single = find_unrecognized_single_token_attendees(metadata, transcript_text)

    if not is_audio and not unrecognized_single:
        return

    if metadata.get("attendees_confirmed_by_operator") is True:
        receipt = metadata.get("attendee_confirmation_receipt")
        if not isinstance(receipt, dict) or receipt.get("status") != "RESOLVED_HITL":
            raise ValueError(
                "Attendee Gate Security Violation: 'attendees_confirmed_by_operator' is True "
                "but valid 'attendee_confirmation_receipt' with status 'RESOLVED_HITL' is missing."
            )
        subj = receipt.get("subject_sha256") or metadata.get("subject_sha256")
        if not subj:
            raise ValueError(
                "Attendee Gate Security Violation: Unauthenticated confirmation metadata. "
                "Missing cryptographic subject_sha256 binding in attendee_confirmation_receipt."
            )
        return

    attendees = extract_candidate_attendees(metadata, transcript_text)
    attendee_summary = ", ".join(attendees)
    meeting_name = metadata.get("meeting_name", "Ingested Meeting")

    if is_audio:
        reason_desc = f"Input source is audio or raw transcript for '{meeting_name}'."
    else:
        unrec_str = ", ".join(unrecognized_single)
        reason_desc = f"Input source '{meeting_name}' contains unrecognized single-token attendee(s): [{unrec_str}]."

    prompt = (
        f"Mandatory Stage U0 Attendee Confirmation Gate:\n"
        f"{reason_desc}\n"
        f"Identified Candidate Attendees: {attendee_summary}\n\n"
        f"Repository governance mandates explicit operator confirmation of meeting participants "
        f"before proceeding to downstream synthesis."
    )

    rec_conf, rec_pct, rec_tier, inferred_list = _compute_acoustic_roster_confidence(metadata)
    options = [
        {
            "label": f"(Recommended) Confirm identified attendee roster: [{attendee_summary}] — Confidence: {rec_tier} ({rec_pct}%)",
            "action": "CONFIRM_ROSTER", "attendees": attendees,
            "confidence": rec_conf, "confidence_score": rec_conf,
            "confidence_level": f"{rec_tier} ({rec_pct}%)", "tier": rec_tier,
            "is_recommended": True, "inferred_acoustic_candidates": inferred_list,
        },
        {
            "label": "Modify, rename, or provide additional attendees — Confidence: MEDIUM (70%)",
            "action": "MODIFY_ROSTER", "confidence": 0.70, "confidence_score": 0.70,
            "confidence_level": "MEDIUM (70%)", "tier": "MEDIUM", "is_recommended": False,
        },
    ]
    raise HITLRequiredError(
        prompt, hitl_type="attendee_confirmation", missing_fields=["attendees_confirmed_by_operator"],
        options=options, unmapped_speakers=unrecognized_single if unrecognized_single else attendees, prompt=prompt,
    )


def _collect_names_from_items(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    return [name for it in items if (name := _extract_name_from_roster_item(it))]


def _extract_flat_confirmed_attendees(
    confirmed_attendees: Union[List[str], Dict[str, Any]]
) -> List[str]:
    if isinstance(confirmed_attendees, list):
        return _collect_names_from_items(confirmed_attendees)
    if isinstance(confirmed_attendees, dict):
        flat: List[str] = []
        for val in confirmed_attendees.values():
            flat.extend(_collect_names_from_items(val))
        return flat
    return []


from .attendee_profiles import (
    extract_structured_confirmed_attendees,
    is_attendee_field_confirmed,
)


def record_attendee_confirmation(
    metadata: Dict[str, Any],
    confirmed_attendees: Union[List[str], Dict[str, Any]],
    rationale: str = "Operator confirmed meeting attendee roster",
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Records operator attendee confirmation receipt and updates metadata.
    Constructs canonical subject digest, creates signed Ed25519 approval,
    and logs an entry to hitl_decision_ledger.json.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    flat_names = _extract_flat_confirmed_attendees(confirmed_attendees)
    m_name = str(metadata.get("meeting_name") or "")
    m_date = str(metadata.get("date") or metadata.get("meeting_date") or "")
    m_proj = str(metadata.get("project") or "nea")

    structured_profiles = extract_structured_confirmed_attendees(
        confirmed_attendees, default_scope=m_name or None
    )

    has_extended_confirmations = any(
        bool(set(p.get("confirmed_fields", ["name"])) - {"name"})
        for p in structured_profiles
    )

    receipt = {
        "decision_type": "ATTENDEE_CONFIRMATION",
        "timestamp_utc": timestamp,
        "confirmed_attendees": confirmed_attendees,
        "structured_attendees": structured_profiles,
        "confirmed_fields": sorted(list({f for p in structured_profiles for f in p.get("confirmed_fields", ["name"])})),
        "rationale": rationale,
        "status": "RESOLVED_HITL",
    }
    metadata["attendees_confirmed_by_operator"] = True
    metadata["attendee_confirmation_receipt"] = receipt
    metadata["structured_attendees"] = structured_profiles

    if isinstance(confirmed_attendees, (list, dict)):
        metadata["attendees"] = confirmed_attendees
        from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
            build_roster_from_attendees,
        )
        metadata["roster"] = build_roster_from_attendees(confirmed_attendees)
        metadata.pop("pre_enrichment_roles", None)

    subject_dict: Dict[str, Any] = {
        "meeting_name": m_name,
        "meeting_date": m_date,
        "project": m_proj,
        "attendees": sorted(flat_names),
    }

    if has_extended_confirmations:
        subject_dict["structured_attendees"] = sorted(
            [
                {
                    "name": p["name"],
                    "role": p.get("role"),
                    "organization": p.get("organization"),
                    "scope": p.get("scope"),
                    "confirmed_fields": sorted(p.get("confirmed_fields", ["name"])),
                }
                for p in structured_profiles
            ],
            key=lambda x: x["name"],
        )

    try:
        from dual_pipeline.hitl_governance_core.crypto import (
            append_signed_approval_to_ledger,
            compute_alr_subject_digest,
            create_signed_hitl_approval_entry,
            get_governed_hitl_keypair,
        )
        subj_digest = compute_alr_subject_digest(subject_dict)
        receipt["subject_sha256"] = subj_digest
        metadata["subject"] = subject_dict
        metadata["subject_sha256"] = subj_digest
        metadata["context_sha256"] = subj_digest

        priv, pub = get_governed_hitl_keypair()
        op_id = metadata.get("reviewer") or metadata.get("operator_id") or "human_operator"
        signed_entry = create_signed_hitl_approval_entry(
            subject_sha256=subj_digest,
            case_ids=["ATTENDEE_CONFIRMATION", "ROSTER_CONFIRMATION"],
            decision="APPROVE",
            operator_id=op_id,
            private_key=priv,
            public_key=pub,
            meeting_slug=m_name or None,
            meeting_date=m_date or None,
            metadata={
                "meeting_name": m_name,
                "meeting_date": m_date,
                "project": m_proj,
                "confirmed_attendees": flat_names,
                "rationale": rationale,
            },
        )
        from dual_pipeline.hitl_governance_core.ledger import _find_default_ledger_path

        target_ledger = (
            Path(metadata["hitl_ledger_path"]).resolve()
            if metadata.get("hitl_ledger_path")
            else (
                repo_root / "hitl_decision_ledger.json"
                if repo_root and (repo_root / "hitl_decision_ledger.json").is_file()
                else _find_default_ledger_path().resolve()
            )
        )
        metadata["hitl_ledger_path"] = str(target_ledger)
        receipt["hitl_ledger_path"] = str(target_ledger)
        append_signed_approval_to_ledger(signed_entry, ledger_path=target_ledger)
        receipt["signed_entry_id"] = signed_entry.get("entry_id")

        receipt["subject_sha256"] = subj_digest
        receipt["entry_sha256"] = signed_entry.get("entry_sha256")
    except Exception as e:
        logger.warning("Could not sign HITL attendee confirmation entry: %s", e)

    return receipt

