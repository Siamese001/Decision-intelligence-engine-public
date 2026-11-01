"""
dual_pipeline/ambiguity_core/engine.py

Unified Full-Spectrum 6-Domain Ambiguity Engine.
Orchestrates D1–D6 detectors, computes global candidate rankings,
and surfaces gate-eligible ambiguities based on the governed SSOT margin rule.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .detector_d1_speaker import SpeakerAttributionDetector
from .detector_d2_vocabulary import VocabularyAcronymDetector
from .detector_d3_polarity import SemanticPolarityDetector
from .detector_d4_crosstalk import CrosstalkCollisionDetector
from .detector_d5_drops import RecordingDropsDetector
from .detector_d6_entity import EntityClarificationDetector
from .models import (
    AMBIGUITY_MARGIN_THRESHOLD,
    AmbiguityRecord,
)

logger = logging.getLogger(__name__)


class AmbiguityEngine:
    """Full-Spectrum 6-Domain Ambiguity Orchestration Engine."""

    def __init__(
        self,
        margin_threshold: float = AMBIGUITY_MARGIN_THRESHOLD,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> None:
        from dual_pipeline.model_registry import ModelRegistry, ModelTier

        def_m, def_e = ModelRegistry.resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
        self.margin_threshold = margin_threshold
        self.model = model or def_m
        self.reasoning_effort = reasoning_effort or def_e or "xhigh"

        self.d1_detector = SpeakerAttributionDetector(
            margin_threshold=margin_threshold, model=model, reasoning_effort=reasoning_effort
        )
        self.d2_detector = VocabularyAcronymDetector(margin_threshold=margin_threshold)
        self.d3_detector = SemanticPolarityDetector(margin_threshold=margin_threshold)
        self.d4_detector = CrosstalkCollisionDetector(margin_threshold=margin_threshold)
        self.d5_detector = RecordingDropsDetector(margin_threshold=margin_threshold)
        self.d6_detector = EntityClarificationDetector()

    def scan(
        self,
        turns: List[Dict[str, Any]],
        known_attendees: Optional[List[str]] = None,
        project_acronyms: Optional[List[str]] = None,
        known_entities: Optional[List[Dict[str, Any]]] = None,
        cross_meeting_registry: Optional[Dict[str, Any]] = None,
    ) -> List[AmbiguityRecord]:
        """Scans transcript turns across all 6 ambiguity domains."""
        all_records: List[AmbiguityRecord] = []

        try:
            d1_recs = self.d1_detector.detect(turns, known_attendees=known_attendees)
            all_records.extend(d1_recs)
        except Exception as e:
            logger.warning(f"Domain 1 detector error: {e}")

        try:
            d2_recs = self.d2_detector.detect(turns, project_acronyms=project_acronyms)
            all_records.extend(d2_recs)
        except Exception as e:
            logger.warning(f"Domain 2 detector error: {e}")

        try:
            d3_recs = self.d3_detector.detect(turns)
            all_records.extend(d3_recs)
        except Exception as e:
            logger.warning(f"Domain 3 detector error: {e}")

        try:
            d4_recs = self.d4_detector.detect(turns)
            all_records.extend(d4_recs)
        except Exception as e:
            logger.warning(f"Domain 4 detector error: {e}")

        try:
            d5_recs = self.d5_detector.detect(turns)
            all_records.extend(d5_recs)
        except Exception as e:
            logger.warning(f"Domain 5 detector error: {e}")

        try:
            d6_recs = self.d6_detector.detect(
                turns,
                known_attendees=known_attendees,
                known_entities=known_entities,
                cross_meeting_registry=cross_meeting_registry,
            )
            all_records.extend(d6_recs)
        except Exception as e:
            logger.warning(f"Domain 6 detector error: {e}")

        logger.info(
            f"AmbiguityEngine scan completed: {len(all_records)} total ambiguities found "
            f"({sum(1 for r in all_records if r.requires_hitl)} require HITL with Delta <= {self.margin_threshold})."
        )
        return all_records
