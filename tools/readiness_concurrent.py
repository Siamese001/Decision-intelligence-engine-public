import concurrent.futures
import datetime as dt
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from tools.readiness_cache import get_cached_stage, save_cached_stage
from tools.readiness_core import ROOT, run_stage
from tools.readiness_scope_collector import FILE_SCOPED_PYTHON_GATES, is_empty_scope_skippable


def _stage_future_timeout(cmd: List[str]) -> float:
    default_timeout = 900.0 if any("run_tests_with_progress.py" in arg for arg in cmd) else 360.0
    return float(os.environ.get("NEE_STAGE_TIMEOUT", default_timeout)) + 30.0


STAGE_P90_PRIORITIES: Dict[int, float] = {
    29: 120.0,  # Core Architectural & Governance Test Suite
    30: 15.0,   # Gate 10: E2E Coverage Audit Score
    15: 10.0,   # No Synthetic Embeddings Policy
    12: 5.0,    # Output Directory Hierarchy Gate
    25: 4.0,    # SSOT Configuration & Release Threshold Scanner
    33: 3.5,    # Gate 32: Direct Graph Write Path Linter
    32: 3.0,    # Gate 31: Copilot Active Graph Smoke Gate
    21: 2.5,    # Strict Type Gate
    27: 2.0,    # Raw SQLite Direct Usage Linter (Ruff TID251)
    3: 1.8,     # Module File Budget Linter (All Modules)
    2: 1.5,     # Architectural Layer & Cycle Linter
    18: 1.5,    # Cognitive Complexity Linter
    19: 1.5,    # AST Invariant & Data/Code Decoupling Linter
    34: 1.5,    # Test & Healer Ratchet Linter
    13: 1.2,    # Hardcoded Parameter & SSOT Linter
    14: 1.2,    # Parameter Reference & Parity Linter
    6: 1.0,     # Credential & Career Firewall Scanner
    8: 1.0,     # Mock Boundary Governance Linter
    4: 1.0,     # Production Purity & Derivation Integrity
    5: 0.8,     # Direct Stage U0 Anti-Scripting Linter
    1: 0.5,     # Model-Neutral Artifact Naming
    7: 0.5,     # Epistemic Boundaries Audit
    9: 0.5,     # HITL Ledger Merkle Integrity
    10: 0.5,    # Dual Pipeline Contracts Validation
    11: 0.5,    # Contract Parity & SSOT Linter
    16: 0.5,    # Runtime Evidence Controls
    17: 0.5,    # Wave Implementation Plan Schema Gate
    20: 0.5,    # Blast Radius Risk Linter
    22: 0.5,    # MCP Schema Overhead Audit
    23: 0.5,    # Workspace Skills Budget & Token Overhead Gate
    24: 0.5,    # Workspace Rules Budget & >90% Deduplication Gate
    26: 0.5,    # Prompt SSOT Integrity Linter
    28: 0.5,    # Wave Implementation Status Table Linter
    31: 0.5,    # Grounded Delivery & Claim Manifest Verification
}


def _execute_parallel_linter_gates(
    stages_slice: List[Tuple[str, List[str]]],
    total_stages: int,
    use_cache: bool,
    max_workers: int,
    head_sha: str,
    target_files: List[str],
    verbose: bool,
    repo_root: Optional[Path] = None,
    run_stage_fn: Optional[Any] = None,
    get_cached_stage_fn: Optional[Any] = None,
    save_cached_stage_fn: Optional[Any] = None,
    exempt_indices: Optional[Set[int]] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Execute static linter stages concurrently while reporting results in index order."""
    return _execute_unified_stages_ljf(
        stages_slice=stages_slice,
        total_stages=total_stages,
        use_cache=use_cache,
        max_workers=max_workers,
        head_sha=head_sha,
        target_files=target_files,
        verbose=verbose,
        repo_root=repo_root,
        run_stage_fn=run_stage_fn,
        get_cached_stage_fn=get_cached_stage_fn,
        save_cached_stage_fn=save_cached_stage_fn,
        exempt_indices=exempt_indices,
        full=True,
    )


def _execute_final_gates_concurrent(
    gate_28_tuple: Tuple[str, List[str]],
    gate_29_tuple: Tuple[str, List[str]],
    total_stages: int,
    use_cache: bool,
    head_sha: str,
    target_files: List[str],
    verbose: bool,
    repo_root: Optional[Path] = None,
    run_stage_fn: Optional[Any] = None,
    get_cached_stage_fn: Optional[Any] = None,
    save_cached_stage_fn: Optional[Any] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Execute Gate 28 and Gate 29 concurrently and merge results in index order."""
    stages = [
        (28, gate_28_tuple[0], gate_28_tuple[1]),
        (29, gate_29_tuple[0], gate_29_tuple[1]),
    ]
    return _execute_unified_stages_ljf(
        stages_slice=stages,
        total_stages=total_stages,
        use_cache=use_cache,
        max_workers=2,
        head_sha=head_sha,
        target_files=target_files,
        verbose=verbose,
        repo_root=repo_root,
        run_stage_fn=run_stage_fn,
        get_cached_stage_fn=get_cached_stage_fn,
        save_cached_stage_fn=save_cached_stage_fn,
        full=True,
    )



def _execute_unified_stages_ljf(
    stages_slice: List[Tuple[Any, ...]],
    total_stages: int,
    use_cache: bool,
    max_workers: int,
    head_sha: str,
    target_files: List[str],
    verbose: bool,
    repo_root: Optional[Path] = None,
    run_stage_fn: Optional[Any] = None,
    get_cached_stage_fn: Optional[Any] = None,
    save_cached_stage_fn: Optional[Any] = None,
    exempt_indices: Optional[Set[int]] = None,
    full: bool = False,
    fail_fast: bool = False,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Execute all stages concurrently under Longest-Job-First (LJF) priority scheduling.

    Stage 28 starts at t=0.0s, non-test stages run concurrently, and cache writes
    occur asynchronously inside worker threads without blocking the main thread.
    Results are returned in deterministic index order 1..total_stages.
    """
    root = repo_root or ROOT
    run_fn = run_stage_fn or (lambda name, cmd: run_stage(name, cmd, root))
    get_cache_fn = get_cached_stage_fn or get_cached_stage
    save_cache_fn = save_cached_stage_fn or save_cached_stage

    cached_map: Dict[int, Any] = {}
    pending_futures: Dict[int, Any] = {}
    overall_passed = True

    normalized_stages: List[Tuple[int, str, List[str]]] = []
    for idx_offset, item in enumerate(stages_slice):
        if len(item) == 3:
            normalized_stages.append((int(item[0]), str(item[1]), list(item[2])))
        else:
            normalized_stages.append((idx_offset + 1, str(item[0]), list(item[1])))

    stages_to_run: List[Tuple[int, str, List[str]]] = []
    for stage_idx, stage_name, cmd in normalized_stages:
        if exempt_indices and stage_idx in exempt_indices:
            continue
        if is_empty_scope_skippable(stage_idx, target_files, full):
            continue
        if use_cache:
            c = get_cache_fn(stage_name, cmd, root, head_sha, target_files)
            if c is not None:
                cached_map[stage_idx] = c
                continue
        stages_to_run.append((stage_idx, stage_name, cmd))

    # Sort stages_to_run by LJF priority so Stage 28 starts at t=0.0s
    stages_to_run.sort(
        key=lambda s: STAGE_P90_PRIORITIES.get(s[0], 0.5),
        reverse=True,
    )

    actual_workers = max(1, min(max_workers, len(stages_to_run))) if stages_to_run else 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=actual_workers) as executor:
        for stage_idx, stage_name, cmd in stages_to_run:
            submitted_at = time.monotonic()

            def _run(name=stage_name, command=cmd, queued_at=submitted_at, s_idx=stage_idx):
                started_at = dt.datetime.now(dt.timezone.utc).isoformat()
                queue_wait = time.monotonic() - queued_at
                ok, out, duration = run_fn(name, command)
                if use_cache and ok:
                    try:
                        save_cache_fn(name, command, root, head_sha, out, duration, target_files)
                    except Exception:
                        pass
                try:
                    from tools.gate_run_ledger import record_gate_run
                    record_gate_run(
                        name, "readiness", duration, "PASS" if ok else "FAIL",
                        cache_decision="miss" if use_cache else "disabled",
                        parent_phase="readiness", scope="stage", repo_root=root,
                    )
                except Exception:
                    pass
                finished_at = dt.datetime.now(dt.timezone.utc).isoformat()
                return ok, out, duration, queue_wait, started_at, finished_at

            pending_futures[stage_idx] = (stage_name, cmd, executor.submit(_run))

        stage_results: List[Dict[str, Any]] = []
        for stage_idx, stage_name, cmd in normalized_stages:
            if exempt_indices and stage_idx in exempt_indices:
                print(f"[{stage_idx}/{total_stages}] {stage_name}... ⏭️ EXEMPT (Proportional Gate)")
                stage_results.append({
                    "stage_index": stage_idx,
                    "stage_name": stage_name,
                    "status": "PASS",
                    "duration_seconds": 0.0,
                    "queue_wait_seconds": 0.0,
                    "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "finished_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "cache_decision": "proportional_exempt",
                    "command": "exempt under versioned gate manifest",
                    "output_summary": "Exempt under versioned gate manifest.",
                })
                continue

            if is_empty_scope_skippable(stage_idx, target_files, full):
                print(f"[{stage_idx}/{total_stages}] {stage_name}... ⚡ SKIPPED (empty target scope)")
                stage_results.append({
                    "stage_index": stage_idx,
                    "stage_name": stage_name,
                    "status": "PASS",
                    "duration_seconds": 0.0,
                    "queue_wait_seconds": 0.0,
                    "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "finished_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "cache_decision": "empty_scope_skip",
                    "command": " ".join(cmd),
                    "output_summary": "Skipped: 0 matching target files on branch.",
                })
                continue

            if stage_idx in cached_map:
                c = cached_map[stage_idx]
                print(f"[{stage_idx}/{total_stages}] {stage_name}... ⚡ CACHED ({c.duration_seconds}s)")
                res_dict = c.to_dict()
                res_dict["stage_index"] = stage_idx
                res_dict.update({"cache_decision": "hit", "queue_wait_seconds": 0.0})
                stage_results.append(res_dict)
                try:
                    from tools.gate_run_ledger import record_gate_run
                    record_gate_run(
                        stage_name, "readiness", c.duration_seconds, "PASS",
                        cache_hit=True, cache_decision="hit", parent_phase="readiness",
                        scope="stage", repo_root=root,
                    )
                except Exception:
                    pass
                continue

            _, _, future = pending_futures[stage_idx]
            f_timeout = _stage_future_timeout(cmd)
            try:
                passed, output, duration, wait, started_at, finished_at = future.result(timeout=f_timeout)
            except concurrent.futures.TimeoutError:
                now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
                passed, output, duration, wait, started_at, finished_at = (
                    False, f"Stage timed out after {f_timeout}s", round(f_timeout, 3), 0.0, now_iso, now_iso
                )
            status_label = "PASS" if passed else "FAIL"
            print(f"[{stage_idx}/{total_stages}] Running {stage_name}... {'✅' if passed else '❌'} {status_label} ({duration}s)")
            if not passed:
                overall_passed = False
                if verbose:
                    print("-" * 40 + f"\n{output}\n" + "-" * 40)
                if fail_fast:
                    for _, _, f in pending_futures.values():
                        f.cancel()

            stage_entry = {
                "stage_index": stage_idx,
                "stage_name": stage_name,
                "status": status_label,
                "duration_seconds": duration,
                "queue_wait_seconds": round(wait, 4),
                "started_at_utc": started_at,
                "finished_at_utc": finished_at,
                "cache_decision": "miss" if use_cache else "disabled",
                "command": " ".join(cmd),
                "output_summary": output[:500] if len(output) > 500 else output,
            }
            if not passed:
                try:
                    from tools.pr_governance_core.diagnostics import format_failed_stage_diagnostics

                    diag = format_failed_stage_diagnostics(stage_idx, stage_name, cmd, output, root)
                    stage_entry.update(diag)
                except Exception:
                    pass
            stage_results.append(stage_entry)

    return stage_results, overall_passed
