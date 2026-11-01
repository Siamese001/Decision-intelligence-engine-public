"""End-to-End qualification verification & deterministic release negative controls for PRF."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import jsonschema
import pytest

from dual_pipeline.transcript_qa_core.frozen_gates import check_frozen_dependency_validity
from dual_pipeline.transcript_qa_core.gate1 import run_qa_gate_1
from tools.qualify_prf_core.evidence import revalidate_manifest, sha256_file

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "contracts/dual_pipeline/speaker_quality_receipt.schema.json"
W5_RECEIPT_PATH = REPO_ROOT / "reports/prf_diarization_wave5.json"
SANDBOX_ROOT = Path("/private/tmp/prf_diarization_qualification")
SANDBOX_PRF_DIR = SANDBOX_ROOT / "output/nea/10-06-2026/Prf_Scope_Sow_Clement_PartnerAdvisory"
PROD_PRF_DIR = REPO_ROOT / "output/nea/10-06-2026/Prf_Scope_Sow_Clement_PartnerAdvisory"
EVIDENCE_MANIFEST = REPO_ROOT / "reports/prf_rca_evidence_manifest.json"


def test_wave5_receipt_conformance_and_metrics() -> None:
    """Verifies that the Wave 5 receipt exists, validates against schema, and passes all gates."""
    assert W5_RECEIPT_PATH.exists(), f"Wave 5 receipt missing: {W5_RECEIPT_PATH}"
    with open(W5_RECEIPT_PATH, "r", encoding="utf-8") as f:
        receipt = json.load(f)

    with open(SCHEMA_PATH, "r", encoding="utf-8") as sf:
        schema = json.load(sf)
    jsonschema.validate(instance=receipt, schema=schema)

    assert receipt["wave"] == "Wave 5"
    assert receipt["terminal_outcome"] == "PASS"

    rel = receipt["denominators"]["released_text_metrics"]
    assert rel["named_word_coverage_pct"] >= 95.0
    assert rel["named_turn_coverage_pct"] >= 80.0
    assert rel["one_word_turns"] <= 60  # > 60% reduction from 155
    assert rel["generic_client_turns"] <= 35
    assert rel["released_coverage_gate_status"] == "PASS"

    indep = receipt["denominators"]["independent_reference_metrics"]
    assert indep["measurement_status"] == "MEASURED"
    assert indep["independent_reference_named_coverage_pct"] >= 95.0
    assert indep["named_word_attribution_accuracy_pct"] >= 98.0
    assert indep["true_identified_reference_word_share_pct"] >= 95.0

    assert receipt["unknown_status"]["unmeasured_metrics"] == []


def test_sandbox_output_01_quality_and_gate1() -> None:
    """Verifies that sandbox Output 01 exists and passes Gate 1 deterministic QA."""
    f1_path = SANDBOX_PRF_DIR / "01_Prf_Scope_Sow_Clement_PartnerAdvisory_10-06-2026_0341PM0408PMET_hitl_resolved_transcript.md"
    meta_path = SANDBOX_PRF_DIR / "metadata.json"
    assert f1_path.exists(), f"Sandbox Output 01 missing: {f1_path}"
    assert meta_path.exists(), f"Sandbox metadata missing: {meta_path}"

    f1_text = f1_path.read_text(encoding="utf-8")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    g1_res = run_qa_gate_1(f1_text, source_text=f1_text, metadata=meta)
    assert g1_res.get("passed") is True, f"Gate 1 failed on sandbox Output 01: {g1_res.get('failures')}"

    cov = g1_res.get("coverage_metrics", {})
    assert cov["named_word_coverage_pct"] >= 95.0
    assert cov["named_turn_coverage_pct"] >= 80.0
    assert cov["one_word_turns"] <= 60

    # Verify Participants header
    participants_section = f1_text.split("---")[0]
    assert "Noemi Monnerville (Web User Experience Designer)" in participants_section
    assert "- **Contractor:**" not in participants_section
    assert "Venkat (Solutions Architect) [Candidate Identity; surname pending operator confirmation]" in participants_section


def test_sandbox_output_02_identity_readback() -> None:
    """Verifies independent readback on sandbox Output 02: PartnerAdvisory Noemi and candidate-only Venkat."""
    f2_path = SANDBOX_PRF_DIR / "02_Prf_Scope_Sow_Clement_PartnerAdvisory_10-06-2026_0341PM0408PMET_executive_recap.md"
    assert f2_path.exists(), f"Sandbox Output 02 missing: {f2_path}"

    f2_text = f2_path.read_text(encoding="utf-8")
    assert "Contractor:" not in f2_text, "Found forbidden 'Contractor:' section in Output 02!"
    assert "Noemi Monnerville (Web User Experience Designer)" in f2_text

    # Verify Venkat is candidate-only without surname guessing
    assert "Venkat (Solutions Architect) [Candidate Identity;" in f2_text
    assert "Krishnakumar Venkataraman" not in f2_text
    assert "Venkataraman" not in f2_text


def test_sandbox_output_03_and_deployment_proposal() -> None:
    """Verifies sandbox Output 03, deployment proposal, and rollback manifest."""
    f3_path = SANDBOX_PRF_DIR / "03_Prf_Scope_Sow_Clement_PartnerAdvisory_10-06-2026_0341PM0408PMET_canonical_signal_projection.json"
    prop_path = REPO_ROOT / "reports/prf_deployment_proposal.json"
    roll_path = REPO_ROOT / "reports/prf_rollback_manifest.json"
    rev_path = REPO_ROOT / "reports/prf_before_after_review_package.md"

    assert f3_path.exists(), f"Sandbox Output 03 missing: {f3_path}"
    assert prop_path.exists(), f"Deployment proposal missing: {prop_path}"
    assert roll_path.exists(), f"Rollback manifest missing: {roll_path}"
    assert rev_path.exists(), f"Review package missing: {rev_path}"

    f3_data = json.loads(f3_path.read_text(encoding="utf-8"))
    assert "evidence_index" in f3_data

    prop_data = json.loads(prop_path.read_text(encoding="utf-8"))
    assert prop_data["status"] == "PROPOSED_PENDING_OPERATOR_APPROVAL"
    assert prop_data["meeting_id"] == "00_10-06-2026_Prf_Scope_Sow_Clement_Venkat_master_154116"

    roll_data = json.loads(roll_path.read_text(encoding="utf-8"))
    assert "original_production_hashes" in roll_data
    assert len(roll_data["original_production_hashes"]) >= 8


def test_production_purity_invariant() -> None:
    """Verifies INV-INGESTION-ENGINEERING-ISOLATION: production files were NOT mutated."""
    assert EVIDENCE_MANIFEST.exists(), f"Evidence manifest missing: {EVIDENCE_MANIFEST}"
    source_hashes, manifest_errors, _ = revalidate_manifest(EVIDENCE_MANIFEST)
    assert not manifest_errors, f"Production purity violated! Manifest errors: {manifest_errors}"

    orig_f1_path = PROD_PRF_DIR / "01_Prf_Scope_Sow_Clement_PartnerAdvisory_10-06-2026_0341PM0408PMET_hitl_resolved_transcript.md"
    orig_f1_hash = sha256_file(orig_f1_path)
    assert orig_f1_hash == "28f96b4aa44720267de2f29180340d096501042a1e8160a89fca3a9b08e5783e"


def test_deterministic_negative_controls() -> None:
    """Verifies that bad/corrupted transcripts fail Gate 1 and trigger dependency invalidation."""
    f1_path = SANDBOX_PRF_DIR / "01_Prf_Scope_Sow_Clement_PartnerAdvisory_10-06-2026_0341PM0408PMET_hitl_resolved_transcript.md"
    meta_path = SANDBOX_PRF_DIR / "metadata.json"
    f1_text = f1_path.read_text(encoding="utf-8")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    # Negative control 1: Corrupt coverage by replacing Clement with [Client Speaker]
    corrupted_f1 = f1_text.replace("**Clement:**", "**[Client Speaker]:**")
    g1_corrupted = run_qa_gate_1(corrupted_f1, source_text=corrupted_f1, metadata=meta)
    assert g1_corrupted.get("passed") is False
    assert g1_corrupted["coverage_metrics"]["named_word_coverage_pct"] < 95.0

    # Negative control 2: Stale Contractor affiliation in Output 02 triggers invalidation
    stale_f2 = "Contractor:\n- Noemi Monnerville (Web User Experience Designer)\n"
    valid, reason = check_frozen_dependency_validity(
        "output_2",
        stale_f2,
        None,
        {"identity_corrections": [{"person": "Noemi Monnerville", "company": "PartnerAdvisory"}]},
    )
    assert valid is False
    assert "Contractor" in reason
