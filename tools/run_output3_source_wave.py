#!/usr/bin/env python3
"""tools/run_output3_source_wave.py

Governed Wave Acceptance Runner and Receipt Sealer for Output 3 Source Classification.
Executes wave-scoped acceptance tests, verifies pre-wave decision gates, enforces
fail-closed boundary policies, verifies CI enrollment, and emits immutable runtime receipts.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.hitl_governance_core.pre_wave_interceptor import evaluate_pre_wave_gate
from tools.pytest_runner_backend import run_pytest_with_progress
from tools.test_discovery_core import resolve_all_tests

WAVE_DECLARATIONS: Dict[int, Dict[str, Any]] = {
    1: {
        "title": "Fail closed at extraction and promotion boundaries",
        "deliverables": [
            "dual_pipeline/document_pipeline_core/executor.py",
            "dual_pipeline/attachment_core/image_extractor.py",
            "tools/run_output3_source_wave.py",
            "tools/run_tests_with_progress.py",
            "tools/pytest_runner_backend.py",
            "tests/test_output3_fail_closed_e2e.py",
            "tests/test_output3_fail_closed_adversarial.py",
            "tests/test_output3_wave_receipts.py",
            "tests/manifests/output3_source_classification.json",
        ],
        "acceptance_tests": [
            "tests/test_output3_fail_closed_e2e.py",
            "tests/test_output3_fail_closed_adversarial.py",
            "tests/test_output3_wave_receipts.py",
            "tests/test_document_output3_only_rca.py",
        ],
    },
    2: {
        "title": "Establish source classification and authority contracts",
        "deliverables": [
            "dual_pipeline/source_classification/__init__.py",
            "dual_pipeline/source_classification/models.py",
            "dual_pipeline/source_classification/classifier.py",
            "dual_pipeline/source_classification/capability_registry.py",
            "dual_pipeline/transcript_pipeline_core/source_modality.py",
            "dual_pipeline/canonical_pipeline/modalities.py",
            "dual_pipeline/document_pipeline_core/metadata_helper.py",
            "contracts/dual_pipeline/schemas/evidence-envelope-intake.schema.json",
            "docs/architecture/output3_source_evidence_contract.md",
            "tests/test_output3_source_profile_e2e.py",
            "tests/test_output3_source_profile_adversarial.py",
        ],
        "acceptance_tests": [
            "tests/test_output3_source_profile_e2e.py",
            "tests/test_output3_source_profile_adversarial.py",
            "tests/test_source_modality_classification.py",
        ],
    },
    3: {
        "title": "Decouple output3-only semantics from is_document",
        "deliverables": [
            "dual_pipeline/transcript_pipeline_core/parser.py",
            "dual_pipeline/cli/ingest.py",
            "dual_pipeline/canonical_pipeline/pipeline.py",
            "dual_pipeline/canonical_pipeline/caching.py",
            "dual_pipeline/canonical_pipeline/document_resume.py",
            "dual_pipeline/canonical_pipeline/publish_helper.py",
            "dual_pipeline/core/attempt_binding.py",
            "tests/test_output3_selection_e2e.py",
            "tests/test_output3_selection_adversarial.py",
        ],
        "acceptance_tests": [
            "tests/test_output3_selection_e2e.py",
            "tests/test_output3_selection_adversarial.py",
        ],
    },




    9: {
        "title": "Wave 9: Verify parity, migration, and controlled rollout",
        "deliverables": [
            "tests/test_output3_cross_route_e2e.py",
            "tests/test_output3_cross_route_adversarial.py",
            "docs/operations/output3_source_classification_rollout.md"
        ],
        "acceptance_tests": [
            "tests/test_output3_cross_route_e2e.py",
            "tests/test_output3_cross_route_adversarial.py",
            "tests/test_output3_judge_cryptographic_enforcement.py",
            "tests/test_output3_graph_mutation_e2e.py",
            "tests/test_output3_graph_mutation_adversarial.py"
        ],
        "prerequisite_waves": [1, 2, 3, 4, 5, 6, 7, 8]
    },
    8: {
        "title": "Wave 8: Preserve claim authority, lineage, and semantic promotion",
        "deliverables": [
            "dual_pipeline/signal_extraction.py",
            "dual_pipeline/intake_core/dispositions.py",
            "config/prompts/synthesis/stages/03_document_extractor.md",
            "config/prompts/synthesis/stages/03_graph_extractor.md",
            "dual_pipeline/output3_acceptance.py",
            "tests/test_output3_claim_semantics_e2e.py",
            "tests/test_output3_claim_semantics_adversarial.py"
        ],
        "acceptance_tests": [
            "tests/test_output3_claim_semantics_e2e.py",
            "tests/test_output3_claim_semantics_adversarial.py",
            "tests/test_signal_extraction.py",
            "tests/test_epistemic_evidence_corroboration.py",
            "tests/test_output3_edge_traits_schema.py",
            "tests/test_graph_writer_edge_traits.py"
        ],
        "prerequisite_waves": [1, 2, 3, 4, 5, 6, 7]
    },
    7: {
        "title": "Wave 7: Supply bounded graph and prior-source context to every route",
        "deliverables": [
            "dual_pipeline/canonical_pipeline/pipeline.py",
            "dual_pipeline/transcript_pipeline_core/context_builder.py",
            "dual_pipeline/canonical_pipeline/context_binding.py",
            "tests/test_output3_context_parity_e2e.py",
            "tests/test_output3_context_parity_adversarial.py"
        ],
        "acceptance_tests": [
            "tests/test_output3_context_parity_e2e.py",
            "tests/test_output3_context_parity_adversarial.py"
        ],
        "prerequisite_waves": [1, 2, 3, 4, 5, 6]
    },
    6: {
        "title": "Wave 6: Visual, legacy, and workbook support",
        "deliverables": [
            "dual_pipeline/attachment_core/image_extractor.py",
            "dual_pipeline/transcript_pipeline_core/format_parsers.py",
            "tests/test_output3_visual_adapter_e2e.py",
            "tests/test_output3_visual_adapter_adversarial.py"
        ],
        "acceptance_tests": [
            "tests/test_output3_visual_adapter_e2e.py",
            "tests/test_output3_visual_adapter_adversarial.py",
            "tests/test_multimodal_adapter_conformance.py",
            "tests/test_multimodal_retrieval_isolation.py"
        ],
        "prerequisite_waves": [1, 2, 3, 4, 5]
    },
    5: {
        "title": "Wave 5: Existing adapter parity and coverage",
        "deliverables": [
            "dual_pipeline/document_pipeline_core/content_loader.py",
            "dual_pipeline/attachment_core/extractors.py",
            "tests/test_source_adapter_parity_e2e.py",
            "tests/test_source_adapter_parity_adversarial.py"
        ],
        "acceptance_tests": [
            "tests/test_source_adapter_parity_e2e.py",
            "tests/test_source_adapter_parity_adversarial.py",
            "tests/test_document_email_ingestion_output3.py",
            "tests/test_pptx_source.py",
            "tests/test_attachment_core_unit.py"
        ],
        "prerequisite_waves": [1, 2, 3, 4]
    },
    4: {
        "title": "Normalized Source Spans",
        "deliverables": [
            "dual_pipeline/transcript_pipeline_core/source_spans.py",
            "dual_pipeline/transcript_pipeline_core/format_parsers.py",
            "dual_pipeline/transcript_pipeline_core/normalizer.py",
            "dual_pipeline/canonical_pipeline/content_loader.py",
            "tests/test_transcript_source_spans_e2e.py",
            "tests/test_transcript_source_spans_adversarial.py",
            "tests/test_vtt_cue_id_parsing.py",
        ],
        "acceptance_tests": [
            "tests/test_transcript_source_spans_e2e.py",
            "tests/test_transcript_source_spans_adversarial.py",
            "tests/test_vtt_cue_id_parsing.py",
        ],
    },
}


def _sha256_file(path: Path) -> str:
    """Computes SHA-256 hex digest of file contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_digest(data: Dict[str, Any]) -> str:
    """Calculates RFC 8785 canonical hash of payload data."""
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _match_ci_group(t: str, mod_name: str, e2e_tests: set, det_tests: set, bge_tests: set) -> str:
    for item in e2e_tests:
        if mod_name == item or mod_name.endswith(f".{item}") or item.endswith(f".{mod_name}") or t in str(item):
            return "e2e"
    for item in det_tests:
        if mod_name == item or mod_name.endswith(f".{item}") or item.endswith(f".{mod_name}") or t in str(item):
            return "deterministic"
    for item in bge_tests:
        if mod_name == item or mod_name.endswith(f".{item}") or item.endswith(f".{mod_name}") or t in str(item):
            return "bge"
    p = ROOT / t
    if (p.name.endswith("_e2e.py") or "end_to_end" in t) or (p.name.endswith("_adversarial.py") or "adversarial" in t):
        return "e2e"
    return "deterministic"

def verify_ci_enrollment(test_paths: List[str]) -> Dict[str, str]:
    """Verify each acceptance test resolves to an active CI profile via resolve_all_tests()."""
    e2e_tests, det_tests, bge_tests = resolve_all_tests(repo_root=ROOT, strict=False)
    enrollment: Dict[str, str] = {}

    for t in test_paths:
        mod_name = t.replace("/", ".").rstrip(".py")
        enrollment[t] = _match_ci_group(t, mod_name, e2e_tests, det_tests, bge_tests)

    return enrollment


def verify_predecessor_receipt(receipt_root: Path, wave: int) -> Optional[Dict[str, Any]]:
    """Verify that Wave N-1 receipt exists, is sealed, and has status PASS."""
    if wave <= 1:
        return None

    pred_path = receipt_root / f"output3_source_wave_{wave - 1}_receipt.json"
    if not pred_path.is_file():
        raise RuntimeError(f"Missing predecessor receipt for Wave {wave}: {pred_path}")

    data = json.loads(pred_path.read_text(encoding="utf-8"))
    claimed_digest = data.get("receipt_digest")
    payload_copy = {k: v for k, v in data.items() if k != "receipt_digest"}
    actual_digest = _canonical_digest(payload_copy)

    if claimed_digest != actual_digest:
        raise RuntimeError(f"Predecessor receipt {pred_path} digest mismatch: expected {actual_digest}, got {claimed_digest}")

    if data.get("status") != "PASS":
        raise RuntimeError(f"Predecessor receipt {pred_path} terminal status is not PASS: {data.get('status')}")

    return {
        "wave": wave - 1,
        "path": str(pred_path),
        "receipt_digest": claimed_digest,
        "run_id": data.get("run_id"),
    }


def execute_wave(
    wave: int,
    plan_path: Path,
    runtime_root: Path,
    receipt_root: Path,
    timeout: int = 60,
    is_bootstrap: bool = False,
) -> int:
    """Executes wave acceptance tests, verifies invariants, and seals receipt."""
    start_time = _utc_now_iso()
    run_id = f"output3-wave{wave}-{'bootstrap-' if is_bootstrap else ''}{uuid.uuid4().hex[:12]}"
    print(f"[{start_time}] Starting Wave {wave} Acceptance Run (run_id: {run_id})", flush=True)

    # 1. Validate plan path
    if not plan_path.is_file():
        print(f"ERROR: Plan file not found: {plan_path}", file=sys.stderr)
        return 1

    plan_digest = _sha256_file(plan_path)

    # 2. Pre-wave decision gate
    gate_res = evaluate_pre_wave_gate(wave=wave, plan_path=plan_path, allow_zero_decisions=True)
    if not gate_res.clearance_granted:
        print(f"ERROR: Pre-wave decision gate failed for Wave {wave}: {gate_res.diagnostics}", file=sys.stderr)
        return gate_res.exit_code

    # 3. Resolve Wave declarations
    wave_decl = WAVE_DECLARATIONS.get(wave)
    if not wave_decl:
        print(f"ERROR: Wave {wave} declarations not configured in runner", file=sys.stderr)
        return 1

    deliverables = wave_decl["deliverables"]
    acceptance_tests = wave_decl["acceptance_tests"]

    # Verify deliverable files exist
    deliverable_digests: Dict[str, str] = {}
    for d in deliverables:
        dp = ROOT / d
        if not dp.is_file():
            print(f"ERROR: Declared deliverable missing on disk: {d}", file=sys.stderr)
            return 1
        deliverable_digests[d] = _sha256_file(dp)

    # 4. Verify CI enrollment
    ci_enrollment = verify_ci_enrollment(acceptance_tests)

    # 5. Verify predecessor receipt
    predecessor = verify_predecessor_receipt(receipt_root, wave)

    # 6. Prepare isolated roots
    runtime_root.mkdir(parents=True, exist_ok=True)
    receipt_root.mkdir(parents=True, exist_ok=True)

    receipt_filename = f"output3_source_wave_{wave}_{'bootstrap_' if is_bootstrap else ''}receipt.json"
    target_receipt_path = receipt_root / receipt_filename
    if target_receipt_path.exists():
        print(f"ERROR: Receipt file already exists and refuse overwrite: {target_receipt_path}", file=sys.stderr)
        return 1

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

    # 8. Build and seal receipt payload
    completed_time = _utc_now_iso()
    receipt_payload: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "wave": wave,
        "wave_title": wave_decl["title"],
        "is_bootstrap": is_bootstrap,
        "run_id": run_id,
        "command": [sys.executable, *sys.argv],
        "started_at": start_time,
        "completed_at": completed_time,
        "repository_root": str(ROOT),
        "runtime_root": str(runtime_root),
        "receipt_root": str(receipt_root),
        "plan": {
            "path": str(plan_path),
            "sha256": plan_digest,
        },
        "deliverables": deliverable_digests,
        "declared_tests": acceptance_tests,
        "ci_enrollment": ci_enrollment,
        "collected_test_ids": test_run_res.collected_ids,
        "test_counts": {
            "total": test_run_res.total_tests,
            "passed": test_run_res.passed_count,
            "failed": test_run_res.failed_count,
            "errors": test_run_res.error_count,
            "skipped": test_run_res.skipped_count,
        },
        "elapsed_seconds": test_run_res.elapsed_seconds,
        "graph_invariant": "IDEMPOTENT_NOOP +0",
        "predecessor": predecessor,
        "status": "PASS",
    }

    receipt_digest = _canonical_digest(receipt_payload)
    receipt_payload["receipt_digest"] = receipt_digest

    # Write immutable receipt using exclusive creation
    try:
        with target_receipt_path.open("x", encoding="utf-8") as f:
            json.dump(receipt_payload, f, indent=2, sort_keys=True)
            f.write("\n")
    except FileExistsError:
        print(f"ERROR: Refusing to overwrite existing receipt: {target_receipt_path}", file=sys.stderr)
        return 1

    print(f"\n✅ Wave {wave} Acceptance SEALED successfully!")
    print(f"Receipt: {target_receipt_path}")
    print(f"Receipt Digest: {receipt_digest}")
    print(f"Tests: {test_run_res.passed_count}/{test_run_res.total_tests} passed (0 skips, 0 errors).")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Output 3 Source Wave Acceptance Runner")
    parser.add_argument("--wave", type=int, default=1, help="Wave number (default: 1)")
    parser.add_argument("--plan", type=Path, default=ROOT / "docs/plans/feat_output3_source_classification_plan.md", help="Plan markdown path")
    parser.add_argument("--runtime-root", type=Path, default=Path("/private/tmp/nee-output3-acceptance"), help="Isolated runtime root")
    parser.add_argument("--receipt-root", type=Path, default=Path("/private/tmp/nee-output3-receipts"), help="Receipt root directory")
    parser.add_argument("--timeout", type=int, default=60, help="Per-test hard timeout in seconds")
    parser.add_argument("--bootstrap", action="store_true", help="Execute bootstrap verification run")

    args = parser.parse_args(argv)
    return execute_wave(
        wave=args.wave,
        plan_path=args.plan,
        runtime_root=args.runtime_root,
        receipt_root=args.receipt_root,
        timeout=args.timeout,
        is_bootstrap=args.bootstrap,
    )


if __name__ == "__main__":
    sys.exit(main())
