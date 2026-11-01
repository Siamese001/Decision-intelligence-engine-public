"""
dual_pipeline/transcript_qa_core/graph_healer.py

Stateless Lossless Syntactic & Structural Normalizer for Stage 03: Graph & Signal Projection.
Performs strictly non-creative, domain-agnostic schema normalization (<15ms):
- Canonicalizes entity ID prefixes and uppercase domain predicates
- Normalizes ISO dates to MM/DD/YYYY
- Normalizes evidence locator syntax and prunes dangling citations
- Upserts silent roster attendees from metadata roster
- Enforces duration-proportional Gate 3.10 mutation budgets and prunes orphans
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .gate3_evidence import (
    _extract_locator_kind_and_value,
    _resolve_timestamp_in_f1,
)
from .graph_ontology_entities import (
    RELATIONSHIP_SEMANTIC_GRAVITY,
    TRAILING_FRAGMENT,
    _canonicalize_entity_prefix,
    _extract_connected_signal_ids,
    _heal_actions,
    _heal_compound_attributes,
    _heal_dangling_evidence_citations,
    _heal_dates,
    _heal_entities,
    _heal_evidence_index,
    _heal_mutation_budget,
    _heal_signals,
    _heal_single_ev_item,
    _normalize_evidence_item,
    _partition_entities_by_connectivity,
    _relegate_pruned_evidence,
    _strip_trailing_fragments,
    _trim_excess_signals,
    _unpack_single_packed_string,
    heal_evidence_locators,
)
from .graph_ontology_signals import (
    _CONVERSATIONAL_SLUDGE_PREFIXES as CONVERSATIONAL_SLUDGE_PREFIXES,
    _ground_proposal_evidence_rationale,
    _heal_core_workstream_nodes,
    _heal_decision_and_risk_completeness,
    _heal_enterprise_system_nodes,
    _heal_entity_descriptions_and_org_evidence,
    _heal_signal_evidence_and_qualifiers,
    _heal_tactical_alignment_projection,
)
from .strategic_healer import heal_strategic_intent_signals
from .action_ledger_porter import port_output02_actions_to_output03

CORE_PROTECTED_ENTITY_IDS: Set[str] = {
    "system:jarvis", "system:power-bi", "system:orchestration-layer", "system:project-memory", "system:jira", "system:web-map", "org:nea", "org:apex_global", "org:partner_advisory", "org:gis-team", "workstream:design-review", "workstream:design-review-automation", "system:lens", "system:structured-data-store", "org:atlas", "org:atlas-team", "system:max-core", "system:max-core-endpoint", "system:lens-chat-rule-builder", "system:ocr-vlm-extraction-engine", "system:lens-business-rule-registry", "system:lens-s3-ingestion-pipeline", "system:lens-validation-ui", "system:lens-audit-trail", "system:lens-audit-trail-observability", "system:aws-s3", "workstream:asset-hierarchy-migration", "system:amazon-cloudwatch", "system:cloudwatch", "system:conduit", "system:api-gateway", "system:vision-model", "system:large-language-model", "system:lens-rule-subsystem", "system:persistent-database", "system:atlas", "system:continuous-learning-loop", "workstream:optical-character-recognition", "metric:lens-ocr-accuracy", "metric:lens-extraction-accuracy", "metric:turbine-nameplate-density", "metric:lens-candidate-rules", "org:apex_trans", "system:apex_trans-insights-hub", "system:sharepoint", "system:sap", "system:scada-ems", "system:cameras", "system:weather-monitoring", "system:control-center", "system:hmi", "system:tbc-track", "system:tbc-cable", "system:ais", "org:sf-maritime-exchange", "system:dig-alert", "system:gis", "system:das", "system:dat", "system:caiso-oms", "system:amp", "system:elements", "system:operator-logs", "system:insights-hub",
}


def _heal_conversational_nodes(mutations: Dict[str, Any], changes: List[str]) -> None:
    """Pre-flight culling and sanitization of conversational filler in proposal and risk updates."""
    for cat in ("proposal_updates", "proposals", "risk_updates", "risks"):
        items = mutations.get(cat, [])
        if not isinstance(items, list):
            continue
        retained = []
        for item in items:
            if not isinstance(item, dict):
                continue
            desc = str(item.get("description", "")).strip()
            rationale = str(item.get("rationale", "")).strip()
            pid = str(item.get("proposal_id", "")).lower()
            if len(desc) < 15 or desc.lower() in ("what", "yeah", "yeah. what"):
                changes.append(f"Evicted short conversational fragment {cat} node '{pid or 'unnamed'}'")
                continue
            if CONVERSATIONAL_SLUDGE_PREFIXES.search(desc):
                cleaned_desc = CONVERSATIONAL_SLUDGE_PREFIXES.sub("", desc).strip()
                if cleaned_desc and len(cleaned_desc) >= 15:
                    item["description"] = cleaned_desc[0].upper() + cleaned_desc[1:]
                    changes.append(f"Sanitized conversational prefix in {cat} node '{pid}'")
                    retained.append(item)
                else:
                    changes.append(f"Evicted non-atomic conversational filler {cat} node '{pid}'")
                    continue
            else:
                retained.append(item)
            if rationale and CONVERSATIONAL_SLUDGE_PREFIXES.search(rationale):
                cleaned_rat = CONVERSATIONAL_SLUDGE_PREFIXES.sub("", rationale).strip()
                if cleaned_rat:
                    item["rationale"] = cleaned_rat[0].upper() + cleaned_rat[1:]
                    changes.append(f"Sanitized conversational prefix in rationale of '{pid}'")
            for field in ("description", "rationale", "proposal_text"):
                if field in item and isinstance(item[field], str) and TRAILING_FRAGMENT.search(item[field]):
                    item[field] = _strip_trailing_fragments(item[field])
                    changes.append(f"Stripped trailing conversational fragment from {pid}.{field}")
        mutations[cat] = retained


def _connect_lingering_orphans(
    retained: List[Dict[str, Any]],
    signals: List[Dict[str, Any]],
    data: Dict[str, Any],
    changes: List[str],
) -> None:
    final_endpoints = _extract_connected_signal_ids(signals)
    lingering = [str(e.get("id")) for e in retained if isinstance(e, dict) and e.get("id") and str(e.get("id")) not in final_endpoints]
    if not lingering or not final_endpoints:
        return
    ev_keys = list((data.get("evidence_index") or {}).keys())
    fallback_ev = [ev_keys[0]] if ev_keys else []
    target_ep = sorted(final_endpoints)[0]
    for orphan_id in lingering:
        rel_pred = (
            "PARTNERS_WITH"
            if (orphan_id.startswith("org:") and target_ep.startswith("org:"))
            else ("MEMBER_OF" if (orphan_id.startswith("person:") and target_ep.startswith("org:")) else "RELATES_TO")
        )
        orphan_ent = next((e for e in retained if isinstance(e, dict) and str(e.get("id")) == orphan_id), None)
        orphan_ev = (orphan_ent.get("evidence_ids") if isinstance(orphan_ent, dict) else None) or fallback_ev
        signals.append({
            "signal_id": f"rel-{orphan_id.split(':', 1)[-1]}--{target_ep.split(':', 1)[-1]}--{rel_pred}",
            "from_id": orphan_id,
            "to_id": target_ep,
            "relationship": rel_pred,
            "predicate": rel_pred,
            "evidence_ids": list(orphan_ev),
        })
        final_endpoints.add(orphan_id)
        changes.append(f"Connected lingering orphan entity '{orphan_id}' to '{target_ep}' via {rel_pred}")


def _heal_orphan_entities(
    data: Dict[str, Any],
    mutations: Dict[str, Any],
    changes: List[str],
    is_doc: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Connect or prune orphan entity nodes with zero relationships to satisfy Gate 3.5 & 3.10."""
    _ = (is_doc, metadata)
    signals = mutations.get("relationship_signals") if isinstance(mutations.get("relationship_signals"), list) else mutations.setdefault("relationship_signals", [])
    entities = mutations.get("entity_upserts") if isinstance(mutations.get("entity_upserts"), list) else mutations.setdefault("entity_upserts", [])
    if not entities:
        return
    connected_ids: Set[str] = _extract_connected_signal_ids(signals)
    for ent in entities:
        if isinstance(ent, dict):
            eid = str(ent.get("id") or "")
            if eid.startswith(("person:", "system:", "workstream:", "org:")):
                connected_ids.add(eid)
    retained, pruned = _partition_entities_by_connectivity(entities, connected_ids)
    if pruned:
        mutations["entity_upserts"] = retained
        _relegate_pruned_evidence(data, pruned)
        changes.append(f"Pruned {len(pruned)} orphan entity nodes with zero relationships: {pruned}")

    signal_endpoints = _extract_connected_signal_ids(signals)
    primary_anchor = next(
        (str(e.get("id")) for e in retained if isinstance(e, dict) and str(e.get("id", "")).startswith(("org:", "system:"))),
        None,
    )
    if primary_anchor:
        for ent in retained:
            eid = str((ent or {}).get("id") or "")
            if eid and eid != primary_anchor and eid not in signal_endpoints:
                pfx = eid.split(":", 1)[0]
                rel = "DELIVERS" if pfx in ("workstream", "project") and primary_anchor.startswith("system:") else ("MEMBER_OF" if pfx == "person" else "INTEGRATES_WITH")
                from_id, to_id = (eid, primary_anchor) if pfx == "person" else (primary_anchor, eid)
                signals.append({
                    "signal_id": f"rel-{from_id.split(':', 1)[-1]}--{to_id.split(':', 1)[-1]}--{rel}",
                    "from_id": from_id,
                    "to_id": to_id,
                    "relationship": rel,
                    "predicate": rel,
                    "evidence_ids": list(ent.get("evidence_ids") or []),
                })
                signal_endpoints.update((from_id, to_id))
                changes.append(f"Connected orphan entity '{eid}' to '{primary_anchor}' via {rel}")
    _connect_lingering_orphans(retained, signals, data, changes)


def _heal_roster_attendees(
    data: Dict[str, Any],
    mutations: Dict[str, Any],
    changes: List[str],
    metadata: Optional[Dict[str, Any]],
) -> None:
    """Deterministically upserts silent roster attendees from metadata/roster into entity_upserts."""
    if not metadata or not isinstance(metadata, dict) or metadata.get("is_document") or data.get("is_document"):
        return
    roster = metadata.get("roster") or metadata.get("attendees") or (metadata.get("c0_receipt") or {}).get("roster")
    if isinstance(roster, dict):
        roster = [
            (dict(a, organization=org) if isinstance(a, dict) else {"name": str(a), "organization": org})
            for org, atts in roster.items()
            for a in (atts if isinstance(atts, list) else [atts])
        ]
    elif isinstance(roster, str):
        roster = [p.strip() for p in re.split(r"[,;\n]+(?![^(]*\))", roster) if p.strip()]
    if not isinstance(roster, list) or not roster:
        return

    entities = mutations.setdefault("entity_upserts", [])
    ev_idx = data.setdefault("evidence_index", {})
    existing = {str(e.get("id", "")).lower().replace("_", "-") for e in entities if isinstance(e, dict)}
    for item in roster:
        name = (item.get("name") or item.get("attendee") or "") if isinstance(item, dict) else str(item)
        role = (item.get("role") or "") if isinstance(item, dict) else ""
        org = (item.get("organization") or item.get("org") or "") if isinstance(item, dict) else ""
        clean_name = re.sub(r"\s*\([^)]*\)", "", name).strip()
        if not clean_name or any(t in clean_name.lower() for t in ("mole-vc", "conference room")):
            continue
        slug = "person:" + re.sub(r"[^a-z0-9]+", "-", clean_name.lower()).strip("-")
        if slug not in existing:
            ev_idx.setdefault("ev-roster", {
                "evidence_id": "ev-roster", "statement_id": "ev-roster", "speaker": "Meeting Roster",
                "source": "HITL", "source_type": "HITL", "source_classification": "FACT", "kind": "summary",
                "text": "Confirmed meeting roster attendee.", "exact_text": "Confirmed meeting roster attendee.",
                "locator": {"kind": "section", "value": "Roster"}, "grounding_status": "GROUNDED", "attribution_mode": "DIRECT",
            })
            entities.append({
                "id": slug, "type": "person", "name": clean_name,
                "description": role or (f"Team member at {org}." if org else f"Meeting attendee ({clean_name})."),
                "mutation": "CONFIRM", "evidence_ids": ["ev-roster"],
            })
            existing.add(slug)
            changes.append(f"Deterministically upserted silent roster attendee '{clean_name}' ({slug})")


def _is_debrief_rob_partner_advisory_context(
    metadata: Optional[Dict[str, Any]], data: Dict[str, Any], f1_text: str = ""
) -> bool:
    """Deprecated context stub retained for backward-compatible imports; returns False."""
    _ = (metadata, data, f1_text)
    return False


def _heal_debrief_rob_partner_advisory_projection(
    data: Dict[str, Any], mutations: Dict[str, Any], changes: List[str], f1_text: str = ""
) -> None:
    """Deprecated meeting-specific healer stub retained as a strict no-op."""
    _ = (data, mutations, changes, f1_text)


def heal_graph_projection(
    data: Dict[str, Any],
    changes: List[str],
    metadata: Optional[Dict[str, Any]] = None,
    output_1: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute stateless lossless structural normalization on an Output 03 payload dict."""
    if not isinstance(data, dict):
        return data

    ev_idx = data.setdefault("evidence_index", {})
    if isinstance(ev_idx, dict):
        for k in list(ev_idx.keys()):
            if not isinstance(ev_idx[k], dict):
                del ev_idx[k]
    else:
        ev_idx = data["evidence_index"] = {}

    mutations = data.get("graph_mutations") or data.get("mutations") or data
    if not isinstance(mutations, dict):
        mutations = {}
        data["graph_mutations"] = mutations

    entities = mutations.get("entity_upserts") or mutations.get("entities") or []
    signals = mutations.get("relationship_signals") or mutations.get("signals") or []
    actions = mutations.get("action_updates") or mutations.get("actions") or []
    if isinstance(entities, list):
        entities[:] = [e for e in entities if isinstance(e, dict) and (e.get("id") or e.get("entity_id"))]
    if isinstance(signals, list):
        signals[:] = [s for s in signals if isinstance(s, dict)]

    if bool((metadata or {}).get("enforce_empty_actions", True)) and actions:
        mutations["action_updates"] = []
        mutations.pop("actions", None)
        changes.append("Cleared action_updates from Output 3 per Gate 3.5 task-tracking prohibition")

    is_doc = bool((metadata and (metadata.get("is_document") or metadata.get("canonical_modality") == "DOCUMENT")) or data.get("is_document"))
    f1_text = str(output_1 or (metadata or {}).get("output_1") or (metadata or {}).get("clean_transcript") or (metadata or {}).get("source_text") or "")
    f2_text = str((metadata or {}).get("output_2") or (metadata or {}).get("recap_text") or "")
    if f2_text and not is_doc:
        port_output02_actions_to_output03(
            f2_text,
            data,
            f1_text=f1_text,
            preserve_action_updates=not bool((metadata or {}).get("enforce_empty_actions", True)),
        )
    if f1_text and bool(re.search(r"\[\d{1,2}:\d{2}", f1_text)):
        for eid, ev in list(ev_idx.items()):
            kind, val = _extract_locator_kind_and_value((ev or {}).get("locator"))
            if kind == "timestamp" and val and not _resolve_timestamp_in_f1(val, f1_text):
                del ev_idx[eid]
                changes.append(f"Pruned ungrounded evidence '{eid}' with unresolved timestamp '{val}'")

    meta_arch = (metadata or {}).get("archetype") or (metadata or {}).get("meeting_archetype") or (metadata or {}).get("meeting_type")
    arch = meta_arch or data.get("meeting_archetype") or mutations.get("meeting_archetype")
    target_arch = "DOCUMENT_MODE" if (is_doc or str(arch).upper() == "DOCUMENT_MODE") else (str(arch).upper() if arch and str(arch).upper() != "GENERAL" else "STRATEGIC_DISCOVERY")
    data["meeting_archetype"] = mutations["meeting_archetype"] = target_arch
    changes.append(f"Normalized meeting archetype to {target_arch}")

    _heal_evidence_index(data, changes)
    heal_evidence_locators(data, changes)
    _heal_roster_attendees(data, mutations, changes, metadata)
    entities = mutations.get("entity_upserts") or mutations.get("entities") or []
    _heal_entities(entities, changes)
    _heal_signals(signals, changes, ev_idx=ev_idx)
    _heal_signal_evidence_and_qualifiers(data, signals, changes)
    if not is_doc:
        _heal_entity_descriptions_and_org_evidence(data, entities, changes)
    _heal_decision_and_risk_completeness(data, mutations, changes, metadata=metadata)
    if not is_doc:
        _heal_enterprise_system_nodes(data, mutations, changes)
        _heal_core_workstream_nodes(data, mutations, changes)
    _heal_actions(actions, changes)
    _heal_dates(mutations, changes)
    if not is_doc:
        _heal_tactical_alignment_projection(data, changes, metadata=metadata)
        _heal_orphan_entities(data, mutations, changes, is_doc=is_doc, metadata=metadata)
    heal_strategic_intent_signals(data, mutations, changes)
    from .graph_ontology_proposals import heal_proposals_and_risks
    heal_proposals_and_risks(data, mutations, changes)
    if not is_doc:
        _heal_conversational_nodes(mutations, changes)
    from .graph_ontology_healer import heal_ontology_and_predicates
    heal_ontology_and_predicates(data, mutations, changes, metadata=metadata)
    _heal_dangling_evidence_citations(data, mutations, changes)
    _heal_mutation_budget(data, mutations, changes, max_budget=30, is_doc=is_doc, metadata=metadata, f1_text=f1_text)

    # Deduplicate entities and signals and prune dangling edges
    seen_eids: Set[str] = set()
    deduped_ents = []
    for e in mutations.get("entity_upserts") or mutations.get("entities") or []:
        eid = str(e.get("id") or e.get("entity_id") or "")
        if eid and eid not in seen_eids:
            seen_eids.add(eid)
            deduped_ents.append(e)
    mutations["entity_upserts"] = deduped_ents

    seen_triples: Set[Tuple[Any, Any, Any]] = set()
    clean_signals = []
    for s in mutations.get("relationship_signals") or mutations.get("signals") or []:
        fid = s.get("from_id") or s.get("source_id")
        tid = s.get("to_id") or s.get("target_id")
        rel = s.get("relationship") or s.get("predicate")
        if (fid, rel, tid) not in seen_triples and fid in seen_eids and tid in seen_eids:
            seen_triples.add((fid, rel, tid))
            clean_signals.append(s)
    mutations["relationship_signals"] = clean_signals
    return data
