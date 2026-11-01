"""
Remediation Judge (A10) for Graph Freshness (Wave 7).
Compiles findings into an atomic SQL transaction script and generates calibrated TTL decay proposals.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.graph_freshness_core.agents.detection import (
    FreshnessFindings,
    LineageFindings,
    OrphanedEvidenceFindings,
)
from dual_pipeline.graph_freshness_core.agents.sentinel import SentinelFindings
from dual_pipeline.graph_freshness_core.agents.supersession import SupersessionFindings


@dataclass
class RemediationPlan:
    statements: List[Tuple[str, tuple]]
    sql_script: str


class RemediationJudge:
    """A10: Bundles agent findings into a deterministic SQLite transaction and emits learning proposals."""

    def build_transaction(
        self,
        orphaned: OrphanedEvidenceFindings,
        freshness: FreshnessFindings,
        lineage: LineageFindings,
        supersession: SupersessionFindings,
        sentinel: SentinelFindings,
    ) -> RemediationPlan:
        statements: List[Tuple[str, tuple]] = []
        sql_lines = ["BEGIN TRANSACTION;"]

        # 1. Purge orphaned evidence
        for eid in sorted(list(orphaned.evidence_ids_to_purge)):
            q = "DELETE FROM evidence_item WHERE evidence_id = ?;"
            statements.append((q, (eid,)))
            sql_lines.append(f"DELETE FROM evidence_item WHERE evidence_id = '{eid}';")

        # 2. Stale assertions
        for aid in sorted(list(freshness.stale_assertion_ids)):
            q = "UPDATE assertion SET currentness_state='historical', retrieval_eligible=0 WHERE assertion_id = ?;"
            statements.append((q, (aid,)))
            sql_lines.append(f"UPDATE assertion SET currentness_state='historical', retrieval_eligible=0 WHERE assertion_id = '{aid}';")

        # 3. Quarantined assertions
        for aid in sorted(list(lineage.assertions_to_quarantine)):
            q = "UPDATE assertion SET validation_state='needs_review', retrieval_eligible=0 WHERE assertion_id = ?;"
            statements.append((q, (aid,)))
            sql_lines.append(f"UPDATE assertion SET validation_state='needs_review', retrieval_eligible=0 WHERE assertion_id = '{aid}';")

        # 4. Superseded assertions
        for loser, winner in sorted(list(supersession.superseded_mapping.items())):
            q = "UPDATE assertion SET superseded_by_assertion_id=?, resolution_state='superseded', currentness_state='historical', retrieval_eligible=0 WHERE assertion_id=?;"
            statements.append((q, (winner, loser)))
            sql_lines.append(f"UPDATE assertion SET superseded_by_assertion_id='{winner}', resolution_state='superseded', currentness_state='historical', retrieval_eligible=0 WHERE assertion_id='{loser}';")

        # 5. Purge search documents
        for doc_id in sorted(list(sentinel.search_docs_to_purge)):
            q = "DELETE FROM search_document WHERE id = ?;"
            statements.append((q, (doc_id,)))
            sql_lines.append(f"DELETE FROM search_document WHERE id = '{doc_id}';")

        sql_lines.append("COMMIT;")
        return RemediationPlan(statements=statements, sql_script="\n".join(sql_lines))

    def generate_ttl_proposal(
        self,
        current_ttl_days: int,
        freshness_findings: FreshnessFindings,
        min_ttl_days: int = 30,
        max_ttl_days: int = 180,
        auto_apply: bool = False,
    ) -> Dict[str, Any]:
        """
        Generates calibrated TTL decay proposal conforming to learning-change-proposal:1.0.0.
        Strictly enforces auto_apply=False and approval_required=True.
        """
        if auto_apply:
            raise PermissionError("FORGERY_DETECTED: auto_apply: true forbidden for maintenance proposals")

        stale_count = len(freshness_findings.stale_assertion_ids)
        if stale_count > 10:
            proposed_ttl = max(min_ttl_days, current_ttl_days - 15)
        elif stale_count == 0:
            proposed_ttl = min(max_ttl_days, current_ttl_days + 15)
        else:
            proposed_ttl = current_ttl_days

        proposal_id = f"proposal-ttl-{uuid.uuid4().hex[:8]}"
        return {
            "schema_version": "learning-change-proposal:1.0.0",
            "proposal_id": proposal_id,
            "target_parameter": "graph_freshness.default_ttl_days",
            "current_value": current_ttl_days,
            "proposed_value": proposed_ttl,
            "drift_cohort_size": stale_count,
            "source_agent": "RemediationJudge",
            "approval_required": True,
            "auto_apply": False,
            "status": "proposed",
            "evidence_summary": f"Observed {stale_count} stale assertions under {current_ttl_days}d TTL; calibrated recommendation is {proposed_ttl}d.",
            "evaluation_plan": [
                "Simulate TTL decay across historical assertion dataset.",
                "Verify zero premature eviction of verified grounded assertions.",
                "Operator sign-off required in HITL decision ledger.",
            ],
        }
