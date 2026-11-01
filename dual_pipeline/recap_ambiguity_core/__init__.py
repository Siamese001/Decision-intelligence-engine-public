"""Executive Recap Ambiguity Core Package (Domains E1-E4)."""
from __future__ import annotations

from .detector_e1_roster import DetectorE1Roster
from .detector_e2_altitude import DetectorE2Altitude
from .detector_e3_actions import DetectorE3Actions
from .detector_e4_systems import DetectorE4Systems
from .engine import ExecutiveAmbiguityEngine
from .models import (
    ExecutiveAmbiguityDomain,
    ExecutiveAmbiguityRecord,
    ExecutiveCandidateOption,
)

__all__ = [
    "ExecutiveAmbiguityDomain",
    "ExecutiveAmbiguityRecord",
    "ExecutiveCandidateOption",
    "DetectorE1Roster",
    "DetectorE2Altitude",
    "DetectorE3Actions",
    "DetectorE4Systems",
    "ExecutiveAmbiguityEngine",
]
