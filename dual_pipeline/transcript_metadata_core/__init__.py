"""Core attendee parsing utilities for transcript metadata."""
from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
    build_roster_from_attendees,
    extract_attendees_from_header_block,
    extract_speakers_from_dialogue_turns,
    format_company_grouped_attendees,
    heal_attendees_block,
    is_blacklisted_attendee,
)

__all__ = [
    "extract_attendees_from_header_block",
    "extract_speakers_from_dialogue_turns",
    "is_blacklisted_attendee",
    "format_company_grouped_attendees",
    "build_roster_from_attendees",
    "heal_attendees_block",
]
