#!/usr/bin/env python3
"""AI-DLC KPI Report Generator.

Computes git-derived development-lifecycle efficiency metrics over a specified commit
window or range (e.g., 9763cd1f~100..9763cd1f). Outputs structured JSON receipts
and supports command-line metric querying and comparative diffs against baseline receipts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REWORK_REGEX = re.compile(
    r"satisfy|lint|complexity|budget|ruff|whitespace|align (test|mock)|readiness|compact|ratchet",
    re.IGNORECASE,
)

SEAL_REGEX = re.compile(
    r"seal.*receipt|finalize.*receipt|add.*receipt|update.*receipt|receipt.*lock|allow matching recent branch ancestors|enhance readiness receipt",
    re.IGNORECASE,
)

CONVENTIONAL_REGEX = re.compile(r"^([a-z]+)(\([^)]+\))?!?:", re.IGNORECASE)


def run_git(cmd: List[str], cwd: Path) -> str:
    """Runs a git command and returns stdout stripped."""
    res = subprocess.run(["git"] + cmd, cwd=cwd, capture_output=True, text=True, check=True)
    return res.stdout.strip()


def parse_commits_batch(
    commit_range: Optional[str] = None,
    count: Optional[int] = None,
    first_parent: bool = False,
    cwd: Optional[Path] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Tuple[int, int, int]], Dict[str, List[str]]]:
    """Retrieves commit metadata and file diff stats in a single high-efficiency git call."""
    root = cwd or ROOT
    # Record delimiter to safely separate commits and numstat
    commit_token = "---COMMIT_DELIMITER---"
    cmd = [
        "log",
        f"--pretty=format:{commit_token}%n%H|%h|%P|%an|%ad|%s",
        "--date=iso",
        "--numstat",
    ]
    if first_parent:
        cmd.append("--first-parent")
    if count and not commit_range:
        cmd.extend(["-n", str(count)])
    if commit_range:
        cmd.append(commit_range)

    try:
        raw = run_git(cmd, cwd=root)
    except subprocess.CalledProcessError as e:
        print(f"Error fetching git log: {e.stderr}", file=sys.stderr)
        return [], {}, {}

    raw_blocks = raw.split(commit_token)
    commits: List[Dict[str, Any]] = []
    stats_map: Dict[str, Tuple[int, int, int]] = {}
    files_map: Dict[str, List[str]] = {}

    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        header = lines[0].strip()
        parts = header.split("|", 5)
        if len(parts) < 6:
            continue
        full_sha, short_sha, parents_raw, author, date_str, subject = parts
        parents = parents_raw.split() if parents_raw else []
        is_merge = len(parents) > 1

        commits.append(
            {
                "full_sha": full_sha,
                "short_sha": short_sha,
                "parents": parents,
                "is_merge": is_merge,
                "author": author,
                "date": date_str,
                "subject": subject,
            }
        )

        # Parse numstat lines
        numstat_lines = lines[1:]
        files_touched = []
        ins_total = 0
        del_total = 0
        for numstat_line in numstat_lines:
            numstat_line = numstat_line.strip()
            if not numstat_line:
                continue
            cols = numstat_line.split(maxsplit=2)
            if len(cols) == 3:
                ins_s, del_s, fpath = cols
                files_touched.append(fpath)
                try:
                    ins_total += int(ins_s) if ins_s != "-" else 0
                    del_total += int(del_s) if del_s != "-" else 0
                except ValueError:
                    pass

        stats_map[full_sha] = (len(files_touched), ins_total, del_total)
        files_map[full_sha] = files_touched

    return commits, stats_map, files_map


def compute_origin_lag(cwd: Path) -> int:
    """Calculates number of commits local main is ahead of origin/main if available."""
    try:
        raw = run_git(["rev-list", "--count", "origin/main..main"], cwd=cwd)
        return int(raw.strip())
    except Exception:
        try:
            raw = run_git(["rev-list", "--count", "origin/main..HEAD"], cwd=cwd)
            return int(raw.strip())
        except Exception:
            return 0


def compute_kpi_metrics(
    commit_range: Optional[str] = None,
    count: Optional[int] = 100,
    first_parent: bool = False,
    cwd: Optional[Path] = None,
) -> Dict[str, Any]:
    """Computes full suite of AI-DLC lifecycle metrics."""
    root = cwd or ROOT
    commits, stats_map, files_map = parse_commits_batch(
        commit_range=commit_range,
        count=count,
        first_parent=first_parent,
        cwd=root,
    )

    total_commits = len(commits)
    if total_commits == 0:
        return {
            "error": "NO_COMMITS_FOUND",
            "commit_range": commit_range or f"HEAD~{count}..HEAD",
            "total_commits": 0,
        }

    merges = [c for c in commits if c["is_merge"]]
    non_merges = [c for c in commits if not c["is_merge"]]

    # 1. Commit Type Breakdown
    mix_counts: Dict[str, int] = {}
    for c in non_merges:
        m = CONVENTIONAL_REGEX.match(c["subject"])
        c_type = m.group(1).lower() if m else "other"
        mix_counts[c_type] = mix_counts.get(c_type, 0) + 1

    # 2. Gate Appeasement / Rework Commits
    rework_commits = [c for c in non_merges if REWORK_REGEX.search(c["subject"])]
    rework_count = len(rework_commits)
    rework_ratio = round(rework_count / max(1, len(non_merges)), 4)

    # 3. Receipt / Seal Commits
    seal_commits = [c for c in non_merges if SEAL_REGEX.search(c["subject"])]
    seal_count = len(seal_commits)
    seal_ratio = round(seal_count / max(1, len(non_merges)), 4)

    # 4. Hotfile Touches & Artifact Touches
    hotfile_target = "tools/run_ci_gates.py"
    hotfile_touches = 0
    generated_touches = 0
    files_per_commit: List[int] = []
    lines_inserted_per_commit: List[int] = []
    lines_deleted_per_commit: List[int] = []

    for c in commits:
        sha = c["full_sha"]
        touched = files_map.get(sha, [])
        if hotfile_target in touched:
            hotfile_touches += 1
        generated_count = sum(
            1 for f in touched if f.startswith("reports/") or f.startswith("output/")
        )
        generated_touches += generated_count

        f_count, ins, dels = stats_map.get(sha, (0, 0, 0))
        files_per_commit.append(f_count)
        lines_inserted_per_commit.append(ins)
        lines_deleted_per_commit.append(dels)

    median_files = statistics.median(files_per_commit) if files_per_commit else 0
    median_insertions = (
        statistics.median(lines_inserted_per_commit) if lines_inserted_per_commit else 0
    )
    median_deletions = (
        statistics.median(lines_deleted_per_commit) if lines_deleted_per_commit else 0
    )

    # 5. Lead Time (from commit date to merge commit date)
    commit_by_sha = {c["full_sha"]: c for c in commits}
    lead_times_minutes: List[float] = []
    for m in merges:
        try:
            m_date = dt.datetime.fromisoformat(m["date"].replace("Z", "+00:00"))
            parents = m["parents"]
            if len(parents) >= 2:
                b_tip = parents[1]
                b_date = None
                if b_tip in commit_by_sha:
                    b_date = dt.datetime.fromisoformat(
                        commit_by_sha[b_tip]["date"].replace("Z", "+00:00")
                    )
                else:
                    raw_b = run_git(
                        ["log", "-1", "--pretty=format:%ad", "--date=iso", b_tip], cwd=root
                    )
                    if raw_b:
                        b_date = dt.datetime.fromisoformat(raw_b.strip().replace("Z", "+00:00"))
                if b_date is not None:
                    diff_mins = max(0.0, (m_date - b_date).total_seconds() / 60.0)
                    lead_times_minutes.append(round(diff_mins, 2))
        except Exception:
            pass

    lead_p50 = round(statistics.median(lead_times_minutes), 2) if lead_times_minutes else 0.0
    lead_p90 = (
        round(sorted(lead_times_minutes)[int(0.9 * (len(lead_times_minutes) - 1))], 2)
        if lead_times_minutes
        else 0.0
    )
    lead_max = round(max(lead_times_minutes), 2) if lead_times_minutes else 0.0

    # 6. Origin Lag
    origin_lag = compute_origin_lag(root)

    # 7. Provenance Digest
    hasher = hashlib.sha256()
    for c in commits:
        hasher.update(f"{c['full_sha']}:{c['subject']}\n".encode("utf-8"))
    commits_digest = hasher.hexdigest()

    return {
        "report_id": f"aidlc-kpi-{int(dt.datetime.now().timestamp())}",
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "commit_range": commit_range or f"HEAD~{count}..HEAD",
        "input_commits_digest": commits_digest,
        "total_commits": total_commits,
        "merges_count": len(merges),
        "non_merges_count": len(non_merges),
        "commit_mix": mix_counts,
        "rework": {
            "appeasement_commits_count": rework_count,
            "appeasement_ratio": rework_ratio,
            "sample_shas": [c["short_sha"] for c in rework_commits[:5]],
        },
        "seal": {
            "seal_only_commits_count": seal_count,
            "seal_ratio": seal_ratio,
            "sample_shas": [c["short_sha"] for c in seal_commits[:5]],
        },
        "hotfile": {
            "target": hotfile_target,
            "touches_count": hotfile_touches,
        },
        "generated_artifacts": {
            "touches_count": generated_touches,
        },
        "batch_size": {
            "median_files_changed": median_files,
            "median_lines_inserted": median_insertions,
            "median_lines_deleted": median_deletions,
        },
        "lead_time": {
            "samples_count": len(lead_times_minutes),
            "median_minutes": lead_p50,
            "p90_minutes": lead_p90,
            "max_minutes": lead_max,
        },
        "origin_lag_commits": origin_lag,
    }


def compare_reports(current: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    """Compares current KPI metrics against baseline receipt."""
    rework_curr = current.get("rework", {}).get("appeasement_commits_count", 0)
    rework_base = baseline.get("rework", {}).get("appeasement_commits_count", 0)

    seal_curr = current.get("seal", {}).get("seal_only_commits_count", 0)
    seal_base = baseline.get("seal", {}).get("seal_only_commits_count", 0)

    hotfile_curr = current.get("hotfile", {}).get("touches_count", 0)
    hotfile_base = baseline.get("hotfile", {}).get("touches_count", 0)

    lead_curr = current.get("lead_time", {}).get("median_minutes", 0.0)
    lead_base = baseline.get("lead_time", {}).get("median_minutes", 0.0)

    origin_curr = current.get("origin_lag_commits", 0)
    origin_base = baseline.get("origin_lag_commits", 0)

    from tools.kpi_scorecard import evaluate_section4_kpis

    scorecard = evaluate_section4_kpis(current, baseline)

    return {
        "comparison_timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "baseline_range": baseline.get("commit_range"),
        "current_range": current.get("commit_range"),
        "scorecard": scorecard,
        "deltas": {
            "rework_commits": {
                "baseline": rework_base,
                "current": rework_curr,
                "delta": rework_curr - rework_base,
            },
            "seal_commits": {
                "baseline": seal_base,
                "current": seal_curr,
                "delta": seal_curr - seal_base,
            },
            "hotfile_touches": {
                "baseline": hotfile_base,
                "current": hotfile_curr,
                "delta": hotfile_curr - hotfile_base,
            },
            "lead_time_p50_minutes": {
                "baseline": lead_base,
                "current": lead_curr,
                "delta": round(lead_curr - lead_base, 2),
            },
            "origin_lag": {
                "baseline": origin_base,
                "current": origin_curr,
                "delta": origin_curr - origin_base,
            },
        },
    }


def _extract_metric_value(metric_name: str, metrics: Dict[str, Any]) -> float:
    metric_map = {
        "rework": metrics.get("rework", {}).get("appeasement_commits_count", 0),
        "seal": metrics.get("seal", {}).get("seal_only_commits_count", 0),
        "hotfile": metrics.get("hotfile", {}).get("touches_count", 0),
        "generated": metrics.get("generated_artifacts", {}).get("touches_count", 0),
        "batch": metrics.get("batch_size", {}).get("median_lines_inserted", 0),
        "leadtime": metrics.get("lead_time", {}).get("median_minutes", 0.0),
        "origin-lag": metrics.get("origin_lag_commits", 0),
    }
    return float(metric_map.get(metric_name, 0.0))


def _write_receipt(out_path: Path, metrics: Dict[str, Any], comparison: Optional[Dict[str, Any]]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(metrics)
    if comparison:
        payload["baseline_comparison"] = comparison
        scorecard = comparison.get("scorecard", {})
        payload["section4_scorecard"] = scorecard
        payload["kpi_target_met_pct"] = scorecard.get("target_met_pct", 0.0)
        payload["kpi_closeout_verdict"] = scorecard.get("verdict", "IN_PROGRESS")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"✅ Sealed KPI receipt: {out_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="AI-DLC KPI Report Generator")
    parser.add_argument("--range", help="Git revision range (e.g. A..B or HEAD~100..HEAD)")
    parser.add_argument(
        "--count", type=int, default=100, help="Number of commits if range is not specified"
    )
    parser.add_argument(
        "--first-parent", action="store_true", help="Follow only first parent in merge commits"
    )
    parser.add_argument("--out", help="Write receipt JSON to specified path")
    parser.add_argument(
        "--metric",
        choices=["rework", "seal", "hotfile", "generated", "batch", "leadtime", "origin-lag"],
        help="Extract specific metric",
    )
    parser.add_argument(
        "--fail-above", type=float, help="Fail with returncode 1 if metric exceeds threshold"
    )
    parser.add_argument("--compare", help="Path to baseline receipt to compare against")
    parser.add_argument(
        "--repository-root", type=Path, default=ROOT, help="Repository root directory"
    )

    args = parser.parse_args()

    metrics = compute_kpi_metrics(
        commit_range=args.range,
        count=args.count,
        first_parent=args.first_parent,
        cwd=args.repository_root,
    )

    comparison = None
    if args.compare:
        base_path = Path(args.compare)
        if base_path.exists():
            with open(base_path, "r", encoding="utf-8") as f:
                baseline = json.load(f)
            comparison = compare_reports(metrics, baseline)
        else:
            print(f"Error: baseline file not found: {base_path}", file=sys.stderr)
            return 1

    if args.out:
        _write_receipt(Path(args.out), metrics, comparison)

    if args.compare and not args.metric:
        print(json.dumps(comparison, indent=2))

    if args.metric:
        val = _extract_metric_value(args.metric, metrics)
        print(f"METRIC [{args.metric}]: {val}")
        if args.fail_above is not None and val > args.fail_above:
            print(
                f"FAIL: metric {val} exceeded allowed threshold {args.fail_above}", file=sys.stderr
            )
            return 1
        return 0

    if not args.out and not args.compare and not args.metric:
        print(json.dumps(metrics, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
