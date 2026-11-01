"""
Tests for SpeakerDisambiguator — High-reasoning LLM and heuristic speaker attribution.
Verifies verbatim text locks, ambiguity gating (25% margin threshold), and Gate 1.2 manifest hygiene.
"""

import pytest

from dual_pipeline.speaker_disambiguation import (
    AttendeeContext,
    RoomMicConfig,
    SpeakerDisambiguator,
)


@pytest.fixture
def fpl_room_config() -> RoomMicConfig:
    return RoomMicConfig(
        mic_label="JB-E1419-VC",
        display_label="NEA/APEX (room)",
        in_room_attendees=[
            AttendeeContext(
                name="Reid Schenk",
                person_id="person:reid-schenk",
                role="Executive Sponsor / VP",
                company="Apex Global Enterprises",
                domain_topics=["quarterly missions", "priorities", "process flow", "50 step process", "leadership model"],
            ),
            AttendeeContext(
                name="Daniel Brankin",
                person_id="person:daniel-brankin",
                role="Development Lead",
                company="Apex Global Enterprises",
                domain_topics=["Nexus", "wireframe", "HTML", "solar 40", "battery", "batteries 10 to 20", "LAMS database", "query"],
            ),
            AttendeeContext(
                name="Sharon Diaram",
                person_id="person:sharon-diaram",
                role="AI Tech Lead",
                company="Apex Global Enterprises",
                domain_topics=["tiger team", "jira", "264,000 acres", "built out our nexus", "Q4 build ask"],
            ),
            AttendeeContext(
                name="Diana Valencia",
                person_id="person:diana-valencia",
                role="Operations / E&C",
                company="Apex Global Enterprises",
                domain_topics=["pole placements", "spreadsheet resequencing", "copy and paste"],
            ),
        ],
    )


@pytest.fixture
def sample_transcript() -> str:
    return """# APEX Development Rewire -- HITL-Resolved Transcript

**Date:** 08/28/2026
**Platform Reference:** Teams Call

---
**[00:01:10] Drew Oakley**: Let's kick off the discussion regarding the Q4 development rewire and our key objectives.
**[00:01:25] NEA/APEX (room)**: What that first thing that we should be targeting is a Q4 milestone, right?
**[00:02:00] NEA/APEX (room)**: Basically built out our nexus, so don't let him kind of not say. I built an HTML.
**[00:02:30] NEA/APEX (room)**: Solar 40, batteries between 10 and 20 individual projects. LAMS is nothing more than a database. Can we query that?
**[00:03:00] NEA/APEX (room)**: Copy-pasting spreadsheets for leadership resequencing and pole placement.
**[00:03:15] NEA/APEX (room)**: Yeah.
---
## Ambiguity Lock Register
Verified.
"""


def test_turn_parsing_preserves_text_and_timestamps(sample_transcript: str):
    disambiguator = SpeakerDisambiguator(mock=True)
    turns = disambiguator.parse_turns(sample_transcript)

    assert len(turns) == 6
    assert turns[0]["speaker"] == "Drew Oakley"
    assert turns[0]["timestamp"] == "[00:01:10]"
    assert "Q4 development rewire" in turns[0]["text"]

    assert turns[1]["speaker"] == "NEA/APEX (room)"
    assert turns[1]["timestamp"] == "[00:01:25]"
    assert "Q4 milestone" in turns[1]["text"]


def test_verbatim_text_lock(sample_transcript: str, fpl_room_config: RoomMicConfig):
    """Ensure that spoken dialogue text is 100% character-for-character preserved."""
    disambiguator = SpeakerDisambiguator(mock=True)
    result = disambiguator.disambiguate(sample_transcript, fpl_room_config)

    # Check that original spoken text remains completely unaltered
    assert "What that first thing that we should be targeting is a Q4 milestone, right?" in result.resolved_transcript
    assert "Basically built out our nexus, so don't let him kind of not say. I built an HTML." in result.resolved_transcript
    assert "Solar 40, batteries between 10 and 20 individual projects. LAMS is nothing more than a database. Can we query that?" in result.resolved_transcript
    assert "Copy-pasting spreadsheets for leadership resequencing and pole placement." in result.resolved_transcript
    assert "Yeah." in result.resolved_transcript


def test_ambiguity_gating_and_domain_attribution(sample_transcript: str, fpl_room_config: RoomMicConfig):
    """
    Verify 25% margin gating:
    - Distinct domain topic turns resolve to specific speakers.
    - Ambiguous turns ('Yeah.') stay tagged as SHARED_ROOM_MIC.
    """
    disambiguator = SpeakerDisambiguator(confidence_margin_threshold=0.25, mock=True)
    result = disambiguator.disambiguate(sample_transcript, fpl_room_config)

    # Daniel Brankin should be attributed for solar/battery/HTML/LAMS (with company suffix)
    assert "**[00:02:30] Daniel Brankin (Apex Global Enterprises)**:" in result.resolved_transcript

    # Sharon Diaram should be attributed for building out nexus / HTML
    assert "**[00:02:00] Sharon Diaram (Apex Global Enterprises)**:" in result.resolved_transcript

    # Diana Valencia should be attributed for spreadsheet copy-pasting
    assert "**[00:03:00] Diana Valencia (Apex Global Enterprises)**:" in result.resolved_transcript

    # Reid Schenk should be attributed for Q4 milestone / executive target
    assert "**[00:01:25] Reid Schenk (Apex Global Enterprises)**:" in result.resolved_transcript

    # Short affirmation ('Yeah.') must remain tagged as room mic due to ambiguity gating
    assert "**[00:03:15] NEA/APEX (room)**: Yeah." in result.resolved_transcript

    # Verify attribution counts
    assert result.directly_attributed_turns == 4
    assert result.shared_room_preserved_turns == 1


def test_gate_1_2_manifest_compliance(sample_transcript: str, fpl_room_config: RoomMicConfig):
    """Verify generated attribution manifest matches Gate 1.2 requirements."""
    disambiguator = SpeakerDisambiguator(confidence_margin_threshold=0.25, mock=True)
    remote = [{"name": "Drew Oakley", "person_id": "person:dan-okray"}]
    result = disambiguator.disambiguate(sample_transcript, fpl_room_config, remote_attendees=remote)

    manifest = result.attribution_manifest
    assert manifest["meeting_archetype"] == "STRATEGIC_DISCOVERY"
    assert "segments" in manifest

    # Must contain collective:room-mic as SHARED_ROOM_MIC
    room_segment = next((s for s in manifest["segments"] if s["attribution_mode"] == "SHARED_ROOM_MIC"), None)
    assert room_segment is not None
    assert room_segment["speaker_id"] == "collective:room-mic"

    # Must contain directly attributed attendees
    direct_speakers = {s["speaker_id"] for s in manifest["segments"] if s["attribution_mode"] == "DIRECT"}
    assert "person:dan-okray" in direct_speakers
    assert "person:daniel-brankin" in direct_speakers
    assert "person:sharon-diaram" in direct_speakers
    assert "person:reid-schenk" in direct_speakers
    assert "person:diana-valencia" in direct_speakers


def test_lexical_tie_preserves_shared_room_mic():
    """Verify that when attendees from different organizations tie on lexical match,
    the turn is preserved as SHARED_ROOM_MIC (collective:room-mic) and not attributed."""
    room_cfg = RoomMicConfig(
        mic_label="JB-E1419-VC",
        display_label="NEA/APEX (room)",
        in_room_attendees=[
            AttendeeContext(
                name="Alice Engineer",
                person_id="person:alice-engineer",
                role="Consultant",
                company="External Org",
                domain_topics=["shared_database_topic"],
            ),
            AttendeeContext(
                name="Bob Specialist",
                person_id="person:bob-specialist",
                role="Specialist",
                company="Other Org",
                domain_topics=["shared_database_topic"],
            ),
        ],
    )
    transcript = """**[00:04:10] NEA/APEX (room)**: Discussion on shared_database_topic right here."""
    disambiguator = SpeakerDisambiguator(confidence_margin_threshold=0.25, mock=True)
    result = disambiguator.disambiguate(transcript, room_cfg)

    assert len(result.turns) == 1
    assert result.turns[0].attribution_mode == "SHARED_ROOM_MIC"
    assert result.turns[0].person_id == "collective:room-mic"
    assert result.directly_attributed_turns == 0
    assert "**[00:04:10] NEA/APEX (room)**: Discussion on shared_database_topic right here." in result.resolved_transcript


def test_affiliation_mode_does_not_increment_directly_attributed_turns():
    """Verify that organization affiliation attribution (e.g. PartnerAdvisory Speaker)
    is recorded under affiliation_attributed_turns and does not count as directly attributed."""
    room_cfg = RoomMicConfig(
        mic_label="JB-E1419-VC",
        display_label="NEA/APEX (room)",
        in_room_attendees=[
            AttendeeContext(
                name="Consultant One",
                person_id="person:consultant-one",
                role="Consultant",
                company="PartnerAdvisory",
                domain_topics=["client survey"],
            ),
            AttendeeContext(
                name="Consultant Two",
                person_id="person:consultant-two",
                role="Senior Consultant",
                company="PartnerAdvisory",
                domain_topics=["client survey"],
            ),
        ],
    )
    transcript = """**[00:05:00] NEA/APEX (room)**: We reviewed the client survey with the team."""
    disambiguator = SpeakerDisambiguator(confidence_margin_threshold=0.25, mock=True)
    result = disambiguator.disambiguate(transcript, room_cfg)

    assert len(result.turns) == 1
    assert result.turns[0].attribution_mode == "AFFILIATION"
    assert "PartnerAdvisory" in result.turns[0].assigned_speaker
    assert result.directly_attributed_turns == 0
    assert result.affiliation_attributed_turns == 1
