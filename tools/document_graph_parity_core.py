"""Re-export facade for Wave 4 Corpus-Wide GraphDB Parity Remediation helpers."""

from __future__ import annotations

from tools.tier2_dedup_core.document_graph_parity_core import (
    _extract_signal_claims_from_projection,
    backfill_stubs_and_truncated_projections,
    promote_all_candidates_and_empty_sources,
    promote_candidate_nodes_and_link_orphans,
    remediate_boilerplate_assertions,
    sanitize_staging_refs_in_db,
)

__all__ = [
    "_extract_signal_claims_from_projection",
    "backfill_stubs_and_truncated_projections",
    "promote_all_candidates_and_empty_sources",
    "promote_candidate_nodes_and_link_orphans",
    "remediate_boilerplate_assertions",
    "sanitize_staging_refs_in_db",
]
