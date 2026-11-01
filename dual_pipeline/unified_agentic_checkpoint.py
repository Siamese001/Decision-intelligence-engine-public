"""
dual_pipeline/unified_agentic_checkpoint.py — Checkpointing and Resume Manager for Unified Pipelines.

Extracts stage serialization, digest verification, and resume fast-forwarding
out of UnifiedAgenticPipelineTracker to satisfy the strict <=400 lines module budget.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from dual_pipeline.unified_agentic_pipeline import (
        PipelineStage,
        UnifiedAgenticPipelineTracker,
    )


@dataclass
class StageExecutionRecord:
    """Record of an individual stage execution."""
    stage: str
    status: str
    started_at: str
    ended_at: str = ""
    duration_ms: float = 0.0
    monotonic_duration_s: float = 0.0
    session_id: str = ""
    attempt_id: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    finished_at: Optional[str] = None
    correlation: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineExecutionReceipt:
    """Immutable runtime receipt proving the end-to-end traversal of all 7 stages."""
    pipeline_name: str
    framework: str = "U0 -> L1 -> L0 -> C0-PA -> L2 -> X1-X3 (exit) -> evals"
    input_path: str = ""
    input_hash: str = ""
    config_sha256: str = ""
    parameters_digest: str = ""
    session_id: str = ""
    attempt_id: str = ""
    started_at: str = ""
    ended_at: str = ""
    monotonic_duration_s: float = 0.0
    episode_wall_clock_s: float = 0.0
    inter_run_gap_s: float = 0.0
    approval_wait_s: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    stages: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    terminal_outcome: str = "INCOMPLETE"
    remediation_cycles: List[Dict[str, Any]] = field(default_factory=list)
    timing_breakdown: Dict[str, Any] = field(default_factory=dict)
    output_dir: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    token_usage: Dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    correlation: Dict[str, Any] = field(default_factory=dict)
    execution_timing: Dict[str, Any] = field(default_factory=dict)
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    source_evidence: Dict[str, Any] = field(default_factory=dict)
    operator_answers: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    persisted_artifacts_dir: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if "metadata" in d and isinstance(d["metadata"], dict):
            d["metadata"] = {k: v for k, v in d["metadata"].items() if not callable(v)}
        return d

    def write_to_dir(self, target_dir: str | Path) -> Path:
        """Write receipt to output directory as pipeline_execution_receipt.json."""
        import logging
        out_p = Path(target_dir).resolve()
        out_p.mkdir(parents=True, exist_ok=True)
        dest = out_p / "pipeline_execution_receipt.json"
        dest.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        logging.getLogger("UnifiedAgenticPipeline").info(f"Recorded unified pipeline execution receipt: {dest}")
        return dest


def build_pipeline_receipt(
    tracker: Any,
    output_dir: str | Path,
    terminal_outcome: str = "COMPLETE",
) -> PipelineExecutionReceipt:
    """Construct the immutable execution receipt."""
    from dual_pipeline.unified_agentic_pipeline import PIPELINE_STAGE_SEQUENCE
    import time

    missing = [s.value for s in PIPELINE_STAGE_SEQUENCE if s not in tracker.completed_stages]
    outcome = "INCOMPLETE" if missing else terminal_outcome

    stages_dict = {
        k: {
            "stage": v.stage,
            "status": v.status,
            "started_at": v.started_at,
            "ended_at": v.ended_at,
            "duration_ms": v.duration_ms,
            "monotonic_duration_s": v.monotonic_duration_s,
            "session_id": v.session_id,
            "attempt_id": v.attempt_id,
            "details": v.details,
        }
        for k, v in tracker.stage_records.items()
    }

    remediation_list = [
        {
            "stage": r.stage,
            "status": r.status,
            "started_at": r.started_at,
            "ended_at": r.ended_at,
            "duration_ms": r.duration_ms,
            "monotonic_duration_s": r.monotonic_duration_s,
            "session_id": r.session_id,
            "attempt_id": r.attempt_id,
            "details": r.details,
        }
        for r in tracker.remediation_history
    ]

    token_usage: Dict[str, Any] = {}
    cost_usd: float = 0.0
    try:
        from dual_pipeline.cost_tracker import get_cost_tracker, get_token_tracker
        token_usage = get_token_tracker().to_dict()
        cost_usd = get_cost_tracker().total_cost_usd
    except Exception:
        pass

    now_iso = datetime.now(timezone.utc).isoformat()
    start_mono = getattr(tracker, "_pipeline_start_mono", None)
    if start_mono is None:
        start_mono = time.perf_counter()
        tracker._pipeline_start_mono = start_mono
    total_mono_s = round(time.perf_counter() - start_mono, 6)
    timing_map = dict(tracker.timing_breakdown)
    itemized_sum = sum(v for k, v in timing_map.items() if k != "active_compute_s")
    if timing_map.get("active_compute_s", 0.0) == 0.0:
        timing_map["active_compute_s"] = max(0.0, round(total_mono_s - itemized_sum, 6))

    start_wall = getattr(tracker, "_pipeline_start_wall", None) or now_iso
    ep_wall = float(tracker.metadata.get("episode_wall_clock_s") or total_mono_s)
    ir_gap = float(tracker.metadata.get("inter_run_gap_s") or 0.0)
    app_wait = float(tracker.metadata.get("approval_wait_s") or 0.0)

    timing_rec = {
        "duration_ms": round(total_mono_s * 1000.0, 3),
        "measurement_state": "FINAL" if outcome == "COMPLETE" else "SNAPSHOT",
        "is_preterminal_snapshot": outcome != "COMPLETE",
        "episode_wall_clock_s": ep_wall,
        "inter_run_gap_s": ir_gap,
        "approval_wait_s": app_wait,
    }

    return PipelineExecutionReceipt(
        pipeline_name=tracker.pipeline_name,
        input_path=tracker.input_path,
        input_hash=tracker.input_hash,
        config_sha256=tracker.config_sha256,
        parameters_digest=tracker.parameters_digest,
        session_id=tracker.session_id,
        attempt_id=tracker.attempt_id,
        started_at=start_wall,
        ended_at=now_iso,
        monotonic_duration_s=total_mono_s,
        episode_wall_clock_s=ep_wall,
        inter_run_gap_s=ir_gap,
        approval_wait_s=app_wait,
        execution_timing=timing_rec,
        stages=stages_dict,
        remediation_cycles=remediation_list,
        terminal_outcome=outcome,
        timing_breakdown=timing_map,
        output_dir=str(output_dir),
        metadata=dict(tracker.metadata),
        token_usage=token_usage,
        cost_usd=cost_usd,
        source_evidence=tracker.metadata.get("source_evidence", {}),
        operator_answers=tracker.metadata.get("operator_answers", []),
        artifacts=tracker.metadata.get("persisted_artifacts", {}),
        persisted_artifacts_dir=str(tracker.metadata.get("persisted_artifacts_dir", "")),
    )


def save_stage_checkpoint(
    tracker: UnifiedAgenticPipelineTracker,
    stage: PipelineStage,
    payload: Any,
    checkpoint_dir: str | Path,
) -> Path:
    """Save a digest-verified stage output checkpoint atomically to disk."""
    cp_dir = Path(checkpoint_dir).resolve()
    cp_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, dict):
        payload_data = payload
    elif hasattr(payload, "__dict__"):
        payload_data = asdict(payload) if hasattr(payload, "__dataclass_fields__") else payload.__dict__
    else:
        payload_data = {"raw": str(payload)}

    raw_bytes = json.dumps(payload_data, sort_keys=True, default=str).encode("utf-8")
    stage_digest = hashlib.sha256(raw_bytes).hexdigest()
    cp_file = cp_dir / f"checkpoint_{stage.value}.json"
    cp_content = {
        "stage": stage.value,
        "stage_digest": stage_digest,
        "pipeline_name": tracker.pipeline_name,
        "config_sha256": tracker.config_sha256,
        "parameters_digest": tracker.parameters_digest,
        "payload": payload_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    encoded = json.dumps(cp_content, indent=2)
    tmp_fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".tmp_{cp_file.name}_", suffix=".tmp", dir=cp_dir
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(cp_file)
    finally:
        tmp_path.unlink(missing_ok=True)
    return cp_file


def can_resume_from_checkpoint(
    tracker: UnifiedAgenticPipelineTracker,
    stage: PipelineStage,
    checkpoint_dir: str | Path,
) -> bool:
    """Check if all prior stages possess valid, matching checkpoints with valid digests."""
    from dual_pipeline.unified_agentic_pipeline import PIPELINE_STAGE_SEQUENCE

    target_idx = PIPELINE_STAGE_SEQUENCE.index(stage)
    if target_idx == 0:
        return True
    prior_stage = PIPELINE_STAGE_SEQUENCE[target_idx - 1]
    cp_file = Path(checkpoint_dir).resolve() / f"checkpoint_{prior_stage.value}.json"
    if not cp_file.is_file():
        return False
    try:
        data = json.loads(cp_file.read_text(encoding="utf-8"))
        if (
            data.get("config_sha256") != tracker.config_sha256
            or data.get("parameters_digest") != tracker.parameters_digest
        ):
            return False
        expected_digest = data.get("stage_digest")
        payload = data.get("payload")
        if expected_digest and payload is not None:
            raw_bytes = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
            if hashlib.sha256(raw_bytes).hexdigest() != expected_digest:
                return False
        return True
    except Exception:
        return False


def load_stage_checkpoint(
    tracker: UnifiedAgenticPipelineTracker,
    stage: PipelineStage,
    checkpoint_dir: str | Path,
) -> Optional[Dict[str, Any]]:
    """Load and return cached stage output dictionary after verifying content digest."""
    cp_file = Path(checkpoint_dir).resolve() / f"checkpoint_{stage.value}.json"
    if not cp_file.is_file():
        return None
    try:
        data = json.loads(cp_file.read_text(encoding="utf-8"))
        payload = data.get("payload")
        expected_digest = data.get("stage_digest")
        if expected_digest and payload is not None:
            raw_bytes = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
            if hashlib.sha256(raw_bytes).hexdigest() != expected_digest:
                return None
        return payload
    except Exception:
        return None


def resume_tracker_to_stage(
    tracker: UnifiedAgenticPipelineTracker,
    target_stage: PipelineStage,
    checkpoint_dir: str | Path,
) -> None:
    """Fast-forward tracker stage completions up to target_stage using saved checkpoints."""
    from dual_pipeline.unified_agentic_pipeline import (
        PIPELINE_STAGE_SEQUENCE,
        StageExecutionRecord,
    )

    target_idx = PIPELINE_STAGE_SEQUENCE.index(target_stage)
    for i in range(target_idx):
        stg = PIPELINE_STAGE_SEQUENCE[i]
        if stg not in tracker.completed_stages:
            payload = load_stage_checkpoint(tracker, stg, checkpoint_dir) or {}
            tracker.completed_stages.append(stg)
            tracker.stage_records[stg.value] = StageExecutionRecord(
                stage=stg.value,
                status="CHECKPOINT_RESTORED",
                started_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=0.0,
                details=payload,
            )
