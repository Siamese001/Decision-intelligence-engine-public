"""
tests/test_output1_attribution_hardening.py

Unit tests for Wave 2: Output 01 Deterministic Hardening:
- Dynamic AttributionSummaryBuilder verifying accurate affiliations and spoken turn counts
- Timestamp zero-padding ([MM:SS] -> [00:MM:SS])
- Automated Rule C-11 silence gap insertion for gaps > 180 seconds
- Automated ASR cleaning: pruning repetitive "start the video" phrases and mapping Jed -> Jen Pan with ALR
"""

from __future__ import annotations

from dual_pipeline.transcript_qa_core.forensic_healer import (
    heal_output_1_forensic,
    heal_recording_gaps,
    zero_pad_timestamps,
)
from dual_pipeline.transcript_stages_core.attribution_builder import AttributionSummaryBuilder


def test_attribution_summary_builder_accurately_assigns_orgs_and_counts_turns():
    """Verify AttributionSummaryBuilder extracts real orgs from roster and counts dialogue turns."""
    metadata = {
        "attendees": [
            {"name": "Parker Vance", "org": "Apex Global Enterprises (NEA / PGD)", "role": "Technical Director / PGD Customer for LENS"},
            {"name": "Eduardo Juan More", "org": "Apex Global Enterprises (NEA / PGD)", "role": "Atlas Lead / Rule Specialist"},
            {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Enterprise Architect / Lead Client Partner"},
            {"name": "Jen Pan", "org": "PartnerAdvisory", "role": "Product Manager / Lead Engineer"},
            {"name": "Jake Stark", "org": "PartnerAdvisory", "role": "UI and Prompting Lead"},
        ]
    }
    dialogue = """
# Live Meeting Transcript
**[00:00:00]** Parker Vance: First turn from Anthony.
**[00:00:32]** Amit Ayer: First turn from Amit.
**[00:01:09]** Parker Vance: Second turn from Anthony.
**[00:02:45]** Eduardo Juan More: First turn from Eduardo.
**[00:05:30]** Jake Stark: First turn from Jake.
**[00:06:23]** Amit Ayer: Second turn from Amit.
"""
    summary = AttributionSummaryBuilder.build_summary(metadata, dialogue_text=dialogue)

    assert "## Attribution Summary" in summary
    assert "| Parker Vance | Apex Global Enterprises (NEA / PGD) | Technical Director / PGD Customer for LENS | 2 | HIGH |" in summary
    assert "| Eduardo Juan More | Apex Global Enterprises (NEA / PGD) | Atlas Lead / Rule Specialist | 1 | HIGH |" in summary
    assert "| Amit Ayer | PartnerAdvisory | Lead Enterprise Architect / Lead Client Partner | 2 | HIGH |" in summary
    assert "| Jen Pan | PartnerAdvisory | Product Manager / Lead Engineer | 0 | HIGH | Referenced project resource; 0 independently attributed spoken turns |" in summary
    assert "| Jake Stark | PartnerAdvisory | UI and Prompting Lead | 1 | HIGH |" in summary


def test_zero_pad_timestamps_normalizes_to_3_part():
    """Verify zero_pad_timestamps converts [MM:SS] into [00:MM:SS]."""
    text = "**[00:00]** Parker Vance: hello\n**[06:23]** Amit Ayer: speaking\n**[16:03]** Eduardo: next\n"
    padded = zero_pad_timestamps(text)
    assert "[00:00:00]" in padded
    assert "[00:06:23]" in padded
    assert "[00:16:03]" in padded
    # Ensure already 3-part timestamps are untouched
    assert zero_pad_timestamps("[01:23:45]") == "[01:23:45]"


def test_heal_recording_gaps_inserts_rule_c11_notation_across_long_silence():
    """Verify heal_recording_gaps inserts [Recording gap: 00:06:23 – 00:16:03] across 9m40s gap."""
    sample = """# Transcript
**[00:06:23]** Amit Ayer: Brief check.
**[00:16:03]** Eduardo Juan More: I am going to go to the next one.
"""
    changes: list = []
    aud: list = []
    healed = heal_recording_gaps(sample, changes, aud)

    assert "[Recording gap: 00:06:23 – 00:16:03]" in healed


def test_heal_output_1_forensic_prunes_hallucinations_and_maps_jed():
    """Verify heal_output_1_forensic cleans repetitive start video phrases and maps Jed -> Jen Pan."""
    metadata = {
        "attendees": [
            {"name": "Jen Pan", "org": "PartnerAdvisory", "role": "Product Manager / Lead Engineer"},
            {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Enterprise Architect"},
        ]
    }
    raw = """# Transcript
**Date:** 09/29/2026

## Transcript
**[00:00]** Amit Ayer: I want to make sure you understand. I'm going to go ahead and start the video. Yeah, as we expand the golden dataset, which Jed will obviously get to at some point here.

## Ambiguity Lock Register
| Item | Ambiguity Key | Timestamp / Section | Canonical Value | Resolution Rationale | Confidence | Verification Status |
|---|---|---|---|---|---|---|
| 1 | Speaker 1 | [00:00] | Amit Ayer | Self-identified | HIGH | (Operator Verified) |
"""
    changes: list = []
    aud: list = []
    healed = heal_output_1_forensic(raw, metadata=metadata, changes=changes, audit_log=aud)

    # repetitive video phrase pruned
    assert "start the video" not in healed
    # Jed mapped to Jen Pan
    assert "which Jen Pan will obviously get to" in healed
    # Timestamps zero-padded
    assert "[00:00:00]" in healed
    # ALR row added for Jed
    assert "| ALR-JED | Jed | Lexical ASR Error | Jen Pan |" in healed
