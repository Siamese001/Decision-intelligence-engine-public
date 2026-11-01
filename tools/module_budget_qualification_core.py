"""
tools/module_budget_qualification_core.py

Core qualification routines and execution runners for Module Budget Policy waves 1 & 2.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent

from tools.record_wave_receipt import (
    compute_dict_digest,
    compute_file_sha256,
    write_atomic_json,
)


def get_git_revision() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True, timeout=10
        ).strip()
        return out
    except Exception:
        return "UNKNOWN_GIT_REV"


def run_acceptance_suite(test_targets: List[str], timeout: int = 60) -> Dict[str, Any]:
    runner = ROOT / "tools" / "run_tests_with_progress.py"
    cmd = [sys.executable, str(runner), f"--timeout={timeout}"] + test_targets
    started = datetime.now(timezone.utc)
    res = subprocess.run(
        cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout * 3
    )
    ended = datetime.now(timezone.utc)

    passed, failed, errors, skipped, total = 0, 0, 0, 0, 0

    for line in res.stdout.splitlines():
        if "Test Execution Summary:" in line:
            for part in line.split("|"):
                p = part.strip()
                if p.startswith("Total:"):
                    total = int(p.split(":")[1].strip())
                elif p.startswith("Passed:"):
                    passed = int(p.split(":")[1].strip())
                elif p.startswith("Failed:"):
                    failed = int(p.split(":")[1].strip())
                elif p.startswith("Errors:"):
                    errors = int(p.split(":")[1].strip())
                elif p.startswith("Skipped:"):
                    skipped = int(p.split(":")[1].strip())

    if total == 0 or total == skipped:
        raise RuntimeError(
            f"Acceptance suite rejected: total tests {total}, skipped {skipped}. Empty or all-skipped suites prohibited."
        )

    if res.returncode != 0 or failed > 0 or errors > 0:
        raise RuntimeError(f"Acceptance suite failed: returncode {res.returncode}, failed {failed}, errors {errors}.")

    return {
        "command": " ".join(cmd),
        "returncode": res.returncode,
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "duration_seconds": (ended - started).total_seconds(),
        "summary": "PASS",
    }


def qualify_wave_1(candidate_path: Path, baseline_path: Path, receipt_path: Path) -> Dict[str, Any]:
    print("=== Qualify Wave 1: Freeze Policy Contract & Comparison Baseline ===")
    if not candidate_path.is_file() or not baseline_path.is_file():
        raise FileNotFoundError("Candidate policy or baseline cohort file missing.")

    inventory_path = ROOT / "reports" / "module_budget_policy" / "w1" / "surface_inventory.json"
    decision_path = ROOT / "reports" / "module_budget_policy" / "w1" / "policy_decision.json"
    comp_baseline_path = ROOT / "reports" / "module_budget_policy" / "w1" / "comparison_baseline.json"
    schema_path = ROOT / "contracts" / "governance" / "module_budget_policy.schema.json"

    for p in [inventory_path, decision_path, comp_baseline_path, schema_path]:
        if not p.is_file():
            raise FileNotFoundError(f"Wave 1 milestone artifact missing: {p}")

    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if not inventory.get("complete_coverage_attestation") or inventory.get("unresolved_consumers"):
        raise ValueError("Surface inventory lacks complete coverage attestation or has unresolved consumers.")

    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    rules = candidate.get("boundary_rules", {})
    required_rules = ["up_to_400", "401_to_650_ordinary", "651_to_800_declarative", "above_warning_range", "legacy_baselines", "structural_violation_invariance", "parsing_and_policy_evidence_error"]
    for r in required_rules:
        if r not in rules:
            raise ValueError(f"Candidate policy missing boundary rule: {r}")

    comp_baseline = json.loads(comp_baseline_path.read_text(encoding="utf-8"))
    rec = comp_baseline.get("baseline_reconciliation_112", {})
    reconciled_sum = rec.get("retained_oversized_count", 0) + rec.get("already_compliant_count", 0) + rec.get("removed_count", 0) + rec.get("renamed_or_invalid_count", 0)
    if reconciled_sum != 112:
        raise ValueError(f"Reconciled baseline count sum is {reconciled_sum}, expected 112.")

    cohort_data = json.loads(baseline_path.read_text(encoding="utf-8"))
    cohort = cohort_data.get("cohort", [])
    if len(cohort) != 50:
        raise ValueError(f"Expected 50 cohort items, found {len(cohort)}")

    corrective = [c for c in cohort if c.get("classification") == "CORRECTIVE_REMEDIATION"]
    feature_int = [c for c in cohort if c.get("classification") == "FEATURE_INTEGRATED_SIZE_CHANGE"]
    if len(corrective) != 8 or [c["index"] for c in corrective] != [4, 26, 34, 38, 39, 43, 44, 48]:
        raise ValueError("Corrective PRs mismatch expectation.")
    if len(feature_int) != 2 or [c["index"] for c in feature_int] != [15, 17]:
        raise ValueError("Feature-integrated PRs mismatch expectation.")

    test_result = run_acceptance_suite(["tests/test_module_budget_policy_contract.py"], timeout=60)

    receipt = {
        "schema_version": "1.0.0",
        "wave": 1,
        "wave_title": "Freeze the policy contract and comparison baseline",
        "terminal_outcome": "PASS",
        "status": "COMPLETE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": get_git_revision(),
        "runtime_root": str(ROOT),
        "acceptance_criteria_results": {f"criterion_{i}": True for i in range(1, 6)},
        "metrics": {
            "total_consumers_accounted": len(inventory.get("consumers", [])),
            "baseline_112_retained": rec.get("retained_oversized_count"),
            "baseline_112_compliant": rec.get("already_compliant_count"),
            "baseline_112_removed": rec.get("removed_count"),
            "historical_50_prs_reviewed": 50,
            "corrective_remediation_prs": 8,
            "feature_integrated_prs": 2,
            "union_affected_prs": 10,
        },
        "acceptance_test_execution": test_result,
        "input_digests": {
            "candidate_policy": compute_file_sha256(candidate_path),
            "baseline_cohort": compute_file_sha256(baseline_path),
            "surface_inventory": compute_file_sha256(inventory_path),
            "comparison_baseline": compute_file_sha256(comp_baseline_path),
            "schema": compute_file_sha256(schema_path),
            "policy_decision": compute_file_sha256(decision_path),
        },
        "predecessor_receipt": None,
    }
    write_atomic_json(receipt_path, receipt, immutable=False, allow_overwrite=True)
    return receipt


def qualify_wave_2(candidate_path: Path, previous_receipt_path: Path, receipt_path: Path) -> Dict[str, Any]:
    print("=== Qualify Wave 2: Implement One Evaluator & Safe Baseline Migration ===")
    if not candidate_path.is_file() or not previous_receipt_path.is_file():
        raise FileNotFoundError("Candidate policy or predecessor receipt missing.")

    prev_data = json.loads(previous_receipt_path.read_text(encoding="utf-8"))
    if prev_data.get("wave") != 1 or prev_data.get("terminal_outcome") != "PASS" or prev_data.get("status") != "COMPLETE":
        raise ValueError(f"Predecessor receipt invalid: {previous_receipt_path}")

    shadow_path = ROOT / "reports" / "module_budget_policy" / "w2" / "shadow_comparison.json"
    if not shadow_path.is_file():
        raise FileNotFoundError(f"Shadow comparison artifact missing: {shadow_path}")

    shadow = json.loads(shadow_path.read_text(encoding="utf-8"))
    summary = shadow.get("summary", {})
    if summary.get("unexplained_verdict_changes", -1) != 0:
        raise ValueError("Shadow comparison contains unexplained verdict changes.")

    dispositions = shadow.get("legacy_baseline_dispositions", [])
    corpus = shadow.get("regression_corpus_evaluations", [])
    if len(dispositions) != 112 or len(corpus) != 4:
        raise ValueError("Dispositions or corpus count mismatch.")

    test_suite = [
        "tests/test_module_budget_policy.py",
        "tests/adversarial/test_module_budget_policy_adversarial.py",
        "tests/test_mmi_file_budgets.py",
        "tests/test_lint_file_budgets_universal.py",
        "tests/adversarial/test_ratchet_monotonic_adversarial.py",
    ]
    test_result = run_acceptance_suite(test_suite, timeout=60)

    policy_mod_path = ROOT / "tools" / "file_budget_core" / "policy.py"
    shadow_mod_path = ROOT / "tools" / "file_budget_core" / "shadow.py"

    receipt = {
        "schema_version": "1.0.0",
        "wave": 2,
        "wave_title": "Implement one evaluator and safe baseline migration",
        "terminal_outcome": "PASS",
        "status": "COMPLETE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": get_git_revision(),
        "runtime_root": str(ROOT),
        "acceptance_criteria_results": {f"criterion_{i}": True for i in range(1, 6)},
        "metrics": {
            "governed_files_evaluated": summary.get("total_governed_files_evaluated"),
            "identical_verdicts": summary.get("identical_verdict_count"),
            "changed_verdicts": summary.get("changed_verdict_count"),
            "unexplained_verdict_changes": summary.get("unexplained_verdict_changes"),
            "legacy_112_dispositions": len(dispositions),
            "regression_corpus_cases": len(corpus),
            "acceptance_tests_passed": test_result["passed"],
            "acceptance_tests_total": test_result["total"],
        },
        "acceptance_test_execution": test_result,
        "input_digests": {
            "candidate_policy": compute_file_sha256(candidate_path),
            "predecessor_receipt": compute_file_sha256(previous_receipt_path),
            "shadow_comparison": compute_file_sha256(shadow_path),
            "policy_module": compute_file_sha256(policy_mod_path),
            "shadow_module": compute_file_sha256(shadow_mod_path),
        },
        "predecessor_receipt": {
            "wave": 1,
            "receipt_path": str(previous_receipt_path),
            "sha256": compute_file_sha256(previous_receipt_path),
        },
    }
    write_atomic_json(receipt_path, receipt, immutable=False, allow_overwrite=True)
    return receipt
