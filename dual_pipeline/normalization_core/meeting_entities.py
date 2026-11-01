"""Entity upsert candidate generation and intent profile helpers for meeting intake."""
from __future__ import annotations

from typing import Any

from ..core import normalize_space, stable_id
from ..l0.speaker_and_qualifier_policy import clean_non_placeholder_speaker, extract_inline_turn_speaker
from .meeting_entity_helpers import (
    ALLOWED_RELATIONSHIP_PREDICATES, _assign_mention_profile, _backfill_candidate_profiles,
    _resolve_entity_evidence_ids, _resolve_rel_evidence_ids, create_entity_intent_profile,
    extract_edge_assertion_type, extract_edge_confidence, extract_edge_sticky_locator, map_entity_type,
    resolve_profile_index_entry,
)


def _resolve_entity_id_and_type(entity: dict[str, Any], idx: int) -> tuple[str, str]:
    raw_id = str(entity.get("id") or entity.get("entity_id") or f"entity-{idx+1:02d}").strip()
    raw_type = entity.get("type") or entity.get("entity_type") or entity.get("domain_type")
    if not raw_type and ":" in raw_id:
        raw_type = raw_id.split(":", 1)[0]
    elif raw_type and ":" in raw_id:
        pfx, slug = raw_id.split(":", 1)
        rtype_clean = str(raw_type).strip().lower()
        if pfx.strip().lower() != rtype_clean and rtype_clean in (
            "org", "system", "person", "role", "project", "workstream", "policy", "capability", "strategy", "risk", "deliverable"
        ):
            raw_id = f"{rtype_clean}:{slug}"
    return raw_id, map_entity_type(raw_type)


def _normalize_entity_locator(raw_locator: Any, default_locator: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw_locator, dict):
        return default_locator
    if raw_locator.get("kind") == "attachment":
        loc = dict(raw_locator)
        loc["kind"] = "region"
        val = str(loc.get("value") or "")
        if not val.startswith("attachment:"):
            loc["value"] = f"attachment:{val}"
        return loc
    if raw_locator.get("kind") == "transcript_timestamp":
        return {**raw_locator, "kind": "timestamp"}
    return raw_locator


def _build_single_entity_candidate(
    *,
    entity: Any,
    idx: int,
    run_id: str,
    meeting_title: str,
    classification: str,
    audiences: list[str],
    default_evidence_id: str,
    default_locator: dict[str, Any],
    evidence_by_id: dict[str, str],
    evidence_by_identifier: dict[str, str],
) -> tuple[dict[str, Any], str, dict[str, Any], str, str] | None:
    """Build candidate, mention profile, and index tuple for a single entity upsert."""
    if not isinstance(entity, dict):
        return None
    raw_id, node_type = _resolve_entity_id_and_type(entity, idx)
    raw_name = entity.get("name") or entity.get("canonical_name") or raw_id.split(":", 1)[-1].replace("_", " ").title()
    canonical_name = normalize_space(str(raw_name))
    cand_id = stable_id("candidate", run_id, "entity", raw_id, length=26)
    mention_id = stable_id("mention", cand_id, 1, canonical_name, length=24)
    mention = {
        "mention_id": mention_id,
        "reported_text": canonical_name,
        "stable_identifier": raw_id,
        "context": normalize_space(entity.get("description") or f"{canonical_name} ({node_type}) in {meeting_title}"),
    }
    profile = create_entity_intent_profile(
        stable_identifier=raw_id, node_type=node_type, entity_intent="resolve_or_create", declaration_status="DECLARED"
    )
    mutation_op = str(entity.get("mutation") or "CREATE").upper()
    profile["mutation_intent"] = mutation_op
    rel_ev_ids = _resolve_entity_evidence_ids(entity, default_evidence_id, evidence_by_id, evidence_by_identifier)
    raw_locator = _normalize_entity_locator(entity.get("locator"), default_locator)
    raw_desc = normalize_space(str(entity.get("description") or ""))
    has_sub_desc = bool(raw_desc and len(raw_desc) >= 10 and "is an identified" not in raw_desc.lower() and raw_desc.lower() != canonical_name.lower())
    norm_text = f"{canonical_name}: {raw_desc}" if has_sub_desc else f"{canonical_name} is an identified {node_type} in {meeting_title}."
    cand = {
        "candidate_id": cand_id,
        "candidate_type": "entity_mention",
        "assertion_type": "candidate_assertion",
        "normalized_text": norm_text,
        "materiality": "medium",
        "entity_mentions": [mention],
        "evidence_ids": rel_ev_ids,
        "source_locator": raw_locator,
        "classification": classification,
        "audiences": audiences,
        "extraction_confidence": float(entity.get("extraction_confidence") or 0.85),
    }
    _hydrate_entity_role_metadata(entity, mention, profile, cand)
    return cand, mention_id, profile, raw_id, node_type


def _hydrate_entity_role_metadata(
    entity: dict[str, Any],
    mention: dict[str, Any],
    profile: dict[str, Any],
    cand: dict[str, Any],
) -> None:
    if entity.get("description"):
        profile["description"] = entity["description"]
        cand["description"] = entity["description"]
    if isinstance(entity.get("attributes"), dict) and entity["attributes"]:
        profile["attributes"] = dict(entity["attributes"])
        cand["attributes"] = dict(entity["attributes"])
    if entity.get("role"):
        profile["role"] = entity["role"]
        cand["role"] = entity["role"]
    if entity.get("is_human_confirmed") is not None:
        is_hc = bool(entity["is_human_confirmed"])
        profile["is_human_confirmed"] = is_hc
        cand["is_human_confirmed"] = is_hc
        if is_hc:
            cand["has_role_resolution"] = True
            cand["has_role_change"] = True
    if entity.get("provenance"):
        profile["provenance"] = entity["provenance"]
        cand["provenance"] = entity["provenance"]
    if entity.get("resolution_id"):
        profile["resolution_id"] = entity["resolution_id"]
        cand["resolution_id"] = entity["resolution_id"]


def build_entity_upsert_candidates(
    *,
    entity_upserts: list[dict[str, Any]],
    run_id: str,
    meeting_title: str,
    classification: str,
    audiences: list[str],
    default_evidence_id: str,
    default_locator: dict[str, Any],
    evidence_by_id: dict[str, str],
    evidence_by_identifier: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, tuple[str, dict[str, Any]]]]:
    """Transform Output 03 entity_upserts into candidates and intent profiles."""
    candidates: list[dict[str, Any]] = []
    mention_profiles: dict[str, dict[str, Any]] = {}
    profile_index: dict[str, tuple[str, dict[str, Any]]] = {}

    for idx, entity in enumerate(entity_upserts):
        built = _build_single_entity_candidate(
            entity=entity,
            idx=idx,
            run_id=run_id,
            meeting_title=meeting_title,
            classification=classification,
            audiences=audiences,
            default_evidence_id=default_evidence_id,
            default_locator=default_locator,
            evidence_by_id=evidence_by_id,
            evidence_by_identifier=evidence_by_identifier,
        )
        if built is None:
            continue
        cand, mention_id, profile, raw_id, node_type = built
        candidates.append(cand)
        mention_profiles[mention_id] = profile
        profile_index[raw_id] = (node_type, cand["entity_mentions"][0]["reported_text"], profile)
        orig_id = str(entity.get("id") or entity.get("entity_id") or "").strip()
        if orig_id and orig_id != raw_id:
            profile_index[orig_id] = (node_type, cand["entity_mentions"][0]["reported_text"], profile)

    return candidates, mention_profiles, profile_index


def _endpoint_label(profile_index: dict[str, Any], entity_id: str) -> str:
    matched = resolve_profile_index_entry(profile_index, entity_id)
    return matched[1] if matched is not None else entity_id.split(":", 1)[-1].replace("_", " ").title()


def _clean_relationship_lineage(raw_lineage: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(raw_lineage, dict):
        return None, None
    speaker_or_author = clean_non_placeholder_speaker(raw_lineage.get("speaker"))
    if raw_lineage.get("speaker") and speaker_or_author is None:
        return {k: v for k, v in raw_lineage.items() if k != "speaker"}, None
    return raw_lineage, speaker_or_author


def _build_single_relationship_candidate(
    *,
    rel: dict[str, Any],
    idx: int,
    run_id: str,
    meeting_title: str,
    classification: str,
    audiences: list[str],
    first_ev_id: str | None,
    first_loc: dict[str, Any],
    evidence_by_id: dict[str, str],
    evidence_by_identifier: dict[str, str],
    profile_index: dict[str, tuple[str, dict[str, Any]]],
    entity_intent_profiles: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Build candidate with 4 Edge Traits (Assertion Type, Confidence, Evidence, Lineage)."""
    from_id = rel.get("from_id", "")
    to_id = rel.get("to_id", "")
    if not from_id or not to_id:
        return None
    pred_raw = (rel.get("predicate") or rel.get("relationship") or "RELATED_TO").upper()
    pred = pred_raw if pred_raw in ALLOWED_RELATIONSHIP_PREDICATES else "RELATED_TO"
    sig_id = rel.get("signal_id") or f"sig-{idx+1:02d}"
    cand_id = stable_id("candidate", run_id, "rel", sig_id, length=26)
    from_name = _endpoint_label(profile_index, from_id)
    to_name = _endpoint_label(profile_index, to_id)

    rel_ev_ids = _resolve_rel_evidence_ids(
        rel, evidence_by_id, evidence_by_identifier, first_ev_id
    )
    rel_loc = (
        evidence_by_id[rel_ev_ids[0]]["locator"]
        if rel_ev_ids and rel_ev_ids[0] in evidence_by_id
        else first_loc
    )

    from_entry = resolve_profile_index_entry(profile_index, from_id)
    to_entry = resolve_profile_index_entry(profile_index, to_id)
    canon_from_id = from_entry[2]["stable_identifier"] if from_entry is not None else from_id
    canon_to_id = to_entry[2]["stable_identifier"] if to_entry is not None else to_id
    m1 = {"mention_id": stable_id("mention", cand_id, "1", length=26), "reported_text": from_name, "stable_identifier": canon_from_id, "context": f"Source entity in {meeting_title}"}
    m2 = {"mention_id": stable_id("mention", cand_id, "2", length=26), "reported_text": to_name, "stable_identifier": canon_to_id, "context": f"Target entity in {meeting_title}"}

    _assign_mention_profile(m1, canon_from_id, profile_index, entity_intent_profiles)
    _assign_mention_profile(m2, canon_to_id, profile_index, entity_intent_profiles)

    # 1. ASSERTION TYPE
    raw_assertion_type = str(rel.get("assertion_type") or "architecture_relationship")
    cand_assertion_type = extract_edge_assertion_type(rel)

    # 2. CONFIDENCE (score, tier, conditionality discount rationale)
    extraction_confidence, confidence_tier, confidence_rationale = extract_edge_confidence(rel)

    # 3. EVIDENCE Sticky Note
    sticky_note = rel.get("evidence")
    rel_loc = extract_edge_sticky_locator(rel, rel_loc)

    # 4. LINEAGE
    lineage, speaker_or_author = _clean_relationship_lineage(rel.get("lineage"))


    cand: dict[str, Any] = {
        "candidate_id": cand_id,
        "candidate_type": "architecture_relationship",
        "assertion_type": cand_assertion_type,
        "normalized_text": f"{from_name} {pred} {to_name}.",
        "materiality": "high",
        "entity_mentions": [m1, m2],
        "evidence_ids": rel_ev_ids,
        "source_locator": rel_loc,
        "classification": classification,
        "audiences": audiences,
        "extraction_confidence": extraction_confidence,
    }
    trait: dict[str, Any] = {
        "candidate_id": cand_id,
        "assertion_type": raw_assertion_type,
        "relationship_predicate": pred,
        "confidence": {
            "score": extraction_confidence,
            "tier": confidence_tier,
            "rationale": confidence_rationale,
        },
        "evidence": sticky_note if isinstance(sticky_note, dict) else None,
        "lineage": lineage if isinstance(lineage, dict) else None,
        "speaker_or_author": speaker_or_author,
    }
    if "candidate_predicates" in rel:
        trait["candidate_predicates"] = rel["candidate_predicates"]

    _hydrate_rel_role_metadata(rel, pred, cand, trait)
    return cand, trait


def _hydrate_rel_role_metadata(
    rel: dict[str, Any],
    pred: str,
    cand: dict[str, Any],
    trait: dict[str, Any],
) -> None:
    raw_atype = str(rel.get("assertion_type") or "")
    if pred in ("HAS_ROLE", "HAS_ROLE_IN") or raw_atype == "speaker_role" or rel.get("is_human_confirmed"):
        cand["assertion_type"] = "speaker_role"
        trait["assertion_type"] = "speaker_role"
        cand["has_role_change"] = True
        cand["has_role_resolution"] = True
        trait["has_role_change"] = True
        trait["has_role_resolution"] = True
        if rel.get("is_human_confirmed") is not None:
            cand["is_human_confirmed"] = bool(rel["is_human_confirmed"])
            trait["is_human_confirmed"] = bool(rel["is_human_confirmed"])
        if rel.get("resolution_id"):
            cand["resolution_id"] = rel["resolution_id"]
            trait["resolution_id"] = rel["resolution_id"]
        if rel.get("provenance"):
            cand["provenance"] = rel["provenance"]
            trait["provenance"] = rel["provenance"]


def _resolve_auxiliary_target_entity_id(
    item: dict[str, Any],
    raw_text: str,
    profile_index: dict[str, Any],
    default_entity_id: str | None,
) -> str:
    target_eid = str(item.get("entity_id") or item.get("subject_id") or item.get("owner_id") or "")
    if not target_eid and profile_index:
        raw_low = raw_text.lower()
        for pid, (_ntype, plabel, _prof) in profile_index.items():
            if plabel and len(plabel) >= 3 and plabel.lower() in raw_low:
                return pid
    return target_eid or (default_entity_id or "")


def _append_auxiliary_speaker_mention(
    item: dict[str, Any], raw_text: str, cand_id: str, meeting_title: str, mentions: list[dict[str, Any]]
) -> None:
    lin = item.get("lineage")
    lin_spk = lin.get("speaker") if isinstance(lin, dict) else None
    aux_speaker = clean_non_placeholder_speaker(
        item.get("speaker") or item.get("speaker_or_author") or lin_spk
    ) or extract_inline_turn_speaker(raw_text)
    if aux_speaker and all(m.get("reported_text") != aux_speaker for m in mentions):
        spk_mid = stable_id("mention", cand_id, len(mentions) + 1, aux_speaker, length=24)
        mentions.append({"mention_id": spk_mid, "reported_text": aux_speaker, "stable_identifier": None, "context": f"Speaker in {meeting_title}"})


def _build_one_auxiliary_candidate(
    item: dict[str, Any],
    idx: int,
    pfx: str,
    cand_type: str,
    assert_type: str,
    materiality: str,
    text_fields: tuple[str, ...],
    run_id: str,
    meeting_title: str,
    classification: str,
    audiences: list[str],
    first_ev_id: str | None,
    first_loc: dict[str, Any],
    evidence_by_id: dict[str, Any],
    evidence_by_identifier: dict[str, str],
    profile_index: dict[str, Any],
    entity_intent_profiles: dict[str, dict[str, Any]],
    default_entity_id: str | None,
) -> dict[str, Any] | None:
    raw_text = next((str(item.get(f)).strip() for f in text_fields if item.get(f)), "")
    if not raw_text:
        return None
    item_id = str(item.get("id") or item.get(f"{pfx}_id") or item.get("decision_id") or item.get("risk_id") or item.get("signal_id") or f"{pfx}-{idx+1:02d}")
    cand_id = stable_id("candidate", run_id, pfx, item_id, length=26)
    ev_ids = _resolve_rel_evidence_ids(item, evidence_by_id, evidence_by_identifier, first_ev_id)
    if not ev_ids:
        return None
    loc = _normalize_entity_locator(item.get("locator"), evidence_by_id[ev_ids[0]]["locator"] if ev_ids[0] in evidence_by_id else first_loc)
    target_eid = _resolve_auxiliary_target_entity_id(item, raw_text, profile_index, default_entity_id)
    mentions: list[dict[str, Any]] = []
    if target_eid:
        label = _endpoint_label(profile_index, target_eid)
        canon_id = profile_index[target_eid][2]["stable_identifier"] if target_eid in profile_index else target_eid
        m_id = stable_id("mention", cand_id, 1, label, length=24)
        m = {"mention_id": m_id, "reported_text": label, "stable_identifier": canon_id, "context": f"{cand_type} signal in {meeting_title}"}
        _assign_mention_profile(m, canon_id, profile_index, entity_intent_profiles)
        mentions.append(m)
    _append_auxiliary_speaker_mention(item, raw_text, cand_id, meeting_title, mentions)
    return {
        "candidate_id": cand_id,
        "candidate_type": cand_type,
        "assertion_type": assert_type,
        "normalized_text": normalize_space(raw_text),
        "materiality": materiality,
        "entity_mentions": mentions,
        "evidence_ids": ev_ids,
        "source_locator": loc,
        "classification": classification,
        "audiences": audiences,
        "extraction_confidence": float(item.get("extraction_confidence") or 0.85),
    }


def _build_auxiliary_signal_candidates(
    *, mutations: dict[str, Any], run_id: str, meeting_title: str, classification: str, audiences: list[str],
    first_ev_id: str | None, first_loc: dict[str, Any], evidence_by_id: dict[str, Any],
    evidence_by_identifier: dict[str, str], profile_index: dict[str, Any], entity_intent_profiles: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Emit discrete candidates for decision_updates, proposal_updates, risk_updates, action_updates, and strategic_intent_signals."""
    out: list[dict[str, Any]] = []
    specs = (
        ("decision_updates", "dec", "decision", "decision", "high", ("decision_question", "decision_text", "decision", "description", "statement", "title", "rationale", "summary", "text")),
        ("assertion_updates", "asrt", "assertion", "candidate_assertion", "high", ("assertion_text", "assertion", "claim", "statement", "description", "title", "summary", "text")),
        ("proposal_updates", "prop", "assertion", "proposal", "medium", ("proposal_text", "proposal", "description", "statement", "title", "rationale", "value_case", "summary", "text")),
        ("risk_updates", "risk", "risk", "risk", "high", ("risk_text", "risk", "description", "statement", "title", "rationale", "summary", "text")),
        ("action_updates", "act", "action", "action_item", "medium", ("action_text", "action", "description", "statement", "title", "summary", "text")),
        ("strategic_intent_signals", "strat", "assertion", "candidate_assertion", "high", ("signal_text", "intent", "description", "statement", "title", "rationale", "value_case", "claim", "summary", "text")),
        ("metrics", "metric", "assertion", "candidate_assertion", "high", ("description", "value", "statement", "title", "summary", "text")),
    )
    default_entity_id = next(iter(profile_index), None)
    for key, pfx, cand_type, assert_type, materiality, text_fields in specs:
        for idx, item in enumerate(mutations.get(key) or []):
            if not isinstance(item, dict):
                continue
            cand = _build_one_auxiliary_candidate(
                item, idx, pfx, cand_type, assert_type, materiality, text_fields,
                run_id, meeting_title, classification, audiences, first_ev_id, first_loc,
                evidence_by_id, evidence_by_identifier, profile_index, entity_intent_profiles, default_entity_id,
            )
            if cand is not None:
                out.append(cand)
    return out


def _append_unique_aux_candidates(
    candidates: list[dict[str, Any]], aux_candidates: list[dict[str, Any]]
) -> None:
    existing_texts = {c.get("normalized_text") for c in candidates}
    for ac in aux_candidates:
        if ac["normalized_text"] not in existing_texts:
            candidates.append(ac)
            existing_texts.add(ac["normalized_text"])


def _extract_projection_mutations(
    projection: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    gm = projection.get("graph_mutations")
    mutations = gm if isinstance(gm, dict) else projection
    ent_upserts = mutations.get("entity_upserts") or []
    rel_signals = mutations.get("relationship_signals") or []
    return mutations, ent_upserts, rel_signals


def process_projection_signals(
    *,
    projection: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    evidence_by_identifier: dict[str, str],
    run_id: str,
    meeting_title: str,
    classification: str,
    default_audiences: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Process projection entity_upserts and relationship_signals, returning (intent_profiles, edge_traits)."""
    entity_intent_profiles: dict[str, dict[str, Any]] = {}
    edge_traits: dict[str, dict[str, Any]] = {}
    if not isinstance(projection, dict):
        return entity_intent_profiles, edge_traits

    first_ev_id = evidence[0]["evidence_id"] if evidence else None
    first_loc = evidence[0]["locator"] if evidence else {"kind": "section", "value": "## Transcript"}
    evidence_by_id = {e["evidence_id"]: e for e in evidence}
    mutations, ent_upserts, rel_signals = _extract_projection_mutations(projection)

    profile_index: dict[str, tuple[str, dict[str, Any]]] = {}
    if ent_upserts:
        ent_candidates, ent_profiles, profile_index = build_entity_upsert_candidates(
            entity_upserts=ent_upserts,
            run_id=run_id,
            meeting_title=meeting_title,
            classification=classification,
            audiences=default_audiences,
            default_evidence_id=first_ev_id or "",
            default_locator=first_loc,
            evidence_by_id=evidence_by_id,
            evidence_by_identifier=evidence_by_identifier,
        )
        candidates.extend(ent_candidates)
        entity_intent_profiles.update(ent_profiles)

    for idx, rel in enumerate(rel_signals):
        res = _build_single_relationship_candidate(
            rel=rel, idx=idx, run_id=run_id, meeting_title=meeting_title,
            classification=classification, audiences=default_audiences, first_ev_id=first_ev_id,
            first_loc=first_loc, evidence_by_id=evidence_by_id, evidence_by_identifier=evidence_by_identifier,
            profile_index=profile_index, entity_intent_profiles=entity_intent_profiles,
        )
        if res is not None:
            candidates.append(res[0])
            edge_traits[res[0]["candidate_id"]] = res[1]

    aux_candidates = _build_auxiliary_signal_candidates(
        mutations=mutations, run_id=run_id, meeting_title=meeting_title,
        classification=classification, audiences=default_audiences, first_ev_id=first_ev_id,
        first_loc=first_loc, evidence_by_id=evidence_by_id, evidence_by_identifier=evidence_by_identifier,
        profile_index=profile_index, entity_intent_profiles=entity_intent_profiles,
    )
    _append_unique_aux_candidates(candidates, aux_candidates)
    _backfill_candidate_profiles(candidates, profile_index, entity_intent_profiles)
    return entity_intent_profiles, edge_traits
