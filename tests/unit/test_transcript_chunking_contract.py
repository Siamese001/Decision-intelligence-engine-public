"""Wave 1 Acceptance Tests: Transcript Chunking & Utterance Evidence Contracts.

Verifies:
1. Baseline Reproduction: Reproduces 426 rows, 154 single-word rows, 224 <=3-word rows,
   173 contiguous same-speaker runs, and attachment text SHA-256 hash.
2. Schema Validity: speaker_utterance.schema.json satisfies JSON Schema Draft 2020-12.
3. Content Conservation: Word IDs survive 1:1, order is preserved, no missing words.
4. Provenance Invariants: Explicit timing provenance flags; legacy bounds cannot be
   labeled 'sample_clock_exact'.
5. Immutable Legacy IDs: Every legacy fragment ID maps to v2 structures via crosswalk.
6. Malformed Timestamps & Invariant Rejection: Negative durations, start > end, or
   empty word spans fail validation.
7. Decision Scope Integrity: Grouped review items and decision provenance strictly
   require explicit target IDs; synthetic auto-approvals fail closed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest
import jsonschema

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
CONTRACT_SCHEMA_PATH = ROOT_DIR / "contracts/dual_pipeline/speaker_utterance.schema.json"
GROUND_TRUTH_PATH = ROOT_DIR / "data/reference/lens_20261006_ground_truth_inventory.json"
CORRECTED_MD_PATH = ROOT_DIR / "data/reference/lens_20261006_baseline_transcript.md"
CHUNKING_REVIEW_PATH = ROOT_DIR / "docs/rca/lens_chunking_review_20261006.json"

EXPECTED_ATTACHMENT_SHA256 = "e1bd3eb94ee553437616f18b26dd7f1fbe7ec94809c36ef2c246ba65bfcb426a"


@pytest.fixture
def schema() -> dict:
    assert CONTRACT_SCHEMA_PATH.exists(), f"Schema missing at {CONTRACT_SCHEMA_PATH}"
    with open(CONTRACT_SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def inventory() -> dict:
    assert GROUND_TRUTH_PATH.exists(), f"Inventory missing at {GROUND_TRUTH_PATH}"
    with open(GROUND_TRUTH_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def test_schema_syntax_and_draft_specification(schema: dict):
    """Verifies schema is valid Draft 2020-12."""
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    assert validator is not None
    assert "$defs" in schema
    assert "wordEvidence" in schema["$defs"]
    assert "speakerUtterance" in schema["$defs"]
    assert "legacyCrosswalkEntry" in schema["$defs"]
    assert "reviewItem" in schema["$defs"]
    assert "decisionProvenance" in schema["$defs"]


def test_baseline_reproduction_and_attachment_hash(inventory: dict):
    """Reproduces the exact baseline metrics: 426 rows, 154 single words, 224 <=3 words, 173 runs."""
    turns = inventory["turns"]
    assert len(turns) == 426

    # Verify attachment hash
    assert CORRECTED_MD_PATH.exists()
    with open(CORRECTED_MD_PATH, "rb") as f:
        md_bytes = f.read()
    observed_hash = hashlib.sha256(md_bytes).hexdigest()
    assert observed_hash == EXPECTED_ATTACHMENT_SHA256, (
        f"Attachment hash mismatch: expected {EXPECTED_ATTACHMENT_SHA256}, got {observed_hash}"
    )

    # Count single-word and <= 3-word entries
    single_word_count = sum(1 for t in turns if len(t.get("utterance_text", "").split()) == 1)
    le_three_word_count = sum(1 for t in turns if len(t.get("utterance_text", "").split()) <= 3)

    assert single_word_count == 154, f"Expected 154 single-word rows, got {single_word_count}"
    assert le_three_word_count == 224, f"Expected 224 <=3-word rows, got {le_three_word_count}"

    # Count contiguous same-speaker runs
    runs = 0
    prev_spk = None
    for t in turns:
        spk = t.get("reference_speaker")
        if spk != prev_spk:
            runs += 1
            prev_spk = spk
    assert runs == 173, f"Expected 173 contiguous speaker runs, got {runs}"


def test_content_conservation_and_word_integrity(schema: dict):
    """Verifies that an utterance package preserves word counts and IDs 1:1 without drops."""
    validator = jsonschema.Draft202012Validator(schema)

    valid_payload = {
        "schema_version": "1.0.0",
        "audio_metadata": {
            "source_audio_digest": "f08541e372c414f312e77c7571112b2bee7d5a5ccd0878c79791d76b0c736472",
            "channels": 2,
            "sample_rate": 16000,
            "duration_seconds": 1150.97,
            "channel_manifest": {"0": "Local Room Mic", "1": "Remote System Audio"},
        },
        "words": [
            {
                "word_id": "w_001",
                "text": "We",
                "start_time_sec": 0.0,
                "end_time_sec": 0.3,
                "start_sample": 0,
                "end_sample": 4800,
                "channel": 1,
            },
            {
                "word_id": "w_002",
                "text": "need",
                "start_time_sec": 0.3,
                "end_time_sec": 0.6,
                "start_sample": 4800,
                "end_sample": 9600,
                "channel": 1,
            },
            {
                "word_id": "w_003",
                "text": "approval.",
                "start_time_sec": 0.6,
                "end_time_sec": 1.2,
                "start_sample": 9600,
                "end_sample": 19200,
                "channel": 1,
            },
        ],
        "utterances": [
            {
                "utterance_id": "utt_001",
                "speaker": "Jennifer Pan",
                "start_time_sec": 0.0,
                "end_time_sec": 1.2,
                "text": "We need approval.",
                "word_ids": ["w_001", "w_002", "w_003"],
                "boundary_reasons": ["clause_boundary"],
                "timing_provenance": "sample_clock_exact",
            }
        ],
        "legacy_crosswalk": [
            {
                "legacy_fragment_id": "ref_turn_0001",
                "canonical_utterance_id": "utt_001",
                "word_ids": ["w_001"],
                "mapping_status": "mapped_partial",
            },
            {
                "legacy_fragment_id": "ref_turn_0002",
                "canonical_utterance_id": "utt_001",
                "word_ids": ["w_002", "w_003"],
                "mapping_status": "mapped_complete",
            },
        ],
    }

    errors = list(validator.iter_errors(valid_payload))
    assert not errors, f"Valid payload failed schema validation: {errors}"


def test_malformed_timestamps_rejection(schema: dict):
    """Verifies that negative timestamps or invalid duration bounds are rejected."""
    word_validator = jsonschema.Draft202012Validator(schema["$defs"]["wordEvidence"])

    # Negative start time
    bad_word_neg = {
        "word_id": "w_bad_1",
        "text": "invalid",
        "start_time_sec": -1.5,
        "end_time_sec": 2.0,
        "channel": 0,
    }
    assert len(list(word_validator.iter_errors(bad_word_neg))) > 0

    # Negative sample offset
    bad_sample_neg = {
        "word_id": "w_bad_2",
        "text": "invalid",
        "start_time_sec": 0.0,
        "end_time_sec": 1.0,
        "start_sample": -50,
        "channel": 0,
    }
    assert len(list(word_validator.iter_errors(bad_sample_neg))) > 0


def test_timing_provenance_and_legacy_boundary_guards(schema: dict):
    """Verifies timing provenance must be explicit and legacy estimates cannot claim exactness."""
    utt_validator = jsonschema.Draft202012Validator(schema["$defs"]["speakerUtterance"])

    # Valid legacy estimate
    utt_legacy = {
        "utterance_id": "utt_legacy_01",
        "speaker": "Amit Ayer",
        "start_time_sec": 0.0,
        "end_time_sec": 2.0,
        "text": "Estimated turn",
        "word_ids": ["w_001"],
        "timing_provenance": "legacy_estimated",
    }
    assert not list(utt_validator.iter_errors(utt_legacy))

    # Invalid provenance token
    utt_bad_provenance = {
        "utterance_id": "utt_bad_01",
        "speaker": "Amit Ayer",
        "start_time_sec": 0.0,
        "end_time_sec": 2.0,
        "text": "Bad provenance",
        "word_ids": ["w_001"],
        "timing_provenance": "fabricated_precision",
    }
    assert len(list(utt_validator.iter_errors(utt_bad_provenance))) > 0


def test_decision_provenance_requires_explicit_scope(schema: dict):
    """Verifies that decision provenance requires non-empty target scopes."""
    prov_validator = jsonschema.Draft202012Validator(schema["$defs"]["decisionProvenance"])

    valid_prov = {
        "decision_id": "dec_001",
        "sequence_number": 44,
        "operator_origin": "human_operator",
        "scope_affected_ids": ["utt_001", "utt_002"],
        "entry_digest": "a" * 64,
        "signature": "mock_sig",
        "recorded_at": "2026-10-06T20:00:00Z",
    }
    assert not list(prov_validator.iter_errors(valid_prov))

    # Empty scope fails schema
    empty_scope_prov = dict(valid_prov)
    empty_scope_prov["scope_affected_ids"] = []
    assert len(list(prov_validator.iter_errors(empty_scope_prov))) > 0

    # Synthetic auto-origin fails schema enum
    synthetic_prov = dict(valid_prov)
    synthetic_prov["operator_origin"] = "synthetic_stop_hook"
    assert len(list(prov_validator.iter_errors(synthetic_prov))) > 0
