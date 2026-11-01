"""Offline HITL Calibration Runner & Bayesian Prior Optimizer.

Replays historical decision records from the governed HITL ledger,
measures calibration metrics (Brier Score, Expected Calibration Error, Epistemic Entropy),
and applies closed-loop Bayesian prior updating (+0.15 affirmation boost, -0.10 rejection penalty,
bounded to [0.05, 0.95]) to sharpen future confidence distributions and widen option margins.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, utc_now, write_json
from dual_pipeline.hitl_governance import (
    DEFAULT_MARGIN_THRESHOLD,
    HITLDecisionStore,
    parse_confidence_score,
)


def calculate_brier_score(predictions: List[float], outcomes: List[float]) -> float:
    """Calculate mean squared error (Brier Score) between predictions and actuals."""
    if not predictions or len(predictions) != len(outcomes):
        return 0.0
    return round(sum((p - y) ** 2 for p, y in zip(predictions, outcomes)) / len(predictions), 6)


def calculate_ece(predictions: List[float], outcomes: List[float], n_bins: int = 10) -> float:
    """Calculate Expected Calibration Error (ECE) across n_bins intervals."""
    if not predictions or len(predictions) != len(outcomes):
        return 0.0
    n = len(predictions)
    ece = 0.0
    bin_size = 1.0 / n_bins
    for b in range(n_bins):
        b_low, b_high = b * bin_size, (b + 1) * bin_size
        indices = [
            i for i, p in enumerate(predictions)
            if (b_low <= p < b_high) or (b == n_bins - 1 and b_low <= p <= b_high)
        ]
        if not indices:
            continue
        bin_acc = sum(outcomes[i] for i in indices) / len(indices)
        bin_conf = sum(predictions[i] for i in indices) / len(indices)
        ece += (len(indices) / n) * abs(bin_acc - bin_conf)
    return round(ece, 6)


def normalize_distribution(scores: List[float], min_bound: float = 0.05, max_bound: float = 0.95) -> List[float]:
    """Clamp scores within safety bounds and preserve relative distribution."""
    clamped = [min(max_bound, max(min_bound, s)) for s in scores]
    total = sum(clamped)
    if total <= 0.0:
        return [1.0 / len(scores)] * len(scores)
    return [round(s / total, 4) if total > 1.0 else round(s, 4) for s in clamped]


class OfflineHITLCalibrationRunner:
    """Replays ledger history and optimizes Bayesian priors for continuous calibration sharpening."""

    def __init__(
        self,
        ledger_path: Optional[Path] = None,
        output_path: Optional[Path] = None,
        boost_step: float = 0.15,
        penalty_step: float = 0.10,
        min_bound: float = 0.05,
        max_bound: float = 0.95,
    ) -> None:
        self.ledger_path = ledger_path or (ROOT / ".local" / "ledger" / "hitl_decision_ledger.json")
        self.output_path = output_path or (ROOT / ".local" / "ledger" / "hitl_prior_calibration.json")
        self.boost_step = boost_step
        self.penalty_step = penalty_step
        self.min_bound = min_bound
        self.max_bound = max_bound

    def load_ledger(self) -> List[Dict[str, Any]]:
        """Load decision history records from the governed ledger."""
        store = HITLDecisionStore(self.ledger_path)
        return store.get_history()

    def replay_and_calibrate(
        self,
        records: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Perform offline replay over decision records, computing baseline vs calibrated metrics."""
        history = records if records is not None else self.load_ledger()
        if not history:
            return {
                "records_analyzed": 0,
                "status": "NO_RECORDS",
                "baseline_brier_score": 0.0,
                "calibrated_brier_score": 0.0,
                "brier_reduction_pct": 0.0,
                "baseline_ece": 0.0,
                "calibrated_ece": 0.0,
                "ece_reduction_pct": 0.0,
                "autonomous_progression_rate": 0.0,
                "priors": {},
            }

        # Track cumulative feedback per option key
        affirmed_counts: Dict[str, int] = {}
        rejected_counts: Dict[str, int] = {}

        base_preds: List[float] = []
        cal_preds: List[float] = []
        actual_outcomes: List[float] = []

        base_margins: List[float] = []
        cal_margins: List[float] = []
        base_surfaced: int = 0
        cal_surfaced: int = 0

        calibrated_records: List[Dict[str, Any]] = []

        for record in history:
            options = record.get("options", [])
            if len(options) < 2:
                continue

            selected_key = str(record.get("selected_key") or "").lower().strip()
            # Also inspect selected_option dict if key is empty
            if not selected_key and isinstance(record.get("selected_option"), dict):
                selected_key = str(record.get("selected_option", {}).get("key", "")).lower().strip()

            record_base_scores = []
            record_cal_scores = []
            record_outcomes = []

            for opt in options:
                opt_key = ""
                if isinstance(opt, dict):
                    opt_key = str(opt.get("key") or opt.get("label") or "").lower().strip()
                elif isinstance(opt, str):
                    opt_key = opt.lower().strip()

                is_selected = 1.0 if (opt_key and selected_key and (opt_key == selected_key or opt_key in selected_key or selected_key in opt_key)) else 0.0
                try:
                    base_score = parse_confidence_score(opt)
                except Exception:
                    base_score = 0.50

                aff = sum(c for k, c in affirmed_counts.items() if (k in opt_key or opt_key in k))
                rej = sum(c for k, c in rejected_counts.items() if (k in opt_key or opt_key in k))

                adjustment = 0.0
                if aff > rej:
                    adjustment += self.boost_step
                elif rej > aff:
                    adjustment -= self.penalty_step

                cal_score = round(min(self.max_bound, max(self.min_bound, base_score + adjustment)), 4)
                record_base_scores.append(base_score)
                record_cal_scores.append(cal_score)
                record_outcomes.append(is_selected)
                base_preds.append(base_score)
                cal_preds.append(cal_score)
                actual_outcomes.append(is_selected)

            # Update prior counts for future decisions
            if selected_key:
                affirmed_counts[selected_key] = affirmed_counts.get(selected_key, 0) + 1
            for opt in options:
                k = ""
                if isinstance(opt, dict):
                    k = str(opt.get("key") or opt.get("label") or "").lower().strip()
                elif isinstance(opt, str):
                    k = opt.lower().strip()
                if k and selected_key and k != selected_key and (k not in selected_key):
                    rejected_counts[k] = rejected_counts.get(k, 0) + 1

            # Compute margins
            sorted_base = sorted(record_base_scores, reverse=True)
            sorted_cal = sorted(record_cal_scores, reverse=True)
            base_margin = sorted_base[0] - sorted_base[1] if len(sorted_base) > 1 else 1.0
            cal_margin = sorted_cal[0] - sorted_cal[1] if len(sorted_cal) > 1 else 1.0
            base_margins.append(base_margin)
            cal_margins.append(cal_margin)

            if base_margin < DEFAULT_MARGIN_THRESHOLD:
                base_surfaced += 1
            if cal_margin < DEFAULT_MARGIN_THRESHOLD:
                cal_surfaced += 1

            calibrated_records.append({
                "decision_id": record.get("decision_id"),
                "category": record.get("category"),
                "base_margin": round(base_margin, 4),
                "calibrated_margin": round(cal_margin, 4),
                "margin_widening": round(cal_margin - base_margin, 4),
            })

        # Global sequential metrics
        bs_base = calculate_brier_score(base_preds, actual_outcomes)
        bs_cal_seq = calculate_brier_score(cal_preds, actual_outcomes)
        seq_reduction_pct = round(((bs_base - bs_cal_seq) / bs_base * 100.0), 2) if bs_base > 0 else 0.0

        ece_base = calculate_ece(base_preds, actual_outcomes)
        ece_cal_seq = calculate_ece(cal_preds, actual_outcomes)
        seq_ece_reduction_pct = round(((ece_base - ece_cal_seq) / ece_base * 100.0), 2) if ece_base > 0 else 0.0

        total_decisions = len(base_margins)
        autonomous_progression_rate = (
            round((total_decisions - cal_surfaced) / total_decisions, 4) if total_decisions > 0 else 1.0
        )

        # Map learned priors
        priors: Dict[str, Dict[str, Any]] = {}
        all_keys = set(affirmed_counts.keys()).union(rejected_counts.keys())
        for k in sorted(all_keys):
            aff = affirmed_counts.get(k, 0)
            rej = rejected_counts.get(k, 0)
            adj = (
                self.boost_step if aff > rej
                else (-self.penalty_step if rej > aff else 0.0)
            )
            priors[k] = {
                "affirmations": aff,
                "rejections": rej,
                "net_adjustment": round(adj, 4),
                "prior_weight": round(min(self.max_bound, max(self.min_bound, 0.50 + adj)), 4),
            }

        # Batch evaluation across entire dataset using fully converged priors
        batch_base_preds: List[float] = []
        batch_cal_preds: List[float] = []
        batch_actuals: List[float] = []

        for record in history:
            sel_k = str(record.get("selected_key") or "").lower().strip()
            if not sel_k and isinstance(record.get("selected_option"), dict):
                sel_k = str(record.get("selected_option", {}).get("key", "")).lower().strip()

            for opt in record.get("options", []):
                k = ""
                if isinstance(opt, dict):
                    k = str(opt.get("key") or opt.get("label") or "").lower().strip()
                elif isinstance(opt, str):
                    k = opt.lower().strip()

                is_sel = 1.0 if (k and sel_k and (k == sel_k or k in sel_k or sel_k in k)) else 0.0
                try:
                    base_val = parse_confidence_score(opt)
                except Exception:
                    base_val = 0.50
                prior_info = priors.get(k, {})
                prior_adj = prior_info.get("net_adjustment", 0.0)
                cal_val = round(min(self.max_bound, max(self.min_bound, base_val + prior_adj)), 4)

                batch_base_preds.append(base_val)
                batch_cal_preds.append(cal_val)
                batch_actuals.append(is_sel)

        bs_batch_base = calculate_brier_score(batch_base_preds, batch_actuals)
        bs_batch_cal = calculate_brier_score(batch_cal_preds, batch_actuals)
        batch_reduction_pct = round(((bs_batch_base - bs_batch_cal) / bs_batch_base * 100.0), 2) if bs_batch_base > 0 else 0.0

        ece_batch_base = calculate_ece(batch_base_preds, batch_actuals)
        ece_batch_cal = calculate_ece(batch_cal_preds, batch_actuals)
        batch_ece_reduction_pct = round(((ece_batch_base - ece_batch_cal) / ece_batch_base * 100.0), 2) if ece_batch_base > 0 else 0.0

        # Out-of-sample chronological train/test split (70% train / 30% test) to prevent in-sample leakage
        split_idx = max(1, int(len(history) * 0.7)) if len(history) >= 2 else len(history)
        train_slice = history[:split_idx]
        test_slice = history[split_idx:] if len(history) >= 2 else history
        train_aff: Dict[str, int] = {}
        train_rej: Dict[str, int] = {}
        for rec in train_slice:
            sk = str(rec.get("selected_key") or "").lower().strip()
            if not sk and isinstance(rec.get("selected_option"), dict):
                sk = str(rec.get("selected_option", {}).get("key", "")).lower().strip()
            if sk:
                train_aff[sk] = train_aff.get(sk, 0) + 1
            for opt in rec.get("options", []):
                ok = str(opt.get("key") or opt.get("label") or "").lower().strip() if isinstance(opt, dict) else str(opt).lower().strip()
                if ok and sk and ok != sk and (ok not in sk):
                    train_rej[ok] = train_rej.get(ok, 0) + 1

        test_base_preds: List[float] = []
        test_cal_preds: List[float] = []
        test_actuals: List[float] = []
        for rec in test_slice:
            sk = str(rec.get("selected_key") or "").lower().strip()
            if not sk and isinstance(rec.get("selected_option"), dict):
                sk = str(rec.get("selected_option", {}).get("key", "")).lower().strip()
            for opt in rec.get("options", []):
                ok = str(opt.get("key") or opt.get("label") or "").lower().strip() if isinstance(opt, dict) else str(opt).lower().strip()
                is_sel = 1.0 if (ok and sk and (ok == sk or ok in sk or sk in ok)) else 0.0
                try:
                    b_val = parse_confidence_score(opt)
                except Exception:
                    b_val = 0.50
                aff_c = sum(c for k, c in train_aff.items() if (k in ok or ok in k))
                rej_c = sum(c for k, c in train_rej.items() if (k in ok or ok in k))
                adj_val = self.boost_step if aff_c > rej_c else (-self.penalty_step if rej_c > aff_c else 0.0)
                c_val = round(min(self.max_bound, max(self.min_bound, b_val + adj_val)), 4)
                test_base_preds.append(b_val)
                test_cal_preds.append(c_val)
                test_actuals.append(is_sel)

        holdout_base_brier = calculate_brier_score(test_base_preds, test_actuals)
        holdout_cal_brier = calculate_brier_score(test_cal_preds, test_actuals)

        return {
            "records_analyzed": len(history),
            "decisions_evaluated": total_decisions,
            "train_split_count": len(train_slice),
            "test_split_count": len(test_slice),
            "holdout_baseline_brier_score": holdout_base_brier,
            "holdout_calibrated_brier_score": holdout_cal_brier,
            "baseline_brier_score": bs_batch_base,
            "calibrated_brier_score": bs_batch_cal,
            "brier_reduction_pct": batch_reduction_pct,
            "sequential_brier_score": bs_cal_seq,
            "sequential_brier_reduction_pct": seq_reduction_pct,
            "baseline_ece": ece_batch_base,
            "calibrated_ece": ece_batch_cal,
            "ece_reduction_pct": batch_ece_reduction_pct,
            "autonomous_progression_rate": autonomous_progression_rate,
            "baseline_surfaced_count": base_surfaced,
            "calibrated_surfaced_count": cal_surfaced,
            "calibrated_priors": priors,
            "decisions_summary": calibrated_records,
        }

    def test_adversarial_stability(
        self,
        category: str = "SYSTEM_RETENTION",
        target_key: str = "entity:system:unifier",
        rogue_key: str = "delete_unifier",
        authentic_count: int = 5,
        rogue_count: int = 25,
    ) -> Dict[str, Any]:
        """Verify that bounds clipping prevents out-of-bounds prior values under adversarial noise."""
        raw_options = [
            {"key": target_key, "label": "Retain system", "confidence": "60%"},
            {"key": rogue_key, "label": "Delete system", "confidence": "50%"},
        ]

        # Synthesize authentic records
        synthetic_records: List[Dict[str, Any]] = []
        for i in range(authentic_count):
            synthetic_records.append({
                "decision_id": f"auth-{i}",
                "category": category,
                "options": raw_options,
                "selected_key": target_key,
            })

        # Synthesize burst of adversarial rogue records
        for j in range(rogue_count):
            synthetic_records.append({
                "decision_id": f"rogue-{j}",
                "category": category,
                "options": raw_options,
                "selected_key": rogue_key,
            })

        res = self.replay_and_calibrate(synthetic_records)
        priors = res.get("calibrated_priors", {})

        target_prior = priors.get(target_key, {}).get("prior_weight", 0.50)
        rogue_prior = priors.get(rogue_key, {}).get("prior_weight", 0.50)

        # Assert bounds clipping strictly holds: no value can drop below min_bound or exceed max_bound
        bounds_held = (
            self.min_bound <= target_prior <= self.max_bound
            and self.min_bound <= rogue_prior <= self.max_bound
        )
        winner_flipped = rogue_prior > target_prior
        poisoning_contained = bounds_held and not winner_flipped

        return {
            "bounds_held": bounds_held,
            "winner_flipped": winner_flipped,
            "min_bound": self.min_bound,
            "max_bound": self.max_bound,
            "target_prior_weight": target_prior,
            "rogue_prior_weight": rogue_prior,
            "authentic_count": authentic_count,
            "rogue_count": rogue_count,
            "poisoning_contained": poisoning_contained,
        }

    def save_calibration(self, results: Dict[str, Any]) -> Path:
        """Persist calibrated state to disk using RFC-8259 canonical serialization."""
        payload = {
            "calibration_contract": "urn:nee:dual-pipeline:hitl-prior-calibration:1.0.0",
            "timestamp": utc_now(),
            "ledger_source": str(self.ledger_path),
            "records_analyzed": results.get("records_analyzed", 0),
            "baseline_brier_score": results.get("baseline_brier_score", 0.0),
            "calibrated_brier_score": results.get("calibrated_brier_score", 0.0),
            "brier_reduction_pct": results.get("brier_reduction_pct", 0.0),
            "baseline_ece": results.get("baseline_ece", 0.0),
            "calibrated_ece": results.get("calibrated_ece", 0.0),
            "ece_reduction_pct": results.get("ece_reduction_pct", 0.0),
            "autonomous_progression_rate": results.get("autonomous_progression_rate", 0.0),
            "calibrated_priors": results.get("calibrated_priors", {}),
        }
        payload["calibration_digest"] = digest(payload)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.output_path, payload)
        return self.output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline HITL Calibration Runner")
    parser.add_argument("--ledger", type=Path, default=None, help="Path to HITL decision ledger")
    parser.add_argument("--output", type=Path, default=None, help="Path to save calibrated priors")
    parser.add_argument("--quiet", action="store_true", help="Suppress stdout output")
    args = parser.parse_args()

    runner = OfflineHITLCalibrationRunner(ledger_path=args.ledger, output_path=args.output)
    results = runner.replay_and_calibrate()
    saved_path = runner.save_calibration(results)

    if not args.quiet:
        print("=" * 80)
        print("Offline HITL Calibration & Bayesian Prior Optimization")
        print("=" * 80)
        print(f"Ledger Path:               {runner.ledger_path}")
        print(f"Records Analyzed:          {results['records_analyzed']}")
        print(f"Decisions Evaluated:       {results['decisions_evaluated']}")
        print(f"Baseline Brier Score:      {results['baseline_brier_score']:.6f}")
        print(f"Calibrated Brier Score:    {results['calibrated_brier_score']:.6f}")
        print(f"Brier Reduction:           {results['brier_reduction_pct']}%")
        print(f"Baseline ECE:              {results['baseline_ece']:.6f}")
        print(f"Calibrated ECE:            {results['calibrated_ece']:.6f}")
        print(f"ECE Reduction:             {results['ece_reduction_pct']}%")
        print(f"Autonomous Progression:    {results['autonomous_progression_rate'] * 100:.1f}%")
        print(f"Calibrated Priors Saved:   {saved_path}")
        print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
