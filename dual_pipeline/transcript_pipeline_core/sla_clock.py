"""Post-recording ingestion SLA clock and episode timing coordinator.

Measures stop-to-publish wall-clock progression, approval wait times,
inter-run gaps, and stage-by-stage budget consumption.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config" / "governance" / "post_recording_sla.json"


def _parse_iso_to_utc_seconds(ts: Optional[Union[float, str, datetime]]) -> Optional[float]:
    """Normalize numeric timestamps, ISO strings, or datetime objects to UTC epoch seconds."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.timestamp()
    if isinstance(ts, str):
        cleaned = ts.strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            pass
        try:
            # Replace Z with +00:00 for fromisoformat compatibility
            if cleaned.endswith("Z"):
                cleaned = cleaned[:-1] + "+00:00"
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return None
    return None


@dataclass
class RunEpisodeSpan:
    """Record of a single execution run attempt within an overall meeting episode."""
    run_id: str
    started_at: float
    ended_at: Optional[float] = None
    terminal_outcome: str = "INCOMPLETE"
    stages: Dict[str, float] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        if self.ended_at is not None:
            return max(0.0, self.ended_at - self.started_at)
        return 0.0


@dataclass
class PostRecordingSLAClock:
    """Tracks the continuous episode lifecycle from meeting stop to final graph publication."""

    stopped_at: Optional[float] = None
    approval_dispatched_at: Optional[float] = None
    approval_received_at: Optional[float] = None
    runs: List[RunEpisodeSpan] = field(default_factory=list)
    sla_config: Dict[str, Any] = field(default_factory=dict)
    terminal_outcome: Optional[str] = None
    completed_at: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.sla_config:
            self.sla_config = self.load_sla_config()

    @classmethod
    def load_sla_config(cls, path: Optional[Path] = None) -> Dict[str, Any]:
        """Loads the SLA governance configuration."""
        target = path or CONFIG_PATH
        if target.exists():
            try:
                return json.loads(target.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "sla_target_seconds": 600.0,
            "stage_budgets_seconds": {
                "source_discovery": 15.0,
                "source_assembly": 60.0,
                "U0": 60.0,
                "L1": 30.0,
                "L0": 15.0,
                "extraction": 60.0,
                "judge": 120.0,
                "publish": 60.0,
                "total": 600.0,
            },
            "max_remediation_cycles": 2,
        }

    def record_stop_event(self, timestamp: Optional[Union[float, str, datetime]] = None) -> None:
        """Records the exact moment audio recording stopped and master was sealed."""
        parsed = _parse_iso_to_utc_seconds(timestamp)
        self.stopped_at = parsed if parsed is not None else datetime.now(timezone.utc).timestamp()

    def record_approval_dispatch(self, timestamp: Optional[Union[float, str, datetime]] = None) -> None:
        """Records when an active ingestion approval request was presented to operator."""
        parsed = _parse_iso_to_utc_seconds(timestamp)
        self.approval_dispatched_at = parsed if parsed is not None else datetime.now(timezone.utc).timestamp()

    def record_approval_received(self, timestamp: Optional[Union[float, str, datetime]] = None) -> None:
        """Records when authentic operator approval was recorded."""
        parsed = _parse_iso_to_utc_seconds(timestamp)
        self.approval_received_at = parsed if parsed is not None else datetime.now(timezone.utc).timestamp()

    def start_run(self, run_id: str, started_at: Optional[Union[float, str, datetime]] = None) -> RunEpisodeSpan:
        """Begins tracking an individual pipeline execution run."""
        parsed = _parse_iso_to_utc_seconds(started_at)
        s_time = parsed if parsed is not None else datetime.now(timezone.utc).timestamp()
        if self.stopped_at is None:
            self.stopped_at = s_time
        span = RunEpisodeSpan(run_id=run_id, started_at=s_time)
        self.runs.append(span)
        return span

    def finish_run(
        self,
        run_id: str,
        ended_at: Optional[Union[float, str, datetime]] = None,
        terminal_outcome: str = "INCOMPLETE",
        stages: Optional[Dict[str, float]] = None,
    ) -> None:
        """Completes tracking of an individual pipeline run."""
        parsed = _parse_iso_to_utc_seconds(ended_at)
        e_time = parsed if parsed is not None else datetime.now(timezone.utc).timestamp()
        for span in self.runs:
            if span.run_id == run_id and span.ended_at is None:
                span.ended_at = e_time
                span.terminal_outcome = terminal_outcome
                if stages:
                    span.stages = dict(stages)
                break
        self.completed_at = e_time
        self.terminal_outcome = terminal_outcome

    @property
    def approval_wait_s(self) -> float:
        """Total seconds spent waiting for operator approval."""
        if self.approval_dispatched_at and self.approval_received_at:
            return round(max(0.0, self.approval_received_at - self.approval_dispatched_at), 3)
        return 0.0

    @property
    def inter_run_gap_s(self) -> float:
        """Accumulated seconds between the end of run N and start of run N+1."""
        if len(self.runs) < 2:
            return 0.0
        gap_total = 0.0
        for i in range(len(self.runs) - 1):
            prior_end = self.runs[i].ended_at
            next_start = self.runs[i + 1].started_at
            if prior_end is not None and next_start > prior_end:
                gap_total += (next_start - prior_end)
        return round(max(0.0, gap_total), 3)

    @property
    def episode_wall_clock_s(self) -> float:
        """Wall-clock time from stop event (or initial run start) to final run completion."""
        origin = self.stopped_at
        if origin is None and self.runs:
            origin = self.runs[0].started_at
        if origin is None:
            return 0.0
        end_time = self.completed_at
        if end_time is None and self.runs and self.runs[-1].ended_at is not None:
            end_time = self.runs[-1].ended_at
        if end_time is None:
            end_time = datetime.now(timezone.utc).timestamp()
        return round(max(0.0, end_time - origin), 3)

    @property
    def is_sla_breached(self) -> bool:
        """Returns True if the overall episode exceeded the target SLA ceiling."""
        sla_target = float(self.sla_config.get("sla_target_seconds", 600.0))
        return self.episode_wall_clock_s > sla_target

    def to_dict(self) -> Dict[str, Any]:
        """Serializes SLA metrics for inclusion into receipts and telemetry envelopes."""
        sla_target = float(self.sla_config.get("sla_target_seconds", 600.0))
        return {
            "sla_target_s": sla_target,
            "episode_wall_clock_s": self.episode_wall_clock_s,
            "inter_run_gap_s": self.inter_run_gap_s,
            "approval_wait_s": self.approval_wait_s,
            "is_sla_breached": self.is_sla_breached,
            "runs_count": len(self.runs),
            "stopped_at": datetime.fromtimestamp(self.stopped_at, tz=timezone.utc).isoformat() if self.stopped_at else None,
            "completed_at": datetime.fromtimestamp(self.completed_at, tz=timezone.utc).isoformat() if self.completed_at else None,
            "stage_budgets": self.sla_config.get("stage_budgets_seconds", {}),
        }


def enrich_receipt_with_sla_timing(
    receipt_data: Dict[str, Any],
    clock: Optional[PostRecordingSLAClock] = None,
) -> Dict[str, Any]:
    """Enriches receipt payload with episode SLA metrics at root and in execution_timing."""
    if clock is None:
        # Fallback to metadata / receipt timestamps if clock instance not provided
        meta = receipt_data.get("metadata") or {}
        stopped_str = meta.get("stopped_at")
        started_str = receipt_data.get("started_at")
        ended_str = receipt_data.get("ended_at")
        clock = PostRecordingSLAClock()
        if stopped_str:
            clock.record_stop_event(stopped_str)
        run_span = clock.start_run(
            run_id=receipt_data.get("session_id") or "default_run",
            started_at=started_str,
        )
        clock.finish_run(
            run_id=run_span.run_id,
            ended_at=ended_str,
            terminal_outcome=receipt_data.get("terminal_outcome", "INCOMPLETE"),
        )

    metrics = clock.to_dict()
    receipt_data["episode_wall_clock_s"] = metrics["episode_wall_clock_s"]
    receipt_data["inter_run_gap_s"] = metrics["inter_run_gap_s"]
    receipt_data["approval_wait_s"] = metrics["approval_wait_s"]

    exec_timing = receipt_data.setdefault("execution_timing", {})
    exec_timing["sla_metrics"] = metrics
    exec_timing["episode_wall_clock_s"] = metrics["episode_wall_clock_s"]
    exec_timing["inter_run_gap_s"] = metrics["inter_run_gap_s"]
    exec_timing["approval_wait_s"] = metrics["approval_wait_s"]

    return receipt_data
