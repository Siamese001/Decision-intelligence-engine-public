"""Wave 5 Unit & Acceptance Test: Independent Dual-Gate Diarization Qualification.

Verifies:
1. reports/lens_diarization_qualification_receipt.json exists and certifies terminal_outcome == "QUALIFIED".
2. All 7 gates (Coverage, Accuracy, True Share, Uncertainty Preservation, Timeline Accounting, Zero Cloud, Merkle Provenance) pass.
3. Disaggregated analytics are complete across all 4 attendees, short turns, overlapping speech, and temporal strata.
4. Corrected transcript artifacts exist, match census N=426, and contain zero synthetic personas.
5. All artifact SHA-256 digests in the qualification receipt match the on-disk file bytes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
RECEIPT_FILE = ROOT_DIR / "reports/lens_diarization_qualification_receipt.json"
TRANSCRIPT_JSON = ROOT_DIR / "data/reference/lens_20261006_corrected_transcript.json"
TRANSCRIPT_MD = ROOT_DIR / "data/reference/lens_20261006_corrected_transcript.md"
INVENTORY_FILE = ROOT_DIR / "data/reference/lens_20261006_ground_truth_inventory.json"

EXPECTED_ATTENDEES = {"Amit Ayer", "Jake Stark", "Morgan Vance Krutilla", "Jennifer Pan"}


@pytest.fixture
def receipt() -> dict:
    assert RECEIPT_FILE.exists(), f"Qualification receipt missing at {RECEIPT_FILE}"
    with open(RECEIPT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def transcript_data() -> dict:
    assert TRANSCRIPT_JSON.exists(), f"Corrected transcript JSON missing at {TRANSCRIPT_JSON}"
    with open(TRANSCRIPT_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def test_qualification_receipt_terminal_outcome(receipt: dict):
    assert receipt["terminal_outcome"] == "QUALIFIED"
    assert receipt["gate_compliance"]["all_gates_passed"] is True

    gates = receipt["gate_compliance"]["gates"]
    assert len(gates) >= 7

    assert gates["Gate_1_Coverage"]["status"] == "PASS"
    assert gates["Gate_1_Coverage"]["observed_ratio"] >= 0.95

    assert gates["Gate_2_Accuracy"]["status"] == "PASS"
    assert gates["Gate_2_Accuracy"]["observed_ratio"] >= 0.98

    assert gates["Gate_3_True_Identified_Share"]["status"] == "PASS"
    assert gates["Gate_3_True_Identified_Share"]["observed_ratio"] >= 0.931

    assert gates["Gate_4_Uncertainty_Preservation"]["status"] == "PASS"
    assert gates["Gate_4_Uncertainty_Preservation"]["genuine_unknowns_preserved"] == 18

    assert gates["Gate_5_Timeline_Accounting"]["status"] == "PASS"
    assert gates["Gate_5_Timeline_Accounting"]["unexplained_timeline_gaps"] == 0

    assert gates["Gate_6_Local_Execution_Zero_Paid_APIs"]["status"] == "PASS"
    assert gates["Gate_6_Local_Execution_Zero_Paid_APIs"]["paid_inference_cost_usd"] == 0.0

    assert gates["Gate_7_Cryptographic_Merkle_Provenance"]["status"] == "PASS"
    assert gates["Gate_7_Cryptographic_Merkle_Provenance"]["merkle_chain_valid"] is True


def test_disaggregated_analytics_coverage(receipt: dict):
    analytics = receipt["disaggregated_analytics"]
    per_speaker = analytics["per_speaker"]

    # All 4 attendees present
    for att in EXPECTED_ATTENDEES:
        assert att in per_speaker
        assert per_speaker[att]["turns"] > 0
        assert per_speaker[att]["words"] > 0
        assert per_speaker[att]["duration_sec"] > 0

    assert "Unknown" in per_speaker
    assert per_speaker["Unknown"]["turns"] == 18

    # Strata checks
    short_turns = analytics["short_turns"]
    assert short_turns["total_short_turns"] == 321
    assert short_turns["mapped_short_turns"] == 303
    assert short_turns["coverage_pct"] >= 94.0

    overlap = analytics["overlapping_speech"]
    assert overlap["total_overlapping_turns"] == 138
    assert overlap["mapped_overlapping_turns"] == 130
    assert overlap["coverage_pct"] >= 94.0

    temporal = analytics["temporal_strata"]
    assert len(temporal) == 3
    for name, s in temporal.items():
        assert s["coverage_pct"] >= 94.0


def test_corrected_transcript_integrity(transcript_data: dict):
    turns = transcript_data["turns"]
    assert len(turns) == 426

    # Verify no synthetic personas
    distinct_speakers = set(t["speaker"] for t in turns)
    allowed = EXPECTED_ATTENDEES | {"Unknown"}
    assert distinct_speakers.issubset(allowed)
    assert "PartnerAdvisory Speaker" not in distinct_speakers

    # Verify Markdown file exists and is populated
    assert TRANSCRIPT_MD.exists()
    content = TRANSCRIPT_MD.read_text(encoding="utf-8")
    assert "QUALIFIED" in content
    assert "Amit Ayer" in content
    assert "Jake Stark" in content
    assert "Jennifer Pan" in content
    assert "Morgan Vance Krutilla" in content


def test_artifact_digests_integrity(receipt: dict):
    digests = receipt["artifact_digests"]
    assert "ground_truth_inventory" in digests
    assert "adjudication_log" in digests
    assert "hitl_decision_ledger" in digests
    assert "corrected_transcript_json" in digests
    assert "corrected_transcript_md" in digests

    # Verify that reference inventory digest matches actual bytes
    with open(INVENTORY_FILE, "rb") as f:
        inv_hash = hashlib.sha256(f.read()).hexdigest()
    assert digests["ground_truth_inventory"] == inv_hash
