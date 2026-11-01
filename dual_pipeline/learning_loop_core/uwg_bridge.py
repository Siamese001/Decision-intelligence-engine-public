"""
dual_pipeline/learning_loop_core/uwg_bridge.py

Bridge to UniversalWriteGateway (UWG) learning commit lane.
Enforces authentic human operator approval origins, zero graph mutation,
and exact bidirectional rollbacks (Wave 8).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from dual_pipeline.calibration import verify_calibration_approval
from dual_pipeline.core import ROOT
from dual_pipeline.hitl_governance import validate_approval_origin
from dual_pipeline.uwg_core.gateway import UniversalWriteGateway

from .simulation import _validate_bounds

DEFAULT_PROPOSALS_DIR = ROOT / "reports" / "learning_proposals"
DEFAULT_RUNTIME_ROOT = ROOT / ".local"

logger = logging.getLogger(__name__)


def apply_answer_learning_proposal(
    proposal: Dict[str, Any],
    approval_receipt: Mapping[str, Any],
    runtime_root: Optional[Path] = None,
    proposals_dir: Optional[Path] = None,
    config_path: Optional[Path] = None,
    simulation_receipt: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Applies an approved answer learning proposal via UniversalWriteGateway.

    Invariants enforced:
    1. Rejects synthetic/stop-hook approvals (validate_approval_origin).
    2. Validates cryptographic signature and canonical proposal digest.
    3. Rejects out-of-bounds parameter values.
    4. Guarantees zero graph mutation (+0 delta, graph_sha256 unchanged).
    5. Produces APPLIED receipt and PENDING_ROLLBACK receipt.
    6. Enforces replay simulation qualification (Wave 2).
    """
    p_id = proposal.get("proposal_id", "unknown_proposal")

    # 1. Anti-synthetic origin validation
    is_human, origin_reason = validate_approval_origin(approval_receipt)
    if not is_human:
        raise PermissionError(f"UWG_COMMIT_REJECTED: non-human approval origin: {origin_reason}")

    # 2. Cryptographic signature and digest verification
    if not verify_calibration_approval(proposal, approval_receipt):
        raise PermissionError(f"UWG_COMMIT_REJECTED: approval verification failed for proposal {p_id}")

    # 3. Parameter bounds check
    ext = proposal.get("extensions", {}).get("com.partner_advisory.nee.learning", {})
    cal_ext = proposal.get("extensions", {}).get("com.partner_advisory.nee.calibration", {})
    param_name = ext.get("parameter_name") or cal_ext.get("target_parameter", "")
    proposed_val = ext.get("proposed_value") if "proposed_value" in ext else cal_ext.get("proposed_value")

    is_valid_bounds, bounds_err = _validate_bounds(param_name, proposed_val)
    if not is_valid_bounds:
        raise ValueError(f"UWG_COMMIT_REJECTED: {bounds_err}")

    # 4. Route through UniversalWriteGateway
    root = (runtime_root or DEFAULT_RUNTIME_ROOT).resolve()
    p_dir = (proposals_dir or DEFAULT_PROPOSALS_DIR).resolve()
    p_dir.mkdir(parents=True, exist_ok=True)
    seed_graph = root / "seed_graph.sqlite"

    uwg = UniversalWriteGateway(root, seed_graph)
    commit_res = uwg.process_commit_request({
        "request_type": "learning_parameter_commit",
        "proposal": proposal,
        "approval_receipt": approval_receipt,
        "config_path": str(config_path) if config_path else None,
        "proposals_dir": str(p_dir),
        "simulation_receipt": dict(simulation_receipt) if simulation_receipt else None,
        "require_replay_receipt": True,
    })

    # 5. Verify zero graph mutation invariant
    delta_counts = commit_res.get("graph_delta_counts", {})
    if delta_counts.get("total_delta", 0) != 0:
        raise RuntimeError(
            f"CRITICAL_INVARIANT_VIOLATION: Non-zero graph mutations observed during parameter commit: {delta_counts}"
        )

    return commit_res


def rollback_answer_learning_proposal(
    rollback_target: Any,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Executes exact bidirectional rollback using UniversalWriteGateway."""
    root = (runtime_root or DEFAULT_RUNTIME_ROOT).resolve()
    seed_graph = root / "seed_graph.sqlite"
    uwg = UniversalWriteGateway(root, seed_graph)

    res = uwg.process_commit_request({
        "request_type": "learning_parameter_rollback",
        "rollback_path": rollback_target if isinstance(rollback_target, (str, Path)) else None,
        "rollback_receipt": rollback_target if isinstance(rollback_target, dict) else None,
    })
    return res
