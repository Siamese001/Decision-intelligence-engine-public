#!/usr/bin/env python3
"""Core execution utilities and SHA-anchored receipt caching for readiness qualification."""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent


_RESOLVED_PYTHON_INTERPRETER: Optional[str] = None


def resolve_python_interpreter(repo_root: Optional[Path] = None) -> str:
    """Find the preferred Python interpreter with all virtualenv dependencies."""
    global _RESOLVED_PYTHON_INTERPRETER
    if repo_root is None and _RESOLVED_PYTHON_INTERPRETER is not None:
        return _RESOLVED_PYTHON_INTERPRETER
    if sys.prefix != sys.base_prefix and Path(sys.executable).is_file():
        res = sys.executable
    else:
        root = repo_root or ROOT
        v_py, w_py = root / ".venv" / "bin" / "python", root / ".venv" / "Scripts" / "python.exe"
        if v_py.exists() and os.access(v_py, os.X_OK):
            res = str(v_py)
        elif w_py.exists():
            res = str(w_py)
        else:
            res = sys.executable
            with contextlib.suppress(Exception):
                p = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=root, capture_output=True, text=True, timeout=15)
                if p.returncode == 0:
                    c_dir = Path(p.stdout.strip())
                    c_root = (root / c_dir).resolve().parent if not c_dir.is_absolute() else c_dir.resolve().parent
                    c_py = c_root / ".venv" / "bin" / "python"
                    if c_py.exists() and os.access(c_py, os.X_OK):
                        res = str(c_py)
    if repo_root is None:
        _RESOLVED_PYTHON_INTERPRETER = res
    return res


def run_stage(name: str, cmd: List[str], repo_root: Optional[Path] = None) -> Tuple[bool, str, float]:
    """Runs a gate stage command and measures execution time."""
    root = repo_root or ROOT
    start_time = time.monotonic()
    env = os.environ.copy()
    env.setdefault("TOKENIZERS_PARALLELISM", "false")
    cmd_str = " ".join(cmd)
    if any(k in cmd_str for k in ("pytest", "run_tests_with_progress.py", "verify_tier1_cached_replay.py", "verify_copilot_smoke.py")):
        env.setdefault("NEE_ALLOW_TEST_FIXTURES", "1")
    else:
        env.pop("NEE_ALLOW_TEST_FIXTURES", None)
    env.setdefault("GIT_OPTIONAL_LOCKS", "0")
    current_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{root}:{current_pp}" if current_pp else str(root)
    default_timeout = "900" if any("run_tests_with_progress.py" in arg for arg in cmd) else "360"
    stage_timeout = int(os.environ.get("NEE_STAGE_TIMEOUT", default_timeout))
    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=stage_timeout,
        )
        duration = round(time.monotonic() - start_time, 3)
        success = (proc.returncode == 0)
        output = proc.stdout if success else (proc.stdout + "\n" + proc.stderr)
        return success, output.strip(), duration
    except Exception as e:
        duration = round(time.monotonic() - start_time, 3)
        return False, str(e), duration


from tools.readiness_scope_collector import (
    _build_core_test_modules,
    collect_target_files,
    FILE_SCOPED_PYTHON_GATES,
    has_tier1_replay_scope,
    is_empty_scope_skippable,
    SSOT_GOVERNANCE_CONFIG_FILES,
)

# Stage 28 Core Test Suites registered via tools.readiness_scope_collector:
# tests.governance.test_worktree_ide_visibility


def get_branch_target_files(repo_root: Path) -> List[str]:
    """Get complete status-aware list of candidate files changed on branch vs main."""
    return collect_target_files(repo_root)



def _resolve_test_workers(cpu_count: int, num_modules: int, user_override: Optional[int] = None) -> int:
    """Calculate optimal test workers scaling to local CPUs while avoiding database contention."""
    if user_override is not None and user_override > 0:
        return min(user_override, max(1, num_modules))

    if num_modules == 0:
        return 1

    # Scaled for 18-core M5 Pro (12-14 test workers + 6 linter workers)
    hw_scaled = max(1, min(14, cpu_count - 2))
    return min(num_modules, hw_scaled)

def _build_stages(
    py: str,
    target_files: List[str],
    test_modules: List[str],
    test_workers: int = 0,
    repo_root: Optional[Path] = None,
    full: bool = False,
) -> List[Tuple[str, List[str]]]:
    """Define all verification stages."""
    root = repo_root or ROOT
    target_args = target_files if (target_files and not full) else []
    ssot_config_touched = any(f in SSOT_GOVERNANCE_CONFIG_FILES for f in target_files)
    ssot_target_args = [] if ssot_config_touched else target_args

    import os
    cpu_count = os.cpu_count() or 4
    num_mods = len(test_modules)
    override = test_workers if test_workers > 0 else None
    resolved_workers = _resolve_test_workers(cpu_count, num_mods, override)

    test_runner_cmd = [py, "-B", "tools/run_tests_with_progress.py"]
    if resolved_workers > 1:
        test_runner_cmd.extend(["-j", str(resolved_workers)])
    test_timeout = str(os.environ.get("NEE_TEST_TIMEOUT", "300"))
    test_runner_cmd.extend(["--timeout", test_timeout])
    test_runner_cmd.append("--cache-modules")
    test_runner_cmd.extend(test_modules)

    stages = [
        ("Model-Neutral Artifact Naming", [py, "-B", "tools/lint_model_neutral_naming.py"]),
        ("Architectural Layer & Cycle Linter", [py, "-B", "tools/lint_layer_imports.py"] + target_args),
        ("Module File Budget Linter (All Modules)", [py, "-B", "tools/lint_file_budgets.py"] + target_args),
        ("Production Purity & Derivation Integrity", [py, "-B", "tools/lint_production_purity.py"] + (target_files if (target_files and not full) else ["--all"])),
        ("Direct Stage U0 Anti-Scripting Linter", [py, "-B", "tools/lint_no_custom_ingest_scripts.py"] + target_args),
        ("Credential & Career Firewall Scanner", [py, "-B", "tools/scan_credential_leaks.py"] + target_args),
        ("Epistemic Boundaries Audit", [py, "-B", "tools/lint_epistemic_boundaries.py"]),
        ("Mock Boundary Governance Linter", [py, "-B", "tools/lint_mock_boundaries.py"] + target_args),
        ("HITL Ledger Merkle Integrity", [py, "-B", "tools/validate_hitl_ledger_integrity.py"]),
        ("Dual Pipeline Contracts Validation", [py, "-B", "tools/validate_dual_pipeline_contracts.py"]),
        ("Contract Parity & SSOT Linter", [py, "-B", "tools/lint_contract_parity.py", "--check"]),
        ("Output Directory Hierarchy Gate", [py, "-B", "tools/validate_output_hierarchy.py", "--check"] + target_args),
        ("Hardcoded Parameter & SSOT Linter", [py, "-B", "tools/lint_hardcoded_parameters.py"] + ssot_target_args),
        ("Parameter Reference & Parity Linter", [py, "-B", "tools/lint_parameter_references.py"] + ssot_target_args),
        ("No Synthetic Embeddings Policy", [py, "-B", "tools/validate_no_synthetic_embeddings.py", "--repository-root", str(root)] + target_args),
        ("Runtime Evidence Controls", [py, "-B", "-m", "dual_pipeline.runtime_evidence", "enforce-controls", "--repository-root", str(root)]),
        ("Wave Implementation Plan Schema Gate", [py, "-B", "tools/validate_implementation_plan.py", "--check-all"]),
        ("Cognitive Complexity Linter", [py, "-B", "tools/lint_cognitive_complexity.py", "--check", "--ratchet"] + target_args),
        ("AST Invariant & Data/Code Decoupling Linter", [py, "-B", "tools/lint_ast_invariants.py", "--check"] + target_args),
        ("Blast Radius Risk Linter", [py, "-B", "tools/lint_blast_radius.py", "--check"]),
        ("Strict Type Gate", [py, "-B", "tools/run_strict_type_gate.py", "--check"] + target_args),
        ("MCP Schema Overhead Audit", [py, "-B", "tools/audit_mcp_schema_overhead.py", "--check"]),
        ("Workspace Skills Budget & Token Overhead Gate", [py, "-B", "tools/audit_skills_budget.py", "--check"]),
        ("Workspace Rules Budget & >90% Deduplication Gate", [py, "-B", "tools/audit_rules_budget.py", "--check"]),
        ("SSOT Configuration & Release Threshold Scanner", [py, "-B", "tools/scan_ssot_configuration_violations.py"] + ssot_target_args),
        ("Prompt SSOT Integrity Linter", [py, "-B", "tools/lint_prompt_ssot_integrity.py", "--check"]),
        ("Raw SQLite Direct Usage Linter (Ruff TID251)", [py, "-m", "ruff", "check", "--select", "TID251", "."]),
        ("Wave Implementation Status Table Linter", [py, "-B", "tools/validate_implementation_summary.py", "--check-all"]),
        ("Core Architectural & Governance Test Suite", test_runner_cmd),
        ("Gate 10: E2E Coverage Audit Score (Dimension 2 ≥ 80%)", [py, "-B", "-m", "tools.coverage_report_reuse", "record", "--repo-root", str(root), "--min-e2e-score", "80", "--fail-on-threshold"]),
        ("Grounded Delivery & Claim Manifest Verification", [py, "-B", "tools/validate_staged_grounding.py", "--repository-root", str(root)] + target_args),
        ("Gate 31: Copilot Active Graph Smoke Gate", [py, "-B", "tools/verify_copilot_smoke.py"]),
        ("Gate 32: Direct Graph Write Path Linter", [py, "-B", "tools/lint_graph_write_paths.py"] + target_args),
        ("Test & Healer Ratchet Linter", [py, "-B", "tools/lint_test_and_healer_ratchet.py", "--repo-root", str(root)] + target_args),
    ]
    if has_tier1_replay_scope(target_files):
        stages.append(("Tier 1 Cached Stage Replay Gate", [py, "-B", "tools/verify_tier1_cached_replay.py"] + target_args))
    return stages


def _load_prior_failures(
    branch_receipt_path: Optional[Path],
    expected_head_sha: Optional[str] = None,
    expected_fingerprint: Optional[str] = None,
    expected_base_sha: Optional[str] = None,
) -> Tuple[Dict[int, Dict[str, Any]], Set[int]]:
    """Loads stages from previous receipt to identify failures for incremental rechecking.
    Requires matching identities; rejects stale results, FAIL, NOT_RUN and any non-PASS status.
    """
    prior_map: Dict[int, Dict[str, Any]] = {}
    failed: Set[int] = set()
    if branch_receipt_path and branch_receipt_path.exists():
        try:
            prior_data = json.loads(branch_receipt_path.read_text(encoding="utf-8"))
            receipt_sha = prior_data.get("head_commit_sha")
            if expected_head_sha and receipt_sha != expected_head_sha:
                return {}, set()
            receipt_fp = prior_data.get("qualification_fingerprint")
            if expected_fingerprint and receipt_fp != expected_fingerprint:
                return {}, set()
            if expected_base_sha and prior_data.get("base_commit_sha") != expected_base_sha:
                return {}, set()

            total_expected = max(32, int(prior_data.get("stages_evaluated", 32)))
            for s in prior_data.get("stages", []):
                idx = int(s.get("stage_index", 0))
                prior_map[idx] = s
                # Reject FAIL, NOT_RUN, SKIPPED, ERROR, and any non-PASS status
                if s.get("status") != "PASS":
                    failed.add(idx)
            for idx in range(1, total_expected + 1):
                if idx not in prior_map:
                    failed.add(idx)
        except Exception:
            pass
    return prior_map, failed


def _load_cached_receipt_for_sha(
    head_sha: str,
    branch_receipt_path: Optional[Path],
    qualification_fingerprint: Optional[str] = None,
    expected_base_sha: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Checks if a valid, passing readiness receipt already exists for the given HEAD SHA.
    Returns the receipt dictionary if found, otherwise None.
    """
    if not branch_receipt_path or not branch_receipt_path.exists():
        return None
    try:
        data = json.loads(branch_receipt_path.read_text(encoding="utf-8"))
        fingerprint_matches = qualification_fingerprint is None or (
            bool(data.get("qualification_fingerprint"))
            and data.get("qualification_fingerprint") == qualification_fingerprint
        )
        base_matches = expected_base_sha is None or (
            bool(data.get("base_commit_sha"))
            and data.get("base_commit_sha") == expected_base_sha
        )
        if (
            data.get("head_commit_sha") == head_sha
            and data.get("terminal_outcome") == "PASS"
            and fingerprint_matches
            and base_matches
        ):
            stages = data.get("stages")
            exp = int(data.get("stages_evaluated", 32))
            if isinstance(stages, list) and len(stages) >= max(6, exp):
                if all(
                    s.get("status") in ("PASS", "EXEMPT")
                    for s in stages
                    if isinstance(s, dict)
                ):
                    return data
    except Exception:
        pass
    return None


def _save_readiness_receipt(
    branch: str,
    head_sha: str,
    terminal_outcome: str,
    total_stages: int,
    stage_results: List[Dict[str, Any]],
    branch_receipt_path: Optional[Path],
    update_global: bool,
    repo_root: Optional[Path] = None,
    wall_clock_duration_seconds: Optional[float] = None,
    qualification_fingerprint: Optional[str] = None,
    proportional_plan: Optional[Dict[str, Any]] = None,
) -> None:
    """Emits sealed JSON readiness receipts at branch and global locations."""
    root = repo_root or ROOT
    receipt_dir = root / "reports"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    base_commit_sha = ""
    with contextlib.suppress(Exception):
        b_proc = subprocess.run(["git", "rev-parse", "main"], cwd=root, capture_output=True, text=True, check=True)
        base_commit_sha = b_proc.stdout.strip()

    cumulative_stage_seconds = round(sum(s["duration_seconds"] for s in stage_results), 3)
    wall_clock_seconds = round(cumulative_stage_seconds if wall_clock_duration_seconds is None else wall_clock_duration_seconds, 3)

    receipt_payload = {
        "receipt_id": f"receipt-main-readiness-{int(time.time())}",
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "branch": branch, "head_commit_sha": head_sha, "base_commit_sha": base_commit_sha,
        "manifest_version": proportional_plan.get("manifest_version", "1.0.0") if proportional_plan else "1.0.0",
        "terminal_outcome": terminal_outcome, "stages_evaluated": total_stages,
        "stages_passed": sum(1 for s in stage_results if s["status"] == "PASS"),
        "stages_failed": sum(1 for s in stage_results if s["status"] == "FAIL"),
        "total_duration_seconds": wall_clock_seconds,
        "wall_clock_duration_seconds": wall_clock_seconds,
        "cumulative_stage_duration_seconds": cumulative_stage_seconds,
        "cache_hits": sum(1 for s in stage_results if s.get("cache_decision") == "hit"),
        "cache_misses": sum(1 for s in stage_results if s.get("cache_decision") == "miss"),
        "qualification_fingerprint": qualification_fingerprint, "stages": stage_results,
    }
    if proportional_plan:
        receipt_payload.update({
            "proportional_plan": proportional_plan,
            "proportional_gates_applied": bool(proportional_plan.get("proportional_applied")),
            "risk_lane": proportional_plan.get("risk_lane"),
            "gate_manifest_version": proportional_plan.get("manifest_version"),
        })

    with contextlib.suppress(Exception):
        from tools.coverage_report_reuse import extract_retained_coverage_audit_metadata
        if cov_meta := extract_retained_coverage_audit_metadata(root, head_sha):
            receipt_payload["retained_coverage_audit"] = cov_meta

    if branch_receipt_path:
        branch_receipt_path.parent.mkdir(parents=True, exist_ok=True)
        with open(branch_receipt_path, "w", encoding="utf-8") as f:
            json.dump(receipt_payload, f, indent=2)

    receipt_path = receipt_dir / "local_main_readiness_receipt.json"
    if branch == "main" or update_global:
        with open(receipt_path, "w", encoding="utf-8") as f:
            json.dump(receipt_payload, f, indent=2)


def _resolve_branch_and_slug(repo_root: Optional[Path] = None, explicit_slug: Optional[str] = None) -> Tuple[str, str, str]:
    root = repo_root or ROOT
    branch, head_sha = "unknown", "unknown"
    with contextlib.suppress(Exception):
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root, capture_output=True, text=True, check=True, timeout=15).stdout.strip()
        head_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True, timeout=15).stdout.strip()

    if explicit_slug:
        with contextlib.suppress(Exception):
            chk = subprocess.run(["git", "rev-parse", "--verify", explicit_slug], cwd=root, capture_output=True, text=True, check=True, timeout=15)
            if chk.returncode == 0 and (sha := chk.stdout.strip()):
                branch, head_sha = explicit_slug, sha

    slug = explicit_slug or branch
    with contextlib.suppress(Exception):
        from tools.pr_governance_core.common import slugify_branch
        return branch, head_sha, slugify_branch(slug)
    return branch, head_sha, re.sub(r"[^a-zA-Z0-9_]", "_", slug).strip("_")
