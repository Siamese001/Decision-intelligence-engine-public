"""Temporal Drift Supersession Agent for Bi-Temporal Knowledge Graph Hygiene.

Executes a bounded Observe -> Decide -> Act -> Evaluate loop (max_steps=2, max_tool_calls=4)
to distinguish chronological state supersession (newer assertion supersedes older assertion
on the same subject + predicate) from true concurrent contradiction (conflicting assertions
within the same time window), emitting propose-only (approval_required=True, auto_apply=False)
maintenance proposals.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
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
from dual_pipeline.core import connect_sqlite


@dataclass(frozen=True)
class SupersessionProposal:
    """Propose-only bi-temporal supersession or contradiction resolution package."""

    proposal_id: str
    subject_id: str
    predicate: str
    action: str  # "supersede_assertion" | "escalate_concurrent_contradiction"
    newer_assertion_id: str
    older_assertion_id: str
    newer_timestamp: str
    older_timestamp: str
    delta_days: int
    rationale: str
    approval_required: bool = True
    auto_apply: bool = False
    status: str = "proposed"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TemporalDriftScanResult:
    """Result of scanning assertions for bi-temporal supersession vs concurrent contradiction."""

    proposals: List[SupersessionProposal]
    supersession_count: int
    concurrent_contradiction_count: int
    db_sha256_unchanged: bool
    steps_executed: int
    tool_calls_used: int
    terminal_outcome: str
    claim_checks: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposals": [p.to_dict() for p in self.proposals],
            "supersession_count": self.supersession_count,
            "concurrent_contradiction_count": self.concurrent_contradiction_count,
            "db_sha256_unchanged": self.db_sha256_unchanged,
            "steps_executed": self.steps_executed,
            "tool_calls_used": self.tool_calls_used,
            "terminal_outcome": self.terminal_outcome,
            "claim_checks": list(self.claim_checks),
        }


def _parse_date(raw_val: Any) -> Optional[datetime]:
    s = str(raw_val or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def _extract_assertion_timestamp(row: Mapping[str, Any]) -> Tuple[str, Optional[datetime]]:
    for key in ("valid_from", "as_of_date", "recorded_at", "source_date", "observed_at"):
        val = row.get(key)
        if val:
            dt = _parse_date(val)
            if dt is not None:
                return str(val), dt
    return "unknown", None


def _load_readonly_assertions(db_path: Path) -> List[Dict[str, Any]]:
    if not db_path.is_file():
        raise FileNotFoundError(f"Database file not found: {db_path}")
    conn = connect_sqlite(db_path, readonly=True)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "assertions" not in tables:
            return []
        return [dict(r) for r in conn.execute("SELECT * FROM assertions").fetchall()]
    finally:
        conn.close()


def _compare_pair_chronology(
    subj: str,
    pred: str,
    first: Mapping[str, Any],
    second: Mapping[str, Any],
    min_days: int,
    idx: int,
) -> SupersessionProposal:
    id_a = str(first.get("assertion_id") or first.get("id") or f"a-{idx}")
    id_b = str(second.get("assertion_id") or second.get("id") or f"b-{idx}")
    raw_a, dt_a = _extract_assertion_timestamp(first)
    raw_b, dt_b = _extract_assertion_timestamp(second)

    if dt_a is not None and dt_b is not None:
        diff = (dt_b - dt_a).days
        if abs(diff) >= min_days:
            if diff > 0:
                newer_id, older_id, newer_ts, older_ts, d_days = id_b, id_a, raw_b, raw_a, diff
            else:
                newer_id, older_id, newer_ts, older_ts, d_days = id_a, id_b, raw_a, raw_b, abs(diff)
            return SupersessionProposal(
                proposal_id=f"prop-supersede-{idx}",
                subject_id=subj,
                predicate=pred,
                action="supersede_assertion",
                newer_assertion_id=newer_id,
                older_assertion_id=older_id,
                newer_timestamp=newer_ts,
                older_timestamp=older_ts,
                delta_days=d_days,
                rationale=f"Assertion {newer_id} ({newer_ts}) chronologically supersedes {older_id} ({older_ts}) by {d_days} days.",
            )

    return SupersessionProposal(
        proposal_id=f"prop-contradiction-{idx}",
        subject_id=subj,
        predicate=pred,
        action="escalate_concurrent_contradiction",
        newer_assertion_id=id_b,
        older_assertion_id=id_a,
        newer_timestamp=raw_b,
        older_timestamp=raw_a,
        delta_days=0,
        rationale=f"Concurrent conflict on ({subj}, {pred}) between {id_a} and {id_b}; requires operator disambiguation.",
    )


class TemporalDriftSupersessionAgent:
    """Detects chronological assertion supersession vs concurrent contradiction."""

    def __init__(
        self,
        *,
        max_steps: int = 2,
        max_tool_calls: int = 4,
        min_supersession_days: int = 1,
        timeout_seconds: float = 20.0,
    ) -> None:
        if max_steps <= 0 or max_steps > 4:
            raise ValueError("max_steps must be within [1, 4].")
        if min_supersession_days <= 0:
            raise ValueError("min_supersession_days must be positive.")
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.min_supersession_days = min_supersession_days
        self.timeout_seconds = timeout_seconds

    def _find_conflicting_groups(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> Tuple[List[Tuple[str, str, List[Mapping[str, Any]]]], List[ClaimCheckPointer]]:
        grouped: Dict[Tuple[str, str], List[Mapping[str, Any]]] = defaultdict(list)
        ptrs: List[ClaimCheckPointer] = []
        for r in rows:
            subj = str(r.get("subject_id") or r.get("subject_node_id") or "").strip()
            pred = str(r.get("predicate") or "").strip().upper()
            if subj and pred:
                grouped[(subj, pred)].append(r)

        conflicts: List[Tuple[str, str, List[Mapping[str, Any]]]] = []
        for (subj, pred), group in grouped.items():
            distinct_vals = {
                str(g.get("object_id") or g.get("object_node_id") or g.get("claim_text") or "").strip()
                for g in group
            }
            if len(group) >= 2 and len(distinct_vals) >= 2:
                conflicts.append((subj, pred, group))
                for g in group[:2]:
                    a_id = str(g.get("assertion_id") or g.get("id") or "asrt")
                    digest = hashlib.sha256(json.dumps(dict(g), sort_keys=True, default=str).encode("utf-8")).hexdigest()
                    ptrs.append(ClaimCheckPointer("src:graph:assertions", a_id, digest, {"assertion_id": a_id}))
        return conflicts, ptrs

    def scan_temporal_drift(
        self,
        *,
        assertions: Optional[Sequence[Mapping[str, Any]]] = None,
        db_path: Optional[Path | str] = None,
        auto_apply: bool = False,
        approval_required: bool = True,
    ) -> TemporalDriftScanResult:
        """Scans assertions and emits propose-only supersession or contradiction packages."""
        if auto_apply or not approval_required:
            raise PermissionError(
                "PROPOSE_ONLY_INVARIANT_VIOLATION: TemporalDriftSupersessionAgent cannot auto-apply mutations."
            )

        before_sha: Optional[str] = None
        raw_rows: List[Mapping[str, Any]] = list(assertions or [])
        if db_path is not None:
            p = Path(db_path)
            before_sha = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
            raw_rows.extend(_load_readonly_assertions(p))

        conflicts, claim_checks = self._find_conflicting_groups(raw_rows)
        proposals: List[SupersessionProposal] = []

        loop = AgentRuntimeLoop(
            RuntimeLoopConfig(
                agent_id="temporal_drift_supersession_agent",
                goal="Classify bi-temporal assertion conflicts into supersession vs contradiction",
                max_steps=self.max_steps,
                max_tool_calls=self.max_tool_calls,
                timeout_seconds=self.timeout_seconds,
                allowed_tools=("inspect_graph_snapshot", "read_assertion_provenance", "verify_contract_compliance"),
            )
        )

        def act_fn(_s: Dict[str, Any], _d: Dict[str, Any], step_idx: int) -> Dict[str, Any]:
            if step_idx == 1:
                task, _ = loop.dispatch_subagent_task(
                    recipient_agent_id="retrieval_specialist",
                    objective="Inspect bi-temporal assertion groups",
                    scope_in=["assertions"],
                    scope_out=["shadow_graph_nodes"],
                    allowed_tools=["read_assertion_provenance"],
                    deliverable={"conflict_groups": len(conflicts)},
                    definition_of_done="All conflicting assertion groups cataloged",
                    claim_checks=claim_checks,
                )
                return {"tool_calls_used": 2, "tokens_used": 90, "a2a_task_id": task.task_id, "state_updates": {"groups": len(conflicts)}}
            for idx, (subj, pred, group) in enumerate(conflicts, start=1):
                proposals.append(_compare_pair_chronology(subj, pred, group[0], group[1], self.min_supersession_days, idx))
            return {"tool_calls_used": 1, "tokens_used": 70, "state_updates": {"proposals": len(proposals)}}

        loop_res = loop.execute(
            initial_state={"groups": 0},
            observe_fn=lambda _s, i: {"step": i, "conflicts": len(conflicts)},
            decide_fn=lambda _o, i: {"action": f"temporal_step_{i}", "delegated": i == 1, "candidates": [(f"temporal_step_{i}", 0.92), ("skip", 0.08)]},
            act_fn=act_fn,
            evaluate_fn=lambda _s, _a, i: {"done": (i == 1 and not conflicts) or i >= 2, "quality_score": 0.5 * i},
        )

        after_sha = hashlib.sha256(Path(db_path).read_bytes()).hexdigest() if (db_path and Path(db_path).is_file()) else None
        sha_ok = (before_sha == after_sha)

        sup_cnt = sum(1 for p in proposals if p.action == "supersede_assertion")
        con_cnt = sum(1 for p in proposals if p.action == "escalate_concurrent_contradiction")
        return TemporalDriftScanResult(
            proposals=proposals,
            supersession_count=sup_cnt,
            concurrent_contradiction_count=con_cnt,
            db_sha256_unchanged=sha_ok,
            steps_executed=loop_res.steps_executed,
            tool_calls_used=loop_res.total_tool_calls,
            terminal_outcome=loop_res.terminal_outcome.value if isinstance(loop_res.terminal_outcome, LoopTerminalOutcome) else str(loop_res.terminal_outcome),
            claim_checks=[c.to_dict() for c in claim_checks],
        )


def compute_empirical_decay_step_seconds(
    scan_result: Optional[TemporalDriftScanResult | Mapping[str, Any]],
    base_step: float = 50.0,
) -> float:
    """Computes a bounded copilot decay_tau_seconds step from empirical supersession intervals."""
    if scan_result is None:
        return float(base_step)
    raw_props = (
        scan_result.proposals
        if isinstance(scan_result, TemporalDriftScanResult)
        else scan_result.get("proposals", [])
    )
    valid_days: List[int] = []
    for p in raw_props:
        act = p.action if isinstance(p, SupersessionProposal) else str(p.get("action", ""))
        d_days = int(p.delta_days if isinstance(p, SupersessionProposal) else p.get("delta_days", 0))
        if act == "supersede_assertion" and d_days > 0:
            valid_days.append(d_days)
    if not valid_days:
        return float(base_step)
    valid_days.sort()
    mid = len(valid_days) // 2
    median_days = (
        float(valid_days[mid])
        if len(valid_days) % 2 == 1
        else 0.5 * (valid_days[mid - 1] + valid_days[mid])
    )
    scaled = float(base_step) * (1.0 + (math.log(1.0 + median_days) / math.log(31.0)))
    return round(max(10.0, min(600.0, scaled)), 2)


TemporalStalenessDriftAgent = TemporalDriftSupersessionAgent

__all__ = [
    "SupersessionProposal",
    "TemporalDriftScanResult",
    "TemporalDriftSupersessionAgent",
    "TemporalStalenessDriftAgent",
    "compute_empirical_decay_step_seconds",
]
