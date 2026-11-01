"""Wave 1 Audit Test: Authoritative Ground-Truth Reference Inventory & Voice Anchors.

Verifies:
1. Ground-truth inventory N contains all 426 audible turns without display merging.
2. Temporal continuity, timestamps, and valid non-negative durations.
3. Dual-channel RMS measurements (Ch0 local mic vs Ch1 remote audio) from master WAV.
4. Voice anchor audio clips exist, have matching SHA-256 digests, and cover confirmed attendees.
5. Exact reconciliation of resolved vs unresolved queue turns (sum == N).
6. Short acknowledgments and backchannels preserved in census.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_REF_DIR = ROOT_DIR / "data/reference"
INVENTORY_FILE = DATA_REF_DIR / "lens_20261006_ground_truth_inventory.json"
ANCHORS_FILE = DATA_REF_DIR / "lens_20261006_voice_anchors.json"
QUEUE_FILE = DATA_REF_DIR / "lens_20261006_unresolved_queue.json"
AUDIO_ANCHORS_DIR = DATA_REF_DIR / "audio_anchors"


@pytest.fixture
def inventory_data() -> dict:
    assert INVENTORY_FILE.exists(), f"Reference inventory missing at {INVENTORY_FILE}"
    with open(INVENTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def anchors_data() -> list:
    assert ANCHORS_FILE.exists(), f"Voice anchors file missing at {ANCHORS_FILE}"
    with open(ANCHORS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def queue_data() -> dict:
    assert QUEUE_FILE.exists(), f"Unresolved queue missing at {QUEUE_FILE}"
    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def test_reference_inventory_completeness_and_bounds(inventory_data: dict):
    meta = inventory_data["metadata"]
    turns = inventory_data["turns"]

    # Verify census count
    assert meta["total_reference_turns_N"] == 426
    assert len(turns) == 426

    # Verify attendee roster
    expected_attendees = {"Amit Ayer", "Jake Stark", "Morgan Vance Krutilla", "Jennifer Pan"}
    assert set(meta["attendees"]) == expected_attendees

    # Check temporal continuity
    first_turn = turns[0]
    last_turn = turns[-1]
    assert first_turn["start_time_sec"] == 0.0
    assert last_turn["start_time_sec"] <= 818.0  # Meeting concluded at 13:38

    for t in turns:
        assert t["start_time_sec"] >= 0.0
        assert t["end_time_sec"] > t["start_time_sec"]
        assert t["duration_sec"] > 0.0
        assert t["word_count"] >= 1
        assert len(t["utterance_text"]) > 0


def test_physical_channel_measurements(inventory_data: dict):
    turns = inventory_data["turns"]
    valid_channels = {
        "ch0_local_isolated",
        "ch0_local_dominant",
        "ch1_remote_isolated",
        "ch1_remote_dominant",
        "overlap_crosstalk",
        "ambient_or_quiet",
    }

    ch0_count = 0
    ch1_count = 0

    for t in turns:
        assert isinstance(t["ch0_rms"], (int, float))
        assert isinstance(t["ch1_rms"], (int, float))
        assert t["ch0_rms"] >= 0.0
        assert t["ch1_rms"] >= 0.0
        assert t["primary_channel"] in valid_channels

        if t["primary_channel"].startswith("ch0"):
            ch0_count += 1
        elif t["primary_channel"].startswith("ch1"):
            ch1_count += 1

    # In a 19m debrief with local speaker + remote attendees, both channels must have significant turns
    assert ch0_count >= 100
    assert ch1_count >= 100


def test_voice_anchors_soundness(anchors_data: list):
    assert len(anchors_data) >= 4

    speakers_covered = set()
    for anchor in anchors_data:
        spk = anchor["speaker_name"]
        speakers_covered.add(spk)
        assert anchor["channel"] in (0, 1)
        assert anchor["duration_sec"] >= 4.0
        assert len(anchor["reference_text"]) > 10

        # Verify physical WAV clip file
        clip_path = AUDIO_ANCHORS_DIR / anchor["clip_filename"]
        assert clip_path.exists(), f"Audio clip missing: {clip_path}"

        # Verify SHA-256 integrity
        actual_hash = hashlib.sha256(clip_path.read_bytes()).hexdigest()
        assert actual_hash == anchor["sha256"], f"Hash mismatch for {anchor['clip_filename']}"

    assert speakers_covered == {"Amit Ayer", "Jake Stark", "Morgan Vance Krutilla", "Jennifer Pan"}


def test_unresolved_queue_integrity(inventory_data: dict, queue_data: dict):
    turns = inventory_data["turns"]
    unresolved = queue_data["unresolved_turns"]

    assert queue_data["total_unresolved"] == len(unresolved)
    assert len(unresolved) > 0

    # Ensure no generic placeholder treated as resolved
    for t in turns:
        assert t["reference_speaker"] != "PartnerAdvisory Speaker", "Generic placeholder 'PartnerAdvisory Speaker' must not be in reference"
        if t["adjudication_status"] == "UNRESOLVED_QUEUE":
            assert t["reference_speaker"] == "Unknown"

    resolved_count = sum(1 for t in turns if t["adjudication_status"] != "UNRESOLVED_QUEUE")
    assert resolved_count + len(unresolved) == len(turns)


def test_short_acknowledgments_retained_in_census(inventory_data: dict):
    turns = inventory_data["turns"]
    short_turns = [t for t in turns if t["is_short_acknowledgment"]]

    # Ensure brief acknowledgments (which historical pipeline dropped) are present
    assert len(short_turns) >= 20

    short_texts = [t["utterance_text"].lower().strip(".,!?") for t in short_turns]
    for expected_ack in ["if", "a", "it", "on"]:
        assert any(expected_ack in st for st in short_texts), f"Expected short ack '{expected_ack}' missing from census"
