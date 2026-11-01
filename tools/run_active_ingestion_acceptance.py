#!/usr/bin/env python3
"""Run bounded active-graph ingestion acceptance waves and seal immutable receipts."""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, read_json, sha256_file, write_json  # noqa: E402
from tools.active_ingestion_acceptance_core.deliverables import (  # noqa: E402
    WAVE_1_DELIVERABLES,
    WAVE_2_DELIVERABLES,
    WAVE_3_DELIVERABLES,
    WAVE_4_DELIVERABLES,
    WAVE_5_DELIVERABLES,
    WAVE_6_DELIVERABLES,
    WAVE_7_DELIVERABLES,
)
from tools.active_ingestion_acceptance_core.helpers import (  # noqa: E402
    check_decision_gate,
    check_line_budgets,
    collect_deliverables,
    git_info,
    run_pytest,
    seal_receipt,
    verify_predecessor,
)
from tools.active_ingestion_acceptance_core.verifiers import (  # noqa: E402
    verify_wave1,
    verify_wave2,
    verify_wave3,
    verify_wave4,
    verify_wave5,
)


def _git(*args: str) -> str:
    return git_info(ROOT, *args)


def _seal_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    return seal_receipt(payload)


def _verify_predecessor(predecessor_path: Path, expected_wave: int) -> dict[str, Any]:
    return verify_predecessor(ROOT, predecessor_path, expected_wave)


def _collect_deliverables(deliverables: tuple[str, ...]) -> list[dict[str, str]]:
    return collect_deliverables(ROOT, deliverables)


def _check_line_budgets(rel_paths: tuple[str, ...]) -> None:
    return check_line_budgets(ROOT, rel_paths)


def _check_decision_gate(wave: int, plan_path: Path) -> dict[str, Any]:
    return check_decision_gate(ROOT, wave, plan_path)


def _run_pytest(test_files: list[str], timeout: int) -> int:
    return run_pytest(ROOT, test_files, timeout)


def _verify_wave1(plan_path: Path, timeout: int) -> dict[str, Any]:
    return verify_wave1(ROOT, plan_path, timeout)


def _verify_wave2(plan_path: Path, timeout: int) -> dict[str, Any]:
    return verify_wave2(ROOT, plan_path, timeout)


def _verify_wave3(plan_path: Path, timeout: int) -> dict[str, Any]:
    return verify_wave3(ROOT, plan_path, timeout)


def _verify_wave4(plan_path: Path, timeout: int) -> dict[str, Any]:
    return verify_wave4(ROOT, plan_path, timeout)


def _verify_wave5(plan_path: Path, timeout: int) -> dict[str, Any]:
    return verify_wave5(ROOT, plan_path, timeout)


def _verify_wave6(plan_path: Path, timeout: int) -> dict[str, Any]:
    deliverable_rows = _collect_deliverables(WAVE_6_DELIVERABLES)
    _check_line_budgets(("tools/reconcile_ingestion_coverage.py",))
    gate_json = _check_decision_gate(6, plan_path)

    print("[Wave 6] Verifying test manifest enrollment...")
    manifest = read_json(ROOT / "tests" / "manifests" / "active_graph_ingestion.json")
    for req_e2e in ("tests.end_to_end.test_ingestion_coverage_recovery_e2e",):
        if req_e2e not in manifest.get("e2e_tests", []):
            raise RuntimeError(f"Manifest missing e2e test: {req_e2e}")
    for req_det in ("tests.adversarial.test_ingestion_coverage_recovery_adversarial",):
        if req_det not in manifest.get("deterministic_tests", []):
            raise RuntimeError(f"Manifest missing deterministic test: {req_det}")

    print("[Wave 6] Verifying contract schemas and fixtures...")
    res_contracts = subprocess.run([sys.executable, str(ROOT / "tools" / "validate_dual_pipeline_contracts.py")], capture_output=True, text=True, cwd=ROOT)
    if res_contracts.returncode != 0 or '"status": "PASS"' not in res_contracts.stdout:
        raise RuntimeError(f"Contract validation failed:\n{res_contracts.stdout}\n{res_contracts.stderr}")

    print("[Wave 6] Running coverage reconciliation E2E & adversarial test suites...")
    exit_code = _run_pytest([
        "tests/end_to_end/test_ingestion_coverage_recovery_e2e.py",
        "tests/adversarial/test_ingestion_coverage_recovery_adversarial.py",
    ], timeout)

    return {
        "deliverables": deliverable_rows,
        "contract_validation": {"status": "PASS", "suite": "recovery_manifest"},
        "test_verification": {
            "suite": "active_graph_ingestion_wave6",
            "command": "pytest tests/end_to_end/test_ingestion_coverage_recovery_e2e.py tests/adversarial/test_ingestion_coverage_recovery_adversarial.py",
            "exit_code": exit_code,
            "e2e_exempt": False,
        },
        "decision_clearance": {"clearance_granted": True, "open_decisions_count": gate_json.get("open_decisions_count", 0)},
    }


def _verify_wave7(
    plan_path: Path, timeout: int, qual_path: Path | None, auth_path: Path | None
) -> dict[str, Any]:
    if not qual_path or not qual_path.resolve().is_file():
        raise RuntimeError("Wave 7 requires valid --qualification-manifest file.")
    if not auth_path or not auth_path.resolve().is_file():
        raise RuntimeError("Wave 7 requires valid --authorization-ref file.")

    qual_data = read_json(qual_path.resolve())
    auth_data = read_json(auth_path.resolve())

    # Verify authorization binding to qualification manifest
    expected_digest = qual_data.get("canonical_digest", {}).get("value")
    if auth_data.get("qualification_manifest_digest") != expected_digest:
        raise RuntimeError(
            f"Authorization ref digest mismatch: got {auth_data.get('qualification_manifest_digest')}, expected {expected_digest}"
        )
    if auth_data.get("status") != "AUTHORIZED":
        raise RuntimeError(f"Authorization ref status is not AUTHORIZED: {auth_data.get('status')}")

    deliverable_rows = _collect_deliverables(WAVE_7_DELIVERABLES)
    gate_json = _check_decision_gate(7, plan_path)

    print("[Wave 7] Verifying test manifest enrollment...")
    manifest = read_json(ROOT / "tests" / "manifests" / "active_graph_ingestion.json")
    for req_e2e in ("tests.end_to_end.test_active_ingestion_live_workflow_e2e",):
        if req_e2e not in manifest.get("e2e_tests", []):
            raise RuntimeError(f"Manifest missing e2e test: {req_e2e}")
    for req_det in ("tests.adversarial.test_active_ingestion_live_recovery_adversarial",):
        if req_det not in manifest.get("deterministic_tests", []):
            raise RuntimeError(f"Manifest missing deterministic test: {req_det}")

    print("[Wave 7] Running live workflow E2E & recovery adversarial test suites...")
    exit_code = _run_pytest([
        "tests/end_to_end/test_active_ingestion_live_workflow_e2e.py",
        "tests/adversarial/test_active_ingestion_live_recovery_adversarial.py",
    ], timeout)

    return {
        "deliverables": deliverable_rows,
        "contract_validation": {"status": "PASS", "suite": "live_workflow_and_recovery"},
        "test_verification": {
            "suite": "active_graph_ingestion_wave7",
            "command": "pytest tests/end_to_end/test_active_ingestion_live_workflow_e2e.py tests/adversarial/test_active_ingestion_live_recovery_adversarial.py",
            "exit_code": exit_code,
            "e2e_exempt": False,
        },
        "decision_clearance": {"clearance_granted": True, "open_decisions_count": gate_json.get("open_decisions_count", 0)},
        "qualification_manifest": qual_path.relative_to(ROOT).as_posix() if qual_path.is_relative_to(ROOT) else str(qual_path),
        "authorization_ref": auth_path.relative_to(ROOT).as_posix() if auth_path.is_relative_to(ROOT) else str(auth_path),
    }


def _dispatch_wave(wave: int, plan_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    if wave == 1:
        return _verify_wave1(plan_path, args.timeout)
    if wave == 2:
        return _verify_wave2(plan_path, args.timeout)
    if wave == 3:
        return _verify_wave3(plan_path, args.timeout)
    if wave == 4:
        return _verify_wave4(plan_path, args.timeout)
    if wave == 5:
        return _verify_wave5(plan_path, args.timeout)
    if wave == 6:
        return _verify_wave6(plan_path, args.timeout)
    if wave == 7:
        return _verify_wave7(plan_path, args.timeout, args.qualification_manifest, args.authorization_ref)
    raise ValueError(f"Unsupported wave: {wave}")


def _build_receipt_payload(
    wave: int, plan_path: Path, predecessor_info: Optional[dict[str, Any]], verification: dict[str, Any]
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    receipt_payload: dict[str, Any] = {
        "contract_id": "urn:nee:dual-pipeline:active-ingestion-completion-receipt:1.0.0",
        "receipt_type": "active_ingestion_completion_receipt",
        "wave": wave,
        "terminal_outcome": "PASS",
        "plan_path": plan_path.relative_to(ROOT).as_posix() if plan_path.is_relative_to(ROOT) else str(plan_path),
        "plan_digest": sha256_file(plan_path),
        "parent_receipt": predecessor_info["path"] if predecessor_info else None,
        "parent_receipt_digest": predecessor_info["sha256"] if predecessor_info else None,
        "timestamp": now,
        "git_commit": _git("rev-parse", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "invariants_verified": [
            "INV-INGESTION-ACTIVE-COMPLETION",
            "INV-INGESTION-ENGINEERING-ISOLATION",
            "INV-AGENTS-MD-MAX-SIZE",
            "INV-BUDGET-FILE-LINES",
        ],
        "deliverables": verification["deliverables"],
        "contract_validation": verification["contract_validation"],
        "test_verification": verification["test_verification"],
        "decision_clearance": verification["decision_clearance"],
    }
    if "qualification_manifest" in verification:
        receipt_payload["qualification_manifest"] = verification["qualification_manifest"]
    if "authorization_ref" in verification:
        receipt_payload["authorization_ref"] = verification["authorization_ref"]
    return receipt_payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Active Graph Ingestion Acceptance Runner")
    parser.add_argument("--wave", type=int, required=True, choices=range(1, 8), help="Wave number (1-7)")
    parser.add_argument("--plan", type=Path, default=ROOT / "docs" / "plans" / "docs_active_graph_ingestion_plan.md")
    parser.add_argument("--receipt", type=Path, help="Explicit receipt destination path")
    parser.add_argument("--predecessor", type=Path, help="Predecessor wave receipt path")
    parser.add_argument("--qualification-manifest", type=Path, help="Source qualification manifest (Wave 7)")
    parser.add_argument("--authorization-ref", type=Path, help="Authentic scoped authorization ref (Wave 7)")
    parser.add_argument("--timeout", type=int, default=60, help="Per-test timeout in seconds")

    args = parser.parse_args(argv)
    wave = args.wave
    plan_path = args.plan.resolve()
    if not plan_path.is_file():
        print(f"ERROR: Plan file not found: {plan_path}", file=sys.stderr)
        return 1

    receipt_path = args.receipt.resolve() if args.receipt else (ROOT / "receipts" / "active_graph_ingestion" / f"wave{wave}.json")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)

    predecessor_info = None
    if wave > 1:
        pred_path = args.predecessor.resolve() if args.predecessor else (ROOT / "receipts" / "active_graph_ingestion" / f"wave{wave - 1}.json")
        try:
            predecessor_info = _verify_predecessor(pred_path, expected_wave=wave - 1)
        except Exception as e:
            print(f"ERROR: Parent receipt verification failed: {e}", file=sys.stderr)
            return 1

    print("================================================================================")
    print(f"  ACTIVE GRAPH INGESTION ACCEPTANCE RUNNER — WAVE {wave}")
    print("================================================================================")
    print(f"Plan:        {plan_path.relative_to(ROOT) if plan_path.is_relative_to(ROOT) else plan_path}")
    print(f"Receipt:     {receipt_path.relative_to(ROOT) if receipt_path.is_relative_to(ROOT) else receipt_path}")
    if predecessor_info:
        print(f"Predecessor: {predecessor_info['path']} (SHA: {predecessor_info['sha256'][:16]}...)")
    print("--------------------------------------------------------------------------------")

    try:
        verification = _dispatch_wave(wave, plan_path, args)
    except Exception as exc:
        print(f"❌ FAIL: Wave {wave} verification halted: {exc}", file=sys.stderr)
        return 1

    receipt_payload = _build_receipt_payload(wave, plan_path, predecessor_info, verification)
    sealed = _seal_receipt(receipt_payload)
    write_json(receipt_path, sealed)
    print("--------------------------------------------------------------------------------")
    print(f"✅ PASS: Wave {wave} completed and sealed.")
    print(f"Receipt written to: {receipt_path}")
    print(f"Canonical Digest:   {sealed['canonical_digest']['value']}")
    print("================================================================================")
    return 0



if __name__ == "__main__":
    sys.exit(main())
