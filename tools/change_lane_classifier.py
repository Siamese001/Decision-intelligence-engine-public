#!/usr/bin/env python3
"""tools/change_lane_classifier.py

Risk-Tiered Change Lane Classifier & Sizing Guard (Wave 7 / Findings X-18 to X-20).
Classifies repository file changes into three distinct risk tiers:
  Lane 0: Low Risk (docs, tests, comments, isolated walkthroughs)
  Lane 1: Medium Risk (standard pipeline code, tools, diagnostics)
  Lane 2: High / Critical Risk (graph storage, releases, ingestion, HITL ledger, governance controls)

Enforces fail-closed security escalation:
- Any unrecognized path defaults to Lane 2.
- Any mixed change escalates to the highest lane touched.
- Policy-as-code itself (config/governance/risk_lanes_policy.json) is a Lane 2 control.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

DEFAULT_POLICY_PATH = Path("config/governance/risk_lanes_policy.json")


def load_policy(policy_path: Optional[Path] = None) -> Dict[str, Any]:
    p = policy_path or DEFAULT_POLICY_PATH
    if not p.is_file():
        # Fallback safe default
        return {
            "default_lane": 2,
            "fail_closed_on_unrecognized": True,
            "escalate_mixed_to_highest": True,
            "path_rules": [],
            "soft_caps": {
                "max_non_generated_loc_per_merge": 400,
                "max_active_worktrees": 30,
                "max_governance_meta_ratio": 0.15,
            },
        }
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"default_lane": 2, "path_rules": []}


def _matches_pattern(normalized_path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return normalized_path == prefix or normalized_path.startswith(prefix + "/")
    if pattern.startswith("*.") and "/" not in pattern:
        return "/" not in normalized_path and fnmatch.fnmatch(normalized_path, pattern)
    if pattern.endswith("*"):
        prefix = pattern[:-1]
        return normalized_path.startswith(prefix)
    if fnmatch.fnmatch(normalized_path, pattern):
        return True
    return False


def classify_file(
    file_path: str,
    policy: Dict[str, Any],
) -> Tuple[int, str]:
    """Classifies a single file path into a risk lane (0, 1, or 2)."""
    cleaned = file_path.replace("\\", "/").strip()
    norm = os.path.normpath(cleaned).replace("\\", "/").lstrip("/")

    # Detect illegal path traversal escaping repository root
    if norm.startswith("..") or norm == "":
        return 2, "Illegal path traversal escaping repository root; fail-closed default to Lane 2"

    # AGENTS.md, root implementation_plan.md, .agents/**, governance/**, and executable prompt markdown are strictly Lane 2
    if norm == "AGENTS.md" or norm.endswith("/AGENTS.md"):
        return 2, "Core agent operating rules and invariants (Lane 2)"
    if norm == "implementation_plan.md":
        return 2, "Root implementation_plan.md is a Lane 2 governance violation (Single Plan Location Policy)"
    if norm.startswith(".agents/") or norm.startswith("governance/"):
        return 2, "Governance rules and policies (Lane 2 security control)"
    if "prompt" in norm.lower() and norm.endswith(".md"):
        return 2, "Executable-prompt Markdown (Lane 2 security control)"

    # Evaluate path rules in policy
    # Lane 2 rules are prioritized first to prevent downgrade
    rules = policy.get("path_rules", [])
    lane_2_rules = [r for r in rules if r.get("lane") == 2]
    other_rules = [r for r in rules if r.get("lane") != 2]

    for rule in lane_2_rules:
        if _matches_pattern(norm, rule.get("pattern", "")):
            return 2, rule.get("reason", "Matched Lane 2 pattern")

    for rule in other_rules:
        if _matches_pattern(norm, rule.get("pattern", "")):
            lane = int(rule.get("lane", 1))
            reason = rule.get("reason", "Matched pattern")
            if lane == 0 and norm.endswith(".py") and not norm.startswith("tests/unit/test_isolated"):
                return 1, f"Executable Python file inside Lane 0 path escalated to Lane 1 ({reason})"
            return lane, reason

    # Fail-closed default
    default_lane = int(policy.get("default_lane", 2))
    return default_lane, "Unrecognized path; fail-closed default to Lane 2"


def classify_changes(
    files: Sequence[str],
    policy_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Classifies a collection of changed files and determines the effective risk lane."""
    policy = load_policy(policy_path)
    file_classifications: Dict[str, Dict[str, Any]] = {}

    if not files:
        # Empty change set is Lane 0
        return {
            "effective_lane": 0,
            "effective_lane_name": "LANE_0_LOW_RISK",
            "file_count": 0,
            "lane_counts": {0: 0, 1: 0, 2: 0},
            "file_classifications": {},
            "touches_lane_2": False,
            "touches_lane_1": False,
            "touches_lane_0": False,
            "escalated": False,
        }

    lane_counts = {0: 0, 1: 0, 2: 0}
    max_lane = 0

    for f in files:
        lane, reason = classify_file(f, policy)
        file_classifications[f] = {"lane": lane, "reason": reason}
        lane_counts[lane] = lane_counts.get(lane, 0) + 1
        if lane > max_lane:
            max_lane = lane

    lane_names = {
        0: "LANE_0_LOW_RISK",
        1: "LANE_1_MEDIUM_RISK",
        2: "LANE_2_HIGH_RISK",
    }

    touches_multiple = sum(1 for c in lane_counts.values() if c > 0) > 1
    escalated = touches_multiple and max_lane > min(l for l, c in lane_counts.items() if c > 0)

    return {
        "effective_lane": max_lane,
        "effective_lane_name": lane_names.get(max_lane, "LANE_2_HIGH_RISK"),
        "file_count": len(files),
        "lane_counts": lane_counts,
        "file_classifications": file_classifications,
        "touches_lane_2": lane_counts.get(2, 0) > 0,
        "touches_lane_1": lane_counts.get(1, 0) > 0,
        "touches_lane_0": lane_counts.get(0, 0) > 0,
        "escalated": escalated,
    }


def _sum_numstat_lines(stdout: str) -> int:
    total_added = 0
    for line in stdout.splitlines():
        parts = line.strip().split()
        if len(parts) >= 3 and parts[0] != "-":
            path = parts[2]
            if path.startswith(("reports/", "inputs/", "rollback/")):
                continue
            total_added += int(parts[0])
    return total_added


def check_batch_size(
    files: Sequence[str],
    repo_root: Path,
    max_loc: int = 400,
) -> Tuple[bool, int, str]:
    """Checks added lines of code across non-generated files (staged or branch diff vs main)."""
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--numstat"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        total_added = _sum_numstat_lines(proc.stdout)
        if total_added == 0:
            branch_proc = subprocess.run(
                ["git", "diff", "main...HEAD", "--numstat"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if branch_proc.returncode == 0:
                total_added = _sum_numstat_lines(branch_proc.stdout)
        passed = total_added <= max_loc
        msg = f"Added {total_added} non-generated LOC (cap: {max_loc})"
        return passed, total_added, msg
    except Exception as e:
        return False, 0, f"Batch size check failed closed: {e}"


def check_worktree_wip(repo_root: Path, max_wip: int = 30) -> Tuple[bool, int, str]:
    """Checks that the number of active and orphan worktrees does not exceed WIP cap."""
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        registered_paths = {
            Path(line.split(" ", 1)[1].strip()).resolve()
            for line in proc.stdout.splitlines()
            if line.startswith("worktree ")
        }
        feature_wt_count = max(0, len(registered_paths) - 1)
        wt_dir = (repo_root / ".worktrees").resolve()
        orphan_count = 0
        if wt_dir.is_dir():
            for child in wt_dir.iterdir():
                if child.is_dir() and not child.name.startswith(".") and child.resolve() not in registered_paths:
                    orphan_count += 1
        total_count = feature_wt_count + orphan_count
        passed = total_count <= max_wip and orphan_count == 0
        msg = f"Active feature worktrees: {feature_wt_count}, orphans: {orphan_count} (WIP cap: {max_wip})"
        return passed, total_count, msg
    except Exception as e:
        return False, 0, f"Worktree WIP check failed closed: {e}"


def run_self_test() -> int:
    """Executes a rigorous self-test verifying all risk lane classification invariants."""
    policy = load_policy()

    # 1. Lane 0 tests
    l0_cases = [
        "docs/plans/my_plan.md",
        "documentation/walkthrough.md",
        "archive/root_clutter/debug.txt",
        "README.md",
        "reports/summary.json",
    ]
    for path in l0_cases:
        lane, _ = classify_file(path, policy)
        assert lane == 0, f"Expected Lane 0 for {path}, got Lane {lane}"

    # 2. Lane 1 tests
    l1_cases = [
        "dual_pipeline/analyzer.py",
        "tools/format_data.py",
        "evals/test_speed.py",
        "tests/unit/test_something.py",
    ]
    for path in l1_cases:
        lane, _ = classify_file(path, policy)
        assert lane == 1, f"Expected Lane 1 for {path}, got Lane {lane}"

    # 3. Lane 2 tests
    l2_cases = [
        "graph/nee_graph.sqlite",
        "storage/connect.py",
        "dual_pipeline/storage/sqlite.py",
        "dual_pipeline/release/activate.py",
        "dual_pipeline/ingestion/u0.py",
        "dual_pipeline/hitl_governance_core/ledger.py",
        "tools/govern_learning_proposals.py",
        "tools/pr_governance.py",
        "tools/pr_governance_core/lifecycle.py",
        "tools/verify_commit.py",
        "tools/attestation_core/storage.py",
        "tools/change_lane_classifier.py",
        "config/governance/risk_lanes_policy.json",
        "AGENTS.md",
        "hitl_decision_ledger.json",
    ]
    for path in l2_cases:
        lane, _ = classify_file(path, policy)
        assert lane == 2, f"Expected Lane 2 for {path}, got Lane {lane}"

    # 4. Fail-closed on unrecognized paths
    unrecognized_cases = [
        "unknown_folder/weird_binary.xyz",
        "kernel/driver.c",
        "bin/executable",
    ]
    for path in unrecognized_cases:
        lane, _ = classify_file(path, policy)
        assert lane == 2, f"Expected Lane 2 fail-closed for {path}, got Lane {lane}"

    # 5. Mixed change set escalation test
    mixed_files = [
        "docs/plans/plan.md",  # Lane 0
        "tools/analyzer.py",  # Lane 1
        "storage/connect.py",  # Lane 2
    ]
    res = classify_changes(mixed_files)
    assert res["effective_lane"] == 2, (
        f"Mixed set must escalate to Lane 2, got {res['effective_lane']}"
    )
    assert res["escalated"] is True
    assert res["touches_lane_2"] is True

    # 6. Lane 0 + Lane 1 escalation test
    l0_l1_files = [
        "docs/plans/plan.md",  # Lane 0
        "tools/analyzer.py",  # Lane 1
    ]
    res_med = classify_changes(l0_l1_files)
    assert res_med["effective_lane"] == 1, (
        f"Lane 0 + 1 set must escalate to Lane 1, got {res_med['effective_lane']}"
    )
    assert res_med["escalated"] is True

    print("✅ All Change Lane Classifier self-tests passed (6/6 test groups verified).")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Risk-Tiered Change Lane Classifier.")
    parser.add_argument("--self-test", action="store_true", help="Run self-test validation suite.")
    parser.add_argument("--files", nargs="*", help="Specific files to classify.")
    parser.add_argument("--staged", action="store_true", help="Classify staged git files.")
    parser.add_argument("--json", action="store_true", help="Output JSON result.")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    repo_root = Path.cwd()
    files_to_check: List[str] = []

    if args.files:
        files_to_check = args.files
    elif args.staged:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        files_to_check = [f.strip() for f in proc.stdout.splitlines() if f.strip()]
    else:
        # Default: unstaged + staged
        proc = subprocess.run(
            ["git", "diff", "HEAD", "--name-only"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        files_to_check = [f.strip() for f in proc.stdout.splitlines() if f.strip()]

    result = classify_changes(files_to_check)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("=" * 80)
        print(
            f"  CHANGE RISK CLASSIFICATION: {result['effective_lane_name']} (Lane {result['effective_lane']})"
        )
        print("=" * 80)
        print(f"Files Evaluated: {result['file_count']}")
        print(
            f"Lane Breakdown:  Lane 0: {result['lane_counts'][0]} | Lane 1: {result['lane_counts'][1]} | Lane 2: {result['lane_counts'][2]}"
        )
        if result["escalated"]:
            print(
                "⚠️  Security Escalation: Change touches multiple risk tiers; escalated to highest lane."
            )
        for f, meta in result["file_classifications"].items():
            print(f"  [Lane {meta['lane']}] {f} ({meta['reason']})")
        print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
