"""Wave 6: Prospective shadow validation, sampled human adjudication, and tested rollback."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .decision_calibration import DecisionAction
from .ipw_estimator import (
    IPWAdjudicationSample,
    IPWEstimationResult,
    compute_horvitz_thompson_ipw,
)


class RollbackTrigger(str, Enum):
    MODEL_MISMATCH = "MODEL_MISMATCH"
    INDEX_MISMATCH = "INDEX_MISMATCH"
    CALIBRATION_MISMATCH = "CALIBRATION_MISMATCH"
    ERROR_CEILING_BREACH = "ERROR_CEILING_BREACH"
    FALLBACK_BUDGET_EXCEEDED = "FALLBACK_BUDGET_EXCEEDED"
    DRIFT_THRESHOLD_EXCEEDED = "DRIFT_THRESHOLD_EXCEEDED"


class ShadowState(str, Enum):
    SHADOW_ACTIVE = "SHADOW_ACTIVE"
    ROLLED_BACK = "ROLLED_BACK"
    PROMOTED = "PROMOTED"


class StoppingRuleType(str, Enum):
    FIXED_SAMPLE = "FIXED_SAMPLE"
    BOUNDED_UNCERTAINTY = "BOUNDED_UNCERTAINTY"
    EARLY_ERROR_BREACH = "EARLY_ERROR_BREACH"


@dataclass
class RollbackEvent:
    trigger: RollbackTrigger
    timestamp: str
    details: str
    baseline_restored: bool
    receipt_digest: str
    restored_sha256: str = ""


@dataclass
class ShadowQueryRecord:
    query_id: str
    query_text: str
    baseline_decision: DecisionAction
    candidate_decision: DecisionAction
    baseline_score: float
    candidate_score: float
    baseline_top_id: str
    candidate_top_id: str
    is_agreement: bool
    fallback_used: bool
    fallback_type: Optional[str] = None
    sampled_for_human_review: bool = False
    critical_slice: str = "general"


@dataclass
class SamplingPolicy:
    p_autonomous: float = 0.10
    p_escalated: float = 0.30
    seed: int = 42

    def __post_init__(self) -> None:
        if not (0.0 < self.p_autonomous <= 1.0 and 0.0 < self.p_escalated <= 1.0):
            raise ValueError("Sampling probabilities must be in (0, 1]")


@dataclass
class ObservationConfig:
    min_sample_size: int = 50
    max_sample_size: int = 500
    max_allowed_error_rate: float = 0.05
    max_allowed_fallback_rate: float = 0.05
    max_allowed_score_drift: float = 0.10
    min_consumer_durability: float = 0.80
    max_consumer_staleness: float = 0.15
    stopping_rule: StoppingRuleType = StoppingRuleType.BOUNDED_UNCERTAINTY
    critical_slices: Tuple[str, ...] = ("executive", "engineering", "shorthand", "direct_probe")


@dataclass
class DriftMetrics:
    mean_score_baseline: float
    mean_score_candidate: float
    score_drift: float
    route_shift_rate: float
    fallback_rate: float
    drift_breached: bool
    details: str


@dataclass
class ConsumerMetrics:
    query_engine_durability: float = 1.0
    query_engine_staleness: float = 0.0
    live_copilot_durability: float = 1.0
    live_copilot_staleness: float = 0.0


@dataclass
class PromotionEligibility:
    is_eligible: bool
    terminal_outcome: str
    reasons: List[str]
    sample_size: int
    adjudicated_error_rate: float
    fallback_rate: float
    score_drift: float
    slice_coverage: Dict[str, int]
    ipw_details: Optional[IPWEstimationResult] = None
    consumer_metrics: Optional[ConsumerMetrics] = None


class RollbackEngine:
    """Deterministic rollback engine triggered by environment or metric mismatch."""

    def __init__(self) -> None:
        self.state: ShadowState = ShadowState.SHADOW_ACTIVE
        self.events: List[RollbackEvent] = []

    @property
    def is_rolled_back(self) -> bool:
        return self.state == ShadowState.ROLLED_BACK

    def trigger_rollback(
        self, trigger: RollbackTrigger, details: str, timestamp: str = "2026-10-06T21:00:00Z"
    ) -> RollbackEvent:
        self.state = ShadowState.ROLLED_BACK
        digest = hashlib.sha256(f"{trigger.value}:{details}:{timestamp}".encode()).hexdigest()
        event = RollbackEvent(trigger, timestamp, details, True, digest)
        self.events.append(event)
        return event

    def execute_physical_rollback(
        self,
        runtime_root: Path,
        trigger: RollbackTrigger,
        details: str,
        baseline_pair_payload: Optional[Dict[str, Any]] = None,
        timestamp: str = "2026-10-06T21:00:00Z",
    ) -> RollbackEvent:
        event = self.trigger_rollback(trigger, details, timestamp=timestamp)
        active_p = runtime_root / "active_pair.json"
        bak_p = runtime_root / "active_pair.json.bak"
        restored = baseline_pair_payload
        if restored is None and bak_p.exists():
            restored = json.loads(bak_p.read_text(encoding="utf-8"))
        if restored is not None:
            content = json.dumps(restored, indent=2) + "\n"
            active_p.write_text(content, encoding="utf-8")
            event.restored_sha256 = hashlib.sha256(content.encode()).hexdigest()
        return event

    def verify_environment(
        self,
        *,
        expected_model_hash: str,
        current_model_hash: str,
        expected_index_gen: str,
        current_index_gen: str,
        calibration_verified: bool,
    ) -> Optional[RollbackEvent]:
        if current_model_hash != expected_model_hash:
            return self.trigger_rollback(
                RollbackTrigger.MODEL_MISMATCH,
                f"Model mismatch: expected {expected_model_hash[:8]}, got {current_model_hash[:8]}",
            )
        if current_index_gen != expected_index_gen:
            return self.trigger_rollback(
                RollbackTrigger.INDEX_MISMATCH,
                f"Index mismatch: expected {expected_index_gen}, got {current_index_gen}",
            )
        if not calibration_verified:
            return self.trigger_rollback(
                RollbackTrigger.CALIBRATION_MISMATCH, "Calibration drifted from Wave 4 weights."
            )
        return None

    def route_query(
        self, candidate_fn: Callable[[], Any], baseline_fn: Callable[[], Any]
    ) -> Tuple[Any, str]:
        if self.is_rolled_back:
            return baseline_fn(), "BASELINE_ENFORCED_POST_ROLLBACK"
        return candidate_fn(), "CANDIDATE_SHADOW_ACTIVE"


class ShadowEvaluationSession:
    """Manages prospective shadow evaluation, stratified sampling, and drift accounting."""

    def __init__(
        self,
        sampling_policy: Optional[SamplingPolicy] = None,
        config: Optional[ObservationConfig] = None,
        rollback_engine: Optional[RollbackEngine] = None,
        disallowed_query_ids: Optional[Set[str]] = None,
    ) -> None:
        self.policy: SamplingPolicy = sampling_policy or SamplingPolicy()
        self.config: ObservationConfig = config or ObservationConfig()
        self.rollback_engine: RollbackEngine = rollback_engine or RollbackEngine()
        self.disallowed_query_ids: Set[str] = disallowed_query_ids or set()
        self.records: List[ShadowQueryRecord] = []
        self.adjudications: List[IPWAdjudicationSample] = []
        self.consumer_metrics: ConsumerMetrics = ConsumerMetrics()
        self._rng: random.Random = random.Random(self.policy.seed)

    def record_query(
        self,
        *,
        query_id: str,
        query_text: str,
        baseline_decision: DecisionAction,
        candidate_decision: DecisionAction,
        baseline_score: float,
        candidate_score: float,
        baseline_top_id: str,
        candidate_top_id: str,
        fallback_used: bool = False,
        fallback_type: Optional[str] = None,
        critical_slice: str = "general",
    ) -> ShadowQueryRecord:
        if query_id in self.disallowed_query_ids:
            raise ValueError(f"Tuning/holdout query ID '{query_id}' rejected to prevent leakage.")

        is_agree = (baseline_top_id == candidate_top_id) and (
            baseline_decision == candidate_decision
        )
        is_auto = candidate_decision == DecisionAction.AUTONOMOUS
        p_sample = self.policy.p_autonomous if is_auto else self.policy.p_escalated
        sampled = self._rng.random() < p_sample

        rec = ShadowQueryRecord(
            query_id, query_text, baseline_decision, candidate_decision,
            baseline_score, candidate_score, baseline_top_id, candidate_top_id,
            is_agree, fallback_used, fallback_type, sampled, critical_slice,
        )
        self.records.append(rec)
        return rec

    def record_adjudication(
        self,
        *,
        query_id: str,
        is_error: bool,
        human_judgment_grade: Optional[int] = None,
        decision_id: Optional[str] = None,
        require_cleared: bool = True,
    ) -> IPWAdjudicationSample:
        rec = next((r for r in self.records if r.query_id == query_id), None)
        if rec is None:
            raise ValueError(f"Query ID '{query_id}' not found in observed shadow records.")

        is_auto = rec.candidate_decision == DecisionAction.AUTONOMOUS
        p = self.policy.p_autonomous if is_auto else self.policy.p_escalated
        dec_id = decision_id or f"hitl-shadow-adjudication-{query_id}"
        verified = False
        if require_cleared:
            try:
                from dual_pipeline.hitl_governance_core.decision_clearance import is_decision_ratified_in_ledger
                verified = is_decision_ratified_in_ledger(dec_id)
            except Exception:
                verified = False
            if not verified:
                raise ValueError(f"Adjudication for '{query_id}' lacks verified receipt in ledger!")

        sample = IPWAdjudicationSample(
            query_id, rec.candidate_decision, p, 1.0 / p, is_error,
            human_judgment_grade, rec.critical_slice, dec_id, verified or not require_cleared,
        )
        self.adjudications.append(sample)
        return sample

    def compute_ipw_metrics(self) -> IPWEstimationResult:
        return compute_horvitz_thompson_ipw(
            self.adjudications,
            max_allowed_error=self.config.max_allowed_error_rate,
            max_sample_size=self.config.max_sample_size,
        )

    def compute_drift(self) -> DriftMetrics:
        if not self.records:
            return DriftMetrics(0.0, 0.0, 0.0, 0.0, 0.0, False, "No records recorded.")
        n = len(self.records)
        b_sc = [r.baseline_score for r in self.records]
        c_sc = [r.candidate_score for r in self.records]
        mb, mc = sum(b_sc) / n, sum(c_sc) / n
        s_drift = abs(mc - mb)
        r_shifts = sum(1 for r in self.records if r.baseline_decision != r.candidate_decision) / n
        fb_rate = sum(1 for r in self.records if r.fallback_used) / n
        breach = s_drift > self.config.max_allowed_score_drift or fb_rate > self.config.max_allowed_fallback_rate
        msg = f"N={n}, ScoreDrift={s_drift:.4f}, FallbackRate={fb_rate:.4f}, RouteShiftRate={r_shifts:.4f}"
        return DriftMetrics(mb, mc, s_drift, r_shifts, fb_rate, breach, msg)

    def check_stopping_rule(self) -> Tuple[bool, str]:
        n = len(self.records)
        if n >= self.config.max_sample_size:
            return True, f"MAX_SAMPLE_REACHED: {n} >= {self.config.max_sample_size}"
        ipw = self.compute_ipw_metrics()
        if ipw.total_samples >= 10 and ipw.error_ceiling_breached:
            return True, f"EARLY_STOP_ERROR_BREACH: IPW error {ipw.ipw_error_rate:.4f} breaches ceiling."
        if n >= self.config.min_sample_size:
            return True, f"MIN_SAMPLE_WINDOW_SATISFIED: {n} queries recorded."
        return False, f"WINDOW_ACCUMULATING: {n}/{self.config.min_sample_size} queries."

    def _check_promotion_reasons(
        self, drift: DriftMetrics, ipw: IPWEstimationResult, manual_err: Optional[float]
    ) -> Tuple[List[str], float, Dict[str, int]]:
        reasons: List[str] = []
        n = len(self.records)
        if self.rollback_engine.is_rolled_back:
            reasons.append("SYSTEM_ROLLED_BACK: Open rollback triggers prevent promotion.")
        if n < self.config.min_sample_size:
            reasons.append(f"SAMPLE_SIZE_DEFICIT: {n} < {self.config.min_sample_size}")
        if drift.drift_breached:
            reasons.append(f"DRIFT_THRESHOLD_EXCEEDED: {drift.details}")

        eff_err = manual_err if manual_err is not None else ipw.ipw_error_rate
        err_breached = (manual_err > self.config.max_allowed_error_rate) if manual_err is not None else ipw.error_ceiling_breached
        if err_breached:
            reasons.append(f"ERROR_CEILING_BREACH: Error rate {eff_err:.4f} > {self.config.max_allowed_error_rate}")

        if self.consumer_metrics.query_engine_durability < self.config.min_consumer_durability:
            reasons.append(f"CONSUMER_DURABILITY_BREACH: {self.consumer_metrics.query_engine_durability:.4f}")
        if self.consumer_metrics.query_engine_staleness > self.config.max_consumer_staleness:
            reasons.append(f"CONSUMER_STALENESS_BREACH: {self.consumer_metrics.query_engine_staleness:.4f}")

        slices: Dict[str, int] = {}
        for r in self.records:
            slices[r.critical_slice] = slices.get(r.critical_slice, 0) + 1
        for s in self.config.critical_slices:
            if slices.get(s, 0) == 0:
                reasons.append(f"CRITICAL_SLICE_UNCOVERED: Slice '{s}' has 0 observations.")
        return reasons, eff_err, slices

    def evaluate_promotion_eligibility(
        self,
        adjudicated_errors: Optional[int] = None,
        adjudicated_total: Optional[int] = None,
    ) -> PromotionEligibility:
        drift = self.compute_drift()
        ipw = self.compute_ipw_metrics()
        manual_err = None
        if adjudicated_errors is not None and adjudicated_total is not None and not self.adjudications:
            manual_err = (adjudicated_errors / adjudicated_total) if adjudicated_total > 0 else 0.0

        reasons, eff_err, slice_counts = self._check_promotion_reasons(drift, ipw, manual_err)
        is_ok = len(reasons) == 0
        return PromotionEligibility(
            is_eligible=is_ok,
            terminal_outcome="PROCEED_TO_PROMOTION" if is_ok else "RETAIN_BASELINE",
            reasons=reasons,
            sample_size=len(self.records),
            adjudicated_error_rate=eff_err,
            fallback_rate=drift.fallback_rate,
            score_drift=drift.score_drift,
            slice_coverage=slice_counts,
            ipw_details=ipw,
            consumer_metrics=self.consumer_metrics,
        )
