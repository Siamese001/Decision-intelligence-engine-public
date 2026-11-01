"""End-to-End verification of Sherpa Deep RCA Wave 2:
Batched U0 Preflight, Quote-Hint Attribution & Stage 01 Remediator Fix.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.u0_ingress import (
    PreflightBatchReport,
    UnifiedPreflightCascadeError,
    batch_validate_preflight_checks,
    hydrate_live_container_artifacts,
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
from dual_pipeline.transcript_qa_core.forensic_healer import heal_recording_gaps
from dual_pipeline.transcript_qa_core.forensic_healer_helpers import (
    ensure_crosstalk_alr_rows,
    heal_alr_special_cases,
)
from dual_pipeline.transcript_stages_core.c05_title_gate import (
    C05TitleCompletenessGate,
    raise_hitl_for_titles_batch,
)
from dual_pipeline.transcript_stages_core.lexical_normalizer import _normalize_domain_terms
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


def test_batched_u0_preflight_cascade_error():
    """Verify missing time and unverified attendee titles are batched into a single UnifiedPreflightCascadeError."""
    metadata = {
        "project": "project_beacon",
        "meeting_name": "Sherpa Connect",
        "date": "10/14/2026",
        "classification": "internal",
        "attendees": [
            {"name": "New Attendee A", "role": "not specified"},
            {"name": "New Attendee B", "role": "TBD"},
        ],
        "attendees_confirmed_by_operator": True,
        "attendee_confirmation_receipt": {
            "status": "RESOLVED_HITL",
            "subject_sha256": "urn:nee:test-receipt",
        },
    }
    raw_transcript = "[00:00:01] New Attendee A: Hello everyone.\n[00:00:05] New Attendee B: Good morning."

    # In execute(), both missing time and missing titles must trigger UnifiedPreflightCascadeError
    u0 = U0IngestionBoundary()
    with pytest.raises(UnifiedPreflightCascadeError) as exc_info:
        u0.execute(raw_transcript, metadata)

    report = exc_info.value.report
    assert not report.is_valid
    assert "time" in report.missing_fields
    assert "attendee_roles" in report.missing_fields
    # Verify diagnostics are populated
    check_names = {d.check_name for d in report.diagnostics}
    assert "U0_MANDATORY_TIME" in check_names
    assert "C05_TITLE_COMPLETENESS" in check_names


def test_c05_title_gate_batched_hitl():
    """Verify C05TitleCompletenessGate batches all placeholder attendee titles in one pass."""
    metadata = {
        "project": "project_beacon",
        "roster": [
            {"name": "External Guest A", "role": "not specified"},
            {"name": "External Guest B", "role": "TBD"},
            {"name": "Verified Person", "role": "Lead Architect"},
        ],
    }
    l0_context = {"metadata": metadata}
    gate = C05TitleCompletenessGate()

    with pytest.raises(UnifiedPreflightCascadeError) as exc_info:
        gate.execute(l0_context)

    missing = exc_info.value.report.missing_fields
    assert "role:External Guest A" in missing
    assert "role:External Guest B" in missing
    assert "role:Verified Person" not in missing


def test_speaker_hints_queue_first_name_binding():
    """Verify speaker_hints_queue.json loading, first-name matching, and QUEUED quote binding."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        queue_file = tmp_path / "speaker_hints_queue.json"
        queue_file.write_text(json.dumps({
            "queue": [
                {
                    "speaker_name": "Cansu",
                    "quote": "Let us review the LangChain architecture",
                    "voice_id": "Speaker 1",
                    "status": "QUEUED",
                }
            ]
        }), encoding="utf-8")
        transcript_file = tmp_path / "test_live_transcript.md"
        transcript_file.write_text("# Live Meeting Transcript\n\n[00:00:01] Speaker 1: Hello.", encoding="utf-8")

        # 1. Test speaker_map_loader with queue file
        spk_map = load_colocated_speaker_map(transcript_file)
        assert spk_map is not None
        assert spk_map.get("Speaker 1") == "Cansu"

        # 2. Test candidate_scorer load_speaker_hints
        meta = {"container_dir": str(tmp_path)}
        hints = load_speaker_hints(meta)
        assert len(hints) == 1
        assert hints[0]["speaker_name"] == "Cansu"

        # 3. Test _check_quote_hint_match with full name roster ("Cansu Cankaya")
        full_text = "Let us review the LangChain architecture today"
        turns = [full_text]
        matched_quote = _check_quote_hint_match(
            spk_id="1",
            name="Cansu Cankaya",
            full_text=full_text,
            turns=turns,
            hints=hints,
        )
        assert matched_quote == "Let us review the LangChain architecture"
        # Verify status transitioned from QUEUED to BOUND
        assert hints[0]["status"] == "BOUND"
        assert hints[0]["bound_speaker_id"] == "1"


def test_unknown_cluster_blocks_bijective_deduction():
    """Verify Unknown / Speaker Unknown clusters disable injective deduction so Drew Oakley is not forced to Unknown."""
    roster = [("Drew Oakley", "PartnerAdvisory")]
    metadata = {
        "roster": [{"name": "Drew Oakley", "role": "Client Service Lead"}],
    }
    # For a genuine named cluster with 1 remaining roster member, bijective deduction triggers
    normal_candidates = score_candidates_for_speaker(
        spk_id="1",
        transcript="[00:01:00] Speaker 1: Hello.",
        roster=roster,
        metadata=metadata,
        assigned_attendees=set(),
        total_clusters=1,
    )
    assert len(normal_candidates) == 1
    assert "Bijective deduction" in normal_candidates[0].evidence_rationale

    # For an Unknown cluster, bijective deduction MUST NOT trigger
    unknown_candidates = score_candidates_for_speaker(
        spk_id="Unknown",
        transcript="[00:01:00] Unknown: Hello.",
        roster=roster,
        metadata=metadata,
        assigned_attendees=set(),
        total_clusters=1,
    )
    # Deduction should be skipped (no candidate returned or not bijective deduction)
    if unknown_candidates:
        assert "Bijective deduction" not in unknown_candidates[0].evidence_rationale


def test_meeting_scope_isolation_for_speaker_unknown():
    """Verify audio uncertainty detector isolates speaker unknown by meeting slug."""
    # Same meeting match
    assert _check_meeting_scope(
        cluster_norm="speaker unknown",
        target_meeting="sherpa_connect_20261014",
        rec_meeting="sherpa_connect_20261014",
        project="project_beacon",
        rec_proj="project_beacon",
    ) is True

    # Cross-meeting mismatch must fail closed
    assert _check_meeting_scope(
        cluster_norm="speaker unknown",
        target_meeting="sherpa_connect_20261014",
        rec_meeting="weekly_partner_gcp_20261013",
        project="project_beacon",
        rec_proj="partner_advisory_internal",
    ) is False


def test_surgical_remediator_short_circuit_and_phonetics():
    """Verify forensic_healer whitespace fix prevents short-circuit, and phonetics are normalized."""
    sample_transcript = (
        "# Forensic Transcript\n\n"
        "## Attribution Summary\n\n"
        "| Speaker | Confirmed Identity | Organization | Role / Title | Notes |\n"
        "|---|---|---|---|---|\n"
        "| Speaker 1 | Cansu Cankaya | PartnerAdvisory | Senior Consultant | Attributed |\n\n"
        "### [00:01:03] **Cansu Cankaya**:\n"
        "We are evaluating Serpa and lane chain for this release.\n\n"
        "### [00:01:03] **Drew Oakley**:\n"
        "Yes, Serpa is working great with lane chain.\n\n"
        "## Ambiguity Lock Register\n\n"
        "| ID | Ambiguity / Context | Timestamp | Evidence Locators | Action & Audit Rationale | Invariant Rule |\n"
        "|---|---|---|---|---|---|\n"
    )

    # 1. Test forensic healer does not silently mutate newline-separated turns
    changes = []
    audit_log = []
    healed = heal_recording_gaps(sample_transcript, changes, audit_log)
    assert "Separated merged turns on same line" not in changes

    # 2. Test SurgicalRemediator.remediate_output_1 on 0 adjustments returns did_remediate=False
    _, did_remediate, audit_trail = SurgicalRemediator.remediate_output_1(
        sample_transcript,
        qa_report={"needs_remediation": True},
        judge_report={"needs_remediation": True},
        directive="surgical_remediation",
    )
    assert not did_remediate
    assert len(audit_trail) == 0

    # 3. Test domain term normalization for Serpa and lane chain
    normalized = _normalize_domain_terms(sample_transcript, ["Sherpa", "LangChain"])
    assert "Serpa" not in normalized
    assert "lane chain" not in normalized
    assert "Sherpa" in normalized
    assert "LangChain" in normalized

    # 4. Test simultaneous [00:01:03] turns annotated in ALR
    alr_changes = []
    healed_alr = ensure_crosstalk_alr_rows(sample_transcript, alr_changes)
    assert "ALR-XTALK-000103" in healed_alr
    assert any("simultaneous turns at [00:01:03]" in c for c in alr_changes)
