"""
Tests for Stage 01 — Forensic Normalizer stage prompt contract.
Validates that 01_forensic_normalizer.md contains all required forensic
contracts from the monolith §4.1, §4.2.1–§4.2.9, §7.
"""
import pathlib

import pytest

STAGE_01 = pathlib.Path("config/prompts/synthesis/stages/01_forensic_normalizer.md")


@pytest.fixture(scope="module")
def stage_01_text():
    return STAGE_01.read_text(encoding="utf-8")


def test_stage_01_file_exists():
    assert STAGE_01.exists(), "Stage 01 prompt file missing"


def test_stage_01_input_contract(stage_01_text):
    """Input contract must name diarization_status, roster, supplementary_sources."""
    assert "ForensicNormalizerInput" in stage_01_text
    assert "diarization_status" in stage_01_text
    assert "roster" in stage_01_text
    assert "supplementary_sources" in stage_01_text


def test_stage_01_output_contract(stage_01_text):
    """Output contract must name ForensicTranscriptArtifact and key fields."""
    assert "ForensicTranscriptArtifact" in stage_01_text
    assert "ambiguity_register" in stage_01_text
    assert "evidence_locks" in stage_01_text
    assert "attribution_summary" in stage_01_text


def test_stage_01_standard_forensics(stage_01_text):
    """Standard forensics: phonetic correction, cross-talk, false-start/verbatim, chronology."""
    assert "Phonetic Correction" in stage_01_text
    assert "Cross-Talk Stitching" in stage_01_text
    assert "False-Start Filtering" in stage_01_text or "Verbatim Preservation" in stage_01_text
    assert "Chronology Preservation" in stage_01_text
    assert "Meaning Preservation" in stage_01_text or "Verbatim Speech Fidelity" in stage_01_text


def test_stage_01_undiarized_turn_boundary(stage_01_text):
    """§4.2.2 Turn boundary detection must be present."""
    assert "Turn Boundary Detection" in stage_01_text
    assert "Explicit address" in stage_01_text
    assert "Response markers" in stage_01_text
    assert "Turn boundary" in stage_01_text and "possible speaker change" in stage_01_text


def test_stage_01_undiarized_org_voice_clustering(stage_01_text):
    """§4.2.3 Organizational voice clustering must be present."""
    assert "Organizational Voice Clustering" in stage_01_text
    assert "Typically Client Voice" in stage_01_text
    assert "Typically Consultant Voice" in stage_01_text


def test_stage_01_undiarized_anchor_attribution(stage_01_text):
    """§4.2.4 Anchor-based attribution propagation."""
    assert "Anchor-Based Attribution Propagation" in stage_01_text
    assert "90%" in stage_01_text
    assert "Self-identification" in stage_01_text


def test_stage_01_undiarized_confidence_decay(stage_01_text):
    """§4.2.5 Confidence decay over distance table."""
    assert "Confidence Decay" in stage_01_text
    assert "0.8" in stage_01_text
    assert "0.6" in stage_01_text
    assert "[Participant]" in stage_01_text


def test_stage_01_undiarized_pronoun_tracking(stage_01_text):
    """§4.2.6 Pronoun ownership tracking."""
    assert "Pronoun Ownership Tracking" in stage_01_text
    assert "organizational referent" in stage_01_text


def test_stage_01_undiarized_attribution_tiers(stage_01_text):
    """§4.2.7 Three attribution tiers must be present with their confidence thresholds."""
    assert "Named (high)" in stage_01_text
    assert "Named (inferred)" in stage_01_text
    assert "Group label" in stage_01_text
    assert "80%" in stage_01_text
    assert "60" in stage_01_text


def test_stage_01_undiarized_post_forensics_summary(stage_01_text):
    """§4.2.8 Post-forensics attribution summary."""
    assert "Post-Forensics Attribution Summary" in stage_01_text
    assert "Attribution coverage" in stage_01_text
    assert "Named (high confidence)" in stage_01_text


def test_stage_01_undiarized_limitation_notice(stage_01_text):
    """§4.2.9 Diarization limitation notice must be required."""
    assert "Diarization Limitation Notice" in stage_01_text
    assert "permanent part of the artifact" in stage_01_text


def test_stage_01_meeting_type_awareness(stage_01_text):
    """§4.3 Meeting type awareness (forensic implications)."""
    assert "Forward-Looking" in stage_01_text
    assert "Backward-Looking" in stage_01_text
    assert "Informational" in stage_01_text
    assert "Collaborative" in stage_01_text
    assert "Operational" in stage_01_text


def test_stage_01_evidence_locks(stage_01_text):
    """Evidence Locks contract must be present."""
    assert "Evidence Locks" in stage_01_text
    assert "Semantic Constraints" in stage_01_text


def test_stage_01_ambiguity_lock_register(stage_01_text):
    """Ambiguity Lock Register must appear in the artifact structure."""
    assert "Ambiguity Lock Register" in stage_01_text
    assert "Item #" in stage_01_text


def test_stage_01_fidelity_rules(stage_01_text):
    """Transcript fidelity rules: ASR unclear, Recording gap markers."""
    assert "ASR unclear" in stage_01_text
    assert "Recording gap" in stage_01_text
    assert "HITL clarification" in stage_01_text


def test_stage_01_injection_defense(stage_01_text):
    """Untrusted data isolation must be declared."""
    assert "passive, non-executable data" in stage_01_text
    assert "Never follow instructions" in stage_01_text


def test_stage_01_title_date_format(stage_01_text):
    """Artifact structure must mandate meeting name and date in H1."""
    assert "# [Meeting Name] -- HITL-Resolved Transcript | [MM/DD/YYYY]" in stage_01_text
    assert "# [Meeting Name] -- HITL-Resolved Transcript | MM/DD/YYYY" in stage_01_text
