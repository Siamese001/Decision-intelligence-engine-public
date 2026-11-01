"""Meeting adapters and helpers that emit source-bound discrete candidates."""
from __future__ import annotations

from typing import Any

from ..contracts import create_artifact, validate_artifact
from ..core import digest, normalize_iso_date, normalize_space, stable_id, trace_id
from ..l0.speaker_and_qualifier_policy import clean_non_placeholder_speaker, extract_inline_turn_speaker, is_placeholder_speaker
from .constants import AUTHORITY_RANKS
from .meeting_entities import process_projection_signals
from .meeting_entity_helpers import (
    _context_extension,
    _extension,
    _markdown_semantic_correction,  # noqa: F401
    _mention,
    _role_workstream,
    _statement_semantics,
)


def _sanitize_locator(loc: Any) -> Any:
    if not isinstance(loc, dict):
        return loc
    kind, val = loc.get("kind"), str(loc.get("value") or "")
    if kind == "attachment":
        return {**loc, "kind": "region", "value": val if val.startswith("attachment:") else f"attachment:{val}"}
    if kind == "transcript_timestamp":
        return {**loc, "kind": "timestamp"}
    if kind in {"c0_receipt", "hitl_decision_ledger", "ledger", "roster"}:
        return {**loc, "kind": "region", "value": val if val.startswith("hitl_decision_ledger") else (f"hitl_decision_ledger:{val}" if val else "hitl_decision_ledger")}
    if kind in {"line_block", "paragraph", "heading", "list_item", "front_matter", "code_block"}:
        return {**loc, "kind": "section", "value": f"{kind}:{val}" if val else str(kind)}
    if kind == "table_block":
        return {**loc, "kind": "cell", "value": f"{kind}:{val}" if val else str(kind)}
    if kind == "slide":
        return {**loc, "kind": "page", "value": f"{kind}:{val}" if val else str(kind)}
    if kind not in {"timestamp", "section", "page", "region", "cell", "message_offset"}:
        return {**loc, "kind": "section", "value": f"{kind}:{val}" if val else str(kind or "section")}
    return loc


def _normalize_attachment_locators(candidates: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> None:
    for c in candidates:
        if isinstance(c.get("evidence_ids"), list):
            c["evidence_ids"] = list(dict.fromkeys(c["evidence_ids"]))
        if "source_locator" in c:
            c["source_locator"] = _sanitize_locator(c["source_locator"])
    for ev in evidence:
        if "locator" in ev:
            ev["locator"] = _sanitize_locator(ev["locator"])


def _append_declared_entity_mentions(
    candidate_id: str,
    meeting_title: str,
    low_text: str,
    declared_doc_entities: list[tuple[str, str]],
    mentions: list[dict[str, Any]],
) -> None:
    for ename, eid in declared_doc_entities:
        if ename.lower() in low_text and all(m.get("reported_text") != ename for m in mentions):
            mentions.append(
                _mention(candidate_id, len(mentions) + 1, ename, stable_identifier=eid or None, context=f"Entity in {meeting_title}")
            )


def _build_statement_mentions(
    *,
    candidate_id: str,
    meeting_title: str,
    speaker: dict[str, Any],
    workstream: str | None,
    raw_reported_speaker: str,
    is_placeholder_spk: bool,
    is_unattributed_doc_speaker: bool,
    statement_text: str,
    declared_doc_entities: list[tuple[str, str]],
    declared_entity_by_name: dict[str, str],
) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    if workstream:
        ctx = f"{meeting_title} / {speaker.get('role_or_org') or 'speaker context'}"
        mentions.append(_mention(candidate_id, len(mentions) + 1, workstream, stable_identifier=None, context=ctx))
    resolved_spk = clean_non_placeholder_speaker(raw_reported_speaker) or extract_inline_turn_speaker(statement_text)
    if resolved_spk and not is_unattributed_doc_speaker:
        spk_sid = (None if is_placeholder_spk else speaker.get("email")) or declared_entity_by_name.get(resolved_spk.lower())
        mentions.append(_mention(candidate_id, len(mentions) + 1, resolved_spk, stable_identifier=spk_sid, context=f"Speaker in {meeting_title}"))
    if declared_doc_entities:
        _append_declared_entity_mentions(candidate_id, meeting_title, statement_text.lower(), declared_doc_entities, mentions)
    fallback_spk = resolved_spk or raw_reported_speaker
    if not mentions and fallback_spk:
        role_label = "Speaker" if resolved_spk else "Participant"
        mentions.append(_mention(candidate_id, 1, fallback_spk, stable_identifier=speaker.get("email"), context=f"{role_label} in {meeting_title}"))
    return mentions


def _normalize_meeting(  # noqa: C901
    package: dict[str, Any],
    *,
    run_context: str,
    contract_suffix: str,
    source_kind: str,
    source_label: str,
) -> dict[str, Any]:
    """Validate and deterministically decompose a source-bound meeting package."""

    validate_artifact(package)
    if not package["contract_id"].endswith(contract_suffix):
        raise ValueError(f"expected a {source_label} intake contract")
    meeting = package["meeting"]
    package_digest = package["canonical_digest"]["value"]
    intake_id = stable_id("intake", package["artifact_id"], package_digest, length=24)
    run_id = stable_id(
        "run-intake", package["artifact_id"], package_digest, run_context, length=24
    )
    current_trace_id = trace_id(run_id, package_digest)
    source_id = stable_id("source", meeting["meeting_id"], length=24)
    from .meeting_postprocess import resolve_meeting_posture_and_authority, resolve_meeting_source_version
    source_content_digest, source_version_id = resolve_meeting_source_version(
        package, meeting, source_id, package_digest
    )
    inline_promo, posture, authority_rank, default_stmt_conf, doc_routing_ext = (
        resolve_meeting_posture_and_authority(package, AUTHORITY_RANKS)
    )
    attendees = list(dict.fromkeys(meeting.get("attendees", [])))
    default_audiences = meeting["audiences"]
    classification = meeting["classification"]
    evidence: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    evidence_by_fingerprint: dict[str, str] = {}
    evidence_by_identifier: dict[str, str] = {}
    evidence_quotes: dict[str, str] = {}

    def add_evidence(identifier: str, text: str, locator: dict[str, Any]) -> str:
        locator = _sanitize_locator(locator)
        content_digest = digest(text)
        evidence_fingerprint = digest({"source_id": source_id, "locator": locator, "content_digest": content_digest})
        if evidence_fingerprint in evidence_by_fingerprint:
            evidence_id = evidence_by_fingerprint[evidence_fingerprint]
            evidence_by_identifier[identifier] = evidence_id
            evidence_quotes.setdefault(evidence_id, text)
            return evidence_id
        evidence_id = stable_id("evidence", source_id, identifier, locator, content_digest, length=26)
        evidence_by_fingerprint[evidence_fingerprint] = evidence_id
        evidence_by_identifier[identifier] = evidence_id
        evidence_quotes.setdefault(evidence_id, text)
        evidence.append(
            {
                "evidence_id": evidence_id,
                "source_id": source_id,
                "source_version_id": source_version_id,
                "locator": locator,
                "relationship": "supports",
                "directness": "derivative",
                "authority_rank": authority_rank,
                "content_digest": content_digest,
            }
        )
        return evidence_id

    projection_ext = (
        _extension(package, "com.partner_advisory.nee.canonical-signal-projection", {})
        or _extension(package, "com.partner_advisory.nee.projection", {})
        or package.get("graph_mutations")
        or {}
    )
    has_structured_projection = bool(projection_ext.get("relationship_signals"))
    from .meeting_entity_helpers import extract_declared_doc_entities
    declared_doc_entities, declared_entity_by_name = extract_declared_doc_entities(projection_ext)
    seen_statement_candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for speaker in package["speakers"]:
        workstream = _role_workstream(speaker.get("role_or_org"))
        first_evidence_id: str | None = None
        raw_reported_speaker = str(speaker.get("reported_name") or "").strip()
        speaker_name_norm = raw_reported_speaker.lower()
        is_placeholder_spk = is_placeholder_speaker(raw_reported_speaker)
        is_unattributed_doc_speaker = has_structured_projection and (
            is_placeholder_spk
            or speaker_name_norm in {"not specified", "unattributed participant", "system", "metadata"}
        )
        for statement in speaker["statements"]:
            evidence_id = add_evidence(statement["statement_id"], statement["text"], statement["locator"])
            first_evidence_id = first_evidence_id or evidence_id
            if is_unattributed_doc_speaker and statement.get("kind") in {"summary", "other", None}:
                continue
            stmt_cls_map = _extension(package, "com.partner_advisory.nee.statement-classifications", {}) or {}
            eff_kind = stmt_cls_map.get(statement["statement_id"]) or statement["kind"]
            candidate_type, assertion_type, materiality = _statement_semantics(eff_kind)
            candidate_id = stable_id("candidate", run_id, statement["statement_id"], length=26)
            statement_text = normalize_space(statement["text"])
            low_text = statement_text.lower()
            mentions = _build_statement_mentions(
                candidate_id=candidate_id,
                meeting_title=meeting["title"],
                speaker=speaker,
                workstream=workstream,
                raw_reported_speaker=raw_reported_speaker,
                is_placeholder_spk=is_placeholder_spk,
                is_unattributed_doc_speaker=is_unattributed_doc_speaker,
                statement_text=statement_text,
                declared_doc_entities=declared_doc_entities,
                declared_entity_by_name=declared_entity_by_name,
            )
            conversational_markers = (
                "yeah, and then", "yeah and then", "it's like", "and it's like",
                "like, wouldn't", "gotcha", "dropped in", "i'm just saying",
                "so if you guys", "you guys can feed me", "let me just pull up",
                "well, you know"
            )
            is_fragment = (
                len(statement_text) < 15
                or any(low_text.startswith(marker) for marker in conversational_markers)
                or low_text.endswith("and then.")
                or low_text.endswith("and...")
                or low_text.endswith("but...")
            )
            if is_fragment:
                continue

            statement_key = (speaker.get("speaker_ref") or speaker.get("reported_name", ""), statement_text)
            if statement_key in seen_statement_candidates:
                existing_candidate = seen_statement_candidates[statement_key]
                if evidence_id not in existing_candidate["evidence_ids"]:
                    existing_candidate["evidence_ids"].append(evidence_id)
                continue

            cand_entry = {
                "candidate_id": candidate_id,
                "candidate_type": candidate_type,
                "assertion_type": assertion_type,
                "normalized_text": statement_text,
                "materiality": materiality,
                "entity_mentions": mentions,
                "evidence_ids": [evidence_id],
                "source_locator": statement["locator"],
                "classification": classification,
                "audiences": default_audiences,
                "extraction_confidence": (
                    statement.get("copilot_confidence")
                    or statement.get("gemini_confidence")
                    or default_stmt_conf
                ),
            }
            seen_statement_candidates[statement_key] = cand_entry
            candidates.append(cand_entry)

        # Omit low-materiality speaker candidate boilerplate if substantive candidates exist
        # to prevent bloating the assertion table with redundant speaker statements.
        if first_evidence_id and not candidates and not is_unattributed_doc_speaker:
            speaker_candidate_id = stable_id("candidate", run_id, speaker["speaker_ref"], length=26)
            first_statement = speaker["statements"][0]
            candidates.append(
                {
                    "candidate_id": speaker_candidate_id,
                    "candidate_type": "entity_mention",
                    "assertion_type": "candidate_assertion",
                    "normalized_text": f"{speaker['reported_name']} is a reported speaker in {meeting['title']}.",
                    "materiality": "low",
                    "entity_mentions": [
                        _mention(
                            speaker_candidate_id,
                            1,
                            speaker["reported_name"],
                            stable_identifier=speaker.get("email"),
                            context=f"Speaker / {speaker.get('role_or_org') or 'role not provided'}",
                        )
                    ],
                    "evidence_ids": [first_evidence_id],
                    "source_locator": first_statement["locator"],
                    "classification": classification,
                    "audiences": default_audiences,
                    "extraction_confidence": (
                        first_statement.get("copilot_confidence")
                        or first_statement.get("gemini_confidence")
                        or 0.75
                    ),
                }
            )

    from .meeting_postprocess import (
        apply_doc_routing_budget,
        build_extra_bundle_extensions,
        deduplicate_candidates,
        extract_action_candidates,
    )
    extract_action_candidates(
        package,
        run_id=run_id,
        meeting_title=meeting["title"],
        classification=classification,
        default_audiences=default_audiences,
        add_evidence=add_evidence,
        evidence_by_identifier=evidence_by_identifier,
        candidates=candidates,
    )
    entity_intent_profiles, edge_traits = process_projection_signals(
        projection=_extension(package, "com.partner_advisory.nee.canonical-signal-projection", {})
        or _extension(package, "com.partner_advisory.nee.projection", {})
        or package.get("graph_mutations"),
        candidates=candidates,
        evidence=evidence,
        evidence_by_identifier=evidence_by_identifier,
        run_id=run_id,
        meeting_title=meeting["title"],
        classification=classification,
        default_audiences=default_audiences,
    )
    candidates = apply_doc_routing_budget(deduplicate_candidates(candidates), doc_routing_ext)
    _normalize_attachment_locators(candidates, evidence)
    if not candidates or not evidence:
        raise ValueError(f"{source_label} package produced no governed candidates or evidence")
    extra_bundle_ext = build_extra_bundle_extensions(package, candidates, doc_routing_ext)
    workstreams = sorted(
        {
            value
            for speaker in package["speakers"]
            if (value := _role_workstream(speaker.get("role_or_org")))
        }
    )
    return create_artifact(
        "normalized-candidate-bundle",
        stable_id("candidate-bundle", run_id, length=26),
        intake_id=intake_id,
        run_id=run_id,
        trace_id=current_trace_id,
        source_id=source_id,
        source_version_id=source_version_id,
        evidence=evidence,
        candidates=candidates,
        extensions=_context_extension(
            source_kind=source_kind,
            attendees=attendees,
            workstream=workstreams[0] if len(workstreams) == 1 else None,
            source_posture=posture,
            authority_rank=authority_rank,
            inline_canonical_promotion=inline_promo,
        )
        | {
            "com.partner_advisory.nee.source": {
                "name": meeting.get("title", ""), "package_digest": package_digest,
                "date": normalize_iso_date(meeting.get("started_at") or meeting.get("date")) or "0000-00-00",
                "date_status": _extension(package, "com.partner_advisory.nee.date-status", None),
                "upload_date": normalize_iso_date(_extension(package, "com.partner_advisory.nee.upload-date", None)),
                "actual_date": normalize_iso_date(_extension(package, "com.partner_advisory.nee.actual-date", None)),
                "source_content_digest": source_content_digest, "source_location": meeting.get("source_location", ""),
                "attendees": attendees, "organizer": meeting.get("organizer"),
                "evidence_quotes": evidence_quotes, "inline_canonical_promotion": inline_promo,
            },
            "com.partner_advisory.nee.entity-intent-profiles": entity_intent_profiles,
            "com.partner_advisory.nee.edge-traits": edge_traits,
            **extra_bundle_ext,
        },
    )


def normalize_copilot(
    package: dict[str, Any], *, run_context: str = "unbound"
) -> dict[str, Any]:
    """Validate and deterministically decompose a Copilot meeting package."""

    return _normalize_meeting(
        package,
        run_context=run_context,
        contract_suffix=":copilot-intake:1.0.0",
        source_kind="copilot_meeting",
        source_label="Copilot",
    )


def normalize_google_meet_gemini(
    package: dict[str, Any], *, run_context: str = "unbound"
) -> dict[str, Any]:
    """Validate and deterministically decompose Google Meet/Gemini notes."""

    return _normalize_meeting(
        package,
        run_context=run_context,
        contract_suffix=":google-meet-gemini-intake:1.0.0",
        source_kind="google_meet_gemini",
        source_label="Google Meet/Gemini",
    )


def normalize_evidence_envelope(
    package: dict[str, Any], *, run_context: str = "unbound"
) -> dict[str, Any]:
    """Validate and deterministically decompose a unified Evidence Envelope intake package."""

    contract_id = package.get("contract_id", "")
    if not contract_id.endswith(":evidence-envelope-intake:1.0.0"):
        raise ValueError(
            f"unsupported intake contract: {contract_id!r}. All intake must use "
            "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0."
        )
    return _normalize_meeting(
        package,
        run_context=run_context,
        contract_suffix=":evidence-envelope-intake:1.0.0",
        source_kind="evidence_envelope",
        source_label="Evidence Envelope",
    )
