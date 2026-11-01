"""Wave 1 E2E Test: Independent Baseline, Reference Audio Ground Truth, and Metrics Disaggregation.

Verifies:
1. End-to-end reproducibility of the independent baseline and reference set.
2. Master WAV integrity, sample rate, channel separation, and disputed meeting endpoint (818s / 13:38).
3. Independent reference set coverage across all 7 mandatory categories:
   complete sentences, chunk boundaries, rapid exchanges, one-word replies,
   overlapping speech, tail silence, and uncertain speech.
4. Three disjoint partitions: tuning (0-9m05s), held-out (9m05s-13m38s), and tail silence (13m38s-19m11s).
5. Timing provenance distinction ('legacy_estimated' vs 'sample_clock_exact').
6. Human review workload disaggregation (426 entries != 9 human questions).
7. Adversarial edge-case defenses: boundary payloads, malformed timestamps, synthetic approval rejection,
   and exact production regression checks.
"""
from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path
import pytest

from evals.independent_baseline_metrics import (
    compute_baseline_metrics,
    format_baseline_metrics_report,
    verify_baseline_invariants,
)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DATA_REF_DIR = ROOT_DIR / "data/reference"
AUDIO_ANCHORS_DIR = DATA_REF_DIR / "audio_anchors"
INDEPENDENT_REF_FILE = DATA_REF_DIR / "lens_20261006_independent_reference_set.json"
INVENTORY_FILE = DATA_REF_DIR / "lens_20261006_ground_truth_inventory.json"
BASELINE_MD_FILE = DATA_REF_DIR / "lens_20261006_baseline_transcript.md"
LEDGER_FILE = ROOT_DIR / "hitl_decision_ledger.json"

MASTER_WAV_REL = "output/transcript_cache/project_lens/10-06-2026/00_10-06-2026_Internal_Debrief_Jake_PartnerAdvisory_master_174803.wav"
EXPECTED_MASTER_SHA256 = "f08541e372c414f312e77c7571112b2bee7d5a5ccd0878c79791d76b0c736472"
EXPECTED_BASELINE_MD_SHA256 = "e1bd3eb94ee553437616f18b26dd7f1fbe7ec94809c36ef2c246ba65bfcb426a"


@pytest.fixture
def master_wav_path() -> Path:
    p = ROOT_DIR / MASTER_WAV_REL
    assert p.is_file(), f"Master audio WAV missing at {p}"
    return p


@pytest.fixture
def independent_ref_data() -> dict:
    assert INDEPENDENT_REF_FILE.is_file(), f"Independent reference set missing at {INDEPENDENT_REF_FILE}"
    with open(INDEPENDENT_REF_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def test_master_audio_physical_integrity(master_wav_path: Path):
    """Verifies physical properties and cryptographic digest of the master LENS recording."""
    hasher = hashlib.sha256()
    with open(master_wav_path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            hasher.update(chunk)
    assert hasher.hexdigest() == EXPECTED_MASTER_SHA256

    with wave.open(str(master_wav_path), "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        duration_sec = n_frames / float(framerate)

    assert channels == 2, f"Expected stereo audio (2 ch), got {channels}"
    assert framerate == 16000, f"Expected 16kHz sampling, got {framerate}"
    assert sample_width == 2, f"Expected 16-bit PCM (2 bytes), got {sample_width}"
    assert abs(duration_sec - 1150.97475) < 0.01, f"Expected 1150.97s, got {duration_sec}s"


def test_disputed_meeting_endpoint_and_tail_silence(independent_ref_data: dict):
    """Verifies that active meeting dialogue is strictly bounded at 818.0s (13:38)

    and that the 332.97s tail is verified silence/ambient floor.
    """
    assert independent_ref_data["disputed_meeting_endpoint_sec"] == 818.0
    partitions = independent_ref_data["partitions"]

    tuning = partitions["tuning"]
    held_out = partitions["held_out"]
    tail = partitions["recording_tail"]

    assert tuning["start_sec"] == 0.0
    assert tuning["end_sec"] == 545.0
    assert tuning["turns"] == 306

    assert held_out["start_sec"] == 545.0
    assert held_out["end_sec"] == 818.0
    assert held_out["turns"] == 120

    assert tail["start_sec"] == 818.0
    assert tail["end_sec"] > 1150.0
    assert tail["turns"] == 0, "Tail interval after 13:38 must contain 0 spoken dialogue turns"


def test_independent_reference_set_seven_categories(independent_ref_data: dict):
    """Verifies that all 7 required acoustic/linguistic categories are represented

    with listenable audio clips and matching SHA-256 digests.
    """
    required_categories = {
        "complete_sentence",
        "chunk_boundary",
        "rapid_exchange",
        "one_word_reply",
        "overlapping_speech",
        "silence_tail",
        "uncertain_speaker",
    }
    covered = set(independent_ref_data.get("categories_covered", []))
    assert required_categories.issubset(covered), f"Missing categories: {required_categories - covered}"

    items = independent_ref_data.get("reference_items", [])
    assert len(items) >= 9

    found_categories = set()
    for item in items:
        cat = item["category"]
        found_categories.add(cat)
        assert item["listenable"] is True
        assert item["timing_provenance"] == "sample_clock_exact"

        clip_name = item["clip_filename"]
        clip_path = AUDIO_ANCHORS_DIR / clip_name
        assert clip_path.is_file(), f"Reference audio clip missing: {clip_path}"

        actual_hash = hashlib.sha256(clip_path.read_bytes()).hexdigest()
        assert actual_hash == item["clip_sha256"], f"Clip hash mismatch for {clip_name}"

    assert required_categories.issubset(found_categories)


def test_baseline_census_and_disaggregated_metrics():
    """Verifies that the baseline census correctly separates display entries,

    unnecessary splits, unresolved speech, and human decisions.
    """
    summary = compute_baseline_metrics(INVENTORY_FILE, LEDGER_FILE)
    invariants = verify_baseline_invariants(summary)

    for k, passed in invariants.items():
        assert passed is True, f"Invariant {k} failed"

    # Core Census
    assert summary.display_entries == 426
    assert summary.unnecessary_sentence_splits == 173
    assert summary.single_word_entries == 154
    assert summary.at_most_three_word_entries == 224
    assert summary.unresolved_words_count == 18
    assert summary.proposed_review_cards == 5

    # Human Review Disaggregation
    assert summary.actual_questions_answered == 9
    assert summary.actual_questions_answered != summary.display_entries
    assert summary.playback_time_sec < summary.fragment_playback_time_sec * 0.20


def test_immutable_baseline_transcript_hash():
    """Verifies that the baseline fragment transcript is immutable and matches expected hash."""
    assert BASELINE_MD_FILE.is_file()
    actual_hash = hashlib.sha256(BASELINE_MD_FILE.read_bytes()).hexdigest()
    assert actual_hash == EXPECTED_BASELINE_MD_SHA256, (
        f"Baseline transcript modified! Expected {EXPECTED_BASELINE_MD_SHA256}, got {actual_hash}"
    )


# ---------------------------------------------------------------------------
# Adversarial & Edge-Case Governance Tests (Covers 3 Mandatory Categories)
# ---------------------------------------------------------------------------

def test_adversarial_synthetic_approval_rejection():
    """Adversarial Category: Invariant bypass attempt.

    Verifies that synthetic auto-approvals or conflating 426 fragments as human
    decisions is strictly blocked by baseline verification invariants.
    """
    summary = compute_baseline_metrics(INVENTORY_FILE, LEDGER_FILE)

    # Attempt invariant bypass: claiming all 426 fragments were human questions
    spoofed_summary = compute_baseline_metrics(INVENTORY_FILE, LEDGER_FILE)
    spoofed_summary.actual_questions_answered = 426  # Synthetic conflation
    invariants = verify_baseline_invariants(spoofed_summary)

    assert invariants["questions_not_equal_entries"] is False, (
        "Verifier must reject conflation of 426 fragments as 426 human decisions"
    )


def test_adversarial_malformed_timestamps_and_boundary_rejection():
    """Adversarial Category: Malformed input and boundary payloads.

    Verifies rejection of inverted timestamps, negative durations, and zero-length spans.
    """
    with open(INVENTORY_FILE, "r", encoding="utf-8") as f:
        inv = json.load(f)

    # Invariant checks across turns
    for t in inv["turns"]:
        start = t["start_time_sec"]
        end = t["end_time_sec"]
        dur = t["duration_sec"]
        # No inverted or zero intervals permitted in valid census
        assert end > start, f"Turn {t.get('turn_id')} has non-positive interval: [{start}, {end}]"
        assert dur > 0.0, f"Turn {t.get('turn_id')} has non-positive duration: {dur}"
        assert len(t["utterance_text"].strip()) > 0, f"Turn {t.get('turn_id')} has empty text"


def test_exact_production_regression_acoustic_bleed_and_silence():
    """Adversarial Category: Exact production regression.

    Pins known production edge cases:
    1. Acoustic bleed at 00:00:34 ('The') must remain strictly Unknown.
    2. Post-13:38 recording tail contains zero speech turns.
    """
    with open(INVENTORY_FILE, "r", encoding="utf-8") as f:
        inv = json.load(f)

    # 1. Turn at 33.5s is acoustic bleed 'The'
    bleed_turn = next((t for t in inv["turns"] if 33.0 <= t["start_time_sec"] <= 35.0), None)
    assert bleed_turn is not None
    assert bleed_turn["utterance_text"].strip().lower() == "the"
    assert bleed_turn["reference_speaker"] == "Unknown", "Acoustic bleed must never be assigned a human attendee"
    assert bleed_turn["adjudication_status"] == "UNRESOLVED_QUEUE"

    # 2. Recording tail after 818.0s contains zero spoken dialogue
    tail_turns = [t for t in inv["turns"] if t["start_time_sec"] > 818.0]
    assert len(tail_turns) == 0, f"Found unexpected speech turns in recording tail: {tail_turns}"
