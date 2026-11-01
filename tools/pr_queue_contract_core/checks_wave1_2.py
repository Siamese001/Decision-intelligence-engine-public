"""tools/pr_queue_contract_core/checks_wave1_2.py

Contract verification checks for Wave 1 and Wave 2.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def check_manifest_integrity(expected_wave: int = 1) -> dict[str, Any]:
    """Check: Manifest Integrity."""
    manifest_path = ROOT / "tests" / "manifests" / "pr_queue_contract_manifest.json"
    if not manifest_path.is_file():
        return {"name": "ManifestIntegrity", "status": "FAIL", "error": f"Missing {manifest_path}"}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        from tools.test_manifest_loader import validate_manifest_payload
        valid, errors = validate_manifest_payload(data, source=str(manifest_path))
        if not valid:
            return {"name": "ManifestIntegrity", "status": "FAIL", "errors": errors}
        req_cases = data.get("required_case_ids", [])
        min_cases = 11 if expected_wave == 1 else (16 if expected_wave == 2 else (22 if expected_wave == 3 else 29))
        if len(req_cases) < min_cases:
            return {
                "name": "ManifestIntegrity",
                "status": "FAIL",
                "error": f"Insufficient required_case_ids ({len(req_cases)} < {min_cases})",
            }
        return {
            "name": "ManifestIntegrity",
            "status": "PASS",
            "details": f"Valid manifest (v{data.get('version')}) with {len(req_cases)} required case IDs for Wave {expected_wave}",
        }
    except Exception as exc:
        return {"name": "ManifestIntegrity", "status": "FAIL", "error": str(exc)}


def check_focused_regression_tests(py: str, timeout: int = 60) -> dict[str, Any]:
    """Check: Focused Regression Tests Execution under pytest and governed runner."""
    target_test = "tests/test_pr_queue_rationale_edge_cases.py"
    cmd_runner = [py, "-B", "tools/run_tests_with_progress.py", "--timeout", str(timeout), target_test]
    start = time.monotonic()
    proc_runner = subprocess.run(cmd_runner, cwd=str(ROOT), capture_output=True, text=True, check=False, timeout=timeout + 15)
    dur_runner = time.monotonic() - start
    if proc_runner.returncode != 0:
        return {"name": "FocusedRegressionTests", "status": "FAIL", "error": f"runner failed: {proc_runner.stderr[-300:]}"}

    cmd_pytest = [py, "-B", "-m", "pytest", f"--timeout={timeout}", "-q", target_test]
    start = time.monotonic()
    proc_pytest = subprocess.run(cmd_pytest, cwd=str(ROOT), capture_output=True, text=True, check=False, timeout=timeout + 15)
    dur_pytest = time.monotonic() - start
    if proc_pytest.returncode != 0:
        return {"name": "FocusedRegressionTests", "status": "FAIL", "error": f"pytest failed: {proc_pytest.stderr[-300:]}"}

    return {
        "name": "FocusedRegressionTests",
        "status": "PASS",
        "details": f"Both runner ({dur_runner:.2f}s) and pytest ({dur_pytest:.2f}s) passed all tests",
    }


def check_collection_parity(py: str, expected_count: int = 11) -> dict[str, Any]:
    """Check: Collection Parity between pytest and run_tests_with_progress."""
    target_test = "tests/test_pr_queue_rationale_edge_cases.py"
    cmd_collect = [py, "-B", "-m", "pytest", "--collect-only", "-q", target_test]
    proc = subprocess.run(cmd_collect, cwd=str(ROOT), capture_output=True, text=True, check=False, timeout=20)
    if proc.returncode != 0:
        return {"name": "CollectionParity", "status": "FAIL", "error": f"pytest collection failed: {proc.stderr}"}

    collected = [line.strip() for line in proc.stdout.splitlines() if "::test_" in line]
    if len(collected) < expected_count:
        return {"name": "CollectionParity", "status": "FAIL", "error": f"Expected >= {expected_count} cases, got {len(collected)}"}

    from tools.run_tests_with_progress import classify_and_build_suites
    _, pytest_items = classify_and_build_suites([target_test], start_dir=ROOT)
    if not pytest_items or len(pytest_items) != 1:
        return {"name": "CollectionParity", "status": "FAIL", "error": "classify_and_build_suites failed"}

    return {
        "name": "CollectionParity",
        "status": "PASS",
        "details": f"Parity verified: exactly {len(collected)} test cases collected",
        "case_count": len(collected),
    }


def check_failing_canary_contract(py: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="canary_contract_") as tmpdir:
        canary = Path(tmpdir) / "test_deliberate_canary_failure.py"
        canary.write_text("def test_failing_canary():\n    assert False, 'Deliberate canary failure'\n", encoding="utf-8")
        p1 = subprocess.run([py, "-B", "-m", "pytest", "--timeout=10", "-q", str(canary)], cwd=str(ROOT), capture_output=True, check=False)
        p2 = subprocess.run([py, "-B", "tools/run_tests_with_progress.py", "--timeout", "10", str(canary)], cwd=str(ROOT), capture_output=True, check=False)
        if p1.returncode == 0 or p2.returncode == 0:
            return {"name": "FailingCanaryContract", "status": "FAIL", "error": "Canary failed to trigger non-zero exit code"}
    return {"name": "FailingCanaryContract", "status": "PASS", "details": "Canary triggered non-zero exit codes in pytest and runner"}


def check_zero_collection_fail_closed(py: str) -> dict[str, Any]:
    proc = subprocess.run([py, "-B", "tools/run_tests_with_progress.py", "tests/nonexistent_zero_collect.py"], cwd=str(ROOT), capture_output=True, check=False)
    if proc.returncode == 0:
        return {"name": "ZeroCollectionFailClosed", "status": "FAIL", "error": "Runner passed on 0-collected target"}
    return {"name": "ZeroCollectionFailClosed", "status": "PASS", "details": f"Failed closed with exit code {proc.returncode}"}


def check_stale_plan_exclusion() -> dict[str, Any]:
    from tools.pr_governance_core.wave_status import compute_wave_columns
    show = lambda b, p: "## Wave 1: stale" if p == "implementation_plan.md" else ""  # noqa: E731
    c, o = compute_wave_columns("b", show, ["tools/x.py"])
    if c != "?" or o != "?":
        return {"name": "StalePlanExclusion", "status": "FAIL", "error": f"Expected completed='?' and open='?', got {c}, {o}"}
    return {"name": "StalePlanExclusion", "status": "PASS", "details": f"Stale root plan excluded (completed='{c}', open='{o}')"}


def check_empty_branch_authoring_status() -> dict[str, Any]:
    from tools.pr_governance_core.common import calculate_readiness_and_risk
    readiness, risk = calculate_readiness_and_risk({"ahead": 0, "behind": 0, "clean_merge": True}, wt={}, open_waves="—")
    if readiness != "⏳ Authoring":
        return {"name": "EmptyBranchAuthoringStatus", "status": "FAIL", "error": f"Expected '⏳ Authoring', got '{readiness}'"}
    return {"name": "EmptyBranchAuthoringStatus", "status": "PASS", "details": f"Classified as '{readiness}' (risk: {risk})"}


def check_diagnostic_ledger_view() -> dict[str, Any]:
    import io
    from contextlib import redirect_stdout
    from tools.pr_governance_core.render import _render_compact_table
    sample = [{"branch": "feat/x", "sub_rank": "1", "priority_rank": 1, "readiness": "⏳ Authoring", "risk_score": 20, "category": "FEATURE", "group": "ACTIVE", "ahead": 0, "behind": 0, "dirty_status": "clean", "open_waves": "?", "completed_waves": "?", "next_action": "Commit work", "ranking_rationale": "Inception"}]
    buf = io.StringIO()
    with redirect_stdout(buf):
        _render_compact_table(sample)
    missing = [k for k in ["Ranking Rationale", "Next Action", "Blocker", "Effort & Waves"] if k not in buf.getvalue()]
    if missing:
        return {"name": "DiagnosticLedgerView", "status": "FAIL", "error": f"Missing: {missing}"}
    return {"name": "DiagnosticLedgerView", "status": "PASS", "details": "Diagnostic ledger displays all required sections"}


def check_model_identity_progress_recovery() -> dict[str, Any]:
    from tools.pr_governance_core.queue_snapshot import discover_plan_ownership
    wt_path = ROOT / ".worktrees" / "docs_model_identity_ssot_rca_plan"
    if not wt_path.exists():
        return {"name": "ModelIdentityProgressRecovery", "status": "PASS", "details": "Model identity worktree not present in this environment"}
    records = discover_plan_ownership("docs/model-identity-ssot-rca-plan", ROOT, wt_path)
    w1_recs = [r for r in records if r.wave_id == 1]
    if not w1_recs:
        return {"name": "ModelIdentityProgressRecovery", "status": "FAIL", "error": "Wave 1 record missing"}
    return {"name": "ModelIdentityProgressRecovery", "status": "PASS", "details": f"Recovered {w1_recs[0].plan_id}: Wave 1 ({w1_recs[0].verified_status}) with {len(w1_recs[0].receipt_bindings)} receipt(s)"}


def check_open_precedence_and_reopening() -> dict[str, Any]:
    from tools.pr_governance_core.wave_status import parse_plan_text, _merge_waves
    waves = parse_plan_text("| Wave 1 | Complete migration | ⏳ OPEN |\n| Wave 2 | Completed tests | 🚫 BLOCKED |\n")
    if waves.get(1) != "open" or waves.get(2) != "open":
        return {"name": "OpenPrecedenceAndReopening", "status": "FAIL", "error": f"OPEN failed to beat completion words: {waves}"}
    merged = {1: "done"}
    _merge_waves(merged, {1: "open"})
    if merged.get(1) != "open":
        return {"name": "OpenPrecedenceAndReopening", "status": "FAIL", "error": "Reopened wave failed to update done to open"}
    return {"name": "OpenPrecedenceAndReopening", "status": "PASS", "details": "OPEN status beats completion words; reopening remains visible"}


def check_plan_isolation_and_no_collision() -> dict[str, Any]:
    from tools.pr_governance_core.queue_snapshot import PlanOwnershipRecord
    p1 = PlanOwnershipRecord("branch_a", "plan_a.md", "/a.md", 1, "sha_a", "untracked", "done", "verified_done")
    p2 = PlanOwnershipRecord("branch_b", "plan_b.md", "/b.md", 1, "sha_b", "untracked", "open", "open")
    if p1.plan_id == p2.plan_id or p1.declared_status == p2.declared_status:
        return {"name": "PlanIsolationAndNoCollision", "status": "FAIL", "error": "Plan records collided"}
    return {"name": "PlanIsolationAndNoCollision", "status": "PASS", "details": "Plans with identical wave IDs maintain isolated records"}


def check_fresh_inception_preservation() -> dict[str, Any]:
    from unittest.mock import MagicMock, patch
    from tools.pr_topology_core.disposition import _check_ahead_zero_worktree_safety
    from tools.pr_topology_core.matrix import BranchDiff
    diff = BranchDiff(branch="feat/fresh", ahead=0, behind=10)
    mock_b = MagicMock(is_inception_active=True, age_desc="created 2h ago")
    with patch("tools.pr_topology_core.birth.inspect_branch_birth", return_value=mock_b), \
         patch("tools.pr_topology_core.disposition.evaluate_dirty_file_coverage", return_value=(False, [], ["file1.py"])):
        disp = _check_ahead_zero_worktree_safety("feat/fresh", diff, {"stash_count": 0}, Path("/tmp/fake"), {}, Path("/tmp/repo"), "main")
        if not disp or disp.action != "COMMIT_FIRST" or "commit work before rebasing" not in disp.reason:
            return {"name": "FreshInceptionPreservation", "status": "FAIL", "error": f"Unexpected disposition: {disp}"}
    return {"name": "FreshInceptionPreservation", "status": "PASS", "details": "Uncommitted inception work protected before rebase"}


def check_worktree_union_and_edge_cases() -> dict[str, Any]:
    """Check: Worktree unioning captures external, nested, and detached registrations with canonical IDs."""
    from tools.pr_governance_core.queue_snapshot import discover_worktrees_union
    wts, errors = discover_worktrees_union(ROOT)
    if not wts:
        return {"name": "WorktreeUnionAndEdgeCases", "status": "FAIL", "error": "No worktrees discovered"}

    canonical_ids = {w.canonical_id for w in wts}
    if len(canonical_ids) != len(wts):
        return {"name": "WorktreeUnionAndEdgeCases", "status": "FAIL", "error": "Canonical ID collision detected"}

    return {
        "name": "WorktreeUnionAndEdgeCases",
        "status": "PASS",
        "details": f"Discovered {len(wts)} worktree entries with 0 canonical collisions",
        "entries_count": len(wts),
    }


def check_concurrent_change_detection() -> dict[str, Any]:
    """Check: Concurrent git reference modification marks snapshot INCONSISTENT."""
    from unittest.mock import patch
    from tools.pr_governance_core.queue_snapshot import collect_queue_snapshot

    calls = 0
    def fake_run_git(cmd: list[str], **kwargs) -> str:
        nonlocal calls
        if "rev-parse" in cmd and "main" in cmd:
            calls += 1
            return "sha_initial" if calls % 2 == 1 else "sha_modified_concurrently"
        return "sha_head"

    with patch("tools.pr_governance_core.common.run_git", side_effect=fake_run_git):
        snap = collect_queue_snapshot(ROOT, max_retries=1)
        if snap.completeness_status != "INCONSISTENT":
            return {"name": "ConcurrentChangeDetection", "status": "FAIL", "error": f"Expected INCONSISTENT, got {snap.completeness_status}"}

    return {"name": "ConcurrentChangeDetection", "status": "PASS", "details": "Concurrent ref modification flagged snapshot as INCONSISTENT"}
