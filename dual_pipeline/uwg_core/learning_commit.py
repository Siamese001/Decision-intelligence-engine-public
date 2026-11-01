"""
dual_pipeline/uwg_core/learning_commit.py

Dedicated governed UWG commit and rollback execution engine for system learning parameters.
Enforces authentic approval origins, replay qualification, compare-and-swap,
duplicate apply prevention, zero graph mutation, and exact bidirectional absent-key restoration.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..contracts import create_artifact
from ..core import read_json, sha256_bytes, stable_id, utc_now, write_json


def _parse_learning_proposal_and_approval(
    request: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    from ..calibration import verify_calibration_approval
    from ..hitl_core.envelope import validate_approval_origin

    p_val = request.get("proposal")
    if isinstance(p_val, (str, Path)):
        proposal = read_json(Path(p_val))
    elif isinstance(p_val, dict):
        proposal = p_val
    else:
        raise ValueError("UWG learning parameter commit requires 'proposal' (dict or path)")

    a_val = request.get("approval_receipt")
    if isinstance(a_val, (str, Path)):
        approval_receipt = read_json(Path(a_val))
    elif isinstance(a_val, dict):
        approval_receipt = a_val
    else:
        raise ValueError("UWG learning parameter commit requires 'approval_receipt' (dict or path)")

    proposal_id = proposal.get("proposal_id")
    if not proposal_id:
        raise ValueError("Proposal is missing 'proposal_id'")

    is_human, origin_reason = validate_approval_origin(approval_receipt)
    if not is_human:
        raise PermissionError(f"UWG_COMMIT_REJECTED: non-human approval origin: {origin_reason}")

    if not verify_calibration_approval(proposal, approval_receipt):
        raise PermissionError(f"UWG_COMMIT_REJECTED: approval verification failed for proposal {proposal_id}")

    return proposal, approval_receipt, proposal_id


def _collect_applied_params(ext: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
    target_param = ext.get("target_parameter") or ext.get("parameter_name")
    proposed_val = ext.get("proposed_value")
    applied_params: dict[str, Any] = {}
    if target_param and proposed_val is not None:
        applied_params[str(target_param)] = proposed_val
    multi_map = ext.get("route_multipliers_map")
    if isinstance(multi_map, Mapping):
        applied_params.update({str(k): v for k, v in multi_map.items() if v is not None})
    if "applied_parameters" in request:
        applied_params.update(request["applied_parameters"])
    if not applied_params:
        raise ValueError("No parameters specified to apply in proposal or commit request")
    return applied_params


def _resolve_and_validate_learning_params(
    proposal: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any]:
    ext = proposal.get("extensions", {}).get("com.partner_advisory.nee.calibration") or proposal.get("extensions", {}).get("com.partner_advisory.nee.learning", {})
    applied_params = _collect_applied_params(ext, request)

    from ..learning_loop_core.models import RETRIEVAL_PARAMETER_BOUNDS
    from ..parameters import COPILOT_PARAMETER_BOUNDS, HITL_PARAMETER_BOUNDS
    param_bounds = {
        k: (b[0], b[1])
        for k, b in {**HITL_PARAMETER_BOUNDS, **COPILOT_PARAMETER_BOUNDS, **RETRIEVAL_PARAMETER_BOUNDS}.items()
    }

    for param_name, val in applied_params.items():
        if param_name in param_bounds:
            min_b, max_b = param_bounds[param_name]
            if not (min_b <= float(val) <= max_b):
                raise ValueError(
                    f"UWG_COMMIT_REJECTED: parameter '{param_name}' value {val} out of bounds [{min_b}, {max_b}]"
                )
    return applied_params


def _find_simulation_receipt(
    proposal: dict[str, Any],
    proposal_id: str,
    proposals_dir: Path,
    request: dict[str, Any],
) -> Optional[dict[str, Any]]:
    sim = (
        request.get("simulation_receipt")
        or request.get("replay_receipt")
        or proposal.get("simulation_receipt")
        or proposal.get("replay_receipt")
    )
    if sim:
        return sim
    for fname in (f"{proposal_id}_simulation_receipt.json", f"{proposal_id}_replay_receipt.json"):
        sp = proposals_dir / fname
        if sp.is_file():
            return read_json(sp)
    return None


def _validate_simulation_receipt(
    sim_receipt: dict[str, Any],
    proposal: dict[str, Any],
    proposal_id: str,
) -> None:
    status = sim_receipt.get("status")
    success = sim_receipt.get("success", False)
    if status not in ("SIMULATED_PASS", "PASS") or not success:
        raise ValueError(f"REPLAY_QUALIFICATION_FAILED: Replay status is '{status}' (must be SIMULATED_PASS or PASS)")

    prop_digest = proposal.get("canonical_digest", {}).get("value")
    receipt_digest = sim_receipt.get("proposal_digest")
    if prop_digest and receipt_digest and receipt_digest != prop_digest:
        raise ValueError("REPLAY_TAMPERED: Replay receipt proposal_digest does not match proposal canonical digest")

    if sim_receipt.get("proposal_id") and sim_receipt.get("proposal_id") != proposal_id:
        raise ValueError("REPLAY_TAMPERED: Replay receipt proposal_id does not match proposal")


def _verify_replay_qualification(
    proposal: dict[str, Any],
    proposal_id: str,
    proposals_dir: Path,
    request: dict[str, Any],
) -> None:
    ext = proposal.get("extensions", {}).get("com.partner_advisory.nee.learning", {})
    cal_ext = proposal.get("extensions", {}).get("com.partner_advisory.nee.calibration", {})
    cat = ext.get("category") or cal_ext.get("category") or proposal.get("proposal_type")
    require_replay = request.get("require_replay_receipt", False) or request.get("require_replay_verification", False)
    if cat in ("copilot_context", "copilot_grounding", "query_retrieval", "retrieval_weight") or proposal.get("proposal_type") == "retrieval_weight":
        require_replay = True

    sim_receipt = _find_simulation_receipt(proposal, proposal_id, proposals_dir, request)
    if require_replay and not sim_receipt:
        raise ValueError(f"REPLAY_RECEIPT_REQUIRED: Missing simulation or replay receipt for proposal '{proposal_id}'")

    if sim_receipt:
        _validate_simulation_receipt(sim_receipt, proposal, proposal_id)


def _check_duplicate_apply(proposal_id: str, proposals_dir: Path) -> None:
    apply_path = proposals_dir / f"{proposal_id}_applied_receipt.json"
    if apply_path.is_file():
        prior = read_json(apply_path)
        if prior.get("status") == "APPLIED":
            raise RuntimeError(
                f"DUPLICATE_APPLY_REJECTED: Proposal '{proposal_id}' has already been applied at {prior.get('applied_at')}"
            )


def _check_decision_clearance(gateway: Any, request: dict[str, Any]) -> None:
    if request.get("skip_decision_clearance", False):
        return
    from dual_pipeline.hitl_governance_core.decision_clearance import require_cleared

    repo_root = request.get("repo_root") or getattr(gateway, "runtime_root", None)
    clearance = require_cleared(
        repo_root=repo_root,
        plan_path=request.get("plan_path"),
        allow_zero_decisions=True,
    )
    if not clearance.cleared:
        raise RuntimeError(
            f"OPERATOR_INPUT_REQUIRED: Parameter commit blocked by {len(clearance.open_decisions)} open operator decision(s)."
        )


def _values_match(curr_v: Any, exp_v: Any) -> bool:
    if curr_v == exp_v:
        return True
    try:
        return abs(float(curr_v) - float(exp_v)) < 1e-6
    except (ValueError, TypeError):
        return False


def _resolve_expected_cas_values(proposal: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    exp_vals: dict[str, Any] = request.get("expected_values", {})
    if exp_vals or not request.get("enforce_cas", True):
        return exp_vals
    ext = proposal.get("extensions", {}).get("com.partner_advisory.nee.learning") or proposal.get("extensions", {}).get("com.partner_advisory.nee.calibration") or {}
    pname = ext.get("parameter_name") or ext.get("target_parameter")
    pval = ext.get("current_value")
    if pname and pval is not None:
        return {pname: pval}
    return {}


def _get_canonical_default(p: str) -> Any:
    from ..learning_loop_core.models import RETRIEVAL_PARAMETER_BOUNDS
    from ..parameters_ops import COPILOT_PARAMETER_BOUNDS, HITL_PARAMETER_BOUNDS
    all_bounds = {**HITL_PARAMETER_BOUNDS, **COPILOT_PARAMETER_BOUNDS, **RETRIEVAL_PARAMETER_BOUNDS}
    return all_bounds[p][2] if p in all_bounds and len(all_bounds[p]) >= 3 else None


def _check_cas(
    target_reg: Any,
    applied_params: dict[str, Any],
    proposal: dict[str, Any],
    request: dict[str, Any],
) -> None:
    exp_vals = _resolve_expected_cas_values(proposal, request)
    for p, exp_v in exp_vals.items():
        if p in applied_params and exp_v is not None:
            curr_v = target_reg.get(p)
            if curr_v is None:
                curr_v = _get_canonical_default(p)
            if not _values_match(curr_v, exp_v):
                raise ValueError(
                    f"CAS_CONFLICT: Parameter '{p}' current value {curr_v} does not match expected {exp_v}"
                )


def _apply_parameters_with_rollback(
    target_reg: Any,
    config_path: Path,
    applied_params: dict[str, Any],
    prev_params: dict[str, Any],
    absent_params: list[str],
    gateway: Any,
    active_before: Any,
    graph_sha256: str,
    generation_id: str,
) -> None:
    from ..parameters import reload_parameters

    applied_so_far: list[str] = []
    try:
        for param_name, val in applied_params.items():
            target_reg.set(param_name, val, persist=True, config_path=config_path)
            applied_so_far.append(param_name)
        gateway._verify_active_pair_unchanged(active_before, graph_sha256, generation_id)
        reload_parameters()
    except Exception:
        for p in applied_so_far:
            if p in absent_params:
                target_reg.unset(p, persist=True, config_path=config_path)
            elif p in prev_params:
                target_reg.set(p, prev_params[p], persist=True, config_path=config_path)
        reload_parameters()
        raise


def commit_learning_parameters(gateway: Any, request: dict[str, Any]) -> dict[str, Any]:
    """Governed UWG lane for atomic system learning parameter commits."""
    proposal, approval_receipt, proposal_id = _parse_learning_proposal_and_approval(request)
    applied_params = _resolve_and_validate_learning_params(proposal, request)

    proposals_dir = Path(request.get("proposals_dir") or (gateway.runtime_root / "learning_proposals"))
    proposals_dir.mkdir(parents=True, exist_ok=True)

    _check_duplicate_apply(proposal_id, proposals_dir)
    _verify_replay_qualification(proposal, proposal_id, proposals_dir, request)
    _check_decision_clearance(gateway, request)

    from ..parameters import ParameterRegistry

    cfg_raw = request.get("config_path")
    config_path = Path(cfg_raw) if cfg_raw else ParameterRegistry().config_path
    target_reg = ParameterRegistry(config_path=config_path)

    raw_json = target_reg._load_json()
    prev_params: dict[str, Any] = {}
    absent_params: list[str] = []
    for p in applied_params:
        found, val = target_reg._lookup_dotted(raw_json, p)
        if found:
            prev_params[p] = val
        else:
            absent_params.append(p)

    _check_cas(target_reg, applied_params, proposal, request)

    active_before, graph_sha256, generation_id = gateway._snapshot_active_for_learning()
    _apply_parameters_with_rollback(
        target_reg, config_path, applied_params, prev_params, absent_params,
        gateway, active_before, graph_sha256, generation_id,
    )

    commit_id = stable_id("commit-learn", proposal_id, length=24)
    rollback_id = stable_id("rollback-learn", proposal_id, length=24)
    applied_at = utc_now()
    raw_digest = proposal.get("canonical_digest", {}).get("value")
    prop_digest = raw_digest or sha256_bytes(json.dumps(proposal, sort_keys=True).encode("utf-8"))

    apply_receipt = create_artifact(
        "uwg-learning-commit-receipt",
        commit_id,
        commit_id=commit_id,
        commit_type="learning_parameter_commit",
        proposal_id=proposal_id,
        proposal_digest=prop_digest,
        approval_receipt_id=approval_receipt["receipt_id"],
        applied_by=approval_receipt.get("approved_by", "operator"),
        applied_at=applied_at,
        status="APPLIED",
        applied_parameters=applied_params,
        previous_parameters=prev_params,
        rollback_receipt_id=rollback_id,
        active_pair_binding={"generation_id": generation_id, "graph_sha256": graph_sha256},
        graph_delta_counts={"node": 0, "assertion": 0, "evidence_item": 0, "total_delta": 0},
        extensions={"com.partner_advisory.nee.learning": {"absent_parameters": absent_params}} if absent_params else {},
    )

    rollback_receipt = {
        "receipt_id": rollback_id,
        "commit_id": commit_id,
        "proposal_id": proposal_id,
        "restore_parameters": prev_params,
        "absent_parameters": absent_params,
        "target_config_path": str(config_path),
        "status": "PENDING_ROLLBACK",
        "created_at": applied_at,
    }

    apply_path = proposals_dir / f"{proposal_id}_applied_receipt.json"
    rb_path = proposals_dir / f"{proposal_id}_rollback_receipt.json"
    write_json(apply_path, apply_receipt)
    write_json(rb_path, rollback_receipt)

    return {
        "status": "APPLIED",
        "commit_id": commit_id,
        "receipt_path": str(apply_path),
        "rollback_receipt_path": str(rb_path),
        "receipt": apply_receipt,
        "rollback_receipt": rollback_receipt,
        "graph_delta_counts": {"node": 0, "assertion": 0, "evidence_item": 0, "total_delta": 0},
    }


def _resolve_rollback_config_path(rollback_data: dict[str, Any]) -> Path:
    from ..parameters import ParameterRegistry
    config_path_str = rollback_data.get("target_config_path")
    default_config = ParameterRegistry().config_path
    if not config_path_str:
        return default_config
    candidate = Path(config_path_str)
    return candidate if candidate.is_file() else default_config


def _update_applied_receipt_status(rb_path: Path | None, proposal_id: str | None, executed_at: str) -> None:
    if not rb_path or not proposal_id:
        return
    applied_file = rb_path.parent / f"{proposal_id}_applied_receipt.json"
    if applied_file.is_file():
        applied_data = read_json(applied_file)
        applied_data["status"] = "ROLLED_BACK"
        applied_data["rolled_back_at"] = executed_at
        write_json(applied_file, applied_data)


def rollback_learning_parameters(
    gateway: Any,
    rollback_target: dict[str, Any] | str | Path,
) -> dict[str, Any]:
    """Executes exact bidirectional rollback with absent-key pruning."""
    from ..parameters import ParameterRegistry, reload_parameters

    if isinstance(rollback_target, (str, Path)):
        rb_path = Path(rollback_target)
        rollback_data = read_json(rb_path)
    elif isinstance(rollback_target, dict):
        rb_path = None
        rollback_data = rollback_target
    else:
        raise ValueError("Rollback target must be dict or file path")

    restore_params = rollback_data.get("restore_parameters", {})
    absent_params = rollback_data.get("absent_parameters", [])
    config_path = _resolve_rollback_config_path(rollback_data)

    target_reg = ParameterRegistry(config_path=config_path)
    for param_name, val in restore_params.items():
        if val is not None:
            target_reg.set(param_name, val, persist=True, config_path=config_path)

    for param_name in absent_params:
        target_reg.unset(param_name, persist=True, config_path=config_path)

    reload_parameters()

    executed_at = utc_now()
    rollback_data["status"] = "EXECUTED"
    rollback_data["executed_at"] = executed_at
    if rb_path and rb_path.is_file():
        write_json(rb_path, rollback_data)

    _update_applied_receipt_status(rb_path, rollback_data.get("proposal_id"), executed_at)

    return {
        "status": "ROLLED_BACK",
        "rollback_id": rollback_data.get("receipt_id"),
        "restored_parameters": restore_params,
        "pruned_parameters": absent_params,
    }
