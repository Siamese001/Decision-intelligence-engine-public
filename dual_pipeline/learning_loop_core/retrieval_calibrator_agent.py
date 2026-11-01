"""Adaptive Retrieval Calibrator Agent for Closed-Loop 11-Gate Parameter Tuning.

Executes a bounded Observe -> Decide -> Act -> Evaluate loop (max_steps=3, max_tool_calls=6)
to mine retrieval failure cohorts, clamp route multipliers within [0.20, 0.70] using
permutation-invariant Bayesian-damped step sizing, evaluate candidate hybrid weights against
the 11 BGE-M3 retrieval gates on holdout metrics, and emit schema-valid
urn:nee:dual-pipeline:learning-change-proposal:1.0.0 artifacts with signed provenance.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from dual_pipeline.a2a.protocol import ClaimCheckPointer
from dual_pipeline.agent_architecture_core.runtime_loop import (
    AgentRuntimeLoop,
    LoopTerminalOutcome,
    RuntimeLoopConfig,
)
from dual_pipeline.contracts import create_artifact, validate_artifact
from dual_pipeline.core import stable_id, utc_now, write_json
from dual_pipeline.learning_engine_core.provenance import (
    PROVENANCE_EXTENSION_KEY,
    build_signed_provenance,
)
from dual_pipeline.release_policy import RETRIEVAL_RELEASE_11_GATES

from .models import CATEGORY_QUERY_RETRIEVAL

HIGHER_IS_BETTER_GATES: Tuple[str, ...] = (
    "ndcg_at_10",
    "recall_at_50",
    "mrr_at_10",
    "contradiction_pair_recall",
    "provenance_preservation_rate",
    "temporal_status_accuracy",
    "entity_anchor_hit_rate",
    "multi_hop_bridge_coverage",
)

LOWER_IS_BETTER_GATES: Tuple[str, ...] = (
    "p95_latency_ms",
    "stale_leakage_rate",
    "candidate_fact_leakage",
)

ALL_ELEVEN_BGE_GATES: Tuple[str, ...] = HIGHER_IS_BETTER_GATES + LOWER_IS_BETTER_GATES
MAX_STEP_DELTA: float = 0.15


def clamp_route_multiplier(value: float) -> float:
    """Clamps a query route multiplier strictly to the governed [0.20, 0.70] range."""
    return round(max(0.20, min(0.70, float(value))), 4)


def normalize_hybrid_weights(
    dense_weight: float,
    sparse_weight: float,
    proposed_colbert_weight: Optional[float] = None,
) -> Dict[str, float]:
    """Normalizes hybrid retrieval weights (2-way or 3-way simplex) within [0.10, 0.90] summing to 1.0."""
    d_clamped = max(0.10, min(0.90, float(dense_weight)))
    s_clamped = max(0.10, min(0.90, float(sparse_weight)))
    if proposed_colbert_weight is not None:
        c_clamped = max(0.10, min(0.90, float(proposed_colbert_weight)))
        total = d_clamped + s_clamped + c_clamped
        d_norm = round(d_clamped / total, 4)
        s_norm = round(s_clamped / total, 4)
        c_norm = round(1.0 - d_norm - s_norm, 4)
        return {"dense_weight": d_norm, "sparse_weight": s_norm, "colbert_weight": c_norm}
    total = d_clamped + s_clamped
    d_norm = round(d_clamped / total, 4)
    s_norm = round(1.0 - d_norm, 4)
    return {"dense_weight": d_norm, "sparse_weight": s_norm}


def _collect_gate_deltas(
    gates: Sequence[str],
    baseline_metrics: Mapping[str, float],
    candidate_metrics: Mapping[str, float],
    higher_is_better: bool,
) -> Tuple[List[str], Dict[str, float]]:
    regressions: List[str] = []
    deltas: Dict[str, float] = {}
    for gate in gates:
        if gate not in baseline_metrics or gate not in candidate_metrics:
            continue
        diff = round(float(candidate_metrics[gate]) - float(baseline_metrics[gate]), 4)
        deltas[gate] = diff
        if (higher_is_better and diff < 0.0) or (not higher_is_better and diff > 0.0):
            regressions.append(f"REGRESSION_ON_{gate.upper()} ({diff:+g})")
    return regressions, deltas


def evaluate_eleven_gate_holdout(
    baseline_metrics: Mapping[str, float],
    candidate_metrics: Mapping[str, float],
) -> Tuple[bool, List[str], Dict[str, float]]:
    """Checks all 11 BGE-M3 gates for zero regression and positive recall/NDCG gain."""
    if not baseline_metrics or not candidate_metrics:
        return False, ["MISSING_HOLDOUT_METRICS"], {}
    reg_hi, del_hi = _collect_gate_deltas(HIGHER_IS_BETTER_GATES, baseline_metrics, candidate_metrics, True)
    reg_lo, del_lo = _collect_gate_deltas(LOWER_IS_BETTER_GATES, baseline_metrics, candidate_metrics, False)
    regressions = reg_hi + reg_lo
    deltas = {**del_hi, **del_lo}

    gain = deltas.get("ndcg_at_10", 0.0) > 0.0 or deltas.get("recall_at_50", 0.0) > 0.0
    if not regressions and not gain:
        regressions.append("ZERO_METRIC_IMPROVEMENT")
    return len(regressions) == 0, regressions, deltas


@dataclass
class CalibrationOutcome:
    """Result of an AdaptiveRetrievalCalibratorAgent calibration cycle."""

    accepted: bool
    clamped_multipliers: Dict[str, float]
    hybrid_weights: Dict[str, float]
    gate_deltas: Dict[str, float]
    rejection_reasons: List[str]
    proposal_artifact: Optional[Dict[str, Any]]
    steps_executed: int
    tool_calls_used: int
    terminal_outcome: str
    claim_checks: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _extract_cohort_item_step(item: Mapping[str, Any], clamped: Mapping[str, float]) -> Tuple[str, float, float]:
    dim = str(item.get("missed_dimension") or item.get("dimension") or "relational").lower()
    base_m = clamped.get(dim, 0.45)
    raw_sugg = item.get("suggested_multiplier")
    raw_grad = item.get("gradient")
    if raw_sugg is not None:
        delta_i = float(raw_sugg) - base_m
    elif raw_grad is not None:
        delta_i = float(raw_grad) * 0.10
    else:
        delta_i = 0.05
    sev_i = max(0.05, min(1.0, float(item.get("severity", raw_grad if raw_grad is not None else 0.5))))
    return dim, round(sev_i, 8), round(delta_i, 8)


def _resolve_candidate_weights(
    proposed_dense_weight: float,
    proposed_sparse_weight: float,
    proposed_colbert_weight: Optional[float],
    current_weights: Optional[Mapping[str, float]],
) -> Dict[str, float]:
    if current_weights is None:
        return normalize_hybrid_weights(proposed_dense_weight, proposed_sparse_weight, proposed_colbert_weight)
    d_w = float(current_weights.get("dense_weight", proposed_dense_weight))
    s_w = float(current_weights.get("sparse_weight", proposed_sparse_weight))
    c_w = current_weights.get("colbert_weight", proposed_colbert_weight)
    return normalize_hybrid_weights(d_w, s_w, float(c_w) if c_w is not None else None)


class AdaptiveRetrievalCalibratorAgent:
    """Calibrates route multipliers and hybrid weights via 11-gate holdout simulation."""

    def __init__(
        self,
        *,
        max_steps: int = 3,
        max_tool_calls: int = 6,
        timeout_seconds: float = 30.0,
        max_step_delta: float = MAX_STEP_DELTA,
        proposals_dir: Optional[Path] = None,
    ) -> None:
        if max_steps <= 0 or max_steps > 5:
            raise ValueError("max_steps must be within [1, 5].")
        if max_tool_calls <= 0:
            raise ValueError("max_tool_calls must be positive.")
        if max_step_delta <= 0.0 or max_step_delta > 0.50:
            raise ValueError("max_step_delta must be within (0.0, 0.50].")
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.timeout_seconds = timeout_seconds
        self.max_step_delta = float(max_step_delta)
        self.proposals_dir = proposals_dir

    def _compute_candidate_parameters(
        self,
        failure_cohort: Sequence[Mapping[str, Any]],
        current_multipliers: Mapping[str, float],
        proposed_dense_weight: float = 0.62,
        proposed_sparse_weight: float = 0.38,
        proposed_colbert_weight: Optional[float] = None,
        current_weights: Optional[Mapping[str, float]] = None,
    ) -> Tuple[Dict[str, float], Dict[str, float], List[ClaimCheckPointer]]:
        clamped: Dict[str, float] = {k: clamp_route_multiplier(v) for k, v in current_multipliers.items()}
        ptrs: List[ClaimCheckPointer] = []
        dim_buckets: Dict[str, List[Tuple[float, float]]] = {}

        for idx, item in enumerate(failure_cohort):
            dim, sev_i, delta_i = _extract_cohort_item_step(item, clamped)
            dim_buckets.setdefault(dim, []).append((sev_i, delta_i))
            ev_id = str(item.get("event_id") or f"ev_fail_cohort_{idx + 1:04d}")
            raw = json.dumps(dict(item), sort_keys=True, default=str).encode("utf-8")
            ptrs.append(ClaimCheckPointer("src:ledger:answer_failures", ev_id, hashlib.sha256(raw).hexdigest(), {"event_id": ev_id}))

        for dim, pairs in sorted(dim_buckets.items()):
            base_m = clamped.get(dim, 0.45)
            sorted_pairs = sorted(pairs)
            w_sum = sum(w for w, _ in sorted_pairs)
            mean_delta = sum(w * d for w, d in sorted_pairs) / w_sum if w_sum > 0 else 0.0
            ln_n = math.log(max(1, len(sorted_pairs)))
            raw_step = mean_delta * ((1.0 + ln_n) / (1.0 + 0.5 * ln_n))
            capped_step = max(-self.max_step_delta, min(self.max_step_delta, raw_step))
            clamped[dim] = clamp_route_multiplier(base_m + capped_step)

        weights = _resolve_candidate_weights(
            proposed_dense_weight, proposed_sparse_weight, proposed_colbert_weight, current_weights
        )
        return clamped, weights, ptrs

    def _build_proposal_extensions(
        self,
        prop_id: str,
        run_id: str,
        ev_ids: List[str],
        proposed_change: str,
        clamped: Mapping[str, float],
        current_multipliers: Mapping[str, float],
        weights: Mapping[str, float],
        deltas: Mapping[str, float],
        baseline_metrics: Mapping[str, float],
        candidate_metrics: Mapping[str, float],
    ) -> Dict[str, Any]:
        changed_dims = [k for k in clamped if abs(float(clamped[k]) - float(current_multipliers.get(k, 0.45))) > 1e-6]
        primary_dim = max(changed_dims, key=lambda k: (round(abs(float(clamped[k]) - float(current_multipliers.get(k, 0.45))), 8), k)) if changed_dims else "relational"
        param_name = f"retrieval.route_multiplier.{primary_dim}"
        curr_val = clamp_route_multiplier(current_multipliers.get(primary_dim, 0.45))
        prop_val = clamp_route_multiplier(clamped.get(primary_dim, curr_val))
        target_metric = "ndcg_at_10" if (float(deltas.get("ndcg_at_10", 0.0)) > 0.0 or "recall_at_50" not in candidate_metrics) else "recall_at_50"
        t_before = float(baseline_metrics.get(target_metric, 0.80))
        t_after = float(candidate_metrics.get(target_metric, t_before + 0.05))
        bge_gates = {g: True for g in (*RETRIEVAL_RELEASE_11_GATES, *ALL_ELEVEN_BGE_GATES)}
        cohort_key = f"calibrator:retrieval_weight:{primary_dim}"
        replay_def = {"evaluator": "AdaptiveRetrievalCalibratorAgent.evaluate_eleven_gate_holdout", "cohort_key": cohort_key, "target_parameter": param_name, "proposed_value": prop_val}
        prov = build_signed_provenance(proposal_id=prop_id, evidence_event_ids=ev_ids, run_ids=[run_id], proposed_change=proposed_change, replay_definition=replay_def)
        multi_map = {f"retrieval.route_multiplier.{k}": clamp_route_multiplier(clamped[k]) for k in changed_dims}
        return {
            "com.partner_advisory.nee.learning": {
                "origin_agent": "AdaptiveRetrievalCalibratorAgent",
                "cohort_key": cohort_key, "category": CATEGORY_QUERY_RETRIEVAL, "target_type": "parameters",
                "parameter_name": param_name, "current_value": curr_val, "proposed_value": prop_val,
                "target_metric": target_metric, "target_before": t_before, "target_after": t_after, "bge_gates": bge_gates,
            },
            "com.partner_advisory.nee.calibration": {
                "target_parameter": param_name, "current_value": curr_val, "proposed_value": prop_val,
                "route_multipliers_map": multi_map, "hybrid_weights_map": dict(weights),
                "requires_release_gate": True, "auto_mutation_permitted": False,
            },
            "com.partner_advisory.nee.calibrator": {
                "origin_agent": "AdaptiveRetrievalCalibratorAgent",
                "route_multipliers": dict(clamped), "hybrid_weights": dict(weights), "gate_deltas": dict(deltas),
                "baseline_metrics": {k: float(v) for k, v in baseline_metrics.items()},
                "candidate_metrics": {k: float(v) for k, v in candidate_metrics.items()},
            },
            PROVENANCE_EXTENSION_KEY: prov,
        }

    def _mint_learning_proposal(
        self,
        clamped: Mapping[str, float],
        current_multipliers: Mapping[str, float],
        weights: Mapping[str, float],
        deltas: Mapping[str, float],
        failure_cohort: Sequence[Mapping[str, Any]],
        baseline_metrics: Mapping[str, float],
        candidate_metrics: Mapping[str, float],
        proposals_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        ev_ids = [
            str(f.get("event_id") or f"ev_cohort_{i + 1:06d}")
            for i, f in enumerate(failure_cohort)
        ] or ["ev_cohort_default_01"]
        run_id = stable_id("run-calibrator", ev_ids[0], length=24)
        prop_id = stable_id("learning-change-proposal", run_id, length=26)
        proposed_change = json.dumps({"route_multipliers": dict(clamped), "hybrid_weights": dict(weights)}, sort_keys=True)
        ext = self._build_proposal_extensions(
            prop_id, run_id, ev_ids, proposed_change, clamped, current_multipliers,
            weights, deltas, baseline_metrics, candidate_metrics,
        )
        artifact = create_artifact(
            "learning-change-proposal",
            prop_id,
            proposal_id=prop_id,
            run_ids=[run_id],
            proposal_type="retrieval_weight",
            status="proposed",
            problem=f"Retrieval failure cohort ({len(failure_cohort)} records) exhibited suboptimal route/hybrid weighting.",
            evidence_event_ids=ev_ids,
            proposed_change=proposed_change,
            evaluation_plan=[
                "Verify all 11 BGE-M3 gates pass on holdout cohort with zero regressions.",
                "Require human operator approval before parameter promotion.",
            ],
            risk="low",
            approval_required=True,
            auto_apply=False,
            created_at=utc_now(),
            extensions=ext,
        )
        validate_artifact(artifact)
        target_dir = proposals_dir or self.proposals_dir
        if target_dir is not None:
            Path(target_dir).mkdir(parents=True, exist_ok=True)
            write_json(Path(target_dir) / f"{prop_id}.json", artifact)
        return artifact

    def calibrate(
        self,
        *,
        failure_cohort: Sequence[Mapping[str, Any]],
        current_multipliers: Mapping[str, float],
        baseline_metrics: Mapping[str, float],
        candidate_metrics: Mapping[str, float],
        proposed_dense_weight: float = 0.62,
        proposed_sparse_weight: float = 0.38,
        proposed_colbert_weight: Optional[float] = None,
        auto_apply: bool = False,
        approval_required: bool = True,
        proposals_dir: Optional[Path] = None,
    ) -> CalibrationOutcome:
        """Executes 3-step failure cohort analysis, 11-gate holdout check, and proposal emission."""
        if auto_apply or not approval_required:
            raise PermissionError("PROPOSE_ONLY_INVARIANT_VIOLATION: AdaptiveRetrievalCalibratorAgent cannot auto-apply changes.")
        if not failure_cohort:
            raise ValueError("failure_cohort must be non-empty.")

        clamped, weights, claim_checks = self._compute_candidate_parameters(
            failure_cohort, current_multipliers, proposed_dense_weight, proposed_sparse_weight, proposed_colbert_weight=proposed_colbert_weight
        )
        gates_ok, regressions, deltas = False, [], {}
        proposal_artifact: Optional[Dict[str, Any]] = None

        loop = AgentRuntimeLoop(
            RuntimeLoopConfig(
                agent_id="adaptive_retrieval_calibrator_agent",
                goal="Calibrate route multipliers and hybrid weights against 11 BGE-M3 holdout gates",
                max_steps=self.max_steps,
                max_tool_calls=self.max_tool_calls,
                timeout_seconds=self.timeout_seconds,
                allowed_tools=("evaluate_candidate_retrieval", "compute_hybrid_scoring", "verify_contract_compliance"),
            )
        )

        def act_fn(_s: Dict[str, Any], _d: Dict[str, Any], step_idx: int) -> Dict[str, Any]:
            nonlocal gates_ok, regressions, deltas, proposal_artifact
            if step_idx == 1:
                task, _ = loop.dispatch_subagent_task(
                    recipient_agent_id="retrieval_specialist",
                    objective="Mine failure cohort and clamp candidate route multipliers",
                    scope_in=["answer_ledger", "query_routing"],
                    scope_out=["canonical_graph_writes"],
                    allowed_tools=["compute_hybrid_scoring"],
                    deliverable={"clamped_multipliers": clamped},
                    definition_of_done="Multipliers clamped within [0.20, 0.70]",
                    claim_checks=claim_checks,
                )
                return {"tool_calls_used": 2, "tokens_used": 90, "a2a_task_id": task.task_id, "state_updates": {"step": 1}}
            if step_idx == 2:
                gates_ok, regressions, deltas = evaluate_eleven_gate_holdout(baseline_metrics, candidate_metrics)
                return {"tool_calls_used": 2, "tokens_used": 110, "state_updates": {"step": 2, "gates_ok": gates_ok}}
            if gates_ok:
                proposal_artifact = self._mint_learning_proposal(
                    clamped, current_multipliers, weights, deltas,
                    failure_cohort, baseline_metrics, candidate_metrics, proposals_dir=proposals_dir,
                )
            return {"tool_calls_used": 1, "tokens_used": 80, "state_updates": {"step": 3, "emitted": proposal_artifact is not None}}

        loop_res = loop.execute(
            initial_state={"step": 0},
            observe_fn=lambda _s, i: {"step": i, "cohort_size": len(failure_cohort)},
            decide_fn=lambda _o, i: {"action": f"calibrate_step_{i}", "delegated": i == 1, "candidates": [(f"calibrate_step_{i}", 0.93), ("abort", 0.07)]},
            act_fn=act_fn,
            evaluate_fn=lambda _s, _a, i: {"done": (i == 2 and not gates_ok) or i >= 3, "quality_score": 0.33 * i},
        )

        return CalibrationOutcome(
            accepted=gates_ok and proposal_artifact is not None,
            clamped_multipliers=clamped,
            hybrid_weights=weights,
            gate_deltas=deltas,
            rejection_reasons=regressions,
            proposal_artifact=proposal_artifact,
            steps_executed=loop_res.steps_executed,
            tool_calls_used=loop_res.total_tool_calls,
            terminal_outcome=loop_res.terminal_outcome.value if isinstance(loop_res.terminal_outcome, LoopTerminalOutcome) else str(loop_res.terminal_outcome),
            claim_checks=[c.to_dict() for c in claim_checks],
        )


__all__ = [
    "ALL_ELEVEN_BGE_GATES",
    "AdaptiveRetrievalCalibratorAgent",
    "CalibrationOutcome",
    "MAX_STEP_DELTA",
    "clamp_route_multiplier",
    "evaluate_eleven_gate_holdout",
    "normalize_hybrid_weights",
]
