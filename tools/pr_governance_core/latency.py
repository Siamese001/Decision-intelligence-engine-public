"""Wall-clock telemetry for governed PR review and promotion lifecycles."""

from __future__ import annotations

import contextlib
import datetime as dt
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Generator, Optional, Sequence

PROMOTION_PHASES: Sequence[str] = (
    "instruction_intake",
    "preparation",
    "commit_hooks",
    "mainline_sync",
    "readiness",
    "review",
    "lock_wait",
    "merge",
    "verification",
    "cleanup",
)



class PromotionTimeline:
    """Collect non-overlapping PR promotion phases and mirror them to the local ledger."""

    def __init__(
        self,
        branch: str,
        repo_root: Path,
        retry: int = 0,
        run_id: Optional[str] = None,
        tier: str = "pr_promotion",
    ) -> None:
        self.branch = branch
        self.repo_root = repo_root
        self.retry = max(0, retry)
        self.tier = tier
        prefix = "pr-promo" if tier == "pr_promotion" else "pr-review"
        self.run_id = run_id or f"{prefix}-{uuid.uuid4().hex[:12]}"
        self._start_monotonic = time.monotonic()
        self.started_at_utc = dt.datetime.now(dt.timezone.utc).isoformat()
        self._checkpoint_monotonic = self._start_monotonic
        self._checkpoint_utc = self.started_at_utc
        self.phases: list[Dict[str, Any]] = []
        self.tagged_intervals: Dict[str, float] = {
            "human_wait": 0.0,
            "repairs": 0.0,
            "cleanup": 0.0,
        }

    def checkpoint(
        self,
        name: str,
        *,
        scope: str,
        status: str = "PASS",
        cache_decision: str = "disabled",
        metadata: Dict[str, Any] | None = None,
        interval_type: Optional[str] = None,
    ) -> None:
        """Close the next sequential phase so phase durations never overlap."""
        if name in ("rebase", "mainline_sync_precheck"):
            name = "mainline_sync"
        now_monotonic = time.monotonic()
        finished_at = dt.datetime.now(dt.timezone.utc).isoformat()
        duration = round(max(0.0, now_monotonic - self._checkpoint_monotonic), 4)
        event = {
            "phase": name,
            "scope": scope,
            "status": status.upper(),
            "cache_decision": cache_decision,
            "retry": self.retry,
            "started_at_utc": self._checkpoint_utc,
            "finished_at_utc": finished_at,
            "wall_clock_seconds": duration,
            "metadata": metadata or {},
        }
        if interval_type and interval_type in self.tagged_intervals:
            self.tagged_intervals[interval_type] = round(
                self.tagged_intervals[interval_type] + duration, 4
            )
        elif name == "cleanup":
            self.tagged_intervals["cleanup"] = round(
                self.tagged_intervals["cleanup"] + duration, 4
            )

        self.phases.append(event)
        self._record(event)
        self._checkpoint_monotonic = now_monotonic
        self._checkpoint_utc = finished_at

    @contextlib.contextmanager
    def phase(
        self,
        name: str,
        *,
        scope: str,
        cache_decision: str = "disabled",
        interval_type: Optional[str] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        if name == "rebase":
            name = "mainline_sync_precheck"
        started_monotonic = time.monotonic()

        started_at = dt.datetime.now(dt.timezone.utc).isoformat()
        state: Dict[str, Any] = {"status": "PASS", "metadata": {}}
        try:
            yield state
        except Exception:
            state["status"] = "FAIL"
            raise
        finally:
            now_monotonic = time.monotonic()
            duration = round(max(0.0, now_monotonic - started_monotonic), 4)
            finished_at = dt.datetime.now(dt.timezone.utc).isoformat()
            event = {
                "phase": name,
                "scope": scope,
                "status": str(state["status"]).upper(),
                "cache_decision": cache_decision,
                "retry": self.retry,
                "started_at_utc": started_at,
                "finished_at_utc": finished_at,
                "wall_clock_seconds": duration,
                "metadata": dict(state["metadata"]),
            }
            if interval_type and interval_type in self.tagged_intervals:
                self.tagged_intervals[interval_type] = round(
                    self.tagged_intervals[interval_type] + duration, 4
                )
            elif name == "cleanup":
                self.tagged_intervals["cleanup"] = round(
                    self.tagged_intervals["cleanup"] + duration, 4
                )
            self.phases.append(event)
            self._record(event)
            self._checkpoint_monotonic = now_monotonic
            self._checkpoint_utc = finished_at

    def tag_interval(self, category: str, duration_seconds: float) -> None:
        """Tag an external interval like human wait or repairs without double-counting."""
        valid_cat = category if category in self.tagged_intervals else "repairs"
        dur = round(max(0.0, float(duration_seconds)), 4)
        self.tagged_intervals[valid_cat] = round(self.tagged_intervals[valid_cat] + dur, 4)

    def _record(self, event: Dict[str, Any]) -> None:
        try:
            from tools.gate_run_ledger import record_gate_run

            record_gate_run(
                gate_name=event["phase"],
                tier=self.tier,
                wall_clock_seconds=event["wall_clock_seconds"],
                status=event["status"],
                cache_hit=event["cache_decision"] in ("hit", "receipt_available_revalidated"),
                cache_decision=event["cache_decision"],
                parent_phase=self.tier,
                scope=event["scope"],
                retry=event["retry"],
                started_at_utc=event["started_at_utc"],
                metadata={"run_id": self.run_id, "branch": self.branch, **event["metadata"]},
                repo_root=self.repo_root,
            )
        except Exception:
            pass

    def finish(self, terminal_outcome: str = "PASS") -> Dict[str, Any]:
        finished_at = dt.datetime.now(dt.timezone.utc).isoformat()
        wall_clock = round(time.monotonic() - self._start_monotonic, 4)
        accounted = round(sum(float(p["wall_clock_seconds"]) for p in self.phases), 4)
        phase_breakdown: Dict[str, float] = {}
        for p in self.phases:
            p_name = p["phase"]
            phase_breakdown[p_name] = round(
                phase_breakdown.get(p_name, 0.0) + float(p["wall_clock_seconds"]), 4
            )

        return {
            "run_id": self.run_id,
            "branch": self.branch,
            "tier": self.tier,
            "terminal_outcome": terminal_outcome.upper(),
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": finished_at,
            "wall_clock_seconds": wall_clock,
            "accounted_phase_seconds": accounted,
            "unaccounted_seconds": round(max(0.0, wall_clock - accounted), 4),
            "retry": self.retry,
            "phases": self.phases,
            "phase_breakdown": phase_breakdown,
            "tagged_intervals": dict(self.tagged_intervals),
        }


# Backward-compatible alias
class ReviewTimeline(PromotionTimeline):
    """Backward-compatible wrapper for PR review runs."""

    def __init__(self, branch: str, repo_root: Path, retry: int = 0) -> None:
        super().__init__(branch=branch, repo_root=repo_root, retry=retry, tier="pr_review")
