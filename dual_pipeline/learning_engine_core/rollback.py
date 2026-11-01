"""
dual_pipeline/learning_engine_core/rollback.py

Rollback snapshot management and zero-mutation verification during proposal simulation.
Guarantees transactional reversibility of approved learning updates.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from dual_pipeline.core import ROOT, digest, read_json, stable_id, write_json

logger = logging.getLogger(__name__)


_EPHEMERAL_NON_GRAPH_SQLITE_FILES = frozenset({"test_timings.sqlite", "learning.sqlite"})


def snapshot_database_digests(target_dir: Path) -> Dict[str, str]:
    """Computes SHA-256 digests of key SQLite databases in target directory and ledger."""
    digests: Dict[str, str] = {}
    if not target_dir.exists():
        return digests
    db_paths = [
        p for p in (list(target_dir.glob("*.sqlite")) + list((target_dir / "ledger").glob("*.sqlite")))
        if p.name not in _EPHEMERAL_NON_GRAPH_SQLITE_FILES
    ]
    for db_path in db_paths:
        try:
            h = hashlib.sha256()
            with open(db_path, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            digests[str(db_path)] = h.hexdigest()
        except OSError:
            pass
    return digests


def verify_zero_graph_mutation(
    initial_digests: Mapping[str, str],
    current_digests: Mapping[str, str],
) -> Tuple[bool, List[str]]:
    """Asserts that no governed database files were modified or created."""
    mutations: List[str] = []
    for db_path, init_hash in initial_digests.items():
        if Path(db_path).name in _EPHEMERAL_NON_GRAPH_SQLITE_FILES:
            continue
        curr_hash = current_digests.get(db_path)
        if curr_hash != init_hash:
            mutations.append(f"Database modified: {db_path} ({init_hash[:8]} -> {curr_hash[:8] if curr_hash else 'DELETED'})")
    for db_path in current_digests:
        if Path(db_path).name in _EPHEMERAL_NON_GRAPH_SQLITE_FILES:
            continue
        if db_path not in initial_digests:
            mutations.append(f"Unexpected database created during simulation: {db_path}")
    return len(mutations) == 0, mutations


def record_rollback_snapshot(
    proposals_dir: Path,
    proposal: Mapping[str, Any],
    pre_mutation_state: Mapping[str, Any],
) -> Dict[str, Any]:
    """Records an immutable pre-mutation rollback snapshot."""
    p_id = str(proposal.get("proposal_id", "unknown"))
    now = datetime.now(timezone.utc).isoformat()
    snapshot = {
        "snapshot_id": stable_id("snap-rollback", p_id, length=24),
        "proposal_id": p_id,
        "proposal_type": proposal.get("proposal_type"),
        "created_at": now,
        "pre_mutation_state": dict(pre_mutation_state),
        "status": "RECORDED",
    }
    target_path = proposals_dir / f"{p_id}_rollback_snapshot.json"
    write_json(target_path, snapshot)
    return snapshot


def _rollback_alias(pre_state: Mapping[str, Any], aliases_path: Optional[Path]) -> str:
    """Restores previous alias state."""
    from_id = pre_state.get("from_id")
    previous_target = pre_state.get("previous_target")
    if not (from_id and aliases_path and aliases_path.is_file()):
        return "none"
    try:
        data = read_json(aliases_path)
        aliases = data.get("aliases", {})
        if previous_target:
            aliases[from_id] = previous_target
        else:
            aliases.pop(from_id, None)
        data["aliases"] = aliases
        write_json(aliases_path, data)
        return f"Restored alias '{from_id}' -> '{previous_target}'"
    except Exception as err:
        logger.error("Failed to restore alias: %s", err)
        return "none"


def _rollback_parameter(pre_state: Mapping[str, Any], parameter_path: Optional[Path]) -> str:
    """Restores previous parameter configuration with bidirectional absent-key restoration."""
    param_name = pre_state.get("parameter_name")
    if not (param_name and parameter_path and parameter_path.is_file()):
        return "none"
    try:
        config_data = read_json(parameter_path)
        previous_val = pre_state.get("previous_value")
        if isinstance(config_data, dict) and param_name in config_data:
            if previous_val is not None:
                config_data[param_name] = previous_val
                res = f"Restored parameter '{param_name}' to {previous_val}"
            else:
                config_data.pop(param_name, None)
                res = f"Pruned absent parameter '{param_name}'"
            write_json(parameter_path, config_data)
            return res

        from dual_pipeline.parameters import ParameterRegistry, reload_parameters

        reg = ParameterRegistry(config_path=parameter_path)
        if previous_val is not None:
            reg.set(param_name, previous_val, persist=True, config_path=parameter_path)
            res = f"Restored parameter '{param_name}' to {previous_val}"
        else:
            reg.unset(param_name, persist=True, config_path=parameter_path)
            res = f"Pruned absent parameter '{param_name}'"
        reload_parameters()
        return res
    except Exception as err:
        logger.error("Failed to restore parameter: %s", err)
        return "none"


_PARAM_PROPOSAL_TYPES = frozenset({
    "hitl_confidence", "threshold", "retrieval_weight",
    "copilot_context", "copilot_grounding", "query_retrieval", "parameters",
})


def _rollback_from_uwg_receipt(proposals_dir: Path, proposal_id: str, receipt_path: Path) -> Dict[str, Any]:
    rb_data = dict(read_json(receipt_path))
    if rb_data.get("status") in ("ROLLED_BACK", "EXECUTED"):
        rb_data["status"] = "ROLLED_BACK"
        rb_data["idempotent_replay"] = True
        return rb_data
    from dual_pipeline.learning_loop_core.uwg_bridge import rollback_answer_learning_proposal
    uwg_res = rollback_answer_learning_proposal(receipt_path, runtime_root=proposals_dir.parent)
    now = datetime.now(timezone.utc).isoformat()
    receipt = {
        "rollback_receipt_id": rb_data.get("receipt_id") or stable_id("receipt-rollback", proposal_id, length=24),
        "proposal_id": proposal_id, "executed_at": now, "status": "ROLLED_BACK",
        "restored_action": f"Restored parameters {uwg_res.get('restored_parameters', {})}",
        "restored_parameters": uwg_res.get("restored_parameters", {}),
        "pruned_parameters": uwg_res.get("pruned_parameters", []),
    }
    write_json(receipt_path, receipt)
    return receipt


def _restore_priors_from_snapshot(pre_state: Mapping[str, Any]) -> str:
    priors_path_str = pre_state.get("priors_path")
    if not priors_path_str:
        return "none"
    pp = Path(priors_path_str)
    prev_priors = pre_state.get("previous_priors_data")
    if prev_priors is not None:
        write_json(pp, prev_priors)
        return f"Restored speaker priors at {pp.name}"
    if pp.is_file():
        pp.unlink()
        return f"Removed newly created speaker priors at {pp.name}"
    return "none"


def execute_rollback(
    proposals_dir: Path,
    proposal_id: str,
    aliases_path: Optional[Path] = None,
    parameter_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Restores pre-mutation state using the recorded rollback snapshot or UWG rollback receipt."""
    snapshot_path = proposals_dir / f"{proposal_id}_rollback_snapshot.json"
    receipt_path = proposals_dir / f"{proposal_id}_rollback_receipt.json"
    applied_path = proposals_dir / f"{proposal_id}_applied_receipt.json"

    if not snapshot_path.is_file() and receipt_path.is_file():
        return _rollback_from_uwg_receipt(proposals_dir, proposal_id, receipt_path)
    if not snapshot_path.is_file():
        raise FileNotFoundError(f"Rollback snapshot not found for proposal {proposal_id} at {snapshot_path}")

    snapshot = dict(read_json(snapshot_path))
    if snapshot.get("status") == "ROLLED_BACK":
        if receipt_path.is_file():
            cached = dict(read_json(receipt_path))
            cached["status"], cached["idempotent_replay"] = "ROLLED_BACK", True
            return cached
        return {
            "rollback_receipt_id": stable_id("receipt-rollback", proposal_id, length=24),
            "proposal_id": proposal_id, "status": "ROLLED_BACK",
            "restored_action": "already_rolled_back", "snapshot_id": snapshot.get("snapshot_id"),
            "idempotent_replay": True,
        }

    pre_state, ptype = snapshot.get("pre_mutation_state", {}), snapshot.get("proposal_type")
    now = datetime.now(timezone.utc).isoformat()
    restored_action = _restore_priors_from_snapshot(pre_state)
    if ptype == "alias_addition":
        restored_action = _rollback_alias(pre_state, aliases_path)
    elif ptype in _PARAM_PROPOSAL_TYPES:
        if receipt_path.is_file() and read_json(receipt_path).get("status") == "PENDING_ROLLBACK":
            from dual_pipeline.learning_loop_core.uwg_bridge import rollback_answer_learning_proposal
            rollback_answer_learning_proposal(receipt_path, runtime_root=proposals_dir.parent)
        param_res = _rollback_parameter(pre_state, parameter_path)
        if param_res != "none":
            restored_action = param_res

    if applied_path.is_file():
        applied_data = dict(read_json(applied_path))
        applied_data["status"], applied_data["rolled_back_at"] = "ROLLED_BACK", now
        write_json(applied_path, applied_data)

    receipt = {
        "rollback_receipt_id": stable_id("receipt-rollback", proposal_id, length=24),
        "proposal_id": proposal_id, "executed_at": now, "status": "ROLLED_BACK",
        "restored_action": restored_action, "snapshot_id": snapshot.get("snapshot_id"),
    }
    write_json(receipt_path, receipt)
    snapshot["status"], snapshot["rolled_back_at"] = "ROLLED_BACK", now
    write_json(snapshot_path, snapshot)
    return receipt


def _check_canary_sim_receipt(p_id: str, prop_digest: Optional[str], sim_receipt: Optional[Mapping[str, Any]]) -> List[str]:
    if sim_receipt is None:
        return [f"Missing simulation receipt for proposal {p_id}"]
    errs: List[str] = []
    if prop_digest and sim_receipt.get("proposal_digest") != prop_digest:
        errs.append("Simulation receipt proposal_digest mismatch (tampered or stale)")
    if sim_receipt.get("status") not in ("SIMULATED_PASS", "PASS") or not sim_receipt.get("success", False):
        errs.append(f"Simulation receipt status is not SIMULATED_PASS ({sim_receipt.get('status')})")
    return errs


def _check_canary_regressions(
    proposal: Mapping[str, Any],
    post_apply_metrics: Mapping[str, Any],
    sim_receipt: Optional[Mapping[str, Any]],
    bge_gate_results: Optional[Mapping[str, bool]],
) -> List[str]:
    """Evaluates post-application canary metrics for target, guard, latency, or 11-gate regression."""
    p_id = str(proposal.get("proposal_id", "unknown"))
    prop_digest = proposal.get("canonical_digest", {}).get("value")
    regressions = _check_canary_sim_receipt(p_id, prop_digest, sim_receipt)

    sim_details = (sim_receipt or {}).get("details", {}) if isinstance(sim_receipt, Mapping) else {}
    target_before = float(post_apply_metrics.get("target_before", (sim_receipt or {}).get("target_metric_before", sim_details.get("target_metric_before", 0.0))))
    target_after = float(post_apply_metrics.get("target_after", post_apply_metrics.get("target_metric_after", target_before)))
    if target_after < target_before - 1e-9:
        regressions.append(f"Target metric regressed below baseline ({target_after:.4f} < {target_before:.4f})")

    g_b, d_b, s_b = float(post_apply_metrics.get("guard_groundedness_before", 0.90)), float(post_apply_metrics.get("guard_durability_before", 0.85)), float(post_apply_metrics.get("guard_staleness_before", 0.05))
    g_a, d_a, s_a = float(post_apply_metrics.get("guard_groundedness_after", g_b)), float(post_apply_metrics.get("guard_durability_after", d_b)), float(post_apply_metrics.get("guard_staleness_after", s_b))
    if g_a < g_b - 1e-6 or d_a < d_b - 1e-6 or s_a > s_b + 1e-6:
        regressions.append("Post-apply guard metric regression detected")

    latency_ms = float(post_apply_metrics.get("latency_ms", post_apply_metrics.get("simulated_latency_ms", 15.0)))
    budget_ms = float(post_apply_metrics.get("latency_budget_ms", sim_details.get("latency_budget_ms", 250.0)))
    if latency_ms > budget_ms:
        regressions.append(f"Post-apply latency {latency_ms:.1f}ms exceeds budget {budget_ms:.1f}ms")

    ext = proposal.get("extensions", {}).get("com.partner_advisory.nee.learning", {})
    gates = bge_gate_results or post_apply_metrics.get("bge_gates")
    if gates is not None and (ext.get("category") in ("query_retrieval", "retrieval_weight") or str(ext.get("parameter_name", "")).startswith("retrieval.")):
        from dual_pipeline.learning_loop_core.simulation import evaluate_retrieval_11_gates
        ok_11, failed_11 = evaluate_retrieval_11_gates(gates)
        if not ok_11:
            regressions.append(f"Post-apply 11 BGE-M3 gates failed: {failed_11}")
    return regressions


def evaluate_post_apply_canary_impl(
    engine: Any,
    proposal: Mapping[str, Any],
    post_apply_metrics: Mapping[str, Any],
    *,
    aliases_path: Optional[Path] = None,
    parameter_path: Optional[Path] = None,
    bge_gate_results: Optional[Mapping[str, bool]] = None,
    ledger_db_path: Optional[Path] = None,
    run_id: Optional[str] = None,
    parent_span_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Evaluates stage LP5 post-application canary metrics and triggers automatic rollback on regression."""
    from dual_pipeline.learning_loop_core.seal import record_canary_learning_event

    p_id = str(proposal.get("proposal_id", "unknown"))
    r_id = run_id or f"run-learning-{p_id}"
    now = datetime.now(timezone.utc).isoformat()
    sim_path = engine.proposals_dir / f"{p_id}_simulation_receipt.json"
    sim_receipt = read_json(sim_path) if sim_path.is_file() else None

    regressions = _check_canary_regressions(proposal, post_apply_metrics, sim_receipt, bge_gate_results)
    canary_passed = len(regressions) == 0
    rb_receipt = (
        execute_rollback(engine.proposals_dir, p_id, aliases_path=aliases_path, parameter_path=parameter_path)
        if not canary_passed
        else None
    )

    canary_receipt = {
        "receipt_id": stable_id("receipt-canary", p_id, length=24), "proposal_id": p_id,
        "proposal_digest": proposal.get("canonical_digest", {}).get("value"),
        "evaluated_at": now, "canary_passed": canary_passed,
        "status": "CANARY_PASS" if canary_passed else "ROLLED_BACK",
        "rolled_back": not canary_passed, "regressions": regressions,
        "metrics": dict(post_apply_metrics),
        "rollback_receipt_id": rb_receipt.get("rollback_receipt_id") if rb_receipt else None,
    }
    write_json(engine.proposals_dir / f"{p_id}_canary_receipt.json", canary_receipt)
    canary_receipt["lp5_event"] = record_canary_learning_event(
        status="PASS" if canary_passed else "FAIL", run_id=r_id, proposal_id=p_id,
        payload_reference=canary_receipt["receipt_id"], artifact_data=canary_receipt,
        db_path=ledger_db_path, parent_span_id=parent_span_id,
    )
    return canary_receipt


def rollback_applied_proposal(
    *,
    proposal_id: str,
    previous_parameters: Optional[Mapping[str, Any]] = None,
    reason: str = "canary_regression",
    active_params_path: Optional[Path] = None,
    rollback_ledger_path: Optional[Path] = None,
    proposals_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Restores previous parameters and appends an immutable rollback ledger entry."""
    if proposals_dir is not None and (proposals_dir / f"{proposal_id}_rollback_snapshot.json").is_file():
        return execute_rollback(proposals_dir, proposal_id, parameter_path=active_params_path)
    now = datetime.now(timezone.utc).isoformat()
    restored = dict(previous_parameters or {})
    if active_params_path is not None:
        active_params_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(active_params_path, {"schema_version": "1.0.0", "updated_at": now, "parameters": restored})
    receipt = {
        "rollback_receipt_id": stable_id("receipt-rollback", proposal_id, length=24),
        "proposal_id": proposal_id,
        "status": "ROLLED_BACK",
        "reason": reason,
        "restored_parameters": restored,
        "executed_at": now,
    }
    if rollback_ledger_path is not None:
        rollback_ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with rollback_ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt, sort_keys=True) + "\n")
    return receipt


def verify_post_apply_canary_and_seal(
    engine: Any,
    *,
    proposal_id: str,
    baseline_metrics: Dict[str, float],
    canary_metrics: Dict[str, float],
    previous_parameters: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Verifies LP5 canary metrics, auto-rolls back on regression, or seals LP0-LP5."""
    from dual_pipeline.learning_loop_core.seal import (
        LEARNING_STAGES_WITH_CANARY,
        evaluate_post_apply_canary,
        record_learning_event,
        seal_learning_stage_chain,
    )

    check = evaluate_post_apply_canary(baseline_metrics=baseline_metrics, canary_metrics=canary_metrics)
    run_id = f"run-canary-{proposal_id}"
    db_path = engine.runtime_root / "ledger" / "learning.sqlite"
    if not check["passed"]:
        rb = rollback_applied_proposal(
            proposal_id=proposal_id,
            previous_parameters=previous_parameters,
            reason=f"mrr_delta={check['mrr_delta']}",
            active_params_path=engine.runtime_root / "active_parameters.json",
            rollback_ledger_path=engine.runtime_root / "rollback_ledger.jsonl",
            proposals_dir=engine.proposals_dir,
        )
        return {"status": "ROLLED_BACK", "canary_check": check, "rollback": rb}

    events = [
        record_learning_event(
            stage=stg,
            event_type="learning_canary_evaluated" if stg == "LP5" else f"stage_{stg.lower()}_verified",
            status="PASS",
            run_id=run_id,
            proposal_id=proposal_id,
            payload_reference=proposal_id,
            artifact_data=check,
            db_path=db_path,
        )
        for stg in LEARNING_STAGES_WITH_CANARY
    ]
    seal = seal_learning_stage_chain(
        proposal_id=proposal_id,
        run_id=run_id,
        stage_events=events,
        output_dir=engine.runtime_root / "runs" / run_id,
        require_canary=True,
    )
    return {"status": "SEALED", "canary_check": check, "seal": seal}


def reconcile_cross_agent_learning_fleet_impl(
    engine: Any,
    *,
    hitl_ledger_path: Optional[Path] = None,
    output_receipt_path: Optional[Path] = None,
    min_occurrences: int = 2,
) -> Dict[str, Any]:
    """Correlates HITL override cohorts and runtime outcomes into parameter_learning_proposals.json."""
    hitl_items = engine.ingest_hitl_decisions(hitl_ledger_path=hitl_ledger_path)
    telemetry_items = engine.ingest_telemetry_failures()
    all_signals = hitl_items + telemetry_items
    cohorts = engine.analyze_failure_cohorts(all_signals, min_occurrences=min_occurrences)
    proposals = engine.aggregate_proposals(cohorts)
    override_cohorts = [c for c in cohorts if "override" in str(c.get("cohort_key", "")).lower()]
    receipt = {
        "schema_version": "1.0.0",
        "reconciled_at": datetime.now(timezone.utc).isoformat(),
        "total_signals_evaluated": len(all_signals),
        "cohorts_detected": len(cohorts),
        "override_cohorts_detected": len(override_cohorts),
        "proposals_synthesized": len(proposals),
        "proposal_ids": [p.get("proposal_id") for p in proposals],
        "proposals": proposals,
    }
    target_out = output_receipt_path or (ROOT / "reports" / "parameter_learning_proposals.json")
    target_out.parent.mkdir(parents=True, exist_ok=True)
    write_json(target_out, receipt)
    return receipt
