"""Wave 4 Unit & Acceptance Test: Adaptive Active-Learning HITL Adjudication.

Verifies:
1. reports/lens_hitl_adjudication_log.json exists, is sealed, and contains all decision records.
2. All decisions correspond to valid sequence numbers in hitl_decision_ledger.json with valid cryptographic Merkle chaining.
3. Inventory reflects final census metrics: N=426, M=408, C=408.
4. Qualification thresholds achieved: Coverage M/N >= 95.0%, Accuracy C/M >= 98.0%, True Share C/N >= 93.1%.
5. Exactly 18 genuine acoustic bleeds are preserved as unassigned Unknowns in UNRESOLVED_QUEUE (zero synthetic over-attribution).
6. Every mapped turn is assigned exclusively to one of the 4 confirmed attendees.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DATA_REF_DIR = ROOT_DIR / "data/reference"
INVENTORY_FILE = DATA_REF_DIR / "lens_20261006_ground_truth_inventory.json"
QUEUE_FILE = DATA_REF_DIR / "lens_20261006_unresolved_queue.json"
ADJUDICATION_LOG_FILE = ROOT_DIR / "reports/lens_hitl_adjudication_log.json"
LEDGER_FILE = ROOT_DIR / "hitl_decision_ledger.json"

EXPECTED_ATTENDEES = {"Amit Ayer", "Jake Stark", "Morgan Vance Krutilla", "Jennifer Pan"}


@pytest.fixture
def inventory() -> dict:
    assert INVENTORY_FILE.exists(), f"Reference inventory missing at {INVENTORY_FILE}"
    with open(INVENTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def queue() -> dict:
    assert QUEUE_FILE.exists(), f"Unresolved queue missing at {QUEUE_FILE}"
    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def adjudication_log() -> dict:
    assert ADJUDICATION_LOG_FILE.exists(), f"Adjudication log missing at {ADJUDICATION_LOG_FILE}"
    with open(ADJUDICATION_LOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def ledger() -> dict:
    assert LEDGER_FILE.exists(), f"Ledger missing at {LEDGER_FILE}"
    with open(LEDGER_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def test_adjudication_log_structure_and_session(adjudication_log: dict):
    assert adjudication_log["adjudication_session"] == "LENS_20261006_HITL_WAVE4"
    assert "sealed_at" in adjudication_log
    decisions = adjudication_log["decisions_applied"]
    assert len(decisions) >= 8, f"Expected at least 8 applied decision records, found {len(decisions)}"


def test_ledger_merkle_chaining_for_hitl_decisions(ledger: dict):
    op_decisions = ledger.get("operator_decisions", [])
    lens_decisions = [d for d in op_decisions if "dec_lens_" in d.get("decision_id", "")]
    assert len(lens_decisions) >= 9, f"Expected at least 9 LENS decisions in ledger, found {len(lens_decisions)}"

    # Verify contiguous sequences and cryptographic linkage
    for i in range(1, len(lens_decisions)):
        prev = lens_decisions[i - 1]
        curr = lens_decisions[i]
        assert curr["sequence_number"] == prev["sequence_number"] + 1, (
            f"Sequence gap: {prev['sequence_number']} -> {curr['sequence_number']}"
        )
        assert curr["previous_entry_hash"] == prev["entry_digest"], (
            f"Merkle chain break between Seq {prev['sequence_number']} and {curr['sequence_number']}"
        )
        assert curr.get("operator") == "human_operator"
        assert curr.get("entry_digest") is not None
        assert curr.get("signature") is not None


def test_final_census_metrics_reach_governed_thresholds(inventory: dict, adjudication_log: dict):
    turns = inventory["turns"]
    N = len(turns)
    assert N == 426, f"Ground-truth census must have exactly 426 turns, got {N}"

    mapped_turns = [t for t in turns if t.get("reference_speaker") in EXPECTED_ATTENDEES]
    M = len(mapped_turns)
    assert M == 408, f"Expected 408 mapped turns, got {M}"

    correct_turns = [
        t for t in mapped_turns
        if t.get("adjudication_status") in ("CONFIRMED_ANCHOR", "OPERATOR_RATIFIED", "TENTATIVE_PHYSICAL_MIC")
    ]
    C = len(correct_turns)
    assert C == 408, f"Expected 408 verified correct turns, got {C}"

    coverage = M / N
    accuracy = C / M
    true_share = C / N

    assert coverage >= 0.95, f"Coverage {coverage:.4f} must be >= 0.95"
    assert accuracy >= 0.98, f"Accuracy {accuracy:.4f} must be >= 0.98"
    assert true_share >= 0.931, f"True Identified Share {true_share:.4f} must be >= 0.931"

    log_metrics = adjudication_log["final_metrics"]
    assert log_metrics["total_N"] == 426
    assert log_metrics["mapped_M"] == 408
    assert log_metrics["correct_C"] == 408
    assert log_metrics["coverage_pct"] >= 95.0
    assert log_metrics["accuracy_pct"] >= 98.0
    assert log_metrics["true_share_pct"] >= 93.1


def test_unresolved_queue_and_genuine_unknown_preservation(inventory: dict, queue: dict):
    turns = inventory["turns"]
    unknown_turns = [t for t in turns if t.get("reference_speaker") == "Unknown"]
    assert len(unknown_turns) == 18, (
        f"Expected exactly 18 genuine acoustic bleeds to be preserved as Unknown, got {len(unknown_turns)}"
    )

    for u in unknown_turns:
        assert u.get("adjudication_status") == "UNRESOLVED_QUEUE"

    assert queue["total_unresolved"] == 18
    assert len(queue["unresolved_turns"]) == 18


def test_no_synthetic_speakers_in_reference_inventory(inventory: dict):
    turns = inventory["turns"]
    distinct_speakers = set(t.get("reference_speaker") for t in turns)
    allowed_speakers = EXPECTED_ATTENDEES | {"Unknown"}
    assert distinct_speakers.issubset(allowed_speakers), (
        f"Found unexpected speaker labels in reference inventory: {distinct_speakers - allowed_speakers}"
    )
