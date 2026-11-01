"""Unit tests for Wave 4: Generalized speaker disambiguation across arbitrary attendee rosters."""


from dual_pipeline.speaker_disambiguation_core.disambiguator import SpeakerDisambiguator
from dual_pipeline.speaker_disambiguation_core.models import AttendeeContext, RoomMicConfig


def test_disambiguation_generalized_across_novel_attendees():
    """Verify that disambiguation works accurately for a brand new client roster using domain topics and roles."""
    disambiguator = SpeakerDisambiguator(mock=True)

    in_room_attendees = [
        AttendeeContext(
            name="Sarah Jenkins",
            person_id="person:sarah-jenkins",
            company="Acme Corp",
            role="Lead Cloud Architect",
            domain_topics=["kubernetes", "vpc peering", "terraform", "ingress gateway"],
        ),
        AttendeeContext(
            name="Marcus Vance",
            person_id="person:marcus-vance",
            company="Acme Corp",
            role="Program Manager",
            domain_topics=["q3 charter", "deliverables", "gantt chart", "risk register"],
        ),
    ]

    room_config = RoomMicConfig(
        mic_label="Conference Room A",
        display_label="Acme Conference Room",
        in_room_attendees=in_room_attendees,
    )

    transcript = (
        "**Conference Room A** [00:00:10]: We need to configure the terraform modules and ensure the ingress gateway is secured.\n\n"
        "**Conference Room A** [00:00:25]: The q3 charter and risk register must be signed off by Friday."
    )

    result = disambiguator.disambiguate(transcript, room_config)
    assert result.directly_attributed_turns == 2
    assert "Sarah Jenkins" in result.resolved_transcript
    assert "Marcus Vance" in result.resolved_transcript


def test_disambiguation_historical_participants_supported_via_memory():
    """Verify that historical participants (Amit, Dan, Stefan) are supported via SpeakerMemory."""
    disambiguator = SpeakerDisambiguator(mock=True)

    in_room_attendees = [
        AttendeeContext(
            name="Amit Ayer",
            person_id="person:amit-ayer",
            company="PartnerAdvisory",
            role="Lead Enterprise Architect",
            domain_topics=["architecture"],
        ),
        AttendeeContext(
            name="Daniel Brankin",
            person_id="person:daniel-brankin",
            company="Apex Global",
            role="Engineering Lead",
            domain_topics=["transmission system"],
        ),
    ]

    room_config = RoomMicConfig(
        mic_label="Room Mic",
        display_label="Shared Room",
        in_room_attendees=in_room_attendees,
    )

    transcript = (
        "**Room Mic** [00:00:10]: I will walk through the technical screening standards and system blueprint.\n\n"
        "**Room Mic** [00:00:25]: The solar 40 batteries between 10 is the core question for the transmission system."
    )

    result = disambiguator.disambiguate(transcript, room_config)
    assert result.directly_attributed_turns == 2
    assert "Amit Ayer" in result.resolved_transcript
    assert "Daniel Brankin" in result.resolved_transcript
