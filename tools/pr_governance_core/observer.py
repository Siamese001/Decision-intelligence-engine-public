"""tools/pr_governance_core/observer.py

Read-Only Antigravity PR Queue Observer Engine (Wave 6).
Provides single-run process locking, isolated report destinations, immutable run identities,
atomic last-accepted pointers, sleeping-host/missed-run tolerance, and notification deduplication.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]

from tools.pr_governance_core.decision_api import evaluate_queue_decisions
from tools.pr_governance_core.queue_snapshot import (
    QueueSnapshot,
    collect_queue_snapshot,
)
from tools.pr_governance_core.snapshot_history import compute_rank_transitions
from .observer_lock import ObserverLockCollisionError, ObserverLockInfo, acquire_observer_lock, is_pid_alive
from .observer_artifacts import recent_merges, seal_manifest, write_exclusive, write_pointer
from .render import render_pr_queue_observer_table


def compute_snapshot_digest(snapshot_data: Dict[str, Any]) -> str:
    """Computes a stable SHA-256 digest of canonical snapshot dictionary."""
    serialized = json.dumps(snapshot_data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_failure_signature(errors: List[str], completeness_status: str) -> Optional[str]:
    """Generates a normalized signature for collection or runtime failure states."""
    if not errors and completeness_status == "COMPLETE":
        return None
    sorted_errs = sorted(errors)
    payload = f"{completeness_status}::" + "::".join(sorted_errs)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class NotificationAssessment:
    notify: bool
    terminal_outcome: str  # "NOTIFICATION_EMITTED", "NO_NOTIFICATION", "COLLECTION_FAILURE_ALERT"
    reason: str
    transitions: List[Dict[str, Any]] = field(default_factory=list)
    new_work: List[Dict[str, Any]] = field(default_factory=list)
    progress_alerts: List[str] = field(default_factory=list)
    collection_errors: List[str] = field(default_factory=list)
    failure_signature: Optional[str] = field(default=None)


def _handle_error_notification(
    curr_failure_sig: Optional[str],
    prev_failure_sig: Optional[str],
    current_errors: List[str],
    completeness_status: str,
) -> Optional[NotificationAssessment]:
    if curr_failure_sig is None:
        return None
    if curr_failure_sig == prev_failure_sig:
        return NotificationAssessment(
            notify=False,
            terminal_outcome="NO_NOTIFICATION",
            reason=f"Repeated unchanged collection failure signature ({curr_failure_sig}) suppressed",
            collection_errors=current_errors,
            failure_signature=curr_failure_sig,
        )
    return NotificationAssessment(
        notify=True,
        terminal_outcome="COLLECTION_FAILURE_ALERT",
        reason=f"Collection failure or incomplete snapshot detected: {', '.join(current_errors) or completeness_status}",
        collection_errors=current_errors,
        failure_signature=curr_failure_sig,
    )


def _detect_progress_discrepancies(
    curr_branches: List[Dict[str, Any]],
    prev_branches: List[Dict[str, Any]],
) -> List[str]:
    prev_map = {b["branch"]: b for b in prev_branches if "branch" in b}
    alerts: List[str] = []
    for cb in curr_branches:
        b_name = cb.get("branch", "")
        if b_name not in prev_map:
            continue
        pb = prev_map[b_name]
        p_comp = str(pb.get("completed_waves", "—"))
        c_comp = str(cb.get("completed_waves", "—"))
        if p_comp not in ("—", "") and c_comp in ("—", ""):
            alerts.append(f"Lost progress on {b_name}: previously completed [{p_comp}], now [{c_comp}]")
        if pb.get("open_waves") == "—" and cb.get("open_waves") not in ("—", ""):
            alerts.append(f"Wave reopened on {b_name}: open waves [{cb.get('open_waves')}]")
    return alerts


def _detect_operator_interventions(
    curr_branches: List[Dict[str, Any]],
    cycles: List[Any],
) -> List[str]:
    reasons: List[str] = []
    if cycles:
        reasons.append(f"{len(cycles)} dependency cycle(s) detected")
    hitl_branches = [b["branch"] for b in curr_branches if "HITL" in b.get("readiness", "")]
    if hitl_branches:
        reasons.append(f"HITL operator review required for {', '.join(hitl_branches)}")
    return reasons


def assess_notification(
    current_decisions: Dict[str, Any],
    last_accepted: Optional[Dict[str, Any]],
    current_errors: List[str],
    completeness_status: str,
) -> NotificationAssessment:
    """Evaluates whether to emit an active notification or suppress redundant noise."""
    curr_branches = current_decisions.get("branches", [])
    prev_branches = (last_accepted or {}).get("branches", [])
    prev_failure_sig = (last_accepted or {}).get("failure_signature")
    curr_failure_sig = compute_failure_signature(current_errors, completeness_status)

    err_assessment = _handle_error_notification(curr_failure_sig, prev_failure_sig, current_errors, completeness_status)
    if err_assessment is not None:
        return err_assessment

    progress_alerts = _detect_progress_discrepancies(curr_branches, prev_branches)
    transitions = compute_rank_transitions(prev_branches, curr_branches)
    new_work = [t.to_dict() for t in transitions if t.previous_position is None]
    meaningful_transitions = [
        t.to_dict() for t in transitions
        if t.previous_position is not None and (t.previous_tier != t.current_tier or t.changed_evidence)
    ]

    if progress_alerts:
        return NotificationAssessment(
            notify=True, terminal_outcome="NOTIFICATION_REQUESTED",
            reason=f"Progress discrepancy detected: {'; '.join(progress_alerts)}",
            transitions=[t.to_dict() for t in transitions], new_work=new_work, progress_alerts=progress_alerts,
        )

    interventions = _detect_operator_interventions(curr_branches, current_decisions.get("cycles", []))
    if interventions:
        return NotificationAssessment(
            notify=True, terminal_outcome="NOTIFICATION_REQUESTED",
            reason=f"Operator intervention required: {'; '.join(interventions)}",
            transitions=[t.to_dict() for t in transitions], new_work=new_work,
        )

    if meaningful_transitions or new_work:
        summary_reasons = []
        if new_work:
            summary_reasons.append(f"{len(new_work)} new item(s) discovered")
        if meaningful_transitions:
            summary_reasons.append(f"{len(meaningful_transitions)} ranking transition(s)")
        return NotificationAssessment(
            notify=True, terminal_outcome="NOTIFICATION_REQUESTED",
            reason="; ".join(summary_reasons),
            transitions=[t.to_dict() for t in transitions], new_work=new_work,
        )

    return NotificationAssessment(
        notify=False, terminal_outcome="NO_NOTIFICATION",
        reason="No meaningful changes detected; queue state identical to last accepted observation",
        transitions=[], new_work=[],
    )


@dataclass
class ObserverRunReceipt:
    run_id: str
    observed_at: str
    observed_at_local: str
    snapshot_digest: str
    hours_since_last_observation: Optional[float]
    missed_interval_hours: Optional[float]
    is_stale_snapshot: bool
    assessment: NotificationAssessment
    last_accepted_digest_prior: Optional[str]
    last_accepted_digest_after: Optional[str]
    mode: str = "scheduled"
    observation_outcome: str = "UNKNOWN"
    notification_status: str = "UNVERIFIED"
    table_markdown: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["assessment"] = asdict(self.assessment)
        return d


def _mode_state_paths(out_dir: Path, mode: str) -> tuple[Path, Path]:
    prefix = "scheduled" if mode == "scheduled" else "interactive"
    return out_dir / f"{prefix}_baseline.json", out_dir / f"{prefix}_failure_history.json"


def _read_json_state(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def _load_comparison_state(baseline_file: Path, failure_file: Path, now: datetime) -> tuple[Optional[Dict[str, Any]], Optional[str], Optional[float], Optional[float], Optional[Dict[str, Any]]]:
    baseline = _read_json_state(baseline_file)
    last_accepted_file = baseline_file.parent / "last_accepted_snapshot.json"
    if last_accepted_file.exists():
        last_accepted = _read_json_state(last_accepted_file)
        if last_accepted:
            if not baseline:
                baseline = last_accepted
            else:
                la_obs = last_accepted.get("observed_at")
                b_obs = baseline.get("observed_at")
                if la_obs and b_obs:
                    try:
                        la_dt = datetime.fromisoformat(la_obs)
                        b_dt = datetime.fromisoformat(b_obs)
                        if la_dt > b_dt:
                            baseline = last_accepted
                    except (ValueError, TypeError):
                        pass
                elif la_obs and not b_obs:
                    baseline = last_accepted
    failure = _read_json_state(failure_file)
    digest = (baseline or {}).get("snapshot_digest")
    observed_at = (baseline or {}).get("observed_at")
    hours = (now - datetime.fromisoformat(observed_at)).total_seconds() / 3600 if observed_at else None
    missed = hours if hours and hours > 24 else None
    return baseline, digest, hours, missed, failure


def _assess_mode(snap: QueueSnapshot, decisions: Dict[str, Any], prior: Optional[Dict[str, Any]], failure: Optional[Dict[str, Any]], mode: str, force_notify: bool) -> NotificationAssessment:
    previous = {**(prior or {}), "failure_signature": (failure or {}).get("failure_signature")}
    assessment = assess_notification(decisions, previous, snap.errors, snap.completeness_status)
    if force_notify:
        assessment.notify, assessment.terminal_outcome = True, "NOTIFICATION_REQUESTED"
    if mode == "interactive":
        assessment.notify = False
        assessment.terminal_outcome = "NO_NOTIFICATION"
        assessment.reason = "Interactive observation; scheduled notification history is untouched"
    return assessment


def _run_outcome(snap: QueueSnapshot) -> tuple[bool, str, str]:
    complete = snap.completeness_status == "COMPLETE" and not snap.errors
    outcome = "BLOCKED" if snap.completeness_status == "INCONSISTENT" else "PARTIAL"
    return complete, outcome, "LAYOUT_UNVERIFIED"


def _table_summary(repo_root: Path, snap: QueueSnapshot, decisions: Dict[str, Any], mode: str, outcome: str, notification: str, run_id: str, runs_dir: Path) -> Dict[str, Any]:
    physical_dir = repo_root / ".worktrees"
    physical = [p.resolve() for p in physical_dir.iterdir() if not p.name.startswith(".")] if physical_dir.is_dir() else []
    observed = {Path(worktree.path).resolve() for worktree in snap.worktrees}
    covered = sum(path in observed for path in physical)
    branches = decisions.get("branches", [])
    active = sum(item.get("tier") not in {"W", "D", "A"} for item in branches)
    return {
        "mode": mode.upper(), "observed_at": "", "branch_count": active, "entry_count": len(branches),
        "outcome": outcome, "notification": notification, "layout": "LAYOUT_UNVERIFIED",
        "detail": "; ".join(snap.errors) or "layout not qualified in Antigravity host",
        "git_state": f"Base main@{snap.base_sha or 'UNKNOWN'}; worktrees {covered}/{len(physical)}; stashes {len(snap.stashes)}/{len(snap.stashes) if not snap.errors else 'UNKNOWN'}",
        "next_action": "Verify table layout in the Antigravity host",
        "bundle_url": "file://" + str((runs_dir / run_id / "manifest.json").absolute()),
    }


def _movements(decisions: Dict[str, Any], prior: Optional[Dict[str, Any]]) -> Dict[str, str]:
    previous = {item.get("branch"): item.get("sub_rank") for item in (prior or {}).get("branches", [])}
    result = {}
    for item in decisions.get("branches", []):
        branch, rank = item.get("branch", ""), item.get("sub_rank")
        result[branch] = "NEW" if branch not in previous else ("UNCHANGED" if previous[branch] == rank else f"{previous[branch]} to {rank}")
    return result


def _make_receipt(snap: QueueSnapshot, decisions: Dict[str, Any], assessment: NotificationAssessment, summary: Dict[str, Any], table: str, mode: str, run_id: str, now: datetime, local: str, digest: str, prior_digest: Optional[str], hours: Optional[float], missed: Optional[float], stale: bool, complete: bool) -> ObserverRunReceipt:
    return ObserverRunReceipt(
        run_id, now.isoformat(), local, digest, hours, missed, stale, assessment, prior_digest,
        digest if complete else prior_digest, mode, summary["outcome"], summary["notification"], table,
    )


def _seal_run(out_dir: Path, runs_dir: Path, snap: QueueSnapshot, decisions: Dict[str, Any], summary: Dict[str, Any], receipt: ObserverRunReceipt, council_result: Optional[Any] = None) -> None:
    run_dir = runs_dir / receipt.run_id
    run_dir.mkdir(exist_ok=False)
    write_exclusive(run_dir / "snapshot.json", snap.to_dict())
    write_exclusive(run_dir / "decisions.json", decisions)
    details = {"coverage": summary["git_state"], "errors": snap.errors, "branches": decisions.get("branches", []), "stashes": snap.to_dict().get("stashes", []), "worktrees": snap.to_dict().get("worktrees", [])}
    write_exclusive(run_dir / "details.json", details)
    write_exclusive(run_dir / "table.md", receipt.table_markdown, json_data=False)
    write_exclusive(run_dir / "receipt.json", receipt.to_dict())
    manifest = ["snapshot.json", "decisions.json", "details.json", "table.md", "receipt.json"]
    if council_result is not None:
        write_exclusive(run_dir / "council.json", council_result.to_dict())
        manifest.append("council.json")
    seal_manifest(run_dir, tuple(manifest))
    write_exclusive(runs_dir / f"{receipt.run_id}.json", receipt.to_dict())


def _advance_mode_state(out_dir: Path, baseline_file: Path, failure_file: Path, mode: str, snap: QueueSnapshot, decisions: Dict[str, Any], assessment: NotificationAssessment, receipt: ObserverRunReceipt, complete: bool) -> None:
    if complete:
        accepted = {"run_id": receipt.run_id, "snapshot_digest": receipt.snapshot_digest, "observed_at": receipt.observed_at, "branches": decisions.get("branches", []), "failure_signature": None}
        write_pointer(baseline_file, accepted)
        if mode == "scheduled":
            write_pointer(out_dir / "last_accepted_snapshot.json", accepted)
        failure_file.unlink(missing_ok=True)
    elif assessment.failure_signature:
        failure = {"run_id": receipt.run_id, "failure_signature": assessment.failure_signature, "updated_at": receipt.observed_at}
        write_pointer(failure_file, failure)


def run_observation(
    repo_root: Path,
    output_dir: Optional[Path] = None,
    snapshot: Optional[QueueSnapshot] = None,
    stale_threshold_hours: float = 6.0,
    force_notify: bool = False,
    mode: str = "scheduled",
) -> ObserverRunReceipt:
    """Collect, decide, render, and seal one isolated observer run."""
    if mode not in {"interactive", "scheduled"}:
        raise ValueError(f"Unsupported observer mode: {mode}")
    out_dir = output_dir or (repo_root / ".local" / "pr_queue_observer")
    out_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = out_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    baseline_file, failure_file = _mode_state_paths(out_dir, mode)
    with acquire_observer_lock(out_dir / "observer.lock"):
        snap = snapshot or collect_queue_snapshot(repo_root=repo_root)
        now, digest = datetime.now(timezone.utc), compute_snapshot_digest(snap.to_dict())
        run_id = f"{now:%Y%m%dT%H%M%SZ}_{digest[:8]}_{uuid4().hex[:8]}"
        local = now.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        stale_at = datetime.fromisoformat(snap.observed_at) if snap.observed_at else now
        stale = (now - stale_at).total_seconds() / 3600 > stale_threshold_hours
        prior, prior_digest, hours, missed, failure = _load_comparison_state(baseline_file, failure_file, now)
        decisions = evaluate_queue_decisions(snap, repo_root=repo_root).to_dict()
        from tools.pr_governance_core.council import deliberate_council_queue
        council_result = deliberate_council_queue(decisions.get("branches", []))
        assessment = _assess_mode(snap, decisions, prior, failure, mode, force_notify)
        complete, outcome, layout = _run_outcome(snap)
        notification = "REQUESTED" if mode == "scheduled" and assessment.notify else "SUPPRESSED"
        if complete:
            outcome = "PARTIAL" if layout != "VERIFIED" else "COMPLETE"
        summary = _table_summary(repo_root, snap, decisions, mode, outcome, notification, run_id, runs_dir)
        summary["observed_at"] = f"{local} ({now.strftime('%Z') or 'UTC'})"
        from .detail_artifact import generate_pr_queue_detail_artifact
        detail_path = repo_root / "reports" / "pr_queue_detail.json"
        generate_pr_queue_detail_artifact(repo_root, decisions.get("branches", []), target_path=detail_path)
        table = render_pr_queue_observer_table(decisions.get("branches", []), summary, _movements(decisions, prior), recent_merges(repo_root), detail_path=detail_path)
        receipt = _make_receipt(snap, decisions, assessment, summary, table, mode, run_id, now, local, digest, prior_digest, hours, missed, stale, complete)
        _seal_run(out_dir, runs_dir, snap, decisions, summary, receipt, council_result)
        _advance_mode_state(out_dir, baseline_file, failure_file, mode, snap, decisions, assessment, receipt, complete)
        return receipt

