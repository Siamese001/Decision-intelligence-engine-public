#!/usr/bin/env python3
"""Unified Pre-Commit Orchestrator.

Consolidates pre-commit gate executions into a single Python process to eliminate
repeated process spawn overhead during commit and rebase operations.

Steps executed sequentially:
1. Tier 1 Fast Structural Gatekeeper (AST, Linter, Credentials, Hierarchy)
2. Runtime Evidence & Control File Invariants
3. Synthetic Embedding Ban Scanner (Staged)
4. Staged Grounding & Delivery Boundary Invariants
5. Static Regex Safety & ReDoS Prevention
6. Fast Static Invariant Test Suite
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if sys.prefix == sys.base_prefix:
    venv_py = (
        (ROOT / ".venv" / "bin" / "python")
        if os.name != "nt"
        else (ROOT / ".venv" / "Scripts" / "python.exe")
    )
    if venv_py.exists():
        os.execv(str(venv_py), [str(venv_py)] + sys.argv)


def _step_prompt_mirror(
    repository_root: Path, staged: bool, verbose: bool, record_gate_run: Any
) -> int:
    if not (repository_root / "automations" / "prompts").is_dir():
        return 0
    t0 = time.monotonic()
    from tools.lint_prompt_ssot import run_prompt_ssot_lint

    ok, errors = run_prompt_ssot_lint(repository_root)
    d0 = time.monotonic() - t0
    if record_gate_run:
        record_gate_run(
            "pre_commit_prompt_mirror",
            "pre_commit",
            d0,
            "PASS" if ok else "FAIL",
            metadata={"staged": staged, "check_only": True, "errors": len(errors)},
        )
    if not ok:
        print(f"❌ Step 0 failed in {d0:.2f}s (prompt SSOT check):")
        for err in errors[:5]:
            print(f"   - {err}")
        return 1
    return 0


def _resolve_git_dir_for_receipt(repository_root: Path) -> Path:
    import subprocess

    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"}
    }
    git_dir = repository_root / ".git"
    try:
        g_res = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=repository_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if g_res.returncode == 0 and g_res.stdout.strip():
            cand = Path(g_res.stdout.strip())
            git_dir = cand if cand.is_absolute() else (repository_root / cand)
    except Exception:
        pass
    return git_dir


def _step_test_and_healer_ratchet(
    repository_root: Path, staged: bool, record_gate_run: Any
) -> int:
    t0 = time.monotonic()
    from tools.lint_test_and_healer_ratchet import run_ratchet_audit

    violations = run_ratchet_audit(repository_root)
    d_ratchet = time.monotonic() - t0
    if record_gate_run:
        record_gate_run(
            "pre_commit_test_and_healer_ratchet",
            "pre_commit",
            d_ratchet,
            "PASS" if not violations else "FAIL",
            metadata={"staged": staged, "violations": len(violations)},
        )
    if violations:
        print(f"❌ Test & Healer Ratchet failed in {d_ratchet:.2f}s:")
        for v in violations[:10]:
            print(f"   - {v}")
        return 1
    return 0


def write_pre_commit_receipt(repository_root: Path, staged: bool) -> Path:
    """Writes `.git/nee_pre_commit_receipt.json` binding the staged git tree digest."""
    import json
    import subprocess

    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"}
    }
    tree_sha = ""
    git_dir = repository_root / ".git"
    try:
        t_res = subprocess.run(
            ["git", "write-tree"],
            cwd=repository_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if t_res.returncode == 0:
            tree_sha = t_res.stdout.strip()
        g_res = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=repository_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if g_res.returncode == 0 and g_res.stdout.strip():
            cand = Path(g_res.stdout.strip())
            git_dir = cand if cand.is_absolute() else (repository_root / cand)
    except Exception:
        pass

    receipt_path = (git_dir if git_dir.is_dir() else repository_root / ".git") / "nee_pre_commit_receipt.json"
    if not receipt_path.parent.is_dir():
        receipt_path = repository_root / ".nee_pre_commit_receipt.json"
    payload = {
        "schema_version": "1.0.0",
        "terminal_outcome": "PASS",
        "tree_sha": tree_sha,
        "staged": staged,
        "timestamp": time.time(),
    }
    try:
        receipt_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass
    return receipt_path


def verify_pre_commit_receipt_for_head(repository_root: Path) -> tuple[bool, str]:
    """Verifies that HEAD commit tree matches the pre-commit receipt (`--no-verify` detection)."""
    import json
    import subprocess

    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"}
    }
    git_dir = repository_root / ".git"
    try:
        g_res = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=repository_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if g_res.returncode == 0 and g_res.stdout.strip():
            cand = Path(g_res.stdout.strip())
            git_dir = cand if cand.is_absolute() else (repository_root / cand)
        head_tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=repository_root,
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except Exception as exc:
        return False, f"Failed to resolve HEAD tree: {exc}"

    receipt_path = git_dir / "nee_pre_commit_receipt.json"
    if not receipt_path.is_file():
        return False, "Missing .git/nee_pre_commit_receipt.json (`git commit --no-verify` detected)."
    try:
        data = json.loads(receipt_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"Corrupt pre-commit receipt: {exc}"
    if data.get("terminal_outcome") != "PASS" or data.get("tree_sha") != head_tree:
        return (
            False,
            f"Pre-commit receipt tree_sha mismatch ({data.get('tree_sha')} != {head_tree}); `--no-verify` bypass detected.",
        )
    return True, "Verified"


def _step1_tier1(staged: bool, verbose: bool, record_gate_run: Any) -> int:
    print("\n[Step 1/6] Running Tier 1 Structural Gatekeeper...")
    from tools.verify_commit import verify_commit

    t0 = time.monotonic()
    rc1 = verify_commit(staged=staged, verbose=verbose)
    d1 = time.monotonic() - t0
    if record_gate_run:
        record_gate_run(
            "pre_commit_tier1",
            "pre_commit",
            d1,
            "PASS" if rc1 == 0 else "FAIL",
            metadata={"staged": staged},
        )
    if rc1 != 0:
        print(f"❌ Step 1 failed in {d1:.2f}s")
        return rc1
    print(f"✅ Step 1 passed in {d1:.2f}s")
    return 0


def _step2_runtime_evidence(repository_root: Path, record_gate_run: Any) -> int:
    print("\n[Step 2/6] Enforcing Runtime Evidence Controls...")
    t0 = time.monotonic()
    from dual_pipeline.runtime_evidence import validate_control_files

    errors = validate_control_files(repository_root)
    if not (repository_root / "documentation").is_dir():
        errors = [e for e in errors if "documentation/" not in e]
    d2 = time.monotonic() - t0
    if record_gate_run:
        record_gate_run(
            "pre_commit_runtime_evidence",
            "pre_commit",
            d2,
            "FAIL" if errors else "PASS",
            metadata={"violations": len(errors)},
        )
    if errors:
        print(f"❌ Step 2 failed in {d2:.2f}s with violations:")
        for err in errors:
            print(f"   - {err}")
        return 1
    print(f"✅ Step 2 passed in {d2:.2f}s")
    return 0


def _step3_synthetic_scan(repository_root: Path, staged: bool, record_gate_run: Any) -> int:
    print("\n[Step 3/6] Scanning for Retired Synthetic Embedding Identifiers...")
    t0 = time.monotonic()
    from tools.validate_no_synthetic_embeddings import scan as scan_synthetic

    res3 = scan_synthetic(repository_root, staged=staged)
    d3 = time.monotonic() - t0
    if record_gate_run:
        record_gate_run(
            "pre_commit_synthetic_scan",
            "pre_commit",
            d3,
            "PASS" if res3["status"] == "PASS" else "FAIL",
        )
    if res3["status"] != "PASS":
        print(f"❌ Step 3 failed in {d3:.2f}s: matches detected: {res3.get('matches')}")
        return 1
    print(f"✅ Step 3 passed in {d3:.2f}s (matches: 0)")
    return 0


def _step4_staged_grounding(
    repository_root: Path, staged: bool, verbose: bool, record_gate_run: Any
) -> int:
    print("\n[Step 4/6] Enforcing Staged Grounding & Delivery Boundary Invariants...")
    t0 = time.monotonic()
    from tools.validate_staged_grounding import validate_staged_grounding

    rc_ground, violations = validate_staged_grounding(repository_root, staged=staged, verbose=verbose)
    d_ground = time.monotonic() - t0
    if record_gate_run:
        record_gate_run(
            "pre_commit_staged_grounding",
            "pre_commit",
            d_ground,
            "PASS" if rc_ground == 0 else "FAIL",
            metadata={"violations": len(violations)},
        )
    if rc_ground != 0:
        print(f"❌ Step 4 failed in {d_ground:.2f}s with grounding violations:")
        for v in violations:
            print(f"   - {v}")
        return rc_ground
    print(f"✅ Step 4 passed in {d_ground:.2f}s")
    return 0


def _step5_regex_safety(
    repository_root: Path, staged: bool, verbose: bool, record_gate_run: Any
) -> int:
    print("\n[Step 5/6] Enforcing Static Regex Safety & ReDoS Prevention...")
    t0 = time.monotonic()
    from tools.lint_regex_safety import get_staged_python_files, run_regex_audit

    if staged:
        files = get_staged_python_files(repository_root)
    else:
        files = []
        for d in ("dual_pipeline", "storage", "tools"):
            files.extend(sorted((repository_root / d).glob("**/*.py")))

    scanned_files, scanned_patterns, violations = run_regex_audit(files, verbose=verbose)
    d_regex = time.monotonic() - t0
    if record_gate_run:
        record_gate_run(
            "pre_commit_regex_safety",
            "pre_commit",
            d_regex,
            "PASS" if not violations else "FAIL",
            metadata={"violations": len(violations), "scanned_patterns": scanned_patterns},
        )
    if violations:
        print(f"❌ Step 5 failed in {d_regex:.2f}s with {len(violations)} ReDoS violation(s):")
        for v in violations:
            print(f"   - {v['file']}:{v['lineno']}: {v['pattern']} -> {v['issues']}")
        return 1
    print(f"✅ Step 5 passed in {d_regex:.2f}s ({scanned_files} file(s), {scanned_patterns} pattern(s))")
    return 0


def _step6_fast_invariants(repository_root: Path, verbose: bool, record_gate_run: Any) -> int:
    print("\n[Step 6/6] Executing Fast Static Invariant Test Suite...")
    t0 = time.monotonic()
    from tools.run_tests_with_progress import ProgressBarTestRunner, build_suite

    if not (repository_root / "documentation").is_dir():
        test_targets = ["tests.test_governance_rules"]
    else:
        test_targets = [
            "tests.test_retrieval_release_evaluation.RetrievalReleaseStaticGateTests",
            "tests.test_no_synthetic_embedding_ban",
            "tests.test_bge_m3_deprecation_gates",
            "tests.test_unified_agent_architecture",
        ]
    suite = build_suite(test_targets, start_dir=str(repository_root / "tests"))
    runner = ProgressBarTestRunner(verbosity=1 if verbose else 0)
    result = runner.run(suite)
    d6 = time.monotonic() - t0
    if record_gate_run:
        record_gate_run(
            "pre_commit_fast_invariants",
            "pre_commit",
            d6,
            "PASS" if result.wasSuccessful() else "FAIL",
            metadata={"tests_run": result.testsRun},
        )
    if not result.wasSuccessful():
        print(
            f"❌ Step 6 failed in {d6:.2f}s: {len(result.failures)} failures, {len(result.errors)} errors"
        )
        return 1
    print(f"✅ Step 6 passed in {d6:.2f}s ({result.testsRun} tests run)")
    return 0


def run_orchestrator(repository_root: Path, staged: bool = True, verbose: bool = False) -> int:
    t_start = time.monotonic()
    print("=" * 80)
    print("  UNIFIED PRE-COMMIT ORCHESTRATOR")
    print(f"  Repository Root: {repository_root}")
    print(f"  Mode:            {'STAGED (FAST)' if staged else 'FULL'}")
    print("=" * 80)

    if not (repository_root / "automations" / "prompts").is_dir():
        record_gate_run = None
    else:
        try:
            from tools.gate_run_ledger import record_gate_run
        except Exception:
            record_gate_run = None

    steps = [
        lambda: _step_prompt_mirror(repository_root, staged, verbose, record_gate_run),
        lambda: _step1_tier1(staged, verbose, record_gate_run),
        lambda: _step_test_and_healer_ratchet(repository_root, staged, record_gate_run),
        lambda: _step2_runtime_evidence(repository_root, record_gate_run),
        lambda: _step3_synthetic_scan(repository_root, staged, record_gate_run),
        lambda: _step4_staged_grounding(repository_root, staged, verbose, record_gate_run),
        lambda: _step5_regex_safety(repository_root, staged, verbose, record_gate_run),
        lambda: _step6_fast_invariants(repository_root, verbose, record_gate_run),
    ]

    for step_index, step_func in enumerate(steps, start=1):
        rc = step_func()
        if rc != 0:
            if record_gate_run:
                record_gate_run(
                    "pre_commit_total",
                    "pre_commit",
                    time.monotonic() - t_start,
                    "FAIL",
                    parent_phase="commit",
                    scope="staged",
                    metadata={"staged": staged, "failed_step": step_index},
                )
            return rc

    total_d = time.monotonic() - t_start
    write_pre_commit_receipt(repository_root, staged)
    if record_gate_run:
        record_gate_run(
            "pre_commit_total", "pre_commit", total_d, "PASS",
            parent_phase="commit", scope="staged", metadata={"staged": staged}
        )
    print("\n" + "=" * 80)
    print(f"🎉 ALL PRE-COMMIT GATES VERIFIED in {total_d:.2f}s")
    print("=" * 80)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Unified Pre-Commit Orchestrator")
    parser.add_argument("--repository-root", default=ROOT, type=Path)
    parser.add_argument(
        "--staged", action="store_true", default=True, help="Scan only staged files (default: True)"
    )
    parser.add_argument("--full", action="store_true", help="Scan full repository")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args(argv)

    staged = not args.full and args.staged
    return run_orchestrator(
        repository_root=Path(args.repository_root).resolve(),
        staged=staged,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    sys.exit(main())
