"""
dual_pipeline/clearance_lifecycle_core/phase0_preflight.py

Phase 0: Pre-Flight Contextual Seeding (T-5 min).
Ingests meeting calendar metadata, attendee rosters, project dictionaries,
and loads calibrated Bayesian priors from the persistent decision ledger.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from dual_pipeline.hitl_governance_core.telemetry import resolve_hitl_ledger_path

from .models import PreflightContextSnapshot

logger = logging.getLogger(__name__)


class PreflightSeeder:
    """Pre-Flight contextual seeder running at T-5 min."""

    def __init__(self, ledger_path: Optional[Path] = None) -> None:
        self.ledger_path = ledger_path or resolve_hitl_ledger_path()

    def load_bayesian_priors(self) -> Dict[str, Any]:
        """Loads historical HITL affirmations and penalizations from ledger."""
        if not self.ledger_path.exists():
            return {}
        try:
            data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
            return data.get("priors", {})
        except Exception as e:
            logger.warning(f"Failed loading Bayesian priors from {self.ledger_path}: {e}")
            return {}

    def seed(
        self,
        meeting_name: str,
        date_iso: str,
        attendees: Optional[List[str]] = None,
        project_acronyms: Optional[List[str]] = None,
    ) -> PreflightContextSnapshot:
        """Constructs a deterministic preflight context snapshot."""
        flat_atts: List[Any] = []
        if isinstance(attendees, dict):
            for v in attendees.values():
                if isinstance(v, list):
                    flat_atts.extend(v)
                else:
                    flat_atts.append(v)
        elif isinstance(attendees, list):
            flat_atts = attendees
        else:
            flat_atts = [attendees] if attendees else []

        clean_atts = [
            a.get("name", str(a)) if isinstance(a, dict) else str(a)
            for a in flat_atts
            if a
        ]
        atts = sorted(list(set(clean_atts)))
        acrs = sorted(list(set(str(x) for x in (project_acronyms or []))))
        priors = self.load_bayesian_priors()

        raw = f"{meeting_name}|{date_iso}|{','.join(atts)}|{','.join(acrs)}".encode("utf-8")
        snap_hash = hashlib.sha256(raw).hexdigest()

        snapshot = PreflightContextSnapshot(
            meeting_name=meeting_name,
            date_iso=date_iso,
            attendees=atts,
            project_acronyms=acrs,
            bayesian_priors=priors,
            snapshot_hash=snap_hash,
        )
        logger.info(f"Phase 0 Pre-Flight snapshot seeded for '{meeting_name}' ({snap_hash[:12]}).")
        return snapshot
