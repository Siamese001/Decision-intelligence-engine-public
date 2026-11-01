"""Shared helper for gating all maintenance and operational mutations behind authentic operator approval.

Eliminates simulated run modes:
- Proposes mutations as explicit table deltas with deterministic digests.
- Checks if prior operator approval is recorded in the HITL ledger.
- If unapproved: persists approval request, presents interactive question, and blocks mutation with AWAITING_APPROVAL.
- If approved: authorizes the mutation to proceed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from dual_pipeline.core import ROOT
from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    build_active_ingestion_approval_request,
    check_active_ingestion_clearance,
    dispatch_and_persist_approval_request,
)
from dual_pipeline.hitl_governance_core.decision_record_writer import resolve_ledger_paths

logger = logging.getLogger(__name__)


def compute_mutation_digest(table_deltas: Dict[str, Any], target_resource: str) -> str:
    """Compute deterministic sha256 digest for proposed mutation."""
    payload = {
        "target": str(target_resource),
        "deltas": {k: table_deltas[k] for k in sorted(table_deltas.keys())},
    }
    canonical_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def request_mutation_approval(
    *,
    action_type: str,
    target_resource: str | Path,
    table_deltas: Dict[str, int],
    description: str,
    directive_id: Optional[str] = None,
    run_id: Optional[str] = None,
    reviewer: Optional[str] = None,
    rationale: Optional[str] = None,
    ledger_path: Optional[Path] = None,
    storage_root: Optional[Path] = None,
    interactive_transport: Optional[Callable[[Dict[str, Any]], Any]] = None,
) -> Dict[str, Any]:
    """Gate a maintenance or operational mutation behind authentic HITL approval.

    Returns:
        Dict with 'cleared' (bool), 'status' ('APPROVED', 'AWAITING_APPROVAL', 'DECLINED'),
        'request_id', 'delta_digest', and 'table_deltas'.
    """
    target_str = str(target_resource)
    delta_digest = compute_mutation_digest(table_deltas, target_str)
    act_id = directive_id or f"directive-maint-{action_type.lower().replace('_', '-')}"
    r_id = run_id or f"run-maint-{action_type.lower().replace('_', '-')}-{delta_digest[:8]}"

    s_path, j_path = resolve_ledger_paths() if not ledger_path else (ledger_path, ledger_path.with_suffix(".json"))
    st_root = Path(storage_root or ROOT / ".local" / "dual_pipeline").resolve()

    # 1. Check if clearance is already recorded in the ledger
    is_cleared, clear_reason, clear_rec = check_active_ingestion_clearance(
        run_id=r_id,
        directive_id=act_id,
        delta_digest=delta_digest,
        ledger_path=s_path,
    )

    if is_cleared:
        logger.info("[MUTATION_APPROVED] Prior approval verified in ledger for %s (digest: %s)", action_type, delta_digest[:8])
        return {
            "cleared": True,
            "status": "APPROVED",
            "request_id": clear_rec.get("decision_id") if clear_rec else None,
            "delta_digest": delta_digest,
            "table_deltas": table_deltas,
            "reason": clear_reason,
            "clearance_record": clear_rec,
        }

    if "DECLINED" in clear_reason:
        logger.warning("[MUTATION_DECLINED] Operator explicitly declined %s (digest: %s)", action_type, delta_digest[:8])
        return {
            "cleared": False,
            "status": "DECLINED",
            "delta_digest": delta_digest,
            "table_deltas": table_deltas,
            "reason": clear_reason,
        }

    # 2. Build approval request
    req = build_active_ingestion_approval_request(
        run_id=r_id,
        directive_id=act_id,
        runtime_root=str(st_root),
        project="maintenance",
        source_package_digest=delta_digest,
        proposed_delta_digest=delta_digest,
        baseline_generation=0,
        table_deltas=table_deltas,
        summary_context=f"Maintenance {action_type} on {target_str}: {description}",
    )

    dispatched = dispatch_and_persist_approval_request(
        req,
        transport=interactive_transport,
        ledger_path=s_path,
        storage_root=st_root,
    )

    resp_status = dispatched.get("response_status")
    if resp_status == "APPROVED":
        return {
            "cleared": True,
            "status": "APPROVED",
            "request_id": dispatched["request_id"],
            "delta_digest": delta_digest,
            "table_deltas": table_deltas,
        }
    if resp_status == "DECLINED":
        return {
            "cleared": False,
            "status": "DECLINED",
            "request_id": dispatched["request_id"],
            "delta_digest": delta_digest,
            "table_deltas": table_deltas,
            "reason": "Operator declined mutation",
        }

    # Print clean instruction for operator
    print(
        f"\n================================================================================\n"
        f"[MAINTENANCE MUTATION PROPOSED: {action_type}]\n"
        f"Target: {target_str}\n"
        f"Proposed Deltas: {table_deltas}\n"
        f"Context: {description}\n"
        f"Status: AWAITING_APPROVAL (Request ID: {dispatched['request_id']})\n"
        f"\nAction Required: Authorize mutation via ask_user_question or run:\n"
        f"  nee ingest-resolve --request-id {dispatched['request_id']} --decision approve\n"
        f"================================================================================\n",
        flush=True,
    )

    return {
        "cleared": False,
        "status": "AWAITING_APPROVAL",
        "request_id": dispatched["request_id"],
        "delta_digest": delta_digest,
        "table_deltas": table_deltas,
        "reason": "Awaiting authentic operator response via ask_user_question / nee ingest-resolve",
    }
