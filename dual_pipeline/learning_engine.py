"""
dual_pipeline/learning_engine.py

Layer 5 Cross-Pipeline Closed-Loop Learning Engine (CLLE).
Analyzes failure cohorts across telemetry, QA gates, and HITL ledgers.
Synthesizes contract-valid learning-change-proposal:1.0.0 packages with signed provenance,
simulates dry-runs with zero graph mutation guarantees, and enforces cryptographic approval receipts.
"""
from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from dual_pipeline.contracts import create_artifact, validate_artifact
from dual_pipeline.core import ROOT, read_json, stable_id, write_json
from dual_pipeline.hitl_governance import resolve_hitl_ledger_path, validate_approval_origin
from dual_pipeline.learning_engine_core.aggregation import (
    aggregate_cohort_proposals, analyze_audio_voice_cohorts_impl, build_healing_proposal_spec, build_retrieval_weight_proposal_spec,
    extract_tokenomics_signals, ingest_and_drain_healing_store, record_proposal_sqlite_status_impl, run_offline_healing_learning_cycle,
    run_unified_learning_cycle_impl, scan_reconciler_outcomes_impl,
)
from dual_pipeline.learning_engine_core.hitl_specs import build_hitl_confidence_spec, calculate_hitl_brier_scores, parse_hitl_decision_record
from dual_pipeline.learning_engine_core.provenance import PROVENANCE_EXTENSION_KEY, build_signed_provenance, verify_proposal_provenance
from dual_pipeline.learning_engine_core.rollback import execute_rollback, reconcile_cross_agent_learning_fleet_impl, record_rollback_snapshot, snapshot_database_digests, verify_post_apply_canary_and_seal, verify_zero_graph_mutation
from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold
from dual_pipeline.transcript_qa_core.alias_registry import register_learned_alias
from storage.connection import get_connection

logger = logging.getLogger(__name__)

DEFAULT_PROPOSALS_DIR = ROOT / "reports" / "learning_proposals"
DEFAULT_RUNTIME_ROOT = ROOT / ".local"


@dataclass
class SimulationResult:
    proposal_id: str
    success: bool
    status: str
    details: Dict[str, Any]
    simulated_at: str

    @property
    def metrics(self) -> Dict[str, Any]:
        return self.details

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id, "success": self.success, "status": self.status,
            "details": self.details, "simulated_at": self.simulated_at,
        }


def _outcome_cohort_key(item: Dict[str, Any]) -> str:
    det = item.get("details") if isinstance(item.get("details"), Mapping) else {}
    f_id = item.get("from_id") or det.get("from_id")
    t_id = item.get("to_id") or det.get("to_id")
    base = f"outcome:{item.get('outcome_type')}:{item.get('category')}"
    return f"{base}:{f_id}->{t_id}" if (f_id and t_id) else base


def _derive_cohort_key(item: Dict[str, Any]) -> str:
    src = item.get("source", "generic")
    if src == "agent_outcome":
        return _outcome_cohort_key(item)
    if src == "telemetry":
        return f"telemetry:{item.get('stage')}:{item.get('event_type')}"
    if src == "hitl":
        cat = item.get("category")
        return f"hitl_override:{cat}" if item.get("outcome_type") == "OVERRIDDEN" else f"hitl:{cat}:{item.get('key')}"
    if src == "qa_report":
        msg = item.get("failure_message", "").lower()
        if "generic speaker" in msg:
            return "qa:speaker_disambiguation:generic_speaker"
        if "alias" in msg or "entity" in msg:
            return "qa:entity_resolution:alias_mismatch"
        return f"qa:{item.get('gate')}:{msg[:32]}"
    if src in ("healing_repair", "healing_escalation"):
        return f"{src}:{item.get('subsystem')}:{item.get('repair_class')}:{item.get('scope')}"
    if src == "tokenomics":
        return f"tokenomics:{item.get('signal_type', 'cost')}:{item.get('category', 'general')}"
    return f"generic:{item.get('run_id', 'unknown')}"


def _resolve_alias_spec(ckey: str, cohort: Dict[str, Any], t_over: Dict[str, Any]) -> Tuple[str, str, List[str], str, Dict[str, Any]]:
    items = cohort.get("items") or [{}]
    first_det = items[0].get("details") or {}
    first_corr = items[0].get("expected_or_corrected") or {}
    f_val = t_over.get("from_id") or first_det.get("from_id") or first_det.get("candidate_id") or first_det.get("assigned_speaker") or "system:unmapped"
    t_val = t_over.get("to_id") or first_det.get("to_id") or first_corr.get("corrected_canonical") or first_corr.get("confirmed_speaker") or "system:canonical"
    o1_imm = bool(t_over.get("output_01_immutable_verified", first_det.get("output_01_immutable_verified", True)))
    problem = f"Recurring ambiguity detected ({cohort['occurrence_count']} occurrences)."
    proposed_change = t_over.get("proposed_change", f"Register dynamic alias mapping '{f_val}' -> '{t_val}' for cohort {ckey}.")
    eval_plan = ["Dry-run simulation against historical failure items", "Verify zero false positive linking"]
    ext = {"com.partner_advisory.nee.learning": {**t_over, "cohort_key": ckey, "target_type": "learned_aliases", "from_id": f_val, "to_id": t_val, "output_01_immutable_verified": o1_imm}}
    return problem, proposed_change, eval_plan, "low", ext


def _infer_proposal_type(ckey: str) -> str:
    if ckey.startswith("healing_escalation:") or "hitl_override" in ckey or "hitl_confidence" in ckey or "decision_override" in ckey:
        return "hitl_confidence"
    if ckey.startswith("healing_repair:"):
        return "workflow"
    if any(k in ckey for k in ("alias", "entity", "speaker", "adjudication", "maintenance")):
        return "alias_addition"
    if "retrieval" in ckey or "coverage_gap" in ckey:
        return "query_retrieval"
    if "threshold" in ckey or "margin" in ckey:
        return "threshold"
    if "quality" in ckey:
        return "workflow"
    return "routing"


class ClosedLoopLearningEngine:
    """Orchestrates cross-pipeline closed-loop learning from telemetry to release."""

    def __init__(self, runtime_root: Optional[Path] = None, proposals_dir: Optional[Path] = None, *, storage_dir: Optional[Path] = None) -> None:
        self.runtime_root = (storage_dir or runtime_root or DEFAULT_RUNTIME_ROOT).resolve()
        self.proposals_dir = (proposals_dir or (storage_dir / "proposals" if storage_dir else DEFAULT_PROPOSALS_DIR)).resolve()
        self.proposals_dir.mkdir(parents=True, exist_ok=True)

    def ingest_telemetry_failures(self, ledger_path: Optional[Path] = None) -> List[Dict[str, Any]]:
        path = ledger_path or (self.runtime_root / "ledger" / "learning.sqlite")
        if not path.exists():
            return []
        failures = []
        try:
            with get_connection(path, readonly=True) as conn:
                cursor = conn.execute(
                    "SELECT event_id, run_id, stage, event_type, status, artifact_json, created_at "
                    "FROM learning_event WHERE status NOT IN ('PASS', 'OK') ORDER BY created_at ASC"
                )
                for r in cursor.fetchall():
                    failures.append({
                        "source": "telemetry", "event_id": r["event_id"], "run_id": r["run_id"],
                        "stage": r["stage"], "event_type": r["event_type"], "status": r["status"],
                        "created_at": r["created_at"],
                        "details": json.loads(r["artifact_json"]) if r["artifact_json"] else {},
                    })
        except Exception as e:
            logger.warning("Telemetry failure query failed: %s", e)
        return failures

    def ingest_hitl_decisions(self, hitl_ledger_path: Optional[Path] = None) -> List[Dict[str, Any]]:
        local_hitl = self.runtime_root / "ledger" / "hitl_decision_ledger.json"
        path = hitl_ledger_path or (local_hitl if local_hitl.exists() else resolve_hitl_ledger_path())
        sqlite_p = path if path.suffix == ".sqlite" else path.with_suffix(".sqlite")
        if sqlite_p.is_file():
            try:
                from dual_pipeline.storage.hitl_sqlite_store import HITLSQLiteStore
                records = HITLSQLiteStore(db_path=sqlite_p).get_history()
                if records:
                    return [p for r in records if (p := parse_hitl_decision_record(r))]
            except Exception as e:
                logger.warning("HITL SQLite ingest note: %s; falling back to JSON", e)
        if not path.exists():
            return []
        try:
            data = read_json(path)
            records = data if isinstance(data, list) else (data.get("operator_decisions") or data.get("decisions") or [])
            return [p for r in records if (p := parse_hitl_decision_record(r))]
        except Exception as e:
            logger.warning("HITL ingest failed: %s", e)
            return []

    def ingest_qa_failures(self, qa_reports: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        failures = []
        for report in qa_reports:
            run_id = report.get("run_id", "run-qa-gate")
            created_at = report.get("evaluated_at", datetime.now(timezone.utc).isoformat())
            for gate in ("gate_1", "gate_2", "gate_3"):
                gate_data = report.get(gate, {})
                if isinstance(gate_data, dict) and not gate_data.get("passed", True):
                    for fail_msg in gate_data.get("failures", []):
                        failures.append({
                            "source": "qa_report", "run_id": run_id, "gate": gate,
                            "failure_message": fail_msg, "created_at": created_at,
                        })
        return failures

    def ingest_tokenomics_signals(self, judge_reports: Sequence[Mapping[str, Any]] = (), healer_results: Sequence[Mapping[str, Any]] = (), circuit_breaker_trips: Sequence[Mapping[str, Any]] = (), cache_metrics: Sequence[Mapping[str, Any]] = ()) -> List[Dict[str, Any]]:
        """Ingest Judge defect codes, Healer repairs, CircuitBreaker trips, and low-cache cohorts."""
        return extract_tokenomics_signals(judge_reports=judge_reports, healer_results=healer_results, circuit_breaker_trips=circuit_breaker_trips, cache_metrics=cache_metrics, now_iso=datetime.now(timezone.utc).isoformat())

    def analyze_failure_cohorts(self, failures: Sequence[Dict[str, Any]], min_occurrences: int = 2) -> List[Dict[str, Any]]:
        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for item in failures:
            buckets.setdefault(_derive_cohort_key(item), []).append(item)
        cohorts = [{"cohort_key": ckey, "occurrence_count": len(items), "run_ids": sorted(list({i.get("run_id", "unknown") for i in items})), "items": items, "latest_timestamp": max(i.get("created_at", "") for i in items)} for ckey, items in buckets.items() if len(items) >= min_occurrences]
        return sorted(cohorts, key=lambda c: c["occurrence_count"], reverse=True)

    def analyze_audio_voice_cohorts(self, events: Sequence[Dict[str, Any]], min_occurrences: int = 3, min_distinct_sources: int = 2) -> List[Dict[str, Any]]:
        return analyze_audio_voice_cohorts_impl(events, min_occurrences=min_occurrences, min_distinct_sources=min_distinct_sources)

    def generate_proposal_for_cohort(self, cohort: Dict[str, Any], proposal_type: Optional[str] = None, target_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ckey = cohort["cohort_key"]
        run_ids = cohort["run_ids"] or ["run-intake-learned"]
        evidence_events = list(dict.fromkeys(stable_id("event", item.get("event_id") or item.get("decision_id") or f"fail-{i}", length=20) for i, item in enumerate(cohort["items"])))
        timestamp = datetime.now(timezone.utc).isoformat()
        proposal_type = proposal_type or _infer_proposal_type(ckey)
        p_id = stable_id("learning-proposal", proposal_type, ckey, length=24)
        t_over = target_override or {}

        if proposal_type == "alias_addition":
            problem, proposed_change, eval_plan, risk, ext = _resolve_alias_spec(ckey, cohort, t_over)
        elif proposal_type == "threshold":
            problem = f"Threshold divergence causing escalations in cohort {ckey}."
            pname, pval = t_over.get("parameter_name", "margin_threshold"), t_over.get("proposed_value", get_hitl_ambiguity_margin_threshold())
            proposed_change = f"Calibrate parameter {pname} to stabilize decision velocity."
            eval_plan = ["Replay ambiguity margin gate on transcripts", "Verify operator cognitive load reduction"]
            risk = "medium"
            ext = {
                "com.partner_advisory.nee.learning": {**t_over, "cohort_key": ckey, "target_type": "parameters", "parameter_name": pname, "proposed_value": pval},
                "com.partner_advisory.nee.calibration": {"target_parameter": pname, "proposed_value": pval, "current_value": t_over.get("current_value", 0.0), "requires_release_gate": True, "auto_mutation_permitted": False},
            }
        elif proposal_type == "hitl_confidence":
            if ckey.startswith("healing_escalation:"):
                proposal_type, problem, proposed_change, eval_plan, risk, ext = build_healing_proposal_spec(ckey, cohort, t_over)
            else:
                problem, proposed_change, eval_plan, risk, ext = build_hitl_confidence_spec(ckey, cohort, t_over)
        elif ckey.startswith("healing_repair:") or proposal_type == "workflow":
            proposal_type, problem, proposed_change, eval_plan, risk, ext = build_healing_proposal_spec(ckey, cohort, t_over)
        elif proposal_type in ("query_retrieval", "retrieval_weight"):
            problem, proposed_change, eval_plan, risk, ext = build_retrieval_weight_proposal_spec(ckey, cohort, t_over)
        else:
            problem, proposed_change = f"Repeated execution failure pattern observed in cohort {ckey}.", f"Adjust routing configuration for {ckey}."
            eval_plan, risk, ext = ["Unit test validation on failure cases"], "medium", {"com.partner_advisory.nee.learning": {"cohort_key": ckey, "target_type": "routing"}}

        replay_def = {"evaluator": "ClosedLoopLearningEngine.simulate_proposal", "cohort_key": ckey, "min_occurrences": 2, "simulated_action": proposed_change}
        ext[PROVENANCE_EXTENSION_KEY] = build_signed_provenance(proposal_id=p_id, evidence_event_ids=evidence_events, run_ids=run_ids, proposed_change=proposed_change, replay_definition=replay_def)
        proposal = create_artifact(
            "learning-change-proposal", p_id, proposal_id=p_id, run_ids=run_ids, proposal_type=proposal_type,
            status="proposed", problem=problem, evidence_event_ids=evidence_events, proposed_change=proposed_change,
            evaluation_plan=eval_plan, risk=risk, approval_required=True, auto_apply=False, created_at=timestamp, extensions=ext,
        )
        write_json(self.proposals_dir / f"{p_id}.json", proposal)
        with contextlib.suppress(Exception):
            self._record_proposal_sqlite_status(p_id, "PROPOSED", {"cohort_key": ckey, "proposal_type": proposal_type})
        return proposal

    def aggregate_proposals(self, cohorts: Sequence[Mapping[str, Any]], target_overrides: Optional[Mapping[str, Mapping[str, Any]]] = None, max_proposals: int = 20) -> List[Dict[str, Any]]:
        return aggregate_cohort_proposals(self, cohorts, target_overrides=target_overrides, max_proposals=max_proposals)

    def verify_provenance(self, proposal: Mapping[str, Any]) -> Tuple[bool, str]:
        return verify_proposal_provenance(proposal)

    def _simulate_hitl_confidence(self, p_id: str, ext: Dict[str, Any], now: str) -> SimulationResult:
        from dual_pipeline.learning_engine_core.hitl_specs import simulate_hitl_confidence_core
        ok, status, det = simulate_hitl_confidence_core(self, ext)
        return SimulationResult(p_id, ok, status, det, now)

    @staticmethod
    def _simulate_alias(p_id: str, ext: Dict[str, Any], now: str) -> SimulationResult:
        from dual_pipeline.learning_engine_core.hitl_specs import simulate_alias_core
        ok, status, det = simulate_alias_core(ext)
        return SimulationResult(p_id, ok, status, det, now)

    @staticmethod
    def _simulate_generic_param(p_id: str, ext: Dict[str, Any], now: str) -> SimulationResult:
        import math
        pname, pval = ext.get("parameter_name"), ext.get("proposed_value")
        if pname is None or pval is None:
            return SimulationResult(p_id, False, "SIMULATED_FAIL", {"error": "Missing parameter info"}, now)
        if isinstance(pval, (int, float)) and not isinstance(pval, bool):
            if math.isnan(pval) or math.isinf(pval) or pval < 0.0:
                return SimulationResult(p_id, False, "SIMULATED_FAIL", {"error": f"Parameter '{pname}' value {pval} is negative or non-finite"}, now)
            if str(pname).endswith(("threshold", "margin", "weight", "confidence", "rate")) and float(pval) > 1.0:
                return SimulationResult(p_id, False, "SIMULATED_FAIL", {"error": f"Parameter '{pname}' value {pval} exceeds 1.0 upper bound"}, now)
        return SimulationResult(p_id, True, "SIMULATED_PASS", {"parameter": pname, "simulated_value": pval, "bounds_check": "VALID"}, now)

    @staticmethod
    def _simulate_audio_voice(proposal: Dict[str, Any], p_id: str, now: str) -> SimulationResult:
        from dual_pipeline.system_learning.holdout_replay_evaluator import HoldoutReplayEvaluator
        rep = HoldoutReplayEvaluator().evaluate_audio_voice_proposal(proposal)
        ok = not bool(rep.get("vetoed", True))
        return SimulationResult(p_id, ok, "SIMULATED_PASS" if ok else "VETOED", rep, now)

    def _dispatch_simulation(self, proposal: Dict[str, Any], p_id: str, ext: Dict[str, Any], now: str) -> SimulationResult:
        cat, ttype, ptype = ext.get("category"), ext.get("target_type"), proposal.get("proposal_type")
        ckey = str(ext.get("cohort_key", ""))
        if "audio_voice_recognition" in (cat, ttype) or ckey.startswith("audio_voice_recognition:"):
            return self._simulate_audio_voice(proposal, p_id, now)
        if (cat or ptype) in ("copilot_context", "copilot_grounding", "query_retrieval", "retrieval_weight") or ptype == "retrieval_weight":
            from dual_pipeline.learning_loop_core import simulate_answer_learning_proposal
            r = simulate_answer_learning_proposal(proposal, runtime_root=self.runtime_root)
            return SimulationResult(r.proposal_id, r.success, r.status, r.details, r.simulated_at)
        if cat == "healing_policy" or ptype == "workflow" or ckey.startswith("healing_"):
            from dual_pipeline.learning_loop_core.simulation import simulate_healing_policy_update
            rt_db = self.runtime_root / "ledger" / "learning.sqlite"
            hr = simulate_healing_policy_update(proposal, db_path=rt_db if rt_db.exists() else None)
            return SimulationResult(p_id, bool(hr["success"]), str(hr["status"]), dict(hr["details"]), now)
        if "hitl_confidence" in (cat, ttype, ptype):
            return self._simulate_hitl_confidence(p_id, ext, now)
        if {cat, ttype, ptype} & {"learned_aliases", "alias_addition"}:
            return self._simulate_alias(p_id, ext, now)
        if cat and cat not in ("parameters", "threshold"):
            return SimulationResult(p_id, False, "SIMULATED_FAIL", {"error": f"Unsupported proposal category '{cat}' has no replay simulator (fail-closed)"}, now)
        if {ttype, ptype} & {"parameters", "threshold"}:
            return self._simulate_generic_param(p_id, ext, now)
        if ptype == "routing":
            return SimulationResult(p_id, True, "SIMULATED_PASS", {"route_tested": ext.get("target_route", "default")}, now)
        return SimulationResult(p_id, False, "SIMULATED_FAIL", {"error": f"Unsupported proposal type '{ptype}' has no replay simulator (fail-closed)"}, now)

    def simulate_proposal(self, proposal: Dict[str, Any]) -> SimulationResult:
        p_id, ext, now = proposal.get("proposal_id", "unknown"), proposal.get("extensions", {}).get("com.partner_advisory.nee.learning", {}), datetime.now(timezone.utc).isoformat()
        init_db_hashes = snapshot_database_digests(self.runtime_root)
        res = self._dispatch_simulation(proposal, p_id, ext, now)
        zero_mutation, mutation_errors = verify_zero_graph_mutation(init_db_hashes, snapshot_database_digests(self.runtime_root))
        if not zero_mutation:
            return SimulationResult(p_id, False, "SIMULATED_FAIL", {"error": f"Simulation mutation invariant violated: {mutation_errors}"}, now)
        res.details["zero_mutation_guaranteed"] = True
        receipt_dict = {"receipt_id": stable_id("receipt-sim", p_id, length=24), "proposal_id": p_id, "proposal_digest": proposal.get("canonical_digest", {}).get("value"), "status": res.status, "success": res.success, "simulated_at": res.simulated_at, "details": res.details}
        with contextlib.suppress(Exception):
            write_json(self.proposals_dir / f"{p_id}_simulation_receipt.json", receipt_dict)
            self._record_proposal_sqlite_status(p_id, "VETOED" if res.status == "VETOED" else res.status, res.details)
        return res

    def _record_proposal_sqlite_status(self, proposal_id: str, status: str, details: Optional[Dict[str, Any]] = None) -> None:
        record_proposal_sqlite_status_impl(self.runtime_root, proposal_id, status, details)

    simulate = simulate_proposal
    identify_hitl_override_cohorts = analyze_failure_cohorts

    def simulate_hitl_brier_scores(self, parameter_name: str, proposed_value: float, category: Optional[str] = None) -> Tuple[float, float]:
        decisions = self.ingest_hitl_decisions()
        if category and category != "all":
            decisions = [d for d in decisions if str(d.get("category", "")).strip() == category]
        return calculate_hitl_brier_scores(decisions, parameter_name, proposed_value)

    def verify_proposal_approval(self, proposal: Dict[str, Any], approval_receipt: Optional[Mapping[str, Any]]) -> Tuple[bool, str]:
        if not approval_receipt:
            return False, "Approval receipt is missing."
        from dual_pipeline.calibration import verify_calibration_approval
        if not verify_calibration_approval(proposal, approval_receipt):
            is_valid_origin, origin_reason = validate_approval_origin(approval_receipt)
            if not is_valid_origin:
                return False, f"Rejected non-human approval origin: {origin_reason}"
            return False, "Approval verification failed: invalid signature, digest mismatch, or missing required fields."
        return True, "Approval receipt verified successfully."

    def _verify_apply_preconditions(self, proposal: Dict[str, Any], approval_receipt: Mapping[str, Any], simulation_receipt: Optional[Mapping[str, Any]]) -> None:
        p_id = proposal.get("proposal_id")
        is_valid, reason = self.verify_proposal_approval(proposal, approval_receipt)
        if not is_valid:
            raise PermissionError(f"Cannot apply proposal {p_id}: {reason}")
        is_prov_valid, prov_reason = self.verify_provenance(proposal)
        if not is_prov_valid:
            raise PermissionError(f"Cannot apply proposal {p_id}: Provenance invalid: {prov_reason}")
        applied_file = self.proposals_dir / f"{p_id}_applied_receipt.json"
        if applied_file.is_file() and read_json(applied_file).get("status") == "APPLIED":
            raise PermissionError(f"Cannot apply proposal {p_id}: proposal is already APPLIED (rollback required before re-application)")
        sim_file = self.proposals_dir / f"{p_id}_simulation_receipt.json"
        eff_sim = simulation_receipt or (read_json(sim_file) if sim_file.is_file() else None)
        if isinstance(eff_sim, Mapping) and eff_sim.get("status") not in ("SIMULATED_PASS", "PASS"):
            raise PermissionError(f"Cannot apply proposal {p_id}: simulation receipt status is '{eff_sim.get('status')}'")
        if str(proposal.get("status", "")).upper() == "SIMULATED_FAIL":
            raise PermissionError(f"Cannot apply proposal {p_id}: proposal status is SIMULATED_FAIL")

    def _commit_parameter_proposal(self, proposal: Dict[str, Any], approval_receipt: Mapping[str, Any], parameter_path: Optional[Path], skip_decision_clearance: bool, simulation_receipt: Optional[Mapping[str, Any]], ext: Dict[str, Any], calib_ext: Dict[str, Any]) -> Dict[str, Any]:
        cur_v = ext.get("current_value")
        pre_state: Dict[str, Any] = {"parameter_name": ext.get("parameter_name") or calib_ext.get("target_parameter"), "previous_value": cur_v if cur_v is not None else calib_ext.get("current_value")}
        priors_updates, priors_path = ext.get("priors_updates"), Path(ext["priors_path"]) if ext.get("priors_path") else (self.runtime_root / "learned_speaker_priors.json")
        if isinstance(priors_updates, dict):
            pre_state.update({"priors_path": str(priors_path), "previous_priors_data": read_json(priors_path) if priors_path.is_file() else None})
        record_rollback_snapshot(self.proposals_dir, proposal, pre_state)
        from dual_pipeline.uwg_core.gateway import UniversalWriteGateway
        uwg = UniversalWriteGateway(self.runtime_root, self.runtime_root / "seed_graph.sqlite")
        commit_res = uwg.process_commit_request({"request_type": "learning_parameter_commit", "proposal": proposal, "approval_receipt": approval_receipt, "config_path": parameter_path, "proposals_dir": self.proposals_dir, "repo_root": self.runtime_root, "skip_decision_clearance": skip_decision_clearance, "simulation_receipt": dict(simulation_receipt) if simulation_receipt else None})
        if isinstance(priors_updates, dict):
            existing_priors = read_json(priors_path) if priors_path.is_file() else {}
            write_json(priors_path, {**(existing_priors if isinstance(existing_priors, dict) else {}), **priors_updates})
        with contextlib.suppress(Exception):
            self._record_proposal_sqlite_status(str(proposal.get("proposal_id")), "APPLIED", commit_res.get("receipt"))
            self.synthesize_hitl_option_nuance_and_posteriors(output_store_path=self.runtime_root / "learned_hitl_option_store.json")
        return commit_res["receipt"]

    def apply_approved_proposal(self, proposal: Dict[str, Any], approval_receipt: Mapping[str, Any], aliases_path: Optional[Path] = None, parameter_path: Optional[Path] = None, skip_decision_clearance: bool = False, simulation_receipt: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        self._verify_apply_preconditions(proposal, approval_receipt, simulation_receipt)
        ext, calib_ext = proposal.get("extensions", {}).get("com.partner_advisory.nee.learning", {}), proposal.get("extensions", {}).get("com.partner_advisory.nee.calibration", {})
        ttype, ptype, p_id = ext.get("target_type"), proposal.get("proposal_type"), proposal["proposal_id"]
        pre_mutation_state: Dict[str, Any] = {}
        if ttype in ("learned_aliases", "alias_addition", "speaker_priors") or ptype == "alias_addition":
            pre_mutation_state["from_id"] = ext.get("from_id")
            if aliases_path and aliases_path.is_file():
                pre_mutation_state["previous_target"] = read_json(aliases_path).get("aliases", {}).get(ext.get("from_id"))
            record_rollback_snapshot(self.proposals_dir, proposal, pre_mutation_state)
            register_learned_alias(ext["from_id"], ext["to_id"], aliases_path=aliases_path)
            with contextlib.suppress(Exception):
                from dual_pipeline.transcript_metadata_core.speaker_priors_registry import register_learned_speaker_alias
                register_learned_speaker_alias(ext["from_id"], ext["to_id"], priors_path=Path(ext["priors_path"]) if ext.get("priors_path") else (self.runtime_root / "learned_speaker_priors.json"))
            change_desc = f"Registered alias '{ext['from_id']}' -> '{ext['to_id']}'"
        elif ttype in ("workflow", "routing") or ptype in ("workflow", "routing"):
            record_rollback_snapshot(self.proposals_dir, proposal, pre_mutation_state)
            change_desc = f"Promoted {ptype or ttype} rule for {ext.get('cohort_key', p_id)}"
        elif {ttype, ptype} & {"parameters", "hitl_confidence", "threshold", "retrieval_weight", "copilot_context", "copilot_grounding", "query_retrieval"}:
            return self._commit_parameter_proposal(proposal, approval_receipt, parameter_path, skip_decision_clearance, simulation_receipt, ext, calib_ext)
        else:
            raise ValueError(f"Unsupported proposal type/category '{ptype}/{ttype}' cannot be applied (fail-closed)")
        with contextlib.suppress(Exception):
            self.synthesize_hitl_option_nuance_and_posteriors(output_store_path=self.runtime_root / "learned_hitl_option_store.json")
        receipt = {"receipt_id": stable_id("receipt-learning-apply", p_id, length=24), "proposal_id": p_id, "proposal_digest": proposal.get("canonical_digest", {}).get("value"), "approval_receipt_id": approval_receipt.get("receipt_id"), "applied_by": approval_receipt.get("approved_by"), "applied_at": datetime.now(timezone.utc).isoformat(), "status": "APPLIED", "applied_change": change_desc}
        write_json(self.proposals_dir / f"{p_id}_applied_receipt.json", receipt)
        return receipt

    def rollback_proposal(self, proposal_id: str, aliases_path: Optional[Path] = None, parameter_path: Optional[Path] = None) -> Dict[str, Any]:
        res = execute_rollback(self.proposals_dir, proposal_id, aliases_path=aliases_path, parameter_path=parameter_path)
        with contextlib.suppress(Exception):
            self._record_proposal_sqlite_status(proposal_id, str(res.get("status", "ROLLED_BACK")), res)
        return res

    def evaluate_post_apply_canary(self, proposal: Mapping[str, Any], post_apply_metrics: Mapping[str, Any], **kwargs: Any) -> Dict[str, Any]:
        from dual_pipeline.learning_engine_core.rollback import evaluate_post_apply_canary_impl
        return evaluate_post_apply_canary_impl(self, proposal, post_apply_metrics, **kwargs)

    def synthesize_hitl_option_nuance_and_posteriors(self, hitl_ledger_path: Optional[Path] = None, output_store_path: Optional[Path] = None, healing_db_path: Optional[Path] = None) -> Dict[str, Any]:
        from dual_pipeline.learning_engine_core.option_nuance import synthesize_hitl_option_nuance
        eff_db = healing_db_path or (self.runtime_root / "ledger" / "learning.sqlite")
        return synthesize_hitl_option_nuance(hitl_ledger_path or resolve_hitl_ledger_path(), output_store_path or (ROOT / "config" / "runtime" / "learned_hitl_option_store.json"), healing_db_path=eff_db if eff_db.exists() else None)

    def recalibrate_post_learning_confidences(self, hitl_ledger_path: Optional[Path] = None, healing_db_path: Optional[Path] = None, output_store_path: Optional[Path] = None) -> Dict[str, Any]:
        from dual_pipeline.learning_engine_core.option_nuance import recalibrate_post_learning_confidences_core
        return recalibrate_post_learning_confidences_core(self.runtime_root, self.proposals_dir, hitl_ledger_path, healing_db_path, output_store_path)

    def ingest_healing_decisions(self, db_path: Optional[Path] = None, outcome_ledger_path: Optional[Path] = None) -> List[Dict[str, Any]]:
        return ingest_and_drain_healing_store(self.runtime_root, db_path=db_path, outcome_ledger_path=outcome_ledger_path)

    def run_healing_learning_cycle(self, db_path: Optional[Path] = None, outcome_ledger_path: Optional[Path] = None, min_occurrences: int = 2, generate_proposals: bool = True) -> Dict[str, Any]:
        res = run_offline_healing_learning_cycle(self, db_path=db_path, outcome_ledger_path=outcome_ledger_path, min_occurrences=min_occurrences, generate_proposals=generate_proposals)
        with contextlib.suppress(Exception):
            self.recalibrate_post_learning_confidences(healing_db_path=db_path)
        return res

    def run_audio_voice_learning_cycle(self, events: Optional[Sequence[Dict[str, Any]]] = None, min_occurrences: int = 3, min_distinct_sources: int = 2, target_overrides: Optional[Mapping[str, Mapping[str, Any]]] = None) -> Dict[str, Any]:
        from dual_pipeline.transcript_metadata_core.speaker_priors_registry import load_audio_voice_learning_events
        raw_events = list(events) if events is not None else load_audio_voice_learning_events(self.runtime_root)
        cohorts = self.analyze_audio_voice_cohorts(raw_events, min_occurrences=min_occurrences, min_distinct_sources=min_distinct_sources)
        proposals = [self.generate_proposal_for_cohort(c, proposal_type="threshold", target_override=dict((target_overrides or {}).get(c["cohort_key"]) or {"category": "audio_voice_recognition", "parameter_name": c["cohort_key"].split(":", 1)[-1]})) for c in cohorts]
        return {"events_analyzed": len(raw_events), "cohorts": cohorts, "proposals": proposals, "simulations": [self.simulate_proposal(p).to_dict() for p in proposals]}

    def reconcile_cross_agent_learning_fleet(self, **kwargs: Any) -> Dict[str, Any]:
        return reconcile_cross_agent_learning_fleet_impl(self, **kwargs)

    def scan_reconciler_outcomes(self, events_log_path: Optional[Path] = None, min_occurrences: int = 2) -> List[Dict[str, Any]]:
        return scan_reconciler_outcomes_impl(self, events_log_path=events_log_path, min_occurrences=min_occurrences)

    def run_unified_learning_cycle(self, **kwargs: Any) -> Dict[str, Any]:
        return run_unified_learning_cycle_impl(self, **kwargs)


def get_learning_engine(runtime_root: Optional[Path] = None, proposals_dir: Optional[Path] = None) -> ClosedLoopLearningEngine:
    return ClosedLoopLearningEngine(runtime_root=runtime_root, proposals_dir=proposals_dir)

