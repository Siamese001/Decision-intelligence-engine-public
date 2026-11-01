"""unified_agentic_pipeline.py — Canonical 7-Stage Base Framework & Lifecycle Tracker.

Enforces monotonic progression across U0 -> L1 -> L0 -> C0-PA -> L2 -> X1-X3 -> EVALS
with fail-closed PipelineSequencingViolationError and immutable execution receipts.
"""
from __future__ import annotations

import abc
import hashlib
import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from dual_pipeline.autonomous_pipeline_tracker import AutonomousAgentTrackerMixin, AutonomousLifecycleMixin
from dual_pipeline.exceptions import DualPipelineValueError
from dual_pipeline.l0.ingestion_timing import PipelineTiming
from dual_pipeline.unified_agentic_checkpoint import PipelineExecutionReceipt, StageExecutionRecord

logger = logging.getLogger("UnifiedAgenticPipeline")


class PipelineStage(str, Enum):
    """The 7 immutable stages of the unified agentic pipeline lifecycle."""
    U0_INGRESS, L1_IDENTITY, L0_ROUTING = "U0", "L1", "L0"
    C0_PA_CONTEXT, L2_SYNTHESIS, X_EXIT_GATES, EVALS_RELEASE = "C0-PA", "L2", "X1-X3", "EVALS"


PIPELINE_STAGE_SEQUENCE: List[PipelineStage] = [
    PipelineStage.U0_INGRESS, PipelineStage.L1_IDENTITY, PipelineStage.L0_ROUTING,
    PipelineStage.C0_PA_CONTEXT, PipelineStage.L2_SYNTHESIS, PipelineStage.X_EXIT_GATES, PipelineStage.EVALS_RELEASE,
]


class PipelineSequencingViolationError(DualPipelineValueError):
    """Raised when an agentic pipeline violates the mandatory stage sequence."""


def _resolve_cost_and_tokens() -> tuple[Dict[str, Any], float]:
    try:
        from dual_pipeline.cost_tracker import get_cost_tracker, get_token_tracker
        return get_token_tracker().to_dict(), get_cost_tracker().total_cost_usd
    except Exception:
        return {}, 0.0


def _resolve_receipt_timestamps(run_span: Any, stage_records: dict, is_final: bool) -> tuple[str, str, float]:
    now_iso = datetime.now(timezone.utc).isoformat()
    started_at = (
        run_span.started_at if (run_span and run_span.started_at)
        else (stage_records["U0"].started_at if "U0" in stage_records else now_iso)
    )
    ended_at = run_span.finished_at if is_final else now_iso
    if is_final:
        duration_ms = float(getattr(run_span, "duration_ms", 0.0) or 0.0)
    elif run_span:
        duration_ms = round((time.perf_counter_ns() - run_span.started_tick) / 1e6, 3)
    else:
        duration_ms = 0.0
    return started_at, ended_at, duration_ms


def _build_timing_map(
    timing: Any, breakdown: dict, stage_records: dict, run_span: Any, duration_ms: float
) -> tuple[dict, dict]:
    timing_map = dict(breakdown)
    child_spans = getattr(timing, "child_spans", None)
    if child_spans:
        timing_map["child_spans_s"] = {
            k: round(sum(c.get("duration_ms", 0.0) for c in v) / 1000.0, 6) for k, v in child_spans.items()
        }
    from dual_pipeline.l0.ingestion_timing import compute_exclusive_accounting
    exclusive_acc = compute_exclusive_accounting(run_span, stage_records, getattr(timing, "all_child_spans", []))
    timing_map["exclusive_accounting"] = exclusive_acc
    if "active_compute_s" not in timing_map:
        stage_sum = sum(
            float((getattr(s, "duration_ms", None) if not isinstance(s, dict) else s.get("duration_ms")) or 0.0) / 1000.0
            for s in stage_records.values()
        )
        timing_map["active_compute_s"] = max(0.0, round((duration_ms / 1000.0) - stage_sum, 6))
    return timing_map, exclusive_acc


def _resolve_attempts(timing: Any) -> list:
    ledger = getattr(timing, "attempt_ledger", None)
    return ledger.get_attempts() if ledger else list(timing.attempts)


class UnifiedAgenticPipelineTracker(AutonomousAgentTrackerMixin):
    """State machine enforcing strict monotonic progression across the 7 stages."""

    def __init__(
        self, pipeline_name: str, input_path: str, metadata: Optional[Dict[str, Any]] = None,
        pinned_config_sha256: Optional[str] = None, pinned_parameters_digest: Optional[str] = None,
        session_id: Optional[str] = None, attempt_id: Optional[str] = None,
    ) -> None:
        self.pipeline_name = pipeline_name
        self.input_path = str(input_path)
        self.metadata = dict(metadata or {})
        self.session_id = str(session_id if session_id is not None else self.metadata.get("session_id", ""))
        self.attempt_id = str(attempt_id if attempt_id is not None else self.metadata.get("attempt_id", ""))
        self.completed_stages: List[PipelineStage] = []
        self.stage_records: Dict[str, StageExecutionRecord] = {}
        self.remediation_history: List[StageExecutionRecord] = []
        self.current_stage: Optional[PipelineStage] = None
        self.input_hash: str = ""
        self.timing = PipelineTiming(pipeline_name, self.metadata)
        self.timing_breakdown: Dict[str, float] = {}

        self.max_tokens: Optional[int] = self.metadata.get("max_tokens")
        self.total_tokens: int = 0
        self.init_autonomous_tracker()

        from dual_pipeline.stage_transit import StageTransitChain
        self.transit_chain = StageTransitChain()

        if pinned_config_sha256:
            self.config_sha256 = pinned_config_sha256
            self.parameters_digest = pinned_parameters_digest or ""
        else:
            try:
                from dual_pipeline.pipeline_runtime_config import capture_runtime_config  # noqa: lazy-decouple
                _cfg = capture_runtime_config()
                self.config_sha256 = _cfg.config_sha256
                self.parameters_digest = _cfg.parameters_digest
            except Exception as _e:
                logger.debug(f"Could not capture runtime config: {_e}")
                self.config_sha256 = ""
                self.parameters_digest = ""

    def record_timing_span(self, category: str, duration_s: float) -> None:
        """Record an explicit timing span for pipeline performance attribution."""
        self.timing_breakdown[category] = round(
            self.timing_breakdown.get(category, 0.0) + max(0.0, duration_s), 6
        )

    def intercept_token_call(self, estimated_next_call_tokens: int) -> None:
        """Crash the pipeline if total_tokens + estimated_next_call_tokens > max_tokens."""
        if self.max_tokens is not None and self.total_tokens + estimated_next_call_tokens > self.max_tokens:
                from dual_pipeline.exceptions import PipelineTokenBudgetExhaustedError
                raise PipelineTokenBudgetExhaustedError(
                    f"Directive token budget exhausted. "
                    f"Total: {self.total_tokens}, Estimated next call: {estimated_next_call_tokens}, Max: {self.max_tokens}"
                )

    def record_token_usage(self, consumed_tokens: int) -> None:
        """Record consumed tokens towards the budget."""
        self.total_tokens += max(0, consumed_tokens)

    def start_stage(self, stage: PipelineStage, transit_token: Optional[Any] = None) -> None:
        """Start a stage, verifying all prior stages in the sequence are complete and transit tokens match."""
        self.intercept_token_call(0)
        expected_idx = len(self.completed_stages)
        target_idx = PIPELINE_STAGE_SEQUENCE.index(stage)

        if target_idx != expected_idx:
            expected_stage = PIPELINE_STAGE_SEQUENCE[expected_idx].value
            raise PipelineSequencingViolationError(
                f"Sequencing violation in {self.pipeline_name}: Attempted to execute stage '{stage.value}' "
                f"out of order. Expected next stage: '{expected_stage}'. "
                f"Completed stages: {[s.value for s in self.completed_stages]}."
            )

        if (
            transit_token is not None
            and hasattr(transit_token, "verify_predecessor")
            and not transit_token.verify_predecessor(self.transit_chain.current_token)
        ):
            raise PipelineSequencingViolationError(
                f"Cryptographic stage transit chaining violation for stage '{stage.value}': "
                f"Provided predecessor hash '{transit_token.predecessor_hash}' does not match "
                f"expected chain token."
            )

        if self.current_stage is not None:
            raise PipelineSequencingViolationError("A stage is already active.")
        self.current_stage = stage
        self.timing.start_stage(stage.value)

    def verify_config_stability(self) -> None:
        """Verifies that active runtime configuration has not drifted during stage execution."""
        if not self.config_sha256:
            return
        try:
            from dual_pipeline.pipeline_runtime_config import verify_zero_config_drift  # noqa: lazy-decouple
            valid, reason = verify_zero_config_drift(self.config_sha256, self.parameters_digest)
            if not valid:
                raise PipelineSequencingViolationError(
                    f"Configuration drift violation during {self.pipeline_name}: {reason}"
                )
        except ImportError:
            pass

    def complete_stage(
        self, stage: PipelineStage, status: str = "PASS", details: Optional[Dict[str, Any]] = None
    ) -> None:
        """Complete a stage, recording metrics, verifying zero config drift, and advancing state machine."""
        if self.current_stage != stage:
            raise PipelineSequencingViolationError(
                f"Cannot complete stage '{stage.value}' while active stage is '{self.current_stage}'."
            )

        # Enforce zero config drift across stage transitions
        self.verify_config_stability()

        det = dict(details or {})
        if stage == PipelineStage.U0_INGRESS and "input_hash" in det:
            self.input_hash = str(det["input_hash"])
            self.timing.input_digest = self.input_hash
        record = self._finish_stage_record(stage, status, det)
        self.stage_records[stage.value] = record
        self.completed_stages.append(stage)
        self.current_stage = None

        out_digest = hashlib.sha256(json.dumps(det, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        env = self.transit_chain.advance(stage=stage.value, stage_output_digest=out_digest)
        return env

    def request_remediation(
        self,
        from_stage: PipelineStage,
        to_stage: PipelineStage,
        reason: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Roll back to an earlier stage (e.g. X1-X3 -> L2) for an adversarial remediation cycle."""
        if self.current_stage != from_stage:
            raise PipelineSequencingViolationError(
                f"Cannot request remediation from stage '{from_stage.value}' while active stage is '{self.current_stage}'."
            )
        det = dict(details or {})
        det["remediation_target"] = to_stage.value
        det["remediation_reason"] = reason
        self.remediation_history.append(
            self._finish_stage_record(from_stage, "REMEDIATION_REQUIRED", det)
        )

        target_idx = PIPELINE_STAGE_SEQUENCE.index(to_stage)
        self.completed_stages = [
            s for s in self.completed_stages
            if PIPELINE_STAGE_SEQUENCE.index(s) < target_idx
        ]
        self.current_stage = None

    def _finish_stage_record(self, stage, status, details, error_type=None):
        timing = self.timing.finish_stage(stage.value, status, error_type)
        return StageExecutionRecord(
            stage=stage.value,
            status=status,
            started_at=timing["started_at"],
            finished_at=timing["finished_at"],
            duration_ms=timing["duration_ms"],
            correlation=timing["correlation"],
            details=details,
            ended_at=timing.get("finished_at") or "",
            monotonic_duration_s=round(timing["duration_ms"] / 1000.0, 6),
            session_id=self.session_id,
            attempt_id=self.attempt_id,
        )

    def fail_active_stage(self, error: Optional[BaseException], status: str = "FAILED") -> None:
        """Close failed/cancelled work without advancing the successful stage chain."""
        if self.current_stage is None:
            return
        stage = self.current_stage
        error_type = type(error).__name__ if error is not None else None
        self.stage_records[stage.value] = self._finish_stage_record(
            stage, status, {"error_type": error_type} if error_type else {}, error_type,
        )
        self.current_stage = None

    def execution_scope(self):
        """Bound all stages/providers to one run and restore caller context on exit."""
        return self.timing.execution_scope(self)

    def save_checkpoint(self, stage: PipelineStage, payload: Any, checkpoint_dir: str | Path) -> Path:
        """Save a digest-verified stage output checkpoint to disk."""
        from dual_pipeline.unified_agentic_checkpoint import save_stage_checkpoint
        return save_stage_checkpoint(self, stage, payload, checkpoint_dir)

    def can_resume_from_stage(self, stage: PipelineStage, checkpoint_dir: str | Path) -> bool:
        """Check if all prior stages possess valid, matching checkpoints."""
        from dual_pipeline.unified_agentic_checkpoint import can_resume_from_checkpoint
        return can_resume_from_checkpoint(self, stage, checkpoint_dir)

    def load_checkpoint(self, stage: PipelineStage, checkpoint_dir: str | Path) -> Optional[Dict[str, Any]]:
        """Load and return the cached stage output dictionary from checkpoint."""
        from dual_pipeline.unified_agentic_checkpoint import load_stage_checkpoint
        return load_stage_checkpoint(self, stage, checkpoint_dir)

    def resume_to_stage(self, target_stage: PipelineStage, checkpoint_dir: str | Path) -> None:
        """Fast-forward tracker stage completions up to target_stage using saved checkpoints."""
        from dual_pipeline.unified_agentic_checkpoint import resume_tracker_to_stage
        resume_tracker_to_stage(self, target_stage, checkpoint_dir)

    def execution_outcome(self, requested: str = "COMPLETE") -> str:
        """A sealed run wins over an earlier or later provisional receipt request."""
        span = self.timing.run_span
        if span is not None and span.finished_at is not None:
            return span.status
        missing = any(stage not in self.completed_stages for stage in PIPELINE_STAGE_SEQUENCE)
        failed = any(record.status in {"FAIL", "FAILED"} for record in self.stage_records.values())
        outcome = "FAILED" if failed else requested
        return "INCOMPLETE" if missing and outcome == "COMPLETE" else outcome

    def build_receipt(self, output_dir: str | Path, terminal_outcome: str = "COMPLETE") -> PipelineExecutionReceipt:
        """Snapshot evidence; only execution-scope exit seals the run interval."""
        self.timing.requested_outcome = terminal_outcome
        outcome = self.execution_outcome(terminal_outcome)

        stages_dict = {
            key: {name: value for name, value in asdict(record).items() if name != "stage"}
            for key, record in self.stage_records.items()
        }
        remediation_list = [asdict(record) for record in self.remediation_history]
        token_usage, cost_usd = _resolve_cost_and_tokens()

        run_span = self.timing.run_span
        is_final = bool(run_span and run_span.finished_at is not None)
        started_at, ended_at, duration_ms = _resolve_receipt_timestamps(run_span, self.stage_records, is_final)
        timing_map, exclusive_acc = _build_timing_map(self.timing, self.timing_breakdown, self.stage_records, run_span, duration_ms)

        timing_rec = run_span.record() if run_span else {}
        timing_rec["measurement_state"] = "FINAL" if is_final else "SNAPSHOT"
        timing_rec["is_preterminal_snapshot"] = not is_final
        timing_rec["duration_ms"] = duration_ms
        timing_rec["exclusive_accounting"] = exclusive_acc
        auth_p = self.metadata.get("authoritative_receipt_path") or self.metadata.get("execution_evidence_path")
        if auth_p:
            timing_rec["authoritative_receipt_path"] = auth_p

        ep_wall = float(self.metadata.get("episode_wall_clock_s") or round(duration_ms / 1000.0, 6))
        ir_gap = float(self.metadata.get("inter_run_gap_s") or 0.0)
        app_wait = float(self.metadata.get("approval_wait_s") or 0.0)
        timing_rec["episode_wall_clock_s"] = ep_wall
        timing_rec["inter_run_gap_s"] = ir_gap
        timing_rec["approval_wait_s"] = app_wait

        self.metadata["autonomous_agents"] = self.autonomous_agents
        self.metadata["learning_events"] = self.learning_events

        from dual_pipeline.l0.receipts import sanitize_metadata_for_persistence
        sanitized_meta = sanitize_metadata_for_persistence(dict(self.metadata))

        return PipelineExecutionReceipt(
            pipeline_name=self.pipeline_name, input_path=self.input_path, input_hash=self.input_hash,
            config_sha256=self.config_sha256, parameters_digest=self.parameters_digest, stages=stages_dict,
            remediation_cycles=remediation_list, terminal_outcome=outcome, output_dir=str(output_dir),
            metadata=sanitized_meta, token_usage=token_usage, cost_usd=cost_usd,
            correlation=self.timing.attributes(), execution_timing=timing_rec, attempts=_resolve_attempts(self.timing),
            session_id=self.session_id, attempt_id=self.attempt_id, started_at=started_at, ended_at=ended_at,
            monotonic_duration_s=round(duration_ms / 1000.0, 6), episode_wall_clock_s=ep_wall,
            inter_run_gap_s=ir_gap, approval_wait_s=app_wait, timing_breakdown=timing_map,
            source_evidence=self.metadata.get("source_evidence", {}), operator_answers=self.metadata.get("operator_answers", []),
            artifacts=self.metadata.get("persisted_artifacts", {}), persisted_artifacts_dir=str(self.metadata.get("persisted_artifacts_dir", "")),
        )


class UnifiedAgenticPipeline(abc.ABC, AutonomousLifecycleMixin):
    """
    Abstract base class for all governed pipelines.

    Every subclass (RawTranscriptPipeline, DocumentPipeline, NotesPipeline) MUST
    inherit from this class and guarantee traversal through the 7-stage lifecycle.
    """

    @property
    @abc.abstractmethod
    def pipeline_name(self) -> str:
        """Unique identifier for the pipeline subclass."""

    def create_tracker(
        self, input_path: str | Path, metadata: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None, attempt_id: Optional[str] = None,
    ) -> UnifiedAgenticPipelineTracker:
        """Instantiate a stage progression tracker for an execution run."""
        return UnifiedAgenticPipelineTracker(
            pipeline_name=self.pipeline_name, input_path=str(input_path),
            metadata=metadata or {}, session_id=session_id, attempt_id=attempt_id,
        )
