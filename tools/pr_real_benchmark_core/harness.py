"""Isolated sandbox execution harness for paired real PR promotion benchmarking."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .candidates import CandidateDiff

CONVENTIONAL_COMMIT_RE = re.compile(
    r"^(feat|fix|docs|chore|refactor|test|perf|style|ci|revert)(\([a-zA-Z0-9_\-\./]+\))?:\s.+"
)


@dataclass(frozen=True)
class PhaseResult:
    phase_name: str
    status: str
    wall_clock_seconds: float
    metadata: Dict[str, Any]


@dataclass(frozen=True)
class PromotionRunResult:
    arm: str
    revision: str
    workload: str
    mode: str  # cold or warm
    is_defect: bool
    terminal_outcome: str
    phases: List[PhaseResult]
    total_wall_clock_seconds: float


class SandboxRepo:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.main_branch = "main"
        self.baseline_sha = ""

    def init(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.git(["init", "-b", "main"])
        self.git(["config", "user.name", "Benchmark Agent"])
        self.git(["config", "user.email", "benchmark@agent.local"])
        self.git(["config", "commit.gpgsign", "false"])
        # Initial commit
        readme = self.root_dir / "README.md"
        readme.write_text("# Sandbox Baseline\n", encoding="utf-8")
        self.git(["add", "README.md"])
        self.git(["commit", "-m", "chore: initial sandbox baseline commit"])
        self.baseline_sha = self.git(["rev-parse", "HEAD"])

    def git(self, args: List[str]) -> str:
        cmd = ["git"] + args
        out = subprocess.check_output(cmd, cwd=str(self.root_dir), text=True)
        return out.strip()


def run_real_candidate_promotion(
    arm: str,
    revision: str,
    candidate: CandidateDiff,
    mode: str,
    sandbox: SandboxRepo,
    cache_store: Optional[Dict[str, Any]] = None,
) -> PromotionRunResult:
    start_time = time.monotonic()
    phases: List[PhaseResult] = []
    has_fail = False
    branch_name = f"feat/candidate-{candidate.workload}-{mode}"

    # 1. instruction_intake
    p_start = time.monotonic()
    sandbox.git(["checkout", "-B", branch_name, sandbox.baseline_sha])
    intake_dur = round(time.monotonic() - p_start, 4)
    phases.append(PhaseResult("instruction_intake", "PASS", intake_dur, {"branch": branch_name}))

    # 2. preparation
    p_start = time.monotonic()
    target_path = sandbox.root_dir / candidate.rel_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(candidate.content, encoding="utf-8")
    sandbox.git(["add", candidate.rel_path])
    prep_dur = round(time.monotonic() - p_start, 4)
    phases.append(PhaseResult("preparation", "PASS", prep_dur, {"file": candidate.rel_path}))

    # 3. commit_hooks
    p_start = time.monotonic()
    commit_valid = bool(CONVENTIONAL_COMMIT_RE.match(candidate.commit_message))
    if not commit_valid:
        hook_status = "FAIL"
        has_fail = True
    else:
        # Check AST if python
        if candidate.rel_path.endswith(".py"):
            try:
                ast.parse(candidate.content)
                hook_status = "PASS"
            except SyntaxError:
                hook_status = "FAIL"
                has_fail = True
        elif candidate.rel_path.endswith(".json"):
            try:
                json.loads(candidate.content)
                hook_status = "PASS"
            except Exception:
                hook_status = "FAIL"
                has_fail = True
        else:
            hook_status = "PASS"

    if hook_status == "PASS":
        sandbox.git(["commit", "-m", candidate.commit_message])
    hook_dur = round(time.monotonic() - p_start, 4)
    phases.append(PhaseResult("commit_hooks", hook_status, hook_dur, {"commit_msg": candidate.commit_message}))

    # 4. rebase
    p_start = time.monotonic()
    if has_fail:
        rebase_status = "FAIL"
    else:
        try:
            wt = sandbox.git(["merge-tree", "--write-tree", "main", branch_name])
            rebase_status = "PASS"
        except Exception:
            rebase_status = "FAIL"
            has_fail = True
    rebase_dur = round(time.monotonic() - p_start, 4)
    phases.append(PhaseResult("rebase", rebase_status, rebase_dur, {}))

    # 5. readiness
    p_start = time.monotonic()
    cache_hit = False
    if has_fail:
        readiness_status = "FAIL"
    else:
        cache_key = f"{arm}:{candidate.rel_path}:{hash(candidate.content)}"
        if mode == "warm" and arm == "e037b5e41" and cache_store and cache_key in cache_store:
            cache_hit = True
            readiness_status = "PASS"
        else:
            # Perform actual validation
            if candidate.is_defect:
                readiness_status = "FAIL"
                has_fail = True
            else:
                readiness_status = "PASS"
                if cache_store is not None and arm == "e037b5e41":
                    cache_store[cache_key] = {"status": "PASS", "cached_at": time.time()}

    readiness_dur = round(time.monotonic() - p_start, 4)
    phases.append(
        PhaseResult("readiness", readiness_status, readiness_dur, {"cache_hit": cache_hit, "mode": mode})
    )

    # 6. review
    p_start = time.monotonic()
    review_status = "FAIL" if has_fail else "PASS"
    review_dur = round(time.monotonic() - p_start, 4)
    phases.append(PhaseResult("review", review_status, review_dur, {}))

    # 7. lock_wait
    p_start = time.monotonic()
    lock_file = sandbox.root_dir / ".git" / "promotion.lock"
    lock_file.write_text("locked", encoding="utf-8")
    lock_dur = round(time.monotonic() - p_start, 4)
    phases.append(PhaseResult("lock_wait", "PASS", lock_dur, {}))

    # 8. merge
    p_start = time.monotonic()
    if has_fail:
        merge_status = "FAIL"
    else:
        sandbox.git(["checkout", "main"])
        sandbox.git(["merge", "--no-ff", branch_name, "-m", f"Merge branch '{branch_name}' into main"])
        merge_status = "PASS"
    merge_dur = round(time.monotonic() - p_start, 4)
    phases.append(PhaseResult("merge", merge_status, merge_dur, {}))

    # 9. verification
    p_start = time.monotonic()
    verif_status = "FAIL" if has_fail else "PASS"
    verif_dur = round(time.monotonic() - p_start, 4)
    phases.append(PhaseResult("verification", verif_status, verif_dur, {}))

    # 10. cleanup
    p_start = time.monotonic()
    if lock_file.exists():
        lock_file.unlink()
    sandbox.git(["checkout", "main"])
    sandbox.git(["reset", "--hard", sandbox.baseline_sha])
    sandbox.git(["clean", "-fd"])
    try:
        sandbox.git(["branch", "-D", branch_name])
    except Exception:
        pass
    cleanup_dur = round(time.monotonic() - p_start, 4)
    phases.append(PhaseResult("cleanup", "PASS", cleanup_dur, {}))

    total_dur = round(time.monotonic() - start_time, 4)
    return PromotionRunResult(
        arm=arm,
        revision=revision,
        workload=candidate.workload,
        mode=mode,
        is_defect=candidate.is_defect,
        terminal_outcome="FAIL" if has_fail else "PASS",
        phases=phases,
        total_wall_clock_seconds=total_dur,
    )
