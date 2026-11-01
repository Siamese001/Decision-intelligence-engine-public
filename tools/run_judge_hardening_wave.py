#!/usr/bin/env python3
"""Thin scope adapter and receipt runner for JARVIS judge hardening waves.

Enforces:
1. Manifest-bound test scoping and CI enrollment validation.
2. Verified parent wave receipt chaining before wave N execution.
3. Isolation: runtime root cannot be repo root or production output/.
4. Strict test criteria: positive nonzero collection, 0 failed, 0 errors, 0 skips.
5. Immutable, exclusive receipt persistence under reports/jarvis_judge_forensics/receipts/.
6. Wave 9 delivery audits: testing gaps >= 80% E2E and Class B pytest compliance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple
import uuid
import xml.etree.ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.test_discovery_core.discovery import resolve_all_tests  # noqa: E402


def compute_file_digest(file_path: Path) -> str:
    """Computes SHA-256 digest of a single file."""
    if not file_path.is_file():
        return "0" * 64
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_tree_digest(root: Path, relative_paths: Sequence[str]) -> str:
    """Computes deterministic combined SHA-256 digest across target paths."""
    hasher = hashlib.sha256()
    for rel in sorted(relative_paths):
        p = root / rel
        if p.is_file():
            hasher.update(rel.encode("utf-8") + compute_file_digest(p).encode("utf-8"))
        elif p.is_dir():
            for sub in sorted(p.rglob("*")):
                if sub.is_file():
                    sub_rel = str(sub.relative_to(root))
                    hasher.update(sub_rel.encode("utf-8") + compute_file_digest(sub).encode("utf-8"))
    return hasher.hexdigest()


def validate_environment(
    runtime_root: Path,
    receipt_root: Path,
    wave: int,
    repo_root: Path,
) -> Tuple[bool, str]:
    """Validates that runtime and receipt roots conform to isolation invariants."""
    res_run, res_rec, res_repo = runtime_root.resolve(), receipt_root.resolve(), repo_root.resolve()
    if res_run == res_repo:
        return False, "Runtime root cannot be the primary repository root."
    out_dir = res_repo / "output"
    if out_dir in res_run.parents or res_run == out_dir:
        return False, "Runtime root cannot reside within production output/ directory."
    target = res_rec / f"wave-{wave}.json"
    if target.exists():
        return False, f"Receipt file already exists at {target}; immutable paths cannot be overwritten."
    return True, "Environment validation passed."


def validate_parent_receipt(receipt_root: Path, wave: int) -> Tuple[bool, Optional[str], str]:
    """Validates that predecessor wave completed with verified COMPLETE receipt."""
    if wave <= 1:
        return True, None, "Wave 1 requires no parent receipt."
    parent_wave = wave - 1
    parent_path = receipt_root / f"wave-{parent_wave}.json"
    if not parent_path.is_file():
        return False, None, f"Parent receipt missing for Wave {parent_wave} at {parent_path}"
    try:
        data = json.loads(parent_path.read_text(encoding="utf-8"))
        if data.get("terminal_result") != "COMPLETE":
            return False, None, f"Parent wave {parent_wave} terminal_result is {data.get('terminal_result')} (expected COMPLETE)"
        return True, compute_file_digest(parent_path), f"Parent wave {parent_wave} verified."
    except Exception as e:
        return False, None, f"Failed parsing parent receipt {parent_path}: {e}"


def check_ci_enrollment(test_files: Sequence[str], repo_root: Path) -> Dict[str, Any]:
    """Verifies that declared wave test files resolve under test_discovery_core."""
    det, e2e, bge = resolve_all_tests(repo_root=repo_root)
    all_resolved = set(det + e2e + bge)
    enrolled: Dict[str, str] = {}
    missing: List[str] = []
    for tf in test_files:
        mod_name = tf.replace("/", ".").replace(".py", "")
        if mod_name in all_resolved:
            enrolled[tf] = "RESOLVED"
        else:
            missing.append(tf)
    return {"all_enrolled": len(missing) == 0, "enrolled_tests": enrolled, "missing_tests": missing}


def load_wave_scope(scopes_path: Path, wave: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Loads wave scope specification from planned_wave_scopes.json."""
    if not scopes_path.is_file():
        return None, f"Scopes file not found: {scopes_path}"
    try:
        data = json.loads(scopes_path.read_text(encoding="utf-8"))
        for entry in data.get("waves", []):
            if entry.get("wave") == wave:
                return entry, None
        return None, f"Wave {wave} not found in {scopes_path}"
    except Exception as e:
        return None, f"Failed reading {scopes_path}: {e}"


def parse_junit_xml(junit_path: Path) -> Tuple[int, int, int, int, int, List[str]]:
    """Extracts test counts and test IDs from JUnit XML report."""
    if not junit_path.is_file():
        return 0, 0, 0, 0, 0, []
    root = ET.parse(junit_path).getroot()
    passed, failed, errors, skipped = 0, 0, 0, 0
    test_ids: List[str] = []
    for tc in root.findall(".//testcase"):
        cls, name = tc.get("classname", ""), tc.get("name", "")
        test_ids.append(f"{cls}::{name}" if cls else name)
        if tc.find("failure") is not None:
            failed += 1
        elif tc.find("error") is not None:
            errors += 1
        elif tc.find("skipped") is not None:
            skipped += 1
        else:
            passed += 1
    return len(test_ids), passed, failed, errors, skipped, test_ids


def execute_tests(
    test_files: Sequence[str],
    repo_root: Path,
    report_path: Path,
    per_test_timeout: int = 60,
    suite_timeout: int = 120,
) -> Tuple[int, float, str, str]:
    """Executes wave tests via tools/run_tests_with_progress.py."""
    cmd = [
        str(repo_root / ".venv/bin/python"),
        str(repo_root / "tools/run_tests_with_progress.py"),
        "--runner", "pytest",
        "--timeout", str(per_test_timeout),
        "--suite-timeout", str(suite_timeout),
    ] + list(test_files)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if report_path.exists():
        report_path.unlink()

    env = dict(os.environ)
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"):
        env.pop(var, None)
    env["PYTEST_ADDOPTS"] = f"--junitxml={report_path}"

    start_time = time.time()
    try:
        proc = subprocess.run(cmd, timeout=suite_timeout + 30, cwd=str(repo_root), env=env, capture_output=True, text=True)
        return proc.returncode, round(time.time() - start_time, 2), proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, round(time.time() - start_time, 2), "", "Execution timed out"


def evaluate_acceptance(exit_code: int, total: int, passed: int, failed: int, errors: int, skipped: int) -> List[str]:
    """Evaluates test metrics against wave acceptance criteria."""
    defects = []
    if exit_code != 0:
        defects.append(f"Subprocess exited with non-zero exit code: {exit_code}")
    if total == 0:
        defects.append("Zero tests collected from declared wave manifests.")
    if failed > 0:
        defects.append(f"{failed} test(s) failed.")
    if errors > 0:
        defects.append(f"{errors} test error(s) encountered.")
    if skipped > 0:
        defects.append(f"{skipped} skipped test(s) encountered. Wave criteria mandate 0 skips.")
    return defects


def run_wave9_audits(repo_root: Path, test_files: Sequence[str]) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """Runs testing gap audit (>=80% E2E) and pytest compliance check for Wave 9 delivery."""
    py_bin = repo_root / ".venv/bin/python"
    gap_cmd = [str(py_bin), str(repo_root / "tools/audit_testing_gaps.py"), "--min-e2e-score", "80"]
    gap_res = subprocess.run(gap_cmd, cwd=str(repo_root), capture_output=True, text=True, timeout=120)
    if gap_res.returncode != 0:
        return False, {}, f"Testing gaps audit failed ({gap_res.returncode}): {gap_res.stderr or gap_res.stdout}"

    compliance_cmd = f"pytest {' '.join(test_files)} --timeout=60"
    comp_run = [str(py_bin), str(repo_root / "tools/check_pytest_compliance.py"), compliance_cmd]
    comp_res = subprocess.run(comp_run, cwd=str(repo_root), capture_output=True, text=True, timeout=120)
    if comp_res.returncode != 0:
        return False, {}, f"Pytest compliance check failed: {comp_res.stderr or comp_res.stdout}"

    return True, {"testing_gaps_audit": "PASS (E2E >= 80%)", "pytest_compliance": comp_res.stdout.strip()}, None


def seal_receipt(receipt_data: Dict[str, Any], receipt_path: Path) -> str:
    """Atomically writes receipt with exclusive creation (O_EXCL)."""
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(receipt_data, indent=2, sort_keys=True)
    with open(receipt_path, "x", encoding="utf-8") as f:
        f.write(payload)
    return compute_file_digest(receipt_path)


def run_self_tests(receipt_root: Path, repo_root: Path) -> bool:
    """Runs bootstrap receipt self-tests before production wave sealing."""
    temp_dir = receipt_root / ".bootstrap_test"
    temp_dir.mkdir(parents=True, exist_ok=True)
    test_receipt = temp_dir / "wave-test.json"
    try:
        if test_receipt.exists():
            test_receipt.unlink()
        digest1 = seal_receipt({"test": True, "terminal_result": "COMPLETE"}, test_receipt)
        if not test_receipt.is_file() or len(digest1) != 64:
            return False
        try:
            seal_receipt({"overwrite": True}, test_receipt)
            return False
        except FileExistsError:
            return True
    finally:
        if test_receipt.exists():
            test_receipt.unlink()
        if temp_dir.exists():
            temp_dir.rmdir()


def _validate_prerequisites(
    args: argparse.Namespace,
) -> Tuple[bool, Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    """Validates environment, plan, parent receipt, and wave scope."""
    ok_env, env_msg = validate_environment(args.runtime_root, args.receipt_root, args.wave, REPO_ROOT)
    if not ok_env:
        print(f"ERROR: {env_msg}", file=sys.stderr)
        return False, None, None, None

    if not args.plan.is_file() or args.plan.stat().st_size == 0:
        print(f"ERROR: Invalid or empty plan file at {args.plan}", file=sys.stderr)
        return False, None, None, None
    plan_digest = compute_file_digest(args.plan)

    ok_p, p_digest, p_msg = validate_parent_receipt(args.receipt_root, args.wave)
    if not ok_p:
        print(f"ERROR: Parent receipt validation failed: {p_msg}", file=sys.stderr)
        return False, None, None, None

    scopes_path = REPO_ROOT / "reports/jarvis_judge_forensics/planned_wave_scopes.json"
    wave_scope, scope_err = load_wave_scope(scopes_path, args.wave)
    if scope_err or not wave_scope:
        print(f"ERROR: {scope_err}", file=sys.stderr)
        return False, None, None, None

    return True, plan_digest, p_digest, wave_scope


def _run_and_evaluate_tests(
    args: argparse.Namespace,
    test_files: List[str],
) -> Tuple[bool, Dict[str, Any], List[str], Dict[str, Any], Dict[str, Any]]:
    """Runs CI enrollment checks, executes tests, and verifies acceptance."""
    ci_info = check_ci_enrollment(test_files, REPO_ROOT)
    if not ci_info["all_enrolled"]:
        print(f"ERROR: Tests not enrolled in active CI profile: {ci_info['missing_tests']}", file=sys.stderr)
        return False, {}, [], {}, {}

    report_path = args.runtime_root / "junit_report.xml"
    exit_code, duration, stdout, stderr = execute_tests(
        test_files, REPO_ROOT, report_path, args.timeout, args.suite_timeout
    )
    total, passed, failed, errors, skipped, test_ids = parse_junit_xml(report_path)

    defects = evaluate_acceptance(exit_code, total, passed, failed, errors, skipped)
    if defects:
        print(f"ERROR: Acceptance criteria failed for Wave {args.wave}:", file=sys.stderr)
        for d in defects:
            print(f"  - {d}", file=sys.stderr)
        return False, {}, [], {}, {}

    wave9_audits: Dict[str, Any] = {}
    if args.wave == 9:
        ok_audit, audit_data, audit_err = run_wave9_audits(REPO_ROOT, test_files)
        if not ok_audit:
            print(f"ERROR: Wave 9 audit validation failed: {audit_err}", file=sys.stderr)
            return False, {}, [], {}, {}
        wave9_audits = audit_data

    metrics = {
        "total_collected": total, "passed": passed, "failed": failed,
        "errors": errors, "skipped": skipped, "duration_seconds": duration,
    }
    return True, metrics, test_ids, ci_info, wave9_audits


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="JARVIS judge hardening wave runner adapter")
    parser.add_argument("--wave", type=int, required=False, default=None, help="Wave number (1-9)")
    parser.add_argument("--plan", type=Path, default=REPO_ROOT / "reports/jarvis_judge_forensics/plan.md")
    parser.add_argument("--runtime-root", type=Path, default=REPO_ROOT / ".local/judge-hardening/w1")
    parser.add_argument("--receipt-root", type=Path, default=REPO_ROOT / "reports/jarvis_judge_forensics/receipts")
    parser.add_argument("--timeout", type=int, default=60, help="Per-test timeout in seconds")
    parser.add_argument("--suite-timeout", type=int, default=120, help="Suite timeout in seconds")
    parser.add_argument("--self-test", action="store_true", help="Run bootstrap self-tests only")
    args = parser.parse_args(argv)

    if not run_self_tests(args.receipt_root, REPO_ROOT):
        print("ERROR: Bootstrap receipt self-tests failed.", file=sys.stderr)
        return 1
    if args.self_test:
        print("Bootstrap self-tests passed successfully.")
        return 0

    if args.wave is None:
        parser.error("--wave is required when not in --self-test mode.")

    ok_pre, plan_digest, p_digest, wave_scope = _validate_prerequisites(args)
    if not ok_pre or not wave_scope:
        return 1

    test_files: List[str] = wave_scope.get("test_files", [])
    if not test_files:
        print(f"ERROR: No test files declared for Wave {args.wave}", file=sys.stderr)
        return 1

    ok_test, metrics, test_ids, ci_info, wave9_audits = _run_and_evaluate_tests(args, test_files)
    if not ok_test:
        return 1

    tree_digest = compute_tree_digest(REPO_ROOT, test_files)
    receipt_data = {
        "wave": args.wave,
        "title": wave_scope.get("title", ""),
        "terminal_result": "COMPLETE",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": f"run_{uuid.uuid4().hex[:12]}",
        "command": " ".join(sys.argv),
        "runtime_root": str(args.runtime_root),
        "plan_path": str(args.plan),
        "plan_sha256": plan_digest,
        "code_tree_digest": tree_digest,
        "parent_receipt_sha256": p_digest,
        "test_scope": test_files,
        "execution_metrics": metrics,
        "structured_node_results": test_ids,
        "ci_enrollment": ci_info,
    }
    if wave9_audits:
        receipt_data["delivery_audits"] = wave9_audits

    target_receipt = args.receipt_root / f"wave-{args.wave}.json"
    receipt_digest = seal_receipt(receipt_data, target_receipt)
    print(f"Wave {args.wave} COMPLETE: Sealed receipt at {target_receipt} (SHA-256: {receipt_digest[:16]}...)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
