"""
tests/adversarial/test_ingestion_hardening_wave1_adversarial.py

Wave 1 Adversarial Tests covering cases M01, M03, M04, T01, and T02.
Verifies:
1. M01: Finalized audio and transcript require outputs 01, 02, 03; STT evidence is not 01.
2. M03: Meeting notes require 02, 03 and explicit 01-not-applicable; no fabricated speech.
3. M04: Documents require 03 only; no 01/02 generation or transcript gates.
4. T01: Token accounting and receipt normalization without double counting or false zero cost.
5. T02: Shared-file acceptance map resolves non-empty sets for all 4 required modes.
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.operation_contracts import (
    OperationKind,
    resolve_canonical_modality,
    resolve_required_deliverables,
    get_not_applicable_deliverables,
    validate_operation_invariants,
    check_conflicting_flags,
)
from dual_pipeline.accounting_normalization import (
    normalize_provider_counters,
    assess_pricing_uncertainty,
    separate_host_from_pipeline,
    verify_receipt_copy_consistency,
)


def test_m01_contract():
    """Case M01: Finalized audio and spoken transcript require outputs 01, 02, 03; STT evidence is not 01."""
    # Both AUDIO and MEETING_TRANSCRIPT must require [01, 02, 03]
    audio_deliv = resolve_required_deliverables(CanonicalModality.AUDIO)
    assert audio_deliv == ["01", "02", "03"], f"Audio must yield [01, 02, 03], got {audio_deliv}"

    transcript_deliv = resolve_required_deliverables(CanonicalModality.MEETING_TRANSCRIPT)
    assert transcript_deliv == ["01", "02", "03"], f"Transcript must yield [01, 02, 03], got {transcript_deliv}"

    # Neither has not-applicable deliverables
    assert get_not_applicable_deliverables(CanonicalModality.AUDIO) == []
    assert get_not_applicable_deliverables(CanonicalModality.MEETING_TRANSCRIPT) == []

    # Selective meeting regeneration of output 3 only remains MEETING modality, cannot claim complete
    meeting3_only = resolve_required_deliverables(
        CanonicalModality.MEETING_TRANSCRIPT,
        operation_kind=OperationKind.SELECTIVE_MEETING_REGENERATION,
        selective_outputs=["03"],
    )
    assert meeting3_only == ["03"]


def test_m03_contract():
    """Case M03: Meeting notes require 02, 03 and explicit 01-not-applicable; never fabricate speech."""
    notes_deliv = resolve_required_deliverables(CanonicalModality.MEETING_NOTES)
    assert notes_deliv == ["02", "03"], f"Notes must yield [02, 03], got {notes_deliv}"

    not_app = get_not_applicable_deliverables(CanonicalModality.MEETING_NOTES)
    assert "01" in not_app, f"Notes must declare 01 as NOT_APPLICABLE, got {not_app}"

    # Invariant validation fails if output 01 is requested on notes
    valid, conflicts = validate_operation_invariants(
        CanonicalModality.MEETING_NOTES,
        flags={},
        requested_outputs=["01"],
    )
    assert not valid
    assert any("outside declared deliverables" in c for c in conflicts)


def test_m04_contract():
    """Case M04: Documents require 03 only; no 01/02 generation or transcript gates."""
    doc_deliv = resolve_required_deliverables(CanonicalModality.DOCUMENT)
    assert doc_deliv == ["03"], f"Documents must yield [03] only, got {doc_deliv}"

    not_app = get_not_applicable_deliverables(CanonicalModality.DOCUMENT)
    assert "01" in not_app and "02" in not_app, f"Documents must declare 01 and 02 NOT_APPLICABLE, got {not_app}"

    # Document mode cannot borrow transcript flags
    conflicts = check_conflicting_flags(
        CanonicalModality.DOCUMENT,
        {"cloud_transcription": True},
    )
    assert len(conflicts) > 0


def test_t01_contract(tmp_path: Path):
    """Case T01: Final receipts reconcile per-mode timing and per-call tokens without double counting or false zero cost."""
    # 1. Test token normalization: prevent double-counting when reasoning tokens are subsumed
    raw_subsumed = {
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "cached_prompt_tokens": 200,
        "reasoning_tokens": 150,
        "completion_tokens_details": {"reasoning_tokens": 150},
    }
    normalized = normalize_provider_counters(raw_subsumed, model="gpt-6-sol")
    assert normalized["normalized"]["total_tokens"] == 1500  # 1000 + 500 (NOT 1000 + 500 + 150)
    assert normalized["integrity"]["double_counting_prevented"] is True
    assert normalized["integrity"]["subsumed_reasoning"] is True

    # 2. Test pricing uncertainty: unverified model pricing is marked UNRECONCILED, never false $0.0
    uncertain_pricing = assess_pricing_uncertainty("unknown-custom-model-v99", calculated_cost_usd=0.0)
    assert uncertain_pricing["cost_status"] == "UNRECONCILED"
    assert uncertain_pricing["price_uncertainty"] is True
    assert uncertain_pricing["cost_usd"] is None

    # 3. Test host separation: pipeline duration is segregated from outer host orchestration
    pipeline_exec = {
        "duration_ms": 216650.233,
        "monotonic_duration_s": 216.65,
        "stage_inclusive_timings": {"L2": {"duration_ms": 107066.83}, "EVALS": {"duration_ms": 108448.308}},
    }
    separated = separate_host_from_pipeline(pipeline_exec, host_metadata={"host_turns": 35})
    assert separated["pipeline_execution_scope"]["duration_ms"] == 216650.233
    assert separated["host_orchestration_scope"]["host_turns"] == 35
    assert separated["host_orchestration_scope"]["reconstructed"] is False

    # 4. Test receipt copy consistency
    canon_file = tmp_path / "canonical_receipt.json"
    copy_file = tmp_path / "copy_receipt.json"
    data = {
        "terminal_outcome": "COMPLETE",
        "monotonic_duration_s": 216.65,
        "total_tokens": 1500,
    }
    canon_file.write_text(json.dumps(data), encoding="utf-8")
    copy_file.write_text(json.dumps(data), encoding="utf-8")

    ok, errors = verify_receipt_copy_consistency(canon_file, [copy_file])
    assert ok is True
    assert errors == []

    # Corrupt copy outcome and assert failure detection
    copy_corrupt = tmp_path / "corrupt_receipt.json"
    copy_corrupt.write_text(json.dumps({"terminal_outcome": "FAILED", "monotonic_duration_s": 216.65}), encoding="utf-8")
    ok_corrupt, errors_corrupt = verify_receipt_copy_consistency(canon_file, [copy_corrupt])
    assert ok_corrupt is False
    assert len(errors_corrupt) > 0


def test_t02_contract(tmp_path: Path):
    """Case T02: Every shared-file change resolves a nonempty acceptance set for document, transcript, notes, live."""
    from tools.run_ingestion_contract_gate import load_wave_acceptance, validate_test_manifest

    repo_root = Path(__file__).resolve().parent.parent.parent
    wave_map_path = repo_root / "reports" / "ingestion_hardening" / "wave_acceptance_map.json"
    manifest_path = repo_root / "tests" / "manifests" / "ingestion_modality_hardening.json"

    # 1. Manifest validates
    ok_man, errors_man, modules = validate_test_manifest(manifest_path)
    assert ok_man is True, f"Manifest validation failed: {errors_man}"
    assert len(modules) >= 10, f"Expected at least 10 enrolled modules, got {len(modules)}"

    # 2. Wave 1 acceptance resolves non-empty test nodes
    required_modes = {"document", "transcript", "notes", "live"}
    ok_wave, msg_wave, wave_entry, exact_nodes = load_wave_acceptance(1, wave_map_path, required_modes)
    assert ok_wave is True, f"Wave 1 acceptance failed: {msg_wave}"
    assert len(exact_nodes) >= 15, f"Wave 1 must have at least 15 exact nodes, got {len(exact_nodes)}"

    # 3. Must contain both E2E and adversarial tests
    assert any("e2e" in node.lower() for node in exact_nodes), "Scope must include E2E test"
    assert any("adversarial" in node.lower() for node in exact_nodes), "Scope must include adversarial test"

    # 4. Zero collection or missing mode fails closed
    dummy_map = tmp_path / "dummy_wave_map.json"
    dummy_map.write_text(
        json.dumps({
            "waves": {
                "1": {
                    "modes_required": ["document", "transcript"],  # missing notes and live
                    "exact_test_nodes": ["tests/test_foo.py::test_bar"],
                }
            }
        }),
        encoding="utf-8",
    )
    ok_missing, msg_missing, _, _ = load_wave_acceptance(1, dummy_map, required_modes)
    assert ok_missing is False
    assert "missing required modes" in msg_missing
