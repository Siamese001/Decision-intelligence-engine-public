"""Wave 1 verifier: Independent baseline reconciliation, reference set validation, and pure contract tests."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from evals.independent_baseline_metrics import (
    compute_baseline_metrics,
    format_baseline_metrics_report,
    verify_baseline_invariants,
)
from tools.transcript_chunking_verifier_core.common import (
    ROOT_DIR,
    get_git_info,
    logger,
    run_tests_via_progress_runner,
    sha256_file,
)


def _verify_reference_set(ref_set_path: Path) -> bool:
    if not ref_set_path.exists():
        logger.error("Independent reference set not found: %s", ref_set_path)
        return False
    with open(ref_set_path, "r", encoding="utf-8") as f:
        ref_set_data = json.load(f)
    required = {
        "complete_sentence", "chunk_boundary", "rapid_exchange",
        "one_word_reply", "overlapping_speech", "silence_tail", "uncertain_speaker",
    }
    if not required.issubset(set(ref_set_data.get("categories_covered", []))):
        logger.error("Missing required reference categories")
        return False
    audio_anchors_dir = ROOT_DIR / "data/reference/audio_anchors"
    for item in ref_set_data.get("reference_items", []):
        clip_path = audio_anchors_dir / item.get("clip_filename", "")
        if not clip_path.exists() or sha256_file(clip_path) != item.get("clip_sha256"):
            logger.error("Clip invalid or mismatch: %s", item.get("clip_filename"))
            return False
    return True


def _check_schema_valid(schema_path: Path) -> bool:
    if not schema_path.exists():
        logger.error("Schema not found: %s", schema_path)
        return False
    import jsonschema
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
        return True
    except Exception as e:
        logger.error("Schema Draft 2020-12 validation failed: %s", e)
        return False


def verify_wave_1(
    manifest_path: Path,
    run_root: Path,
    receipt_path: Path,
) -> int:
    logger.info("Verifying Wave 1: Independent Baseline Reconciliation and Reference Set...")

    # 1. Check Input Manifest
    if not manifest_path.exists():
        logger.error("Input manifest not found: %s", manifest_path)
        return 1

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)

    # Verify attachment hash
    expected_att_hash = manifest_data.get("attachment_evidence", {}).get("expected_sha256")
    att_path = ROOT_DIR / manifest_data.get("attachment_evidence", {}).get("verified_path", "")
    observed_att_hash = sha256_file(att_path)

    if observed_att_hash != expected_att_hash:
        logger.error(
            "Attachment hash mismatch: expected %s, got %s",
            expected_att_hash,
            observed_att_hash,
        )
        return 1

    # 2. Verify baseline metrics against reference inventory and ledger
    inv_path = ROOT_DIR / "data/reference/lens_20261006_ground_truth_inventory.json"
    ledger_path = ROOT_DIR / "hitl_decision_ledger.json"
    if not inv_path.exists():
        logger.error("Reference inventory not found: %s", inv_path)
        return 1

    summary = compute_baseline_metrics(inv_path, ledger_path)
    invariants = verify_baseline_invariants(summary)

    for inv_name, passed in invariants.items():
        if not passed:
            logger.error("Wave 1 Invariant check failed: %s", inv_name)
            return 1

    # 3. Verify independent reference set
    ref_set_path = ROOT_DIR / "data/reference/lens_20261006_independent_reference_set.json"
    if not _verify_reference_set(ref_set_path):
        return 1

    # 4. Check JSON Schema syntax and draft specification
    schema_path = ROOT_DIR / "contracts/dual_pipeline/speaker_utterance.schema.json"
    if not _check_schema_valid(schema_path):
        return 1

    # 5. Execute Wave 1 Test Suite
    test_files = [
        "tests/unit/test_transcript_chunking_contract.py",
        "tests/test_lens_diarization_reference_audit.py",
        "tests/unit/test_independent_baseline_metrics.py",
        "tests/end_to_end/test_independent_baseline_e2e.py",
    ]
    ret_code, test_output = run_tests_via_progress_runner(test_files, timeout=60)
    if ret_code != 0:
        logger.error("Test execution failed (exit code %d):\n%s", ret_code, test_output)
        return ret_code

    # 6. Generate Sealed Receipt
    git_info = get_git_info()
    metrics_report = format_baseline_metrics_report(summary)
    receipt_data = {
        "receipt_version": "1.0.0",
        "wave": 1,
        "title": "Establish an Independent Baseline",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "terminal_outcome": "PASS",
        "provenance": {
            "git_info": git_info,
            "input_manifest_path": str(manifest_path),
            "input_manifest_sha256": sha256_file(manifest_path),
            "schema_path": str(schema_path),
            "schema_sha256": sha256_file(schema_path),
            "reference_inventory_sha256": sha256_file(inv_path),
            "independent_reference_set_sha256": sha256_file(ref_set_path),
            "run_root": str(run_root),
        },
        "disaggregated_metrics": summary.to_dict(),
        "governance_invariants": invariants,
        "independent_reference_set_verification": {
            "categories_verified": sorted(list(covered)),
            "reference_items_count": len(ref_set_data.get("reference_items", [])),
            "disputed_meeting_endpoint_sec": ref_set_data.get("disputed_meeting_endpoint_sec", 818.0),
            "partitions": ref_set_data.get("partitions", {}),
        },
        "metrics_report_markdown": metrics_report,
        "test_results": {
            "test_files": test_files,
            "exit_code": ret_code,
            "summary": "All Wave 1 contract, audit, metrics, and E2E tests passed with zero violations.",
        },
        "next_wave": {
            "wave": 2,
            "title": "Preserve audio context and assemble complete thoughts",
            "prerequisite_receipt": str(receipt_path),
        },
    }

    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    with open(receipt_path, "w", encoding="utf-8") as f:
        json.dump(receipt_data, f, indent=2)

    logger.info("Wave 1 Receipt successfully sealed at %s", receipt_path)
    print("\n" + "=" * 70)
    print("WAVE 1 RECEIPT SUMMARY: PASS (Establish an Independent Baseline)")
    print("=" * 70)
    print("Terminal Outcome     : PASS")
    print(f"Total Display Rows   : {summary.display_entries} (Single-word: {summary.single_word_entries}, <=3-word: {summary.at_most_three_word_entries})")
    print(f"Contiguous Runs      : {summary.unnecessary_sentence_splits} (Runs needing complete-thought assembly)")
    print(f"Actual Questions     : {summary.actual_questions_answered} (HITL Ledger; NOT 426 entries)")
    print(f"Proposed Review Cards: {summary.proposed_review_cards} cards (Playback: {summary.playback_time_sec}s vs {summary.fragment_playback_time_sec}s)")
    print(f"Unresolved Speech    : {summary.unresolved_words_count} turns ({summary.unresolved_duration_sec}s acoustic bleed Unknown)")
    print(f"Reference Categories : {len(covered)}/7 verified ({', '.join(sorted(covered))})")
    print(f"Sealed Receipt       : {receipt_path}")
    print("=" * 70 + "\n")

    return 0
