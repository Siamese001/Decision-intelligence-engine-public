"""
tests/test_multi_tier_speaker_attribution.py

Unit tests for Multi-Tier Speaker Attribution and Disambiguation.
Verifies:
1. Multi-tier attribution parsing of generic Speaker 1 / Speaker 2 turns.
2. Attribution into Tier 1 (Named), Tier 2 (Affiliation), and Tier 3 (Room-Mic Crosstalk).
3. Verbatim spoken text preservation with zero lexical distortion.
4. Auto-synthesis of RoomMicConfig from attendee roster in resolve_shared_room_microphones.
"""
import pytest

from dual_pipeline.speaker_disambiguation import (
    AttendeeContext,
    RoomMicConfig,
    SpeakerDisambiguator,
    resolve_shared_room_microphones,
)


@pytest.fixture
def chakra_room_config():
    attendees = [
        AttendeeContext(
            name="Cameron Mercer",
            person_id="person:stefan-marceau",
            role="Delivery Leader / Senior Director",
            company="PartnerAdvisory",
            domain_topics=["survey", "readout", "37 respondents", "65 engineers", "ai hub"],
        ),
        AttendeeContext(
            name="Drew Oakley",
            person_id="person:dan-okray",
            role="Client Executive / Account Leader",
            company="PartnerAdvisory",
            domain_topics=["consultant", "second class citizen", "partnership", "trust", "wrap up"],
        ),
        AttendeeContext(
            name="Amit Ayer",
            person_id="person:amit-ayer",
            role="Senior Principal / Engineering Leader",
            company="PartnerAdvisory",
            domain_topics=["macbook", "adm", "matt sassento", "technical screening", "candidates"],
        ),
        AttendeeContext(
            name="Chakra Sankaraiah",
            person_id="person:chakra-sankaraiah",
            role="CTO",
            company="NEA",
            domain_topics=["leads team meeting", "mark, within", "pod model", "multifaceted", "vertical foundations"],
        ),
        AttendeeContext(
            name="Mark McCarthy",
            person_id="person:mark-mccarthy",
            role="VP / Head of Software Engineering",
            company="NEA",
            domain_topics=["defining vertical", "tech lead role", "pineapple", "full-time org chart"],
        ),
        AttendeeContext(
            name="Will Lane",
            person_id="person:will-lane",
            role="AI Tooling & Tokenomics Steward",
            company="NEA",
            domain_topics=["kiro", "curator", "opentelemetry", "plumtree", "token", "credits"],
        ),
    ]
    return RoomMicConfig(
        mic_label="generic-speaker",
        display_label="NEA/PartnerAdvisory (room)",
        in_room_attendees=attendees,
    )


def test_multi_tier_attribution_offline_hierarchy(chakra_room_config):
    transcript = """# Test Transcript
**Speaker 1** [00:01:00]: Thanks for making time. We completed the survey readout of 37 respondents across 65 engineers.
**Speaker 2** [00:02:35]: Yes, Mark and I discussed within our leads team meeting that the pod model is not working out.
**Speaker 1** [00:05:00]: As an outside organization, we are committed to helping your delivery teams succeed.
**Speaker 2** [00:08:00]: In our enterprise architecture, we are tracking vertical tech leads in Pineapple.
**Speaker 2** [00:12:00]: For AI tooling, we are trialing Kiro and OpenTelemetry token credits on AWS.
**Speaker 1** [00:15:00]: We provisioned 30 MacBooks into the ADM environment with Matt Sassento.
**Speaker 1** [00:20:00]: Yeah.
**Speaker 2** [00:20:02]: Okay.
"""
    disambiguator = SpeakerDisambiguator(mock=True)
    result = disambiguator.disambiguate(transcript, chakra_room_config)

    resolved = result.resolved_transcript
    lines = resolved.strip().splitlines()

    # Verify verbatim text preservation
    assert "We completed the survey readout of 37 respondents across 65 engineers." in resolved
    assert "Yes, Mark and I discussed within our leads team meeting that the pod model is not working out." in resolved
    assert "We provisioned 30 MacBooks into the ADM environment with Matt Sassento." in resolved

    # Verify Tier 1: Cameron Mercer (PartnerAdvisory)
    assert "**Cameron Mercer (PartnerAdvisory)** [00:01:00]:" in lines[1]

    # Verify Tier 1: Chakra Sankaraiah (NEA)
    assert "**Chakra Sankaraiah (NEA)** [00:02:35]:" in lines[2]

    # Verify Tier 2: PartnerAdvisory Speaker (organizational affiliation)
    assert "**PartnerAdvisory Speaker** [00:05:00]:" in lines[3]

    # Verify Tier 1: Mark McCarthy (NEA)
    assert "**Mark McCarthy (NEA)** [00:08:00]:" in lines[4]

    # Verify Tier 1: Will Lane (NEA)
    assert "**Will Lane (NEA)** [00:12:00]:" in lines[5]

    # Verify Tier 1: Amit Ayer (PartnerAdvisory)
    assert "**Amit Ayer (PartnerAdvisory)** [00:15:00]:" in lines[6]

    # Verify Tier 3: Room Mic / Crosstalk
    assert "**NEA/PartnerAdvisory (room)** [00:20:00]: Yeah." in lines[7]
    assert "**NEA/PartnerAdvisory (room)** [00:20:02]: Okay." in lines[8]

    # Verify manifest telemetry
    manifest = result.attribution_manifest
    assert manifest["telemetry"]["total_room_mic_turns"] == 8
    assert manifest["telemetry"]["directly_attributed_turns"] == 6


def test_resolve_shared_room_microphones_auto_synthesis():
    metadata = {
        "meeting_name": "Test_Meeting",
        "attendees": [
            {"name": "Cameron Mercer", "org": "PartnerAdvisory", "domain_topics": ["survey", "readout"]},
            {"name": "Chakra Sankaraiah", "org": "NEA", "domain_topics": ["leads team", "pod model"]},
        ],
        "mock_disambiguation": True,
    }
    transcript = """**Speaker 1** [00:01:00]: Here is our survey readout.
**Speaker 2** [00:02:00]: Within our leads team meeting, we reviewed it.
"""
    speaker_map = {}
    resolved, meta_out = resolve_shared_room_microphones(transcript, metadata, speaker_map)

    assert "**Cameron Mercer (PartnerAdvisory)** [00:01:00]:" in resolved
    assert "**Chakra Sankaraiah (NEA)** [00:02:00]:" in resolved
    assert "_attribution_manifest" in meta_out
