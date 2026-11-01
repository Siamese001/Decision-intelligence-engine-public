"""
Market Intelligence Healer: document-agnostic deterministic normalization for document ingestions.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _normalize_single_entity(ent: Any, changes: List[str]) -> None:
    if not isinstance(ent, dict):
        return
    desc = ent.get("description")
    eid = ent.get("id") or ent.get("entity_id") or "entity"
    if (desc is None or not str(desc).strip() or "meeting dialogue" in str(desc).lower()) and ent.get("name"):
        ent["description"] = f"{ent['name']} referenced in source document."
        changes.append(f"Populated missing description on entity '{eid}'")


def _normalize_market_entities(mutations: Dict[str, Any], changes: List[str]) -> None:
    for ent in mutations.get("entity_upserts", []):
        _normalize_single_entity(ent, changes)


def _normalize_single_signal(sig: Any, changes: List[str]) -> None:
    if not isinstance(sig, dict):
        return
    # Document-agnostic predicate formatting if needed
    pred = sig.get("relationship") or sig.get("predicate")
    if isinstance(pred, str):
        norm_pred = pred.strip().upper().replace(" ", "_").replace("-", "_")
        if norm_pred != pred:
            sig["relationship"] = norm_pred
            if "predicate" in sig:
                sig["predicate"] = norm_pred
            changes.append(f"Normalized predicate '{pred}' to '{norm_pred}'")


def _normalize_market_signals(mutations: Dict[str, Any], changes: List[str]) -> None:
    for sig in mutations.get("relationship_signals", []):
        _normalize_single_signal(sig, changes)


def heal_market_intel_mutations(
    data: Dict[str, Any],
    metadata: Optional[Dict[str, Any]],
    changes: List[str],
) -> None:
    """Apply document-agnostic entity and signal alignments."""
    meeting_id = str(data.get("meeting_id", ""))
    is_doc = meeting_id.startswith("document") or bool((metadata or {}).get("is_document"))
    if not is_doc:
        return

    mutations = data.get("graph_mutations", {})
    if not isinstance(mutations, dict):
        return

    src_text = str((metadata or {}).get("source_text") or (metadata or {}).get("forensic_transcript") or "")
    if src_text:
        from dual_pipeline.document_pipeline_core.tabular_roster_extractor import reconcile_tabular_workstream_mutations
        tab_ch = reconcile_tabular_workstream_mutations(data, metadata, src_text)
        if tab_ch:
            changes.extend(tab_ch)

    _normalize_market_entities(mutations, changes)
    _normalize_market_signals(mutations, changes)
