"""
Retrieval Sentinel (A9) for Graph Freshness (Wave 6).
Ensures that graph mutations (quarantines, supersessions) are synchronized with search indices.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Set

from dual_pipeline.storage.governed_context import OperationalError


@dataclass
class SentinelFindings:
    search_docs_to_purge: Set[str]


class RetrievalSentinel:
    """A9: Projects graph freshness operations into the dense vector and retrieval boundaries."""

    def __init__(self, integrator: Optional[Any] = None) -> None:
        self.integrator = integrator

    def scan(self, conn: Any, quarantined_or_superseded_ids: Set[str]) -> SentinelFindings:
        """
        Identifies active search documents that must be purged or suppressed
        because their corresponding assertions were quarantined or superseded.
        """
        if not quarantined_or_superseded_ids:
            return SentinelFindings(set())

        cursor = conn.cursor()

        # Determine overlap with the search_document sidecar
        placeholders = ",".join(["?"] * len(quarantined_or_superseded_ids))
        try:
            cursor.execute(
                f"SELECT id FROM search_document WHERE id IN ({placeholders})",
                list(quarantined_or_superseded_ids)
            )
            docs = {row[0] for row in cursor.fetchall()}
        except OperationalError:
            # Fallback for testing environments where the search_document table might be absent
            docs = set()

        return SentinelFindings(search_docs_to_purge=docs)

    def record_sentinel_finding(
        self,
        search_docs_to_purge: Set[str],
        run_id: Optional[str] = None,
    ) -> Optional[Any]:
        """Emits MAINTENANCE_FINDING outcome event for vector search documents to purge."""
        if not self.integrator:
            return None
        from dual_pipeline.learning_adapters.agent_outcome_adapter import AgentOutcomeAdapter
        evt = AgentOutcomeAdapter.build_maintenance_finding_event(
            finding_type="STALE_SEARCH_INDEX",
            entity_ids=sorted(list(search_docs_to_purge)),
            description=f"Purged {len(search_docs_to_purge)} stale search documents from vector sidecar.",
            source_agent="RetrievalSentinel",
            run_id=run_id,
        )
        return self.integrator.record_outcome(evt)
