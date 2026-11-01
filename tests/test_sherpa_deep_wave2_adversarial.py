"""Adversarial and boundary test suite for Sherpa Deep RCA Wave 2:
Preflight cascade, quote-hint attribution, and remediator invariants.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from dual_pipeline.canonical_pipeline.u0_ingress import (
    UnifiedPreflightCascadeError,
    batch_validate_preflight_checks,
)
from dual_pipeline.speaker_attribution_core.attribution_engine import AudioSpeakerAttributionEngine
from dual_pipeline.speaker_attribution_core.audio_uncertainty_detector import _check_meeting_scope
from dual_pipeline.speaker_attribution_core.candidate_scorer import (
    _check_quote_hint_match,
    load_speaker_hints,
    score_candidates_for_speaker,
)
from dual_pipeline.transcript_pipeline_core.speaker_map_loader import load_colocated_speaker_map
from dual_pipeline.transcript_pipeline_core.surgical_remediator import SurgicalRemediator
from dual_pipeline.transcript_qa_core.forensic_healer_helpers import ensure_crosstalk_alr_rows
from dual_pipeline.transcript_stages_core.c05_title_gate import (
    C05TitleCompletenessGate,
    raise_hitl_for_titles_batch,
)
from dual_pipeline.transcript_stages_core.lexical_normalizer import _normalize_domain_terms
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


def test_adversarial_malformed_speaker_hints_queue():
    """Malformed input: corrupt JSON or non-dict/non-list contents in speaker_hints_queue.json."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        queue_file = tmp_path / "speaker_hints_queue.json"

        # 1. Truncated / invalid JSON
        queue_file.write_text("{malformed: json", encoding="utf-8")
        test_file = tmp_path / "test.md"
        test_file.touch()
        # Must not crash, fails open/safe to None
        assert load_colocated_speaker_map(test_file) is None
        assert load_speaker_hints({"container_dir": str(tmp_path)}) == []

        # 2. Valid JSON with wrong types (numbers, booleans, nested lists)
        queue_file.write_text(json.dumps([123, True, None, "plain string"]), encoding="utf-8")
        assert load_speaker_hints({"container_dir": str(tmp_path)}) == []


def test_adversarial_full_preflight_cascade_all_gates_failing():
    """Boundary payload: all preflight gates fail simultaneously; must collect all without early abort."""
    metadata = {
        "project": "project_beacon",
        # Missing "time"
        "classification": "internal",
        # Missing "speaker_map" while generic speakers exist in transcript
        # Missing attendee confirmation
        "source_type": "LIVE_AUDIO",
        "attendees_confirmed_by_operator": False,
        # Multiple unverified titles
        "attendees": [
            {"name": "Attendee Alpha", "role": "not specified"},
            {"name": "Attendee Beta", "role": ""},
            {"name": "Attendee Gamma", "role": "TBD"},
        ],
    }
    raw_transcript = (
        "[00:00:01] Speaker 1: Generic turn here.\n"
        "[00:00:05] Speaker 2: Another generic turn."
    )

    report = batch_validate_preflight_checks(metadata, raw_transcript=raw_transcript)
    assert not report.is_valid
    # Must capture ALL missing categories in a single pass
    assert "time" in report.missing_fields
    assert "speaker_map" in report.missing_fields
    assert "attendee_roles" in report.missing_fields
    assert "attendee_confirmation" in report.missing_fields
    assert len(report.missing_fields) >= 4

    diag_checks = {d.check_name for d in report.diagnostics}
    assert "U0_MANDATORY_TIME" in diag_checks
    assert "U0_UNMAPPED_SPEAKERS" in diag_checks
    assert "C05_TITLE_COMPLETENESS" in diag_checks
    assert "U0_ATTENDEE_CONFIRMATION" in diag_checks


def test_adversarial_invariant_bypass_unknown_cluster_deduction():
    """Invariant bypass attempt: ensure neither 'Unknown', 'Speaker Unknown', nor 'Unattributed' deduces roster."""
    roster = [("Sole Attendee", "PartnerAdvisory")]
    metadata = {"roster": [{"name": "Sole Attendee", "role": "Consultant"}]}

    for evil_spk in ("Unknown", "speaker unknown", "Speaker_Unknown", "Unattributed"):
        candidates = score_candidates_for_speaker(
            spk_id=evil_spk,
            transcript=f"[00:01:00] {evil_spk}: Testing invariant bypass.",
            roster=roster,
            metadata=metadata,
            assigned_attendees=set(),
            total_clusters=1,
        )
        for c in candidates:
            assert "Bijective deduction" not in c.evidence_rationale


def test_adversarial_cross_meeting_hijack_scope_isolation():
    """Invariant bypass attempt: spoofing meeting scope for speaker unknown across namespaces."""
    # Empty target meeting must fail closed
    assert _check_meeting_scope(
        cluster_norm="speaker unknown",
        target_meeting="",
        rec_meeting="sherpa_connect_20261014",
        project="project_beacon",
        rec_proj="project_beacon",
    ) is False

    # Partial substring attack: "sherpa" vs "sherpa_connect_different_date"
    assert _check_meeting_scope(
        cluster_norm="speaker unknown",
        target_meeting="sherpa_connect_20261014",
        rec_meeting="another_meeting_20261014",
        project="proj_a",
        rec_proj="proj_b",
    ) is False


def test_adversarial_triple_simultaneous_crosstalk():
    """Boundary payload: 3 consecutive turns with identical timestamp [00:02:15] across 3 different speakers."""
    transcript = (
        "# Forensic Transcript\n\n"
        "## Attribution Summary\n\n"
        "| Speaker | Confirmed Identity | Organization | Role / Title | Notes |\n"
        "|---|---|---|---|---|\n"
        "| Speaker 1 | Alice Smith | PartnerAdvisory | Consultant | Attributed |\n\n"
        "### [00:02:15] **Alice Smith**:\n"
        "I was thinking about the deploy.\n\n"
        "### [00:02:15] **Bob Jones**:\n"
        "Wait, can I add something here?\n\n"
        "### [00:02:15] **Carol White**:\n"
        "Yes, go ahead Bob.\n\n"
        "## Ambiguity Lock Register\n\n"
        "| ID | Ambiguity / Context | Timestamp | Evidence Locators | Action & Audit Rationale | Invariant Rule |\n"
        "|---|---|---|---|---|---|\n"
    )
    ch = []
    healed = ensure_crosstalk_alr_rows(transcript, ch)
    # ALR row must be injected
    assert "ALR-XTALK-000215" in healed
    # Calling it a second time must be idempotent (no duplicate rows)
    ch_idempotent = []
    healed_twice = ensure_crosstalk_alr_rows(healed, ch_idempotent)
    assert healed_twice.count("ALR-XTALK-000215") == 1
    assert len(ch_idempotent) == 0


def test_adversarial_phonetic_normalization_case_and_boundary_variants():
    """Boundary payload: varying case and surrounding punctuation for Serpa and LangChain."""
    dirty_text = (
        "We tested Serpa, SERPA, and serpa in production. "
        "Also lane chain, Lane Chain, and LANG CHAIN were used."
    )
    clean = _normalize_domain_terms(dirty_text, [])
    assert "Serpa" not in clean
    assert "SERPA" not in clean
    assert "serpa" not in clean
    assert "lane chain" not in clean
    assert "Lane Chain" not in clean
    assert "LANG CHAIN" not in clean
    assert "Sherpa" in clean
    assert "LangChain" in clean
