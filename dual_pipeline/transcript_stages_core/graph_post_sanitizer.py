from __future__ import annotations

from typing import Any, Dict, Optional

from dual_pipeline.transcript_stages_core.graph_post_entity_healer import (
    align_canonical_signals,
    heal_and_align_entities,
)
from dual_pipeline.transcript_stages_core.graph_post_evidence_healer import (
    finalize_evidence_citations,
    inject_canonical_evidence,
)
from dual_pipeline.transcript_stages_core.graph_post_helpers import _normalize_decisions
from dual_pipeline.transcript_stages_core.graph_post_risk_healer import (
    heal_risks_and_proposals,
    prune_and_budget_signals,
)


def sanitize_projection_payload(
    data: Dict[str, Any],
    source_text: Optional[str] = None,
    project: Optional[str] = None,
    is_document: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Apply decomposed entity, risk, proposal, and evidence sanitization passes."""
    mutations = data.setdefault("graph_mutations", {})
    ev_index = data.setdefault("evidence_index", {})
    proj = project or data.get("project")
    is_doc = is_document or bool(data.get("is_document") or data.get("output3_only") or data.get("canonical_modality") == "DOCUMENT")

    # 1. Canonical evidence injection
    inject_canonical_evidence(data, source_text=source_text)
    fallback_ev = [next(iter(ev_index.keys()))] if ev_index else []

    # 2. Entity healing and alignment
    heal_and_align_entities(mutations, ev_index, fallback_ev, source_text=source_text, project=proj, is_document=is_doc)

    # 3. Canonical signal alignment
    align_canonical_signals(mutations, ev_index, fallback_ev, project=proj)

    # 4. Risk and proposal healing
    heal_risks_and_proposals(mutations, ev_index, fallback_ev, data)
    default_date = str(data.get("meeting_date") or data.get("date") or "09/11/2026").replace("-", "/")
    _normalize_decisions(mutations.get("decision_updates", []), default_date)
    _normalize_decisions(data.get("decision_updates", []), default_date)

    # 5. Signal pruning and budget triage
    eff_meta = dict(metadata or {})
    if is_doc and ev_index:
        eff_meta["max_mutation_budget"] = max(
            int(eff_meta.get("max_mutation_budget") or 30),
            len(mutations.get("entity_upserts", [])) + len(mutations.get("relationship_signals", [])) + 50,
        )
    prune_and_budget_signals(
        mutations, fallback_ev, is_document=is_doc, ev_index=ev_index,
        data=data, metadata=eff_meta, source_text=source_text or "",
    )

    # 6. Evidence citations finalization
    finalize_evidence_citations(data, mutations, source_text=source_text)

    return data
