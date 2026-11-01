"""
dual_pipeline/learning_loop_core/scanner.py

Scans sealed Wave 7 answer evaluation receipts and answer ledger feedback
to detect recurring failure cohorts and synthesize contract-valid learning proposals.
Enforces receipt deduplication, durable cursors, and explicit source digest provenance.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import read_json, stable_id, utc_now, write_json
from dual_pipeline.learning_engine_core.provenance import (
    PROVENANCE_EXTENSION_KEY,
    build_signed_provenance,
)
from dual_pipeline.parameters import ParameterRegistry

from .ledger_scanner import extract_failures_from_ledger as _extract_failures_from_ledger
from .models import (
    CATEGORY_COPILOT_CONTEXT,
    CATEGORY_COPILOT_GROUNDING,
    CATEGORY_PARAMETER_DEFAULTS,
    CATEGORY_QUERY_RETRIEVAL,
    AnswerFailureCohort,
    AnswerFailureItem,
)

logger = logging.getLogger(__name__)


def _extract_failures_from_receipt(
    receipt_data: Dict[str, Any], receipt_file: Path
) -> List[AnswerFailureItem]:
    failures: List[AnswerFailureItem] = []
    receipt_id = receipt_data.get("receipt_id") or receipt_file.stem
    metrics = receipt_data.get("metrics", {})
    pipeline = receipt_data.get("pipeline", "copilot")
    ts = receipt_data.get("sealed_at") or receipt_data.get("evaluated_at") or utc_now()

    # 1. Context Recall failure
    ctx_recall = metrics.get("context_recall")
    if ctx_recall is not None and float(ctx_recall) < 0.70:
        failures.append(
            AnswerFailureItem(
                source="eval_receipt",
                receipt_id=receipt_id,
                question_id=f"{receipt_id}_ctx",
                pipeline=pipeline,
                failure_type="context_recall_miss",
                metric_value=float(ctx_recall),
                threshold=0.70,
                timestamp=ts,
                details={"metric": "context_recall", "value": float(ctx_recall)},
            )
        )

    # 2. Groundedness failure
    groundedness = metrics.get("groundedness")
    if groundedness is not None and float(groundedness) < 0.85:
        failures.append(
            AnswerFailureItem(
                source="eval_receipt",
                receipt_id=receipt_id,
                question_id=f"{receipt_id}_grnd",
                pipeline=pipeline,
                failure_type="groundedness_low",
                metric_value=float(groundedness),
                threshold=0.85,
                timestamp=ts,
                details={"metric": "groundedness", "value": float(groundedness)},
            )
        )

    # 3. Conflict miss rate
    conflict_miss = metrics.get("conflict_miss_rate")
    if conflict_miss is not None and float(conflict_miss) > 0.10:
        failures.append(
            AnswerFailureItem(
                source="eval_receipt",
                receipt_id=receipt_id,
                question_id=f"{receipt_id}_conf",
                pipeline=pipeline,
                failure_type="conflict_miss",
                metric_value=float(conflict_miss),
                threshold=0.10,
                timestamp=ts,
                details={"metric": "conflict_miss_rate", "value": float(conflict_miss)},
            )
        )

    # 4. Query retrieval miss
    if pipeline == "query":
        usefulness = metrics.get("usefulness")
        if usefulness is not None and float(usefulness) < 0.75:
            failures.append(
                AnswerFailureItem(
                    source="eval_receipt",
                    receipt_id=receipt_id,
                    question_id=f"{receipt_id}_usef",
                    pipeline="query",
                    failure_type="query_retrieval_miss",
                    metric_value=float(usefulness),
                    threshold=0.75,
                    timestamp=ts,
                    details={"metric": "usefulness", "value": float(usefulness)},
                )
            )

    # 5. Item-level failure details
    for item in receipt_data.get("failure_details", []):
        f_type = item.get("failure_type", "unspecified_miss")
        failures.append(
            AnswerFailureItem(
                source="eval_receipt_item",
                receipt_id=receipt_id,
                question_id=item.get("question_id", receipt_id),
                pipeline=item.get("pipeline", pipeline),
                failure_type=f_type,
                metric_value=float(item.get("metric_value", 0.0)),
                threshold=float(item.get("threshold", 0.5)),
                timestamp=ts,
                details=item,
            )
        )

    return failures


def _collect_eval_failures(eval_dir: Path) -> List[AnswerFailureItem]:
    """Collects failure items from receipt files, deduplicating duplicate files."""
    failures: List[AnswerFailureItem] = []
    if not eval_dir.is_dir():
        return failures
    seen_hashes: set[str] = set()
    seen_receipt_ids: set[str] = set()

    for json_file in sorted(eval_dir.glob("*.json")):
        if json_file.name.endswith((
            "_applied_receipt.json",
            "_rollback_receipt.json",
            "_canary_receipt.json",
            "_simulation_receipt.json",
            "_rollback_snapshot.json",
        )):
            continue
        try:
            raw_bytes = json_file.read_bytes()
            content_hash = hashlib.sha256(raw_bytes).hexdigest()
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)

            data = json.loads(raw_bytes.decode("utf-8"))
            receipt_id = data.get("receipt_id") or json_file.stem
            if receipt_id in seen_receipt_ids:
                continue
            seen_receipt_ids.add(receipt_id)

            failures.extend(_extract_failures_from_receipt(data, json_file))
        except Exception:
            continue
    return failures


def _categorize_failure_item(f: AnswerFailureItem) -> str:
    if f.failure_type in ("context_recall_miss", "context_recall_low"):
        return CATEGORY_COPILOT_CONTEXT
    if f.failure_type in ("groundedness_low", "conflict_miss", "provenance_low"):
        return CATEGORY_COPILOT_GROUNDING
    if f.failure_type in ("query_retrieval_miss", "usefulness_low", "uptake_low") or f.pipeline == "query":
        return CATEGORY_QUERY_RETRIEVAL
    return CATEGORY_COPILOT_CONTEXT


def _bucket_failures(failures: List[AnswerFailureItem]) -> Dict[str, List[AnswerFailureItem]]:
    buckets: Dict[str, List[AnswerFailureItem]] = {
        CATEGORY_COPILOT_CONTEXT: [],
        CATEGORY_COPILOT_GROUNDING: [],
        CATEGORY_QUERY_RETRIEVAL: [],
    }
    for f in failures:
        buckets[_categorize_failure_item(f)].append(f)
    return buckets


def _calculate_proposed_value(param_name: str, curr_val: Any, step: float) -> float:
    if "tau" in param_name:
        return round(max(30.0, min(1800.0, float(curr_val) + step)), 4)
    return round(max(0.0, min(1.0, float(curr_val) + step)), 4)


def _build_bucket_cohort(
    cat: str,
    items: List[AnswerFailureItem],
    registry: ParameterRegistry,
    temporal_drift_scan: Optional[Any] = None,
) -> AnswerFailureCohort:
    c_info = CATEGORY_PARAMETER_DEFAULTS.get(cat, {})
    param_name = c_info.get("primary_parameter", "copilot.context.decay_tau_seconds")
    step = float(c_info.get("default_step", 50.0))
    if cat == CATEGORY_COPILOT_CONTEXT and temporal_drift_scan is not None:
        from dual_pipeline.staleness_core.temporal_drift_agent import compute_empirical_decay_step_seconds
        step = compute_empirical_decay_step_seconds(temporal_drift_scan, base_step=step)
    curr_val = registry.get(param_name, 300.0 if "tau" in param_name else 0.5)
    proposed_val = _calculate_proposed_value(param_name, curr_val, step)
    run_ids = sorted(list({f.receipt_id for f in items}))
    receipt_ids = sorted(list({f.receipt_id for f in items}))
    c_key = f"answer_eval:{cat}:{items[0].failure_type}"
    return AnswerFailureCohort(
        cohort_key=c_key,
        category=cat,
        occurrence_count=len(items),
        run_ids=run_ids,
        receipt_ids=receipt_ids,
        items=[it.to_dict() for it in items],
        target_parameter=param_name,
        current_value=curr_val,
        proposed_value=proposed_val,
        latest_timestamp=max(f.timestamp for f in items),
        rationale=(
            f"Observed {len(items)} {items[0].failure_type} occurrences across {len(receipt_ids)} eval runs. "
            f"Proposing calibration of {param_name} from {curr_val} to {proposed_val}."
        ),
    )


def scan_answer_eval_receipts(
    eval_dir: Path,
    ledger_path: Optional[Path] = None,
    min_occurrences: int = 2,
    config_path: Optional[Path] = None,
    cursor_path: Optional[Path] = None,
    temporal_drift_scan: Optional[Any] = None,
) -> List[AnswerFailureCohort]:
    """Scans eval receipt files and feedback ledger for recurring failure patterns."""
    all_failures: List[AnswerFailureItem] = _collect_eval_failures(eval_dir)
    if ledger_path and ledger_path.is_file():
        all_failures.extend(_extract_failures_from_ledger(ledger_path))

    seen_ids: set[str] = set()
    if cursor_path and cursor_path.is_file():
        try:
            cursor_data = read_json(cursor_path)
            seen_ids = set(cursor_data.get("processed_receipt_ids", []))
        except Exception as e:
            logger.warning("Failed to load scanner cursor: %s", e)

    if seen_ids:
        all_failures = [f for f in all_failures if f.receipt_id not in seen_ids]

    buckets = _bucket_failures(all_failures)
    registry = ParameterRegistry(config_path=config_path) if config_path else ParameterRegistry()
    cohorts: List[AnswerFailureCohort] = []

    for cat, items in buckets.items():
        if len(items) >= min_occurrences:
            cohorts.append(_build_bucket_cohort(cat, items, registry, temporal_drift_scan=temporal_drift_scan))

    if cursor_path:
        try:
            new_seen = sorted(list(seen_ids | {f.receipt_id for f in all_failures}))
            write_json(cursor_path, {"processed_receipt_ids": new_seen, "updated_at": utc_now()})
        except Exception as e:
            logger.warning("Failed to save scanner cursor: %s", e)

    return sorted(cohorts, key=lambda c: c.occurrence_count, reverse=True)


def _resolve_source_receipt_digests(cohort: AnswerFailureCohort, proposals_dir: Path) -> List[str]:
    """Resolves SHA-256 digests for cohort receipts, marking unavailable sources explicitly."""
    digests: List[str] = []
    for rid in cohort.receipt_ids:
        found = False
        candidates = [
            proposals_dir.parent / "evals" / f"{rid}.json",
            proposals_dir.parent / f"{rid}.json",
            proposals_dir / f"{rid}.json",
        ]
        for cp in candidates:
            if cp.is_file():
                digests.append(hashlib.sha256(cp.read_bytes()).hexdigest())
                found = True
                break
        if not found:
            digests.append(f"MISSING_SOURCE:{rid}")
    return digests


def _build_answer_learning_extensions(
    cohort: AnswerFailureCohort,
    p_id: str,
    pname: str,
    cval: Any,
    pval: Any,
    run_ids: List[str],
    evidence_events: List[str],
    proposed_change: str,
    source_receipt_digests: List[str],
) -> Dict[str, Any]:
    target_metric_key = "context_recall" if cohort.category == CATEGORY_COPILOT_CONTEXT else "groundedness_accuracy"
    learning_ext: Dict[str, Any] = {
        "cohort_key": cohort.cohort_key, "category": cohort.category, "target_type": "parameters",
        "parameter_name": pname, "current_value": cval, "proposed_value": pval,
        "receipt_ids": cohort.receipt_ids, "source_receipt_digests": source_receipt_digests,
    }
    if cohort.items:
        avg_metric = sum(float(it.get("metric_value", 0.5)) for it in cohort.items) / len(cohort.items)
        avg_thresh = sum(float(it.get("threshold", 0.75)) for it in cohort.items) / len(cohort.items)
        learning_ext["baseline_score"] = round(avg_metric, 4)
        learning_ext["target_scores"] = {target_metric_key: round(max(avg_thresh, avg_metric + 0.15), 4)}
    replay_def = {
        "evaluator": "AnswerReplayEvaluator.simulate_parameter_change",
        "cohort_key": cohort.cohort_key, "target_parameter": pname, "proposed_value": pval,
    }
    prov = build_signed_provenance(
        proposal_id=p_id, evidence_event_ids=evidence_events, run_ids=run_ids,
        proposed_change=proposed_change, replay_definition=replay_def, source_receipt_digests=source_receipt_digests,
    )
    return {
        "com.partner_advisory.nee.learning": learning_ext,
        "com.partner_advisory.nee.calibration": {
            "target_parameter": pname, "current_value": cval, "proposed_value": pval,
            "requires_release_gate": True, "auto_mutation_permitted": False,
        },
        PROVENANCE_EXTENSION_KEY: prov,
    }


def synthesize_answer_learning_proposal(
    cohort: AnswerFailureCohort,
    proposals_dir: Path,
    parameter_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Synthesizes an immutable, schema-valid learning-change-proposal:1.0.0 for an answer cohort."""
    proposals_dir.mkdir(parents=True, exist_ok=True)
    p_override = parameter_override or {}
    pname = p_override.get("target_parameter", cohort.target_parameter)
    pval = p_override.get("proposed_value", cohort.proposed_value)
    cval = p_override.get("current_value", cohort.current_value)

    if cohort.category == CATEGORY_COPILOT_CONTEXT:
        proposal_type = "copilot_context"
    elif cohort.category == CATEGORY_COPILOT_GROUNDING:
        proposal_type = "copilot_grounding"
    else:
        proposal_type = "retrieval_weight"

    p_id = stable_id("learning-proposal", proposal_type, cohort.cohort_key, length=24)
    run_ids = cohort.run_ids or ["run-answer-eval-cohort"]
    evidence_events = [stable_id("event", item.get("receipt_id") or f"ev-{i}", length=20) for i, item in enumerate(cohort.items)]
    source_receipt_digests = _resolve_source_receipt_digests(cohort, proposals_dir)
    problem = f"Recurring answer failure cohort in category '{cohort.category}' ({cohort.occurrence_count} occurrences)."
    proposed_change = f"Calibrate parameter '{pname}' from {cval} to {pval} to resolve {cohort.cohort_key} failures."
    evaluation_plan = [
        "Execute dry-run replay simulation on historical answer evaluation items",
        "Verify target metric improvement (recall / groundedness)",
        "Verify guard metrics not worsened (staleness, durability, usefulness)",
        "Verify latency within governed budget",
    ]
    if cohort.category == CATEGORY_QUERY_RETRIEVAL:
        evaluation_plan.append("Verify clearance across all 11 BGE-M3 retrieval release gates")

    ext = _build_answer_learning_extensions(
        cohort, p_id, pname, cval, pval, run_ids, evidence_events, proposed_change, source_receipt_digests
    )
    proposal = create_artifact(
        "learning-change-proposal", p_id, proposal_id=p_id, run_ids=run_ids, proposal_type=proposal_type,
        status="proposed", problem=problem, evidence_event_ids=evidence_events, proposed_change=proposed_change,
        evaluation_plan=evaluation_plan, risk="low" if cohort.category == CATEGORY_COPILOT_CONTEXT else "medium",
        approval_required=True, auto_apply=False, created_at=utc_now(), extensions=ext,
    )
    write_json(proposals_dir / f"{p_id}.json", proposal)
    return proposal


CANONICAL_COHORT_TYPES = (
    "RETRIEVAL_MISS",
    "GROUNDING_REJECTION",
    "JUDGE_RETRY_EXHAUSTION",
    "OWNER_DRIFT",
    "TEMPORAL_STALENESS",
)


def cluster_failure_cohorts(
    feedback_items: List[Dict[str, Any]],
    *,
    min_cohort_size: int = 3,
) -> Dict[str, List[Dict[str, Any]]]:
    """Deterministically clusters feedback items across the 5 canonical failure cohort types."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in feedback_items:
        raw_t = str(item.get("failure_type") or item.get("category") or "RETRIEVAL_MISS").upper()
        if "STALE" in raw_t or "TEMPORAL" in raw_t or "CONTEXT" in raw_t:
            norm_t = "TEMPORAL_STALENESS"
        elif "OWNER" in raw_t or "ALIAS" in raw_t:
            norm_t = "OWNER_DRIFT"
        elif "RETRY" in raw_t or "EXHAUST" in raw_t:
            norm_t = "JUDGE_RETRY_EXHAUSTION"
        elif "GROUND" in raw_t or "CONFLICT" in raw_t:
            norm_t = "GROUNDING_REJECTION"
        else:
            norm_t = "RETRIEVAL_MISS"
        grouped.setdefault(norm_t, []).append(dict(item))
    return {k: v for k, v in grouped.items() if len(v) >= min_cohort_size}


def _build_cohort_proposal_spec(
    cohort_type: str, items: List[Dict[str, Any]]
) -> Tuple[str, Any, Any, str, str, Dict[str, Any]]:
    from dual_pipeline.release_policy import RETRIEVAL_RELEASE_11_GATES

    base_ext: Dict[str, Any] = {
        "cohort_key": f"feedback:{cohort_type.lower()}",
        "target_before": 0.72,
        "target_after": 0.86,
        "baseline_score": 0.72,
        "target_scores": {"groundedness_accuracy": 0.86, "context_recall": 0.86},
    }
    if cohort_type == "TEMPORAL_STALENESS":
        raw_taus = [float(it.get("suggested_decay_tau") or it.get("observed_decay_tau") or 300.0) for it in items]
        avg_tau = round(max(30.0, min(1800.0, sum(raw_taus) / len(raw_taus))), 4)
        return "copilot.context.decay_tau_seconds", 300.0, avg_tau, "copilot_context", CATEGORY_COPILOT_CONTEXT, {**base_ext, "target_type": "parameters"}
    if cohort_type == "OWNER_DRIFT":
        from_a = str(items[0].get("from_alias") or items[0].get("from_id") or "unknown_alias")
        to_c = str(items[0].get("to_canonical") or items[0].get("to_id") or "canonical_owner")
        return f"alias:{from_a}", from_a, to_c, "alias_addition", "entity_resolution", {
            **base_ext, "target_type": "learned_aliases", "from_id": from_a, "to_id": to_c, "output_01_immutable_verified": True,
        }
    if cohort_type == "GROUNDING_REJECTION":
        return "copilot.rigor.min_provenance_score", 0.70, 0.80, "copilot_grounding", CATEGORY_COPILOT_GROUNDING, {**base_ext, "target_type": "parameters"}
    if cohort_type == "JUDGE_RETRY_EXHAUSTION":
        return "copilot.rigor.budget_ms", 250.0, 350.0, "copilot_grounding", CATEGORY_COPILOT_GROUNDING, {**base_ext, "target_type": "parameters"}
    gates = items[0].get("bge_gates") or {g: True for g in RETRIEVAL_RELEASE_11_GATES}
    return "retrieval.bm25_weight", 0.35, 0.40, "retrieval_weight", CATEGORY_QUERY_RETRIEVAL, {
        **base_ext, "target_type": "parameters", "bge_gates": dict(gates),
    }


def scan_feedback_for_proposals(
    feedback_items: Sequence[Dict[str, Any]] | Path,
    *,
    min_cohort_size: int = 3,
    proposals_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Clusters feedback items and synthesizes governed learning proposals for all 5 cohort types."""
    if isinstance(feedback_items, Path):
        rows = [json.loads(line) for line in feedback_items.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        rows = [dict(it) for it in feedback_items]
    cohorts = cluster_failure_cohorts(rows, min_cohort_size=min_cohort_size)
    proposals: List[Dict[str, Any]] = []
    for cohort_type, items in sorted(cohorts.items()):
        ev_ids = [str(it.get("event_id") or f"ev-{cohort_type.lower()}-{i + 1}") for i, it in enumerate(items)]
        p_id = stable_id("learning-proposal", cohort_type.lower(), ev_ids[0], length=24)
        pname, cval, pval, ptype, cat, extra_ext = _build_cohort_proposal_spec(cohort_type, items)
        proposed_change = f"Calibrate {pname} from {cval} to {pval} for {cohort_type} cohort ({len(items)} events)."
        prov = build_signed_provenance(
            proposal_id=p_id, evidence_event_ids=ev_ids, run_ids=["run-feedback-scan"],
            proposed_change=proposed_change, replay_definition={"cohort_type": cohort_type, "target": pname},
        )
        prop = create_artifact(
            "learning-change-proposal", p_id, proposal_id=p_id, run_ids=["run-feedback-scan"],
            proposal_type=ptype, status="proposed",
            problem=f"Recurring {cohort_type} cohort ({len(items)} occurrences).",
            evidence_event_ids=ev_ids, proposed_change=proposed_change,
            evaluation_plan=["Simulate on historical cohort", "Require operator approval before apply"],
            risk="low", approval_required=True, auto_apply=False, created_at=utc_now(),
            extensions={
                "com.partner_advisory.nee.learning": {
                    **extra_ext, "category": cat, "parameter_name": pname, "current_value": cval, "proposed_value": pval,
                },
                "com.partner_advisory.nee.calibration": {
                    "target_parameter": pname, "current_value": cval, "proposed_value": pval,
                    "requires_release_gate": True, "auto_mutation_permitted": False,
                },
                PROVENANCE_EXTENSION_KEY: prov,
            },
        )
        if proposals_dir is not None:
            Path(proposals_dir).mkdir(parents=True, exist_ok=True)
            write_json(Path(proposals_dir) / f"{p_id}.json", prop)
        proposals.append(prop)
    return proposals
