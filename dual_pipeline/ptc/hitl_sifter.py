"""
dual_pipeline/ptc/hitl_sifter.py

Calibrated HITL Governance: Programmatic Ambiguity Sifter & Queue Manager (PASQM).
Enforces:
- In-memory ambiguity sifting against empirical priors from hitl_decision_ledger.json
- 20% margin rule: Delta = c_top - c_second > 0.20 -> autonomous resolution
- Atomic single-item popping sorted by criticality (P0 > P1 > P2)
- Dynamic post-action prior calibration (+12% boost) and queue re-sifting
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from dual_pipeline.core import digest, utc_now
from dual_pipeline.hitl_governance import DEFAULT_MARGIN_THRESHOLD

CRITICALITY_PRIORITY_ORDER = {
    "P0": 0,
    "TIER_0": 0,
    "CRITICAL": 0,
    "P1": 1,
    "TIER_1": 1,
    "HIGH": 1,
    "P2": 2,
    "TIER_2": 2,
    "MEDIUM": 2,
    "P3": 3,
    "TIER_3": 3,
    "LOW": 3,
}


@dataclass
class PTCHITLOption:
    """Discrete, mutually exclusive option evaluated within an ambiguous decision."""
    option_id: str
    title: str
    raw_confidence: float = 0.50
    calibrated_confidence: float = 0.0
    evidence_basis: str = ""
    risk_assessment: str = ""
    pattern_key: str = ""  # Key used to correlate with historical decision ledger priors


@dataclass
class PTCHITLCandidate:
    """Candidate disambiguation or decision item pending evaluation."""
    candidate_id: str
    question: str
    context: str = ""
    criticality: str = "P1"
    options: List[PTCHITLOption] = field(default_factory=list)
    category: str = "entity_disambiguation"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PTCSiftResult:
    """Result of programmatic ambiguity sifting across candidate roster."""
    auto_resolved: List[Dict[str, Any]]
    ambiguous_queue: List[Dict[str, Any]]
    total_candidates: int
    auto_resolved_count: int
    ambiguous_count: int
    sift_digest: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _calculate_sift_delta(sorted_opts: Sequence[PTCHITLOption]) -> tuple[Optional[PTCHITLOption], float]:
    if len(sorted_opts) >= 2:
        top_opt = sorted_opts[0]
        second_opt = sorted_opts[1]
        delta = top_opt.calibrated_confidence - second_opt.calibrated_confidence
    elif len(sorted_opts) == 1:
        top_opt = sorted_opts[0]
        delta = 1.0
    else:
        top_opt = None
        delta = 0.0
    return top_opt, round(delta, 4)


def _format_single_sift_option(i: int, opt: PTCHITLOption) -> Dict[str, Any]:
    title_prefix = "(Recommended) " if i == 0 else ""
    conf_pct = int(round(opt.calibrated_confidence * 100))
    if conf_pct >= 85:
        conf_tier = "HIGH"
    elif conf_pct >= 50:
        conf_tier = "MEDIUM"
    else:
        conf_tier = "LOW"
    return {
        "option_id": opt.option_id,
        "title": f"{title_prefix}{opt.title}",
        "confidence": f"{conf_tier} ({conf_pct}%)",
        "raw_score": opt.raw_confidence,
        "calibrated_score": opt.calibrated_confidence,
        "evidence_basis": opt.evidence_basis,
        "risk_assessment": opt.risk_assessment,
        "pattern_key": opt.pattern_key,
    }


def _format_sift_options(sorted_opts: Sequence[PTCHITLOption]) -> List[Dict[str, Any]]:
    return [_format_single_sift_option(i, opt) for i, opt in enumerate(sorted_opts)]


class PTCAmbiguitySifter:
    """Evaluates candidate decisions against Bayesian priors and separates by margin."""

    def __init__(self, priors_map: Optional[Mapping[str, float]] = None) -> None:
        self.priors: Dict[str, float] = {k.lower(): float(v) for k, v in (priors_map or {}).items()}

    def compute_calibrated_confidence(
        self, option: PTCHITLOption, priors: Optional[Mapping[str, float]] = None
    ) -> float:
        """Apply empirical historical prior to option's raw confidence score."""
        active_priors = priors if priors is not None else self.priors
        key = (option.pattern_key or option.option_id).lower()
        prior = active_priors.get(key, 1.0)
        calibrated = min(1.0, max(0.01, option.raw_confidence * prior))
        option.calibrated_confidence = round(calibrated, 4)
        return option.calibrated_confidence

    def sift(
        self,
        candidates: Sequence[PTCHITLCandidate],
        priors_map: Optional[Mapping[str, float]] = None,
        margin_threshold: float = DEFAULT_MARGIN_THRESHOLD,
    ) -> PTCSiftResult:
        """Partition candidates into auto-resolved (Delta >= threshold) and ambiguous queue."""
        active_priors = priors_map if priors_map is not None else self.priors
        auto_resolved: List[Dict[str, Any]] = []
        ambiguous_queue: List[Dict[str, Any]] = []

        for candidate in candidates:
            # 1. Calibrate options
            for opt in candidate.options:
                self.compute_calibrated_confidence(opt, active_priors)

            # 2. Sort options descending by calibrated confidence
            sorted_opts = sorted(candidate.options, key=lambda o: o.calibrated_confidence, reverse=True)
            top_opt, delta = _calculate_sift_delta(sorted_opts)

            # 3. Partition by margin gate
            if delta > margin_threshold and top_opt is not None:
                auto_resolved.append({
                    "candidate_id": candidate.candidate_id,
                    "question": candidate.question,
                    "winner_option_id": top_opt.option_id,
                    "winner_title": top_opt.title,
                    "confidence": top_opt.calibrated_confidence,
                    "confidence_margin_delta": delta,
                    "criticality": candidate.criticality,
                    "resolved_at": utc_now(),
                    "reason": f"AUTONOMOUS_RESOLUTION: Margin Delta {delta:.4f} > {margin_threshold}",
                })
            else:
                formatted_options = _format_sift_options(sorted_opts)
                crit_order = CRITICALITY_PRIORITY_ORDER.get(candidate.criticality.upper(), 2)
                ambiguous_queue.append({
                    "candidate_id": candidate.candidate_id,
                    "question": candidate.question,
                    "context": candidate.context,
                    "criticality": candidate.criticality,
                    "criticality_order": crit_order,
                    "confidence_margin_delta": delta,
                    "options": formatted_options,
                    "metadata": candidate.metadata,
                })

        # 4. Sort ambiguous queue: Criticality (P0 > P1 > P2), then lowest margin delta
        ambiguous_queue.sort(key=lambda item: (item["criticality_order"], item["confidence_margin_delta"]))

        sift_payload = {
            "auto_resolved_count": len(auto_resolved),
            "ambiguous_count": len(ambiguous_queue),
            "total_candidates": len(candidates),
            "auto_resolved_ids": [r["candidate_id"] for r in auto_resolved],
            "ambiguous_ids": [q["candidate_id"] for q in ambiguous_queue],
        }
        sift_digest = f"sha256:{digest(sift_payload)}"

        return PTCSiftResult(
            auto_resolved=auto_resolved,
            ambiguous_queue=ambiguous_queue,
            total_candidates=len(candidates),
            auto_resolved_count=len(auto_resolved),
            ambiguous_count=len(ambiguous_queue),
            sift_digest=sift_digest,
        )


class PTCHITLQueueManager:
    """Governed in-memory queue manager enforcing atomic popping and dynamic re-sifting."""

    def __init__(
        self,
        sifter: Optional[PTCAmbiguitySifter] = None,
        margin_threshold: float = DEFAULT_MARGIN_THRESHOLD,
    ) -> None:
        self.sifter = sifter or PTCAmbiguitySifter()
        self.margin_threshold = margin_threshold
        self._raw_candidates: Dict[str, PTCHITLCandidate] = {}
        self._auto_resolved: List[Dict[str, Any]] = []
        self._ambiguous_queue: List[Dict[str, Any]] = []
        self._resolution_history: List[Dict[str, Any]] = []

    def load_candidates(
        self,
        candidates: Sequence[PTCHITLCandidate],
        priors: Optional[Mapping[str, float]] = None,
    ) -> PTCSiftResult:
        """Load candidates, index them, and execute initial ambiguity sifting."""
        if priors:
            self.sifter.priors.update({k.lower(): float(v) for k, v in priors.items()})

        self._raw_candidates = {c.candidate_id: c for c in candidates}
        result = self.sifter.sift(candidates, margin_threshold=self.margin_threshold)

        self._auto_resolved = list(result.auto_resolved)
        self._ambiguous_queue = list(result.ambiguous_queue)
        return result

    def pop_next_atomic(self) -> Optional[Dict[str, Any]]:
        """Pop strictly ONE atomic candidate from head of ambiguous queue."""
        if not self._ambiguous_queue:
            return None
        return self._ambiguous_queue.pop(0)

    def record_operator_resolution(
        self,
        candidate_id: str,
        selected_option_id: str,
        boost_factor: float = 1.12,
        penalty_factor: float = 0.90,
    ) -> Dict[str, Any]:
        """Record operator's choice, recalibrate priors, and re-sift remaining queued items."""
        candidate = self._raw_candidates.get(candidate_id)
        if not candidate:
            raise KeyError(f"Candidate '{candidate_id}' not found in queue manager.")

        # Recalibrate priors: boost selected pattern, penalize rejected patterns
        selected_key = ""
        for opt in candidate.options:
            key = (opt.pattern_key or opt.option_id).lower()
            if opt.option_id == selected_option_id:
                selected_key = key
                current = self.sifter.priors.get(key, 1.0)
                self.sifter.priors[key] = round(current * boost_factor, 4)
            else:
                rej_key = key
                rej_current = self.sifter.priors.get(rej_key, 1.0)
                self.sifter.priors[rej_key] = round(rej_current * penalty_factor, 4)

        resolution_entry = {
            "candidate_id": candidate_id,
            "selected_option_id": selected_option_id,
            "affirmed_pattern_key": selected_key,
            "new_prior": self.sifter.priors.get(selected_key, 1.0),
            "resolved_at": utc_now(),
        }
        self._resolution_history.append(resolution_entry)

        # Remove resolved candidate from candidate registry
        self._raw_candidates.pop(candidate_id, None)

        # Re-sift remaining ambiguous candidates with updated priors!
        remaining_candidate_ids = {q["candidate_id"] for q in self._ambiguous_queue if q["candidate_id"] != candidate_id}
        remaining_candidates = [self._raw_candidates[cid] for cid in remaining_candidate_ids if cid in self._raw_candidates]

        promoted_count = 0
        if remaining_candidates:
            resift_result = self.sifter.sift(remaining_candidates, margin_threshold=self.margin_threshold)
            # Add newly auto-resolved items
            for item in resift_result.auto_resolved:
                self._auto_resolved.append(item)
                self._raw_candidates.pop(item["candidate_id"], None)
                promoted_count += 1
            # Update ambiguous queue
            self._ambiguous_queue = list(resift_result.ambiguous_queue)
        else:
            self._ambiguous_queue = []

        return {
            "resolution": resolution_entry,
            "promoted_by_recalibration": promoted_count,
            "remaining_ambiguous_count": len(self._ambiguous_queue),
            "auto_resolved_total": len(self._auto_resolved),
        }

    def get_auto_resolved(self) -> List[Dict[str, Any]]:
        return list(self._auto_resolved)

    def get_remaining_ambiguous_count(self) -> int:
        return len(self._ambiguous_queue)
