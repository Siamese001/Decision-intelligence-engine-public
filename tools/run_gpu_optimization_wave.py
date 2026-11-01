#!/usr/bin/env python3
"""tools/run_gpu_optimization_wave.py

Governed Wave Acceptance Runner and Receipt Sealer for GPU Optimization.
Enforces:
1. Sequential wave predecessor validation (Wave N requires Wave N-1 COMPLETE receipt).
2. Pre-wave HITL decision clearance gate.
3. Deliverable file presence and SHA-256 fingerprinting.
4. Active CI enrollment verification for all declared acceptance tests.
5. In-band progress execution with mandatory per-test timeout (--timeout=60).
6. Zero skipped test invariant (skipped > 0 fails closed).
7. Host hardware, software, model, and configuration binding.
8. RFC-8785 canonical digest calculation and immutable receipt sealing.
"""
from __future__ import annotations

import argparse
import datetime
import os
from pathlib import Path
import sys
import uuid
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.hitl_governance_core.pre_wave_interceptor import evaluate_pre_wave_gate
from tools.gpu_optimization_runner_core import (
    WAVE_DECLARATIONS,
    canonical_digest,
    collect_hardware_software_telemetry,
    collect_model_config_corpus_digests,
    compute_file_sha256,
    get_git_commit,
    verify_ci_enrollment,
    verify_predecessor_receipt,
    write_immutable_receipt,
)
from tools.pytest_runner_backend import run_pytest_with_progress


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def execute_gpu_wave(
    wave: int,
    sandbox: Path,
    plan_path: Path,
    receipt_root: Path,
    timeout: int = 60,
    immutable: bool = True,
    override_tests: Optional[List[str]] = None,
) -> int:
    """Executes wave acceptance tests, verifies invariants, and seals cryptographic receipt."""
    start_time = _utc_now_iso()
    run_id = f"gpu-wave{wave}-{uuid.uuid4().hex[:12]}"
    print(f"[{start_time}] Starting GPU Optimization Wave {wave} Acceptance Run (run_id: {run_id})", flush=True)

    # 1. Validate sandbox
    resolved_sandbox = sandbox.resolve()
    resolved_sandbox.mkdir(parents=True, exist_ok=True)
    print(f"Disposable Runtime Sandbox: {resolved_sandbox}", flush=True)

    # 2. Validate plan path
    if not plan_path.is_file():
        print(f"ERROR: Plan file not found: {plan_path}", file=sys.stderr)
        return 1
    plan_digest = compute_file_sha256(plan_path)

    # 3. Pre-wave decision gate
    gate_res = evaluate_pre_wave_gate(wave=wave, plan_path=plan_path, allow_zero_decisions=True)
    if not gate_res.clearance_granted:
        print(f"ERROR: Pre-wave decision gate failed for Wave {wave}: {gate_res.diagnostics}", file=sys.stderr)
        return gate_res.exit_code

    # 4. Resolve Wave declarations
    wave_decl = WAVE_DECLARATIONS.get(wave)
    if not wave_decl:
        print(f"ERROR: Wave {wave} declarations not configured in runner", file=sys.stderr)
        return 1

    deliverables = wave_decl["deliverables"]
    acceptance_tests = override_tests or wave_decl["acceptance_tests"]

    # Verify deliverable files exist
    deliverable_digests: Dict[str, str] = {}
    for d in deliverables:
        dp = ROOT / d
        if not dp.exists():
            print(f"ERROR: Declared deliverable missing on disk: {d}", file=sys.stderr)
            return 1
        deliverable_digests[d] = compute_file_sha256(dp) if dp.is_file() else "DIRECTORY"

    # 5. Verify CI enrollment
    ci_enrollment = verify_ci_enrollment(acceptance_tests, repo_root=ROOT)

    # 6. Verify predecessor receipt
    receipt_root.mkdir(parents=True, exist_ok=True)
    predecessor = verify_predecessor_receipt(receipt_root, wave)

    target_receipt_path = receipt_root / f"gpu_wave_{wave}_receipt.json"
    sandbox_receipt_path = resolved_sandbox / "receipts" / f"gpu_wave_{wave}_receipt.json"

    # 7. Execute scoped pytest acceptance run
    print(f"Executing {len(acceptance_tests)} acceptance test files with pytest (--timeout={timeout})...", flush=True)
    test_run_res = run_pytest_with_progress(
        tests=acceptance_tests,
        timeout=timeout,
        verbose=1,
    )

    if test_run_res.total_tests == 0:
        print("ERROR: Zero tests collected by pytest; failing closed.", file=sys.stderr)
        return 1

    if not test_run_res.wasSuccessful():
        print(
            f"ERROR: Acceptance tests failed: {test_run_res.failed_count} failures, "
            f"{test_run_res.error_count} errors.",
            file=sys.stderr,
        )
        return 1

    if test_run_res.skipped_count > 0:
        print(f"ERROR: Unexplained test skips encountered ({test_run_res.skipped_count}); failing closed.", file=sys.stderr)
        return 1

    # 8. Collect telemetry and environment digests
    hw_sw_telemetry = collect_hardware_software_telemetry()
    model_digests = collect_model_config_corpus_digests(ROOT)
    git_commit = get_git_commit(ROOT)

    # 9. Build and seal receipt payload
    completed_time = _utc_now_iso()
    receipt_payload: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "wave": wave,
        "wave_title": wave_decl["title"],
        "run_id": run_id,
        "command": [sys.executable, *sys.argv],
        "started_at": start_time,
        "completed_at": completed_time,
        "repository_root": str(ROOT),
        "commit": git_commit,
        "sandbox": str(resolved_sandbox),
        "plan": {
            "path": str(plan_path),
            "sha256": plan_digest,
        },
        "hardware_software": hw_sw_telemetry,
        "model_configuration_corpus_digests": model_digests,
        "generation": run_id,
        "deliverables": deliverable_digests,
        "declared_tests": acceptance_tests,
        "ci_enrollment": ci_enrollment,
        "collected_test_ids": test_run_res.collected_ids,
        "raw_measurements": {
            "test_counts": {
                "total": test_run_res.total_tests,
                "passed": test_run_res.passed_count,
                "failed": test_run_res.failed_count,
                "errors": test_run_res.error_count,
                "skipped": test_run_res.skipped_count,
            },
            "elapsed_seconds": test_run_res.elapsed_seconds,
        },
        "parent_receipt": predecessor,
        "status": "PASS",
    }

    receipt_digest = canonical_digest(receipt_payload)
    receipt_payload["receipt_digest"] = receipt_digest

    # Write immutable receipt to both target_receipt_path and sandbox
    write_immutable_receipt(target_receipt_path, receipt_payload, immutable=immutable)
    write_immutable_receipt(sandbox_receipt_path, receipt_payload, immutable=immutable)

    print(f"\n✅ GPU Optimization Wave {wave} Acceptance SEALED successfully!")
    print(f"Receipt: {target_receipt_path}")
    print(f"Receipt Digest: {receipt_digest}")
    print(f"Tests: {test_run_res.passed_count}/{test_run_res.total_tests} passed (0 skips, 0 errors).")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="GPU Optimization Wave Acceptance Runner")
    parser.add_argument("--wave", type=int, default=1, help="Wave number (default: 1)")
    parser.add_argument("--sandbox", type=Path, required=True, help="Explicit disposable runtime sandbox path ($GPU_SANDBOX)")
    parser.add_argument("--immutable", action="store_true", default=True, help="Seal receipt with read-only permissions")
    parser.add_argument(
        "--plan",
        type=Path,
        default=ROOT / "docs/plans/docs_gpu_optimization_debate_plan_20261006.md",
        help="Plan markdown path",
    )
    parser.add_argument(
        "--receipt-root",
        type=Path,
        default=ROOT / "reports/gpu_optimization",
        help="Directory to write sealed receipts",
    )
    parser.add_argument("--timeout", type=int, default=60, help="Per-test hard timeout in seconds")

    args = parser.parse_args(argv)
    return execute_gpu_wave(
        wave=args.wave,
        sandbox=args.sandbox,
        plan_path=args.plan,
        receipt_root=args.receipt_root,
        timeout=args.timeout,
        immutable=args.immutable,
    )


if __name__ == "__main__":
    sys.exit(main())
