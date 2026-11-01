"""Topology checks shared by document and meeting projection QA."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple


def _extract_mutation_corpus(mutations: Dict[str, Any]) -> str:
    parts: List[str] = []
    for category in ("proposal_updates", "decision_updates", "risk_updates", "action_updates"):
        for item in mutations.get(category, []):
            if isinstance(item, dict):
                parts.extend([
                    str(item.get("description") or ""),
                    str(item.get("rationale") or ""),
                    str(item.get("_audit_rationale") or ""),
                    str(item.get(f"{category[:-1]}_id") or ""),
                    str(item.get("decision_question") or ""),
                ])
    for relation in mutations.get("relationship_signals", []):
        if isinstance(relation, dict):
            parts.append(str(relation.get("_audit_rationale") or ""))
    return " ".join(parts).lower()


def _relationship_ids(signals: List[Dict[str, Any]]) -> Set[str]:
    return {
        str(signal[key])
        for signal in signals
        for key in ("from_id", "to_id")
        if signal.get(key)
    }


def _mutation_mentioned_ids(entities: List[Dict[str, Any]], mutations: Dict[str, Any]) -> Set[str]:
    corpus = _extract_mutation_corpus(mutations)
    connected: Set[str] = set()
    for entity in entities:
        entity_id = entity.get("id") or ""
        name = (entity.get("name") or "").lower()
        stem = entity_id.split(":", 1)[-1].lower().replace("-", " ")
        if (entity_id and entity_id.lower() in corpus) or (name and name in corpus) or (stem and stem in corpus):
            connected.add(entity_id)
    return connected


def _document_grounded_ids(
    entities: List[Dict[str, Any]], evidence_index: Dict[str, Any],
) -> Set[str]:
    grounded: Set[str] = set()
    for entity in entities:
        cited = entity.get("evidence_ids") or []
        if isinstance(cited, list) and cited and all(eid in evidence_index for eid in cited):
            grounded.add(str(entity.get("id") or ""))
    return grounded


def _check_topology(
    entity_upserts: List[Dict[str, Any]],
    relationship_signals: List[Dict[str, Any]],
    mutations: Optional[Dict[str, Any]] = None,
    *,
    document_evidence_index: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str]]:
    """Require graph links or, for documents, direct evidence for standalone nodes."""
    connected_ids = _relationship_ids(relationship_signals)
    if mutations:
        connected_ids.update(_mutation_mentioned_ids(entity_upserts, mutations))
    all_entity_ids = {str(entity.get("id")) for entity in entity_upserts if entity.get("id") is not None}
    orphan_ids = all_entity_ids - connected_ids
    if document_evidence_index is not None:
        orphan_ids.difference_update(_document_grounded_ids(entity_upserts, document_evidence_index))
    failures = [f"Orphan entity nodes with zero relationships: {orphan_ids}"] if orphan_ids else []
    return not failures, failures
