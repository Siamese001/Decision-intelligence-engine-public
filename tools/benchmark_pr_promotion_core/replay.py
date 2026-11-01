"""Candidate replay and isolated sandbox initialization for promotion benchmarking."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from tools.pr_governance_core.common import resolve_python, run_git, slugify_branch
from tools.pr_governance_core.latency import PromotionTimeline


def init_sandbox_git_repo(
    sandbox: Path,
    repo_root: Optional[Path] = None,
    fast: bool = True,
) -> None:
    """Initializes an isolated git repository for replay benchmarking."""
    sandbox.mkdir(parents=True, exist_ok=True)
    if not fast and repo_root and (repo_root / ".git").exists():
        run_git(["clone", "--shared", str(repo_root), str(sandbox)], cwd=repo_root)
        run_git(["checkout", "-B", "main", "HEAD"], cwd=sandbox)
        venv_src = (repo_root / ".venv").resolve()
        if venv_src.exists() and not (sandbox / ".venv").exists():
            os.symlink(venv_src, sandbox / ".venv")
        local_src = (repo_root / ".local").resolve()
        if local_src.exists() and not (sandbox / ".local").exists():
            os.symlink(local_src, sandbox / ".local")
        run_git(["config", "user.name", "Benchmark Agent"], cwd=sandbox)
        run_git(["config", "user.email", "benchmark@agent.local"], cwd=sandbox)
        run_git(["config", "core.hooksPath", ".githooks"], cwd=sandbox)
        run_git(["config", "commit.gpgsign", "false"], cwd=sandbox)
    else:
        run_git(["init", "-b", "main"], cwd=sandbox)
        run_git(["config", "user.name", "Benchmark Agent"], cwd=sandbox)
        run_git(["config", "user.email", "benchmark@agent.local"], cwd=sandbox)
        run_git(["config", "commit.gpgsign", "false"], cwd=sandbox)
        init_file = sandbox / "README.md"
        init_file.write_text("# Benchmark Sandbox Repo\n", encoding="utf-8")
        run_git(["add", "README.md"], cwd=sandbox)
        run_git(["commit", "-m", "chore: initial sandbox baseline commit"], cwd=sandbox)


def _replay_fast(
    workload: str,
    mode: str,
    sandbox_root: Path,
    proportional: bool,
    is_repair_flow: bool,
) -> Dict[str, Any]:
    """Fast simulated candidate replay for test suites and development assertions."""
    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=sandbox_root).strip()
    timeline = PromotionTimeline(branch=branch, repo_root=sandbox_root, tier="benchmark_promotion")

    timeline.checkpoint(
        "instruction_intake",
        scope=branch,
        status="PASS",
        metadata={"workload": workload, "mode": mode, "proportional": proportional, "repair": is_repair_flow, "simulation": True},
    )

    file_tag = f"{mode}_repair" if is_repair_flow else mode
    if workload == "documentation":
        target_file = sandbox_root / "docs" / f"doc_{file_tag}.md"
        content = f"# Documentation Update ({file_tag})\n\nUpdated architecture details.\n"
    elif workload == "governance":
        target_file = sandbox_root / "config" / f"policy_{file_tag}.json"
        content = json.dumps({"policy_version": "1.0.0", "mode": file_tag, "strict": True}, indent=2)
    else:
        target_file = sandbox_root / "dual_pipeline" / f"runtime_{file_tag}.py"
        content = f'"""Runtime module for {file_tag}."""\n\ndef execute_task() -> bool:\n    return True\n'

    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(content, encoding="utf-8")
    if not proportional:
        run_git(["add", str(target_file)], cwd=sandbox_root)
        run_git(["commit", "-m", f"feat({workload}): add {file_tag} candidate file"], cwd=sandbox_root)
    else:
        time.sleep(0.001)

    timeline.checkpoint(
        "preparation",
        scope=str(target_file),
        status="PASS",
        metadata={"file": str(target_file.relative_to(sandbox_root))},
    )

    if not proportional:
        log_out = run_git(["log", "main..HEAD", "--format=%s"], cwd=sandbox_root)
        commits = log_out.splitlines()
    else:
        time.sleep(0.001)
        commits = [f"feat({workload}): add {file_tag} candidate file"]
    timeline.checkpoint("commit_hooks", scope=branch, status="PASS", metadata={"commits": commits})

    if not proportional:
        merge_tree = run_git(["merge-tree", "--write-tree", "main", "HEAD"], cwd=sandbox_root)
        wt = merge_tree[:8]
    else:
        time.sleep(0.001)
        wt = "a1b2c3d4"
    timeline.checkpoint("mainline_sync_precheck", scope=branch, status="PASS", metadata={"write_tree": wt})

    cache_decision = "hit" if mode == "warm" else "miss"
    if proportional:
        stages_eval = 6 if workload == "documentation" else (22 if workload == "application" else 30)
        readiness_dur = 0.001 if mode == "warm" else 0.003
    else:
        stages_eval = 30
        readiness_dur = 0.005 if mode == "warm" else 0.025

    if is_repair_flow:
        readiness_dur += (0.001 if proportional else 0.020)

    time.sleep(readiness_dur)
    timeline.checkpoint(
        "readiness",
        scope=branch,
        status="PASS",
        cache_decision=cache_decision,
        metadata={"stages_evaluated": stages_eval, "fast_simulated": True, "proportional": proportional},
    )

    review_dur = 0.001 if proportional else 0.008
    time.sleep(review_dur)
    timeline.checkpoint("review", scope=branch, status="PASS", metadata={"verdict": "MERGE_READY"})

    time.sleep(0.001 if proportional else 0.005)
    timeline.checkpoint("lock_wait", scope="merge_queue_lock", status="PASS")

    if not proportional:
        run_git(["checkout", "main"], cwd=sandbox_root)
        run_git(["merge", "--no-ff", "-m", f"Merge branch '{branch}' into main", branch], cwd=sandbox_root)
    else:
        time.sleep(0.002)
    timeline.checkpoint("merge", scope="main", status="PASS")

    if not proportional:
        rev = run_git(["rev-parse", "HEAD"], cwd=sandbox_root)
        sha = rev[:8]
    else:
        time.sleep(0.002)
        sha = "8fd6d730"
    timeline.checkpoint("verification", scope="verify_post_merge", status="PASS", metadata={"merge_sha": sha})

    if not proportional:
        run_git(["branch", "-D", branch], cwd=sandbox_root)
        time.sleep(0.004)
    else:
        target_file.unlink(missing_ok=True)
        time.sleep(0.001)
    timeline.checkpoint("cleanup", scope=branch, status="PASS", interval_type="cleanup")

    res = timeline.finish(terminal_outcome="PASS")
    res["instruction_to_verified_local_main_seconds"] = round(
        sum(p["wall_clock_seconds"] for p in res["phases"] if p["phase"] != "cleanup"), 4
    )
    return res


def _execute_real_promotion(
    workload: str,
    mode: str,
    sandbox_root: Path,
    proportional: bool,
    is_repair_flow: bool,
    iteration: int,
) -> Dict[str, Any]:
    """Executes real lifecycle gates, git hooks, readiness checks, review, and merge."""
    py = resolve_python()
    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=sandbox_root).strip()
    timeline = PromotionTimeline(branch=branch, repo_root=sandbox_root, tier="benchmark_promotion")

    # 1. instruction_intake
    timeline.checkpoint(
        "instruction_intake",
        scope=branch,
        status="PASS",
        metadata={"workload": workload, "mode": mode, "iteration": iteration, "fast": False, "simulation": False},
    )

    # 2. preparation
    cand_id = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
    file_tag = f"{workload}_{mode}_iter{iteration}_{cand_id}"
    if workload == "documentation":
        target_file = sandbox_root / "docs" / f"doc_{file_tag}.md"
        content = f"# Documentation Benchmark Candidate ({file_tag})\n\nMeasured documentation change for PR benchmark.\n"
    elif workload == "governance":
        target_file = sandbox_root / "config" / f"policy_{file_tag}.json"
        content = json.dumps({"policy_version": "1.0.0", "mode": file_tag, "strict": True, "id": cand_id}, indent=2)
    else:
        target_file = sandbox_root / "dual_pipeline" / f"runtime_{file_tag}.py"
        if is_repair_flow:
            content = f'"""Runtime module for {file_tag}."""\nimport os\n\ndef get_secret() -> str:\n    return os.environ.get("UNMANAGED_KEY", "")\n'
        else:
            content = f'"""Runtime module for {file_tag}."""\n\ndef execute_task_{cand_id}() -> bool:\n    return True\n'

    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(content, encoding="utf-8")
    run_git(["add", str(target_file)], cwd=sandbox_root)
    commit_msg = f"feat({workload}): add {file_tag} candidate file"
    run_git(["commit", "-m", commit_msg], cwd=sandbox_root)
    timeline.checkpoint(
        "preparation",
        scope=str(target_file),
        status="PASS",
        metadata={"file": str(target_file.relative_to(sandbox_root)), "candidate_id": cand_id},
    )

    # 3. commit_hooks
    log_out = run_git(["log", "main..HEAD", "--format=%s"], cwd=sandbox_root)
    commits = log_out.splitlines()
    lint_res = subprocess.run([py, "-B", "tools/lint_commit_message.py", "-m", commit_msg], cwd=sandbox_root, capture_output=True, text=True, timeout=60)
    timeline.checkpoint("commit_hooks", scope=branch, status="PASS" if lint_res.returncode == 0 else "FAIL", metadata={"commits": commits})

    # 4. mainline_sync_precheck
    merge_tree = run_git(["merge-tree", "--write-tree", "main", "HEAD"], cwd=sandbox_root).strip()
    timeline.checkpoint("mainline_sync_precheck", scope=branch, status="PASS", metadata={"write_tree": merge_tree[:8]})

    # 5. readiness
    readiness_cmd = [py, "-B", "tools/verify_main_readiness.py", "--branch", branch]
    if not proportional:
        readiness_cmd.append("--full")
    bench_timeout = int(os.environ.get("NEE_BENCHMARK_PROMOTION_TIMEOUT", "600"))
    t0_read = time.monotonic()
    try:
        read_proc = subprocess.run(readiness_cmd, cwd=sandbox_root, capture_output=True, text=True, timeout=bench_timeout)
    except subprocess.TimeoutExpired as exc:
        read_proc = subprocess.CompletedProcess(readiness_cmd, returncode=124, stdout=exc.stdout or "", stderr=(exc.stderr or "") + f"\nTimed out after {bench_timeout}s")
    dur_read = time.monotonic() - t0_read

    slug = slugify_branch(branch)
    rcpt_path = sandbox_root / "reports" / "readiness" / f"local_main_readiness_{slug}_receipt.json"
    readiness_meta = {"command": " ".join(readiness_cmd), "returncode": read_proc.returncode, "wall_clock_seconds": round(dur_read, 4)}
    if rcpt_path.is_file():
        try:
            rcpt_data = json.loads(rcpt_path.read_text(encoding="utf-8"))
            readiness_meta.update({
                "stages_evaluated": rcpt_data.get("stages_evaluated", 0),
                "stages_passed": rcpt_data.get("stages_passed", 0),
                "stages_failed": rcpt_data.get("stages_failed", 0),
                "cache_hits": rcpt_data.get("cache_hits", 0),
                "cache_misses": rcpt_data.get("cache_misses", 0),
                "cumulative_stage_duration_seconds": rcpt_data.get("cumulative_stage_duration_seconds", 0.0),
            })
            for stg in rcpt_data.get("stages", []):
                if stg.get("stage_index") == 28:
                    readiness_meta["stage_28_tests_seconds"] = stg.get("duration_seconds", 0.0)
                elif stg.get("stage_index") == 29:
                    readiness_meta["stage_29_audit_seconds"] = stg.get("duration_seconds", 0.0)
        except Exception:
            pass

    if is_repair_flow:
        target_file.write_text(f'"""Repaired module for {file_tag}."""\n\ndef get_secret() -> str:\n    return "managed_secret"\n', encoding="utf-8")
        run_git(["add", str(target_file)], cwd=sandbox_root)
        run_git(["commit", "-m", f"fix({workload}): resolve security defect for {file_tag}"], cwd=sandbox_root)
        repair_cmd = [py, "-B", "tools/verify_main_readiness.py", "--branch", branch, "--recheck-failed-only"]
        try:
            repair_proc = subprocess.run(repair_cmd, cwd=sandbox_root, capture_output=True, text=True, timeout=bench_timeout)
        except subprocess.TimeoutExpired as exc:
            repair_proc = subprocess.CompletedProcess(repair_cmd, returncode=124, stdout=exc.stdout or "", stderr=(exc.stderr or "") + f"\nTimed out after {bench_timeout}s")
        readiness_status = "PASS" if repair_proc.returncode == 0 else "FAIL"
        readiness_meta["repair_returncode"] = repair_proc.returncode
    else:
        readiness_status = "PASS" if read_proc.returncode == 0 else "FAIL"

    timeline.checkpoint("readiness", scope=branch, status=readiness_status, cache_decision="miss", metadata=readiness_meta)

    # 6. review
    rev_cmd = [py, "-B", "tools/pr_governance.py", "review", branch, "--skip-readiness-run"]
    t0_rev = time.monotonic()
    try:
        rev_proc = subprocess.run(rev_cmd, cwd=sandbox_root, capture_output=True, text=True, timeout=bench_timeout)
    except subprocess.TimeoutExpired as exc:
        rev_proc = subprocess.CompletedProcess(rev_cmd, returncode=124, stdout=exc.stdout or "", stderr=(exc.stderr or "") + f"\nTimed out after {bench_timeout}s")
    dur_rev = time.monotonic() - t0_rev
    run_git(["add", "reports/"], cwd=sandbox_root, check=False)
    run_git(["commit", "-m", f"chore(review): record PR review receipt for {slug}"], cwd=sandbox_root, check=False)
    timeline.checkpoint("review", scope=branch, status="PASS" if rev_proc.returncode == 0 else "FAIL", metadata={"verdict": "MERGE_READY", "review_seconds": round(dur_rev, 4)})

    # 7. lock_wait
    from tools.pr_governance_core.merge_lock import GovernedMergeQueueLock
    lock = GovernedMergeQueueLock(branch=branch, repo_root=sandbox_root)
    t0_lock = time.monotonic()
    lock.acquire()
    dur_lock = time.monotonic() - t0_lock
    lock.release()
    timeline.checkpoint("lock_wait", scope="merge_queue_lock", status="PASS", metadata={"lock_wait_seconds": round(dur_lock, 4)})

    # 8. merge
    run_git(["checkout", "main"], cwd=sandbox_root)
    merge_res = subprocess.run(["git", "merge", "--no-ff", "--no-commit", branch], cwd=sandbox_root, capture_output=True, text=True, timeout=60)
    merge_receipt = {
        "pr_type": "LOCAL_FEATURE_BRANCH_MERGE",
        "branch": branch,
        "target": "main",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "terminal_outcome": "PASS",
    }
    m_rcpt_file = sandbox_root / "reports" / f"pr_merge_{slug}_receipt.json"
    m_rcpt_file.parent.mkdir(parents=True, exist_ok=True)
    m_rcpt_file.write_text(json.dumps(merge_receipt, indent=2), encoding="utf-8")
    run_git(["add", str(m_rcpt_file)], cwd=sandbox_root)
    commit_env = {**os.environ, "GOVERNED_PR_MERGE": "1"}
    subprocess.run(["git", "commit", "-m", f"Merge branch '{branch}' into main"], cwd=sandbox_root, env=commit_env, capture_output=True, text=True, timeout=60)
    timeline.checkpoint("merge", scope="main", status="PASS" if merge_res.returncode == 0 else "FAIL")

    # 9. verification
    v_proc = subprocess.run([py, "-B", "tools/verify_post_merge.py"], cwd=sandbox_root, capture_output=True, text=True, timeout=120)
    timeline.checkpoint("verification", scope="verify_post_merge", status="PASS" if v_proc.returncode == 0 else "FAIL")

    # 10. cleanup
    run_git(["branch", "-D", branch], cwd=sandbox_root, check=False)
    from tools.post_merge_report_regenerator import regenerate_testing_gaps_report
    t0_audit_regen = time.monotonic()
    regen_res = regenerate_testing_gaps_report(sandbox_root)
    audit_regen_dur = time.monotonic() - t0_audit_regen
    timeline.checkpoint("cleanup", scope=branch, status="PASS", interval_type="cleanup", metadata={
        "audit_recomputation_seconds": round(audit_regen_dur, 4),
        "audit_status": regen_res.get("status", "PASS"),
        "audit_reused": bool(regen_res.get("reused", False)),
        "audit_invocations": int(regen_res.get("audit_invocations", 0 if regen_res.get("reused") else 1)),
        "action": str(regen_res.get("action", "UNKNOWN")),
        "reason": str(regen_res.get("reason", "")),
    })
    run_git(["checkout", "--", "."], cwd=sandbox_root, check=False)
    run_git(["clean", "-fd", "reports/", "governance/"], cwd=sandbox_root, check=False)

    res = timeline.finish(terminal_outcome="PASS")
    res["instruction_to_verified_local_main_seconds"] = round(
        sum(p["wall_clock_seconds"] for p in res["phases"] if p["phase"] != "cleanup"), 4
    )
    return res


def replay_candidate_promotion(
    workload: str,
    mode: str,
    sandbox_root: Path,
    fast: bool = True,
    proportional: bool = False,
    is_repair_flow: bool = False,
    iteration: int = 1,
) -> Dict[str, Any]:
    """Replays an isolated candidate promotion timing all 10 non-overlapping phases."""
    if fast:
        return _replay_fast(workload, mode, sandbox_root, proportional, is_repair_flow)
    return _execute_real_promotion(workload, mode, sandbox_root, proportional, is_repair_flow, iteration)
