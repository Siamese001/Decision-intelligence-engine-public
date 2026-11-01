"""
tests/test_speaker_attribution_engine.py

Wave 2 Acceptance Gate: Validates that AudioSpeakerAttributionEngine detects
single-speaker collapse and excessive speaker skew before Output 01 synthesis
and QA gate evaluation.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dual_pipeline.hitl_governance_core.crypto import (
    append_signed_approval_to_ledger,
    create_signed_hitl_approval_entry,
    generate_hitl_keypair,
)
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.speaker_attribution_core import AudioSpeakerAttributionEngine
from dual_pipeline.speaker_attribution_core.models import CandidateAttribution
from dual_pipeline.speaker_attribution_core.collapse_detector import (
    check_speaker_skew_and_collapse,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError


def test_cluster_assignment_question_contains_context_for_every_target(tmp_path, monkeypatch):
    transcript = "\n".join([
        "Avery: First surrounding thought.",
        "Speaker 1: First target turn.",
        "Jordan: Follow-up thought.",
        "Speaker 1: Second target turn.",
        "Avery: Later follow-up.",
    ])
    engine = AudioSpeakerAttributionEngine(
        decision_store=HITLDecisionStore(ledger_path=tmp_path / "ledger.json", sqlite_path=tmp_path / "ledger.sqlite")
    )
    monkeypatch.setattr(engine, "_score_candidates_for_speaker", lambda *args, **kwargs: [
        CandidateAttribution("Avery", "Org", 0.7, "MEDIUM", "fixture evidence", True),
        CandidateAttribution("Jordan", "Org", 0.3, "LOW", "fixture evidence"),
    ])
    captured = {}
    engine._resolve_single_generic_speaker(
        "Speaker 1", transcript, [("Avery", "Org"), ("Jordan", "Org")], {"meeting_id": "meeting-test"},
        lambda question, _options: captured.setdefault("question", question) or "Avery",
        False, {}, total_clusters=1,
    )
    question = captured["question"]
    assert "First surrounding thought." in question
    assert "First target turn." in question
    assert "Second target turn." in question
    assert "Later follow-up." in question
    assert question.count("**UNRESOLVED — Speaker 1**") == 2


@pytest.fixture
def multi_attendee_metadata():
    return {
        "project": "project_nexus",
        "attendees": {
            "PartnerAdvisory": [
                "Amit Ayer (Lead Enterprise Architect)",
            ],
            "Apex Global Enterprises": [
                "Hayden Radovich (VP Architecture)",
            ],
        },
    }


def test_single_speaker_collapse_all_turns_same_speaker(multi_attendee_metadata):
    """Incident replay: 74 turns all labeled 'Hayden' with 2 attendees must halt with single_speaker_collapse."""
    engine = AudioSpeakerAttributionEngine()
    transcript = "\n\n".join(f"**Hayden:** Turn utterance number {i} discussing architecture." for i in range(74))

    with pytest.raises(HITLRequiredError) as exc_info:
        engine.analyze_and_resolve(transcript, multi_attendee_metadata)

    err = exc_info.value
    assert err.hitl_type == "single_speaker_collapse"
    assert "single-speaker collapse detected" in str(err).lower()
    assert err.evidence["attendee_count"] == 2
    assert err.evidence["total_turns"] == 74
    assert err.evidence["dominant_label"] == "Hayden"
    assert err.evidence["dominant_label_turns"] == 74
    assert err.evidence["max_share_bps"] == 8500


def test_single_speaker_collapse_named_labels_not_generic(multi_attendee_metadata):
    """Proves fix for regex blindness: named labels enter attribution validation as claims, not verified identities."""
    transcript = "\n\n".join(f"Hayden: Dialogue segment {i}" for i in range(20))

    with pytest.raises(HITLRequiredError) as exc_info:
        check_speaker_skew_and_collapse(transcript, [("Hayden", "NEE"), ("Amit", "PartnerAdvisory")], multi_attendee_metadata)

    err = exc_info.value
    assert err.hitl_type == "single_speaker_collapse"
    assert err.evidence["dominant_label"] == "Hayden"
    assert err.evidence["total_turns"] == 20
    assert err.evidence["dominant_ratio"] == 1.0


def test_single_speaker_collapse_balanced_conversation_passes(multi_attendee_metadata):
    """Balanced dialogue (50% / 50%) across attendees does not trigger collapse halt."""
    turns = []
    for i in range(20):
        spk = "Hayden" if i % 2 == 0 else "Amit"
        turns.append(f"**{spk}:** Utterance {i}")
    transcript = "\n\n".join(turns)

    res = check_speaker_skew_and_collapse(
        transcript, [("Hayden", "NEE"), ("Amit", "PartnerAdvisory")], multi_attendee_metadata
    )
    assert res is None


def test_single_speaker_collapse_sub_10_turns_threshold(multi_attendee_metadata):
    """Transcripts with < 10 total turns do not trigger the 85% skew threshold."""
    transcript = "\n\n".join(f"**Hayden:** Short meeting turn {i}" for i in range(8))
    res = check_speaker_skew_and_collapse(
        transcript, [("Hayden", "NEE"), ("Amit", "PartnerAdvisory")], multi_attendee_metadata, min_turns=10
    )
    assert res is None


def test_single_speaker_collapse_single_attendee_meeting_passes():
    """Solo recordings or 1-person monologue meetings do not trigger collapse halt."""
    single_roster_metadata = {
        "project": "conduit",
        "attendees": {
            "PartnerAdvisory": ["Amit Ayer (Lead Architect)"],
        },
    }
    transcript = "\n\n".join(f"**Amit Ayer:** Personal note {i}" for i in range(25))
    res = check_speaker_skew_and_collapse(
        transcript, [("Amit Ayer", "PartnerAdvisory")], single_roster_metadata
    )
    assert res is None


def test_single_speaker_collapse_bypassed_with_valid_receipt(multi_attendee_metadata, tmp_path: Path):
    """When metadata contains an authenticated operator resolution receipt, collapse check passes."""
    sub_hash = "col_sub_" + "0" * 48
    priv, pub = generate_hitl_keypair()
    entry = create_signed_hitl_approval_entry(
        subject_sha256=sub_hash,
        case_ids=["COLLAPSE-01"],
        operator_id="amit.ayer",
        private_key=priv,
        public_key=pub,
    )
    ledger_file = tmp_path / "hitl_decision_ledger.json"
    ledger_file.write_text(json.dumps({"decisions": []}), encoding="utf-8")
    append_signed_approval_to_ledger(entry, ledger_path=ledger_file)

    metadata_with_receipt = dict(multi_attendee_metadata)
    metadata_with_receipt["subject_sha256"] = sub_hash
    metadata_with_receipt["hitl_ledger_path"] = str(ledger_file)

    transcript = "\n\n".join(f"**Hayden:** Turn {i}" for i in range(30))
    res = check_speaker_skew_and_collapse(
        transcript, [("Hayden", "NEE"), ("Amit", "PartnerAdvisory")], metadata_with_receipt
    )
    assert res is None


def test_single_speaker_collapse_caller_asserted_boolean_rejected(multi_attendee_metadata):
    """Caller-asserted hitl_receipt_bound boolean without signed ledger entry must fail closed."""
    meta = dict(multi_attendee_metadata)
    meta["hitl_receipt_bound"] = True
    meta["speaker_attribution_resolved"] = True
    meta["hitl_receipts"] = [{"hitl_type": "single_speaker_collapse"}]

    transcript = "\n\n".join(f"**Hayden:** Turn {i}" for i in range(30))
    with pytest.raises(HITLRequiredError) as exc_info:
        check_speaker_skew_and_collapse(
            transcript, [("Hayden", "NEE"), ("Amit", "PartnerAdvisory")], meta
        )
    assert exc_info.value.hitl_type == "single_speaker_collapse"
