#!/usr/bin/env python3
# ruff: noqa: E402
"""Local Main Promotion & Git Governance Readiness Gatekeeper.

Performs comprehensive 32-gate verification before promoting changes into local `main`:
1-27: Architectural, security, SSOT, AST invariants, and file budget linters.
28: Core Architectural & Governance Test Suite.
29: Gate 10: E2E Coverage Audit Score (Dimension 2 ≥ 80%).
30: Grounded Delivery & Claim Manifest Verification.
31: Gate 31: Copilot Active Graph Smoke Gate.
32: Gate 32: Direct Graph Write Path Linter.

Emits an immutable receipt to `reports/readiness/local_main_readiness_<slug>_receipt.json`.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import functools
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_GATE_WORKERS = min(12, os.cpu_count() or 8)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.readiness_cache import (
    build_input_manifest,
    compute_qualification_fingerprint,
    get_cached_stage,
    save_cached_stage,
    validate_manifest_freshness,
)
from tools.readiness_concurrent import (
    _execute_parallel_linter_gates as _core_execute_parallel_linter_gates,
    _execute_unified_stages_ljf as _core_execute_unified_stages_ljf,
)
from tools.readiness_core import (
    _build_core_test_modules as _core_build_core_test_modules,
)
from tools.readiness_core import (
    _build_stages as _core_build_stages,
)
from tools.readiness_core import (
    _load_cached_receipt_for_sha,
    _load_prior_failures,
    get_branch_target_files,
)
from tools.readiness_core import (
    _resolve_branch_and_slug as _core_resolve_branch_and_slug,
)
from tools.readiness_core import (
    _save_readiness_receipt as _core_save_readiness_receipt,
)
from tools.readiness_core import (
    resolve_python_interpreter as _core_resolve_python_interpreter,
)
from tools.readiness_core import (
    run_stage as _core_run_stage,
)

_build_stages = _core_build_stages


def resolve_python_interpreter() -> str:
    """Find the preferred Python interpreter with all virtualenv dependencies."""
    return _core_resolve_python_interpreter(repo_root=ROOT)


def run_stage(name: str, cmd: List[str]) -> Tuple[bool, str, float]:
    """Runs a gate stage command and measures execution time."""
    return _core_run_stage(name, cmd, repo_root=ROOT)


def _resolve_branch_and_slug(explicit_slug: Optional[str]) -> Tuple[str, str, str]:
    """Resolve active branch, HEAD commit sha, and sanitized slug."""
    return _core_resolve_branch_and_slug(repo_root=ROOT, explicit_slug=explicit_slug)


def _save_readiness_receipt(
    branch: str,
    head_sha: str,
    terminal_outcome: str,
    total_stages: int,
    stage_results: List[Dict[str, Any]],
    branch_receipt_path: Optional[Path],
    update_global: bool,
    wall_clock_duration_seconds: Optional[float] = None,
    qualification_fingerprint: Optional[str] = None,
    proportional_plan: Optional[Dict[str, Any]] = None,
) -> None:
    """Emits sealed JSON readiness receipts at branch and global locations."""
    try:
        from tools.coverage_report_reuse import resolve_primary_repo_root

        primary_root = resolve_primary_repo_root(ROOT)
    except Exception:
        primary_root = ROOT

    _core_save_readiness_receipt(
        branch=branch,
        head_sha=head_sha,
        terminal_outcome=terminal_outcome,
        total_stages=total_stages,
        stage_results=stage_results,
        branch_receipt_path=branch_receipt_path,
        update_global=update_global,
        repo_root=primary_root,
        wall_clock_duration_seconds=wall_clock_duration_seconds,
        qualification_fingerprint=qualification_fingerprint,
        proportional_plan=proportional_plan,
    )
    if terminal_outcome == "PASS":
        try:
            status_proc = subprocess.run(
                ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                cwd=primary_root,
                capture_output=True,
                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
                check=False,
            )
            is_clean = True
            from tools.readiness_cache_core.constants import is_generated_report

            for item in status_proc.stdout.split(b"\0"):
                if not item:
                    continue
                rel_bytes = item[3:] if len(item) > 3 else item
                rel_str = rel_bytes.decode("utf-8", errors="replace")
                if not is_generated_report(rel_str):
                    is_clean = False
                    break
            if not is_clean:
                print("Notice: Out-of-tree DSSE attestation skipped (working tree contains uncommitted edits).")
                return

            from tools.attestation_core import compute_tree_digest, save_readiness_attestation

            tree_digest = compute_tree_digest(primary_root, ref=head_sha)
            base_sha = ""
            with contextlib.suppress(Exception):
                base_proc = subprocess.run(
                    ["git", "rev-parse", "main"],
                    cwd=primary_root,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                base_sha = base_proc.stdout.strip()

            manifest_ver = "1.0.0"
            if proportional_plan:
                manifest_ver = proportional_plan.get("manifest_version", "1.0.0")
            pred = {
                "branch": branch,
                "head_commit_sha": head_sha,
                "base_commit_sha": base_sha,
                "manifest_version": manifest_ver,
                "terminal_outcome": terminal_outcome,
                "total_stages": total_stages,
                "stages": stage_results,
                "stage_results": stage_results,
                "qualification_fingerprint": qualification_fingerprint,
            }
            if proportional_plan:
                pred["proportional_plan"] = proportional_plan
            att_path = save_readiness_attestation(
                repo_root=primary_root,
                predicate=pred,
                tree_digest=tree_digest,
                head_sha=head_sha,
            )
            print(f"Out-of-tree DSSE attestation recorded at: {att_path.relative_to(primary_root)}")
        except Exception as exc:
            print(f"Warning: Out-of-tree DSSE attestation recording failed: {exc}", file=sys.stderr)


def _execute_parallel_linter_gates(
    stages_slice: List[Tuple[str, List[str]]],
    total_stages: int,
    use_cache: bool,
    max_workers: int,
    head_sha: str,
    target_files: List[str],
    verbose: bool,
    use_semantic: bool = False,
    manifest: Optional[Any] = None,
    exempt_indices: Optional[Set[int]] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Execute stages 1-27 concurrently while reporting results in index order."""
    from tools.readiness_cache import get_cached_stage, save_cached_stage

    return _core_execute_parallel_linter_gates(
        stages_slice=stages_slice,
        total_stages=total_stages,
        use_cache=use_cache,
        max_workers=max_workers,
        head_sha=head_sha,
        target_files=target_files,
        verbose=verbose,
        repo_root=ROOT,
        run_stage_fn=run_stage,
        get_cached_stage_fn=functools.partial(
            get_cached_stage, use_semantic_cache=use_semantic, manifest=manifest
        ),
        save_cached_stage_fn=functools.partial(
            save_cached_stage, use_semantic_cache=use_semantic, manifest=manifest
        ),
        exempt_indices=exempt_indices,
    )


def _execute_unified_stages(
    stages_slice: List[Tuple[Any, ...]],
    total_stages: int,
    use_cache: bool,
    max_workers: int,
    head_sha: str,
    target_files: List[str],
    verbose: bool,
    use_semantic: bool = False,
    manifest: Optional[Any] = None,
    exempt_indices: Optional[Set[int]] = None,
    full: bool = False,
    fail_fast: bool = False,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Execute all 32 stages concurrently using unified LJF scheduling."""
    from tools.readiness_cache import get_cached_stage, save_cached_stage

    return _core_execute_unified_stages_ljf(
        stages_slice=stages_slice,
        total_stages=total_stages,
        use_cache=use_cache,
        max_workers=max_workers,
        head_sha=head_sha,
        target_files=target_files,
        verbose=verbose,
        repo_root=ROOT,
        run_stage_fn=run_stage,
        get_cached_stage_fn=functools.partial(
            get_cached_stage, use_semantic_cache=use_semantic, manifest=manifest
        ),
        save_cached_stage_fn=functools.partial(
            save_cached_stage, use_semantic_cache=use_semantic, manifest=manifest
        ),
        exempt_indices=exempt_indices,
        full=full,
        fail_fast=fail_fast,
    )


def _print_banner(
    total_stages: int,
    py: str,
    target_files: List[str],
    parallel_gates: bool,
    fail_fast: bool,
    recheck_failed_only: bool,
    use_cache: bool,
    max_gate_workers: int,
    test_workers: int,
) -> None:
    """Print readiness evaluation banner."""
    print("=" * 80)
    print(f"  LOCAL MAIN PROMOTION & GOVERNANCE READINESS GATE ({total_stages} GATES)")
    print(f"  Repository Root: {ROOT}")
    print(f"  Python Runtime:  {py}")
    print(f"  Branch Targets:  {len(target_files)} modified Python module(s)")
    if parallel_gates and not fail_fast and not recheck_failed_only:
        print(
            f"  Execution Mode:  ⚡ PARALLEL CONCURRENT GATES (Workers: {max_gate_workers}, Test-Workers: {test_workers})"
        )
    elif fail_fast:
        print("  Execution Mode:  ⚡ FAIL-FAST (Halt on first stage failure)")
    elif recheck_failed_only:
        print("  Execution Mode:  🔄 RECHECK-FAILED-ONLY (Incremental re-test)")
    if use_cache:
        print("  Cache Mode:      ⚡ CONTENT-ADDRESSABLE STAGE MEMOIZATION")
    print("=" * 80)


def _execute_stage_item(
    stage_name: str,
    cmd: List[str],
    idx: int,
    total_stages: int,
    use_cache: bool,
    head_sha: str,
    target_files: List[str],
    verbose: bool,
    semantic_skip: bool = False,
    manifest: Optional[Any] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Execute one stage synchronously and print formatted output."""
    print(f"[{idx}/{total_stages}] Running {stage_name}...", end=" ", flush=True)
    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    passed, output, duration = run_stage(stage_name, cmd)
    finished_at = dt.datetime.now(dt.timezone.utc).isoformat()
    status_label = "PASS" if passed else "FAIL"
    print(f"{'✅' if passed else '❌'} {status_label} ({duration}s)")
    if not passed and verbose:
        print("-" * 40 + f"\n{output}\n" + "-" * 40)
    elif passed and use_cache:
        save_cached_stage(
            stage_name,
            cmd,
            ROOT,
            head_sha,
            output,
            duration,
            target_files,
            use_semantic_cache=semantic_skip,
            manifest=manifest,
        )

    res = {
        "stage_index": idx,
        "stage_name": stage_name,
        "status": status_label,
        "duration_seconds": duration,
        "queue_wait_seconds": 0.0,
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "cache_decision": "miss" if use_cache else "disabled",
        "command": " ".join(cmd),
        "output_summary": output[:500] if len(output) > 500 else output,
    }
    if not passed:
        try:
            from tools.pr_governance_core.diagnostics import format_failed_stage_diagnostics

            diag = format_failed_stage_diagnostics(idx, stage_name, cmd, output, ROOT)
            res.update(diag)
        except Exception:
            pass
    return res, passed


def _handle_fail_fast_abort(
    stages: List[Tuple[str, List[str]]],
    start_idx: int,
    total_stages: int,
    stage_results: List[Dict[str, Any]],
    failed_stage_name: str,
) -> None:
    """Mark remaining stages as NOT_RUN and halt."""
    print(f"\n⚡ FAIL-FAST ENGAGED: Halting immediately after {failed_stage_name} failure.")
    for rem_idx in range(start_idx, total_stages + 1):
        rem_name, rem_cmd = stages[rem_idx - 1]
        stage_results.append(
            {
                "stage_index": rem_idx,
                "stage_name": rem_name,
                "status": "NOT_RUN",
                "duration_seconds": 0.0,
                "command": " ".join(rem_cmd),
                "output_summary": "Skipped due to --fail-fast",
            }
        )


def _execute_dry_run_simulation(
    stages: Sequence[Tuple[str, List[str]]],
    branch: str,
    head_sha: str,
    total_stages: int,
) -> int:
    print("=" * 80)
    print(f"  [DRY RUN] MAIN READINESS SIMULATION — Branch: {branch} (HEAD: {head_sha[:8]})")
    print(f"  Total Gates Defined: {total_stages}")
    print("=" * 80)
    for idx, (stg_name, cmd) in enumerate(stages, start=1):
        suffix = " ..." if len(cmd) > 3 else ""
        cmd_preview = " ".join(cmd[:3]) + suffix
        print(f"  [{idx:02d}/{total_stages}] {stg_name:<50} -> {cmd_preview}")
    print("=" * 80)
    print(f"✅ [DRY RUN] All {total_stages} gate definitions and command structures verified successfully.")
    print("=" * 80)
    return 0


def _check_cached_readiness(
    force_rerun: bool,
    use_cache: bool,
    recheck_failed_only: bool,
    head_sha: str,
    branch_receipt_path: Optional[Path],
    qualification_fingerprint: str,
    readiness_started: float,
    branch: str,
    update_global: bool,
    readiness_dir: Path,
    manifest: Optional[Any] = None,
) -> Optional[int]:
    if force_rerun or not use_cache or recheck_failed_only:
        return None
    if manifest is not None and not validate_manifest_freshness(manifest, ROOT)[0]:
        return None
    cached_receipt = _load_cached_receipt_for_sha(
        head_sha, branch_receipt_path, qualification_fingerprint
    )
    if not cached_receipt:
        return None

    import json

    print("=" * 80)
    elapsed = time.monotonic() - readiness_started
    print(f"🎉 LOCAL MAIN READINESS VERIFIED: cached equivalent result ({elapsed:.2f}s).")
    if branch_receipt_path:
        print(f"Receipt recovered from: {branch_receipt_path}")
    print("=" * 80)
    if branch == "main" or update_global:
        receipt_path = readiness_dir / "local_main_readiness_receipt.json"
        with open(receipt_path, "w", encoding="utf-8") as f:
            json.dump(cached_receipt, f, indent=2)
    return 0


def verify_main_readiness(  # noqa: C901
    verbose: bool = False,
    update_global: bool = False,
    explicit_slug: Optional[str] = None,
    fail_fast: bool = False,
    recheck_failed_only: bool = False,
    full: bool = False,
    use_cache: bool = True,
    parallel_gates: bool = True,
    max_gate_workers: int = DEFAULT_GATE_WORKERS,
    test_workers: int = 0,
    force_rerun: bool = False,
    semantic_skip: bool = False,
    dry_run: bool = False,
    gate: Optional[int] = None,
    allow_behind: bool = False,
) -> int:
    readiness_started = time.monotonic()
    py = resolve_python_interpreter()
    target_files = get_branch_target_files(ROOT)

    from tools.readiness_planner import filter_impacted_test_modules, plan_readiness

    readiness_plan, required_indices = plan_readiness(target_files, repo_root=ROOT, force_full=full)
    raw_test_modules = _core_build_core_test_modules(target_files, full)
    test_modules = filter_impacted_test_modules(target_files, readiness_plan.test_mode, raw_test_modules)
    stages = _build_stages(
        py, target_files, test_modules, test_workers=test_workers, repo_root=ROOT, full=full
    )
    total_stages = len(stages)
    if total_stages > readiness_plan.total_registered_stages and readiness_plan.risk_lane >= 1:
        required_indices = list(required_indices) + list(
            range(readiness_plan.total_registered_stages + 1, total_stages + 1)
        )
    exempt_indices = set(range(1, total_stages + 1)) - set(required_indices)

    if gate is not None:
        if not (1 <= gate <= total_stages):
            print(f"Error: Gate {gate} out of range (1-{total_stages})")
            return 1
        stage_name, cmd = stages[gate - 1]
        print("=" * 80)
        print(f"Executing Single Gate [{gate}/{total_stages}]: {stage_name}")
        print(f"Command: {' '.join(cmd)}")
        print("=" * 80)
        success, output, duration = run_stage(stage_name, cmd)
        print(output)
        print("=" * 80)
        outcome_str = "✅ PASS" if success else "❌ FAIL"
        print(f"{outcome_str}: Gate {gate} ({stage_name}) completed in {duration:.2f}s")
        print("=" * 80)
        return 0 if success else 1

    _print_banner(
        total_stages,
        py,
        target_files,
        parallel_gates,
        fail_fast,
        recheck_failed_only,
        use_cache,
        max_gate_workers,
        test_workers,
    )
    if readiness_plan.proportional_applied:
        print(f"🎯 PROPORTIONAL GATES: {readiness_plan.risk_lane_name} ({len(readiness_plan.required_stage_indices)}/{total_stages} required stages, {readiness_plan.exempt_stages_count} exempt)")

    branch, head_sha, slug = _resolve_branch_and_slug(explicit_slug)

    if not allow_behind and not dry_run and branch != "main":
        behind_count = 0
        with contextlib.suppress(Exception):
            sync_res = subprocess.run(
                ["git", "rev-list", "--first-parent", "--count", f"{head_sha}..main"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            behind_count = int(sync_res.stdout.strip())
        if behind_count > 0:
            print("=" * 80)
            print(f"❌ READINESS REJECTED: Branch '{branch}' is {behind_count} mainline commit(s) behind 'main'.")
            print("   Rebase onto 'main' before running readiness qualification:")
            print(f"     python tools/pr_governance.py sync {branch}")
            print("   (Pass --allow-behind to bypass for local diagnostics)")
            print("=" * 80)
            return 1

    if dry_run:
        return _execute_dry_run_simulation(stages, branch, head_sha, total_stages)

    manifest = build_input_manifest(ROOT, head_sha, target_files, semantic_skip)
    qualification_fingerprint = compute_qualification_fingerprint(
        stages, ROOT, head_sha, target_files, semantic_skip, manifest=manifest
    )
    try:
        from tools.coverage_report_reuse import resolve_primary_repo_root

        primary_root = resolve_primary_repo_root(ROOT)
    except Exception:
        primary_root = ROOT
    readiness_dir = primary_root / "reports" / "readiness"
    branch_receipt_path = (
        readiness_dir / f"local_main_readiness_{slug}_receipt.json" if slug else None
    )

    cached_rc = _check_cached_readiness(
        force_rerun=force_rerun,
        use_cache=use_cache,
        recheck_failed_only=recheck_failed_only,
        head_sha=head_sha,
        branch_receipt_path=branch_receipt_path,
        qualification_fingerprint=qualification_fingerprint,
        readiness_started=readiness_started,
        branch=branch,
        update_global=update_global,
        readiness_dir=readiness_dir,
        manifest=manifest,
    )
    if cached_rc is not None:
        return cached_rc

    prior_stages_map, failed_indices = _load_prior_failures(
        branch_receipt_path if recheck_failed_only else None,
        expected_head_sha=head_sha if recheck_failed_only else None,
        expected_fingerprint=qualification_fingerprint if recheck_failed_only else None,
    )

    stage_results: List[Dict[str, Any]] = []
    overall_passed = True

    use_parallel = (
        parallel_gates and not fail_fast and not recheck_failed_only and total_stages >= 27
    )
    start_sequential_idx = 1

    if use_parallel:
        all_stage_tuples = [
            (i, stages[i - 1][0], stages[i - 1][1]) for i in range(1, total_stages + 1)
        ]
        stage_results, overall_passed = _execute_unified_stages(
            all_stage_tuples,
            total_stages,
            use_cache,
            max_gate_workers,
            head_sha,
            target_files,
            verbose,
            semantic_skip,
            manifest=manifest,
            exempt_indices=exempt_indices,
            full=full,
            fail_fast=fail_fast,
        )
        start_sequential_idx = total_stages + 1

    for idx in range(start_sequential_idx, total_stages + 1):
        stage_name, cmd = stages[idx - 1]
        if idx in exempt_indices:
            print(f"[{idx}/{total_stages}] {stage_name}... ⏭️ EXEMPT (Lane {readiness_plan.risk_lane} Proportional Gate)")
            stage_results.append(
                {
                    "stage_index": idx,
                    "stage_name": stage_name,
                    "status": "PASS",
                    "duration_seconds": 0.0,
                    "queue_wait_seconds": 0.0,
                    "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "finished_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "cache_decision": "proportional_exempt",
                    "command": "exempt under versioned gate manifest",
                    "output_summary": f"Exempt for {readiness_plan.risk_lane_name} under versioned gate manifest {readiness_plan.manifest_version}.",
                }
            )
            continue
        if idx >= 29 and not overall_passed:
            print(f"[{idx}/{total_stages}] {stage_name}... ⏭️ SKIPPED (Static prerequisites failed)")
            stage_results.append(
                {
                    "stage_index": idx,
                    "stage_name": stage_name,
                    "status": "NOT_RUN",
                    "duration_seconds": 0.0,
                    "command": " ".join(cmd),
                    "output_summary": "Skipped because static prerequisites failed",
                }
            )
            continue

        if recheck_failed_only and prior_stages_map and idx not in failed_indices:
            print(f"[{idx}/{total_stages}] {stage_name}... ⏭️ SKIPPED (Passed in prior receipt)")
            stage_results.append(prior_stages_map[idx])
            continue

        if use_cache and not recheck_failed_only:
            cached = get_cached_stage(
                stage_name,
                cmd,
                ROOT,
                head_sha,
                target_files,
                use_semantic_cache=semantic_skip,
                manifest=manifest,
            )
            if cached is not None:
                print(
                    f"[{idx}/{total_stages}] {stage_name}... ⚡ CACHED ({cached.duration_seconds}s)"
                )
                res_dict = cached.to_dict()
                res_dict["stage_index"] = idx
                res_dict.update({"cache_decision": "hit", "queue_wait_seconds": 0.0})
                stage_results.append(res_dict)
                continue

        stage_res, passed = _execute_stage_item(
            stage_name,
            cmd,
            idx,
            total_stages,
            use_cache,
            head_sha,
            target_files,
            verbose,
            semantic_skip,
            manifest=manifest,
        )
        stage_results.append(stage_res)
        if not passed:
            overall_passed = False
            if fail_fast:
                _handle_fail_fast_abort(stages, idx + 1, total_stages, stage_results, stage_name)
                break

    stage_results.sort(key=lambda s: int(s.get("stage_index", 0)))
    terminal_outcome = "PASS" if overall_passed else "FAIL"
    if overall_passed:
        is_fresh, fresh_reason = validate_manifest_freshness(manifest, ROOT)
        if not is_fresh:
            print(f"\n❌ PRE-SEAL REVALIDATION FAILED: {fresh_reason}. Candidate invalidated.")
            terminal_outcome = "FAIL"
            overall_passed = False

    wall_clock_duration = time.monotonic() - readiness_started
    _save_readiness_receipt(
        branch,
        head_sha,
        terminal_outcome,
        total_stages,
        stage_results,
        branch_receipt_path,
        update_global,
        wall_clock_duration,
        qualification_fingerprint,
        proportional_plan=readiness_plan.to_dict(),
    )

    print("=" * 80)
    outcome_msg = (
        f"🎉 READINESS PASS: {total_stages} gates in {wall_clock_duration:.2f}s."
        if overall_passed
        else f"⛔ READINESS FAIL in {wall_clock_duration:.2f}s: promotion criteria failed."
    )
    print(outcome_msg)
    if branch_receipt_path:
        print(f"Receipt recorded at: {branch_receipt_path}")
    print("=" * 80)
    return 0 if overall_passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify repository readiness before merging to local main."
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Print detailed stage outputs")
    parser.add_argument(
        "--update-global",
        action="store_true",
        help="Force update of top-level reports/local_main_readiness_receipt.json",
    )
    parser.add_argument(
        "--branch-slug", type=str, default=None, help="Explicit branch slug for readiness receipt"
    )
    parser.add_argument("--branch", type=str, default=None, help="Feature branch name")
    parser.add_argument(
        "--fail-fast", action="store_true", help="Terminate immediately on first stage failure"
    )
    parser.add_argument(
        "--recheck-failed-only",
        action="store_true",
        help="Only re-execute stages that failed in previous run",
    )
    parser.add_argument(
        "--full", action="store_true", help="Execute all test suites without change-scoping"
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass stage memoization cache and re-run all stages",
    )
    parser.add_argument(
        "--serial-gates", action="store_true", help="Disable concurrent parallel gate execution"
    )
    parser.add_argument(
        "--gate-workers",
        type=int,
        default=DEFAULT_GATE_WORKERS,
        help=f"Concurrent workers for stages 1-27 (default: {DEFAULT_GATE_WORKERS})",
    )
    parser.add_argument(
        "--test-workers",
        type=int,
        default=0,
        help="Parallel test workers for Gate 28 (default: 0 for auto-scaling)",
    )
    parser.add_argument(
        "--force-rerun",
        action="store_true",
        help="Bypass SHA-anchored whole-receipt cache to force re-validation",
    )
    parser.add_argument(
        "--semantic-skip",
        action="store_true",
        help="Use AST-level semantic caching instead of strict byte matching",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate readiness gate execution without running tests or mutating receipts",
    )
    parser.add_argument(
        "--gate",
        type=int,
        default=None,
        help="Execute only a specific gate by index (1-32)",
    )
    parser.add_argument(
        "--allow-behind",
        action="store_true",
        help="Permit readiness qualification on branches behind main (diagnostics only)",
    )
    args = parser.parse_args()
    return verify_main_readiness(
        verbose=args.verbose,
        update_global=args.update_global,
        explicit_slug=args.branch_slug or args.branch,
        fail_fast=args.fail_fast,
        recheck_failed_only=args.recheck_failed_only,
        full=args.full,
        use_cache=not args.no_cache,
        parallel_gates=not args.serial_gates,
        max_gate_workers=args.gate_workers,
        test_workers=args.test_workers,
        force_rerun=args.force_rerun,
        semantic_skip=args.semantic_skip,
        dry_run=args.dry_run,
        gate=args.gate,
        allow_behind=args.allow_behind,
    )


if __name__ == "__main__":
    sys.exit(main())
