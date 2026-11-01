"""
dual_pipeline/learning_loop_core/seal.py

Stage-chain sealing engine for closed-loop learning across stages LP0-LP4 (Wave 8).
Calculates an immutable Merkle hash chain across learning lifecycle events.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from dual_pipeline.core import digest, stable_id, utc_now, write_json
from dual_pipeline.telemetry_core.constants import ledger_connection

logger = logging.getLogger(__name__)

LEARNING_STAGES: Sequence[str] = ("LP0", "LP1", "LP2", "LP3", "LP4")
REQUIRED_STAGES: Sequence[str] = LEARNING_STAGES
CANARY_STAGE: str = "LP5"
LEARNING_STAGES_WITH_CANARY: Sequence[str] = ("LP0", "LP1", "LP2", "LP3", "LP4", "LP5")


def evaluate_post_apply_canary(
    *,
    baseline_metrics: Dict[str, float],
    canary_metrics: Dict[str, float],
    mrr_regression_tolerance: float = -0.01,
) -> Dict[str, Any]:
    """Evaluates post-apply canary metrics against baseline for MRR/NDCG regression."""
    b_mrr = float(baseline_metrics.get("mrr", baseline_metrics.get("mrr_at_10", 0.0)))
    c_mrr = float(canary_metrics.get("mrr", canary_metrics.get("mrr_at_10", b_mrr)))
    b_ndcg = float(baseline_metrics.get("ndcg", baseline_metrics.get("ndcg_at_10", 0.0)))
    c_ndcg = float(canary_metrics.get("ndcg", canary_metrics.get("ndcg_at_10", b_ndcg)))
    mrr_delta = round(c_mrr - b_mrr, 4)
    ndcg_delta = round(c_ndcg - b_ndcg, 4)
    passed = (mrr_delta >= mrr_regression_tolerance) and (ndcg_delta >= mrr_regression_tolerance)
    return {
        "passed": passed,
        "status": "CANARY_PASS" if passed else "CANARY_REGRESSION",
        "mrr_delta": mrr_delta,
        "ndcg_delta": ndcg_delta,
        "baseline_metrics": dict(baseline_metrics),
        "canary_metrics": dict(canary_metrics),
    }


def record_canary_learning_event(
    *,
    status: str,
    run_id: str,
    proposal_id: str,
    payload_reference: str,
    artifact_data: Dict[str, Any],
    db_path: Optional[Path] = None,
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
    parent_span_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Records a stage LP5 post-application canary event in the learning event ledger."""
    return record_learning_event(
        stage=CANARY_STAGE,
        event_type="learning_canary_evaluated",
        status=status,
        run_id=run_id,
        proposal_id=proposal_id,
        payload_reference=payload_reference,
        artifact_data=artifact_data,
        db_path=db_path,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
    )


def record_learning_event(
    *,
    stage: str,
    event_type: str,
    status: str,
    run_id: str,
    proposal_id: str,
    payload_reference: str,
    artifact_data: Dict[str, Any],
    db_path: Optional[Path] = None,
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
    parent_span_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Records a structured stage event in the durable learning event ledger."""
    now_ts = utc_now()
    ev_id = stable_id("evt", stage.lower(), proposal_id, length=24)
    t_id = trace_id or f"trace-learn-{proposal_id}"
    s_id = span_id or f"span-{stage.lower()}-{proposal_id}"
    p_span = parent_span_id
    payload_dig = digest(artifact_data)
    art_json = json.dumps(artifact_data, sort_keys=True)

    ev_dict = {
        "event_id": ev_id,
        "run_id": run_id,
        "trace_id": t_id,
        "span_id": s_id,
        "parent_span_id": p_span,
        "use_case": "learning",
        "stage": stage,
        "event_type": event_type,
        "status": status,
        "payload_reference": payload_reference,
        "payload_digest": payload_dig,
        "artifact_json": art_json,
        "created_at": now_ts,
    }

    if db_path is not None:
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with ledger_connection(db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO learning_event
                       (event_id, run_id, trace_id, span_id, parent_span_id, use_case, stage,
                        event_type, status, payload_reference, payload_digest, artifact_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ev_id,
                        run_id,
                        t_id,
                        s_id,
                        p_span,
                        "learning",
                        stage,
                        event_type,
                        status,
                        payload_reference,
                        payload_dig,
                        art_json,
                        now_ts,
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.warning("Failed to persist learning event to %s: %s", db_path, e)

    return ev_dict


def seal_learning_stage_chain(
    *,
    proposal_id: str,
    run_id: str,
    stage_events: Sequence[Dict[str, Any]],
    output_dir: Optional[Path] = None,
    require_canary: bool = False,
) -> Dict[str, Any]:
    """Computes an immutable Merkle hash chain across stages LP0-LP4 (or LP0-LP5 with canary) and emits a run seal.

    Merkle formula:
        H_0 = sha256(canonical_json(LP0))
        H_i = sha256(H_{i-1} + canonical_json(LP_i))
    """
    now_ts = utc_now()
    stage_map = {ev.get("stage"): ev for ev in stage_events if ev.get("stage")}
    target_stages = (
        LEARNING_STAGES_WITH_CANARY
        if (require_canary or CANARY_STAGE in stage_map)
        else LEARNING_STAGES
    )

    missing_stages = [s for s in target_stages if s not in stage_map]
    if missing_stages:
        raise ValueError(
            f"Cannot seal learning run {run_id}: missing mandatory stages {missing_stages}"
        )

    current_hash = ""
    event_hashes: Dict[str, str] = {}

    for stage_name in target_stages:
        ev = stage_map[stage_name]
        ev_canonical = json.dumps(ev, sort_keys=True, separators=(",", ":")).encode("utf-8")
        step_digest = hashlib.sha256(ev_canonical).hexdigest()
        event_hashes[stage_name] = step_digest

        if not current_hash:
            current_hash = step_digest
        else:
            combined = (current_hash + step_digest).encode("utf-8")
            current_hash = hashlib.sha256(combined).hexdigest()

    merkle_root = current_hash
    seal_id = stable_id("seal-learning", proposal_id, length=24)

    seal_receipt = {
        "seal_id": seal_id,
        "run_id": run_id,
        "proposal_id": proposal_id,
        "stages_sealed": list(target_stages),
        "stage_count": len(target_stages),
        "terminal_outcome": "PASS",
        "merkle_root": merkle_root,
        "event_hashes": event_hashes,
        "sealed_at": now_ts,
    }

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        seal_path = output_dir / f"run_seal_{proposal_id}.json"
        write_json(seal_path, seal_receipt)
        seal_receipt["seal_path"] = str(seal_path)

    return seal_receipt


seal_learning_lifecycle = seal_learning_stage_chain
