"""Governed Graph Health & Remediation Scout (Advisory Sub-Agent).

Specializes in post-ingestion graph diagnosis without active mutation:
1. Zero-Mutation Enforcement: strictly read-only SQLite access (query_only=ON).
2. Scan 1: Near-duplicate entity clustering and alias drift detection.
3. Scan 2: Temporal contradiction and assertion supersession candidate detection.
4. Scan 3: Orphan node and candidate clutter detection.
5. Emits contract-sealed ``learning-change-proposal:1.0.0`` proposals with
   approval_required=True and auto_apply=False.
6. Enforces HITL 25% ambiguity margin gating (Delta = c_top - c_second).
"""
from __future__ import annotations

from .graph_scout_core import (
    _STOPWORDS,
    ClutterFinding,
    ContradictionFinding,
    DuplicateEntityFinding,
    GraphScout,
    _normalize_name,
    _string_similarity,
    _token_jaccard,
    emit_proposals,
)

__all__ = [
    "ClutterFinding",
    "ContradictionFinding",
    "DuplicateEntityFinding",
    "GraphScout",
    "_STOPWORDS",
    "_normalize_name",
    "_string_similarity",
    "_token_jaccard",
    "emit_proposals",
    "check_scheduled_scout_due",
    "record_scheduled_scout_complete",
]


def record_scheduled_scout_complete(args: Any, report: dict[str, Any]) -> None:
    """Records scheduled scout full scan completion to the ledger."""
    if getattr(args, "scheduled", False):
        from .graph_mutation_ledger import GraphMutationLedger
        ledger = GraphMutationLedger(args.runtime_root / "graph_mutation_ledger.json")
        ledger.record_full_scan(run_id=report["run_id"])
        report["status"] = "SCHEDULED_COMPLETE"


def check_scheduled_scout_due(args: Any) -> Optional[dict[str, Any]]:
    """Checks if a scheduled scout run should be gated or skipped."""
    if not getattr(args, "scheduled", False):
        return None

    import json
    import logging
    from .graph_mutation_ledger import GraphMutationLedger
    from .proposal_queue import ProposalQueue

    logger = logging.getLogger("dual_pipeline.graph_scout")
    queue = ProposalQueue()
    max_backlog = getattr(args, "max_proposal_backlog", 20)
    if queue.is_queue_gated(max_backlog=max_backlog):
        msg = (
            f"Proposal queue backlog threshold reached ({queue.get_open_proposals_count()} >= {max_backlog}). "
            "Halting scheduled full-graph scout until operator review resolves pending proposals."
        )
        logger.warning(msg)
        if getattr(args, "json", False):
            print(json.dumps({"status": "QUEUE_GATED", "message": msg, "open_proposals": queue.get_open_proposals_count()}))
        else:
            print(f"[GRAPH-SCOUT] [GATED] {msg}")
        return {"status": "QUEUE_GATED", "message": msg, "open_proposals": queue.get_open_proposals_count()}

    ledger = GraphMutationLedger(args.runtime_root / "graph_mutation_ledger.json")
    cadence_mutations = getattr(args, "cadence_mutations", 10)
    cadence_days = getattr(args, "cadence_days", 7)
    if not ledger.should_trigger_scheduled_scout(cadence_mutations=cadence_mutations, cadence_days=cadence_days):
        msg = (
            f"Scheduled scout not due (cumulative mutations={ledger.get_total_mutations()}, "
            f"threshold={cadence_mutations} mutations or {cadence_days} days)."
        )
        logger.info(msg)
        if getattr(args, "json", False):
            print(json.dumps({"status": "SCHEDULED_NOT_DUE", "message": msg, "cumulative_mutations": ledger.get_total_mutations()}))
        else:
            print(f"[GRAPH-SCOUT] [NOT DUE] {msg}")
        return {"status": "SCHEDULED_NOT_DUE", "message": msg, "cumulative_mutations": ledger.get_total_mutations()}

    return None
