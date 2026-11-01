"""Unified Executive Recap Ambiguity Engine (Domains E1-E4)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from dual_pipeline.hitl_governance import DEFAULT_MARGIN_THRESHOLD

from .detector_e1_roster import DetectorE1Roster
from .detector_e2_altitude import DetectorE2Altitude
from .detector_e3_actions import DetectorE3Actions
from .detector_e4_systems import DetectorE4Systems
from .models import ExecutiveAmbiguityRecord

logger = logging.getLogger(__name__)


class ExecutiveAmbiguityEngine:
    """Unified ambiguity scanning engine for Output 2 (Executive Recap) pre-synthesis."""

    def __init__(
        self,
        margin_threshold: float = DEFAULT_MARGIN_THRESHOLD,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> None:
        from dual_pipeline.model_registry import ModelRegistry, ModelTier
        def_m, def_e = ModelRegistry.resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
        self.margin_threshold = margin_threshold
        self.model = model or def_m
        self.reasoning_effort = reasoning_effort or def_e or "high"
        self.d_e1 = DetectorE1Roster()
        self.d_e2 = DetectorE2Altitude()
        self.d_e3 = DetectorE3Actions()
        self.d_e4 = DetectorE4Systems()

    def scan(
        self,
        metadata: Dict[str, Any],
        transcript_text: str = "",
        action_items: Optional[List[Dict[str, Any]]] = None,
    ) -> List[ExecutiveAmbiguityRecord]:
        """Scan meeting context across all 4 executive ambiguity domains."""
        all_records: List[ExecutiveAmbiguityRecord] = []

        # Domain E1: Attendee Affiliation
        e1_recs = self.d_e1.detect(metadata)
        all_records.extend(e1_recs)

        # Domain E2: Executive Altitude & Catalyst
        e2_recs = self.d_e2.detect(metadata)
        all_records.extend(e2_recs)

        # Domain E3: Action Item Governance
        if action_items:
            e3_recs = self.d_e3.detect_from_items(action_items)
            all_records.extend(e3_recs)

        # Domain E4: Systems Architecture Grounding
        if transcript_text:
            e4_recs = self.d_e4.detect_from_text(transcript_text)
            all_records.extend(e4_recs)

        gate_eligible = [r for r in all_records if r.is_gate_eligible(self.margin_threshold)]
        logger.info(
            f"ExecutiveAmbiguityEngine: scanned context, found {len(all_records)} total ambiguities, "
            f"{len(gate_eligible)} gate-eligible (margin < {self.margin_threshold:.2f})."
        )
        return gate_eligible
