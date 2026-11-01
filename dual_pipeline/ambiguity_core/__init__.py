"""
dual_pipeline/ambiguity_core/__init__.py

Package exports for Full-Spectrum 5-Domain Ambiguity Engine.
"""

from .d6_lexical_filter import (
    COMMUNICATIVE_VERBS,
    SUPPRESSED_WORDS,
    is_suppressed_or_common_word,
    is_valid_name_syntax,
    should_surface_unconfirmed_candidate,
)
from .detector_d1_speaker import SpeakerAttributionDetector
from .detector_d2_vocabulary import VocabularyAcronymDetector
from .detector_d3_polarity import SemanticPolarityDetector
from .detector_d4_crosstalk import CrosstalkCollisionDetector
from .detector_d5_drops import RecordingDropsDetector
from .detector_d6_entity import EntityClarificationDetector
from .engine import AmbiguityEngine
from .models import (
    AMBIGUITY_MARGIN_THRESHOLD,
    AmbiguityDomain,
    AmbiguityRecord,
    AmbiguityStatus,
    CandidateOption,
)

__all__ = [
    "AMBIGUITY_MARGIN_THRESHOLD",
    "AmbiguityDomain",
    "AmbiguityRecord",
    "AmbiguityStatus",
    "CandidateOption",
    "SpeakerAttributionDetector",
    "VocabularyAcronymDetector",
    "SemanticPolarityDetector",
    "CrosstalkCollisionDetector",
    "RecordingDropsDetector",
    "EntityClarificationDetector",
    "COMMUNICATIVE_VERBS",
    "SUPPRESSED_WORDS",
    "is_suppressed_or_common_word",
    "is_valid_name_syntax",
    "should_surface_unconfirmed_candidate",
    "AmbiguityEngine",
]
