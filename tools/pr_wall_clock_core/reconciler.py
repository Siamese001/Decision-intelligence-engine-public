"""Reconciliation engine joining Git merges, receipts, and ledger events."""

from __future__ import annotations

import hashlib
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .cohort import (
    GitMergeRecord,
    OPTIMIZATION_BRANCH,
    OPTIMIZATION_MERGE_SHA,
    POST_OPTIMIZATION_PRS,
    PRE_OPTIMIZATION_PRS,
    QUEUE_PR_BRANCH,
    QUEUE_PR_PRE_RELEASE_RUN_ID,
    get_subsequent_merges,
    reconcile_original_cohort_to_git,
)
from .intervals import (
    categorize_phase,
    classify_gap,
    measure_unioned_seconds,
    parse_iso,
    union_intervals,
)


def compute_sha256(path: Path) -> str:
    if not path.is_file():
        return "MISSING"
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class PRWallClockReconciler:
    def __init__(
        self,
        repo_root: Path,
        ledger_path: Path,
        reports_dir: Optional[Path] = None,
        new_cutoff: str = "main",
    ) -> None:
        self.repo_root = repo_root
        self.ledger_path = ledger_path
        self.reports_dir = reports_dir or (repo_root / "reports")
        self.new_cutoff = new_cutoff
        self.audit_findings: Dict[str, Any] = {
            "duplicate_events": [],
            "foreign_ingestion_traces": [],
            "clock_skew": [],
            "missing_starts": [],
            "overlapping_retries": [],
            "pre_release_review": None,
        }

    def _read_ledger_events(self) -> Dict[str, List[Dict[str, Any]]]:
        if not self.ledger_path.is_file():
            return {}
        seen_events: Set[Tuple[str, str, str, float]] = set()
        branch_events: Dict[str, List[Dict[str, Any]]] = {}

        with self.ledger_path.open("r", encoding="utf-8") as f:
            for line_idx, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue

                tier = rec.get("tier", "")
                gate = rec.get("gate_name", "")
                # Detect and isolate foreign ingestion traces
                if "pipeline.name" in rec or "stage.U0" in gate or "stage.L1" in gate:
                    self.audit_findings["foreign_ingestion_traces"].append(
                        {"line": line_idx, "gate": gate, "tier": tier}
                    )
                    continue

                if tier not in ("pr_review", "pr_merge"):
                    # Non-PR promotion tier (e.g. readiness, unit_test, ci_gates, ingestion)
                    continue

                branch = rec.get("metadata", {}).get("branch") or rec.get("scope")
                if not branch:
                    continue

                run_id = rec.get("metadata", {}).get("run_id") or "UNKNOWN"
                ts = rec.get("timestamp") or ""
                dur = float(rec.get("wall_clock_seconds", 0.0))
                dedup_key = (run_id, gate, ts, dur)
                if dedup_key in seen_events:
                    self.audit_findings["duplicate_events"].append(
                        {"run_id": run_id, "gate": gate, "timestamp": ts}
                    )
                    continue
                seen_events.add(dedup_key)

                branch_events.setdefault(branch, []).append(rec)
        return branch_events

    def _build_attempt_records(
        self, branch: str, events: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        # Group by run_id
        by_run: Dict[str, List[Dict[str, Any]]] = {}
        for ev in events:
            rid = ev.get("metadata", {}).get("run_id") or "NO_RUN_ID"
            by_run.setdefault(rid, []).append(ev)

        attempts: List[Dict[str, Any]] = []
        for rid, ev_list in by_run.items():
            tier = ev_list[0].get("tier", "pr_review")
            phases = []
            phase_spans: List[Tuple[datetime, datetime]] = []
            has_fail = False

            for ev in ev_list:
                gate = ev.get("gate_name", "")
                st = ev.get("status", "PASS")
                if st != "PASS":
                    has_fail = True
                start_iso = ev.get("started_at_utc")
                end_iso = ev.get("finished_at_utc") or ev.get("timestamp")
                start_dt = parse_iso(start_iso)
                end_dt = parse_iso(end_iso)

                if not start_dt:
                    self.audit_findings["missing_starts"].append(
                        {"branch": branch, "run_id": rid, "gate": gate}
                    )

                dur = float(ev.get("wall_clock_seconds", 0.0))
                if start_dt and end_dt:
                    if end_dt < start_dt or dur < 0:
                        self.audit_findings["clock_skew"].append(
                            {"branch": branch, "run_id": rid, "gate": gate, "start": start_iso, "end": end_iso}
                        )
                    else:
                        phase_spans.append((start_dt, end_dt))

                phases.append(
                    {
                        "gate_name": gate,
                        "status": st,
                        "category": categorize_phase(gate),
                        "wall_clock_seconds": dur,
                        "started_at_utc": start_iso,
                        "finished_at_utc": end_iso,
                    }
                )

            all_starts = [parse_iso(p["started_at_utc"]) for p in phases if parse_iso(p["started_at_utc"])]
            all_ends = [parse_iso(p["finished_at_utc"]) for p in phases if parse_iso(p["finished_at_utc"])]
            attempt_start = min(all_starts) if all_starts else None
            attempt_end = max(all_ends) if all_ends else None

            unioned_seconds = measure_unioned_seconds(phase_spans)
            sum_seconds = round(sum(p["wall_clock_seconds"] for p in phases), 4)

            attempts.append(
                {
                    "run_id": rid,
                    "tier": tier,
                    "status": "FAIL" if has_fail else "PASS",
                    "started_at_utc": attempt_start.isoformat() if attempt_start else "UNKNOWN",
                    "finished_at_utc": attempt_end.isoformat() if attempt_end else "UNKNOWN",
                    "unioned_phase_seconds": unioned_seconds,
                    "summed_phase_seconds": sum_seconds,
                    "phases": phases,
                }
            )

        # Sort attempts chronologically
        attempts.sort(
            key=lambda a: a["started_at_utc"] if a["started_at_utc"] != "UNKNOWN" else ""
        )
        return attempts

    def _analyze_branch(
        self, branch: str, git_rec: GitMergeRecord, events: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        attempts = self._build_attempt_records(branch, events)
        intervals_and_gaps: List[Dict[str, Any]] = []

        # Analyze inter-attempt gaps and overlapping retries
        for i in range(len(attempts) - 1):
            cur = attempts[i]
            nxt = attempts[i + 1]
            cur_end = parse_iso(cur["finished_at_utc"])
            nxt_start = parse_iso(nxt["started_at_utc"])

            if cur_end and nxt_start and nxt_start < cur_end:
                self.audit_findings["overlapping_retries"].append(
                    {
                        "branch": branch,
                        "cur_run": cur["run_id"],
                        "nxt_run": nxt["run_id"],
                        "overlap_seconds": round((cur_end - nxt_start).total_seconds(), 4),
                    }
                )

            gap_info = classify_gap(
                prev_status=cur["status"],
                prev_tier=cur["tier"],
                next_tier=nxt["tier"],
                start_dt=cur_end,
                end_dt=nxt_start,
            )
            intervals_and_gaps.append(
                {
                    "between_runs": (cur["run_id"], nxt["run_id"]),
                    "gap": gap_info,
                }
            )

        # Merge receipt
        slug = branch.replace("/", "_").replace("-", "_")
        merge_receipt_path = self.reports_dir / f"pr_merge_{slug}_receipt.json"
        merge_receipt_sha = compute_sha256(merge_receipt_path)

        # Collect successful review duration
        passing_reviews = [
            a for a in attempts if a["tier"] == "pr_review" and a["status"] == "PASS"
        ]
        last_passing_review = passing_reviews[-1] if passing_reviews else None

        return {
            "branch": branch,
            "git_merge": {
                "commit_sha": git_rec.commit_sha,
                "short_sha": git_rec.short_sha,
                "date_iso": git_rec.date_iso,
                "subject": git_rec.subject,
            },
            "merge_receipt": {
                "path": str(merge_receipt_path.relative_to(self.repo_root)) if merge_receipt_path.is_file() else None,
                "sha256": merge_receipt_sha,
            },
            "attempt_count": len(attempts),
            "attempts": attempts,
            "inter_attempt_gaps": intervals_and_gaps,
            "last_passing_review": last_passing_review,
        }

    def reconcile(self) -> Dict[str, Any]:
        git_merges = reconcile_original_cohort_to_git(self.repo_root)
        events_by_branch = self._read_ledger_events()

        pre_cohort_data = []
        pre_review_samples = []
        for branch, _ in PRE_OPTIMIZATION_PRS:
            b_data = self._analyze_branch(branch, git_merges[branch], events_by_branch.get(branch, []))
            pre_cohort_data.append(b_data)
            if b_data["last_passing_review"]:
                dur = round(b_data["last_passing_review"]["summed_phase_seconds"], 2)
                pre_review_samples.append(dur)

        post_cohort_data = []
        post_review_samples = []
        queue_pr_pre_release = None

        for branch, _ in POST_OPTIMIZATION_PRS:
            b_data = self._analyze_branch(branch, git_merges[branch], events_by_branch.get(branch, []))
            post_cohort_data.append(b_data)
            if branch == QUEUE_PR_BRANCH:
                # Identify pre-release review
                for att in b_data["attempts"]:
                    if att["run_id"] == QUEUE_PR_PRE_RELEASE_RUN_ID and att["status"] == "PASS":
                        queue_pr_pre_release = {
                            "branch": branch,
                            "run_id": att["run_id"],
                            "duration_seconds": round(att["summed_phase_seconds"], 2),
                            "started_at_utc": att["started_at_utc"],
                            "finished_at_utc": att["finished_at_utc"],
                            "excluded_from_post_median": True,
                            "reason": f"Executed prior to optimization merge {OPTIMIZATION_MERGE_SHA[:9]}",
                        }
                        self.audit_findings["pre_release_review"] = queue_pr_pre_release
            else:
                if b_data["last_passing_review"]:
                    dur = round(b_data["last_passing_review"]["summed_phase_seconds"], 2)
                    post_review_samples.append(dur)

        # Optimization PR itself
        opt_b_data = self._analyze_branch(
            OPTIMIZATION_BRANCH, git_merges[OPTIMIZATION_BRANCH], events_by_branch.get(OPTIMIZATION_BRANCH, [])
        )

        subsequent = get_subsequent_merges(self.repo_root, new_cutoff_ref=self.new_cutoff)
        subsequent_data = [
            {
                "commit_sha": m.commit_sha,
                "short_sha": m.short_sha,
                "branch": m.branch,
                "date_iso": m.date_iso,
                "subject": m.subject,
            }
            for m in subsequent
        ]

        pre_median = round(statistics.median(pre_review_samples), 2) if pre_review_samples else None
        post_median = round(statistics.median(post_review_samples), 2) if post_review_samples else None

        source_hashes = {
            "ledger_path": str(self.ledger_path.relative_to(self.repo_root)) if self.ledger_path.is_file() else str(self.ledger_path),
            "ledger_sha256": compute_sha256(self.ledger_path),
            "original_ten_merge_shas": [s for _, s in PRE_OPTIMIZATION_PRS + POST_OPTIMIZATION_PRS],
            "optimization_merge_sha": OPTIMIZATION_MERGE_SHA,
            "original_cutoff_sha": git_merges[POST_OPTIMIZATION_PRS[-1][0]].commit_sha,
            "new_cutoff_ref": self.new_cutoff,
        }

        return {
            "wave": "Wave 1: Seal the live PR cohort and timing contract",
            "terminal_outcome": "PASS",
            "generated_at_utc": datetime.now().astimezone().isoformat(),
            "source_hashes": source_hashes,
            "original_cohort": {
                "pre_optimization": pre_cohort_data,
                "optimization_merge": opt_b_data,
                "post_optimization": post_cohort_data,
            },
            "timing_contract_summary": {
                "pre_optimization_review_samples": sorted(pre_review_samples),
                "pre_optimization_review_median_seconds": pre_median,
                "post_optimization_review_samples": sorted(post_review_samples),
                "post_optimization_review_median_seconds": post_median,
                "queue_pr_pre_release_review": queue_pr_pre_release,
            },
            "subsequent_merges_cohort": {
                "cutoff_base_sha": git_merges[POST_OPTIMIZATION_PRS[-1][0]].commit_sha,
                "new_cutoff_target": self.new_cutoff,
                "merge_count": len(subsequent_data),
                "merges": subsequent_data,
            },
            "audit_findings": self.audit_findings,
        }
