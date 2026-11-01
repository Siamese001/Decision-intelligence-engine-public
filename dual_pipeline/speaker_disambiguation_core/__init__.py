"""
dual_pipeline.speaker_disambiguation_core

Modularized implementation of high-reasoning speaker disambiguation,
attendee context models, room mic configs, and turn attribution records.
"""

from .disambiguator import SpeakerDisambiguator
from .models import (
    AttendeeContext,
    DisambiguationResult,
    RoomMicConfig,
    TurnAttribution,
)
from .patterns import PATTERNS

__all__ = [
    "AttendeeContext",
    "RoomMicConfig",
    "TurnAttribution",
    "DisambiguationResult",
    "PATTERNS",
    "SpeakerDisambiguator",
]
