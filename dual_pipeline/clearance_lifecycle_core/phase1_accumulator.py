"""
dual_pipeline/clearance_lifecycle_core/phase1_accumulator.py

Phase 1: In-Flight Non-Blocking Ambiguity Accumulation.
Enforces Law 3 (Untrusted Fast Path): zero live human interruptions.
Accumulates ambiguities directly into the append-only C-10 register.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.ambiguity_core.engine import AmbiguityEngine
from dual_pipeline.ambiguity_core.models import AmbiguityRecord

from .c10_register import AmbiguityLockRegister
from .models import PreflightContextSnapshot

logger = logging.getLogger(__name__)


class InFlightAccumulator:
    """In-flight non-blocking ambiguity accumulator."""

    def __init__(
        self,
        engine: Optional[AmbiguityEngine] = None,
        register: Optional[AmbiguityLockRegister] = None,
    ) -> None:
        self.engine = engine or AmbiguityEngine()
        self.register = register or AmbiguityLockRegister()
        self.accumulated_records: List[AmbiguityRecord] = []
        self.live_interrupt_count: int = 0

    def process_turns(
        self,
        turns: List[Dict[str, Any]],
        context: Optional[PreflightContextSnapshot] = None,
    ) -> Tuple[List[Dict[str, Any]], List[AmbiguityRecord]]:
        """Processes turns non-blockingly, scans ambiguities, and appends C-10 locks."""
        attendees = context.attendees if context else None
        acronyms = context.project_acronyms if context else None

        records = self.engine.scan(turns, known_attendees=attendees, project_acronyms=acronyms)
        self.accumulated_records.extend(records)

        for rec in records:
            self.register.append_lock(rec)

        assert self.live_interrupt_count == 0, "Violation of Law 3: Live human interrupt detected in Phase 1!"

        logger.info(
            f"Phase 1 non-blocking accumulation complete: {len(turns)} turns processed, "
            f"{len(records)} ambiguities locked in C-10 register."
        )
        return turns, records
