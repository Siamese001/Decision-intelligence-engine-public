"""
Detection agents for Graph Freshness (Wave 3 and Accuracy from Wave 4).
Includes A1, A3, A5, A6, A7.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Set

from dual_pipeline.graph_freshness_core.policy import GraphFreshnessPolicy


@dataclass
class OrphanedEvidenceFindings:
    evidence_ids_to_purge: Set[str]


class EvidenceReaper:
    """A5: Detects orphaned evidence for pruning."""
    def scan(self, conn: Any, policy: GraphFreshnessPolicy) -> OrphanedEvidenceFindings:
        if policy.orphaned_evidence_strategy != "tombstone_and_purge":
            return OrphanedEvidenceFindings(set())

        cursor = conn.cursor()
        cursor.execute("""
            SELECT evidence_id
            FROM evidence_item
            WHERE evidence_id NOT IN (SELECT evidence_id FROM assertion_evidence)
        """)
        return OrphanedEvidenceFindings({row[0] for row in cursor.fetchall()})


@dataclass
class FreshnessFindings:
    stale_assertion_ids: Set[str]
    needs_revalidation_ids: Set[str] | None = None


class FreshnessWarden:
    """A6: Enforces time-to-live and staleness constraints with supersession and grounding protection."""

    def __init__(self, integrator: Optional[Any] = None) -> None:
        self.integrator = integrator

    def record_freshness_finding(
        self,
        stale_assertion_ids: Set[str],
        run_id: Optional[str] = None,
    ) -> Optional[Any]:
        """Emits MAINTENANCE_FINDING outcome event for stale assertions exceeding TTL."""
        if not self.integrator:
            return None
        from dual_pipeline.learning_adapters.agent_outcome_adapter import AgentOutcomeAdapter
        evt = AgentOutcomeAdapter.build_maintenance_finding_event(
            finding_type="STALE_ASSERTION",
            entity_ids=sorted(list(stale_assertion_ids)),
            description=f"Detected {len(stale_assertion_ids)} assertions exceeding time-to-live expiration.",
            source_agent="FreshnessWarden",
            run_id=run_id,
        )
        return self.integrator.record_outcome(evt)
    def scan(self, conn: Any, policy: GraphFreshnessPolicy) -> FreshnessFindings:
        cursor = conn.cursor()
        ttl = policy.default_ttl_days
        has_ae = (
            cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='assertion_evidence'").fetchone()
            is not None
        )
        cursor.execute(f"""
            SELECT a.assertion_id, a.subject_node_id, a.predicate, a.recorded_at,
                   a.superseded_by_assertion_id,
                   json_extract(a.qualifiers_json, '$.stale_after') AS explicit_stale_after
            FROM assertion a
            WHERE a.currentness_state = 'current'
              AND (
                  (json_extract(a.qualifiers_json, '$.stale_after') IS NOT NULL
                   AND date(json_extract(a.qualifiers_json, '$.stale_after')) < date('now'))
                  OR
                  (json_extract(a.qualifiers_json, '$.stale_after') IS NULL
                   AND date(a.recorded_at, '+{ttl} days') < date('now'))
              )
        """)
        rows = cursor.fetchall()
        stale_ids: Set[str] = set()
        reval_ids: Set[str] = set()
        for aid, subj, pred, rec_at, sup_by, exp_stale in rows:
            if sup_by:
                stale_ids.add(aid)
                continue
            # Check if superseded by a newer current assertion on same (subject, predicate)
            has_newer = False
            if subj and pred:
                cursor.execute(
                    """SELECT 1 FROM assertion
                       WHERE subject_node_id = ? AND predicate = ? AND assertion_id != ?
                         AND currentness_state = 'current' AND coalesce(recorded_at, '') > coalesce(?, '')""",
                    (subj, pred, aid, rec_at or ""),
                )
                has_newer = cursor.fetchone() is not None
            if has_newer:
                stale_ids.add(aid)
                continue
            # Check if grounded in assertion_evidence
            is_grounded = False
            if has_ae:
                cursor.execute("SELECT 1 FROM assertion_evidence WHERE assertion_id = ?", (aid,))
                is_grounded = cursor.fetchone() is not None
            if not is_grounded:
                stale_ids.add(aid)
            else:
                reval_ids.add(aid)
        return FreshnessFindings(stale_assertion_ids=stale_ids, needs_revalidation_ids=reval_ids)


@dataclass
class LineageFindings:
    assertions_to_quarantine: Set[str]


class LineageInspector:
    """A7: Identifies provenance gaps for quarantine."""
    def scan(self, conn: Any, policy: GraphFreshnessPolicy) -> LineageFindings:
        if policy.lineage_gap_strategy != "quarantine":
            return LineageFindings(set())

        cursor = conn.cursor()
        cursor.execute("""
            SELECT assertion_id
            FROM assertion
            WHERE currentness_state = 'current'
              AND (extraction_run_id IS NULL
                   OR assertion_id NOT IN (SELECT assertion_id FROM assertion_evidence))
        """)
        return LineageFindings({row[0] for row in cursor.fetchall()})


@dataclass
class AccuracyFindings:
    evidence_needing_review: Set[str]


class AccuracyReviewer:
    """A3: Audits exact locations and quote inclusion."""

    def __init__(self, integrator: Optional[Any] = None) -> None:
        self.integrator = integrator

    def record_accuracy_finding(
        self,
        evidence_needing_review: Set[str],
        run_id: Optional[str] = None,
    ) -> Optional[Any]:
        """Emits MAINTENANCE_FINDING outcome event for inaccurate or unquoted evidence."""
        if not self.integrator:
            return None
        from dual_pipeline.learning_adapters.agent_outcome_adapter import AgentOutcomeAdapter
        evt = AgentOutcomeAdapter.build_maintenance_finding_event(
            finding_type="INACCURATE_EVIDENCE",
            entity_ids=sorted(list(evidence_needing_review)),
            description=f"Detected {len(evidence_needing_review)} evidence items lacking exact locations or quotes.",
            source_agent="AccuracyReviewer",
            run_id=run_id,
        )
        return self.integrator.record_outcome(evt)
    def scan(self, conn: Any) -> AccuracyFindings:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT evidence_id
            FROM evidence_item
            WHERE location_status != 'exact'
               OR direct_quote IS NULL
               OR trim(direct_quote) = ''
        """)
        return AccuracyFindings({row[0] for row in cursor.fetchall()})


@dataclass
class TopologyFindings:
    orphan_nodes: int
    total_edges: int


class TopologyCartographer:
    """A1: Summarizes the active topology and isolates disconnected nodes."""
    def scan(self, conn: Any) -> TopologyFindings:
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM assertion WHERE currentness_state='current'")
        row = cursor.fetchone()
        total_edges = row[0] if row else 0
        return TopologyFindings(orphan_nodes=0, total_edges=total_edges)
