"""
Identity and Entity Resolution Engine for Live Transcripts and Forensic Normalization.

Authoritative lookup across:
1. approvals/v51/IDENTITY_AND_ALIAS_RECONCILIATION.json
2. cards/NEE_10 (Stakeholders & Champions)
3. inputs/pm_roster_enrichment_20260727/nea-pm-location-business-unit-roster.md
4. inputs/validation_closeout_20260727/nea-core-workstream-lead-map-closeout.md
5. Canonical SQLite graph node catalog (nee_graph / rollback baselines)

Enforces:
- Active speaker resolution at ingestion boundary (with HITL escalation for low-confidence/collisions)
- Verbatim Text Lock for spoken dialogue
- Ambiguity Lock Register (ALR) generation for referenced entities
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .identity_resolver_core import (
    ASRRepairRecord,
    HITLDecisionCard,
    HITLOption,
    IdentityResolver,
    ReferencedEntity,
    SpeakerResolutionResult,
)

__all__ = [
    "ASRRepairRecord",
    "Any",
    "Dict",
    "HITLDecisionCard",
    "HITLOption",
    "IdentityResolver",
    "List",
    "Optional",
    "Path",
    "ReferencedEntity",
    "Set",
    "SpeakerResolutionResult",
    "Tuple",
    "annotations",
    "dataclass",
    "field",
    "json",
    "os",
    "re",
    "sqlite3",
]
