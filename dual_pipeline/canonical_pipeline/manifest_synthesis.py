"""
dual_pipeline/canonical_pipeline/manifest_synthesis.py

Layer 5: Automatic synthesis of EvidenceCoverageManifest from Output 03
canonical signal projections and intake packages.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from dual_pipeline.canonical_pipeline.evidence_coverage import (
    EvidenceCoverageManifest,
    EvidenceItem,
    EvidenceState,
    ItemType,
)


def _extract_entity_items(entities: List[Any], seen_keys: Set[str]) -> List[EvidenceItem]:
    items: List[EvidenceItem] = []
    for i, ent in enumerate(entities):
        if not isinstance(ent, dict):
            continue
        name = str(ent.get("name") or ent.get("canonical_name") or ent.get("id") or "").strip()
        if not name:
            continue
        eid = str(ent.get("id") or ent.get("node_id") or name).strip()
        etype = str(ent.get("entity_type") or ent.get("type") or "ENTITY").upper()
        key = f"entity:{name.lower()}:{etype}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        item_type = (
            ItemType.PERSON_IDENTITY.value
            if etype in ("PERSON", "STAKEHOLDER", "ATTENDEE", "SPEAKER")
            else ItemType.TABLE_RECORD.value
        )
        items.append(
            EvidenceItem(
                item_id=f"fact-entity-{eid}",
                item_type=item_type,
                is_eligible_graph_fact=True,
                state=EvidenceState.ACTIVATED.value,
                source_origin={"source_type": "OUTPUT_03", "locator": f"entity_upserts[{i}]"},
                disposition_reason="Grounded in Output 03 canonical signal projection",
                proposed_fact={"subject": name, "predicate": "EXISTENCE", "object": etype},
            )
        )
    return items


_KNOWN_GRAPH_PREDICATES = {
    "AFFILIATED_WITH", "CANDIDATE_FOR", "CONSTRAINS", "CONTAINS", "CONTEXT_FOR", "CONTRIBUTES_TO",
    "DELIVERS", "DEPENDS_ON", "GOVERNS", "HAS_RISK", "HAS_ROLE_IN", "OWNS_OR_LEADS",
    "RELATED_TO", "REPORTS_TO", "ROUTES_TO", "SUPPORTED_BY", "TRANSITIONS_TO", "USES_PATTERN", "VALIDATES",
}
_PREDICATE_ALIASES = {
    "MEMBER_OF": "AFFILIATED_WITH", "ASSIGNED_TO": "AFFILIATED_WITH", "PART_OF": "AFFILIATED_WITH",
    "OWNS": "OWNS_OR_LEADS", "LEADS": "OWNS_OR_LEADS", "MANAGES": "OWNS_OR_LEADS",
    "SPONSORS": "GOVERNS", "INTEGRATES_WITH": "RELATED_TO", "ACCELERATES": "CONTRIBUTES_TO",
    "DELIVERS_VIA": "DELIVERS", "DELIVERS_VALUE_TO": "DELIVERS", "HAS_ROLE": "HAS_ROLE_IN",
    "USES_SYSTEM": "DEPENDS_ON", "HAS_METRIC": "CONTAINS",
}


def canonicalize_relationship_predicate(raw_pred: str) -> str:
    """Map raw Output 03 relationship predicates to canonical GraphDB predicates."""
    norm = str(raw_pred or "").upper().strip().replace(" ", "_")
    if norm in _KNOWN_GRAPH_PREDICATES:
        return norm
    if norm in _PREDICATE_ALIASES:
        return _PREDICATE_ALIASES[norm]
    return "RELATED_TO"


def _extract_signal_items(signals: List[Any], seen_keys: Set[str]) -> List[EvidenceItem]:
    items: List[EvidenceItem] = []
    for i, sig in enumerate(signals):
        if not isinstance(sig, dict):
            continue
        from_id = str(sig.get("from_id") or sig.get("from") or sig.get("subject") or "").strip()
        to_id = str(sig.get("to_id") or sig.get("to") or sig.get("object") or "").strip()
        raw_rel = str(sig.get("relationship") or sig.get("predicate") or sig.get("type") or "").upper().strip()
        if not (from_id and to_id and raw_rel):
            continue
        rel = canonicalize_relationship_predicate(raw_rel)
        key = f"rel:{from_id.lower()}:{rel}:{to_id.lower()}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        items.append(
            EvidenceItem(
                item_id=f"fact-rel-{i}",
                item_type=ItemType.TABLE_RECORD.value,
                is_eligible_graph_fact=True,
                state=EvidenceState.ACTIVATED.value,
                source_origin={"source_type": "OUTPUT_03", "locator": f"relationship_signals[{i}]"},
                disposition_reason="Grounded in Output 03 canonical signal projection",
                proposed_fact={"subject": from_id, "predicate": rel, "raw_predicate": raw_rel, "object": to_id},
            )
        )
    return items


def _extract_claim_like_items(
    records: List[Any],
    locator_prefix: str,
    id_prefix: str,
    text_fields: tuple[str, ...],
    default_subject: str,
    seen_keys: Set[str],
) -> List[EvidenceItem]:
    items: List[EvidenceItem] = []
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        subj = str(
            rec.get("subject")
            or rec.get("subject_node_id")
            or rec.get("entity_id")
            or rec.get("subject_id")
            or rec.get("owner_id")
            or rec.get("owner")
            or default_subject
            or ""
        ).strip()
        pred = str(rec.get("predicate") or "HAS_CLAIM_TEXT").upper().strip()
        obj = str(
            rec.get("object")
            or rec.get("object_literal")
            or next((rec.get(f) for f in text_fields if rec.get(f)), "")
            or ""
        ).strip()
        if not (subj and pred and obj):
            continue
        key = f"{id_prefix}:{subj.lower()}:{pred}:{obj.lower()}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        items.append(
            EvidenceItem(
                item_id=f"fact-{id_prefix}-{i}",
                item_type=ItemType.TABLE_RECORD.value,
                is_eligible_graph_fact=True,
                state=EvidenceState.ACTIVATED.value,
                source_origin={"source_type": "OUTPUT_03", "locator": f"{locator_prefix}[{i}]"},
                disposition_reason="Grounded in Output 03 canonical signal projection",
                proposed_fact={"subject": subj, "predicate": pred, "object": obj},
            )
        )
    return items


def _extract_assertion_items(assertions: List[Any], seen_keys: Set[str], default_subject: str = "") -> List[EvidenceItem]:
    return _extract_claim_like_items(
        assertions,
        "assertion_updates",
        "asrt",
        ("assertion_text", "assertion", "claim", "statement", "description", "title", "summary", "text"),
        default_subject,
        seen_keys,
    )


def _extract_speaker_items(speakers: List[Any], seen_keys: Set[str]) -> List[EvidenceItem]:
    items: List[EvidenceItem] = []
    for i, spk in enumerate(speakers):
        if not isinstance(spk, dict):
            continue
        name = str(spk.get("reported_name") or spk.get("name") or "").strip()
        role = str(spk.get("role") or spk.get("title") or "").strip()
        if not (name and role):
            continue
        key = f"speaker:{name.lower()}:{role.lower()}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        items.append(
            EvidenceItem(
                item_id=f"fact-speaker-{i}",
                item_type=ItemType.SPEAKER_ROLE.value,
                is_eligible_graph_fact=True,
                state=EvidenceState.ACTIVATED.value,
                source_origin={"source_type": "OUTPUT_03", "locator": f"speakers[{i}]"},
                disposition_reason="Grounded in Output 03 speaker attribution",
                proposed_fact={"subject": name, "predicate": "ROLE", "object": role},
            )
        )
    return items


def _extract_auxiliary_items(mutations: Dict[str, Any], entities: List[Any], seen_keys: Set[str]) -> List[EvidenceItem]:
    default_subj = next(
        (str(e.get("id") or e.get("entity_id") or e.get("name") or "") for e in entities if isinstance(e, dict) and (e.get("id") or e.get("entity_id") or e.get("name"))),
        "",
    )
    items = _extract_assertion_items(mutations.get("assertion_updates") or mutations.get("assertions") or [], seen_keys, default_subj)
    items.extend(
        _extract_claim_like_items(
            mutations.get("decision_updates") or [], "decision_updates", "dec",
            ("decision_question", "decision_text", "decision", "description", "statement", "title", "rationale", "summary", "text"),
            default_subj, seen_keys,
        )
    )
    items.extend(
        _extract_claim_like_items(
            mutations.get("action_updates") or [], "action_updates", "act",
            ("action_text", "action", "description", "statement", "title", "summary", "text"),
            default_subj, seen_keys,
        )
    )
    return items


def build_manifest_from_output_03(
    output_03_dict: Dict[str, Any],
    run_id: str = "run-auto",
    source_digest: str = "0" * 64,
) -> EvidenceCoverageManifest:
    """Extracts eligible graph facts from Output 03 canonical signal projection into a coverage manifest."""
    seen_keys: Set[str] = set()
    mutations = output_03_dict.get("graph_mutations") or output_03_dict.get("mutations") or output_03_dict
    if not isinstance(mutations, dict):
        mutations = {}

    items: List[EvidenceItem] = []
    entities = mutations.get("entity_upserts") or mutations.get("entities") or []
    items.extend(_extract_entity_items(entities, seen_keys))
    signals = mutations.get("relationship_signals") or mutations.get("relationships") or []
    items.extend(_extract_signal_items(signals, seen_keys))
    items.extend(_extract_auxiliary_items(mutations, entities, seen_keys))
    speakers = output_03_dict.get("speakers") or []
    items.extend(_extract_speaker_items(speakers, seen_keys))

    return EvidenceCoverageManifest(
        manifest_id=f"cov-{run_id}",
        run_id=run_id,
        source_digest=source_digest,
        items=items,
        session_id=output_03_dict.get("meeting_id") or output_03_dict.get("session_id"),
        meeting_slug=output_03_dict.get("meeting_id"),
    )
