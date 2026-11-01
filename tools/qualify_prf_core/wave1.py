"""Wave 1 Qualification Executor: Preserve evidence and establish independent truth."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import jsonschema

from .evidence import (
    CACHE_ROOT,
    analyze_channel_and_headers,
    get_git_revision,
    resolve_repo_root,
    revalidate_manifest,
    sha256_file,
)
from .metrics import get_scoring_protocol

REPO_ROOT = resolve_repo_root()
SCHEMA_PATH = REPO_ROOT / "contracts/dual_pipeline/speaker_quality_receipt.schema.json"


def _analyze_selected_cache(cache_path: Path) -> Tuple[Dict[str, Any], int]:
    with open(cache_path, "r", encoding="utf-8") as f:
        segments = json.load(f)

    total_turns = len(segments)
    one_word_turns = sum(1 for s in segments if len(s.get("text", "").strip().split()) == 1)
    at_most_3_words = sum(1 for s in segments if len(s.get("text", "").strip().split()) <= 3)

    client_turns = 0
    client_words = 0
    total_words = 0
    sandwiches = 0

    for i, s in enumerate(segments):
        words = s.get("text", "").strip().split()
        wc = len(words)
        total_words += wc
        spk = s.get("speaker", "Unknown")

        if spk in ("Unknown", "[Client Speaker]", "Client Speaker"):
            client_turns += 1
            client_words += wc

        if 0 < i < total_turns - 1:
            p_spk = segments[i - 1].get("speaker")
            n_spk = segments[i + 1].get("speaker")
            if spk == "Unknown" and p_spk != "Unknown" and p_spk == n_spk:
                sandwiches += 1

    named_turns = total_turns - client_turns
    named_words = total_words - client_words
    named_turn_cov = round((named_turns / max(1, total_turns)) * 100, 2)
    named_word_cov = round((named_words / max(1, total_words)) * 100, 2)
    gate_status = "PASS" if named_word_cov >= 95.0 else "FAIL"

    metrics = {
        "total_displayed_turns": total_turns,
        "one_word_turns": one_word_turns,
        "at_most_3_words": at_most_3_words,
        "generic_client_turns": client_turns,
        "named_turns": named_turns,
        "released_dialogue_words": total_words,
        "generic_client_words": client_words,
        "released_named_words": named_words,
        "named_turn_coverage_pct": named_turn_cov,
        "named_word_coverage_pct": named_word_cov,
        "released_coverage_gate_status": gate_status,
    }
    return metrics, sandwiches


def _build_wave_1_receipt(
    manifest_path: Path,
    manifest_digest: str,
    source_hashes: Dict[str, str],
    channel_analysis: Dict[str, Any],
    cache_metrics: Dict[str, Any],
    sandwiches_count: int,
    ref_manifest_path: Path,
    ref_data: Dict[str, Any],
    corrections_records: List[Dict[str, Any]],
    id_corrections_path: Path,
    sandbox_root: Path,
) -> Dict[str, Any]:
    ref_manifest_digest = sha256_file(ref_manifest_path)
    return {
        "schema_version": "1.0.0",
        "receipt_type": "speaker_quality_receipt",
        "wave": "Wave 1",
        "title": "PRF Diarization Evidence Preservation & Independent Truth Binding",
        "meeting_id": "00_10-06-2026_Prf_Scope_Sow_Clement_Venkat_master_154116",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "terminal_outcome": "PASS",
        "source_manifest_path": str(manifest_path),
        "source_manifest_digest": manifest_digest,
        "source_hashes": source_hashes,
        "model_provenance": {
            "model_name": "pyannote/speaker-diarization-3.1",
            "snapshot_revision": None,
            "device": "Apple Silicon Metal (Local Hermetic)",
            "execution_platform": "Darwin arm64 (MPS/Metal)",
            "provenance_status": "HISTORICAL_UNVERIFIED",
            "historical_model_details_status": "UNMEASURED_IN_HISTORICAL_RUN",
            "details": {
                "whisper_model": "mlx-community/whisper-large-v3-mlx",
                "asr_normalization": "mono_16k",
                "historical_receipt_note": "Acoustic provenance receipt lacks bound model commit revision, decode parameters, and inference timestamps.",
            },
        },
        "denominators": {
            "released_text_metrics": cache_metrics,
            "independent_reference_metrics": {
                "reference_speech_words": None,
                "reference_named_words": None,
                "independent_reference_named_coverage_pct": None,
                "named_word_attribution_accuracy_pct": None,
                "true_identified_reference_word_share_pct": None,
                "measurement_status": "UNMEASURED",
            },
            "scoring_protocol": get_scoring_protocol(),
        },
        "independent_reference_origin": {
            "manifest_path": str(ref_manifest_path),
            "manifest_sha256": ref_manifest_digest,
            "status": "BOUND",
            "origin_description": "Frozen reference manifest binding PRF master audio partitions, sample ranges, and all 105 same-cluster Unknown sandwiches.",
            "partitions": ref_data.get("partitions", {}),
            "exclusion_rules": [
                "Difficult speech cannot be excluded post-hoc.",
                "Uncertain voices must remain UNRESOLVED.",
                "No synthetic persona substitution.",
            ],
        },
        "unknown_status": {
            "generic_client_turns": cache_metrics["generic_client_turns"],
            "unknown_turns": cache_metrics["generic_client_turns"],
            "same_cluster_sandwiches": sandwiches_count,
            "unmeasured_metrics": [
                "independent_reference_named_word_coverage",
                "named_word_attribution_accuracy",
                "speech_recognition_word_error_rate",
                "diarization_error_rate_der",
            ],
            "status_description": "Historical 411-turn artifact has 166 generic/unknown turns and fails 95% word coverage gate at 90.53%; independent accuracy is UNMEASURED.",
        },
        "channel_and_header_analysis": channel_analysis,
        "identity_corrections": {
            "correction_count": len(corrections_records),
            "records": corrections_records,
        },
        "downstream_hashes": {
            "ref_manifest_sha256": ref_manifest_digest,
            "id_corrections_sha256": sha256_file(id_corrections_path),
        },
        "prior_receipt_validation": None,
        "execution_environment": {
            "code_revision": get_git_revision(),
            "runtime_location": str(sandbox_root),
            "sandbox_root": str(sandbox_root),
            "python_version": sys.version.split()[0],
            "platform": sys.platform,
        },
    }


def execute_wave_1_qualification(
    manifest_path: Path,
    sandbox_root: Path,
    receipt_path: Path,
    ref_manifest_path: Path,
    id_corrections_path: Path,
) -> int:
    """Executes Wave 1 qualification."""
    print("=" * 80)
    print("  PRF DIARIZATION QUALIFICATION DRIVER — WAVE 1: PRESERVE EVIDENCE")
    print("=" * 80)

    source_hashes, manifest_errors, manifest_digest = revalidate_manifest(manifest_path)
    if manifest_errors:
        print("[FAIL] Manifest revalidation failed with errors:", file=sys.stderr)
        for err in manifest_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print("[PASS] All manifest files exist and hashes revalidate exactly.")

    channel_analysis = analyze_channel_and_headers()
    print("[PASS] Channel and audio header analysis completed.")

    cache_path = CACHE_ROOT / "output/transcript_cache/00_10-06-2026_Prf_Scope_Sow_Clement_Venkat_master_154116_584009ff5464f464.segments.json"
    if not cache_path.exists():
        print(f"[FAIL] Selected cache not found: {cache_path}", file=sys.stderr)
        return 1
    cache_metrics, sandwiches_count = _analyze_selected_cache(cache_path)

    if not ref_manifest_path.exists():
        print(f"[FAIL] Reference manifest missing: {ref_manifest_path}", file=sys.stderr)
        return 1
    with open(ref_manifest_path, "r", encoding="utf-8") as f:
        ref_data = json.load(f)

    if not id_corrections_path.exists():
        print(f"[FAIL] Identity corrections missing: {id_corrections_path}", file=sys.stderr)
        return 1
    with open(id_corrections_path, "r", encoding="utf-8") as f:
        id_data = json.load(f)

    receipt = _build_wave_1_receipt(
        manifest_path=manifest_path,
        manifest_digest=manifest_digest,
        source_hashes=source_hashes,
        channel_analysis=channel_analysis,
        cache_metrics=cache_metrics,
        sandwiches_count=sandwiches_count,
        ref_manifest_path=ref_manifest_path,
        ref_data=ref_data,
        corrections_records=id_data.get("corrections", []),
        id_corrections_path=id_corrections_path,
        sandbox_root=sandbox_root,
    )

    with open(SCHEMA_PATH, "r", encoding="utf-8") as sf:
        schema = json.load(sf)
    try:
        jsonschema.validate(instance=receipt, schema=schema)
        print("[PASS] Receipt payload validated against speaker_quality_receipt.schema.json.")
    except jsonschema.ValidationError as ve:
        print(f"[FAIL] Schema validation error: {ve.message}", file=sys.stderr)
        return 1

    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    with open(receipt_path, "w", encoding="utf-8") as rf:
        json.dump(receipt, rf, indent=2)

    sandbox_root.mkdir(parents=True, exist_ok=True)
    with open(sandbox_root / receipt_path.name, "w", encoding="utf-8") as srf:
        json.dump(receipt, srf, indent=2)

    print(f"[PASS] Wave 1 qualification receipt sealed at {receipt_path} (digest: {sha256_file(receipt_path)})")
    return 0
