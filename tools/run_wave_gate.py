#!/usr/bin/env python3
"""Wave Gate Runner and Runtime Receipt Generator.

Executes declared wave test suites, verifies sequential locks,
audits module line budgets, validates surface catalogs, and generates immutable runtime receipts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

from dual_pipeline.l0.provenance import compute_content_digest
from dual_pipeline.l0.receipts import (
    create_runtime_receipt,
    create_sequential_lock,
    verify_predecessor_lock,
    write_immutable_receipt,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Governed Wave Gate Runner")
    for a in ("--wave", "--spec", "--receipt", "--lock"):
        parser.add_argument(a, type=int if a == "--wave" else str, required=True)
    for a in ("--require-lock", "--plan-path", "--ledger-path", "--surface-catalog"):
        parser.add_argument(a, type=str, default="")
    parser.add_argument("--allow-overwrite", action="store_true")
    parser.add_argument("--immutable", action="store_true")
    parser.add_argument("--suite", type=str, default="class-b")
    parser.add_argument("--retrieval-gates", type=int, default=0)
    return parser.parse_args()


def get_git_revision() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except Exception:
        return "unversioned"


def compute_file_sha256(path: Path) -> str:
    if not path.is_file():
        return "MISSING_FILE"
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


class WaveEvidenceVerifier:
    """Verifies evidence completeness before sealing runtime receipt."""

    REQUIRED_MODALITIES: Set[str] = {
        "audio/transcript", "notes/documents/attachments", "ingestion",
        "query/copilot", "decisions", "maintenance", "release", "CLI/MCP",
    }
    REQUIRED_SURFACE_FIELDS: Tuple[str, ...] = (
        "entrypoint", "modality", "prompt_or_schema", "evaluator",
        "gate", "consumer", "receipt", "ci_owner", "status",
    )

    @classmethod
    def verify_surface_catalog(cls, catalog_path: Path) -> Tuple[bool, str, Dict[str, Any]]:
        if not catalog_path.is_file():
            return False, f"Surface catalog not found: {catalog_path}", {}
        try:
            with open(catalog_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:
            return False, f"Failed parsing surface catalog YAML: {exc}", {}

        surfaces = data.get("surfaces", [])
        if not surfaces:
            return False, "Surface catalog contains 0 registered surfaces", {}

        found_modalities: Set[str] = set()
        for idx, s in enumerate(surfaces):
            mod = s.get("modality")
            if mod:
                found_modalities.add(mod)
            for fld in cls.REQUIRED_SURFACE_FIELDS:
                if not s.get(fld):
                    return False, f"Surface index {idx} ('{s.get('surface_id')}') missing required field '{fld}'", {}

        missing_mod = cls.REQUIRED_MODALITIES - found_modalities
        if missing_mod:
            return False, f"Surface catalog missing required modalities: {sorted(missing_mod)}", {}
        return True, f"Surface catalog verified: {len(surfaces)} surfaces mapped across all required modalities", data

    @classmethod
    def audit_l0_line_budgets(cls, repo_root: Path) -> Tuple[bool, List[str]]:
        from tools.file_budget_core import evaluate_module_file

        l0_dir = repo_root / "dual_pipeline" / "l0"
        violations: List[str] = []
        if l0_dir.is_dir():
            for py_file in sorted(l0_dir.glob("*.py")):
                verdict = evaluate_module_file(py_file, repo_root=repo_root)
                if verdict.is_blocking:
                    violations.append(f"{py_file.name} ({verdict.reason})")
        return len(violations) == 0, violations


def _run_single_test_suite(test_file: str, progress_runner: Path) -> Tuple[bool, str, Dict[str, Any]]:
    """Runs a single test suite using collection-aware progress execution."""
    t_method = "--timeout-method=signal" if os.name == "posix" else "--timeout-method=thread"
    cmd = [sys.executable, str(progress_runner), "--runner", "pytest", "--timeout=60", t_method, "-v", test_file]
    start_time = time.time()
    res = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - start_time
    combined_out = (res.stdout or "") + "\n" + (res.stderr or "")

    def _c(pat: str, fl: int = 0) -> int:
        m = re.search(pat, combined_out, fl)
        return int(m.group(1)) if m else 0

    collected = _c(r"collected\s+(\d+)\s+items", re.IGNORECASE)
    passed, failed, skipped = _c(r"(\d+)\s+passed"), _c(r"(\d+)\s+failed"), _c(r"(\d+)\s+skipped")

    if res.returncode != 0 or failed > 0:
        tail = combined_out[-1000:] if len(combined_out) > 1000 else combined_out
        return False, f"FAILED ({elapsed:.2f}s, exit code {res.returncode})\n{tail}", {
            "status": "FAILED", "duration_sec": elapsed, "collected": collected,
            "passed": passed, "failed": failed or 1, "skipped": skipped,
        }
    if collected == 0:
        return False, f"FAILED: Suite '{test_file}' yielded 0 collected items.", {
            "status": "EMPTY_COLLECTION", "duration_sec": elapsed, "collected": 0,
        }
    executed = passed + skipped
    if executed < collected:
        return False, f"FAILED: Suite '{test_file}' partially executed: {executed}/{collected}.", {"status": "PARTIALLY_EXECUTED", "duration_sec": elapsed, "collected": collected, "executed": executed}
    if skipped > 0 and passed == 0:
        return False, f"FAILED: Suite '{test_file}' skipped all {skipped} tests without explanation.", {"status": "UNEXPLAINED_SKIPS", "duration_sec": elapsed, "skipped": skipped}
    return True, f"PASSED ({elapsed:.2f}s) — {passed} passed, {collected} collected", {
        "status": "PASSED", "duration_sec": elapsed, "collected": collected,
        "passed": passed, "failed": 0, "skipped": skipped,
    }


def _check_destinations(receipt_dest: Path, lock_dest: Path, allow_overwrite: bool) -> bool:
    for dest, label in ((receipt_dest, "Receipt"), (lock_dest, "Lock")):
        if dest.exists() and not allow_overwrite:
            print(f"❌ BLOCKED: {label} already exists at '{dest}'. Exclusive creation prohibits overwrites.")
            return False
    return True


def _load_gate_spec(spec_arg: str, wave: int) -> Tuple[Optional[Dict[str, Any]], Optional[Path]]:
    spec_path = Path(spec_arg).resolve()
    if not spec_path.exists():
        print(f"❌ BLOCKED: Gate specification '{spec_arg}' not found.")
        return None, None
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = yaml.safe_load(f) or {}
    spec_wave = spec.get("wave_number", wave)
    if spec_wave != wave:
        print(f"❌ BLOCKED: Spec wave_number ({spec_wave}) does not match command wave ({wave}).")
        return None, None
    return spec, spec_path


def _verify_wave_predecessor(wave: int, required_prior_lock: Optional[str]) -> Tuple[bool, Optional[str]]:
    if wave > 1 and not required_prior_lock:
        print(f"❌ BLOCKED: Wave {wave} requires prior lock from Wave {wave - 1}.")
        return False, None
    if required_prior_lock:
        kw = {"expected_parent_wave": wave - 1} if wave > 1 else {}
        ok, reason, p_digest = verify_predecessor_lock(required_prior_lock, **kw)
        if not ok:
            print(f"❌ BLOCKED: {'Predecessor' if wave > 1 else 'Baseline'} lock verification failed: {reason}")
            return False, None
        print(f"✔ {reason}")
        return True, p_digest
    return True, None


def _run_pre_wave_intercept(wave: int, spec: Dict[str, Any], plan_path: Optional[str], ledger_path: Optional[str]) -> int:
    pre_gate_script = REPO_ROOT / "tools" / "pre_wave_decision_gate.py"
    if not pre_gate_script.exists():
        return 0
    pre_cmd = [sys.executable, str(pre_gate_script), "--wave", str(wave)]
    if plan_path:
        pre_cmd.extend(["--plan-path", str(plan_path)])
    if ledger_path:
        pre_cmd.extend(["--ledger-path", str(ledger_path)])
    if spec.get("decisions_file"):
        pre_cmd.extend(["--decisions-file", str(spec.get("decisions_file"))])

    pre_res = subprocess.run(pre_cmd, capture_output=True, text=True)
    if pre_res.returncode in (2, 3):
        msg = "has OPEN decisions" if pre_res.returncode == 2 else "0 decision records in scope"
        print(f"\n🛑 PRE-WAVE DECISION INTERCEPT: Wave {wave} {msg}.\n{pre_res.stdout}")
        return pre_res.returncode
    if pre_res.returncode != 0:
        print(f"❌ PRE-WAVE INTERCEPTOR ERROR: Exit code {pre_res.returncode}\n{pre_res.stderr}")
        return pre_res.returncode
    print(f"✔ Pre-Wave Decision Intercept: Clearance granted for Wave {wave}.")
    return 0


def _run_all_test_suites(test_suites: List[str]) -> Tuple[bool, int, int, Dict[str, Any]]:
    total_passed = total_failed = total_collected = 0
    test_results: Dict[str, Any] = {}
    progress_runner = REPO_ROOT / "tools" / "run_tests_with_progress.py"

    for test_file in test_suites:
        test_path = REPO_ROOT / test_file
        if not test_path.is_file():
            print(f"❌ BLOCKED: Declared test suite '{test_file}' does not exist on disk.")
            return False, 0, 0, {}

        print(f"\n--- Running (collection-aware): {test_file} ---")
        ok, msg, res_dict = _run_single_test_suite(test_file, progress_runner)
        print(f"  {'✔' if ok else '❌'} {msg}")
        test_results[test_file] = res_dict
        if ok:
            total_passed += res_dict.get("passed", 0)
            total_collected += res_dict.get("collected", 0)
        else:
            total_failed += (res_dict.get("failed", 1) or 1)

    if total_failed > 0:
        print(f"\n❌ GATE BLOCKED: {total_failed} failure(s) in test execution.")
        return False, 0, 0, {}

    return True, total_passed, total_collected, test_results


def _verify_gate_evidence(spec: Dict[str, Any]) -> Tuple[bool, Dict[str, str], List[str]]:
    print("\n--- Verifying evidence before sealing ---")
    declared_artifacts = spec.get("artifacts", [])
    artifact_records: Dict[str, str] = {}
    output_digests: List[str] = []

    for art in declared_artifacts:
        art_path = REPO_ROOT / art
        art_hash = compute_file_sha256(art_path) if art_path.is_file() else "MISSING_FILE"
        if art_hash == "MISSING_FILE":
            print(f"❌ BLOCKED: Declared deliverable artifact missing: {art}")
            return False, {}, []
        artifact_records[art] = art_hash
        output_digests.append(art_hash)

    l0_ok, l0_violations = WaveEvidenceVerifier.audit_l0_line_budgets(REPO_ROOT)
    if not l0_ok:
        print(f"❌ BLOCKED: Module budget violations in L0: {l0_violations}")
        return False, {}, []
    print("✔ Module line budget audit: All dual_pipeline/l0 modules <= 400 lines.")
    return True, artifact_records, output_digests


def _seal_gate_receipt_and_lock(
    args: argparse.Namespace,
    spec: Dict[str, Any],
    spec_path: Path,
    plan_path: Any,
    surface_catalog: Any,
    catalog_path: Any,
    parent_lock_digest: Optional[str],
    total_collected: int,
    total_passed: int,
    test_results: Dict[str, Any],
    artifact_records: Dict[str, str],
    output_digests: List[str],
) -> int:
    git_rev = get_git_revision()
    receipt_id = f"receipt-wave-{args.wave}-{int(time.time())}"
    wave_id = spec.get("wave_id", f"Wave {args.wave}")

    input_digests: List[str] = [parent_lock_digest] if parent_lock_digest else []
    input_digests.append(compute_content_digest(spec_path.read_bytes()).sha256)
    for p in (plan_path, catalog_path):
        if p and Path(p).is_file():
            input_digests.append(compute_file_sha256(Path(p)))

    receipt = create_runtime_receipt(
        receipt_id=receipt_id,
        wave_id=wave_id,
        step_name="wave_acceptance_gate",
        status="COMPLETE",
        input_digests=input_digests,
        output_digests=output_digests,
        parent_receipt_digest=parent_lock_digest,
        metadata={
            "git_revision": git_rev, "command": " ".join(sys.argv),
            "plan_path": str(plan_path) if plan_path else None, "spec_path": str(args.spec),
            "surface_catalog": str(surface_catalog) if surface_catalog else None,
            "collected_cases": total_collected, "executed_cases": total_passed,
            "test_results": test_results, "artifacts": artifact_records,
            "policy_identities": spec.get("policy_identities", ["NEE-KG-ALIGNMENT-01"]),
            "prompt_identities": spec.get("prompt_identities", []),
            "evaluator_identities": spec.get("evaluator_identities", []),
            "isolated_runtime_root": str(REPO_ROOT), "terminal_result": "PASS",
            "acceptance_criteria": spec.get("acceptance_criteria", []), "timestamp": time.time(),
        },
    )

    try:
        receipt_path = write_immutable_receipt(receipt, args.receipt, allow_overwrite=args.allow_overwrite)
    except FileExistsError as e:
        print(f"❌ BLOCKED: {e}")
        return 1
    print(f"\n✔ Created immutable receipt: {receipt_path} [digest: {receipt.receipt_digest[:16]}...]")

    try:
        lock_path = create_sequential_lock(
            lock_path=args.lock,
            wave_id=wave_id,
            receipt_digest=receipt.receipt_digest,
            parent_lock_digest=parent_lock_digest,
            metadata={"wave": args.wave, "receipt_id": receipt_id, "git_revision": git_rev},
            allow_overwrite=args.allow_overwrite,
        )
    except FileExistsError as e:
        print(f"❌ BLOCKED: {e}")
        return 1
    print(f"✔ Created sequential lock: {lock_path}")
    print("\n" + "=" * 80)
    print(f"  ✅ WAVE {args.wave} GATE PASSED — READY FOR SEQUENTIAL PROMOTION")
    print("=" * 80)
    return 0


def main() -> int:
    args = parse_args()
    print("=" * 80)
    print(f"  GOVERNED WAVE GATE EXECUTION: Wave {args.wave}")
    print("=" * 80)

    if not _check_destinations(Path(args.receipt).resolve(), Path(args.lock).resolve(), args.allow_overwrite):
        return 1

    spec, spec_path = _load_gate_spec(args.spec, args.wave)
    if not spec or not spec_path:
        return 1

    plan_path = args.plan_path or spec.get("plan_path")
    ledger_path = args.ledger_path or spec.get("ledger_path")
    surface_catalog = args.surface_catalog or spec.get("surface_catalog")
    required_prior_lock = args.require_lock or spec.get("required_prior_lock")

    pred_ok, parent_lock_digest = _verify_wave_predecessor(args.wave, required_prior_lock)
    if not pred_ok:
        return 1

    pre_code = _run_pre_wave_intercept(args.wave, spec, plan_path, ledger_path)
    if pre_code != 0:
        return pre_code

    catalog_path = Path(surface_catalog).resolve() if surface_catalog else None
    if catalog_path:
        cat_ok, cat_reason, _ = WaveEvidenceVerifier.verify_surface_catalog(catalog_path)
        if not cat_ok:
            print(f"❌ BLOCKED: Surface catalog verification failed: {cat_reason}")
            return 1
        print(f"✔ {cat_reason}")

    test_suites: List[str] = spec.get("test_suites", [])
    if not test_suites:
        print("❌ BLOCKED: No test suites declared in specification.")
        return 1

    tests_ok, total_passed, total_collected, test_results = _run_all_test_suites(test_suites)
    if not tests_ok:
        return 1

    ev_ok, artifact_records, output_digests = _verify_gate_evidence(spec)
    if not ev_ok:
        return 1

    return _seal_gate_receipt_and_lock(
        args, spec, spec_path, plan_path, surface_catalog, catalog_path,
        parent_lock_digest, total_collected, total_passed, test_results,
        artifact_records, output_digests,
    )


if __name__ == "__main__":
    sys.exit(main())
