"""
tests/test_hitl_locator_envelope_adversarial.py

Class B Adversarial Test for Wave 3:
Validates edge cases, boundary payloads, and invariant bypass prevention:
1. Phantom speaker HITL assertion (not in transcript, not in ledger) fails closed.
2. Arbitrary ungrounded section locator fails closed.
3. Trivial fallback section locator ("transcript", "session context") fails closed.
4. Malformed / unsupported locator kinds fail closed.
5. Zero-signal source material (<50 words) fails closed.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
import pytest

from dual_pipeline.exceptions import ZeroSignalError
from tools.prepare_evidence_envelope_intake import (
    _validate_single_locator,
    prepare_package,
)

SAMPLE_TRANSCRIPT = """Oct 13, 2026
Apex Global (int) | Weekly PartnerAdvisory & GCP Alignment (See Agenda) - Transcript
00:00:25

Anya Gayvoronskaya: Hi, Alex.
Alex Brannan: Yeah. How are you?
Bryan Armalavage: Hey, good afternoon folks.
Robson N. Silva: Hey, good afternoon everyone.
Amit Ayer: Hey everyone, can you hear me?
Rusty Gattmann: Congratulations on the automation RFP award!
Chad Brockman: Nice work everyone.
Vani Eswarappa: Excited to collaborate on Apex Global architecture.
"""


def _base_draft() -> dict:
    return {
        "schema_version": "5.7",
        "meeting_id": "meeting-20261013-weekly-partner_advisory-gcp-alignment",
        "title": "Weekly PartnerAdvisory & GCP Alignment",
        "classification": "restricted",
        "audiences": ["nee-engineering"],
        "evidence_index": {
            "ev-01": {
                "speaker": "person:amit-ayer",
                "source_classification": "REPORTED FACT",
                "kind": "strategic",
                "text": "Amit Ayer: Hey everyone, can you hear me?",
                "locator": {"kind": "timestamp", "value": "00:00:25"},
                "grounding_status": "GROUNDED",
            }
        },
        "graph_mutations": {
            "entity_upserts": [],
            "relationship_signals": [],
            "action_updates": [],
            "decision_updates": [],
        },
    }


def test_ungrounded_phantom_speaker_hitl_fails_closed():
    """Verify that an ungrounded phantom speaker claiming HITL verification fails closed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        source_file = tmp_path / "transcript.txt"
        source_file.write_text(SAMPLE_TRANSCRIPT, encoding="utf-8")

        draft_file = tmp_path / "draft.json"
        draft = _base_draft()
        # Phantom speaker not present in transcript nor in ledger
        draft["evidence_index"]["ev_phantom"] = {
            "statement_id": "stmt_phantom",
            "evidence_id": "ev_phantom",
            "kind": "summary",
            "text": "Human operator verified professional title 'Executive Vice President' for John Phineas Phantom.",
            "exact_text": "Human operator verified professional title 'Executive Vice President' for John Phineas Phantom.",
            "speaker": "Operator",
            "source": "TRANSCRIPT_HITL",
            "locator": {"kind": "hitl_decision_ledger", "value": "hitl"},
        }
        draft_file.write_text(json.dumps(draft), encoding="utf-8")
        output_file = tmp_path / "output.json"

        with pytest.raises(ValueError) as excinfo:
            prepare_package(
                draft_path=draft_file,
                source_path=source_file,
                output_path=output_file,
                source_location=str(source_file),
                classification="restricted",
                audiences=["nee-engineering"],
                started_at="2026-10-13T00:00:25Z",
            )
        assert "source-bound locator is absent" in str(excinfo.value)


def test_arbitrary_unresolved_section_locator_fails_closed():
    """Verify that a nonexistent arbitrary section locator fails closed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        source_file = tmp_path / "transcript.txt"
        source_file.write_text(SAMPLE_TRANSCRIPT, encoding="utf-8")

        draft_file = tmp_path / "draft.json"
        draft = _base_draft()
        draft["evidence_index"]["ev-bad-sec"] = {
            "statement_id": "stmt-bad-sec",
            "speaker": "person:amit-ayer",
            "text": "Fictional ungrounded statement",
            "locator": {"kind": "section", "value": "## Nonexistent Architecture Topology Heading"},
        }
        draft_file.write_text(json.dumps(draft), encoding="utf-8")
        output_file = tmp_path / "output.json"

        with pytest.raises(ValueError) as excinfo:
            prepare_package(
                draft_path=draft_file,
                source_path=source_file,
                output_path=output_file,
                source_location=str(source_file),
                classification="restricted",
                audiences=["nee-engineering"],
                started_at="2026-10-13T00:00:25Z",
            )
        assert "source-bound locator is absent from the preserved export: section '## Nonexistent Architecture Topology Heading'" in str(excinfo.value)


def test_trivial_fallback_locator_fails_closed():
    """Verify that forbidden trivial fallback sections ('transcript', 'session context') fail closed."""
    source_text = "Oct 13 2026 meeting transcript notes."
    source_path = Path("/tmp/dummy.txt")

    for trivial_name in ["transcript", "session context", "transcript context", "forensic transcript", "## transcript"]:
        item = {"text": "Some text"}
        locator = {"kind": "section", "value": trivial_name}
        with pytest.raises(ValueError) as excinfo:
            _validate_single_locator(item, locator, source_text, source_path)
        assert "Trivial fallback locator is not permitted" in str(excinfo.value)


def test_unsupported_locator_kind_fails_closed():
    """Verify that invalid locator kinds are rejected by _validate_single_locator."""
    source_text = "Oct 13 2026 meeting transcript notes."
    source_path = Path("/tmp/dummy.txt")

    item = {"text": "Some text"}
    locator = {"kind": "unsupported_kind", "value": "some_value"}
    with pytest.raises(ValueError) as excinfo:
        _validate_single_locator(item, locator, source_text, source_path)
    assert "Evidence envelope intake locators must use" in str(excinfo.value)


def test_zero_signal_empty_source_fails_closed():
    """Verify that source transcript below 50 words raises ZeroSignalError."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        source_file = tmp_path / "empty_transcript.txt"
        source_file.write_text("Too short meeting notes only ten words here in total.", encoding="utf-8")

        draft_file = tmp_path / "draft.json"
        draft = _base_draft()
        draft_file.write_text(json.dumps(draft), encoding="utf-8")
        output_file = tmp_path / "output.json"

        with pytest.raises(ZeroSignalError) as excinfo:
            prepare_package(
                draft_path=draft_file,
                source_path=source_file,
                output_path=output_file,
                source_location=str(source_file),
                classification="restricted",
                audiences=["nee-engineering"],
                started_at="2026-10-13T00:00:25Z",
            )
        assert "falls below minimum semantic yield threshold" in str(excinfo.value)
